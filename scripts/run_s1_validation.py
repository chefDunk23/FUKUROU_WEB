"""
scripts/run_s1_validation.py
==============================
Step 2-2: S-1パターン（ダート中距離 + 坂あり）の検証。

検証A: 頭ベース再現確認（Phase 2との比較）
検証B: レースベース的中率（4パターンの選び方）
検証C: 馬場別レースベース
検証D: ホールドアウト（2026年1月以降）

出力: BACKTEST_FINAL_VALIDATION.md
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from scripts.run_racecourse_search import (
    CONDS_A,
    _HILL_PC,
    _build_features,
    _calc_stats,
    _l1_mask,
    _apply_l2,
    _load_extended,
)
from tipster.backtest import _LOOKBACK_DAYS

# ── 設定 ────────────────────────────────────────────────────────────────────
EVAL_START   = date(2025, 6, 1)   # 検証データ開始（リーク防止）
EVAL_END     = date.today()
HOLDOUT_START = date(2026, 1, 1)

S1_CONDS = ["margin", "class_ok", "f3_top", "hill_fit", "sire_venue"]
S1_COMBO_IDX = tuple(CONDS_A.index(c) for c in S1_CONDS)
SEG_NAME = "ダート中距離"
L2_NAME  = "坂あり"

_BABA_CODE_MAP = {"1": "良", "2": "稍重", "3": "重", "4": "不良"}

MIN_SAMPLE = 30


# ── データロード ─────────────────────────────────────────────────────────────
def _load_df() -> pd.DataFrame:
    load_start = EVAL_START - timedelta(days=_LOOKBACK_DAYS)
    df = _load_extended(load_start, EVAL_END)
    df = _build_features(df)
    # 馬場コードをラベルに変換（track_condition: '1'=良 ... '4'=不良）
    if "track_condition" in df.columns:
        df["baba_label"] = df["track_condition"].astype(str).map(_BABA_CODE_MAP)
    else:
        df["baba_label"] = None
    return df


def _s1_mask(df: pd.DataFrame) -> pd.Series:
    l1 = _l1_mask(df, SEG_NAME, EVAL_START, EVAL_END)
    l2 = _apply_l2(df, SEG_NAME, L2_NAME)
    return l1 & l2 if l2 is not None else l1


def _match_mask(df: pd.DataFrame) -> pd.Series:
    cols = [f"cond_{CONDS_A[i]}" for i in S1_COMBO_IDX]
    match = pd.Series(True, index=df.index)
    for col in cols:
        match = match & (df[col] == 1.0)
    return match


# ── 検証A: 頭ベース再現 ───────────────────────────────────────────────────────
def validation_a(df: pd.DataFrame, lines: list[str]) -> None:
    lines.append("## 検証A: 頭ベース再現確認（Phase 2との比較）\n")
    seg_mask = _s1_mask(df)
    stats = _calc_stats(df, S1_COMBO_IDX, CONDS_A, seg_mask)
    if stats is None:
        lines.append("データなし\n\n")
        return

    phase2_ref = 58.8  # Phase 2検証の同期間結果（調査1より）
    diff = stats["place_rate"] * 100 - phase2_ref

    lines.append(f"- 対象期間: {EVAL_START} 〜 {EVAL_END}")
    lines.append(f"- セグメント: {SEG_NAME}|{L2_NAME}")
    lines.append(f"- 条件: {'+'.join(S1_CONDS)}")
    lines.append(f"- **頭ベース複勝率: {stats['place_rate']:.1%} / {stats['n']}頭**")
    lines.append(f"- 勝率: {stats['win_rate']:.1%}")
    lines.append(f"- 対象レース数: {stats['race_count']}R")
    lines.append(f"- Phase 2参照値: {phase2_ref:.1f}%")
    lines.append(f"- 差異: {diff:+.1f}pt {'✅ 再現成功(±3%以内)' if abs(diff) <= 3 else '⚠️ 差異あり'}")
    lines.append("")


# ── 検証B: レースベース的中率（4パターン） ──────────────────────────────────────
def _pick_one_per_race(df_cleared: pd.DataFrame, method: str) -> pd.DataFrame:
    """クリア馬が複数いるレースで1頭選ぶ。"""
    g = df_cleared.groupby("race_id")
    if method == "current":
        # 条件クリア数(=5) は全員同じなので prev1_margin最小を代理キーとする
        return g.apply(
            lambda x: x.nsmallest(1, "prev1_margin") if "prev1_margin" in x.columns else x.head(1)
        ).reset_index(drop=True)
    elif method == "popular":
        return g.apply(lambda x: x.nsmallest(1, "popularity")).reset_index(drop=True)
    elif method == "margin":
        return g.apply(lambda x: x.nsmallest(1, "prev1_margin")).reset_index(drop=True)
    elif method == "f3pct":
        return g.apply(lambda x: x.nsmallest(1, "prev1_f3pct")).reset_index(drop=True)
    elif method == "random":
        return g.apply(lambda x: x.sample(1, random_state=42)).reset_index(drop=True)
    return df_cleared.groupby("race_id").head(1).reset_index(drop=True)


def validation_b(df: pd.DataFrame, seg_mask: pd.Series, lines: list[str]) -> tuple[str, float]:
    lines.append("## 検証B: レースベース的中率（4パターンの選び方）\n")
    cleared_mask = seg_mask & _match_mask(df)
    df_cleared = df[cleared_mask].copy()

    if len(df_cleared) == 0:
        lines.append("クリア馬なし\n\n")
        return "popular", 0.0

    lines.append(f"- 条件クリア馬: {len(df_cleared)}頭 / {df_cleared['race_id'].nunique()}R\n")

    methods = {
        "(a) 条件クリア→prev1_margin最小（現行ロジック代理）": "current",
        "(b) 最人気馬（popularity昇順）": "popular",
        "(c) prev1_margin最小（前走着差最小）": "margin",
        "(d) ランダム（乱数seed=42）": "random",
    }

    best_label = ""
    best_rate = 0.0
    best_method = "popular"
    results = []
    for label, method in methods.items():
        picked = _pick_one_per_race(df_cleared, method)
        n = len(picked)
        if n < MIN_SAMPLE:
            results.append(f"  - {label}: {n}R（サンプル不足 < {MIN_SAMPLE}R）")
            continue
        place_rate = (picked["confirmed_rank"] <= 3).mean()
        win_rate   = (picked["confirmed_rank"] == 1).mean()
        results.append(
            f"  - {label}: **複勝率 {place_rate:.1%}** / 単勝率 {win_rate:.1%} / {n}R"
        )
        if place_rate > best_rate:
            best_rate = place_rate
            best_label = label
            best_method = method

    lines.extend(results)
    lines.append(f"\n**最良パターン: {best_label} ({best_rate:.1%})**\n")
    return best_method, best_rate


# ── 検証C: 馬場別（レースベース） ──────────────────────────────────────────────
def validation_c(df: pd.DataFrame, seg_mask: pd.Series, best_method: str, lines: list[str]) -> None:
    lines.append("## 検証C: 馬場別レースベース的中率\n")
    cleared_mask = seg_mask & _match_mask(df)
    df_cleared = df[cleared_mask].copy()

    baba_labels = ["良", "稍重", "重", "不良"]
    lines.append(f"| 馬場 | R数 | 複勝率 | 勝率 | 判定 |")
    lines.append(f"|---|---|---|---|---|")
    for baba in baba_labels:
        sub = df_cleared[df_cleared["baba_label"] == baba]
        if len(sub) == 0:
            lines.append(f"| {baba} | 0 | - | - | データなし |")
            continue
        picked = _pick_one_per_race(sub, best_method)
        n = len(picked)
        if n < MIN_SAMPLE:
            lines.append(f"| {baba} | {n} | - | - | サンプル不足(<{MIN_SAMPLE}R) |")
            continue
        pr = (picked["confirmed_rank"] <= 3).mean()
        wr = (picked["confirmed_rank"] == 1).mean()
        lines.append(f"| {baba} | {n} | {pr:.1%} | {wr:.1%} | {'良好' if pr >= 0.50 else 'やや低調' if pr >= 0.40 else '低調'} |")
    lines.append("")


# ── 検証D: ホールドアウト ────────────────────────────────────────────────────
def validation_d(df: pd.DataFrame, best_method: str, lines: list[str]) -> None:
    lines.append("## 検証D: ホールドアウト（2026年1月以降）\n")
    ho_l1 = _l1_mask(df, SEG_NAME, HOLDOUT_START, EVAL_END)
    ho_l2 = _apply_l2(df, SEG_NAME, L2_NAME)
    ho_mask = ho_l1 & ho_l2 if ho_l2 is not None else ho_l1
    cleared_mask = ho_mask & _match_mask(df)
    df_cleared = df[cleared_mask].copy()

    if len(df_cleared) == 0:
        lines.append("クリア馬なし（ホールドアウト期間）\n\n")
        return

    picked = _pick_one_per_race(df_cleared, best_method)
    n = len(picked)
    lines.append(f"- 期間: {HOLDOUT_START} 〜 {EVAL_END}")
    lines.append(f"- 選び方: {best_method}")
    if n < MIN_SAMPLE:
        lines.append(f"- **{n}R（サンプル不足 < {MIN_SAMPLE}R）**")
    else:
        pr = (picked["confirmed_rank"] <= 3).mean()
        wr = (picked["confirmed_rank"] == 1).mean()
        lines.append(f"- **複勝率: {pr:.1%} / {n}R**")
        lines.append(f"- 勝率: {wr:.1%}")
    lines.append("")


# ── main ─────────────────────────────────────────────────────────────────────
def main() -> None:
    print(f"[val] データロード中... ({EVAL_START} 〜 {EVAL_END})")
    df = _load_df()
    print(f"[val] ロード完了: {len(df):,}行")

    seg_mask = _s1_mask(df)
    print(f"[val] S-1セグメント: {seg_mask.sum():,}頭 / {df[seg_mask]['race_id'].nunique()}R")

    lines: list[str] = [
        "# S-1パターン バックテスト最終検証",
        "",
        f"実施日: {date.today()}",
        f"対象期間: {EVAL_START} 〜 {EVAL_END}",
        f"セグメント: {SEG_NAME}|{L2_NAME}",
        f"条件セット: {'+'.join(S1_CONDS)}",
        "",
        "---",
        "",
    ]

    print("[val] 検証A: 頭ベース再現...")
    validation_a(df, lines)

    print("[val] 検証B: レースベース4パターン...")
    best_method, best_rate = validation_b(df, seg_mask, lines)

    print("[val] 検証C: 馬場別...")
    validation_c(df, seg_mask, best_method, lines)

    print("[val] 検証D: ホールドアウト...")
    validation_d(df, best_method, lines)

    lines += [
        "---",
        "",
        "## まとめ",
        "",
        f"- 頭ベース複勝率: (検証A参照)",
        f"- レースベース最良複勝率: {best_rate:.1%} / 選び方: {best_method}",
        f"- B-2パターン検証: 別途 run_b2_validation.py で実施予定",
        "",
    ]

    out_path = Path(__file__).resolve().parents[1] / "BACKTEST_FINAL_VALIDATION.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[val] 出力: {out_path}")
    print("[val] 完了。")


if __name__ == "__main__":
    main()
