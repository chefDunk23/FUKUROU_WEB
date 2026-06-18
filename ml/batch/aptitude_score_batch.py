"""
ml/batch/aptitude_score_batch.py
----------------------------------
「総合馬体適性スコア」6特徴量を事前バッチ計算し、
aptitude_scores テーブルに UPSERT する。

[6特徴量]
  apt_distance_shift : 距離シフト適性    (sire × extend/same/shorten)
  apt_track_change   : トラック替わり適性 (sire × to_turf/to_dirt/no_change)
  apt_bias_fit       : バイアス適合度     (sire × front/inner bias 象限)
  apt_temperament    : 気性難スコア       (horse × inner/outer gate 着順差)
  apt_growth         : 成長曲線適性       (sire × age_group: juvenile/sophomore/prime/veteran)
  apt_seasonal       : 季節適性           (sire × spring/summer/autumn/winter)

[PiT 設計]
  sire 統計: 対象レース当日 00:00 より前の産駒全レースで累積集計
  horse 統計: 対象レース自身を除いた過去走で累積集計

[ベイズ平滑化 (全 sire 系特徴量共通)]
  p̂(s,c) = (wins + C × μ₀_c) / (n + C)   C = BAYESIAN_C = 30
  score   = p̂(s,c) / μ₀_c

[実行]
  py -m ml.batch.aptitude_score_batch [--from-year 2015]
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime

import numpy as np
import pandas as pd
from sqlalchemy import text

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────
# 定数
# ─────────────────────────────────────────────────────────────────────────
BAYESIAN_C           = 30
BIAS_THRESHOLD       = 0.2
INNER_GATE_MAX       = 3
OUTER_GATE_MIN       = 6
MIN_GATE_RACES       = 2
DIST_SHIFT_THRESHOLD = 100
CHUNK_SIZE           = 5_000

TURF = "芝"
DIRT = "ダート"

# ─────────────────────────────────────────────────────────────────────────
# DDL / DML
# ─────────────────────────────────────────────────────────────────────────
_DDL_APTITUDE = """
CREATE TABLE IF NOT EXISTS aptitude_scores (
    race_id              VARCHAR(12) NOT NULL,
    ketto_toroku_bango   VARCHAR(10) NOT NULL,
    apt_distance_shift   FLOAT,
    apt_track_change     FLOAT,
    apt_bias_fit         FLOAT,
    apt_temperament      FLOAT,
    apt_growth           FLOAT,
    apt_seasonal         FLOAT,
    computed_at          TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (race_id, ketto_toroku_bango)
)
"""

_UPSERT_SQL = text("""
INSERT INTO aptitude_scores
    (race_id, ketto_toroku_bango,
     apt_distance_shift, apt_track_change, apt_bias_fit,
     apt_temperament, apt_growth, apt_seasonal,
     computed_at)
VALUES
    (:race_id, :ketto_toroku_bango,
     :apt_distance_shift, :apt_track_change, :apt_bias_fit,
     :apt_temperament, :apt_growth, :apt_seasonal,
     :computed_at)
ON CONFLICT (race_id, ketto_toroku_bango)
DO UPDATE SET
    apt_distance_shift = EXCLUDED.apt_distance_shift,
    apt_track_change   = EXCLUDED.apt_track_change,
    apt_bias_fit       = EXCLUDED.apt_bias_fit,
    apt_temperament    = EXCLUDED.apt_temperament,
    apt_growth         = EXCLUDED.apt_growth,
    apt_seasonal       = EXCLUDED.apt_seasonal,
    computed_at        = EXCLUDED.computed_at
""")

# ─────────────────────────────────────────────────────────────────────────
# データロード SQL
# ─────────────────────────────────────────────────────────────────────────
_LOAD_SQL = """
SELECT
    se.race_id,
    r.date                                   AS race_date,
    r.race_number                            AS race_bango,
    r.place_code                             AS keibajo_code,
    r.course_type,
    r.distance,
    se.horse_id                              AS ketto_toroku_bango,
    h.sire_id,
    h.birthday,
    se.bracket_number                        AS wakuban,
    se.horse_number                          AS umaban,
    se.confirmed_rank                        AS kakutei_chakujun,
    COALESCE(bp.front_bias_pit, 0.0)         AS front_bias_pit,
    COALESCE(bp.inner_bias_pit, 0.0)         AS inner_bias_pit
FROM race_entries se
JOIN  races r  ON se.race_id = r.id
LEFT JOIN horses h  ON se.horse_id = h.id
LEFT JOIN track_bias_pit bp ON se.race_id = bp.race_id
WHERE r.date IS NOT NULL
  AND se.confirmed_rank IS NOT NULL
  AND se.confirmed_rank > 0
  AND r.course_type != '障害'
ORDER BY r.date, r.place_code, r.race_number, se.horse_number
"""


# ─────────────────────────────────────────────────────────────────────────
# Step 0: データロード & 前処理
# ─────────────────────────────────────────────────────────────────────────

def _load_race_data(engine) -> pd.DataFrame:
    logger.info("レースデータをロード中...")
    df = pd.read_sql(_LOAD_SQL, engine)

    df["race_date"] = pd.to_datetime(df["race_date"], errors="coerce")
    df["birthday"]  = pd.to_datetime(df["birthday"],  errors="coerce")

    for col in ("kakutei_chakujun", "wakuban", "umaban", "distance",
                "front_bias_pit", "inner_bias_pit", "race_bango"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["ketto_toroku_bango"] = df["ketto_toroku_bango"].astype(str).str.strip()
    df["race_id"]            = df["race_id"].astype(str).str.strip()
    df["keibajo_code"]       = df["keibajo_code"].astype(str).str.strip().str.zfill(2)
    df["sire_id"] = df["sire_id"].where(df["sire_id"].notna())

    sire_rate = df["sire_id"].notna().mean() * 100
    logger.info(
        "  ロード完了: %d 行 / %d レース / %d 頭 | sire_id 充填率=%.1f%%",
        len(df), df["race_id"].nunique(), df["ketto_toroku_bango"].nunique(), sire_rate,
    )
    return df


def _add_prev1_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values(["ketto_toroku_bango", "race_date", "race_bango"]).copy()
    g = df.groupby("ketto_toroku_bango", sort=False)
    df["prev_course_type"] = g["course_type"].shift(1)
    df["prev_distance"]    = g["distance"].shift(1)
    return df


def _add_condition_cols(df: pd.DataFrame) -> pd.DataFrame:
    # --- 距離シフト ---
    dist_diff = df["distance"] - df["prev_distance"]
    df["shift_cat"] = np.select(
        [dist_diff > DIST_SHIFT_THRESHOLD, dist_diff < -DIST_SHIFT_THRESHOLD],
        ["extend", "shorten"],
        default="same",
    )
    df.loc[df["prev_distance"].isna(), "shift_cat"] = None

    # --- トラック替わり ---
    df["track_cat"] = np.select(
        [(df["prev_course_type"] == DIRT) & (df["course_type"] == TURF),
         (df["prev_course_type"] == TURF) & (df["course_type"] == DIRT)],
        ["to_turf", "to_dirt"],
        default="no_change",
    )
    df.loc[df["prev_course_type"].isna(), "track_cat"] = None

    # --- バイアス象限 ---
    def _sign(s: pd.Series) -> np.ndarray:
        return np.where(s > BIAS_THRESHOLD, "1",
               np.where(s < -BIAS_THRESHOLD, "-1", "0"))
    df["bias_cat"] = (
        pd.Series(_sign(df["front_bias_pit"]), index=df.index) + "f"
        + pd.Series(_sign(df["inner_bias_pit"]), index=df.index) + "i"
    )

    # --- 馬齢グループ ---
    age = df["race_date"].dt.year - df["birthday"].dt.year
    df["age_group"] = np.select(
        [age == 2, age == 3, age == 4, age >= 5],
        ["juvenile", "sophomore", "prime", "veteran"],
        default=None,
    )
    df.loc[df["birthday"].isna(), "age_group"] = None

    # --- 季節 ---
    month = df["race_date"].dt.month
    df["season"] = np.select(
        [month.isin([3, 4, 5]), month.isin([6, 7, 8]), month.isin([9, 10, 11])],
        ["spring", "summer", "autumn"],
        default="winter",
    )

    return df


# ─────────────────────────────────────────────────────────────────────────
# 共通: sire 系 PiT ベイズ平滑化スコア計算
# ─────────────────────────────────────────────────────────────────────────

def _sire_feature_pit(
    df: pd.DataFrame,
    cond_col: str,
    score_col: str,
) -> pd.Series:
    """sire_id × cond_col の PiT ベイズ平滑化スコアを計算する。"""
    valid_mask = df["sire_id"].notna() & df[cond_col].notna()
    ref = df.loc[valid_mask].copy()
    ref["_is_win"] = (ref["kakutei_chakujun"] == 1).astype(float)

    if len(ref) == 0:
        logger.warning("  %s: 有効参照データなし", score_col)
        return pd.Series(np.nan, index=df.index)

    global_rate: dict[str, float] = ref.groupby(cond_col)["_is_win"].mean().to_dict()
    global_mean: float = float(ref["_is_win"].mean())

    daily = (
        ref
        .groupby(["sire_id", cond_col, "race_date"], as_index=False)
        .agg(day_wins=("_is_win", "sum"), day_races=("_is_win", "count"))
        .sort_values(["sire_id", cond_col, "race_date"])
    )

    grp = daily.groupby(["sire_id", cond_col], sort=False)
    daily["cum_wins"]  = grp["day_wins"].cumsum()
    daily["cum_races"] = grp["day_races"].cumsum()
    daily["pit_wins"]  = daily["cum_wins"]  - daily["day_wins"]
    daily["pit_races"] = daily["cum_races"] - daily["day_races"]

    lookup = daily[["sire_id", cond_col, "race_date", "pit_wins", "pit_races"]].sort_values("race_date")

    target_sorted = (
        df.loc[valid_mask, ["sire_id", cond_col, "race_date"]]
        .reset_index()
        .sort_values("race_date")
        .reset_index(drop=True)
    )

    merged2 = pd.merge_asof(
        target_sorted,
        lookup,
        on="race_date",
        by=["sire_id", cond_col],
        direction="backward",
    )

    mu0       = merged2[cond_col].map(global_rate).fillna(global_mean)
    pit_wins  = merged2["pit_wins"].fillna(0)
    pit_races = merged2["pit_races"].fillna(0)

    score = np.where(
        pit_races == 0,
        np.nan,
        (pit_wins + BAYESIAN_C * mu0) / (pit_races + BAYESIAN_C) / mu0.clip(lower=1e-6),
    )

    result_arr = np.full(len(df), np.nan)
    orig_indices = merged2["index"].to_numpy()
    result_arr[orig_indices] = score

    n_valid = int(np.sum(~np.isnan(result_arr)))
    mean_val = float(np.nanmean(result_arr)) if n_valid > 0 else float("nan")
    logger.info("  %s: %d/%d 有効 | mean=%.3f", score_col, n_valid, len(df), mean_val)

    return pd.Series(result_arr, index=df.index)


# ─────────────────────────────────────────────────────────────────────────
# 要素4: 気性難スコア
# ─────────────────────────────────────────────────────────────────────────

def _compute_apt_temperament(df: pd.DataFrame) -> pd.Series:
    work = df.sort_values(["ketto_toroku_bango", "race_date", "race_bango"]).copy()

    work["_field_size"] = (
        work.groupby("race_id")["umaban"].transform("max").clip(lower=2)
    )
    work["_rank_ratio"] = work["kakutei_chakujun"] / work["_field_size"]

    work["_is_inner"] = (work["wakuban"] <= INNER_GATE_MAX).astype(float).fillna(0)
    work["_is_outer"] = (work["wakuban"] >= OUTER_GATE_MIN).astype(float).fillna(0)

    work["_inner_rv"] = np.where(work["_is_inner"].astype(bool), work["_rank_ratio"].fillna(0), 0.0)
    work["_outer_rv"] = np.where(work["_is_outer"].astype(bool), work["_rank_ratio"].fillna(0), 0.0)

    g = work.groupby("ketto_toroku_bango", sort=False)
    work["_cum_inner_sum"] = g["_inner_rv"].cumsum()
    work["_cum_inner_n"]   = g["_is_inner"].cumsum()
    work["_cum_outer_sum"] = g["_outer_rv"].cumsum()
    work["_cum_outer_n"]   = g["_is_outer"].cumsum()

    work["_pit_inner_sum"] = work["_cum_inner_sum"] - work["_inner_rv"]
    work["_pit_inner_n"]   = work["_cum_inner_n"]   - work["_is_inner"]
    work["_pit_outer_sum"] = work["_cum_outer_sum"] - work["_outer_rv"]
    work["_pit_outer_n"]   = work["_cum_outer_n"]   - work["_is_outer"]

    global_avg = float(work["_rank_ratio"].mean())

    smoothed_inner = (
        (work["_pit_inner_sum"] + BAYESIAN_C * global_avg)
        / (work["_pit_inner_n"] + BAYESIAN_C)
    )
    smoothed_outer = (
        (work["_pit_outer_sum"] + BAYESIAN_C * global_avg)
        / (work["_pit_outer_n"] + BAYESIAN_C)
    )

    raw_score = smoothed_inner - smoothed_outer

    sigma = float(raw_score.std())
    if sigma < 1e-6:
        logger.warning("  apt_temperament: sigma ~= 0, スキップ")
        return pd.Series(np.nan, index=df.index)

    score = (raw_score / sigma).clip(0.0, 1.0)

    insufficient = (
        (work["_pit_inner_n"] < MIN_GATE_RACES)
        | (work["_pit_outer_n"] < MIN_GATE_RACES)
    )
    score = score.where(~insufficient, np.nan)
    result = score.reindex(df.index)

    n_valid = int(result.notna().sum())
    logger.info(
        "  apt_temperament: %d/%d 有効 | mean=%.3f",
        n_valid, len(df), float(result.mean()) if n_valid > 0 else float("nan"),
    )
    return result


# ─────────────────────────────────────────────────────────────────────────
# UPSERT
# ─────────────────────────────────────────────────────────────────────────

def _upsert_aptitude(engine, df: pd.DataFrame) -> int:
    score_cols = [
        "apt_distance_shift", "apt_track_change", "apt_bias_fit",
        "apt_temperament", "apt_growth", "apt_seasonal",
    ]
    out = df[["race_id", "ketto_toroku_bango"] + score_cols].copy()
    out = out.where(pd.notnull(out), None)
    out["computed_at"] = datetime.utcnow()

    records = out.to_dict("records")
    total = len(records)

    with engine.begin() as conn:
        for i in range(0, total, CHUNK_SIZE):
            chunk = records[i : i + CHUNK_SIZE]
            conn.execute(_UPSERT_SQL, chunk)
            pct = min(i + CHUNK_SIZE, total)
            if pct % 50_000 < CHUNK_SIZE or pct == total:
                logger.info("  UPSERT 進行中: %d/%d", pct, total)

    logger.info("  aptitude_scores UPSERT 完了: %d 行", total)
    return total


# ─────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────

def run(from_year: int = 2015) -> int:
    """全期間の適性スコアを計算し aptitude_scores に UPSERT する。

    from_year 以前のデータも PiT 計算の参照に使用するが、
    DB への書き込みは from_year 以降の行のみ。

    Returns:
        UPSERT した行数。
    """
    from ml.db import engine as _engine

    with _engine.connect() as conn:
        conn.execute(text(_DDL_APTITUDE))
        conn.commit()
    logger.info("DDL 完了 (aptitude_scores テーブル確認)")

    df = _load_race_data(_engine)
    df = _add_prev1_features(df)
    df = _add_condition_cols(df)

    logger.info("要素1: apt_distance_shift 計算中...")
    df["apt_distance_shift"] = _sire_feature_pit(df, "shift_cat", "apt_distance_shift")

    logger.info("要素2: apt_track_change 計算中...")
    df["apt_track_change"] = _sire_feature_pit(df, "track_cat", "apt_track_change")

    logger.info("要素3: apt_bias_fit 計算中...")
    df["apt_bias_fit"] = _sire_feature_pit(df, "bias_cat", "apt_bias_fit")

    logger.info("要素5: apt_growth 計算中...")
    df["apt_growth"] = _sire_feature_pit(df, "age_group", "apt_growth")

    logger.info("要素6: apt_seasonal 計算中...")
    df["apt_seasonal"] = _sire_feature_pit(df, "season", "apt_seasonal")

    logger.info("要素4: apt_temperament 計算中...")
    df["apt_temperament"] = _compute_apt_temperament(df)

    df_out = df[df["race_date"].dt.year >= from_year].copy()
    logger.info(
        "全スコア計算完了: 全%d行 -> UPSERT対象 %d行 (%d年以降 / %d レース)",
        len(df), len(df_out), from_year, df_out["race_id"].nunique(),
    )

    from ml.db import engine as _engine  # noqa: F811
    return _upsert_aptitude(_engine, df_out)


# ─────────────────────────────────────────────────────────────────────────
# CLI エントリポイント
# ─────────────────────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    parser = argparse.ArgumentParser(description="aptitude_scores バッチ計算")
    parser.add_argument(
        "--from-year", type=int, default=2015,
        help="UPSERT 対象の開始年（デフォルト: 2015）",
    )
    args = parser.parse_args()
    n = run(from_year=args.from_year)
    logger.info("完了。UPSERT: %d 行", n)


if __name__ == "__main__":
    main()
