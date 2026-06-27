"""
scripts/run_racecourse_search.py
================================
Phase 2 深掘り: 競馬場特性×セグメント別条件探索

設計思想:
  Phase 1 で「セグメント別に 53% の複勝率」を確認。本スクリプトでは
  競馬場の物理特性（洋芝/野芝・直線長・坂有無・大小回り）をレベル2分割として追加し、
  さらなる絞り込みによる複勝率向上・安定性改善を狙う。

新規条件（4+2）:
  [馬実績系]
  rc_fit        : 今回と同じ競馬場での過去3走以内に3着以内
  turf_type_fit : 同芝種別（洋芝/野芝）での過去3走以内に3着以内
  straight_fit  : 同直線タイプ（長/短）での過去3走以内に3着以内
  hill_fit      : 同坂区分（あり/なし）での過去3走以内に3着以内
  [種牡馬系]
  sire_venue    : 種牡馬の該当競馬場 top3率 > 全体 top3率 (≥10頭)
  sire_surface  : 種牡馬の芝/ダート top3率が今走コースに有利

使用条件一覧 (16 条件):
  Phase1 踏襲 (10): margin / class_ok / jockey_ok / weight_ok / interval_ok /
                    surface_ok / f3_top / sire_surf / sire_dist / heavy_ok
  新規       (6): rc_fit / turf_type_fit / straight_fit / hill_fit / sire_venue / sire_surface

当日レース情報は一切使わない（過去走の情報のみ）。
既存ロジック（engine.py / conditions.py / 既存戦略JSON）は変更しない。

使用例:
  py -3 scripts/run_racecourse_search.py --from-date 2025-06-27 --to-date 2026-06-27
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from collections import defaultdict
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
# 競馬場特性マスタ（tipster/racecourse_features.json 読み込み）
# ─────────────────────────────────────────────────────────────────────────

_RC_JSON = Path(__file__).parent.parent / "tipster" / "racecourse_features.json"
with open(_RC_JSON, encoding="utf-8") as _f:
    _RC_FEAT = {k: v for k, v in json.load(_f).items() if not k.startswith("_")}

# 特性別競馬場セット
_WESTERN_PC    = frozenset(pc for pc, f in _RC_FEAT.items() if f["turf_type"] == "western")
_HILL_PC       = frozenset(pc for pc, f in _RC_FEAT.items() if f["has_hill"])
_LONG_STR_PC   = frozenset(pc for pc, f in _RC_FEAT.items() if f["straight_m"] >= 400)
_LARGE_PC      = frozenset(pc for pc, f in _RC_FEAT.items() if f["course_size"] in ("large", "medium"))
_SMALL_PC      = frozenset(pc for pc, f in _RC_FEAT.items() if f["course_size"] == "small")

# ─────────────────────────────────────────────────────────────────────────
# Level1 セグメント（Phase1 と同じ）
# ─────────────────────────────────────────────────────────────────────────

SEGMENTS_L1 = {
    "芝短距離":     ("芝",    0,    1400),
    "芝マイル":     ("芝",    1401, 1800),
    "芝中距離":     ("芝",    1801, 2200),
    "芝長距離":     ("芝",    2201, 9999),
    "ダート短距離": ("ダート", 0,    1400),
    "ダート中距離": ("ダート", 1401, 9999),
}

# Level2 フィルタ定義（セグメント名が引数に入る）
# None を返すと「適用外」（芝-only フィルタをダートに適用しないなど）
def _l2_filters(seg_name: str) -> dict[str, pd.Series | None]:
    """キー=Level2名、値=DataFrame にあとで適用する place_code マスク関数"""
    is_turf = "芝" in seg_name
    return {
        "洋芝":   ("turf_western",   _WESTERN_PC,  True)  if is_turf else None,
        "野芝":   ("turf_japanese",  _WESTERN_PC,  False) if is_turf else None,
        "長直線": ("straight_long",  _LONG_STR_PC, True),
        "短直線": ("straight_short", _LONG_STR_PC, False),
        "坂あり": ("hill_yes",       _HILL_PC,     True),
        "坂なし": ("hill_no",        _HILL_PC,     False),
        "大回り": ("large",          _SMALL_PC,    False),
        "小回り": ("small",          _SMALL_PC,    True),
    }

# ─────────────────────────────────────────────────────────────────────────
# 条件定義
# ─────────────────────────────────────────────────────────────────────────

CONDS_A = [
    "margin",         # 0
    "class_ok",       # 1
    "jockey_ok",      # 2
    "weight_ok",      # 3
    "interval_ok",    # 4
    "surface_ok",     # 5
    "f3_top",         # 6
    "sire_surf",      # 7
    "sire_dist",      # 8
    "heavy_ok",       # 9
    "rc_fit",         # 10 NEW
    "turf_type_fit",  # 11 NEW
    "straight_fit",   # 12 NEW
    "hill_fit",       # 13 NEW
    "sire_venue",     # 14 NEW
    "sire_surface",   # 15 NEW
]
CONDS_B = CONDS_A + ["dist_ext", "dist_short"]
_N_A = len(CONDS_A)

# ─────────────────────────────────────────────────────────────────────────
# データ読み込み
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
    SELECT DISTINCT ON (sire_id)
           sire_id, top3_rate AS sire_top3_rate,
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
    ORDER BY sire_id, target_date DESC
""")


def _load_extended(load_start: date, to_date: date) -> pd.DataFrame:
    print(f"[rc] ベースデータ読み込み ({load_start} ~ {to_date})...")
    base = _load_bulk_data(load_start, to_date)

    print("[rc] 拡張フィールド読み込み...")
    supp = pd.read_sql(_SUPP_SQL, _engine, params={"start": load_start, "end": to_date})

    print("[rc] 種牡馬特性読み込み...")
    sire_df = pd.read_sql(_SIRE_SQL, _engine)

    df = base.merge(supp, on=["race_id", "horse_id"], how="left")
    df = df.merge(sire_df, on="sire_id", how="left")

    # class_level
    def _cl(row):
        return _class_level_from_codes(
            str(row["grade_code"]) if pd.notna(row["grade_code"]) else None,
            str(row["jyoken_cd_3"]) if pd.notna(row["jyoken_cd_3"]) else None,
        )
    df["class_level"] = df.apply(_cl, axis=1)

    # f3 rank in race
    df["f3_rank"] = df.groupby("race_id")["f3_time"].rank(ascending=True, na_option="keep")
    df["f3_rank_pct"] = df["f3_rank"] / df["field_size"]

    return df


# ─────────────────────────────────────────────────────────────────────────
# 特徴量計算
# ─────────────────────────────────────────────────────────────────────────

def _build_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values(["horse_id", "date"]).reset_index(drop=True)
    g = df.groupby("horse_id", sort=False)

    # past-race shifts（rank / surface / distance / margin / tc / class / f3pct / place_code）
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

    # ─── Phase1 条件 ────────────────────────────────────────────────────

    # 0: margin
    df["cond_margin"] = (df["prev1_margin"] <= 1.0).astype(object)
    df.loc[df["prev1_margin"].isna(), "cond_margin"] = None

    # 1: class_ok
    cl_ok = df["class_level"] <= df["prev1_class"]
    df["cond_class_ok"] = cl_ok.astype(object)
    df.loc[df["prev1_class"].isna(), "cond_class_ok"] = None

    # 2: jockey_ok
    yr_wins = df["jockey_yr_wins_db"] if "jockey_yr_wins_db" in df.columns else pd.Series(np.nan, index=df.index)
    cont = df["jockey_id"] == df["prev_jockey_id"]
    lead = yr_wins >= 30
    df["cond_jockey_ok"] = (cont | lead).astype(object)
    df.loc[df["prev_jockey_id"].isna(), "cond_jockey_ok"] = None

    # 3: weight_ok
    w_ok = df["burden_weight"] <= (df["prev_burden_weight"] + 0.4)
    df["cond_weight_ok"] = w_ok.astype(object)
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

    # ─── NEW: 競馬場適性条件 ────────────────────────────────────────────

    # 10: rc_fit（同競馬場）
    rc_good = pd.Series(False, index=df.index)
    rc_any  = pd.Series(False, index=df.index)
    for i in range(1, 4):
        same_rc = (df[f"prev{i}_place_code"] == df["place_code"]) & df[f"prev{i}_place_code"].notna()
        good    = same_rc & (df[f"prev{i}_rank"] <= 3) & df[f"prev{i}_rank"].notna()
        rc_good |= good
        rc_any  |= same_rc
    df["cond_rc_fit"] = np.where(~rc_any, None, rc_good.astype(float))

    # 11: turf_type_fit（洋芝/野芝）
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

    # 12: straight_fit（長直線/短直線）
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

    # 13: hill_fit（坂あり/なし）
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

    # 14: sire_venue（種牡馬の今走会場 top3率 > 全体 top3率、最低10頭）
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

    # 15: sire_surface（種牡馬の芝/ダート surface top3率が今走と合致）
    s_turf = df.get("sire_sfs_turf_top3", pd.Series(np.nan, index=df.index))
    s_dirt = df.get("sire_sfs_dirt_top3", pd.Series(np.nan, index=df.index))
    turf_ok = df["surface"] == "芝"
    sire_surf_fit = np.where(turf_ok, s_turf >= s_dirt + 0.01, s_dirt >= s_turf + 0.01)
    df["cond_sire_surface"] = sire_surf_fit.astype(object)
    no_sire_s = s_turf.isna() | s_dirt.isna()
    df.loc[no_sire_s, "cond_sire_surface"] = None

    # PatternB: 距離変化条件
    df["cond_dist_ext"]   = (df["distance"] > df["prev1_distance"]).astype(object)
    df["cond_dist_short"] = (df["distance"] < df["prev1_distance"]).astype(object)
    df.loc[df["prev1_distance"].isna(), ["cond_dist_ext", "cond_dist_short"]] = None

    return df


# ─────────────────────────────────────────────────────────────────────────
# セグメントフィルタ
# ─────────────────────────────────────────────────────────────────────────

def _l1_mask(df: pd.DataFrame, seg: str, from_date: date, to_date: date) -> pd.Series:
    surf, d_lo, d_hi = SEGMENTS_L1[seg]
    return (
        (df["surface"] == surf)
        & (df["distance"] >= d_lo)
        & (df["distance"] <= d_hi)
        & (df["date"].dt.date >= from_date)
        & (df["date"].dt.date <= to_date)
    )


def _apply_l2(df: pd.DataFrame, seg: str, l2_name: str) -> pd.Series | None:
    """Level2 フィルタを df['place_code'] に適用し boolean Series を返す。"""
    specs = _l2_filters(seg)
    spec = specs.get(l2_name)
    if spec is None:
        return None
    _, pc_set, include = spec
    if include:
        return df["place_code"].isin(pc_set)
    else:
        return ~df["place_code"].isin(pc_set)


# ─────────────────────────────────────────────────────────────────────────
# コンボ計算
# ─────────────────────────────────────────────────────────────────────────

_COND_COLS_A = [f"cond_{c}" for c in CONDS_A]


def _match_mask(df: pd.DataFrame, combo_indices: tuple, cond_names: list) -> pd.Series:
    cols = [f"cond_{cond_names[i]}" for i in combo_indices]
    match = pd.Series(True, index=df.index)
    for col in cols:
        match = match & (df[col] == 1.0)
    return match


def _calc_stats(df: pd.DataFrame, combo_indices: tuple, cond_names: list,
                mask: pd.Series, min_ninki: int | None = None) -> dict | None:
    sub = df[mask]
    if min_ninki is not None:
        sub = sub[sub["popularity"].notna() & (sub["popularity"] >= min_ninki)]
    m = _match_mask(sub, combo_indices, cond_names)
    matched = sub[m]
    n = len(matched)
    if n == 0:
        return None
    place = (matched["confirmed_rank"] <= 3).sum()
    win   = (matched["confirmed_rank"] == 1).sum()
    return {
        "n": n,
        "place": int(place),
        "win":   int(win),
        "place_rate": place / n,
        "win_rate":   win   / n,
        "race_count": matched["race_id"].nunique(),
        "avg_per_day": n / 365,
    }


def _calc_roi(df: pd.DataFrame, combo_indices: tuple, cond_names: list,
              mask: pd.Series, payout_map: dict, min_ninki: int | None = None) -> dict:
    sub = df[mask]
    if min_ninki is not None:
        sub = sub[sub["popularity"].notna() & (sub["popularity"] >= min_ninki)]
    m = _match_mask(sub, combo_indices, cond_names)
    matched = sub[m]
    place_ret = win_ret = 0
    n = na = 0
    for _, row in matched.iterrows():
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


def _search_combos(df: pd.DataFrame, mask: pd.Series, cond_names: list,
                   min_n: int = 50, combo_sizes: tuple = (4, 5),
                   min_ninki: int | None = None) -> list[dict]:
    results = []
    for k in combo_sizes:
        for combo in itertools.combinations(range(len(cond_names)), k):
            stats = _calc_stats(df, combo, cond_names, mask, min_ninki)
            if stats is None or stats["n"] < min_n:
                continue
            label = "+".join(cond_names[i] for i in combo)
            results.append({"combo": combo, "n_conds": k, "label": label, **stats})
    results.sort(key=lambda x: (-x["place_rate"], -x["n"]))
    return results


def _stability(df: pd.DataFrame, combo: tuple, cond_names: list,
               mask_base: pd.Series, from_date: date, to_date: date,
               min_ninki: int | None = None) -> list[dict]:
    """4ヶ月×3期間の安定性チェック"""
    total = (to_date - from_date).days
    pd_d = total // 3
    periods = [
        (from_date,                            from_date + timedelta(days=pd_d - 1),   "P1"),
        (from_date + timedelta(days=pd_d),     from_date + timedelta(days=pd_d*2 - 1), "P2"),
        (from_date + timedelta(days=pd_d*2),   to_date,                                "P3"),
    ]
    out = []
    for p_from, p_to, lbl in periods:
        period_mask = mask_base & (df["date"].dt.date >= p_from) & (df["date"].dt.date <= p_to)
        s = _calc_stats(df, combo, cond_names, period_mask, min_ninki)
        if s and s["n"] >= 10:
            out.append({"lbl": lbl, "from": p_from, "to": p_to, **s})
    return out


# ─────────────────────────────────────────────────────────────────────────
# 出力ヘルパー
# ─────────────────────────────────────────────────────────────────────────

def _fmt(r: dict) -> str:
    return (
        f"N={r['n_conds']} 複{r['place_rate']:.1%} 単{r['win_rate']:.1%} "
        f"{r['n']:,}頭/{r['race_count']:,}R 日均{r['avg_per_day']:.2f} | {r['label']}"
    )


# ─────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────

def _parse_date(s: str) -> date:
    return date.today() if s == "today" else date.fromisoformat(s)


def _cli() -> None:
    parser = argparse.ArgumentParser(description="競馬場特性セグメント別条件探索")
    parser.add_argument("--from-date", default="2025-06-27")
    parser.add_argument("--to-date",   default="today")
    parser.add_argument("--min-n",     type=int, default=100, help="最低頭数")
    parser.add_argument("--top-n",     type=int, default=5,   help="表示件数")
    parser.add_argument("--ninki-b",   type=int, default=4,   help="機能B人気閾値")
    args = parser.parse_args()

    from_date = _parse_date(args.from_date)
    to_date   = _parse_date(args.to_date)

    print(f"[rc] 期間: {from_date} ~ {to_date}")
    print(f"[rc] 最低サンプル: {args.min_n}頭 / 機能B: {args.ninki_b}番人気以降")
    print(f"[rc] 競馬場分類: 洋芝={sorted(_WESTERN_PC)} 長直線={sorted(_LONG_STR_PC)} "
          f"坂あり={sorted(_HILL_PC)}")

    load_start = from_date - timedelta(days=_LOOKBACK_DAYS)
    df = _load_extended(load_start, to_date)
    print("[rc] 特徴量計算中...")
    df = _build_features(df)
    print(f"[rc] 完了: {len(df):,}行")

    # payout_map
    target_rids = df[
        (df["date"].dt.date >= from_date) & (df["date"].dt.date <= to_date)
    ]["race_id"].unique().tolist()
    print(f"[rc] payoutsロード ({len(target_rids)}レース)...")
    payout_map = _fetch_payouts_bulk(target_rids)

    # ─── 0. 競馬場特性別基礎統計 ────────────────────────────────────────
    print()
    print("=== 競馬場特性別基礎統計 ===")
    for l2_key in ["洋芝", "野芝", "長直線", "短直線", "坂あり", "坂なし"]:
        specs = {
            "洋芝":   _WESTERN_PC,
            "野芝":   frozenset(pc for pc in _RC_FEAT if pc not in _WESTERN_PC),
            "長直線": _LONG_STR_PC,
            "短直線": frozenset(pc for pc in _RC_FEAT if pc not in _LONG_STR_PC),
            "坂あり": _HILL_PC,
            "坂なし": frozenset(pc for pc in _RC_FEAT if pc not in _HILL_PC),
        }
        pc_set = specs[l2_key]
        mask = (
            df["place_code"].isin(pc_set)
            & (df["date"].dt.date >= from_date) & (df["date"].dt.date <= to_date)
        )
        sub = df[mask]
        if len(sub) < 100:
            continue
        pr = (sub["confirmed_rank"] <= 3).mean()
        wr = (sub["confirmed_rank"] == 1).mean()
        print(f"  {l2_key:8s}: {sub['race_id'].nunique():>5}R / {len(sub):>7,}頭 "
              f"| 複自然{pr:.1%} 単自然{wr:.1%}")

    # ─── 1. 新条件単体評価 ──────────────────────────────────────────────
    print()
    print("=== 新条件（rc_fit/turf_type_fit/straight_fit/hill_fit/sire_venue/sire_surface）単体評価 ===")
    new_conds = ["rc_fit", "turf_type_fit", "straight_fit", "hill_fit", "sire_venue", "sire_surface"]
    base_mask = (df["date"].dt.date >= from_date) & (df["date"].dt.date <= to_date)
    for cname in new_conds:
        idx = CONDS_A.index(cname)
        stats = _calc_stats(df, (idx,), CONDS_A, base_mask)
        if stats and stats["n"] >= 50:
            nat = (df[base_mask]["confirmed_rank"] <= 3).mean()
            print(f"  {cname:20s}: 複{stats['place_rate']:.1%} 単{stats['win_rate']:.1%} "
                  f"{stats['n']:,}頭 (自然{nat:.1%})")

    # ─── 2. Level1×Level2 セグメント別コンボ探索 ─────────────────────
    print()
    print("=== Level1 x Level2 セグメント別コンボ探索（PatternA 4-5条件）===")

    all_results: dict[str, list] = {}

    for seg_name in SEGMENTS_L1:
        l1_m = _l1_mask(df, seg_name, from_date, to_date)
        seg_n = l1_m.sum()
        if seg_n < 200:
            print(f"\n[{seg_name}] サンプル不足({seg_n})")
            continue

        nat_pr = (df[l1_m]["confirmed_rank"] <= 3).mean()

        # Level2 フィルタ
        l2_specs = _l2_filters(seg_name)
        sublists = [("全体", l1_m)]
        for l2_name, spec in l2_specs.items():
            if spec is None:
                continue
            l2_m = _apply_l2(df, seg_name, l2_name)
            if l2_m is None:
                continue
            combined = l1_m & l2_m
            n_races = df[combined]["race_id"].nunique()
            if n_races < 50:
                continue
            sublists.append((l2_name, combined))

        print(f"\n--- {seg_name} (自然複勝率{nat_pr:.1%}, {seg_n:,}頭) ---")

        for sub_name, sub_mask in sublists:
            sub_n = sub_mask.sum()
            sub_r = df[sub_mask]["race_id"].nunique()
            if sub_r < 50:
                continue

            combos = _search_combos(df, sub_mask, CONDS_A,
                                    min_n=args.min_n, combo_sizes=(4, 5))
            all_results[f"{seg_name}|{sub_name}"] = combos

            sub_nat = (df[sub_mask]["confirmed_rank"] <= 3).mean()
            print(f"\n  [{sub_name}] {sub_r}R/{sub_n:,}頭 自然{sub_nat:.1%} "
                  f"発見: {len(combos)}パターン")

            if not combos:
                print("  なし")
                continue

            for r in combos[:args.top_n]:
                print(f"  {_fmt(r)}")
                # 安定性
                periods = _stability(df, r["combo"], CONDS_A, sub_mask, from_date, to_date)
                for p in periods:
                    print(f"    {p['lbl']}({p['from']}~{p['to']}): "
                          f"複{p['place_rate']:.1%} {p['n']}頭/{p['race_count']}R")
                if len(periods) == 3:
                    rates = [p["place_rate"] for p in periods]
                    spread = max(rates) - min(rates)
                    print(f"    -> ばらつき {spread:.1%} "
                          f"[{'安定' if spread <= 0.15 else '不安定'}]")
                # ROI
                if r["n"] >= args.min_n:
                    roi = _calc_roi(df, r["combo"], CONDS_A, sub_mask, payout_map)
                    print(f"    ROI: 複{roi['place_roi']:.1%} 単{roi['win_roi']:.1%} "
                          f"({roi['n_roi']}頭 NA={roi['na_roi']})")

    # ─── 3. Phase1 有望パターン深掘り ───────────────────────────────────
    print()
    print("=== Phase1 有望パターン深掘り ===")

    # Pattern①: ダート中距離 margin+class_ok+interval_ok+surface_ok+f3_top (複53.2%/387頭)
    p1_seg = "ダート中距離"
    p1_combo_names = ["margin", "class_ok", "interval_ok", "surface_ok", "f3_top"]
    p1_combo_idx = tuple(CONDS_A.index(c) for c in p1_combo_names)
    p1_mask = _l1_mask(df, p1_seg, from_date, to_date)
    p1_stats = _calc_stats(df, p1_combo_idx, CONDS_A, p1_mask)
    p1_roi   = _calc_roi(df, p1_combo_idx, CONDS_A, p1_mask, payout_map)
    print(f"\n[1] {p1_seg} {'+'.join(p1_combo_names)}")
    if p1_stats:
        print(f"    複{p1_stats['place_rate']:.1%} 単{p1_stats['win_rate']:.1%} "
              f"{p1_stats['n']}頭 | ROI複{p1_roi['place_roi']:.1%} 単{p1_roi['win_roi']:.1%}")
    else:
        print("    データなし")

    # 競馬場条件を追加した場合の比較
    new_rc_conds = ["rc_fit", "turf_type_fit", "straight_fit", "hill_fit", "sire_venue", "sire_surface"]
    print(f"  +競馬場条件を追加した場合:")
    for extra in new_rc_conds:
        extra_idx = CONDS_A.index(extra)
        new_combo = p1_combo_idx + (extra_idx,)
        s = _calc_stats(df, new_combo, CONDS_A, p1_mask)
        if s and s["n"] >= 50:
            roi = _calc_roi(df, new_combo, CONDS_A, p1_mask, payout_map)
            delta = s["place_rate"] - (p1_stats["place_rate"] if p1_stats else 0)
            print(f"    +{extra:20s}: 複{s['place_rate']:.1%} ({delta:+.1%}) "
                  f"{s['n']}頭 | ROI複{roi['place_roi']:.1%}")

    # Level2 セグメント別深掘り
    print(f"  Level2 セグメント別:")
    for l2_name, spec in _l2_filters(p1_seg).items():
        if spec is None:
            continue
        l2_m = _apply_l2(df, p1_seg, l2_name)
        if l2_m is None:
            continue
        combined = p1_mask & l2_m
        s = _calc_stats(df, p1_combo_idx, CONDS_A, combined)
        if s and s["n"] >= 30:
            n_r = df[combined]["race_id"].nunique()
            roi = _calc_roi(df, p1_combo_idx, CONDS_A, combined, payout_map)
            print(f"    {l2_name:8s}: 複{s['place_rate']:.1%} "
                  f"{s['n']}頭/{n_r}R | ROI複{roi['place_roi']:.1%} 単{roi['win_roi']:.1%}")

    # Pattern②: 芝中距離穴馬 margin+weight_ok+surface_ok+sire_surf (単ROI148.2%)
    p2_seg = "芝中距離"
    p2_combo_names = ["margin", "weight_ok", "surface_ok", "sire_surf"]
    p2_combo_idx = tuple(CONDS_A.index(c) for c in p2_combo_names)
    p2_mask = _l1_mask(df, p2_seg, from_date, to_date)
    p2_stats = _calc_stats(df, p2_combo_idx, CONDS_A, p2_mask, min_ninki=args.ninki_b)
    p2_roi   = _calc_roi(df, p2_combo_idx, CONDS_A, p2_mask, payout_map, min_ninki=args.ninki_b)
    print(f"\n[2] {p2_seg} 穴馬({args.ninki_b}番人気以降) {'+'.join(p2_combo_names)}")
    if p2_stats:
        print(f"    複{p2_stats['place_rate']:.1%} 単{p2_stats['win_rate']:.1%} "
              f"{p2_stats['n']}頭 | ROI複{p2_roi['place_roi']:.1%} 単{p2_roi['win_roi']:.1%}")
        # 安定性
        periods = _stability(df, p2_combo_idx, CONDS_A, p2_mask, from_date, to_date,
                             min_ninki=args.ninki_b)
        for p in periods:
            print(f"    {p['lbl']}: 複{p['place_rate']:.1%} {p['n']}頭/{p['race_count']}R")
        if len(periods) == 3:
            rates = [p["place_rate"] for p in periods]
            spread = max(rates) - min(rates)
            print(f"    -> ばらつき {spread:.1%} ['{'安定' if spread <= 0.15 else '不安定'}']")
    else:
        print("    データなし")

    # 追加条件の効果
    print(f"  +競馬場条件を追加した場合(穴馬):")
    for extra in new_rc_conds:
        extra_idx = CONDS_A.index(extra)
        new_combo = p2_combo_idx + (extra_idx,)
        s = _calc_stats(df, new_combo, CONDS_A, p2_mask, min_ninki=args.ninki_b)
        if s and s["n"] >= 30:
            roi = _calc_roi(df, new_combo, CONDS_A, p2_mask, payout_map, min_ninki=args.ninki_b)
            delta = s["place_rate"] - (p2_stats["place_rate"] if p2_stats else 0)
            print(f"    +{extra:20s}: 複{s['place_rate']:.1%} ({delta:+.1%}) "
                  f"{s['n']}頭 | ROI複{roi['place_roi']:.1%} 単{roi['win_roi']:.1%}")

    # Level2 セグメント別深掘り (p2)
    print(f"  Level2 セグメント別(穴馬):")
    for l2_name, spec in _l2_filters(p2_seg).items():
        if spec is None:
            continue
        l2_m = _apply_l2(df, p2_seg, l2_name)
        if l2_m is None:
            continue
        combined = p2_mask & l2_m
        s = _calc_stats(df, p2_combo_idx, CONDS_A, combined, min_ninki=args.ninki_b)
        if s and s["n"] >= 20:
            n_r = df[combined]["race_id"].nunique()
            roi = _calc_roi(df, p2_combo_idx, CONDS_A, combined, payout_map, min_ninki=args.ninki_b)
            print(f"    {l2_name:8s}: 複{s['place_rate']:.1%} "
                  f"{s['n']}頭/{n_r}R | ROI複{roi['place_roi']:.1%} 単{roi['win_roi']:.1%}")

    # ─── 4. 機能B（穴馬）Level2 セグメント ──────────────────────────────
    print()
    print(f"=== 機能B ({args.ninki_b}番人気以降) Level1xLevel2 上位パターン ===")

    for seg_name in SEGMENTS_L1:
        l1_m = _l1_mask(df, seg_name, from_date, to_date)
        if l1_m.sum() < 200:
            continue
        l2_specs = _l2_filters(seg_name)
        sublists_b = [("全体", l1_m)]
        for l2_name, spec in l2_specs.items():
            if spec is None:
                continue
            l2_m = _apply_l2(df, seg_name, l2_name)
            if l2_m is None:
                continue
            combined = l1_m & l2_m
            if df[combined]["race_id"].nunique() < 50:
                continue
            sublists_b.append((l2_name, combined))

        print(f"\n--- {seg_name} 機能B ---")
        for sub_name, sub_mask in sublists_b[:3]:  # 上位3 Level2 のみ
            combos_b = _search_combos(df, sub_mask, CONDS_A,
                                      min_n=50, combo_sizes=(4, 5),
                                      min_ninki=args.ninki_b)
            if not combos_b:
                continue
            sub_nat_b = (df[sub_mask & (df["popularity"] >= args.ninki_b)]["confirmed_rank"] <= 3).mean()
            print(f"  [{sub_name}] 穴馬自然{sub_nat_b:.1%}")
            for r in combos_b[:3]:
                roi = _calc_roi(df, r["combo"], CONDS_A, sub_mask, payout_map,
                                min_ninki=args.ninki_b)
                print(f"    {_fmt(r)} | ROI複{roi['place_roi']:.1%} 単{roi['win_roi']:.1%}")

    # ─── 5. 全体サマリー ─────────────────────────────────────────────────
    print()
    print("=== 全体サマリー（Level1xLevel2 最良パターン）===")
    print(f"{'セグメント':20s} {'複勝率':>8} {'頭数':>7} {'ROI複':>8} 条件")
    print("-" * 90)
    for key, res in sorted(all_results.items(), key=lambda x: -(x[1][0]["place_rate"] if x[1] else 0)):
        if not res:
            continue
        best = res[0]
        roi = _calc_roi(df, best["combo"], CONDS_A,
                        all_results.get(key, [{}]).__class__,  # dummy
                        payout_map)
        # rebuild mask for ROI
        seg, sub = key.split("|", 1)
        l1_m = _l1_mask(df, seg, from_date, to_date)
        if sub == "全体":
            mask_for_roi = l1_m
        else:
            l2_m2 = _apply_l2(df, seg, sub)
            mask_for_roi = l1_m & l2_m2 if l2_m2 is not None else l1_m
        roi = _calc_roi(df, best["combo"], CONDS_A, mask_for_roi, payout_map)
        print(f"{key:20s} {best['place_rate']:>8.1%} {best['n']:>7,} "
              f"{roi['place_roi']:>8.1%} {best['label']}")


if __name__ == "__main__":
    _cli()
