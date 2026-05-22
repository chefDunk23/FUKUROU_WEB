"""
api_v2/routers/prediction.py
==============================
GET /api/v2/predict/{race_id} — V2 スタックアンサンブル予測。

処理フロー:
    1. fukurou_keiba_v2 からレース情報・出走馬・過去走統計を取得
    2. course_physical_master.csv でコース物理特徴量を JOIN
    3. fukurou_jvdl のフィーチャーストアで特徴量を拡充
    4. 6 サブモデルでスコアを計算（score_ability_v2 ... score_condition_v2）
    5. V2 5-fold アンサンブルで最終 AI スコアを算出
    6. AI スコア降順で並べた JSON を返す
"""
from __future__ import annotations

import logging
import math
from functools import lru_cache
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from shared.config import PATHS
from shared.db.jvdata import get_conn as get_v2_conn
from shared.db.jvdl import get_conn as get_jvdl_conn
from src.features.track_code_aliases import TRACK_CODE_ALIASES
from src.models.submodel_registry import SubmodelManager
from src.models.v2.config import (
    FEATURES_APTITUDE,
    FEATURES_AUX,
    FEATURES_CHOKYO,
    FEATURES_JOCKEY,
    FEATURES_PAST_PERF,
    FEATURES_PHYSICAL,
    FEATURES_RATING,
    FEATURES_SUBMODEL,
    FEATURES_TRAINER,
    FEATURES_TRAINING,
    GRADE_CODE_MAP,
    NUMERIC_CODE_COLS,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v2", tags=["v2-predict"])

# ── レスポンス型 ──────────────────────────────────────────────────────────────

class HorsePrediction(BaseModel):
    umaban: int
    horse_id: str
    horse_name: str | None
    ai_score: float
    ai_rank: int
    tan_odds: float | None
    odds_rank: int | None
    actual_rank: int | None


class RacePredictionResponse(BaseModel):
    race_id: str
    race_date: str
    keibajo_code: str
    distance: int
    horses: list[HorsePrediction]
    model_folds: int
    feature_count: int
    is_confirmed: bool


# ── メインアンサンブル（起動時1回ロード）────────────────────────────────────

class _V2Ensemble:
    def __init__(self, model_dir: Path) -> None:
        fold_files = sorted(model_dir.glob("lgbm_rank_fold*.lgb"))
        if not fold_files:
            raise FileNotFoundError(
                f"V2モデルが見つかりません: {model_dir}/lgbm_rank_fold*.lgb\n"
                "先に scripts/train_v2_main.py を実行してください。"
            )
        self._models = [lgb.Booster(model_file=str(p)) for p in fold_files]
        self._feature_names: list[str] = self._models[0].feature_name()
        logger.info("[V2Ensemble] %d モデルロード完了", len(self._models))

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        scores = np.stack([m.predict(X[self._feature_names]) for m in self._models])
        return scores.mean(axis=0)

    @property
    def n_folds(self) -> int:
        return len(self._models)

    @property
    def feature_names(self) -> list[str]:
        return self._feature_names


_ensemble: _V2Ensemble | None = None


def _get_ensemble() -> _V2Ensemble:
    global _ensemble
    if _ensemble is None:
        _ensemble = _V2Ensemble(PATHS.model_dir_v2)
    return _ensemble


# ── サブモデル群（起動時1回ロード）─────────────────────────────────────────────

_SUBMODEL_NAMES = [
    "ability_v2", "course_v2", "team_v2",
    "training_v2", "pace_v2", "condition_v2",
]


class _SubmodelSet:
    def __init__(self, base_dir: Path) -> None:
        self._submodels: dict[str, tuple[lgb.Booster, list[str]]] = {}
        for name in _SUBMODEL_NAMES:
            mgr = SubmodelManager(base_dir / name)
            if not mgr.exists():
                raise FileNotFoundError(
                    f"サブモデルが見つかりません: {base_dir / name}\n"
                    "先に scripts/train_v2_submodels.py を実行してください。"
                )
            booster, feature_cols, _ = mgr.load()
            self._submodels[name] = (booster, feature_cols)
        logger.info("[SubmodelSet] %d サブモデルロード完了", len(self._submodels))

    def score(self, df: pd.DataFrame) -> pd.DataFrame:
        result = df.copy()
        for name, (booster, feature_cols) in self._submodels.items():
            X = result.reindex(columns=feature_cols)
            result[f"score_{name}"] = booster.predict(X)
        return result


_submodel_set: _SubmodelSet | None = None


def _get_submodel_set() -> _SubmodelSet:
    global _submodel_set
    if _submodel_set is None:
        _submodel_set = _SubmodelSet(PATHS.submodel_dir_v2)
    return _submodel_set


# ── コース物理マスタ ──────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _load_course_master() -> pd.DataFrame:
    master = pd.read_csv(PATHS.course_master_csv, dtype=str, comment="#")
    master["keibajo_code"] = master["keibajo_code"].str.strip().str.zfill(2)
    master["track_code"]   = master["track_code"].str.strip().str.zfill(2)
    for col in ["distance", "straight_dist", "dist_to_corner1",
                "elevation_diff", "last_straight_hill_flag"]:
        master[col] = pd.to_numeric(master[col], errors="coerce")
    return master


# ── DB 照会 SQL ───────────────────────────────────────────────────────────────

_SQL_RACE_ENTRIES = """
WITH all_confirmed AS (
    SELECT e.race_id, e.horse_id, r.race_date, e.kakutei_chakujun
    FROM   race_entries e
    JOIN   races r ON e.race_id = r.id
    WHERE  e.kakutei_chakujun IS NOT NULL
      AND  e.kakutei_chakujun > 0
),
past_stats AS (
    SELECT
        race_id, horse_id,
        COALESCE(COUNT(*) OVER (
            PARTITION BY horse_id ORDER BY race_date
            RANGE BETWEEN UNBOUNDED PRECEDING AND '1 day'::interval PRECEDING
        ), 0)::integer AS feature_past_starts,
        COALESCE(SUM(CASE WHEN kakutei_chakujun = 1 THEN 1 ELSE 0 END) OVER (
            PARTITION BY horse_id ORDER BY race_date
            RANGE BETWEEN UNBOUNDED PRECEDING AND '1 day'::interval PRECEDING
        ), 0)::integer AS feature_past_wins,
        COALESCE(SUM(CASE WHEN kakutei_chakujun BETWEEN 1 AND 3 THEN 1 ELSE 0 END) OVER (
            PARTITION BY horse_id ORDER BY race_date
            RANGE BETWEEN UNBOUNDED PRECEDING AND '1 day'::interval PRECEDING
        ), 0)::integer AS feature_past_top3
    FROM all_confirmed
)
SELECT
    r.id AS race_id, r.race_date, r.race_num, r.keibajo_code,
    r.race_name_hondai, r.distance, r.track_code, r.course_kubun,
    r.grade_code, r.tenko_code, r.shiba_baba_code, r.dirt_baba_code,
    r.zen_3f, r.go_3f, r.lap_time_array,
    e.umaban, e.horse_id, e.horse_name, e.trainer_cd, e.jockey_cd,
    e.horse_weight, e.weight_diff, e.basis_weight,
    e.tan_odds, e.ninki, e.kakutei_chakujun,
    COALESCE(ps.feature_past_starts, 0) AS feature_past_starts,
    COALESCE(ps.feature_past_wins,   0) AS feature_past_wins,
    COALESCE(ps.feature_past_top3,   0) AS feature_past_top3
FROM   races r
JOIN   race_entries e ON e.race_id = r.id
LEFT   JOIN past_stats ps ON ps.race_id = r.id AND ps.horse_id = e.horse_id
WHERE  r.id = %s
ORDER  BY e.umaban
"""


def _fetch_race_data(race_id: str) -> pd.DataFrame:
    with get_v2_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(_SQL_RACE_ENTRIES, (race_id,))
            rows = cur.fetchall()
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def _derive_db_race_id(race_date, keibajo_code: str, race_num: int) -> str:
    return pd.Timestamp(race_date).strftime("%Y%m%d") + str(keibajo_code).zfill(2) + str(race_num).zfill(2)


def _fetch_feature_stores(
    db_race_id: str,
    horse_ids: list[str],
    jockey_cds: list[str],
    trainer_cds: list[str],
    race_date,
) -> dict[str, pd.DataFrame]:
    date_val = pd.Timestamp(race_date).date()
    with get_jvdl_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT race_id, horse_id, pre_race_rating "
                "FROM horse_rating_store WHERE race_id = %s",
                (db_race_id,),
            )
            hr = pd.DataFrame(cur.fetchall())

            cur.execute(
                "SELECT race_id, ketto_toroku_bango AS horse_id, "
                "       chokyo_master_score, s1_time_score, accel_bonus "
                "FROM chokyo_scores WHERE race_id = %s",
                (db_race_id,),
            )
            cs = pd.DataFrame(cur.fetchall())

            cur.execute(
                "SELECT race_id, ketto_toroku_bango AS horse_id, "
                "       apt_distance_shift, apt_bias_fit, apt_seasonal "
                "FROM aptitude_scores WHERE race_id = %s",
                (db_race_id,),
            )
            apt = pd.DataFrame(cur.fetchall())

            cur.execute(
                "SELECT kishu_code AS jockey_cd, "
                "       win_rate AS jockey_win_rate, "
                "       surface_turf_win_rate AS jockey_turf_win_rate, "
                "       surface_dirt_win_rate AS jockey_dirt_win_rate, "
                "       surface_turf_win_shift AS jockey_turf_win_shift, "
                "       surface_dirt_win_shift AS jockey_dirt_win_shift "
                "FROM jockey_feature_store "
                "WHERE kishu_code = ANY(%s) AND target_date = %s",
                (jockey_cds, date_val),
            )
            jf = pd.DataFrame(cur.fetchall())

            cur.execute(
                "SELECT chokyoshi_code AS trainer_cd, "
                "       win_rate AS trainer_win_rate, "
                "       surface_turf_win_rate AS trainer_turf_win_rate, "
                "       surface_dirt_win_rate AS trainer_dirt_win_rate "
                "FROM trainer_feature_store "
                "WHERE chokyoshi_code = ANY(%s) AND target_date = %s",
                (trainer_cds, date_val),
            )
            tf = pd.DataFrame(cur.fetchall())

            cur.execute(
                "SELECT horse_id, best_z_total, z_trend_slope, avg_accel, "
                "       session_count, slope_ratio "
                "FROM training_feature_store "
                "WHERE horse_id = ANY(%s) AND target_date = %s",
                (horse_ids, date_val),
            )
            trf = pd.DataFrame(cur.fetchall())

    return {"hr": hr, "cs": cs, "apt": apt, "jf": jf, "tf": tf, "trf": trf}


# ── コース特徴量 JOIN ─────────────────────────────────────────────────────────

def _apply_course_features(df: pd.DataFrame) -> pd.DataFrame:
    master = _load_course_master()
    df = df.copy()
    df["keibajo_code"] = df["keibajo_code"].astype(str).str.zfill(2)
    df["track_code"]   = df["track_code"].astype(str).str.zfill(2)

    for (kc, tc_alias), tc_canonical in TRACK_CODE_ALIASES.items():
        mask = (df["keibajo_code"] == kc) & (df["track_code"] == tc_alias)
        df.loc[mask, "track_code"] = tc_canonical

    course_cols = ["straight_dist", "dist_to_corner1", "elevation_diff", "last_straight_hill_flag"]
    merged = df.merge(
        master[["keibajo_code", "track_code", "distance"] + course_cols],
        on=["keibajo_code", "track_code", "distance"],
        how="left",
    )

    unmatched = merged[course_cols[0]].isna()
    if unmatched.any():
        for idx in merged[unmatched].index:
            kc   = merged.at[idx, "keibajo_code"]
            tc   = merged.at[idx, "track_code"]
            dist = int(merged.at[idx, "distance"])
            cands = master[(master["keibajo_code"] == kc) & (master["track_code"] == tc)]
            if not cands.empty:
                nearest = cands.iloc[(cands["distance"] - dist).abs().argsort().iloc[0]]
                for col in course_cols:
                    merged.at[idx, col] = nearest[col]
    return merged


# ── ラップ統計量 ─────────────────────────────────────────────────────────────

def _compute_lap_stats(arr) -> tuple[float, float]:
    if arr is None:
        return float("nan"), float("nan")
    valid = [float(v) for v in (arr if isinstance(arr, list) else [])
             if v is not None and not math.isnan(float(v))]
    if len(valid) < 2:
        return float("nan"), float("nan")
    a = np.array(valid)
    return float(np.var(a)), float(np.std(a))


# ── 数値変換 ─────────────────────────────────────────────────────────────────

def _prepare_numerics(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "grade_code" in df.columns:
        df["grade_code"] = df["grade_code"].map(GRADE_CODE_MAP)
    for col in NUMERIC_CODE_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ["basis_weight", "horse_weight", "weight_diff", "zen_3f", "go_3f", "distance"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


# ── 特徴量アセンブリ ──────────────────────────────────────────────────────────

def _build_features(race_id: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """raw_df（全馬情報）と X（サブモデルスコア6列）を返す。"""
    df = _fetch_race_data(race_id)
    if df.empty:
        return pd.DataFrame(), pd.DataFrame()

    df = df.copy()

    lap_stats = df["lap_time_array"].apply(
        lambda arr: pd.Series(_compute_lap_stats(arr), index=["lap_variance", "lap_std"])
    )
    df = pd.concat([df, lap_stats], axis=1)

    df["zen_3f"]     = pd.to_numeric(df["zen_3f"], errors="coerce")
    df["go_3f"]      = pd.to_numeric(df["go_3f"],  errors="coerce")
    df["pace_index"] = df["zen_3f"] - df["go_3f"]

    df["feature_past_starts"] = pd.to_numeric(df["feature_past_starts"], errors="coerce")
    df["feature_past_wins"]   = pd.to_numeric(df["feature_past_wins"],   errors="coerce")
    df["feature_past_top3"]   = pd.to_numeric(df["feature_past_top3"],   errors="coerce")
    df["feature_past_win_rate"] = np.where(
        df["feature_past_starts"] > 0, df["feature_past_wins"] / df["feature_past_starts"], np.nan
    )
    df["feature_past_fukusho_rate"] = np.where(
        df["feature_past_starts"] > 0, df["feature_past_top3"] / df["feature_past_starts"], np.nan
    )

    df = _apply_course_features(df)

    race_date   = df["race_date"].iloc[0]
    keibajo     = df["keibajo_code"].iloc[0]
    race_num    = int(str(race_id)[-2:])
    db_race_id  = _derive_db_race_id(race_date, keibajo, race_num)
    horse_ids   = df["horse_id"].astype(str).tolist()
    jockey_cds  = df["jockey_cd"].astype(str).tolist()
    trainer_cds = df["trainer_cd"].astype(str).tolist()

    stores = _fetch_feature_stores(db_race_id, horse_ids, jockey_cds, trainer_cds, race_date)
    df["horse_id"] = df["horse_id"].astype(str)

    for store_df in [stores["hr"], stores["cs"], stores["apt"]]:
        if store_df.empty:
            continue
        store_df = store_df.copy()
        store_df["horse_id"] = store_df["horse_id"].astype(str)
        cols = [c for c in store_df.columns if c != "race_id"]
        df = df.merge(store_df[cols], on="horse_id", how="left")

    for store_df, join_col in [(stores["jf"], "jockey_cd"), (stores["tf"], "trainer_cd")]:
        if store_df.empty:
            continue
        store_df = store_df.copy()
        store_df[join_col] = store_df[join_col].astype(str)
        df = df.merge(store_df, on=join_col, how="left")

    if not stores["trf"].empty:
        trf = stores["trf"].copy()
        trf["horse_id"] = trf["horse_id"].astype(str)
        df = df.merge(trf, on="horse_id", how="left")

    df = _prepare_numerics(df)

    # 6 サブモデルでスコア計算
    df = _get_submodel_set().score(df)

    X = df[FEATURES_SUBMODEL].copy()
    return df, X


# ── エンドポイント ────────────────────────────────────────────────────────────

@router.get("/predict/{race_id}", response_model=RacePredictionResponse)
def predict_race(race_id: str) -> RacePredictionResponse:
    logger.info("[V2Predict] race_id=%s", race_id)

    try:
        ensemble = _get_ensemble()
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e))

    try:
        raw_df, X = _build_features(race_id)
    except Exception as e:
        logger.exception("[V2Predict] 特徴量構築エラー: %s", e)
        raise HTTPException(status_code=500, detail=f"特徴量構築エラー: {e}")

    if raw_df.empty:
        raise HTTPException(status_code=404, detail=f"レースが見つかりません: {race_id}")

    model_features = ensemble.feature_names
    for f in model_features:
        if f not in X.columns:
            X[f] = np.nan
    scores   = ensemble.predict(X[model_features])
    ai_ranks = pd.Series(scores).rank(ascending=False, method="min").astype(int).tolist()

    if "ninki" in raw_df.columns and raw_df["ninki"].notna().any():
        odds_ranks = raw_df["ninki"].rank(ascending=True, method="min").where(raw_df["ninki"].notna())
    elif "tan_odds" in raw_df.columns and raw_df["tan_odds"].notna().any():
        odds_ranks = raw_df["tan_odds"].rank(ascending=True, method="min").where(raw_df["tan_odds"].notna())
    else:
        odds_ranks = pd.Series([None] * len(raw_df))

    is_confirmed = (
        "kakutei_chakujun" in raw_df.columns
        and raw_df["kakutei_chakujun"].notna().any()
    )

    def _safe_float(v) -> float | None:
        if v is None:
            return None
        try:
            f = float(v)
            return None if math.isnan(f) else f
        except (TypeError, ValueError):
            return None

    def _safe_int(v) -> int | None:
        f = _safe_float(v)
        return int(f) if f is not None else None

    horses = []
    for i, row in raw_df.reset_index(drop=True).iterrows():
        horses.append(HorsePrediction(
            umaban=int(row["umaban"]),
            horse_id=str(row["horse_id"]),
            horse_name=row.get("horse_name") or None,
            ai_score=round(float(scores[i]), 6),
            ai_rank=int(ai_ranks[i]),
            tan_odds=_safe_float(row.get("tan_odds")),
            odds_rank=_safe_int(odds_ranks.iloc[i]),
            actual_rank=_safe_int(row.get("kakutei_chakujun")),
        ))

    horses.sort(key=lambda h: h.ai_rank)

    return RacePredictionResponse(
        race_id=race_id,
        race_date=str(pd.Timestamp(raw_df["race_date"].iloc[0]).date()),
        keibajo_code=str(raw_df["keibajo_code"].iloc[0]),
        distance=int(raw_df["distance"].iloc[0]),
        horses=horses,
        model_folds=ensemble.n_folds,
        feature_count=len(model_features),
        is_confirmed=bool(is_confirmed),
    )
