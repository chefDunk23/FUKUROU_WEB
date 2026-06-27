"""
scripts/_investigate_phase2_vs_bt.py
======================================
Phase 2 vs バックテスト乖離の追加調査（読み取り専用・コード変更なし）

調査1: Phase 2 S-1条件（margin+class_ok+f3_top+hill_fit+sire_venue）の再現
調査2: 条件クリア馬が1レースに平均何頭いるか
調査3: 条件の対応関係整理
調査4: 選び方を変えた場合の的中率比較

出力: BACKTEST_DISCREPANCY_INVESTIGATION.md
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from tipster.backtest import _LOOKBACK_DAYS
from scripts.run_racecourse_search import (
    _load_extended, _build_features,
    _l1_mask, _apply_l2,
    _match_mask, _calc_stats,
    CONDS_A, _HILL_PC,
)

# ─────────────────────────────────────────────────────────────────────────────
# 定数
# ─────────────────────────────────────────────────────────────────────────────

FROM_DATE = date(2025, 6, 27)
TO_DATE   = date(2026, 6, 27)

# Phase 2 S-1 条件コンボ（margin+class_ok+f3_top+hill_fit+sire_venue）
S1_COND_NAMES = ["margin", "class_ok", "f3_top", "hill_fit", "sire_venue"]
S1_COMBO_IDX  = tuple(CONDS_A.index(c) for c in S1_COND_NAMES)

# B-2 条件コンボ（margin+class_ok+interval_ok+surface_ok+f3_top）
B2_COND_NAMES = ["margin", "class_ok", "interval_ok", "surface_ok", "f3_top"]
B2_COMBO_IDX  = tuple(CONDS_A.index(c) for c in B2_COND_NAMES)

# honmei_v6 で使われている条件（conditions_v2.py）→ 対応関係整理用
HONMEI_V6_CONDITIONS = [
    "v2_past_margin",     # 過去3走以内に勝ち馬差≤1秒（必須）
    "v2_race_quality",    # 前走上位3頭の次走複勝率≥35%（必須）
    "v2_class_change",    # クラス変化評価
    "v2_jockey_positive", # 騎手評価
    "v2_weight_favor",    # 斤量軽減
    "v2_interval_optimal","# 間隔（15〜28日）",
    "v2_surface_history", # 同馬場好走歴
    "v2_distance_match",  # 距離適性
]


def _load_data() -> pd.DataFrame:
    print(f"[inv] データロード中 ({FROM_DATE} ~ {TO_DATE})...")
    load_start = FROM_DATE - timedelta(days=_LOOKBACK_DAYS)
    df = _load_extended(load_start, TO_DATE)
    print("[inv] 特徴量計算中...")
    df = _build_features(df)
    print(f"[inv] 完了: {len(df):,}行")
    return df


def _s1_mask(df: pd.DataFrame) -> pd.Series:
    """S-1セグメント: ダート中距離 + 坂あり（対象期間内）"""
    l1 = _l1_mask(df, "ダート中距離", FROM_DATE, TO_DATE)
    l2 = _apply_l2(df, "ダート中距離", "坂あり")
    return l1 & l2


def _b2_mask(df: pd.DataFrame) -> pd.Series:
    """B-2セグメント: ダート中距離 全場（対象期間内）"""
    return _l1_mask(df, "ダート中距離", FROM_DATE, TO_DATE)


# ─────────────────────────────────────────────────────────────────────────────
# 調査1: Phase 2 S-1 条件の再現
# ─────────────────────────────────────────────────────────────────────────────

def inv1_reproduce_phase2(df: pd.DataFrame, lines: list[str]) -> None:
    lines.append("\n## 調査1: Phase 2 S-1条件の再現")
    lines.append(f"\n対象期間: {FROM_DATE} 〜 {TO_DATE}")
    lines.append(f"S-1セグメント: ダート中距離 + 坂あり（{sorted(_HILL_PC)}）")
    lines.append(f"条件セット: {'+'.join(S1_COND_NAMES)}")
    lines.append(f"条件インデックス: {S1_COMBO_IDX}")

    mask = _s1_mask(df)
    n_total_horses = mask.sum()
    n_races = df[mask]["race_id"].nunique()
    nat_pr = (df[mask]["confirmed_rank"] <= 3).mean()
    lines.append(f"\n### S-1セグメント基礎統計")
    lines.append(f"- 対象馬数: {n_total_horses:,}頭")
    lines.append(f"- 対象レース数: {n_races:,}R")
    lines.append(f"- 自然複勝率（ランダム選択）: {nat_pr:.1%}")

    stats = _calc_stats(df, S1_COMBO_IDX, CONDS_A, mask)
    if stats:
        lines.append(f"\n### 条件クリア馬（頭数ベース）")
        lines.append(f"- クリア馬数: {stats['n']:,}頭")
        lines.append(f"- 複勝数: {stats['place']:,}頭")
        lines.append(f"- **複勝率（頭数ベース）: {stats['place_rate']:.1%}**")
        lines.append(f"- 勝率: {stats['win_rate']:.1%}")
        lines.append(f"- 対象レース数: {stats['race_count']:,}R")
        lines.append(f"- 1日平均頭数: {stats['avg_per_day']:.2f}頭")
    else:
        lines.append("\n- データなし")

    # B-2も同様に
    lines.append(f"\n### B-2セグメント: {'+'.join(B2_COND_NAMES)}")
    b2_mask = _b2_mask(df)
    stats_b2 = _calc_stats(df, B2_COMBO_IDX, CONDS_A, b2_mask)
    if stats_b2:
        lines.append(f"- クリア馬数: {stats_b2['n']:,}頭")
        lines.append(f"- **複勝率（頭数ベース）: {stats_b2['place_rate']:.1%}**")
        lines.append(f"- 対象レース数: {stats_b2['race_count']:,}R")
    else:
        lines.append("- データなし")


# ─────────────────────────────────────────────────────────────────────────────
# 調査2: 条件クリア馬が1レースに平均何頭いるか
# ─────────────────────────────────────────────────────────────────────────────

def inv2_clearers_per_race(df: pd.DataFrame, lines: list[str]) -> None:
    lines.append("\n## 調査2: 条件クリア馬が1レースに平均何頭いるか（S-1）")

    mask = _s1_mask(df)
    sub = df[mask].copy()

    # 各馬の条件クリアフラグ
    cond_cols = [f"cond_{c}" for c in S1_COND_NAMES]
    sub["all_clear"] = True
    for col in cond_cols:
        sub["all_clear"] = sub["all_clear"] & (sub[col] == 1.0)

    # レース別クリア頭数
    race_clear = sub.groupby("race_id")["all_clear"].sum().reset_index()
    race_clear.columns = ["race_id", "n_clearers"]
    race_clear["n_clearers"] = race_clear["n_clearers"].fillna(0).astype(int)

    total_races = len(race_clear)
    avg_clearers = race_clear["n_clearers"].mean()
    lines.append(f"\n対象レース数: {total_races:,}R")
    lines.append(f"平均クリア馬数: {avg_clearers:.2f}頭/R")
    lines.append("")

    dist = race_clear["n_clearers"].value_counts().sort_index()
    lines.append("| クリア頭数 | レース数 | 割合 |")
    lines.append("|---|---|---|")
    for n_clear, cnt in dist.items():
        pct = cnt / total_races
        lines.append(f"| {n_clear}頭 | {cnt:,}R | {pct:.1%} |")

    # 0頭レースでの「推奨なし」が妥当かチェック
    zero_clear = race_clear[race_clear["n_clearers"] == 0]
    lines.append(f"\n#### 条件クリア馬ゼロのレース: {len(zero_clear):,}R ({len(zero_clear)/total_races:.1%})")
    lines.append("→ このレースは「推奨なし」が正しい")

    # 1頭だけのレース: 選び方の問題なし
    one_clear = race_clear[race_clear["n_clearers"] == 1]
    lines.append(f"\n#### 条件クリア馬が1頭のみ: {len(one_clear):,}R ({len(one_clear)/total_races:.1%})")
    lines.append("→ 「選び方」は関係ない（その1頭を推奨するだけ）")

    # 1頭の場合の複勝率
    one_clear_rids = set(one_clear["race_id"])
    one_sub = sub[sub["race_id"].isin(one_clear_rids) & sub["all_clear"]]
    if len(one_sub) > 0:
        pr_one = (one_sub["confirmed_rank"] <= 3).mean()
        wr_one = (one_sub["confirmed_rank"] == 1).mean()
        lines.append(f"  - 複勝率: {pr_one:.1%} ({len(one_sub)}頭)")
        lines.append(f"  - 勝率: {wr_one:.1%}")

    # 2頭以上のレース
    multi_clear = race_clear[race_clear["n_clearers"] >= 2]
    lines.append(f"\n#### 条件クリア馬が2頭以上: {len(multi_clear):,}R ({len(multi_clear)/total_races:.1%})")
    multi_rids = set(multi_clear["race_id"])
    multi_sub = sub[sub["race_id"].isin(multi_rids) & sub["all_clear"]]
    if len(multi_sub) > 0:
        pr_multi = (multi_sub["confirmed_rank"] <= 3).mean()
        lines.append(f"  - 全クリア馬の複勝率: {pr_multi:.1%} ({len(multi_sub)}頭)")
        lines.append("  → ここが「選び方」で改善可能な領域")


# ─────────────────────────────────────────────────────────────────────────────
# 調査3: 条件対応関係
# ─────────────────────────────────────────────────────────────────────────────

def inv3_condition_mapping(lines: list[str]) -> None:
    lines.append("\n## 調査3: Phase 2条件 vs honmei_v6条件の対応関係")

    lines.append("""
| Phase 2条件 | 定義 | honmei_v6対応 | 備考 |
|---|---|---|---|
| `margin` | prev1_margin ≤ 1.0秒 | `v2_past_margin` (過去3走以内に≤1秒) | ✅ 類似（lookabackが違う） |
| `class_ok` | class_level ≤ prev1_class | `v2_class_change` (降級=+1) | ⚠️ 部分的（honmei_v6は降級ボーナスのみ） |
| `f3_top` | prev1_f3pct ≤ 0.33 (上がり上位1/3) | **なし** | ❌ honmei_v6に未実装 |
| `hill_fit` | 坂あり競馬場での過去3走以内に3着以内 | **なし** | ❌ honmei_v6に未実装 |
| `sire_venue` | 種牡馬の該当会場top3率 > 全体 (≥10頭) | **なし** | ❌ honmei_v6に未実装 |
| — | — | `v2_race_quality` (前走レースレベル必須) | honmei_v6のみ |
| — | — | `v2_jockey_positive` (騎手評価) | honmei_v6のみ |
| — | — | `v2_weight_favor` (斤量軽減) | honmei_v6のみ |
| `interval_ok` | days 15〜28 | `v2_interval_optimal` (同じ定義) | ✅ 同一 |
| `surface_ok` | 同馬場で3着以内 | `v2_surface_history` (同じ概念) | ✅ 類似 |
""")

    lines.append("### 欠落条件の影響")
    lines.append("- `f3_top`: 前走の上がりタイムが上位1/3（末脚の指標）→ 欠落により末脚力フィルタが効かない")
    lines.append("- `hill_fit`: 坂あり競馬場での過去好走歴 → 欠落により坂適性フィルタが効かない")
    lines.append("- `sire_venue`: 種牡馬の会場特性 → 欠落により血統的な会場適性が無視される")
    lines.append("")
    lines.append("**結論**: honmei_v6 はS-1の「核心条件」であるhil_fit/f3_top/sire_venueを含まない別条件セット。")
    lines.append("S-1パターンの知見をhonmei_v6に組み込む場合は、これら3条件を追加実装する必要がある。")


# ─────────────────────────────────────────────────────────────────────────────
# 調査4: 選び方を変えた場合の的中率比較
# ─────────────────────────────────────────────────────────────────────────────

def inv4_selection_strategies(df: pd.DataFrame, lines: list[str]) -> None:
    lines.append("\n## 調査4: 「選び方」を変えた場合の的中率比較（S-1）")

    mask = _s1_mask(df)
    sub = df[mask].copy()

    cond_cols = [f"cond_{c}" for c in S1_COND_NAMES]
    sub["all_clear"] = True
    for col in cond_cols:
        sub["all_clear"] = sub["all_clear"] & (sub[col] == 1.0)

    # クリア馬のみ
    cleared = sub[sub["all_clear"]].copy()
    if cleared.empty:
        lines.append("データなし")
        return

    # クリア馬の自然複勝率（頭数ベース = Phase 2と同じ方法）
    pr_all = (cleared["confirmed_rank"] <= 3).mean()
    wr_all = (cleared["confirmed_rank"] == 1).mean()
    lines.append(f"\n### (A) 全クリア馬の複勝率（頭数ベース = Phase 2方式）")
    lines.append(f"- {len(cleared):,}頭 / 複勝率 {pr_all:.1%} / 勝率 {wr_all:.1%}")

    # 1レースから1頭選ぶ（4戦略）
    race_ids_with_clearers = cleared["race_id"].unique()
    lines.append(f"\n対象レース（クリア馬が1頭以上）: {len(race_ids_with_clearers):,}R")
    lines.append("（以下は各レースから1頭を選んだ場合の1レース1エントリーベース）")

    strategies = {
        "(B) 人気上位を選ぶ（popularity昇順 = 最も人気）": lambda g: g.nsmallest(1, "popularity"),
        "(C) prev1_margin最小（前走最接戦）": lambda g: g.nsmallest(1, "prev1_margin"),
        "(D) prev1_f3pct最小（前走上がり最上位）": lambda g: g.nsmallest(1, "prev1_f3pct"),
        "(E) ランダム選択（期待値 = (A)と同じ）": lambda g: g.sample(1, random_state=42),
    }

    for name, picker in strategies.items():
        picks = []
        for rid, grp in cleared.groupby("race_id"):
            selected = picker(grp)
            picks.append(selected)
        if not picks:
            continue
        pick_df = pd.concat(picks)
        pr = (pick_df["confirmed_rank"] <= 3).mean()
        wr = (pick_df["confirmed_rank"] == 1).mean()
        n = len(pick_df)
        lines.append(f"\n### {name}")
        lines.append(f"- {n:,}R / 複勝率 {pr:.1%} / 勝率 {wr:.1%}")

    # 比較: クリア馬が複数いるレースのみで戦略を比較
    multi_race_ids = (
        cleared.groupby("race_id").size()
        .pipe(lambda s: s[s >= 2].index)
    )
    if len(multi_race_ids) > 0:
        lines.append(f"\n### クリア馬2頭以上のレースのみ ({len(multi_race_ids)}R) での比較")
        multi_cleared = cleared[cleared["race_id"].isin(multi_race_ids)]
        pr_all_multi = (multi_cleared["confirmed_rank"] <= 3).mean()
        lines.append(f"- 全クリア馬（頭数ベース）: {pr_all_multi:.1%} ({len(multi_cleared)}頭)")

        for name, picker in strategies.items():
            picks = []
            for rid, grp in multi_cleared.groupby("race_id"):
                selected = picker(grp)
                picks.append(selected)
            if not picks:
                continue
            pick_df = pd.concat(picks)
            pr = (pick_df["confirmed_rank"] <= 3).mean()
            n = len(pick_df)
            lines.append(f"- {name}: {pr:.1%} ({n}R)")


# ─────────────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    df = _load_data()

    lines: list[str] = [
        "# Phase 2 vs バックテスト乖離 追加調査",
        "",
        f"実施日: {date.today()}",
        f"対象期間: {FROM_DATE} 〜 {TO_DATE}",
        "調査対象: S-1セグメント（ダート中距離 + 坂あり）",
        f"Phase 2条件: {'+'.join(S1_COND_NAMES)}",
        "バックテスト条件: honmei_v6 (v2_past_margin + v2_race_quality + ...)",
        "",
        "---",
    ]

    print("[inv] 調査1: Phase 2 S-1条件の再現...")
    inv1_reproduce_phase2(df, lines)

    print("[inv] 調査2: クリア馬数分布...")
    inv2_clearers_per_race(df, lines)

    print("[inv] 調査3: 条件対応関係...")
    inv3_condition_mapping(lines)

    print("[inv] 調査4: 選び方戦略比較...")
    inv4_selection_strategies(df, lines)

    lines += [
        "",
        "---",
        "",
        "## まとめ",
        "",
        "### 乖離の3原因（調査確認結果）",
        "1. **集計単位の差**: Phase 2 = 頭数ベース（1レースに複数頭可）/ バックテスト = レースベース（1頭）",
        "2. **条件セットの差**: hill_fit / f3_top / sire_venue がhonmei_v6に未実装",
        "3. **フィルタの有無**: Phase 2はmin_n=50でフィルタ後の高確率パターンのみ表示",
        "",
        "### 改善の方向性（調査4より）",
        "- 条件クリア馬から「最も人気の高い馬」を選ぶことで改善できるか → 調査4参照",
        "- hill_fit / f3_top / sire_venue をhonmei_v7以降に追加することで",
        "  Phase 2パターンに近づく可能性がある",
    ]

    out_path = Path(__file__).resolve().parents[1] / "BACKTEST_DISCREPANCY_INVESTIGATION.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[inv] 出力完了: {out_path}")


if __name__ == "__main__":
    main()
