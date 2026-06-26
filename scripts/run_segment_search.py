"""
scripts/run_segment_search.py
================================
Phase 2 セグメント別条件探索。

設計思想:
  競馬はコース種別×距離帯ごとに「効く条件」が異なる。全レース共通条件では
  複勝率 ~37% が天井と判明したため、セグメント別に最適条件を探索する。

対象セグメント（6種）:
  芝短距離 (~1400m) / 芝マイル (1401-1800m) / 芝中距離 (1801-2200m) / 芝長距離 (2201m~)
  ダート短距離 (~1400m) / ダート中距離 (1401m~)

条件:
  過去のレース情報のみ使用。当日のレース情報（馬場状態・オッズ等）は一切使わない。

利用データ（Step0 DB調査で確認済み）:
  - race_entries.f3_time: 上がり3F秒数（カバレッジ100%）
  - races.track_condition: '1'=良/'2'=稍重/'3'=重/'4'=不良 (100%)
  - bloodline_feature_store: sire_turf_wr, sire_dirt_wr, sire_sprint/mile/middle/long_wr,
    sire_heavy_wr (race_id+horse_idで紐付け, 93.1%カバレッジ)
  - jockeys.yr_wins: 騎手年間勝利数 (100%)

既存ロジック（engine.py / conditions.py / 既存戦略JSON）は変更しない。

使用例:
  py -3 scripts/run_segment_search.py --from-date 2025-06-27 --to-date 2026-06-27
"""
from __future__ import annotations

import argparse
import itertools
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).parent.parent))

from ml.db import engine as _engine
from tipster.backtest import (
    _load_bulk_data,
    _LOOKBACK_DAYS,
)
from tipster.combo_backtest import _combo_str, _fetch_payouts_bulk
from tipster.conditions import _class_level_from_codes

# ─────────────────────────────────────────────────────────────────────────
# セグメント定義
# ─────────────────────────────────────────────────────────────────────────

SEGMENTS = {
    '芝短距離':     ('芝',   0,    1400),
    '芝マイル':     ('芝',   1401, 1800),
    '芝中距離':     ('芝',   1801, 2200),
    '芝長距離':     ('芝',   2201, 9999),
    'ダート短距離': ('ダート', 0,   1400),
    'ダート中距離': ('ダート', 1401, 9999),
}

# ─────────────────────────────────────────────────────────────────────────
# 条件定義（短縮名 → 列名のマッピング）
# ─────────────────────────────────────────────────────────────────────────

# PatternA: 変化を考慮しない（値そのもので判定）
CONDS_A = [
    'margin',       # 0: 前走で勝ち馬差1秒以内
    'class_ok',     # 1: クラス降級または同クラス（昇級でない）
    'jockey_ok',    # 2: 継続騎乗またはリーディング騎手
    'weight_ok',    # 3: 斤量増量なし
    'interval_ok',  # 4: 出走間隔15-28日
    'surface_ok',   # 5: 同馬場種別で過去好走歴
    'f3_top',       # 6: 前走上がり3F 上位33%以内
    'sire_surf',    # 7: 種牡馬の芝/ダート適性が今回コースと一致
    'sire_dist',    # 8: 種牡馬の距離適性が今回距離帯と一致
    'heavy_ok',     # 9: 過去3走以内に重馬場（TC=3,4）で好走歴
]

# PatternB: 変化を考慮する条件を追加
CONDS_B = CONDS_A + [
    'dist_ext',     # 10: 距離延長（今回 > 前走距離）
    'dist_short',   # 11: 距離短縮（今回 < 前走距離）
]

_N_A = len(CONDS_A)
_N_B = len(CONDS_B)

# ─────────────────────────────────────────────────────────────────────────
# データ読み込み
# ─────────────────────────────────────────────────────────────────────────

_SUPP_SQL = text("""
    SELECT e.race_id, e.horse_id,
           e.f3_time, e.popularity,
           r.track_condition,
           b.sire_turf_wr, b.sire_dirt_wr,
           b.sire_sprint_wr, b.sire_mile_wr, b.sire_middle_wr, b.sire_long_wr,
           b.sire_heavy_wr,
           j.yr_wins AS jockey_yr_wins_db
    FROM race_entries e
    JOIN races r ON e.race_id = r.id
    LEFT JOIN bloodline_feature_store b
           ON b.horse_id = e.horse_id AND b.race_id = e.race_id
    LEFT JOIN jockeys j ON j.id = e.jockey_id
    WHERE r.date BETWEEN :start AND :end
      AND e.confirmed_rank IS NOT NULL AND e.confirmed_rank > 0
      AND r.course_type IN ('芝','ダート')
      AND r.place_code <= '10'
""")


def _load_extended(load_start: date, to_date: date) -> pd.DataFrame:
    """base + 拡張フィールドを結合した DataFrame を返す。"""
    print(f"[segment] ベースデータ読み込み ({load_start} ~ {to_date})...")
    base = _load_bulk_data(load_start, to_date)

    print("[segment] 拡張フィールド(f3_time/track_condition/bloodline/jockey)読み込み...")
    supp = pd.read_sql(_SUPP_SQL, _engine, params={"start": load_start, "end": to_date})

    df = base.merge(supp, on=["race_id", "horse_id"], how="left")

    # class_level（既存 conditions.py の関数を使用）
    def _cl(row):
        return _class_level_from_codes(
            str(row["grade_code"]) if pd.notna(row["grade_code"]) else None,
            str(row["jyoken_cd_3"]) if pd.notna(row["jyoken_cd_3"]) else None,
        )
    df["class_level"] = df.apply(_cl, axis=1)

    # f3 レース内ランク（低いほど速い＝上位）
    df["f3_rank"] = df.groupby("race_id")["f3_time"].rank(ascending=True, na_option="keep")
    df["f3_rank_pct"] = df["f3_rank"] / df["field_size"]

    return df


def _build_features(df: pd.DataFrame) -> pd.DataFrame:
    """過去走シフト特徴量と条件 boolean 列を一括計算する。"""
    df = df.sort_values(["horse_id", "date"]).reset_index(drop=True)
    g = df.groupby("horse_id", sort=False)

    # ─── past-race shifts ───────────────────────────────────────────────
    for i in range(1, 4):
        df[f"prev{i}_rank"]      = g["confirmed_rank"].shift(i)
        df[f"prev{i}_surface"]   = g["surface"].shift(i)
        df[f"prev{i}_distance"]  = g["distance"].shift(i)
        df[f"prev{i}_margin"]    = g["this_margin"].shift(i)
        df[f"prev{i}_tc"]        = g["track_condition"].shift(i)
        df[f"prev{i}_class"]     = g["class_level"].shift(i)
        df[f"prev{i}_f3pct"]     = g["f3_rank_pct"].shift(i)

    # 出走間隔（日）
    df["days_since_prev"] = (df["date"] - df["prev_race_date"]).dt.days

    # ─── CONDITION 0: margin ─────────────────────────────────────────────
    df["cond_margin"] = (df["prev1_margin"] <= 1.0).astype(object)
    df.loc[df["prev1_margin"].isna(), "cond_margin"] = None

    # ─── CONDITION 1: class_ok (降級または同クラス)──────────────────────
    cl_ok = df["class_level"] <= df["prev1_class"]
    df["cond_class_ok"] = cl_ok.astype(object)
    df.loc[df["prev1_class"].isna(), "cond_class_ok"] = None

    # ─── CONDITION 2: jockey_ok (継続 or リーディング) ──────────────────
    yr_wins = df["jockey_yr_wins_db"]
    cont = df["jockey_id"] == df["prev_jockey_id"]
    lead = yr_wins >= 30
    jok = cont | lead
    df["cond_jockey_ok"] = jok.astype(object)
    df.loc[df["prev_jockey_id"].isna(), "cond_jockey_ok"] = None

    # ─── CONDITION 3: weight_ok (斤量増量なし) ───────────────────────────
    w_ok = df["burden_weight"] <= (df["prev_burden_weight"] + 0.4)
    df["cond_weight_ok"] = w_ok.astype(object)
    df.loc[df["prev_burden_weight"].isna(), "cond_weight_ok"] = None

    # ─── CONDITION 4: interval_ok (15-28日) ─────────────────────────────
    iv = df["days_since_prev"]
    df["cond_interval_ok"] = ((iv >= 15) & (iv <= 28)).astype(object)
    df.loc[iv.isna(), "cond_interval_ok"] = None

    # ─── CONDITION 5: surface_ok (同馬場で過去3走以内に3着以内) ────────
    surf_good = pd.Series(False, index=df.index)
    surf_any  = pd.Series(False, index=df.index)
    for i in range(1, 4):
        same = (df[f"prev{i}_surface"] == df["surface"]) & df[f"prev{i}_surface"].notna()
        good = same & (df[f"prev{i}_rank"] <= 3) & df[f"prev{i}_rank"].notna()
        surf_good |= good
        surf_any  |= same
    df["cond_surface_ok"] = np.where(~surf_any, None, surf_good.astype(float))

    # ─── CONDITION 6: f3_top (前走上がり3F 上位33%) ─────────────────────
    df["cond_f3_top"] = (df["prev1_f3pct"] <= 0.33).astype(object)
    df.loc[df["prev1_f3pct"].isna(), "cond_f3_top"] = None

    # ─── CONDITION 7: sire_surf (種牡馬コース適性) ───────────────────────
    # 芝: sire_turf_wr > sire_dirt_wr + 0.02 / ダート: sire_dirt_wr > sire_turf_wr + 0.02
    SIRE_THRESH = 0.02
    turf_fit  = (df["sire_turf_wr"] >= df["sire_dirt_wr"] + SIRE_THRESH)
    dirt_fit  = (df["sire_dirt_wr"] >= df["sire_turf_wr"] + SIRE_THRESH)
    sire_surf = np.where(df["surface"] == "芝", turf_fit, dirt_fit)
    df["cond_sire_surf"] = sire_surf.astype(object)
    df.loc[df["sire_turf_wr"].isna(), "cond_sire_surf"] = None

    # ─── CONDITION 8: sire_dist (種牡馬距離適性) ─────────────────────────
    # 今回距離帯の種牡馬勝率 >= 全距離帯平均
    dist_cols = ["sire_sprint_wr", "sire_mile_wr", "sire_middle_wr", "sire_long_wr"]
    df["_sire_dist_avg"] = df[dist_cols].mean(axis=1)
    cur_sire_wr = pd.Series(np.nan, index=df.index)
    cur_sire_wr = cur_sire_wr.where(df["distance"] > 1400, df["sire_sprint_wr"])
    cur_sire_wr = cur_sire_wr.where(
        ~((df["distance"] > 1400) & (df["distance"] <= 1800)), df["sire_mile_wr"]
    )
    cur_sire_wr = cur_sire_wr.where(
        ~((df["distance"] > 1800) & (df["distance"] <= 2200)), df["sire_middle_wr"]
    )
    cur_sire_wr = cur_sire_wr.where(df["distance"] <= 2200, df["sire_long_wr"])
    df["cond_sire_dist"] = (cur_sire_wr >= df["_sire_dist_avg"]).astype(object)
    df.loc[cur_sire_wr.isna(), "cond_sire_dist"] = None

    # ─── CONDITION 9: heavy_ok (過去3走以内に重馬場 TC=3,4 で好走) ─────
    heavy_good = pd.Series(False, index=df.index)
    heavy_any  = pd.Series(False, index=df.index)
    for i in range(1, 4):
        is_heavy = df[f"prev{i}_tc"].isin(["3", "4"]) & df[f"prev{i}_tc"].notna()
        good = is_heavy & (df[f"prev{i}_rank"] <= 3) & df[f"prev{i}_rank"].notna()
        heavy_good |= good
        heavy_any  |= is_heavy
    df["cond_heavy_ok"] = np.where(~heavy_any, None, heavy_good.astype(float))

    # ─── CONDITION 10: dist_ext (距離延長) ───────────────────────────────
    df["cond_dist_ext"] = (df["distance"] > df["prev1_distance"]).astype(object)
    df.loc[df["prev1_distance"].isna(), "cond_dist_ext"] = None

    # ─── CONDITION 11: dist_short (距離短縮) ─────────────────────────────
    df["cond_dist_short"] = (df["distance"] < df["prev1_distance"]).astype(object)
    df.loc[df["prev1_distance"].isna(), "cond_dist_short"] = None

    return df


# ─────────────────────────────────────────────────────────────────────────
# セグメントフィルタリング
# ─────────────────────────────────────────────────────────────────────────

def _seg_mask(df: pd.DataFrame, seg: str, from_date: date, to_date: date) -> pd.Series:
    surf, d_lo, d_hi = SEGMENTS[seg]
    return (
        (df["surface"] == surf)
        & (df["distance"] >= d_lo)
        & (df["distance"] <= d_hi)
        & (df["date"].dt.date >= from_date)
        & (df["date"].dt.date <= to_date)
    )


# ─────────────────────────────────────────────────────────────────────────
# 条件マッチング（vectorized）
# ─────────────────────────────────────────────────────────────────────────

_COND_COLS_A = [f"cond_{c}" for c in CONDS_A]
_COND_COLS_B = [f"cond_{c}" for c in CONDS_B]


def _match_mask(df: pd.DataFrame, combo_indices: tuple, cond_names: list) -> pd.Series:
    """指定条件インデックスのAND条件に全て True で合致する行マスク。
    None(不明)はFalseとして扱う（厳格AND）。
    """
    cols = [f"cond_{cond_names[i]}" for i in combo_indices]
    match = pd.Series(True, index=df.index)
    for col in cols:
        match = match & (df[col] == 1.0)  # True=1.0, False=0.0, None=NaN
    return match


def _calc_hit_rate(
    df: pd.DataFrame,
    combo_indices: tuple,
    cond_names: list,
    seg: str,
    from_date: date,
    to_date: date,
    min_ninki: int | None = None,
) -> dict | None:
    seg_m = _seg_mask(df, seg, from_date, to_date)
    sub = df[seg_m]
    if min_ninki is not None:
        sub = sub[sub["popularity"].notna() & (sub["popularity"] >= min_ninki)]

    match = _match_mask(sub, combo_indices, cond_names)
    matched = sub[match]
    n = len(matched)
    if n == 0:
        return None

    place = (matched["confirmed_rank"] <= 3).sum()
    win   = (matched["confirmed_rank"] == 1).sum()

    days_total = (to_date - from_date).days + 1
    date_cnt = matched["date"].dt.date.value_counts()

    return {
        "n": n,
        "place": int(place),
        "win": int(win),
        "place_rate": place / n,
        "win_rate": win / n,
        "race_count": matched["race_id"].nunique(),
        "avg_per_day": n / days_total,
        "days_0": days_total - len(date_cnt),
        "days_1": int((date_cnt == 1).sum()),
        "days_2_3": int(((date_cnt >= 2) & (date_cnt <= 3)).sum()),
        "days_4plus": int((date_cnt >= 4).sum()),
    }


# ─────────────────────────────────────────────────────────────────────────
# 回収率計算（payoutsテーブル利用）
# ─────────────────────────────────────────────────────────────────────────

def _calc_roi(
    df: pd.DataFrame,
    combo_indices: tuple,
    cond_names: list,
    seg: str,
    from_date: date,
    to_date: date,
    payout_map: dict,
    min_ninki: int | None = None,
) -> dict:
    seg_m = _seg_mask(df, seg, from_date, to_date)
    sub = df[seg_m]
    if min_ninki is not None:
        sub = sub[sub["popularity"].notna() & (sub["popularity"] >= min_ninki)]
    match = _match_mask(sub, combo_indices, cond_names)
    matched = sub[match]

    place_ret = 0
    win_ret = 0
    n = 0
    na = 0
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
        "n_roi": n,
        "na_roi": na,
        "place_roi": place_ret / (n * 100) if n else 0.0,
        "win_roi": win_ret / (n * 100) if n else 0.0,
    }


# ─────────────────────────────────────────────────────────────────────────
# コンボ探索
# ─────────────────────────────────────────────────────────────────────────

def _search_combos(
    df: pd.DataFrame,
    seg: str,
    from_date: date,
    to_date: date,
    cond_names: list,
    min_n: int = 50,
    combo_sizes: tuple = (3, 4, 5),
    min_ninki: int | None = None,
) -> list[dict]:
    results = []
    for k in combo_sizes:
        for combo in itertools.combinations(range(len(cond_names)), k):
            stats = _calc_hit_rate(df, combo, cond_names, seg, from_date, to_date, min_ninki)
            if stats is None or stats["n"] < min_n:
                continue
            label = "+".join(cond_names[i] for i in combo)
            results.append({"combo": combo, "n_conds": k, "label": label, **stats})
    results.sort(key=lambda x: (-x["place_rate"], -x["n"]))
    return results


# ─────────────────────────────────────────────────────────────────────────
# 安定性確認（4ヶ月×3期間）
# ─────────────────────────────────────────────────────────────────────────

def _stability_periods(from_date: date, to_date: date) -> list[tuple]:
    total = (to_date - from_date).days
    pd_days = total // 3
    return [
        (from_date, from_date + timedelta(days=pd_days - 1), "P1"),
        (from_date + timedelta(days=pd_days), from_date + timedelta(days=pd_days * 2 - 1), "P2"),
        (from_date + timedelta(days=pd_days * 2), to_date, "P3"),
    ]


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
    parser = argparse.ArgumentParser(description="セグメント別条件探索")
    parser.add_argument("--from-date", default="2025-06-27")
    parser.add_argument("--to-date", default="today")
    parser.add_argument("--min-n", type=int, default=100, help="最低頭数（ROI含む詳細分析の閾値）")
    parser.add_argument("--top-n", type=int, default=5, help="セグメントごとの詳細出力件数")
    parser.add_argument("--ninki-b", type=int, default=4, help="機能B 人気閾値")
    args = parser.parse_args()

    from_date = _parse_date(args.from_date)
    to_date   = _parse_date(args.to_date)

    print(f"[segment] 期間: {from_date} ~ {to_date}")
    print(f"[segment] 最低サンプル: {args.min_n}頭 / 機能B: {args.ninki_b}番人気以降")

    load_start = from_date - timedelta(days=_LOOKBACK_DAYS)
    df = _load_extended(load_start, to_date)
    print("[segment] 特徴量計算中...")
    df = _build_features(df)
    print(f"[segment] 計算完了: {len(df):,}行")

    # payout_map は詳細ROI計算用
    target_rids = df[
        (df["date"].dt.date >= from_date) & (df["date"].dt.date <= to_date)
    ]["race_id"].unique().tolist()
    print(f"[segment] payoutsロード中... ({len(target_rids)}レース)")
    payout_map = _fetch_payouts_bulk(target_rids)

    # ─── セグメント別基礎統計 ────────────────────────────────────────────
    print()
    print("=== セグメント別基礎統計（自然複勝率・自然単勝率）===")
    print(f"{'セグメント':12s} {'レース数':>8} {'出走頭数':>9} {'複勝自然率':>10} {'単勝自然率':>10}")
    print("-" * 60)

    seg_base: dict[str, pd.DataFrame] = {}
    for seg_name in SEGMENTS:
        mask = _seg_mask(df, seg_name, from_date, to_date)
        seg_df = df[mask]
        seg_base[seg_name] = seg_df
        n = len(seg_df)
        if n == 0:
            continue
        races = seg_df["race_id"].nunique()
        place_natural = (seg_df["confirmed_rank"] <= 3).mean()
        win_natural   = (seg_df["confirmed_rank"] == 1).mean()
        print(f"{seg_name:12s} {races:>8,} {n:>9,} {place_natural:>10.1%} {win_natural:>10.1%}")

    # ─── 単一条件評価 ────────────────────────────────────────────────────
    print()
    print("=== 単一条件評価（セグメント×条件、複勝率降順トップ3表示）===")
    for seg_name, seg_df in seg_base.items():
        if len(seg_df) < 200:
            continue
        print(f"\n--- {seg_name} ---")
        single_results = []
        for i, cname in enumerate(CONDS_A):
            stats = _calc_hit_rate(df, (i,), CONDS_A, seg_name, from_date, to_date)
            if stats and stats["n"] >= 50:
                single_results.append({"cond": cname, **stats})
        single_results.sort(key=lambda x: -x["place_rate"])
        for r in single_results[:5]:
            print(f"  {r['cond']:15s} 複{r['place_rate']:.1%} 単{r['win_rate']:.1%} {r['n']:,}頭")

    # ─── PatternA: 3-5条件コンボ探索 ────────────────────────────────────
    print()
    print("=== PatternA（変化考慮なし）3-5条件コンボ探索 ===")

    all_seg_results_a: dict[str, list] = {}
    stability_periods = _stability_periods(from_date, to_date)
    print(f"  安定性期間: P1={stability_periods[0][0]}~{stability_periods[0][1]}, "
          f"P2={stability_periods[1][0]}~{stability_periods[1][1]}, "
          f"P3={stability_periods[2][0]}~{stability_periods[2][1]}")

    for seg_name in SEGMENTS:
        if len(seg_base.get(seg_name, pd.DataFrame())) < 200:
            print(f"\n[{seg_name}] サンプル不足のためスキップ")
            continue

        print(f"\n--- {seg_name} PatternA ---")
        combos = _search_combos(df, seg_name, from_date, to_date, CONDS_A,
                                min_n=args.min_n, combo_sizes=(3, 4, 5))
        all_seg_results_a[seg_name] = combos

        # 自然複勝率
        seg_all = seg_base[seg_name]
        natural_pr = (seg_all["confirmed_rank"] <= 3).mean()

        print(f"  自然複勝率: {natural_pr:.1%} / 発見パターン: {len(combos)}件 (閾値{args.min_n}頭+)")

        if not combos:
            print("  なし")
            continue

        # 上位 top_n 表示 + 安定性 + ROI
        for r in combos[:args.top_n]:
            print(f"  {_fmt(r)}")

            # 安定性チェック
            period_rates = []
            for p_from, p_to, p_lbl in stability_periods:
                ps = _calc_hit_rate(df, r["combo"], CONDS_A, seg_name, p_from, p_to)
                if ps and ps["n"] >= 10:
                    print(f"    {p_lbl}({p_from}~{p_to}): 複{ps['place_rate']:.1%} {ps['n']}頭/{ps['race_count']}R")
                    period_rates.append(ps["place_rate"])
                else:
                    print(f"    {p_lbl}: サンプル不足")

            if len(period_rates) == 3:
                spread = max(period_rates) - min(period_rates)
                stable = "安定" if spread <= 0.15 else "不安定"
                print(f"    → 期間ばらつき: {spread:.1%} [{stable}]")

            # ROI（複勝/単勝）
            if r["n"] >= args.min_n:
                roi = _calc_roi(df, r["combo"], CONDS_A, seg_name, from_date, to_date, payout_map)
                print(f"    ROI: 複勝{roi['place_roi']:.1%} 単勝{roi['win_roi']:.1%} ({roi['n_roi']}頭 NA={roi['na_roi']})")

    # ─── PatternB: 変化条件追加 ──────────────────────────────────────────
    print()
    print("=== PatternB（距離変化条件追加）3-5条件コンボ探索 ===")

    for seg_name in SEGMENTS:
        if len(seg_base.get(seg_name, pd.DataFrame())) < 200:
            continue
        print(f"\n--- {seg_name} PatternB ---")
        # 距離変化条件（10=dist_ext, 11=dist_short）を含む組み合わせのみ
        combos_b = _search_combos(df, seg_name, from_date, to_date, CONDS_B,
                                  min_n=args.min_n, combo_sizes=(3, 4, 5))
        combos_b_only = [r for r in combos_b if any(i >= _N_A for i in r["combo"])]

        if not combos_b_only:
            print("  変化条件含むパターンなし")
            continue

        # PatternAと比較
        a_best = all_seg_results_a.get(seg_name, [{}])[0].get("place_rate", 0)
        print(f"  PatternA最高複勝率: {a_best:.1%} → PatternB（変化条件含む）上位:")
        for r in combos_b_only[:3]:
            note = "↑改善" if r["place_rate"] > a_best else ""
            print(f"  {_fmt(r)} {note}")

    # ─── 機能B（穴馬 4番人気以降）──────────────────────────────────────
    print()
    print(f"=== 機能B（{args.ninki_b}番人気以降 穴馬探索）===")

    for seg_name in SEGMENTS:
        if len(seg_base.get(seg_name, pd.DataFrame())) < 200:
            continue
        # 穴馬の自然複勝率
        seg_ninki = seg_base[seg_name][seg_base[seg_name]["popularity"] >= args.ninki_b]
        if len(seg_ninki) < 50:
            continue
        nat_b = (seg_ninki["confirmed_rank"] <= 3).mean()

        combos_b_fun = _search_combos(df, seg_name, from_date, to_date, CONDS_A,
                                      min_n=50, combo_sizes=(3, 4, 5),
                                      min_ninki=args.ninki_b)

        if not combos_b_fun:
            print(f"\n[{seg_name}機能B] パターンなし（最低50頭未満）")
            continue

        print(f"\n--- {seg_name} 機能B（自然複勝率{nat_b:.1%}） ---")
        for r in combos_b_fun[:3]:
            roi = _calc_roi(df, r["combo"], CONDS_A, seg_name, from_date, to_date,
                            payout_map, min_ninki=args.ninki_b)
            print(f"  {_fmt(r)} | ROI複{roi['place_roi']:.1%} 単{roi['win_roi']:.1%}")

    # ─── 全体サマリー ────────────────────────────────────────────────────
    print()
    print("=== 全体サマリー ===")
    best_by_seg = {}
    for seg_name, res in all_seg_results_a.items():
        if res:
            best_by_seg[seg_name] = res[0]

    print(f"{'セグメント':12s} {'最良複勝率':>10} {'頭数':>8} {'条件':s}")
    print("-" * 80)
    for seg_name, best in best_by_seg.items():
        print(f"{seg_name:12s} {best['place_rate']:>10.1%} {best['n']:>8,} {best['label']}")


if __name__ == "__main__":
    _cli()
