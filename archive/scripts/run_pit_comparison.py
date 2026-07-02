"""
scripts/run_pit_comparison.py
==============================
PIT修正前後の比較表を生成する（S-1〜A-2 パターン）

実行例:
  py -3 scripts/run_pit_comparison.py --from-date 2025-06-27 --to-date 2026-06-27
"""
from __future__ import annotations
import argparse, sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.run_racecourse_search import (
    _load_extended, _build_features,
    _HILL_PC, _LONG_STR_PC,
)
from tipster.backtest import _LOOKBACK_DAYS


# ─────────────────────────────────────────────────────────────────────────
# パターン定義
# ─────────────────────────────────────────────────────────────────────────
# 各パターン: (label, before_place_pct, before_n, conditions, surf_filter, dist_lo, dist_hi, pc_filter)
PATTERNS = [
    {
        "id": "S-1", "name": "ダート中距離|坂あり",
        "before_place": 67.0, "before_n": 115,
        "conds": ["cond_margin", "cond_class_ok", "cond_f3_top", "cond_hill_fit", "cond_sire_venue"],
        "surf": "ダート", "dist_lo": 1401, "dist_hi": 2000,
        "pc_set": _HILL_PC,
    },
    {
        "id": "S-2", "name": "ダート中距離|全体",
        "before_place": 66.4, "before_n": 110,
        "conds": ["cond_class_ok", "cond_interval_ok", "cond_surface_ok", "cond_f3_top", "cond_sire_venue"],
        "surf": "ダート", "dist_lo": 1401, "dist_hi": 2000,
        "pc_set": None,
    },
    {
        "id": "S-3", "name": "ダート中距離|全体(6条件)",
        "before_place": 69.7, "before_n": 99,
        "conds": ["cond_margin", "cond_class_ok", "cond_interval_ok", "cond_surface_ok", "cond_f3_top", "cond_sire_venue"],
        "surf": "ダート", "dist_lo": 1401, "dist_hi": 2000,
        "pc_set": None,
    },
    {
        "id": "A-1", "name": "芝中距離|全体",
        "before_place": 59.8, "before_n": 117,
        "conds": ["cond_weight_ok", "cond_f3_top", "cond_straight_fit", "cond_hill_fit", "cond_sire_surface"],
        "surf": "芝", "dist_lo": 1601, "dist_hi": 2200,
        "pc_set": None,
    },
    {
        "id": "A-2", "name": "ダート中距離|長直線",
        "before_place": 57.8, "before_n": 102,
        "conds": ["cond_interval_ok", "cond_surface_ok", "cond_f3_top", "cond_sire_venue", "cond_sire_surface"],
        "surf": "ダート", "dist_lo": 1401, "dist_hi": 2000,
        "pc_set": _LONG_STR_PC,
    },
    {
        "id": "B-2", "name": "ダート中距離|全体(sire無し)",
        "before_place": 53.2, "before_n": 387,
        "conds": ["cond_margin", "cond_class_ok", "cond_interval_ok", "cond_surface_ok", "cond_f3_top"],
        "surf": "ダート", "dist_lo": 1401, "dist_hi": 2000,
        "pc_set": None,
    },
]


def _eval_pattern(df: pd.DataFrame, p: dict, from_date: date, to_date: date) -> dict:
    mask = (
        (df["surface"] == p["surf"])
        & (df["distance"] >= p["dist_lo"])
        & (df["distance"] <= p["dist_hi"])
        & (df["date"].dt.date >= from_date)
        & (df["date"].dt.date <= to_date)
    )
    if p["pc_set"] is not None:
        mask = mask & df["place_code"].isin(p["pc_set"])

    sub = df[mask]
    match = pd.Series(True, index=sub.index)
    for col in p["conds"]:
        if col in sub.columns:
            match = match & (sub[col] == 1.0)
        else:
            print(f"  [WARN] column not found: {col}")

    matched = sub[match]
    n = len(matched)
    if n == 0:
        return {"n": 0, "place_rate": 0.0, "win_rate": 0.0}
    place = (matched["confirmed_rank"] <= 3).sum()
    win   = (matched["confirmed_rank"] == 1).sum()
    return {"n": n, "place_rate": place / n, "win_rate": win / n}


def _cli() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-date", default="2025-06-27")
    parser.add_argument("--to-date",   default="today")
    args = parser.parse_args()

    from_date = date.today() if args.from_date == "today" else date.fromisoformat(args.from_date)
    to_date   = date.today() if args.to_date   == "today" else date.fromisoformat(args.to_date)

    load_start = from_date - timedelta(days=_LOOKBACK_DAYS)
    print(f"[cmp] データ読み込み ({load_start} ~ {to_date})...")
    df = _load_extended(load_start, to_date)
    print("[cmp] 特徴量計算中...")
    df = _build_features(df)
    print(f"[cmp] 完了: {len(df):,}行")

    # cond_margin を追加（run_final_validation.py と同ロジック）
    if "cond_margin" not in df.columns:
        df["cond_margin"] = (df["prev1_margin"] <= 0.5).astype(object)
        df.loc[df["prev1_margin"].isna(), "cond_margin"] = None

    print()
    print("=" * 70)
    print("PIT修正前後 比較表")
    print(f"期間: {from_date} ~ {to_date}")
    print("=" * 70)
    print(f"{'ID':5} {'パターン名':25} {'修正前':>8} {'修正後':>8} {'変化':>7} {'N修正後':>8}")
    print("-" * 70)

    for p in PATTERNS:
        result = _eval_pattern(df, p, from_date, to_date)
        before = p["before_place"]
        after  = result["place_rate"] * 100
        delta  = after - before
        n_after = result["n"]
        sign = "+" if delta >= 0 else ""
        print(f"{p['id']:5} {p['name']:25} {before:7.1f}%  {after:7.1f}%  {sign}{delta:5.1f}%  {n_after:>7}頭")

    print("=" * 70)
    print()
    print("[注] 修正前 = sire_feature_store 最新スナップショット使用（非PIT）")
    print("[注] 修正後 = merge_asof によるPITスナップショット使用")
    print()


if __name__ == "__main__":
    _cli()
