"""
scripts/run_final_validation.py
================================
Phase 2 最終検証スクリプト

Step1: 安定性検証（3期間分割）
  - ダート中距離|全体: class_ok+interval_ok+surface_ok+f3_top+sire_venue (複66.4%/ROI112.9%)
  - ダート中距離|坂あり: margin+class_ok+f3_top+hill_fit+sire_venue (複67.0%/ROI101.2%)
  - 穴馬 芝短距離(野芝): margin+jockey_ok+sire_venue+sire_surface (単ROI197.3%)

Step2: アブレーション（条件除外テスト）
  66.4%パターン（class_ok+interval_ok+surface_ok+f3_top+sire_venue）の
  各条件を1つずつ外した場合の的中率変化

Step3: 実運用シミュレーション（直近10日間）
  該当馬一覧、条件充足理由、実際の着順、的中判定

使用例:
  py -3 scripts/run_final_validation.py --from-date 2025-06-27 --to-date 2026-06-27
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).parent.parent))

from ml.db import engine as _engine
from tipster.backtest import _load_bulk_data, _LOOKBACK_DAYS
from tipster.combo_backtest import _combo_str, _fetch_payouts_bulk
from tipster.conditions import _class_level_from_codes

# ─────────────────────────────────────────────────────────────────────────
# 競馬場特性マスタ
# ─────────────────────────────────────────────────────────────────────────

_RC_JSON = Path(__file__).parent.parent / "tipster" / "racecourse_features.json"
with open(_RC_JSON, encoding="utf-8") as _f:
    _RC_FEAT = {k: v for k, v in json.load(_f).items() if not k.startswith("_")}

_WESTERN_PC  = frozenset(pc for pc, f in _RC_FEAT.items() if f["turf_type"] == "western")
_HILL_PC     = frozenset(pc for pc, f in _RC_FEAT.items() if f["has_hill"])
_LONG_STR_PC = frozenset(pc for pc, f in _RC_FEAT.items() if f["straight_m"] >= 400)
_SMALL_PC    = frozenset(pc for pc, f in _RC_FEAT.items() if f["course_size"] == "small")

# ─────────────────────────────────────────────────────────────────────────
# 検証対象パターン定義
# ─────────────────────────────────────────────────────────────────────────

PATTERNS = {
    "P1_dirt_mid_all": {
        "label": "ダート中距離|全体",
        "segment": ("ダート", 1401, 9999),
        "l2_pc_set": None,  # フィルタなし
        "conds": ["class_ok", "interval_ok", "surface_ok", "f3_top", "sire_venue"],
        "min_ninki": None,
        "expected_place": 0.664,
        "expected_roi_place": 1.129,
    },
    "P2_dirt_mid_hill": {
        "label": "ダート中距離|坂あり",
        "segment": ("ダート", 1401, 9999),
        "l2_pc_set": _HILL_PC,
        "conds": ["margin", "class_ok", "f3_top", "hill_fit", "sire_venue"],
        "min_ninki": None,
        "expected_place": 0.670,
        "expected_roi_place": 1.012,
    },
    "P3_turf_sprint_western_anaba": {
        "label": "芝短距離(野芝) 穴馬",
        "segment": ("芝", 0, 1400),
        "l2_pc_set": frozenset(pc for pc in _RC_FEAT if pc not in _WESTERN_PC),  # 野芝 = NOT western
        "conds": ["margin", "jockey_ok", "sire_venue", "sire_surface"],
        "min_ninki": 4,  # 4番人気以降
        "expected_place": 0.338,
        "expected_win_roi": 1.973,
    },
}

# ─────────────────────────────────────────────────────────────────────────
# SQL
# ─────────────────────────────────────────────────────────────────────────

_SUPP_SQL = text("""
    SELECT e.race_id, e.horse_id, h.sire_id,
           e.f3_time, e.popularity,
           r.track_condition,
           b.sire_turf_wr, b.sire_dirt_wr,
           b.sire_sprint_wr, b.sire_mile_wr, b.sire_middle_wr, b.sire_long_wr,
           b.sire_heavy_wr
    FROM race_entries e
    JOIN races r ON e.race_id = r.id
    JOIN horses h ON h.id = e.horse_id
    LEFT JOIN bloodline_feature_store b
           ON b.horse_id = e.horse_id AND b.race_id = e.race_id
    WHERE r.date BETWEEN :start AND :end
      AND e.confirmed_rank IS NOT NULL AND e.confirmed_rank > 0
      AND r.course_type IN ('芝','ダート')
      AND r.place_code <= '10'
""")

_SIRE_SQL = text("""
    SELECT sire_id, target_date,
           top3_rate AS sire_top3_rate,
           venue_01_top3_rate, venue_01_count,
           venue_02_top3_rate, venue_02_count,
           venue_03_top3_rate, venue_03_count,
           venue_04_top3_rate, venue_04_count,
           venue_05_top3_rate, venue_05_count,
           venue_06_top3_rate, venue_06_count,
           venue_07_top3_rate, venue_07_count,
           venue_08_top3_rate, venue_08_count,
           venue_09_top3_rate, venue_09_count,
           venue_10_top3_rate, venue_10_count,
           surface_turf_top3_rate AS sire_sfs_turf_top3,
           surface_dirt_top3_rate AS sire_sfs_dirt_top3
    FROM sire_feature_store
    ORDER BY sire_id, target_date
""")

_HORSE_NAME_SQL = text("""
    SELECT id AS horse_id, name AS horse_name FROM horses
""")

_RACE_SQL = text("""
    SELECT id AS race_id, race_name, date, place_code, course_type, distance,
           track_condition, race_number
    FROM races
    WHERE date BETWEEN :start AND :end
      AND course_type IN ('芝','ダート')
      AND place_code <= '10'
""")

_JOCKEY_NAME_SQL = text("""
    SELECT id AS jockey_id, name AS jockey_name FROM jockeys
""")

_VENUE_NAME = {
    "01": "札幌", "02": "函館", "03": "福島", "04": "新潟", "05": "東京",
    "06": "中山", "07": "中京", "08": "京都", "09": "阪神", "10": "小倉",
}

# ─────────────────────────────────────────────────────────────────────────
# データ読み込み
# ─────────────────────────────────────────────────────────────────────────

def _merge_sire_pit(df: pd.DataFrame, sire_df: pd.DataFrame) -> pd.DataFrame:
    """PIT (Point-In-Time) 種牡馬特性マージ: レース日より前の最新スナップショットを使用"""
    sire_df = sire_df.copy()
    sire_df["target_date"] = pd.to_datetime(sire_df["target_date"]).astype("datetime64[us]")
    sire_cols = [c for c in sire_df.columns if c not in ("sire_id", "target_date")]
    race_keys = df[["race_id", "sire_id", "date"]].drop_duplicates().copy()
    race_keys["date"] = race_keys["date"].astype("datetime64[us]")
    race_keys = race_keys.sort_values("date").reset_index(drop=True)
    sire_sorted = sire_df.sort_values("target_date").reset_index(drop=True)
    pit = pd.merge_asof(
        race_keys, sire_sorted,
        left_on="date", right_on="target_date",
        by="sire_id", direction="backward",
    )
    pit = pit[["race_id", "sire_id"] + sire_cols]
    return df.merge(pit, on=["race_id", "sire_id"], how="left")


def _load_extended(load_start: date, to_date: date) -> pd.DataFrame:
    print(f"[val] ベースデータ読み込み ({load_start} ~ {to_date})...")
    base = _load_bulk_data(load_start, to_date)

    print("[val] 拡張フィールド読み込み...")
    supp = pd.read_sql(_SUPP_SQL, _engine, params={"start": load_start, "end": to_date})

    print("[val] 種牡馬特性読み込み (PIT)...")
    sire_df = pd.read_sql(_SIRE_SQL, _engine)

    print("[val] 馬名読み込み...")
    horse_names = pd.read_sql(_HORSE_NAME_SQL, _engine)

    print("[val] 騎手名読み込み...")
    jockey_names = pd.read_sql(_JOCKEY_NAME_SQL, _engine)

    df = base.merge(supp, on=["race_id", "horse_id"], how="left")
    df = _merge_sire_pit(df, sire_df)
    df = df.merge(horse_names, on="horse_id", how="left")
    df = df.merge(jockey_names, on="jockey_id", how="left")

    def _cl(row):
        return _class_level_from_codes(
            str(row["grade_code"]) if pd.notna(row["grade_code"]) else None,
            str(row["jyoken_cd_3"]) if pd.notna(row["jyoken_cd_3"]) else None,
        )
    df["class_level"] = df.apply(_cl, axis=1)

    df["f3_rank"] = df.groupby("race_id")["f3_time"].rank(ascending=True, na_option="keep")
    df["f3_rank_pct"] = df["f3_rank"] / df["field_size"]

    return df


# ─────────────────────────────────────────────────────────────────────────
# 特徴量計算（run_racecourse_search.py と同一）
# ─────────────────────────────────────────────────────────────────────────

CONDS_ALL = [
    "margin", "class_ok", "jockey_ok", "weight_ok", "interval_ok",
    "surface_ok", "f3_top", "sire_surf", "sire_dist", "heavy_ok",
    "rc_fit", "turf_type_fit", "straight_fit", "hill_fit", "sire_venue", "sire_surface",
]


def _build_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values(["horse_id", "date"]).reset_index(drop=True)
    g = df.groupby("horse_id", sort=False)

    for i in range(1, 4):
        df[f"prev{i}_rank"]       = g["confirmed_rank"].shift(i)
        df[f"prev{i}_surface"]    = g["surface"].shift(i)
        df[f"prev{i}_distance"]   = g["distance"].shift(i)
        df[f"prev{i}_margin"]     = g["this_margin"].shift(i)
        df[f"prev{i}_tc"]         = g["track_condition"].shift(i)
        df[f"prev{i}_class"]      = g["class_level"].shift(i)
        df[f"prev{i}_f3pct"]      = g["f3_rank_pct"].shift(i)
        df[f"prev{i}_place_code"] = g["place_code"].shift(i)

    df["days_since_prev"] = (df["date"] - df["prev_race_date"]).dt.days

    # 0: margin
    df["cond_margin"] = (df["prev1_margin"] <= 1.0).astype(object)
    df.loc[df["prev1_margin"].isna(), "cond_margin"] = None

    # 1: class_ok
    df["cond_class_ok"] = (df["class_level"] <= df["prev1_class"]).astype(object)
    df.loc[df["prev1_class"].isna(), "cond_class_ok"] = None

    # 2: jockey_ok
    yr_wins = df["jockey_yr_wins_db"] if "jockey_yr_wins_db" in df.columns else pd.Series(np.nan, index=df.index)
    cont = df["jockey_id"] == df["prev_jockey_id"]
    lead = yr_wins >= 30
    df["cond_jockey_ok"] = (cont | lead).astype(object)
    df.loc[df["prev_jockey_id"].isna(), "cond_jockey_ok"] = None

    # 3: weight_ok
    df["cond_weight_ok"] = (df["burden_weight"] <= (df["prev_burden_weight"] + 0.4)).astype(object)
    df.loc[df["prev_burden_weight"].isna(), "cond_weight_ok"] = None

    # 4: interval_ok
    iv = df["days_since_prev"]
    df["cond_interval_ok"] = ((iv >= 15) & (iv <= 28)).astype(object)
    df.loc[iv.isna(), "cond_interval_ok"] = None

    # 5: surface_ok
    surf_good = pd.Series(False, index=df.index)
    surf_any  = pd.Series(False, index=df.index)
    for i in range(1, 4):
        same = (df[f"prev{i}_surface"] == df["surface"]) & df[f"prev{i}_surface"].notna()
        good = same & (df[f"prev{i}_rank"] <= 3) & df[f"prev{i}_rank"].notna()
        surf_good |= good
        surf_any  |= same
    df["cond_surface_ok"] = np.where(~surf_any, None, surf_good.astype(float))

    # 6: f3_top
    df["cond_f3_top"] = (df["prev1_f3pct"] <= 0.33).astype(object)
    df.loc[df["prev1_f3pct"].isna(), "cond_f3_top"] = None

    # 7: sire_surf
    SIRE_THRESH = 0.02
    turf_fit = (df["sire_turf_wr"] >= df["sire_dirt_wr"] + SIRE_THRESH)
    dirt_fit = (df["sire_dirt_wr"] >= df["sire_turf_wr"] + SIRE_THRESH)
    df["cond_sire_surf"] = np.where(df["surface"] == "芝", turf_fit, dirt_fit).astype(object)
    df.loc[df["sire_turf_wr"].isna(), "cond_sire_surf"] = None

    # 8: sire_dist
    dist_cols = ["sire_sprint_wr", "sire_mile_wr", "sire_middle_wr", "sire_long_wr"]
    df["_sire_dist_avg"] = df[dist_cols].mean(axis=1)
    cur_sire_wr = pd.Series(np.nan, index=df.index)
    cur_sire_wr = cur_sire_wr.where(df["distance"] > 1400, df["sire_sprint_wr"])
    cur_sire_wr = cur_sire_wr.where(
        ~((df["distance"] > 1400) & (df["distance"] <= 1800)), df["sire_mile_wr"])
    cur_sire_wr = cur_sire_wr.where(
        ~((df["distance"] > 1800) & (df["distance"] <= 2200)), df["sire_middle_wr"])
    cur_sire_wr = cur_sire_wr.where(df["distance"] <= 2200, df["sire_long_wr"])
    df["cond_sire_dist"] = (cur_sire_wr >= df["_sire_dist_avg"]).astype(object)
    df.loc[cur_sire_wr.isna(), "cond_sire_dist"] = None

    # 9: heavy_ok
    heavy_good = pd.Series(False, index=df.index)
    heavy_any  = pd.Series(False, index=df.index)
    for i in range(1, 4):
        is_heavy = df[f"prev{i}_tc"].isin(["3", "4"]) & df[f"prev{i}_tc"].notna()
        good = is_heavy & (df[f"prev{i}_rank"] <= 3) & df[f"prev{i}_rank"].notna()
        heavy_good |= good
        heavy_any  |= is_heavy
    df["cond_heavy_ok"] = np.where(~heavy_any, None, heavy_good.astype(float))

    # 10: rc_fit
    rc_good = pd.Series(False, index=df.index)
    rc_any  = pd.Series(False, index=df.index)
    for i in range(1, 4):
        same_rc = (df[f"prev{i}_place_code"] == df["place_code"]) & df[f"prev{i}_place_code"].notna()
        good    = same_rc & (df[f"prev{i}_rank"] <= 3) & df[f"prev{i}_rank"].notna()
        rc_good |= good
        rc_any  |= same_rc
    df["cond_rc_fit"] = np.where(~rc_any, None, rc_good.astype(float))

    # 11: turf_type_fit
    cur_western = df["place_code"].isin(_WESTERN_PC)
    tt_good = pd.Series(False, index=df.index)
    tt_any  = pd.Series(False, index=df.index)
    for i in range(1, 4):
        prev_western = df[f"prev{i}_place_code"].isin(_WESTERN_PC) & df[f"prev{i}_place_code"].notna()
        same_type = (cur_western == prev_western) & df[f"prev{i}_place_code"].notna()
        good = same_type & (df[f"prev{i}_rank"] <= 3) & df[f"prev{i}_rank"].notna()
        tt_good |= good
        tt_any  |= same_type
    df["cond_turf_type_fit"] = np.where(~tt_any, None, tt_good.astype(float))

    # 12: straight_fit
    cur_long = df["place_code"].isin(_LONG_STR_PC)
    sf_good = pd.Series(False, index=df.index)
    sf_any  = pd.Series(False, index=df.index)
    for i in range(1, 4):
        prev_long = df[f"prev{i}_place_code"].isin(_LONG_STR_PC) & df[f"prev{i}_place_code"].notna()
        same_type = (cur_long == prev_long) & df[f"prev{i}_place_code"].notna()
        good = same_type & (df[f"prev{i}_rank"] <= 3) & df[f"prev{i}_rank"].notna()
        sf_good |= good
        sf_any  |= same_type
    df["cond_straight_fit"] = np.where(~sf_any, None, sf_good.astype(float))

    # 13: hill_fit
    cur_hill = df["place_code"].isin(_HILL_PC)
    hf_good  = pd.Series(False, index=df.index)
    hf_any   = pd.Series(False, index=df.index)
    for i in range(1, 4):
        prev_hill = df[f"prev{i}_place_code"].isin(_HILL_PC) & df[f"prev{i}_place_code"].notna()
        same_type = (cur_hill == prev_hill) & df[f"prev{i}_place_code"].notna()
        good = same_type & (df[f"prev{i}_rank"] <= 3) & df[f"prev{i}_rank"].notna()
        hf_good |= good
        hf_any  |= same_type
    df["cond_hill_fit"] = np.where(~hf_any, None, hf_good.astype(float))

    # 14: sire_venue
    pc = df["place_code"]
    sire_ven_rate  = pd.Series(np.nan, index=df.index)
    sire_ven_count = pd.Series(0.0,    index=df.index)
    for code in [f"{i:02d}" for i in range(1, 11)]:
        mask = pc == code
        col_r = f"venue_{code}_top3_rate"
        col_c = f"venue_{code}_count"
        if col_r in df.columns:
            sire_ven_rate[mask]  = df.loc[mask, col_r]
            sire_ven_count[mask] = df.loc[mask, col_c]
    df["cond_sire_venue"] = (sire_ven_rate > df["sire_top3_rate"]).astype(object)
    df.loc[df["sire_top3_rate"].isna() | (sire_ven_count < 10), "cond_sire_venue"] = None

    # 15: sire_surface
    s_turf = df.get("sire_sfs_turf_top3", pd.Series(np.nan, index=df.index))
    s_dirt = df.get("sire_sfs_dirt_top3", pd.Series(np.nan, index=df.index))
    turf_ok = df["surface"] == "芝"
    sire_surf_fit = np.where(turf_ok, s_turf >= s_dirt + 0.01, s_dirt >= s_turf + 0.01)
    df["cond_sire_surface"] = sire_surf_fit.astype(object)
    df.loc[s_turf.isna() | s_dirt.isna(), "cond_sire_surface"] = None

    return df


# ─────────────────────────────────────────────────────────────────────────
# マッチ判定
# ─────────────────────────────────────────────────────────────────────────

def _match_series(df: pd.DataFrame, cond_names: list[str]) -> pd.Series:
    match = pd.Series(True, index=df.index)
    for c in cond_names:
        match = match & (df[f"cond_{c}"] == 1.0)
    return match


def _seg_mask(df: pd.DataFrame, surface: str, d_lo: int, d_hi: int,
              from_date: date, to_date: date) -> pd.Series:
    return (
        (df["surface"] == surface)
        & (df["distance"] >= d_lo) & (df["distance"] <= d_hi)
        & (df["date"].dt.date >= from_date) & (df["date"].dt.date <= to_date)
    )


def _calc_stats_df(sub: pd.DataFrame) -> dict:
    n = len(sub)
    if n == 0:
        return {"n": 0}
    place = int((sub["confirmed_rank"] <= 3).sum())
    win   = int((sub["confirmed_rank"] == 1).sum())
    return {
        "n": n, "place": place, "win": win,
        "place_rate": place / n,
        "win_rate":   win   / n,
        "n_races": sub["race_id"].nunique(),
    }


def _calc_roi_df(sub: pd.DataFrame, payout_map: dict) -> dict:
    place_ret = win_ret = 0
    n = na = 0
    for _, row in sub.iterrows():
        rpm = payout_map.get(row["race_id"])
        if rpm is None or "fukusho" not in rpm:
            na += 1
            continue
        n += 1
        if pd.notna(row["umaban"]):
            combo = _combo_str(int(row["umaban"]))
            fp = rpm["fukusho"].get(combo)
            if fp is not None:
                place_ret += fp
            tp = rpm.get("tansho", {}).get(combo)
            if tp is not None:
                win_ret += tp
    return {
        "n_roi": n, "na_roi": na,
        "place_roi": place_ret / (n * 100) if n else 0.0,
        "win_roi":   win_ret   / (n * 100) if n else 0.0,
    }


# ─────────────────────────────────────────────────────────────────────────
# Step1: 安定性検証
# ─────────────────────────────────────────────────────────────────────────

def _step1_stability(df: pd.DataFrame, payout_map: dict,
                     from_date: date, to_date: date) -> None:
    print()
    print("=" * 70)
    print("Step1: 安定性検証（3期間分割）")
    print("=" * 70)

    total_days = (to_date - from_date).days
    pd_d = total_days // 3
    periods = [
        (from_date,                           from_date + timedelta(days=pd_d - 1),   "P1"),
        (from_date + timedelta(days=pd_d),    from_date + timedelta(days=pd_d*2 - 1), "P2"),
        (from_date + timedelta(days=pd_d*2),  to_date,                                "P3"),
    ]
    print(f"  期間分割: P1={periods[0][0]}~{periods[0][1]} / P2={periods[1][0]}~{periods[1][1]} "
          f"/ P3={periods[2][0]}~{periods[2][1]}")

    for pat_key, pat in PATTERNS.items():
        surf, d_lo, d_hi = pat["segment"]
        conds            = pat["conds"]
        l2_pc_set        = pat["l2_pc_set"]
        min_ninki        = pat["min_ninki"]

        print(f"\n--- {pat['label']} ---")
        print(f"  条件: {' + '.join(conds)}")
        if min_ninki:
            print(f"  対象: {min_ninki}番人気以降（穴馬）")
        print(f"  期待値: 複{pat.get('expected_place',0):.1%} / ROI複{pat.get('expected_roi_place',0):.1%}")

        period_results = []
        for p_from, p_to, lbl in periods:
            seg_m = _seg_mask(df, surf, d_lo, d_hi, p_from, p_to)
            if l2_pc_set is not None:
                seg_m = seg_m & df["place_code"].isin(l2_pc_set)
            if min_ninki is not None:
                seg_m = seg_m & df["popularity"].notna() & (df["popularity"] >= min_ninki)

            sub = df[seg_m]
            matched = sub[_match_series(sub, conds)]
            stats = _calc_stats_df(matched)
            if stats["n"] == 0:
                print(f"  {lbl}: データなし")
                continue
            roi = _calc_roi_df(matched, payout_map)
            period_results.append({
                "lbl": lbl, "p_from": p_from, "p_to": p_to,
                **stats, **roi
            })
            print(f"  {lbl} ({p_from}~{p_to}): "
                  f"複{stats['place_rate']:.1%} 単{stats['win_rate']:.1%} "
                  f"{stats['n']}頭/{stats['n_races']}R "
                  f"| 複ROI{roi['place_roi']:.1%} 単ROI{roi['win_roi']:.1%}")

        if len(period_results) == 3:
            place_rates = [r["place_rate"] for r in period_results]
            roi_places  = [r["place_roi"] for r in period_results]
            spread = max(place_rates) - min(place_rates)
            roi_spread = max(roi_places) - min(roi_places)
            print(f"  >>> 的中率ばらつき: {spread:.1%} "
                  f"[{'[安定]' if spread <= 0.15 else '[不安定]'}]")
            print(f"      複ROIばらつき: {roi_spread:.1%} "
                  f"[{'[安定]' if roi_spread <= 0.30 else '[不安定]'}]")

            # 目標達成判定
            ok_place = [r for r in period_results if r["place_rate"] >= 0.60]
            ok_roi   = [r for r in period_results if r["place_roi"] >= 1.00]
            print(f"      複60%達成: {len(ok_place)}/3期間 / 複ROI100%達成: {len(ok_roi)}/3期間")
            if len(ok_place) < 3 or len(ok_roi) < 3:
                print(f"      [!]  一部期間で目標未達 → ノイズ混入の可能性あり")
            else:
                print(f"      [OK] 全期間で目標達成 → 安定パターンと判断")
        elif len(period_results) < 2:
            print("  [!]  期間データ不足のため安定性評価不可")


# ─────────────────────────────────────────────────────────────────────────
# Step2: アブレーション（条件除外テスト）
# ─────────────────────────────────────────────────────────────────────────

def _step2_ablation(df: pd.DataFrame, payout_map: dict,
                    from_date: date, to_date: date) -> None:
    print()
    print("=" * 70)
    print("Step2: アブレーション（ダート中距離|全体 66.4%パターン）")
    print("=" * 70)

    pat = PATTERNS["P1_dirt_mid_all"]
    surf, d_lo, d_hi = pat["segment"]
    conds = pat["conds"]

    seg_m = _seg_mask(df, surf, d_lo, d_hi, from_date, to_date)
    sub_all = df[seg_m]

    # ベース（全条件AND）
    matched_full = sub_all[_match_series(sub_all, conds)]
    stats_full = _calc_stats_df(matched_full)
    roi_full = _calc_roi_df(matched_full, payout_map)
    print(f"\n  【フルパターン】{' + '.join(conds)}")
    print(f"  複{stats_full['place_rate']:.1%} 単{stats_full['win_rate']:.1%} "
          f"{stats_full['n']}頭/{stats_full['n_races']}R "
          f"| 複ROI{roi_full['place_roi']:.1%} 単ROI{roi_full['win_roi']:.1%}")

    print(f"\n  【各条件を1つ除外した場合】（条件→除いた条件）")
    print(f"  {'除外条件':<20s} {'複勝率':>8} {'変化':>7} {'頭数':>7} {'複ROI':>8}")
    print(f"  {'-'*55}")

    for i, c_remove in enumerate(conds):
        remaining = [c for j, c in enumerate(conds) if j != i]
        matched_sub = sub_all[_match_series(sub_all, remaining)]
        stats_sub = _calc_stats_df(matched_sub)
        roi_sub = _calc_roi_df(matched_sub, payout_map)
        if stats_sub["n"] == 0:
            continue
        delta = stats_sub["place_rate"] - stats_full["place_rate"]
        print(f"  -{c_remove:<19s} {stats_sub['place_rate']:>7.1%} {delta:>+7.1%} "
              f"{stats_sub['n']:>6,}頭 {roi_sub['place_roi']:>7.1%}")

    # 条件ごとの単独貢献度（その条件だけで絞り込む）
    print(f"\n  【各条件の単独評価】（その1条件のみ）")
    print(f"  {'条件':<20s} {'複勝率':>8} {'vs自然':>7} {'頭数':>7}")
    nat = (sub_all["confirmed_rank"] <= 3).mean()
    print(f"  {'[自然率]':<20s} {nat:>7.1%}")
    for c in conds:
        m_single = sub_all[sub_all[f"cond_{c}"] == 1.0]
        s = _calc_stats_df(m_single)
        if s["n"] == 0:
            continue
        delta = s["place_rate"] - nat
        print(f"  {c:<20s} {s['place_rate']:>7.1%} {delta:>+7.1%} {s['n']:>6,}頭")


# ─────────────────────────────────────────────────────────────────────────
# Step3: 実運用シミュレーション（直近10日）
# ─────────────────────────────────────────────────────────────────────────

def _step3_simulation(df: pd.DataFrame, payout_map: dict,
                      from_date: date, to_date: date) -> None:
    print()
    print("=" * 70)
    print("Step3: 実運用シミュレーション（直近10日間 / ダート中距離|全体 パターン）")
    print("=" * 70)

    # 直近10日間
    sim_end   = to_date
    sim_start = to_date - timedelta(days=9)
    # 実際にデータがある最終日を使う
    avail_dates = sorted(df[
        (df["date"].dt.date >= sim_start) & (df["date"].dt.date <= sim_end)
        & (df["surface"] == "ダート") & (df["distance"] >= 1401)
    ]["date"].dt.date.unique())

    if not avail_dates:
        print(f"  [!] {sim_start}~{sim_end} にダート中距離レースなし")
        return

    sim_start = avail_dates[0]
    print(f"  シミュレーション期間: {sim_start} ~ {sim_end} "
          f"（{len(avail_dates)}日分 / {avail_dates[0]}~{avail_dates[-1]}）")

    pat = PATTERNS["P1_dirt_mid_all"]
    surf, d_lo, d_hi = pat["segment"]
    conds = pat["conds"]

    seg_m = _seg_mask(df, surf, d_lo, d_hi, sim_start, sim_end)
    sub = df[seg_m]
    matched = sub[_match_series(sub, conds)].copy()
    matched["date_only"] = matched["date"].dt.date
    matched = matched.sort_values(["date_only", "race_id", "umaban"])

    total = len(matched)
    place_cnt = int((matched["confirmed_rank"] <= 3).sum())
    win_cnt   = int((matched["confirmed_rank"] == 1).sum())
    print(f"  直近10日 合計: {total}頭 / 複勝{place_cnt}頭({place_cnt/total:.1%}) "
          f"/ 単勝{win_cnt}頭({win_cnt/total:.1%})")

    COND_LABELS = {
        "class_ok":    "クラス維持/降級",
        "interval_ok": "間隔15-28日",
        "surface_ok":  "同馬場好走歴",
        "f3_top":      "前走上がり上位33%",
        "sire_venue":  "種牡馬同会場適性",
    }

    def _reason(row: pd.Series) -> str:
        parts = []
        for c in conds:
            v = row.get(f"cond_{c}")
            lbl = COND_LABELS.get(c, c)
            if v == 1.0:
                parts.append(f"OK:{lbl}")
        return " / ".join(parts)

    print()
    prev_date = None
    for _, row in matched.iterrows():
        d = row["date_only"]
        if d != prev_date:
            print(f"\n  >> {d} ({_VENUE_NAME.get(str(row['place_code']).zfill(2), row['place_code'])})")
            prev_date = d

        rank = int(row["confirmed_rank"]) if pd.notna(row["confirmed_rank"]) else "?"
        hit  = "HIT:複" if rank != "?" and rank <= 3 else ("HIT:単" if rank == 1 else "MISS")
        if rank != "?" and rank == 1:
            hit = "HIT:単複"
        elif rank != "?" and rank <= 3:
            hit = "HIT:複"
        else:
            hit = "MISS"

        horse  = row.get("horse_name", f"id:{row['horse_id']}")
        jockey = row.get("jockey_name", "?")
        ninki  = int(row["popularity"]) if pd.notna(row["popularity"]) else "?"
        umaban = int(row["umaban"]) if pd.notna(row["umaban"]) else "?"
        reason = _reason(row)

        rpm = payout_map.get(row["race_id"], {})
        combo = _combo_str(int(row["umaban"])) if pd.notna(row["umaban"]) else None
        place_pay = rpm.get("fukusho", {}).get(combo) if combo else None
        win_pay   = rpm.get("tansho",  {}).get(combo) if combo else None

        pay_str = ""
        if place_pay:
            pay_str += f" 複払戻{place_pay}円"
        if win_pay:
            pay_str += f" 単払戻{win_pay}円"

        print(f"    {hit} {horse}({umaban}番) {ninki}人気 / 着順:{rank} / {jockey}")
        print(f"       理由: {reason}{pay_str}")

    # 日別サマリー
    print()
    print("  【日別サマリー】")
    print(f"  {'日付':<12} {'該当頭数':>8} {'複勝':>6} {'的中率':>8}")
    for d in avail_dates:
        day_sub = matched[matched["date_only"] == d]
        n_d = len(day_sub)
        p_d = int((day_sub["confirmed_rank"] <= 3).sum())
        if n_d > 0:
            print(f"  {str(d):<12} {n_d:>8} {p_d:>6} {p_d/n_d:>8.1%}")
        else:
            print(f"  {str(d):<12}    (該当なし)")


# ─────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────

def _parse_date(s: str) -> date:
    return date.today() if s == "today" else date.fromisoformat(s)


def _cli() -> None:
    parser = argparse.ArgumentParser(description="Phase2 最終検証")
    parser.add_argument("--from-date", default="2025-06-27")
    parser.add_argument("--to-date",   default="today")
    args = parser.parse_args()

    from_date = _parse_date(args.from_date)
    to_date   = _parse_date(args.to_date)

    print(f"[val] ===== Phase2 最終検証 =====")
    print(f"[val] 期間: {from_date} ~ {to_date}")

    load_start = from_date - timedelta(days=_LOOKBACK_DAYS)
    df = _load_extended(load_start, to_date)
    print("[val] 特徴量計算中...")
    df = _build_features(df)
    print(f"[val] 完了: {len(df):,}行")

    target_rids = df[
        (df["date"].dt.date >= from_date) & (df["date"].dt.date <= to_date)
    ]["race_id"].unique().tolist()
    print(f"[val] payoutsロード ({len(target_rids)}レース)...")
    payout_map = _fetch_payouts_bulk(target_rids)

    _step1_stability(df, payout_map, from_date, to_date)
    _step2_ablation(df, payout_map, from_date, to_date)
    _step3_simulation(df, payout_map, from_date, to_date)

    print()
    print("=" * 70)
    print("[val] 最終検証 完了")
    print("=" * 70)


if __name__ == "__main__":
    _cli()
