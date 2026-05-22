"""
api_v2/routers/prediction_legacy.py
=====================================
GET /api/v2/predict-legacy/{race_id} — フクロウ博士AI（PreRace_Model_v1）による予測。

使用モデル: models/v1_legacy/PreRace_Model_v1.txt（175特徴量）
DBから取得できる特徴量: ~31列。残りはNaN（LightGBMの欠損値処理に委ねる）。

このルーターは prediction.py と完全に独立しており、一切の共有状態を持たない。
"""
from __future__ import annotations

import json
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

from shared.db.jvdata import get_conn as get_v2_conn
from shared.db.jvdl import get_conn as get_jvdl_conn

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v2", tags=["v1-legacy-predict"])

_LEGACY_DIR = Path(__file__).parent.parent.parent / "models" / "v1_legacy"

_GRADE_TO_CLASS: dict[str | None, int] = {
    "G": 9, "F": 8, "D": 7, "L": 6,
    "B": 5, "A": 4, "C": 3, "H": 2, "E": 1,
    None: 0,
}

# 重馬場コード（馬場状態コード 3=重, 4=不良）
_HEAVY_BABA_CODES = {"3", "4"}


# ── レスポンス型 ──────────────────────────────────────────────────────────────

class HorsePredictionLegacy(BaseModel):
    umaban: int
    horse_id: str
    horse_name: str | None
    ai_score: float
    ai_rank: int
    tan_odds: float | None
    odds_rank: int | None
    actual_rank: int | None
    submodel_scores: dict[str, float] = {}


class RacePredictionLegacyResponse(BaseModel):
    race_id: str
    race_date: str
    keibajo_code: str
    distance: int
    horses: list[HorsePredictionLegacy]
    model_folds: int
    feature_count: int
    available_features: int
    is_confirmed: bool
    ai_name: str = "フクロウ博士AI"
    ai_description: str = "PreRace_Model_v1（190特徴量）/ 一部NaN補完"


# ── モデルロード（起動時1回）──────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _load_legacy_model() -> tuple[lgb.Booster, list[str]]:
    model_path    = _LEGACY_DIR / "PreRace_Model_v1.txt"
    features_path = _LEGACY_DIR / "PreRace_features.json"
    if not model_path.exists() or not features_path.exists():
        raise FileNotFoundError(
            f"フクロウ博士AIモデルファイルが見つかりません: {_LEGACY_DIR}\n"
            "SETUP.md の Phase 2 手順でモデルをコピーしてください。"
        )
    booster      = lgb.Booster(model_file=str(model_path))
    feature_cols = json.loads(features_path.read_text(encoding="utf-8"))
    logger.info("[LegacyAI] モデルロード完了 features=%d", len(feature_cols))
    return booster, feature_cols


# ── DB照会 SQL ────────────────────────────────────────────────────────────────

_SQL_ENTRIES = """
SELECT
    r.id            AS race_id,
    r.race_date,
    r.keibajo_code,
    r.race_num,
    r.distance,
    r.track_code,
    r.grade_code,
    r.tenko_code,
    r.shiba_baba_code,
    r.dirt_baba_code,
    e.umaban,
    e.wakuban,
    e.horse_id,
    e.horse_name,
    e.jockey_cd,
    e.trainer_cd,
    e.horse_weight   AS bataiju,
    e.weight_diff    AS zogen_sa,
    e.basis_weight   AS futan_juryo,
    e.tan_odds,
    e.ninki,
    e.kakutei_chakujun
FROM races r
JOIN race_entries e ON e.race_id = r.id
WHERE r.id = %s
ORDER BY e.umaban
"""


def _derive_db_race_id(race_date: object, keibajo_code: str, race_num: int) -> str:
    date_str = pd.Timestamp(race_date).strftime("%Y%m%d")
    return date_str + str(keibajo_code).zfill(2) + str(race_num).zfill(2)


def _fetch_entries(race_id: str) -> pd.DataFrame:
    with get_v2_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(_SQL_ENTRIES, (race_id,))
            rows = cur.fetchall()
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def _fetch_feature_stores(
    db_race_id: str,
    horse_ids: list[str],
    jockey_cds: list[str],
    trainer_cds: list[str],
    race_date: object,
) -> dict[str, pd.DataFrame]:
    date_val = pd.Timestamp(race_date).date()
    with get_jvdl_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

            # レース日 ≤ 利用可能な最新日付 でフォールバック（当日データがない場合も取得）
            cur.execute(
                "SELECT kishu_code AS jockey_cd, "
                "       win_rate AS kishu_win_rate, "
                "       top3_rate AS kishu_top3_rate "
                "FROM jockey_feature_store "
                "WHERE kishu_code = ANY(%s) "
                "  AND target_date = ("
                "      SELECT MAX(target_date) FROM jockey_feature_store"
                "      WHERE target_date <= %s"
                "  )",
                (jockey_cds, date_val),
            )
            jf = pd.DataFrame(cur.fetchall())

            cur.execute(
                "SELECT chokyoshi_code AS trainer_cd, "
                "       win_rate AS trainer_win_rate, "
                "       top3_rate AS trainer_top3_rate "
                "FROM trainer_feature_store "
                "WHERE chokyoshi_code = ANY(%s) "
                "  AND target_date = ("
                "      SELECT MAX(target_date) FROM trainer_feature_store"
                "      WHERE target_date <= %s"
                "  )",
                (trainer_cds, date_val),
            )
            tf = pd.DataFrame(cur.fetchall())

            # race_id 完全一致 → なければ馬ごとに直近の調教スコアをフォールバック
            cur.execute(
                "SELECT ketto_toroku_bango AS horse_id, "
                "       chokyo_master_score, s1_time_score, s2_improve_score, "
                "       s3_lastf_score, s4_freq_score, accel_bonus, ref_session_days_before "
                "FROM chokyo_scores WHERE race_id = %s",
                (db_race_id,),
            )
            cs = pd.DataFrame(cur.fetchall())
            if cs.empty:
                cur.execute(
                    "SELECT DISTINCT ON (ketto_toroku_bango) "
                    "       ketto_toroku_bango AS horse_id, "
                    "       chokyo_master_score, s1_time_score, s2_improve_score, "
                    "       s3_lastf_score, s4_freq_score, accel_bonus, ref_session_days_before "
                    "FROM chokyo_scores "
                    "WHERE ketto_toroku_bango = ANY(%s) AND race_id <= %s "
                    "ORDER BY ketto_toroku_bango, race_id DESC",
                    (horse_ids, db_race_id),
                )
                cs = pd.DataFrame(cur.fetchall())

            cur.execute(
                "SELECT ketto_toroku_bango AS horse_id, "
                "       apt_distance_shift, apt_track_change, apt_bias_fit, "
                "       apt_temperament, apt_growth, apt_seasonal "
                "FROM aptitude_scores WHERE race_id = %s",
                (db_race_id,),
            )
            apt = pd.DataFrame(cur.fetchall())
            if apt.empty:
                cur.execute(
                    "SELECT DISTINCT ON (ketto_toroku_bango) "
                    "       ketto_toroku_bango AS horse_id, "
                    "       apt_distance_shift, apt_track_change, apt_bias_fit, "
                    "       apt_temperament, apt_growth, apt_seasonal "
                    "FROM aptitude_scores "
                    "WHERE ketto_toroku_bango = ANY(%s) AND race_id <= %s "
                    "ORDER BY ketto_toroku_bango, race_id DESC",
                    (horse_ids, db_race_id),
                )
                apt = pd.DataFrame(cur.fetchall())

    return {"jf": jf, "tf": tf, "cs": cs, "apt": apt}


# ── 特徴量ビルド ──────────────────────────────────────────────────────────────

def _build_legacy_features(race_id: str) -> pd.DataFrame:
    df = _fetch_entries(race_id)
    if df.empty:
        return df

    race_date = df["race_date"].iloc[0]
    month     = pd.Timestamp(race_date).month
    kc        = str(df["keibajo_code"].iloc[0]).zfill(2)
    race_num  = int(df["race_num"].iloc[0])

    # 数値変換
    for col in ["bataiju", "zogen_sa", "futan_juryo", "distance", "umaban", "wakuban", "tan_odds"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # フィーチャーストア取得
    db_race_id = _derive_db_race_id(race_date, kc, race_num)
    stores = _fetch_feature_stores(
        db_race_id,
        df["horse_id"].astype(str).tolist(),
        df["jockey_cd"].astype(str).tolist(),
        df["trainer_cd"].astype(str).tolist(),
        race_date,
    )

    df["jockey_cd"]  = df["jockey_cd"].astype(str)
    df["trainer_cd"] = df["trainer_cd"].astype(str)
    df["horse_id"]   = df["horse_id"].astype(str)

    jf = stores["jf"]
    if not jf.empty:
        jf["jockey_cd"] = jf["jockey_cd"].astype(str)
        df = df.merge(jf, on="jockey_cd", how="left")
    else:
        df["kishu_win_rate"]  = float("nan")
        df["kishu_top3_rate"] = float("nan")

    tf = stores["tf"]
    if not tf.empty:
        tf["trainer_cd"] = tf["trainer_cd"].astype(str)
        df = df.merge(tf, on="trainer_cd", how="left")
    else:
        df["trainer_win_rate"]  = float("nan")
        df["trainer_top3_rate"] = float("nan")

    for store_df in [stores["cs"], stores["apt"]]:
        if not store_df.empty:
            store_df = store_df.copy()
            store_df["horse_id"] = store_df["horse_id"].astype(str)
            df = df.merge(store_df, on="horse_id", how="left")

    # ── 派生特徴量 ──────────────────────────────────────────────────────────
    df["kyori"]       = df["distance"]
    df["class_level"] = df["grade_code"].map(_GRADE_TO_CLASS).fillna(0)
    df["month"]       = month

    df["abs_zogen_sa"] = df["zogen_sa"].abs()

    bataiju_mean = df["bataiju"].mean()
    futan_mean   = df["futan_juryo"].mean()
    df["bataiju_diff_from_race_mean"]    = df["bataiju"] - bataiju_mean
    df["futan_juryo_diff_from_race_mean"] = df["futan_juryo"] - futan_mean

    # 騎手勝率のレース内ランク（降順: 高勝率 → 1位）
    df["kishu_win_rate_rank_in_race"] = df["kishu_win_rate"].rank(
        ascending=False, method="min", na_option="bottom"
    )

    # 季節フラグ
    is_summer = month in (6, 7, 8)
    is_winter = month in (12, 1, 2)
    df["is_summer_hokkaido"] = int(is_summer and kc in ("01", "02"))

    # 重馬場判定（芝または砂で3=重/4=不良）
    shiba = str(df["shiba_baba_code"].iloc[0]) if "shiba_baba_code" in df.columns else ""
    dirt  = str(df["dirt_baba_code"].iloc[0])  if "dirt_baba_code"  in df.columns else ""
    is_heavy = (shiba in _HEAVY_BABA_CODES) or (dirt in _HEAVY_BABA_CODES)
    df["is_winter_heavy"] = int(is_winter and is_heavy)

    # 季節別体重変化
    df["weight_change_summer"] = df["zogen_sa"] * float(is_summer)
    df["weight_change_winter"] = df["zogen_sa"] * float(is_winter)

    return df


def _assemble_X(df: pd.DataFrame, feature_cols: list[str]) -> tuple[pd.DataFrame, int]:
    X = pd.DataFrame(float("nan"), index=df.index, columns=feature_cols)
    available = 0
    for col in feature_cols:
        if col in df.columns:
            X[col] = pd.to_numeric(df[col], errors="coerce").values
            if X[col].notna().any():
                available += 1
    return X, available


# ── エンドポイント ────────────────────────────────────────────────────────────

@router.get("/predict-legacy/{race_id}", response_model=RacePredictionLegacyResponse)
def predict_race_legacy(race_id: str) -> RacePredictionLegacyResponse:
    logger.info("[LegacyAI] race_id=%s", race_id)

    try:
        booster, feature_cols = _load_legacy_model()
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e))

    try:
        df = _build_legacy_features(race_id)
    except Exception as e:
        logger.exception("[LegacyAI] 特徴量構築エラー: %s", e)
        raise HTTPException(status_code=500, detail=f"特徴量構築エラー: {e}")

    if df.empty:
        raise HTTPException(status_code=404, detail=f"レースが見つかりません: {race_id}")

    X, available_features = _assemble_X(df, feature_cols)
    scores   = booster.predict(X)
    ai_ranks = pd.Series(scores).rank(ascending=False, method="min").astype(int).tolist()

    if "ninki" in df.columns and df["ninki"].notna().any():
        odds_ranks = df["ninki"].rank(ascending=True, method="min").where(df["ninki"].notna())
    elif "tan_odds" in df.columns and df["tan_odds"].notna().any():
        odds_ranks = df["tan_odds"].rank(ascending=True, method="min").where(df["tan_odds"].notna())
    else:
        odds_ranks = pd.Series([None] * len(df))

    is_confirmed = (
        "kakutei_chakujun" in df.columns
        and df["kakutei_chakujun"].notna().any()
    )

    def _safe_float(v: object) -> float | None:
        if v is None:
            return None
        try:
            f = float(v)  # type: ignore[arg-type]
            return None if math.isnan(f) else f
        except (TypeError, ValueError):
            return None

    def _safe_int(v: object) -> int | None:
        f = _safe_float(v)
        return int(f) if f is not None else None

    df = df.reset_index(drop=True)
    horses: list[HorsePredictionLegacy] = []
    for i, row in df.iterrows():
        horses.append(HorsePredictionLegacy(
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

    return RacePredictionLegacyResponse(
        race_id=race_id,
        race_date=str(pd.Timestamp(df["race_date"].iloc[0]).date()),
        keibajo_code=str(df["keibajo_code"].iloc[0]),
        distance=int(df["distance"].iloc[0]),
        horses=horses,
        model_folds=1,
        feature_count=len(feature_cols),
        available_features=available_features,
        is_confirmed=bool(is_confirmed),
    )
