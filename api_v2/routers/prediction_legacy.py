"""
api_v2/routers/prediction_legacy.py
=====================================
GET /api/v2/predict-legacy/{race_id} — フクロウ博士AI（PreRace_Model_v1）による予測。

使用モデル: models/v1_legacy/PreRace_Model_v1.txt（190特徴量）
実装可能特徴量: ~75列（33基本+42追加）。残りはNaN（LightGBMの欠損値処理に委ねる）。

実装済み特徴量カテゴリ:
  A. 基本: kyori/wakuban/umaban/bataiju/zogen_sa/futan_juryo + 派生 (13)
  B. 騎手・調教師: kishu_win_rate/top3_rate + trainer_win_rate/top3_rate + ランク (5)
  C. 調教: chokyo_master_score/s1-s4/accel_bonus/ref_session_days + trf_store (12)
  D. 適性: apt_distance_shift/track_change/bias_fit/temperament/growth/seasonal (6)
  E. 過去5走: prev_N_kyori/bataiju/futan_juryo/umaban (N=1..5) (20)
  F. 間隔・距離変化: interval/interval_weeks/is_rest_return/is_long_layoff/distance_change (5)
  G. キャリア統計: career_races/recent_avg_chakujun/recent_run_density/fatigue_index (4)
  H. 季節・月次成績: horse_month_avg_rank/horse_season_avg_rank/horse_season_rank_deviation (3)
  I. 重馬場: heavy_track_score/is_winter_heavy (2)
  J. その他派生: abs_zogen_sa/bataiju_diff/futan_juryo_diff/weight_change_* (5)

不可能な特徴量（切り捨て/NaN）:
  - sire_* (血統DBなし)
  - ability_* / time_zscore / relative_time (旧JVLパイプライン必須)
  - mot_* / furi_* / pace_harmony (複雑な過去走分析)
  - track_bias_* / cross_* / blood_x_* (旧パイプライン集計値)
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
_HEAVY_BABA_CODES = {"3", "4"}

# 季節コード（月→季節 1=春 2=夏 3=秋 4=冬）
def _month_to_season(m: int) -> int:
    return {1: 4, 2: 4, 3: 1, 4: 1, 5: 1, 6: 2, 7: 2, 8: 2, 9: 3, 10: 3, 11: 3, 12: 4}[m]


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

# 過去走データ（直近全レース、Python側でN走目を抽出）
_SQL_HORSE_HISTORY = """
SELECT
    e.horse_id,
    r.race_date                       AS prev_race_date,
    r.distance                        AS prev_kyori,
    e.horse_weight                    AS prev_bataiju,
    e.basis_weight                    AS prev_futan_juryo,
    e.umaban                          AS prev_umaban,
    e.kakutei_chakujun                AS prev_chakujun,
    EXTRACT(MONTH FROM r.race_date)::int AS prev_month,
    ROW_NUMBER() OVER (
        PARTITION BY e.horse_id
        ORDER BY r.race_date DESC, r.id DESC
    ) AS rn
FROM race_entries e
JOIN races r ON r.id = e.race_id
WHERE r.race_date < %s
  AND e.horse_id = ANY(%s)
  AND e.kakutei_chakujun IS NOT NULL
  AND e.kakutei_chakujun > 0
ORDER BY e.horse_id, rn
"""


def _derive_db_race_id(race_date: object, keibajo_code: str, race_num: int) -> str:
    return pd.Timestamp(race_date).strftime("%Y%m%d") + str(keibajo_code).zfill(2) + str(race_num).zfill(2)


def _fetch_entries(race_id: str) -> pd.DataFrame:
    with get_v2_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(_SQL_ENTRIES, (race_id,))
            rows = cur.fetchall()
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def _fetch_horse_history(horse_ids: list[str], race_date: object) -> pd.DataFrame:
    date_val = pd.Timestamp(race_date).date()
    with get_v2_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(_SQL_HORSE_HISTORY, (date_val, horse_ids))
            rows = cur.fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    for col in ["prev_kyori", "prev_bataiju", "prev_futan_juryo", "prev_umaban", "prev_chakujun"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


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

            # レース日以前の最新 target_date にフォールバック
            cur.execute(
                "SELECT kishu_code AS jockey_cd, "
                "       win_rate AS kishu_win_rate, top3_rate AS kishu_top3_rate, "
                "       baba_omo_win_rate AS jockey_omo_win_rate, "
                "       baba_furyo_win_rate AS jockey_furyo_win_rate "
                "FROM jockey_feature_store "
                "WHERE kishu_code = ANY(%s) "
                "  AND target_date = (SELECT MAX(target_date) FROM jockey_feature_store WHERE target_date <= %s)",
                (jockey_cds, date_val),
            )
            jf = pd.DataFrame(cur.fetchall())

            cur.execute(
                "SELECT chokyoshi_code AS trainer_cd, "
                "       win_rate AS trainer_win_rate, top3_rate AS trainer_top3_rate "
                "FROM trainer_feature_store "
                "WHERE chokyoshi_code = ANY(%s) "
                "  AND target_date = (SELECT MAX(target_date) FROM trainer_feature_store WHERE target_date <= %s)",
                (trainer_cds, date_val),
            )
            tf = pd.DataFrame(cur.fetchall())

            # chokyo_scores: race_id 完全一致 → なければ馬ごと直近フォールバック
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
                    "SELECT DISTINCT ON (ketto_toroku_bango) ketto_toroku_bango AS horse_id, "
                    "       chokyo_master_score, s1_time_score, s2_improve_score, "
                    "       s3_lastf_score, s4_freq_score, accel_bonus, ref_session_days_before "
                    "FROM chokyo_scores "
                    "WHERE ketto_toroku_bango = ANY(%s) AND race_id <= %s "
                    "ORDER BY ketto_toroku_bango, race_id DESC",
                    (horse_ids, db_race_id),
                )
                cs = pd.DataFrame(cur.fetchall())

            # aptitude_scores: 同上
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
                    "SELECT DISTINCT ON (ketto_toroku_bango) ketto_toroku_bango AS horse_id, "
                    "       apt_distance_shift, apt_track_change, apt_bias_fit, "
                    "       apt_temperament, apt_growth, apt_seasonal "
                    "FROM aptitude_scores "
                    "WHERE ketto_toroku_bango = ANY(%s) AND race_id <= %s "
                    "ORDER BY ketto_toroku_bango, race_id DESC",
                    (horse_ids, db_race_id),
                )
                apt = pd.DataFrame(cur.fetchall())

            # training_feature_store: best_z_total→chokyo_best_tscore 等にマッピング
            cur.execute(
                "SELECT horse_id, "
                "       best_z_total AS chokyo_best_tscore, "
                "       z_trend_slope AS chokyo_trend, "
                "       avg_accel AS chokyo_accel_avg, "
                "       session_count AS chokyo_count, "
                "       slope_ratio AS chokyo_slope_ratio "
                "FROM training_feature_store "
                "WHERE horse_id = ANY(%s) "
                "  AND target_date = (SELECT MAX(target_date) FROM training_feature_store WHERE target_date <= %s)",
                (horse_ids, date_val),
            )
            trf = pd.DataFrame(cur.fetchall())

    return {"jf": jf, "tf": tf, "cs": cs, "apt": apt, "trf": trf}


# ── 過去走特徴量アタッチ ──────────────────────────────────────────────────────

def _attach_history_features(df: pd.DataFrame, hist: pd.DataFrame, race_date: object, current_distance: int) -> pd.DataFrame:
    """
    horse_id ごとの過去走 DataFrame を受け取り、prev_N_* / interval / career_* / season 特徴量を df に追加する。
    hist: _fetch_horse_history() の返り値（rn列を持つ）
    """
    if hist.empty:
        return df

    current_date = pd.Timestamp(race_date)
    current_month = current_date.month
    current_season = _month_to_season(current_month)

    # ── 馬ごとに集計 ─────────────────────────────────────────────────────────
    records: list[dict] = []
    for horse_id, grp in hist.groupby("horse_id", sort=False):
        grp = grp.sort_values("rn").reset_index(drop=True)

        rec: dict = {"horse_id": str(horse_id)}

        # prev_N_* (N=1..5)
        for n in range(1, 6):
            row = grp[grp["rn"] == n]
            if row.empty:
                rec[f"prev_{n}_kyori"]      = float("nan")
                rec[f"prev_{n}_bataiju"]    = float("nan")
                rec[f"prev_{n}_futan_juryo"]= float("nan")
                rec[f"prev_{n}_umaban"]     = float("nan")
            else:
                r = row.iloc[0]
                rec[f"prev_{n}_kyori"]      = float(r["prev_kyori"])      if pd.notna(r["prev_kyori"])      else float("nan")
                rec[f"prev_{n}_bataiju"]    = float(r["prev_bataiju"])    if pd.notna(r["prev_bataiju"])    else float("nan")
                rec[f"prev_{n}_futan_juryo"]= float(r["prev_futan_juryo"])if pd.notna(r["prev_futan_juryo"])else float("nan")
                rec[f"prev_{n}_umaban"]     = float(r["prev_umaban"])     if pd.notna(r["prev_umaban"])     else float("nan")

        # 出走間隔（直前レースから）
        first = grp[grp["rn"] == 1]
        if not first.empty and pd.notna(first.iloc[0]["prev_race_date"]):
            last_date = pd.Timestamp(first.iloc[0]["prev_race_date"])
            interval_days = (current_date - last_date).days
            rec["interval"]        = float(interval_days)
            rec["interval_weeks"]  = float(interval_days) / 7.0
            rec["is_rest_return"]  = float(interval_days >= 28)
            rec["is_long_layoff"]  = float(interval_days >= 90)
        else:
            rec["interval"] = rec["interval_weeks"] = rec["is_rest_return"] = rec["is_long_layoff"] = float("nan")

        # 距離変化
        if not first.empty and pd.notna(first.iloc[0]["prev_kyori"]):
            d_change = float(current_distance) - float(first.iloc[0]["prev_kyori"])
            rec["distance_change"] = d_change
            rec["dist_change"]     = d_change  # モデル上は同一値
        else:
            rec["distance_change"] = rec["dist_change"] = float("nan")

        # キャリア統計
        rec["career_races"] = float(len(grp))

        chakujun_vals = grp["prev_chakujun"].dropna().tolist()
        last5 = [v for v in chakujun_vals[:5] if not math.isnan(v)]
        rec["recent_avg_chakujun"] = float(np.mean(last5)) if last5 else float("nan")

        # 直近90日のレース数（レース密度）
        recent_cutoff = current_date - pd.Timedelta(days=90)
        recent_count = grp[pd.to_datetime(grp["prev_race_date"]) >= recent_cutoff].shape[0]
        rec["recent_run_density"] = float(recent_count)
        # 疲労指数: 90日に3走以上で線形スケール（最大1.0）
        rec["fatigue_index"] = min(float(recent_count) / 3.0, 1.0) if recent_count > 0 else 0.0

        # 月次・季節別平均順位
        month_grp = grp[grp["prev_month"] == current_month]["prev_chakujun"].dropna()
        rec["horse_month_avg_rank"] = float(month_grp.mean()) if len(month_grp) > 0 else float("nan")

        season_months = [m for m, s in {m: _month_to_season(m) for m in range(1, 13)}.items() if s == current_season]
        season_grp = grp[grp["prev_month"].isin(season_months)]["prev_chakujun"].dropna()
        rec["horse_season_avg_rank"] = float(season_grp.mean()) if len(season_grp) > 0 else float("nan")
        rec["horse_season_rank_deviation"] = float(season_grp.std()) if len(season_grp) > 1 else float("nan")

        records.append(rec)

    if not records:
        return df

    hist_df = pd.DataFrame(records)
    hist_df["horse_id"] = hist_df["horse_id"].astype(str)
    return df.merge(hist_df, on="horse_id", how="left")


# ── 特徴量ビルド ──────────────────────────────────────────────────────────────

def _build_legacy_features(race_id: str) -> pd.DataFrame:
    df = _fetch_entries(race_id)
    if df.empty:
        return df

    race_date = df["race_date"].iloc[0]
    month     = pd.Timestamp(race_date).month
    kc        = str(df["keibajo_code"].iloc[0]).zfill(2)
    race_num  = int(df["race_num"].iloc[0])
    distance  = int(pd.to_numeric(df["distance"].iloc[0], errors="coerce") or 0)

    for col in ["bataiju", "zogen_sa", "futan_juryo", "distance", "umaban", "wakuban", "tan_odds"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df["jockey_cd"]  = df["jockey_cd"].astype(str)
    df["trainer_cd"] = df["trainer_cd"].astype(str)
    df["horse_id"]   = df["horse_id"].astype(str)

    horse_ids   = df["horse_id"].tolist()
    jockey_cds  = df["jockey_cd"].tolist()
    trainer_cds = df["trainer_cd"].tolist()

    db_race_id = _derive_db_race_id(race_date, kc, race_num)

    # ── 全ストア並行取得 ──────────────────────────────────────────────────────
    stores = _fetch_feature_stores(db_race_id, horse_ids, jockey_cds, trainer_cds, race_date)
    hist   = _fetch_horse_history(horse_ids, race_date)

    # ── 騎手 ──────────────────────────────────────────────────────────────────
    jf = stores["jf"]
    if not jf.empty:
        jf = jf.copy(); jf["jockey_cd"] = jf["jockey_cd"].astype(str)
        df = df.merge(jf, on="jockey_cd", how="left")
    else:
        for c in ["kishu_win_rate", "kishu_top3_rate", "jockey_omo_win_rate", "jockey_furyo_win_rate"]:
            df[c] = float("nan")

    # ── 調教師 ────────────────────────────────────────────────────────────────
    tf = stores["tf"]
    if not tf.empty:
        tf = tf.copy(); tf["trainer_cd"] = tf["trainer_cd"].astype(str)
        df = df.merge(tf, on="trainer_cd", how="left")
    else:
        df["trainer_win_rate"] = df["trainer_top3_rate"] = float("nan")

    # ── 調教・適性・training store ────────────────────────────────────────────
    for store_df in [stores["cs"], stores["apt"]]:
        if not store_df.empty:
            s = store_df.copy(); s["horse_id"] = s["horse_id"].astype(str)
            df = df.merge(s, on="horse_id", how="left")

    trf = stores["trf"]
    if not trf.empty:
        trf = trf.copy(); trf["horse_id"] = trf["horse_id"].astype(str)
        df = df.merge(trf, on="horse_id", how="left")

    # ── 過去走特徴量アタッチ ──────────────────────────────────────────────────
    df = _attach_history_features(df, hist, race_date, distance)

    # ── 派生特徴量 ────────────────────────────────────────────────────────────
    df["kyori"]       = df["distance"]
    df["class_level"] = df["grade_code"].map(_GRADE_TO_CLASS).fillna(0)
    df["month"]       = month

    df["abs_zogen_sa"] = df["zogen_sa"].abs()

    bataiju_mean = df["bataiju"].mean()
    futan_mean   = df["futan_juryo"].mean()
    df["bataiju_diff_from_race_mean"]     = df["bataiju"]     - bataiju_mean
    df["futan_juryo_diff_from_race_mean"] = df["futan_juryo"] - futan_mean

    # レース内ランク
    df["kishu_win_rate_rank_in_race"]   = df["kishu_win_rate"].rank(ascending=False, method="min", na_option="bottom")
    df["trainer_win_rate_rank_in_race"] = df["trainer_win_rate"].rank(ascending=False, method="min", na_option="bottom")

    # 季節フラグ
    is_summer = month in (6, 7, 8)
    is_winter = month in (12, 1, 2)
    df["is_summer_hokkaido"] = int(is_summer and kc in ("01", "02"))

    shiba = str(df["shiba_baba_code"].iloc[0]) if "shiba_baba_code" in df.columns else ""
    dirt  = str(df["dirt_baba_code"].iloc[0])  if "dirt_baba_code"  in df.columns else ""
    is_heavy = (shiba in _HEAVY_BABA_CODES) or (dirt in _HEAVY_BABA_CODES)
    df["is_winter_heavy"] = int(is_winter and is_heavy)

    df["weight_change_summer"] = df["zogen_sa"] * float(is_summer)
    df["weight_change_winter"] = df["zogen_sa"] * float(is_winter)

    # 重馬場スコア: 騎手の重+不良馬場での勝率合計（簡易代替）
    for col in ["jockey_omo_win_rate", "jockey_furyo_win_rate"]:
        if col not in df.columns:
            df[col] = float("nan")
    df["heavy_track_score"] = df[["jockey_omo_win_rate", "jockey_furyo_win_rate"]].mean(axis=1)

    logger.info(
        "[LegacyAI] 特徴量ビルド完了: race_id=%s  history_rows=%d",
        race_id, len(hist),
    )
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
