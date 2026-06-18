"""
ml/batch/chokyo_score_batch.py
-------------------------------
「調教マスタースコア（0〜100）」をバッチ事前計算し、
PostgreSQL の chokyo_scores テーブルに UPSERT する。

[アルゴリズム概要]
  S1 (40%): 当日グループT偏差値  — コース取り補正後タイムを (日付×センター×コース種別) で標準化
  S2 (25%): 自己ベース比較       — 同コース種別の自己過去S1との乖離（PiT expanding）
  S3 (20%): 末脚Tスコア          — lap_1 を S1 と同一グループ（course_type 含む）で標準化
  S_accel:  加速ラップボーナス   — lap_2−lap_1 の正値のみを 0〜10pt でボーナス化
  S4 (15%): 調教本数スコア       — レース間隔内の「質の高い調教（S1≥45）」本数

  chokyo_master_score = clip(0.40×S1 + 0.25×S2 + 0.20×S3 + 0.15×S4 + S_accel, 0, 100)

[重要設計方針]
  - 坂路とウッドのラスト1Fを絶対に同一分布で偏差値化しない
    → S1/S3 のグループキーに _course_type を必ず含める
  - WCコース取り補正係数は鈴木理論準拠（内外で約3.2秒差 = E補正最大3.2秒）
  - PiT厳守: 各セッションの自己比較は「そのセッション日より前」の履歴のみ使用

[実行方法]
  py -m ml.batch.chokyo_score_batch [--from-year 2015]
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

# ──────────────────────────────────────────────────────────────────────
# 定数
# ──────────────────────────────────────────────────────────────────────

_WC_AE_CORRECTION: dict[str, float] = {
    "A": 0.0, "B": 0.8, "C": 1.6, "D": 2.4, "E": 3.2,
}
_IMO_CORRECTION: dict[str, float] = {
    "I": 0.0, "M": 0.15, "O": 0.30,
}

_SLOPE_CODES = {"坂路", "S", "HC", "slope", "Hanro"}
_WC_CODES = {
    "A", "B", "C", "D", "E",
    "ウッドチップ", "CW", "Wood", "Poly", "P",
    "00", "01", "10", "11", "20", "21", "30", "31",
    "40", "41", "50", "51", "60", "61", "70", "71", "80", "81",
}
_CENTER_MAP = {
    "1": "栗東", "12": "栗東", "05": "栗東", "栗東": "栗東",
    "0": "美浦", "02": "美浦", "06": "美浦", "美浦": "美浦",
    "地方": "地方", "09": "地方",
    "北海道": "北海道", "10": "北海道",
}

_TSCORE_GROUP = ["_date_str", "_center_norm", "_course_type"]
_MIN_GROUP_SIZE = 3
_MIN_TIME_STD = 0.3
_MIN_LAP_STD = 0.1

_SELF_MIN_COUNT = 3
_SELF_STD_FLOOR = 5.0

_REF_WINDOW_DAYS = 28

_S4_QUALITY_THRESHOLD = 45.0
_S4_INTER_RACE_MAX_DAYS = 56

_S4_SCORE_MAP = {0: 20, 1: 35, 2: 50, 3: 65}
_S4_SCORE_MAP.update({k: 80 for k in range(4, 7)})
_S4_SCORE_MAP.update({k: 70 for k in range(7, 10)})

_W_S1, _W_S2, _W_S3, _W_S4 = 0.40, 0.25, 0.20, 0.15
_S_ACCEL_SCALE = 0.3

# ──────────────────────────────────────────────────────────────────────
# DDL / DML
# ──────────────────────────────────────────────────────────────────────
_DDL_CREATE = """
CREATE TABLE IF NOT EXISTS chokyo_scores (
    race_id                 VARCHAR(12)   NOT NULL,
    ketto_toroku_bango      VARCHAR(10)   NOT NULL,
    chokyo_master_score     FLOAT,
    s1_time_score           FLOAT,
    s2_improve_score        FLOAT,
    s3_lastf_score          FLOAT,
    s4_freq_score           FLOAT,
    accel_bonus             FLOAT,
    ref_session_days_before SMALLINT,
    computed_at             TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (race_id, ketto_toroku_bango)
)
"""

_UPSERT_SQL = text("""
INSERT INTO chokyo_scores
    (race_id, ketto_toroku_bango, chokyo_master_score,
     s1_time_score, s2_improve_score, s3_lastf_score, s4_freq_score,
     accel_bonus, ref_session_days_before, computed_at)
VALUES
    (:race_id, :ketto_toroku_bango, :chokyo_master_score,
     :s1_time_score, :s2_improve_score, :s3_lastf_score, :s4_freq_score,
     :accel_bonus, :ref_session_days_before, :computed_at)
ON CONFLICT (race_id, ketto_toroku_bango)
DO UPDATE SET
    chokyo_master_score     = EXCLUDED.chokyo_master_score,
    s1_time_score           = EXCLUDED.s1_time_score,
    s2_improve_score        = EXCLUDED.s2_improve_score,
    s3_lastf_score          = EXCLUDED.s3_lastf_score,
    s4_freq_score           = EXCLUDED.s4_freq_score,
    accel_bonus             = EXCLUDED.accel_bonus,
    ref_session_days_before = EXCLUDED.ref_session_days_before,
    computed_at             = EXCLUDED.computed_at
""")

# ──────────────────────────────────────────────────────────────────────
# Step 1: データロード
# ──────────────────────────────────────────────────────────────────────
_LOAD_TRAINING_SQL = """
SELECT
    horse_id,
    date        AS session_date,
    center,
    course,
    time_total,
    lap_1,
    lap_2,
    position
FROM training_data
WHERE time_total IS NOT NULL
  AND time_total > 0
  AND date IS NOT NULL
ORDER BY date, horse_id
"""

_LOAD_ENTRIES_SQL = """
SELECT
    r.id                    AS race_id,
    r.date                  AS race_date,
    e.horse_id              AS ketto_toroku_bango
FROM race_entries e
JOIN races r ON e.race_id = r.id
WHERE r.date IS NOT NULL
ORDER BY r.date, e.horse_id
"""


def _load_training_data(engine) -> pd.DataFrame:
    logger.info("調教データをロード中...")
    td = pd.read_sql(_LOAD_TRAINING_SQL, engine)
    td["session_date"] = pd.to_datetime(td["session_date"])
    td["time_total"] = pd.to_numeric(td["time_total"], errors="coerce")
    td["lap_1"] = pd.to_numeric(td["lap_1"], errors="coerce")
    td["lap_2"] = pd.to_numeric(td["lap_2"], errors="coerce")
    logger.info("  調教データ: %d 行 / %d 馬", len(td), td["horse_id"].nunique())
    return td


def _load_race_entries(engine) -> pd.DataFrame:
    logger.info("出走データをロード中...")
    entries = pd.read_sql(_LOAD_ENTRIES_SQL, engine)
    entries["race_date"] = pd.to_datetime(entries["race_date"])
    entries = entries.sort_values(["ketto_toroku_bango", "race_date"]).reset_index(drop=True)
    entries["prev_race_date"] = entries.groupby(
        "ketto_toroku_bango", observed=True
    )["race_date"].shift(1)
    logger.info("  出走データ: %d 行 / %d レース", len(entries), entries["race_id"].nunique())
    return entries


# ──────────────────────────────────────────────────────────────────────
# Step 2: 正規化 & コース取り補正
# ──────────────────────────────────────────────────────────────────────
def _normalize(td: pd.DataFrame) -> pd.DataFrame:
    td["_center_norm"] = td["center"].astype(str).str.strip().map(_CENTER_MAP).fillna("other")
    td["_date_str"] = td["session_date"].dt.strftime("%Y%m%d")
    course_raw = td["course"].astype(str).str.strip()
    td["_course_type"] = np.where(
        course_raw.isin(_SLOPE_CODES), "slope",
        np.where(course_raw.isin(_WC_CODES), "wc", "other")
    )
    return td


def _apply_position_correction(td: pd.DataFrame) -> pd.DataFrame:
    td["_corr_offset"] = 0.0
    ae_mask = td["course"].astype(str).str.strip().isin(_WC_AE_CORRECTION)
    if ae_mask.any():
        td.loc[ae_mask, "_corr_offset"] = (
            td.loc[ae_mask, "course"].astype(str).str.strip().map(_WC_AE_CORRECTION)
        )
    imo_mask = (td["_course_type"] == "wc") & ~ae_mask
    if imo_mask.any():
        td.loc[imo_mask, "_corr_offset"] = (
            td.loc[imo_mask, "position"].astype(str).str.strip()
            .map(_IMO_CORRECTION).fillna(0.0)
        )
    td["_time_corr"] = td["time_total"] - td["_corr_offset"]
    return td


# ──────────────────────────────────────────────────────────────────────
# Step 3: S1 — 当日グループ T偏差値
# ──────────────────────────────────────────────────────────────────────
def _compute_s1(td: pd.DataFrame) -> pd.DataFrame:
    logger.info("S1 (当日グループTスコア) を計算中...")
    grp = td.groupby(_TSCORE_GROUP, observed=True)["_time_corr"]
    td["_g_mean"] = grp.transform("mean")
    td["_g_std"]  = grp.transform("std")
    td["_g_count"] = grp.transform("count")
    valid = (
        (td["_g_count"] >= _MIN_GROUP_SIZE)
        & (td["_g_std"] >= _MIN_TIME_STD)
        & td["_time_corr"].notna()
    )
    td["s1"] = np.where(
        valid,
        np.clip(50.0 - (td["_time_corr"] - td["_g_mean"]) / td["_g_std"] * 10.0, 0.0, 100.0),
        np.nan,
    )
    n_valid = td["s1"].notna().sum()
    logger.info("  S1 有効: %d/%d (%.1f%%)", n_valid, len(td), n_valid / len(td) * 100)
    td.drop(columns=["_g_mean", "_g_std", "_g_count"], inplace=True)
    return td


# ──────────────────────────────────────────────────────────────────────
# Step 4: S3 基礎値 — lap_1 T偏差値
# ──────────────────────────────────────────────────────────────────────
def _compute_s3_base(td: pd.DataFrame) -> pd.DataFrame:
    logger.info("S3 (lap_1 Tスコア) を計算中... [course_type 完全独立]")
    valid_lap = td["lap_1"].notna()
    if valid_lap.sum() < 2:
        td["s3_base"] = np.nan
        return td
    grp_l = td[valid_lap].groupby(_TSCORE_GROUP, observed=True)["lap_1"]
    g_mean_l = grp_l.transform("mean")
    g_std_l  = grp_l.transform("std")
    g_count_l = grp_l.transform("count")
    lap_valid_mask = valid_lap & (g_count_l >= _MIN_GROUP_SIZE) & (g_std_l >= _MIN_LAP_STD)
    td["s3_base"] = np.where(
        lap_valid_mask,
        np.clip(50.0 - (td["lap_1"] - g_mean_l) / g_std_l * 10.0, 0.0, 100.0),
        np.nan,
    )
    n_valid = td["s3_base"].notna().sum()
    logger.info("  S3 基礎値 有効: %d/%d (%.1f%%)", n_valid, len(td), n_valid / len(td) * 100)
    return td


# ──────────────────────────────────────────────────────────────────────
# Step 5: S_accel — 加速ラップボーナス
# ──────────────────────────────────────────────────────────────────────
def _compute_accel(td: pd.DataFrame) -> pd.DataFrame:
    accel_delta = (
        pd.to_numeric(td["lap_2"], errors="coerce")
        - pd.to_numeric(td["lap_1"], errors="coerce")
    )
    td["s_accel"] = np.clip(accel_delta / _S_ACCEL_SCALE * 5.0, 0.0, 10.0).fillna(0.0)
    return td


# ──────────────────────────────────────────────────────────────────────
# Step 6: 自己過去S1の統計（PiT expanding）
# ──────────────────────────────────────────────────────────────────────
def _compute_self_stats(td: pd.DataFrame) -> pd.DataFrame:
    """
    各調教セッションに対し、「そのセッション日より前」の
    同コース種別における自己S1の累積平均・標準偏差・件数を付与する。

    PiT 境界: expanding().mean().shift(1) により当該セッション自身を除外。
    merge を廃止しインデックスベース直接代入: 同日複数追いによる多対多爆発を防ぐ。
    """
    logger.info("自己過去S1統計（PiT expanding）を計算中...")
    td = td.reset_index(drop=True)
    valid_mask = td["s1"].notna()
    td_valid = td.loc[valid_mask, ["horse_id", "_course_type", "session_date", "s1"]].copy()
    td_valid = td_valid.sort_values(["horse_id", "_course_type", "session_date"])
    grp = td_valid.groupby(["horse_id", "_course_type"], observed=True)["s1"]
    td_valid["_self_avg"]   = grp.transform(lambda x: x.expanding().mean().shift(1))
    td_valid["_self_std"]   = grp.transform(lambda x: x.expanding().std().shift(1))
    td_valid["_self_count"] = grp.transform(lambda x: x.expanding().count().shift(1))
    td["_self_avg"]   = np.nan
    td["_self_std"]   = np.nan
    td["_self_count"] = np.nan
    td.loc[td_valid.index, "_self_avg"]   = td_valid["_self_avg"]
    td.loc[td_valid.index, "_self_std"]   = td_valid["_self_std"]
    td.loc[td_valid.index, "_self_count"] = td_valid["_self_count"]
    logger.info("  自己統計付与: %d 行 / avg有効 %d 件", len(td), td["_self_avg"].notna().sum())
    return td


# ──────────────────────────────────────────────────────────────────────
# Step 7: 参照セッション特定（明示的 JOIN）
# ──────────────────────────────────────────────────────────────────────
def _find_reference_sessions(
    entries: pd.DataFrame,
    td: pd.DataFrame,
) -> pd.DataFrame:
    """
    各 (race_id, horse_id) について、レース前 _REF_WINDOW_DAYS 日以内の
    最直近調教セッションを特定し、スコア列を付与する。

    merge_asof 廃止: by= 使用時のサイレント全 NaN バグを回避するため
    明示的 JOIN + 日付フィルタ + idxmax で実装。年ごとに処理。
    """
    logger.info("参照セッションを特定中（明示的 JOIN + %d日窓）...", _REF_WINDOW_DAYS)

    _REF_COLS = [
        "session_date", "_course_type",
        "s1", "s3_base", "s_accel",
        "_self_avg", "_self_std", "_self_count",
    ]

    td_ref = td[["horse_id"] + _REF_COLS].rename(
        columns={"horse_id": "ketto_toroku_bango"}
    ).copy()

    td_ref["ketto_toroku_bango"] = td_ref["ketto_toroku_bango"].astype(str).str.strip()
    td_ref["session_date"]       = pd.to_datetime(td_ref["session_date"])
    entries = entries.copy()
    entries["ketto_toroku_bango"] = entries["ketto_toroku_bango"].astype(str).str.strip()
    entries["race_date"]          = pd.to_datetime(entries["race_date"])

    td_best = (
        td_ref
        .sort_values(
            ["ketto_toroku_bango", "session_date", "s1"],
            ascending=[True, True, False],
            na_position="last",
        )
        .drop_duplicates(subset=["ketto_toroku_bango", "session_date"], keep="first")
        .reset_index(drop=True)
    )

    years   = sorted(entries["race_date"].dt.year.unique())
    results = []

    for year in years:
        ent_y = entries[entries["race_date"].dt.year == year].copy()
        win_start = pd.Timestamp(year, 1, 1) - pd.Timedelta(days=_REF_WINDOW_DAYS)
        win_end   = pd.Timestamp(year, 12, 31)
        td_y = td_best[
            (td_best["session_date"] >= win_start)
            & (td_best["session_date"] <= win_end)
        ]

        if len(td_y) == 0 or len(ent_y) == 0:
            for c in _REF_COLS:
                if c not in ent_y.columns:
                    ent_y[c] = np.nan
            ent_y["ref_session_days_before"] = pd.array(
                [pd.NA] * len(ent_y), dtype="Int16"
            )
            results.append(ent_y)
            continue

        merged = ent_y.merge(
            td_y[["ketto_toroku_bango"] + _REF_COLS],
            on="ketto_toroku_bango",
            how="inner",
        )

        delta_days = (merged["race_date"] - merged["session_date"]).dt.days
        in_window  = (delta_days > 0) & (delta_days <= _REF_WINDOW_DAYS)
        valid      = merged[in_window].copy()
        valid["_delta"] = delta_days[in_window].values

        if len(valid) > 0:
            best_idx = valid.groupby(
                ["race_id", "ketto_toroku_bango"]
            )["session_date"].idxmax()
            best = valid.loc[best_idx].drop_duplicates(
                subset=["race_id", "ketto_toroku_bango"]
            )
            ent_y = ent_y.merge(
                best[["race_id", "ketto_toroku_bango", "_delta"] + _REF_COLS],
                on=["race_id", "ketto_toroku_bango"],
                how="left",
            )
        else:
            ent_y["_delta"] = np.nan
            for c in _REF_COLS:
                if c not in ent_y.columns:
                    ent_y[c] = np.nan

        ent_y["ref_session_days_before"] = ent_y["_delta"].astype("Int16")
        ent_y.drop(columns=["_delta"], inplace=True, errors="ignore")

        n_y = ent_y["s1"].notna().sum()
        logger.info("  %d年: %d/%d 件 (%.0f%%)", year, n_y, len(ent_y), n_y / len(ent_y) * 100)
        results.append(ent_y)

    result = pd.concat(results, ignore_index=True)
    n_found = result["s1"].notna().sum()
    logger.info("  参照セッション特定合計: %d/%d 件 (%.1f%%)", n_found, len(result), n_found / len(result) * 100)
    return result


# ──────────────────────────────────────────────────────────────────────
# Step 8: S2 計算
# ──────────────────────────────────────────────────────────────────────
def _compute_s2(result: pd.DataFrame) -> pd.DataFrame:
    has_self = (
        result["_self_count"].notna()
        & (result["_self_count"] >= _SELF_MIN_COUNT)
        & result["_self_avg"].notna()
    )
    s1_today = result["s1"]
    self_avg  = result["_self_avg"]
    self_std  = result["_self_std"].fillna(_SELF_STD_FLOOR).clip(lower=_SELF_STD_FLOOR)
    improvement_z = (s1_today - self_avg) / self_std
    s2_computed = np.clip(50.0 + improvement_z * 10.0, 0.0, 100.0)
    result["s2"] = np.where(has_self, s2_computed, 50.0)
    result.loc[result["s1"].isna(), "s2"] = np.nan
    return result


# ──────────────────────────────────────────────────────────────────────
# Step 9: S4 計算
# ──────────────────────────────────────────────────────────────────────
def _s4_from_count(n: int) -> float:
    if n >= 10:
        return 55.0
    return float(_S4_SCORE_MAP.get(n, 55.0))


def _compute_s4(result: pd.DataFrame, td: pd.DataFrame) -> pd.DataFrame:
    logger.info("S4 (調教本数スコア) を計算中...")
    quality_td = td[td["s1"] >= _S4_QUALITY_THRESHOLD][
        ["horse_id", "session_date"]
    ].rename(columns={
        "horse_id": "ketto_toroku_bango",
        "session_date": "q_date",
    }).copy()

    result["_inter_start"] = result.apply(
        lambda r: max(
            r["prev_race_date"] if pd.notna(r["prev_race_date"]) else r["race_date"] - pd.Timedelta(days=_S4_INTER_RACE_MAX_DAYS),
            r["race_date"] - pd.Timedelta(days=_S4_INTER_RACE_MAX_DAYS),
        ),
        axis=1,
    )

    q_merged = result[["race_id", "ketto_toroku_bango", "race_date", "_inter_start"]].merge(
        quality_td, on="ketto_toroku_bango", how="left"
    )
    in_window = (
        (q_merged["q_date"] >= q_merged["_inter_start"])
        & (q_merged["q_date"] < q_merged["race_date"])
    )
    counts = (
        q_merged[in_window]
        .groupby(["race_id", "ketto_toroku_bango"], observed=True)
        .size()
        .rename("_quality_count")
        .reset_index()
    )

    result = result.merge(counts, on=["race_id", "ketto_toroku_bango"], how="left")
    result["_quality_count"] = result["_quality_count"].fillna(0).astype(int)
    result["s4"] = result["_quality_count"].map(_s4_from_count)
    logger.info("  S4 計算完了: quality_count 中央値 %.1f", result["_quality_count"].median())
    result.drop(columns=["_inter_start", "_quality_count"], inplace=True)
    return result


# ──────────────────────────────────────────────────────────────────────
# Step 10: 統合スコア計算
# ──────────────────────────────────────────────────────────────────────
def _integrate(result: pd.DataFrame) -> pd.DataFrame:
    s1 = result["s1"]
    s2 = result["s2"]
    s3 = result["s3_base"].where(result["s3_base"].notna(), s1)
    s4 = result["s4"]
    accel = result["s_accel"].fillna(0.0)
    raw = _W_S1 * s1 + _W_S2 * s2 + _W_S3 * s3 + _W_S4 * s4
    result["chokyo_master_score"] = np.where(
        s1.notna(),
        np.clip(raw + accel, 0.0, 100.0),
        np.nan,
    )
    n_valid = result["chokyo_master_score"].notna().sum()
    score = result["chokyo_master_score"].dropna()
    logger.info(
        "  統合スコア: %d/%d 有効 | mean=%.1f std=%.1f min=%.1f max=%.1f",
        n_valid, len(result), score.mean(), score.std(), score.min(), score.max(),
    )
    result = result.rename(columns={
        "s1": "s1_time_score",
        "s2": "s2_improve_score",
        "s3_base": "s3_lastf_score",
        "s4": "s4_freq_score",
        "s_accel": "accel_bonus",
    })
    return result


# ──────────────────────────────────────────────────────────────────────
# Step 11: DB UPSERT
# ──────────────────────────────────────────────────────────────────────
def _upsert(result: pd.DataFrame, engine) -> int:
    logger.info("chokyo_scores テーブルに UPSERT 中...")
    with engine.connect() as conn:
        conn.execute(text(_DDL_CREATE))
        conn.commit()

    out_cols = [
        "race_id", "ketto_toroku_bango", "chokyo_master_score",
        "s1_time_score", "s2_improve_score", "s3_lastf_score", "s4_freq_score",
        "accel_bonus", "ref_session_days_before",
    ]
    if "s3_lastf_score" not in result.columns and "s3_base" in result.columns:
        result = result.rename(columns={"s3_base": "s3_lastf_score"})

    out = result[out_cols].copy()
    out["computed_at"] = datetime.utcnow()
    records = out.to_dict(orient="records")

    _CHUNK = 5_000
    with engine.connect() as conn:
        for start in range(0, len(records), _CHUNK):
            conn.execute(_UPSERT_SQL, records[start : start + _CHUNK])
        conn.commit()

    logger.info("  UPSERT 完了: %d 行", len(records))
    return len(records)


# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────

def run(from_year: int = 2015) -> int:
    """全期間の調教スコアを計算し chokyo_scores に UPSERT する。

    from_year 以前のデータも S1/S2 の PiT 計算に使用するが、
    DB への書き込みは from_year 以降の行のみ。

    Returns:
        UPSERT した行数。
    """
    from ml.db import engine as _engine

    td      = _load_training_data(_engine)
    entries = _load_race_entries(_engine)

    td = _normalize(td)
    td = _apply_position_correction(td)
    td = _compute_s1(td)
    td = _compute_s3_base(td)
    td = _compute_accel(td)
    td = _compute_self_stats(td)

    result = _find_reference_sessions(entries, td)
    result = _compute_s2(result)
    result = _compute_s4(result, td)
    result = _integrate(result)

    out = result[result["race_date"].dt.year >= from_year].copy()
    logger.info(
        "  UPSERT 対象: %d 年以降 %d 行 (%d レース)",
        from_year, len(out), out["race_id"].nunique(),
    )

    from ml.db import engine as _engine  # noqa: F811 — re-import for clarity
    return _upsert(out, _engine)


# ──────────────────────────────────────────────────────────────────────
# CLI エントリポイント
# ──────────────────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    parser = argparse.ArgumentParser(description="調教マスタースコア バッチ計算")
    parser.add_argument(
        "--from-year", type=int, default=2015,
        help="UPSERT 対象の最古年（デフォルト: 2015）",
    )
    args = parser.parse_args()
    n = run(from_year=args.from_year)
    logger.info("完了。UPSERT: %d 行", n)


if __name__ == "__main__":
    main()
