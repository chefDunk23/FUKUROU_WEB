"""
api_v2/routers/prediction.py
==============================
GET /api/v2/predict/{race_id} — V2 デュアルエンジン予測。

処理フロー:
    1. fukurou_keiba_v2 からレース情報・出走馬・過去走統計を取得
    2. course_physical_master.csv でコース物理特徴量を JOIN
    3. fukurou_jvdl のフィーチャーストアで特徴量を拡充
    4. 6 サブモデルでスコアを計算（score_ability_v2 ... score_pedigree_v1）
    5. サーフェス判定:
       - 芝  → 6-submodel アンサンブル (models/v2/ensemble/)
       - ダート → 4-submodel アンサンブル (models/v2/ensemble_dirt/) ※調教・血統除外
    6. AI スコア降順で並べた JSON を返す（used_model_type を付与）
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
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from api_v2.deps import rate_limit_predict
from shared.config import PATHS
from shared.db.jvdata import get_conn as get_v2_conn
from shared.db.jvdl import get_conn as get_jvdl_conn
from shared.services.model_version import get_model_version
from src.features.ability_features_v3 import (
    ABILITY_V3_COLS,
    create_ability_features_v3,
)
from src.features.course_features_v3 import COURSE_V3_COLS, create_course_features_v3
from src.features.pace_features_v4 import PACE_V4_COLS, create_pace_features_v4
from src.features.pace_simulation_v1 import PACE_SIM_COLS, create_pace_simulation_features
from src.features.pedigree_features_v1 import PEDIGREE_V1_COLS, create_pedigree_features_v1
from src.features.track_code_aliases import TRACK_CODE_ALIASES
from src.models.feature_labels import get_label as _get_label
from src.models.submodel_registry import SubmodelManager
from src.models.v2.config import (
    FEATURES_APTITUDE,
    FEATURES_AUX,
    FEATURES_CHOKYO,
    FEATURES_JOCKEY,
    FEATURES_PAST_PERF,
    FEATURES_SUBMODEL,
    FEATURES_TRAINER,
    FEATURES_TRAINING,
    GRADE_CODE_MAP,
    NUMERIC_CODE_COLS,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v2", tags=["v2-predict"])

# ── レスポンス型 ──────────────────────────────────────────────────────────────

# ── 証拠・貢献度モデル（?include_evidence=true 時のみ付与）──────────────────

class FeatureContribution(BaseModel):
    """1特徴量の SHAP 貢献量と元データ値。id は特徴量カラム名。"""
    id:           str
    label:        str | None = None
    value:        float | None = None
    contribution: float


class SubModelEvidence(BaseModel):
    """サブモデル 1 種分：スコア・メインへの貢献・主要特徴量リスト。"""
    id:                str
    label:             str | None = None
    score:             float
    shap_contribution: float
    top_features:      list[FeatureContribution]


class MainEnsembleEvidence(BaseModel):
    """メインアンサンブルの SHAP 内訳（入力 = サブモデルスコア 6 列）。"""
    base_value: float
    features:   list[FeatureContribution]


class HorseEvidence(BaseModel):
    """馬 1 頭の予測根拠（サブモデル層 + メインアンサンブル層の二層構造）。"""
    sub_models:    list[SubModelEvidence]
    main_ensemble: MainEnsembleEvidence


class HorsePrediction(BaseModel):
    umaban: int
    horse_id: str
    horse_name: str | None
    ai_score: float
    ai_rank: int
    tan_odds: float | None
    odds_rank: int | None
    actual_rank: int | None
    submodel_scores: dict[str, float]
    evidence: HorseEvidence | None = None


class RacePredictionResponse(BaseModel):
    race_id: str
    race_date: str
    keibajo_code: str
    distance: int
    horses: list[HorsePrediction]
    model_folds: int
    feature_count: int
    is_confirmed: bool
    ai_name: str = "更新中AI"
    ai_description: str = "V2スタック6サブモデル → LambdaRankアンサンブル"
    used_model_type: str = "6submodel_turf"


# ── メインアンサンブル（起動時1回ロード）────────────────────────────────────

# 芝: 全6サブモデル使用, ダート: 調教・血統を除いた4サブモデル
_TURF_SUBMODEL_SCORES: list[str] = [
    "score_ability_v2", "score_course_v2", "score_team_v2",
    "score_training_v2", "score_pace_v2", "score_pedigree_v1",
]
_DIRT_SUBMODEL_SCORES: list[str] = [
    "score_ability_v2", "score_course_v2", "score_team_v2", "score_pace_v2",
]


class _V2Ensemble:
    def __init__(self, model_dir: Path) -> None:
        fold_files = sorted(model_dir.glob("lgbm_rank_fold*.lgb"))
        if not fold_files:
            raise FileNotFoundError(
                f"V2モデルが見つかりません: {model_dir}/lgbm_rank_fold*.lgb\n"
                "先に scripts/train_v2_ensemble.py を実行してください。"
            )
        self._models = [lgb.Booster(model_file=str(p)) for p in fold_files]
        self._feature_names: list[str] = self._models[0].feature_name()
        logger.info("[V2Ensemble] %d モデルロード完了 (%s)", len(self._models), model_dir.name)

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        scores = np.stack([m.predict(X[self._feature_names]) for m in self._models])
        return scores.mean(axis=0)

    def predict_contrib(self, X: pd.DataFrame) -> np.ndarray:
        """SHAP 貢献度を fold 平均で返す。shape: (n_samples, n_features + 1)。"""
        contribs = np.stack([
            m.predict(X[self._feature_names], pred_contrib=True)
            for m in self._models
        ])
        return contribs.mean(axis=0)

    @property
    def n_folds(self) -> int:
        return len(self._models)

    @property
    def feature_names(self) -> list[str]:
        return self._feature_names


class _DualEngine:
    """芝用（6-submodel）とダート用（4-submodel）の2アンサンブルをメモリ上で保持するシングルトン。"""

    def __init__(self) -> None:
        self.turf = _V2Ensemble(PATHS.model_dir_v2)
        self.dirt = _V2Ensemble(PATHS.model_dir_v2_dirt)
        logger.info(
            "[DualEngine] turf=%d folds / dirt=%d folds ロード完了",
            self.turf.n_folds, self.dirt.n_folds,
        )


_dual_engine: _DualEngine | None = None


def _get_dual_engine() -> _DualEngine:
    global _dual_engine
    if _dual_engine is None:
        _dual_engine = _DualEngine()
    return _dual_engine


def _detect_surface(track_code: str) -> str:
    """JV-Data track_code から推論エンジン種別を返す。
    芝 (10-22) → "turf" (6-submodel)
    ダート (23-29) + 障害 (51-59) → "dirt" (4-submodel)
    先頭文字比較では 20-22 が誤判定されるため整数範囲で判定する。
    """
    try:
        t = int(float(str(track_code).strip()))
    except (TypeError, ValueError):
        return "turf"
    return "dirt" if (23 <= t <= 29 or 51 <= t <= 59) else "turf"


# ── サブモデル群（起動時1回ロード）─────────────────────────────────────────────

_SUBMODEL_NAMES = [
    "ability_v2", "course_v2", "team_v2",
    "training_v2", "pace_v2", "pedigree_v1",
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

    def score_with_contrib(self, df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
        """score() と同じスコアを付けつつ SHAP 配列も返す。
        contrib_map: {submodel_name: {"feature_cols": [...], "shap": ndarray(n, n_feat+1)}}
        """
        result = df.copy()
        contrib_map: dict[str, dict] = {}
        for name, (booster, feature_cols) in self._submodels.items():
            X = result.reindex(columns=feature_cols)
            result[f"score_{name}"] = booster.predict(X)
            contrib_map[name] = {
                "feature_cols": feature_cols,
                "shap": booster.predict(X, pred_contrib=True),
            }
        return result, contrib_map


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

# ── jvdl フォールバック SQL（keiba_v2 にデータがない今週末レース用）────────────

_SQL_JVDL_RACE_ENTRIES = """
WITH all_confirmed AS (
    SELECT e.race_id, e.horse_id, r.date AS race_date, e.confirmed_rank AS kakutei_chakujun
    FROM   race_entries e
    JOIN   races r ON e.race_id = r.id
    WHERE  e.confirmed_rank IS NOT NULL
      AND  e.confirmed_rank > 0
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
),
target_entries AS (
    SELECT *,
        ROW_NUMBER() OVER (ORDER BY horse_id)::integer AS entry_order
    FROM   race_entries
    WHERE  race_id = %s
)
SELECT
    r.id              AS race_id,
    r.date::date      AS race_date,
    r.race_number     AS race_num,
    r.place_code      AS keibajo_code,
    COALESCE(NULLIF(TRIM(r.name), ''), '') AS race_name_hondai,
    r.distance,
    CASE r.course_type
        WHEN '芝'     THEN '10'
        WHEN 'ダート' THEN '23'
        WHEN '障害'   THEN '51'
        ELSE '10'
    END               AS track_code,
    NULL::text        AS course_kubun,
    r.grade_code,
    r.track_condition AS jvdl_track_condition,
    r.weather         AS jvdl_weather,
    NULL::text        AS shiba_baba_code,
    NULL::text        AS dirt_baba_code,
    r.zenhan_3f       AS zen_3f,
    r.kohan_3f        AS go_3f,
    NULL::text[]      AS lap_time_array,
    COALESCE(NULLIF(e.horse_number::integer, 0), e.entry_order) AS umaban,
    e.horse_id,
    h.name            AS horse_name,
    h.sire_id,
    h.bms_id,
    e.trainer_id      AS trainer_cd,
    e.jockey_id       AS jockey_cd,
    e.horse_weight,
    e.horse_weight_diff AS weight_diff,
    e.weight          AS basis_weight,
    CASE WHEN e.win_odds  > 0 THEN e.win_odds  ELSE NULL END AS tan_odds,
    CASE WHEN e.popularity > 0 THEN e.popularity ELSE NULL END AS ninki,
    CASE WHEN e.confirmed_rank > 0 THEN e.confirmed_rank ELSE NULL END AS kakutei_chakujun,
    COALESCE(ps.feature_past_starts, 0) AS feature_past_starts,
    COALESCE(ps.feature_past_wins,   0) AS feature_past_wins,
    COALESCE(ps.feature_past_top3,   0) AS feature_past_top3
FROM   races r
JOIN   target_entries e ON e.race_id = r.id
LEFT   JOIN horses   h ON h.id = e.horse_id
LEFT   JOIN past_stats ps ON ps.race_id = r.id AND ps.horse_id = e.horse_id
ORDER  BY COALESCE(NULLIF(e.horse_number::integer, 0), e.entry_order)
"""


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
    NULLIF(TRIM(r.joken_code_2), '000') AS jyoken_cd_2,
    NULLIF(TRIM(r.joken_code_3), '000') AS jyoken_cd_3,
    NULLIF(TRIM(r.joken_code_4), '000') AS jyoken_cd_4,
    NULLIF(TRIM(r.joken_code_5), '000') AS jyoken_cd_5,
    e.umaban, e.horse_id, e.horse_name, e.trainer_cd, e.jockey_cd,
    e.horse_weight, e.weight_diff, e.basis_weight,
    e.tan_odds, e.ninki, e.kakutei_chakujun,
    e.corner_1, e.corner_2, e.corner_3, e.corner_4, e.go_3f_time,
    NULL::text AS sire_id, NULL::text AS bms_id,
    COALESCE(ps.feature_past_starts, 0) AS feature_past_starts,
    COALESCE(ps.feature_past_wins,   0) AS feature_past_wins,
    COALESCE(ps.feature_past_top3,   0) AS feature_past_top3
FROM   races r
JOIN   race_entries e ON e.race_id = r.id
LEFT   JOIN past_stats ps ON ps.race_id = r.id AND ps.horse_id = e.horse_id
WHERE  r.id = %s
ORDER  BY e.umaban
"""


_SQL_HORSE_HISTORY = """
SELECT
    e.horse_id,
    r.id          AS race_id,
    r.race_date,
    e.kakutei_chakujun,
    r.grade_code,
    NULLIF(TRIM(r.joken_code_2), '000') AS jyoken_cd_2,
    NULLIF(TRIM(r.joken_code_3), '000') AS jyoken_cd_3,
    NULLIF(TRIM(r.joken_code_4), '000') AS jyoken_cd_4,
    NULLIF(TRIM(r.joken_code_5), '000') AS jyoken_cd_5,
    e.umaban,
    e.corner_1,
    e.corner_2,
    e.corner_3,
    e.corner_4,
    e.go_3f_time,
    r.distance,
    r.track_code,
    r.keibajo_code,
    e.horse_weight,
    (SELECT MAX(e2.umaban)
     FROM   race_entries e2
     WHERE  e2.race_id = e.race_id) AS field_size
FROM   race_entries e
JOIN   races r ON e.race_id = r.id
WHERE  e.horse_id = ANY(%s)
  AND  r.race_date < %s
  AND  e.kakutei_chakujun IS NOT NULL
  AND  e.kakutei_chakujun > 0
ORDER  BY e.horse_id, r.race_date, r.id
"""


def _fetch_horse_history(horse_ids: list[str], race_date) -> pd.DataFrame:
    date_val = pd.Timestamp(race_date).date()
    with get_v2_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(_SQL_HORSE_HISTORY, (horse_ids, date_val))
            rows = cur.fetchall()
    return pd.DataFrame(rows)


_HIST_COLS = [
    "horse_id", "race_id", "race_date", "kakutei_chakujun",
    "grade_code", "jyoken_cd_2", "jyoken_cd_3", "jyoken_cd_4", "jyoken_cd_5",
    "umaban", "corner_1", "corner_2", "corner_3", "corner_4",
    "go_3f_time", "distance", "track_code", "keibajo_code", "horse_weight", "field_size",
]


def _compute_rolling_features(
    df: pd.DataFrame,
    target_race_id: str,
    race_date,
) -> pd.DataFrame:
    """
    ability_v3 と pace_v4 のローリング特徴量をリアルタイム計算する。
    学習時の enrich_ability_v3.py / enrich_pace_v4.py と同等の処理を推論時に再現。
    失敗時は NaN のまま df を返す（サブモデルは reindex で domain default に準じた値で処理）。
    """
    try:
        horse_ids = df["horse_id"].astype(str).tolist()
        hist = _fetch_horse_history(horse_ids, race_date)

        # current-race stub: 結果列を NaN にしてリーク防止
        _stub_cols = ["horse_id", "race_date", "umaban", "distance", "track_code",
                      "keibajo_code", "grade_code",
                      "jyoken_cd_2", "jyoken_cd_3", "jyoken_cd_4", "jyoken_cd_5"]
        stub = df[[c for c in _stub_cols if c in df.columns]].copy()
        stub["horse_id"] = stub["horse_id"].astype(str)
        stub["race_id"] = str(target_race_id)
        stub["kakutei_chakujun"] = np.nan
        stub["corner_1"] = np.nan
        stub["corner_2"] = np.nan
        stub["corner_3"] = np.nan
        stub["corner_4"] = np.nan
        stub["go_3f_time"] = np.nan
        stub["field_size"] = (
            stub.groupby("race_id")["umaban"].transform("max").clip(lower=1)
        )

        if not hist.empty:
            hist["horse_id"] = hist["horse_id"].astype(str)
            hist["race_id"]  = hist["race_id"].astype(str)
            hist = hist.reindex(columns=_HIST_COLS)
        else:
            hist = pd.DataFrame(columns=_HIST_COLS)

        stub = stub.reindex(columns=_HIST_COLS)
        combined = pd.concat([hist, stub], ignore_index=True)

        # ability_v3 は confirmed_rank カラムを要求
        combined["confirmed_rank"] = pd.to_numeric(
            combined["kakutei_chakujun"], errors="coerce"
        )
        combined["grade_code"] = combined["grade_code"].fillna("").astype(str)

        enriched = create_ability_features_v3(combined)
        enriched = create_pace_features_v4(enriched)
        enriched = create_course_features_v3(enriched)

        # 当該レースの行だけ抽出して df に戻す
        target_rows = (
            enriched[enriched["race_id"] == str(target_race_id)]
            .set_index("horse_id")
        )
        df = df.copy()
        df["horse_id"] = df["horse_id"].astype(str)
        for col in ABILITY_V3_COLS + PACE_V4_COLS + COURSE_V3_COLS:
            if col in target_rows.columns:
                df[col] = df["horse_id"].map(target_rows[col])


        # ── keiba_v2 の過去走から通算成績・馬体重を補完 ────────────────────────
        # jvdl フォールバック時は window 関数が keiba_v2 履歴を参照できないため
        # feature_past_* が 0 になる。hist から直接集計して上書きする。
        if not hist.empty:
            horse_map = df["horse_id"]
            hist_rank = pd.to_numeric(hist["kakutei_chakujun"], errors="coerce")
            career = (
                hist.assign(_rank=hist_rank)
                .groupby("horse_id")
                .agg(
                    _starts=("_rank", "count"),
                    _wins=("_rank", lambda x: (x == 1).sum()),
                    _top3=("_rank", lambda x: (x <= 3).sum()),
                )
            )
            career["_win_rate"] = np.where(
                career["_starts"] > 0,
                career["_wins"] / career["_starts"],
                np.nan,
            )
            career["_fukusho_rate"] = np.where(
                career["_starts"] > 0,
                career["_top3"] / career["_starts"],
                np.nan,
            )
            for col, src in [
                ("feature_past_starts",       "_starts"),
                ("feature_past_wins",         "_wins"),
                ("feature_past_top3",         "_top3"),
                ("feature_past_win_rate",     "_win_rate"),
                ("feature_past_fukusho_rate", "_fukusho_rate"),
            ]:
                if col not in df.columns:
                    df[col] = np.nan
                computed = horse_map.map(career[src])
                mask = df[col].isna() | (df[col] == 0)
                df.loc[mask, col] = computed[mask]

            # 馬体重: 当日確定前は 0/NULL → keiba_v2 の前走体重で補完
            valid_wt = hist[hist["horse_weight"].notna() & (hist["horse_weight"] > 0)]
            if not valid_wt.empty:
                latest_weight = (
                    valid_wt
                    .sort_values("race_date")
                    .groupby("horse_id")["horse_weight"]
                    .last()
                )
                if "horse_weight" not in df.columns:
                    df["horse_weight"] = np.nan
                wt_mask = df["horse_weight"].isna() | (df["horse_weight"] == 0)
                df.loc[wt_mask, "horse_weight"] = horse_map.map(latest_weight)[wt_mask]

        logger.info(
            "[RollingFeatures] 完了: race_id=%s hist=%d行 cols=%d",
            target_race_id, len(hist),
            len(ABILITY_V3_COLS) + len(PACE_V4_COLS) + len(COURSE_V3_COLS),
        )
    except Exception as exc:
        logger.warning("[RollingFeatures] 計算失敗 (NaN フォールバック): %s", exc)
        df = df.copy()
        for col in ABILITY_V3_COLS + PACE_V4_COLS + COURSE_V3_COLS + PEDIGREE_V1_COLS:
            if col not in df.columns:
                df[col] = np.nan

    return df


def _fill_lineage_from_jvdl(df: pd.DataFrame, horse_ids: list[str]) -> pd.DataFrame:
    """fukurou_jvdl.horses から sire_id / bms_id / sex / birthday を取得して df に付与する。"""
    try:
        with get_jvdl_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT id AS horse_id, sire_id, bms_id, sex, birthday "
                    "FROM horses WHERE id = ANY(%s)",
                    (horse_ids,),
                )
                rows = cur.fetchall()
        if not rows:
            return df
        lineage = pd.DataFrame(rows)
        lineage["horse_id"] = lineage["horse_id"].astype(str)
        lineage["birthday"] = pd.to_datetime(lineage["birthday"], errors="coerce")
        df = df.copy()
        df["horse_id"] = df["horse_id"].astype(str)
        df = df.merge(lineage, on="horse_id", how="left", suffixes=("", "_jvdl"))
        for col in ("sire_id", "bms_id"):
            jvdl_col = f"{col}_jvdl"
            if jvdl_col in df.columns:
                df[col] = df[col].combine_first(df[jvdl_col])
                df = df.drop(columns=[jvdl_col])
        # horse_age: レース日時点の年齢
        race_date = pd.to_datetime(df.get("race_date"), errors="coerce")
        if "birthday_jvdl" in df.columns:
            df["birthday"] = df["birthday"].combine_first(df["birthday_jvdl"])
            df = df.drop(columns=["birthday_jvdl"])
        df["horse_age"] = (race_date - df.get("birthday", pd.NaT)).dt.days / 365.25
        # horse_sex
        for col in ("sex",):
            jvdl_col = f"{col}_jvdl"
            if jvdl_col in df.columns:
                df[col] = df.get(col, pd.Series("1", index=df.index)).combine_first(df[jvdl_col])
                df = df.drop(columns=[jvdl_col])
        df["horse_sex"] = pd.to_numeric(
            df.get("sex", pd.Series(1, index=df.index)), errors="coerce"
        ).fillna(1).astype(int)
    except Exception as exc:
        logger.warning("[Lineage] jvdl lineage lookup failed: %s", exc)
    return df


def _fetch_race_data(race_id: str) -> pd.DataFrame:
    """
    fukurou_keiba_v2 から取得。データがない場合（今週末の未来レースなど）は
    fukurou_jvdl にフォールバックして同等のカラム構成で返す。
    """
    with get_v2_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(_SQL_RACE_ENTRIES, (race_id,))
            rows = cur.fetchall()

    if rows:
        return pd.DataFrame(rows)

    # keiba_v2 に未登録 → jvdl フォールバック（12文字IDで直接参照）
    logger.info("[predict] keiba_v2 に %s なし → jvdl フォールバック", race_id)
    with get_jvdl_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(_SQL_JVDL_RACE_ENTRIES, (race_id,))
            rows = cur.fetchall()

    if not rows:
        return pd.DataFrame()

    logger.info("[predict] jvdl から %d 頭取得: %s", len(rows), race_id)
    return pd.DataFrame(rows)


def _derive_db_race_id(race_date, keibajo_code: str, race_num: int) -> str:
    return pd.Timestamp(race_date).strftime("%Y%m%d") + str(keibajo_code).zfill(2) + str(race_num).zfill(2)


def _fetch_feature_stores(
    db_race_id: str,
    horse_ids: list[str],
    jockey_cds: list[str],
    trainer_cds: list[str],
    race_date,
    sire_ids: list[str] | None = None,
    bms_ids: list[str] | None = None,
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

            # jockey/trainer/sire は「race_date 以前の最新 target_date」で取得
            # フィーチャーストアが今週分まで更新されていない場合でも直前週のデータを使う
            cur.execute(
                "SELECT MAX(target_date) FROM jockey_feature_store WHERE target_date <= %s",
                (date_val,),
            )
            jf_date = cur.fetchone()["max"] or date_val

            cur.execute(
                "SELECT kishu_code AS jockey_cd, "
                "       win_rate AS jockey_win_rate, "
                "       surface_turf_win_rate AS jockey_turf_win_rate, "
                "       surface_dirt_win_rate AS jockey_dirt_win_rate, "
                "       surface_turf_win_shift AS jockey_turf_win_shift, "
                "       surface_dirt_win_shift AS jockey_dirt_win_shift "
                "FROM jockey_feature_store "
                "WHERE kishu_code = ANY(%s) AND target_date = %s",
                (jockey_cds, jf_date),
            )
            jf = pd.DataFrame(cur.fetchall())
            logger.debug("[FeatureStore] jockey date=%s rows=%d", jf_date, len(jf))

            cur.execute(
                "SELECT MAX(target_date) FROM trainer_feature_store WHERE target_date <= %s",
                (date_val,),
            )
            tf_date = cur.fetchone()["max"] or date_val

            cur.execute(
                "SELECT chokyoshi_code AS trainer_cd, "
                "       win_rate AS trainer_win_rate, "
                "       surface_turf_win_rate AS trainer_turf_win_rate, "
                "       surface_dirt_win_rate AS trainer_dirt_win_rate "
                "FROM trainer_feature_store "
                "WHERE chokyoshi_code = ANY(%s) AND target_date = %s",
                (trainer_cds, tf_date),
            )
            tf = pd.DataFrame(cur.fetchall())
            logger.debug("[FeatureStore] trainer date=%s rows=%d", tf_date, len(tf))

            cur.execute(
                "SELECT MAX(target_date) FROM training_feature_store WHERE target_date <= %s",
                (date_val,),
            )
            trf_date = cur.fetchone()["max"] or date_val

            cur.execute(
                "SELECT horse_id, best_z_total, z_trend_slope, avg_accel, "
                "       session_count, slope_ratio "
                "FROM training_feature_store "
                "WHERE horse_id = ANY(%s) AND target_date = %s",
                (horse_ids, trf_date),
            )
            trf = pd.DataFrame(cur.fetchall())
            logger.debug("[FeatureStore] training date=%s rows=%d", trf_date, len(trf))

            # ── sire_feature_store: 父・母父の血統統計 ──────────────────────────
            all_sire_ids = [
                s for s in ((sire_ids or []) + (bms_ids or []))
                if s and str(s).strip()
            ]
            sire_store = pd.DataFrame()
            if all_sire_ids:
                cur.execute(
                    "SELECT MAX(target_date) FROM sire_feature_store WHERE target_date <= %s",
                    (date_val,),
                )
                sf_date = cur.fetchone()["max"] or date_val

                _SIRE_STAT_COLS = (
                    "total_count, win_rate, top3_rate, "
                    "surface_turf_win_rate, surface_dirt_win_rate, "
                    "dist_sprint_win_rate, dist_mile_win_rate, "
                    "dist_middle_win_rate, dist_long_win_rate, "
                    "venue_01_win_rate, venue_02_win_rate, venue_03_win_rate, "
                    "venue_04_win_rate, venue_05_win_rate, venue_06_win_rate, "
                    "venue_07_win_rate, venue_08_win_rate, venue_09_win_rate, "
                    "venue_10_win_rate"
                )
                cur.execute(
                    f"SELECT sire_id, {_SIRE_STAT_COLS} "
                    "FROM sire_feature_store "
                    "WHERE sire_id = ANY(%s) AND target_date = %s",
                    (all_sire_ids, sf_date),
                )
                sire_store = pd.DataFrame(cur.fetchall())
                logger.debug("[FeatureStore] sire date=%s rows=%d", sf_date, len(sire_store))

    return {"hr": hr, "cs": cs, "apt": apt, "jf": jf, "tf": tf, "trf": trf, "sire_store": sire_store}


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


# ── SHAP 証拠計算 ─────────────────────────────────────────────────────────────

_SUBMODEL_LABELS: dict[str, str] = {
    "ability_v2":  "基礎能力",
    "course_v2":   "コース適性",
    "team_v2":     "人馬チーム",
    "training_v2": "調教仕上がり",
    "pace_v2":     "ペース展開",
    "pedigree_v1": "血統適性",
}
_TOP_FEATURES_N = 5


def _safe_float_or_none(v) -> float | None:
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _compute_horse_evidence(
    raw_df: pd.DataFrame,
    X_main: pd.DataFrame,
    contrib_map: dict,
    ensemble: _V2Ensemble,
) -> list[HorseEvidence]:
    """各馬の予測根拠（SHAP値 + 生データ）を組み立てる。"""
    main_shap_full = ensemble.predict_contrib(X_main)   # (n, n_feats + 1)
    main_base_vals = main_shap_full[:, -1]
    main_shap      = main_shap_full[:, :-1]             # (n, n_feats)
    main_feats     = ensemble.feature_names             # ["score_ability_v2", ...]

    evidences: list[HorseEvidence] = []
    for i in range(len(raw_df)):
        # ── サブモデル層 ─────────────────────────────────────────────────────
        sub_evidences: list[SubModelEvidence] = []
        for j, mfeat in enumerate(main_feats):
            sub_name      = mfeat.removeprefix("score_")
            sub_score     = _safe_float_or_none(X_main.iloc[i].get(mfeat)) or 0.0
            shap_to_main  = float(main_shap[i, j])

            top_features: list[FeatureContribution] = []
            if sub_name in contrib_map:
                cm        = contrib_map[sub_name]
                row_shap  = cm["shap"][i, :-1]
                feat_cols = cm["feature_cols"]
                for idx in np.argsort(np.abs(row_shap))[::-1][:_TOP_FEATURES_N]:
                    fid   = feat_cols[idx]
                    raw_v = _safe_float_or_none(
                        raw_df.iloc[i].get(fid) if fid in raw_df.columns else None
                    )
                    if raw_v is not None:
                        raw_v = round(raw_v, 4)
                    top_features.append(FeatureContribution(
                        id=fid,
                        label=_get_label(fid),
                        value=raw_v,
                        contribution=round(float(row_shap[idx]), 4),
                    ))

            sub_evidences.append(SubModelEvidence(
                id=sub_name,
                label=_SUBMODEL_LABELS.get(sub_name),
                score=round(sub_score, 4),
                shap_contribution=round(shap_to_main, 4),
                top_features=top_features,
            ))

        # ── メインアンサンブル層 ─────────────────────────────────────────────
        main_features = [
            FeatureContribution(
                id=mf,
                label=_get_label(mf),
                value=(
                    round(float(X_main.iloc[i][mf]), 4)
                    if _safe_float_or_none(X_main.iloc[i].get(mf)) is not None else None
                ),
                contribution=round(float(main_shap[i, k]), 4),
            )
            for k, mf in enumerate(main_feats)
        ]

        evidences.append(HorseEvidence(
            sub_models=sub_evidences,
            main_ensemble=MainEnsembleEvidence(
                base_value=round(float(main_base_vals[i]), 4),
                features=main_features,
            ),
        ))
    return evidences


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

def _build_features(
    race_id: str,
    include_contrib: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame, dict | None]:
    """raw_df, X, contrib_map を返す（include_contrib=False 時 contrib_map は None）。"""
    df = _fetch_race_data(race_id)
    if df.empty:
        return pd.DataFrame(), pd.DataFrame()

    df = df.copy()

    # ability_v3 / pace_v4 ローリング特徴量をリアルタイム計算（推論ギャップ解消）
    df = _compute_rolling_features(df, race_id, df["race_date"].iloc[0])

    # 展開シミュレーション特徴量（pace_v2 サブモデルへ渡す）
    # avg_c4_norm_5 は全距離で利用可能。_compute_rolling_features 完了後に呼ぶ。
    try:
        df = create_pace_simulation_features(df)
    except Exception as _pace_exc:
        logger.warning("[PaceSim] 推論時の展開シミュレーション計算失敗 → NaN フォールバック: %s", _pace_exc)
        for _col in PACE_SIM_COLS:
            if _col not in df.columns:
                df[_col] = np.nan

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

    # sire_id / bms_id が NULL（keiba_v2 パス）の場合は jvdl.horses から補完
    if "sire_id" not in df.columns or df["sire_id"].isna().all():
        df = _fill_lineage_from_jvdl(df, horse_ids)

    sire_ids = df["sire_id"].dropna().astype(str).tolist() if "sire_id" in df.columns else []
    bms_ids  = df["bms_id"].dropna().astype(str).tolist()  if "bms_id"  in df.columns else []

    stores = _fetch_feature_stores(
        db_race_id, horse_ids, jockey_cds, trainer_cds, race_date,
        sire_ids=sire_ids, bms_ids=bms_ids,
    )
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

    # ── 血統特徴量 (pedigree_v1) ──────────────────────────────────────────────
    sire_store = stores.get("sire_store")
    if sire_store is not None and not sire_store.empty:
        sire_store = sire_store.copy()
        sire_store["sire_id"] = sire_store["sire_id"].astype(str)
        stat_cols = [c for c in sire_store.columns if c != "sire_id"]

        # 父統計: df.sire_id → sire_store
        if "sire_id" in df.columns:
            sire_rename = {c: f"sire_{c}" for c in stat_cols}
            df = df.merge(
                sire_store.rename(columns={"sire_id": "sire_id"} | sire_rename),
                on="sire_id", how="left",
            )

        # 母父統計: df.bms_id → sire_store
        if "bms_id" in df.columns:
            bms_rename = {c: f"bms_{c}" for c in stat_cols}
            df = df.merge(
                sire_store.rename(columns={"sire_id": "bms_id"} | bms_rename),
                on="bms_id", how="left",
            )

        df = create_pedigree_features_v1(df)
        # 中間列クリーンアップ
        raw_pedigree = [
            c for c in df.columns
            if (c.startswith("sire_") or c.startswith("bms_"))
            and c not in PEDIGREE_V1_COLS
            and c not in ("sire_id", "bms_id")
        ]
        df = df.drop(columns=raw_pedigree)
    else:
        for col in PEDIGREE_V1_COLS:
            if col not in df.columns:
                df[col] = np.nan

    df = _prepare_numerics(df)

    # 6 サブモデルでスコア計算
    if include_contrib:
        df, contrib_map = _get_submodel_set().score_with_contrib(df)
    else:
        df = _get_submodel_set().score(df)
        contrib_map = None

    X = df[FEATURES_SUBMODEL].copy()
    return df, X, contrib_map


# ── エンドポイント ────────────────────────────────────────────────────────────

# ── DBキャッシュ（race_predictions テーブル） ────────────────────────────────

_SQL_CACHE_SELECT = """
    SELECT payload FROM race_predictions
    WHERE race_id = %s AND model_version = %s
"""
_SQL_CACHE_UPSERT = """
    INSERT INTO race_predictions (race_id, model_version, predicted_at, payload)
    VALUES (%s, %s, now(), %s)
    ON CONFLICT (race_id) DO UPDATE
      SET model_version = EXCLUDED.model_version,
          predicted_at  = EXCLUDED.predicted_at,
          payload       = EXCLUDED.payload
"""


def _get_cached_prediction(race_id: str) -> "RacePredictionResponse | None":
    """race_predictions からモデルバージョン一致のキャッシュを取得する。障害時は None を返す。"""
    model_ver = get_model_version()
    try:
        with get_jvdl_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(_SQL_CACHE_SELECT, (race_id, model_ver))
                row = cur.fetchone()
        if row is None:
            return None
        return RacePredictionResponse.model_validate(row["payload"])
    except Exception:
        logger.exception("[V2Predict] キャッシュ読み取り失敗 race_id=%s", race_id)
        return None


def _save_prediction_cache(resp: "RacePredictionResponse") -> None:
    """race_predictions テーブルに予測結果を UPSERT する。障害時はログのみ（予測は返す）。"""
    model_ver = get_model_version()
    try:
        with get_jvdl_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    _SQL_CACHE_UPSERT,
                    (resp.race_id, model_ver, psycopg2.extras.Json(resp.model_dump(mode="json"))),
                )
            conn.commit()
    except Exception:
        logger.exception("[V2Predict] キャッシュ保存失敗 race_id=%s", resp.race_id)


# ── コア予測計算（エンドポイント・バッチ共用） ───────────────────────────────

def _compute_prediction(
    race_id: str,
    include_evidence: bool = False,
) -> "RacePredictionResponse | None":
    """予測を計算して返す。モデル未ロードは FileNotFoundError を送出。レース未発見は None を返す。"""
    dual_engine = _get_dual_engine()  # FileNotFoundError を呼び出し元に伝播

    raw_df, X_all, contrib_map = _build_features(race_id, include_contrib=include_evidence)

    if raw_df.empty:
        return None

    # サーフェス判定 → エンジン・特徴量列を選択
    track_code = str(raw_df["track_code"].iloc[0]) if "track_code" in raw_df.columns else "10"
    surface = _detect_surface(track_code)
    if surface == "dirt":
        engine = dual_engine.dirt
        active_submodel_scores = _DIRT_SUBMODEL_SCORES
        used_model_type = "4submodel_dirt"
        ai_description = "V2スタック4サブモデル(ダート) → LambdaRankアンサンブル"
    else:
        engine = dual_engine.turf
        active_submodel_scores = _TURF_SUBMODEL_SCORES
        used_model_type = "6submodel_turf"
        ai_description = "V2スタック6サブモデル(芝) → LambdaRankアンサンブル"

    logger.info("[V2Predict] surface=%s model=%s track_code=%s", surface, used_model_type, track_code)

    X = X_all[active_submodel_scores].copy()
    model_features = engine.feature_names
    for f in model_features:
        if f not in X.columns:
            X[f] = np.nan
    scores   = engine.predict(X[model_features])
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

    # evidence 計算（include_evidence=true の場合のみ）
    evidences: list[HorseEvidence | None] = [None] * len(raw_df)
    if include_evidence and contrib_map is not None:
        try:
            evidences = _compute_horse_evidence(
                raw_df.reset_index(drop=True),
                X[model_features],
                contrib_map,
                engine,
            )
        except Exception as e:
            logger.warning("[V2Predict] evidence計算失敗（予測は返す）: %s", e)

    horses = []
    for i, row in raw_df.reset_index(drop=True).iterrows():
        sub_scores = {
            f: round(float(row[f]), 6)
            for f in FEATURES_SUBMODEL
            if f in raw_df.columns and not math.isnan(float(row[f]) if row[f] is not None else float("nan"))
        }
        horses.append(HorsePrediction(
            umaban=int(row["umaban"]),
            horse_id=str(row["horse_id"]),
            horse_name=row.get("horse_name") or None,
            ai_score=round(float(scores[i]), 6),
            ai_rank=int(ai_ranks[i]),
            tan_odds=_safe_float(row.get("tan_odds")),
            odds_rank=_safe_int(odds_ranks.iloc[i]),
            actual_rank=_safe_int(row.get("kakutei_chakujun")),
            submodel_scores=sub_scores,
            evidence=evidences[i],
        ))

    horses.sort(key=lambda h: h.ai_rank)

    return RacePredictionResponse(
        race_id=race_id,
        race_date=str(pd.Timestamp(raw_df["race_date"].iloc[0]).date()),
        keibajo_code=str(raw_df["keibajo_code"].iloc[0]),
        distance=int(raw_df["distance"].iloc[0]),
        horses=horses,
        model_folds=engine.n_folds,
        feature_count=len(model_features),
        is_confirmed=bool(is_confirmed),
        used_model_type=used_model_type,
        ai_description=ai_description,
    )


@router.get("/predict/{race_id}", response_model=RacePredictionResponse)
def predict_race(
    race_id: str,
    include_evidence: bool = Query(
        False,
        description="true にすると各馬の SHAP 貢献度・根拠特徴量を evidence フィールドに付与する",
    ),
    _: None = Depends(rate_limit_predict),
) -> RacePredictionResponse:
    logger.info("[V2Predict] race_id=%s include_evidence=%s", race_id, include_evidence)

    if not include_evidence:
        cached = _get_cached_prediction(race_id)
        if cached is not None:
            logger.info("[V2Predict] cache hit: %s", race_id)
            return cached

    try:
        resp = _compute_prediction(race_id, include_evidence=include_evidence)
    except FileNotFoundError as e:
        logger.error("[V2Predict] モデルファイル未検出: %s", e)
        raise HTTPException(status_code=503, detail="AIモデルが未ロードです。管理者に連絡してください。")
    except Exception as e:
        logger.exception("[V2Predict] 特徴量構築エラー: %s", e)
        raise HTTPException(status_code=500, detail="予測処理でエラーが発生しました")

    if resp is None:
        raise HTTPException(status_code=404, detail=f"レースが見つかりません: {race_id}")

    if not include_evidence:
        _save_prediction_cache(resp)

    return resp
