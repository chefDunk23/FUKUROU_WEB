"""
scripts/backtest_strategies_v3.py — バックテストシミュレーター V3
================================================================
V2 からの追加要素
  1. データ密度フィルタ: レース平均 feature_past_starts >= 3.5
     (キャリアが浅い未勝利・新馬戦のノイズを除去)
  2. 5クラス細分化 (grade_code + avg_past_starts ヒューリスティック):
       未勝利相当    = NaN  &  avg_past_starts <  3.5  (密度フィルタで除外)
       2勝相当       = NaN  &  avg_past_starts  3.5-8.0
       3勝相当       = NaN  &  avg_past_starts >= 8.0
       1勝クラス     = grade_code 'E'
       OP・重賞      = grade_code L/C/B/A
  3. 3戦略のみ: B_course, B_course_AND_pace, B_ability_AND_course
  4. 期間: デフォルト 2022-01-01 以降

Usage:
    py -3.13 scripts/backtest_strategies_v3.py
    py -3.13 scripts/backtest_strategies_v3.py --since 2023-01-01
"""
from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── ファイルパス ──────────────────────────────────────────────────────────────
_OOF_PARQUET      = _ROOT / "outputs" / "v2" / "evaluations" / "backtest_oof.parquet"
_SUBMODEL_PARQUET = _ROOT / "models" / "v2" / "submodels" / "oof_scores_v2.parquet"
_FEAT_PARQUET     = _ROOT / "outputs" / "v2_stacked_features.parquet"
_OUT_CSV          = Path(__file__).parent / "backtest_results_v3.csv"

# ── 定数 ─────────────────────────────────────────────────────────────────────
N_HORSES_MIN    = 8
AXIS_ODDS_MAX   = 10.0
BET_UNIT        = 100
TAKEOUT         = 0.225
DENSITY_MIN     = 3.5    # avg feature_past_starts per race
DENSITY_HIGH    = 8.0    # 2勝相当 / 3勝相当 境界

DEFAULT_SINCE   = "2022-01-01"

SUBMODEL_COLS = [
    "score_course_v2",
    "score_ability_v2",
    "score_pace_v2",
]

# ── クラスセグメント ──────────────────────────────────────────────────────────
# NOTE: density フィルタにより「未勝利相当」レースはほぼ除外される
SEGMENTS = [
    "ALL",
    "2勝相当(NaN低)",   # NaN grade, avg_past_starts [3.5, 8.0)
    "3勝相当(NaN高)",   # NaN grade, avg_past_starts >= 8.0
    "1勝クラス",        # grade_code = E
    "OP・重賞",         # grade_code in L/C/B/A
]


def _classify_race(grade_code: object, avg_ps: float) -> str:
    """grade_code + avg_past_starts でレースクラスを判定"""
    if not pd.isna(grade_code):
        g = str(grade_code).strip()
        if g in ("A", "B", "C", "L"):
            return "OP・重賞"
        if g == "E":
            return "1勝クラス"
    # grade_code is NaN (未勝利 / 2勝 / 3勝)
    if avg_ps < DENSITY_HIGH:
        return "2勝相当(NaN低)"
    return "3勝相当(NaN高)"


# ── Harville ユーティリティ ───────────────────────────────────────────────────

def _market_probs(odds_arr: np.ndarray) -> np.ndarray:
    inv = 1.0 / np.maximum(odds_arr, 0.01)
    return inv / inv.sum()


def _harville_umaren_prob(p_a: float, p_b: float) -> float:
    eps = 1e-9
    return (p_a * p_b / max(1 - p_a, eps)) + (p_b * p_a / max(1 - p_b, eps))


def _harville_wide_prob(p_a: float, p_b: float, p_others: np.ndarray) -> float:
    eps = 1e-9
    total = _harville_umaren_prob(p_a, p_b)
    for px in p_others:
        denom_ax = max(1 - p_a - px, eps)
        denom_bx = max(1 - p_b - px, eps)
        p_axb = p_a * px / max(1 - p_a, eps) * p_b / denom_ax
        p_bxa = p_b * px / max(1 - p_b, eps) * p_a / denom_bx
        p_xab = px * p_a / max(1 - px, eps) * p_b / denom_ax
        p_xba = px * p_b / max(1 - px, eps) * p_a / denom_bx
        p_abx = p_a * p_b / max(1 - p_a, eps) * px / max(1 - p_a - p_b, eps)
        p_bax = p_b * p_a / max(1 - p_b, eps) * px / max(1 - p_b - p_a, eps)
        total += p_axb + p_bxa + p_xab + p_xba + p_abx + p_bax
    return min(total, 1.0)


def _payouts(p_axis: float, p_partner: float, p_others: np.ndarray) -> tuple[float, float]:
    pu = _harville_umaren_prob(p_axis, p_partner)
    pw = _harville_wide_prob(p_axis, p_partner, p_others)
    return (
        (1 / max(pu, 1e-9)) * (1 - TAKEOUT),
        (1 / max(pw, 1e-9)) * (1 - TAKEOUT),
    )


# ── カウンタ ─────────────────────────────────────────────────────────────────

@dataclass
class SegCounter:
    n_races:     int   = 0
    n_bets:      int   = 0
    umaren_hits: int   = 0
    umaren_paid: float = 0.0
    wide_hits:   int   = 0
    wide_paid:   float = 0.0

    def add_bet(self, umaren_hit: bool, u_odds: float,
                wide_hit: bool, w_odds: float) -> None:
        self.n_bets += 1
        if umaren_hit:
            self.umaren_hits += 1
            self.umaren_paid += u_odds * BET_UNIT
        if wide_hit:
            self.wide_hits += 1
            self.wide_paid += w_odds * BET_UNIT

    @property
    def umaren_roi(self) -> float:
        return self.umaren_paid / (self.n_bets * BET_UNIT) if self.n_bets else float("nan")

    @property
    def wide_roi(self) -> float:
        return self.wide_paid / (self.n_bets * BET_UNIT) if self.n_bets else float("nan")

    @property
    def umaren_hit_rate(self) -> float:
        return self.umaren_hits / self.n_bets if self.n_bets else float("nan")

    @property
    def wide_hit_rate(self) -> float:
        return self.wide_hits / self.n_bets if self.n_bets else float("nan")


@dataclass
class Strategy:
    name: str
    select_partners: Callable[[pd.DataFrame, pd.Series], list[str]]
    description: str = ""
    _seg: dict[str, SegCounter] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self._seg = {s: SegCounter() for s in SEGMENTS}

    def seg(self, name: str) -> SegCounter:
        return self._seg[name]


# ── 戦略定義 (3戦略のみ) ─────────────────────────────────────────────────────

def _make_strategies() -> list[Strategy]:

    def b_course(others: pd.DataFrame, _: pd.Series) -> list[str]:
        if "score_course_v2" not in others.columns:
            return []
        return [others.loc[others["score_course_v2"].idxmax(), "horse_id"]]

    def b_course_and_pace(others: pd.DataFrame, _: pd.Series) -> list[str]:
        if "score_course_v2" not in others.columns or "score_pace_v2" not in others.columns:
            return []
        best_course = others.loc[others["score_course_v2"].idxmax(), "horse_id"]
        best_pace   = others.loc[others["score_pace_v2"].idxmax(),   "horse_id"]
        return [best_course] if best_course == best_pace else []

    def b_ability_and_course(others: pd.DataFrame, _: pd.Series) -> list[str]:
        if "score_ability_v2" not in others.columns or "score_course_v2" not in others.columns:
            return []
        best_ab = others.loc[others["score_ability_v2"].idxmax(), "horse_id"]
        best_co = others.loc[others["score_course_v2"].idxmax(),  "horse_id"]
        return [best_ab] if best_ab == best_co else []

    return [
        Strategy("B_course",              b_course,              "コース適性1位"),
        Strategy("B_course_AND_pace",     b_course_and_pace,     "course AND pace 同一馬"),
        Strategy("B_ability_AND_course",  b_ability_and_course,  "ability AND course 同一馬"),
    ]


# ── レース処理 ───────────────────────────────────────────────────────────────

def _process_race(
    race: pd.DataFrame,
    seg_label: str,
    strategies: list[Strategy],
    axis_odds_max: float,
) -> None:
    if len(race) < N_HORSES_MIN:
        return

    axis_rows = race[race["ai_rank"] == 1]
    if axis_rows.empty:
        return
    axis = axis_rows.iloc[0]

    try:
        axis_odds = float(axis["tan_odds"])
    except (TypeError, ValueError):
        return
    if axis_odds >= axis_odds_max:
        return

    others = race[race["horse_id"] != axis["horse_id"]].copy()
    if others.empty:
        return

    odds_arr = race["tan_odds"].values.astype(float)
    probs    = _market_probs(odds_arr)
    prob_map = dict(zip(race["horse_id"].values, probs))
    p_axis   = prob_map.get(axis["horse_id"], 0.0)
    axis_rank = int(axis["kakutei_chakujun"])

    for strategy in strategies:
        partners = strategy.select_partners(others, axis)
        if not partners:
            continue

        strategy.seg("ALL").n_races     += 1
        strategy.seg(seg_label).n_races += 1

        for hid in partners:
            partner_row = others[others["horse_id"] == hid]
            if partner_row.empty:
                continue
            partner       = partner_row.iloc[0]
            p_partner     = prob_map.get(hid, 0.0)
            p_others_arr  = np.array(
                [v for k, v in prob_map.items()
                 if k not in (axis["horse_id"], hid)],
                dtype=float,
            )
            partner_rank  = int(partner["kakutei_chakujun"])
            umaren_hit    = {axis_rank, partner_rank} == {1, 2}
            wide_hit      = axis_rank <= 3 and partner_rank <= 3
            u_odds, w_odds = _payouts(p_axis, p_partner, p_others_arr)

            strategy.seg("ALL").add_bet(umaren_hit, u_odds, wide_hit, w_odds)
            strategy.seg(seg_label).add_bet(umaren_hit, u_odds, wide_hit, w_odds)


# ── 表示・保存 ───────────────────────────────────────────────────────────────

def _print_table(strategies: list[Strategy], bet_type: str) -> None:
    is_wide = bet_type == "ワイド"
    threshold = 1 - TAKEOUT

    width = 130
    print(f"\n{'=' * width}")
    print(f"  {bet_type}  /  軸: AIランク1位 / 軸オッズ<{AXIS_ODDS_MAX:.0f}倍 / {N_HORSES_MIN}頭立て以上")
    print(f"  データ密度フィルタ: avg past_starts >= {DENSITY_MIN:.1f}")
    print(f"  ★ Harville理論値（控除{TAKEOUT:.0%}）  ランダム期待値 ≈ {threshold:.1%}")
    print("=" * width)

    seg_display = [
        ("ALL",            "全体"),
        ("2勝相当(NaN低)", "2勝相当(ps<8)"),
        ("3勝相当(NaN高)", "3勝相当(ps>=8)"),
        ("1勝クラス",      "1勝クラス(E)"),
        ("OP・重賞",       "OP・重賞"),
    ]

    col_w = 20
    name_w = 26
    header = f"{'戦略':<{name_w}}"
    for _, label in seg_display:
        header += f" | {label:^{col_w}}"
    print(header)

    subhead = " " * name_w
    for _ in seg_display:
        subhead += f" | {'R数':>4} {'的中率':>6} {'回収率':>8}"
    print(subhead)
    print("-" * width)

    def _fmt(c: SegCounter) -> str:
        if c.n_bets == 0:
            return f"{'—':>4} {'—':>6} {'—':>8}"
        hr  = c.wide_hit_rate  if is_wide else c.umaren_hit_rate
        roi = c.wide_roi       if is_wide else c.umaren_roi
        roi_str = f"{roi:.1%}"
        if not np.isnan(roi) and roi > threshold:
            roi_str += "★"
        return f"{c.n_races:>4} {hr:.1%} {roi_str:>9}"

    for s in strategies:
        row = f"{s.name:<{name_w}}"
        for seg_key, _ in seg_display:
            row += f" | {_fmt(s.seg(seg_key))}"
        print(row)

    print()


def _save_csv(strategies: list[Strategy]) -> None:
    rows = []
    for s in strategies:
        for seg_name in SEGMENTS:
            c = s.seg(seg_name)
            if c.n_bets == 0:
                continue
            rows.append({
                "strategy":        s.name,
                "description":     s.description,
                "segment":         seg_name,
                "n_races":         c.n_races,
                "n_bets":          c.n_bets,
                "umaren_hits":     c.umaren_hits,
                "umaren_hit_rate": round(c.umaren_hit_rate, 4),
                "umaren_roi":      round(c.umaren_roi, 4),
                "wide_hits":       c.wide_hits,
                "wide_hit_rate":   round(c.wide_hit_rate, 4),
                "wide_roi":        round(c.wide_roi, 4),
            })
    pd.DataFrame(rows).to_csv(_OUT_CSV, index=False, encoding="utf-8-sig")
    log.info("CSV保存: %s", _OUT_CSV)


# ── メイン ───────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="馬券戦略バックテスト V3")
    parser.add_argument("--since", default=DEFAULT_SINCE, help="開始日 YYYY-MM-DD")
    parser.add_argument("--until", default=None)
    parser.add_argument("--axis-odds-max", type=float, default=AXIS_ODDS_MAX)
    parser.add_argument("--density-min", type=float, default=DENSITY_MIN,
                        help="avg past_starts下限")
    parser.add_argument("--bet", choices=["umaren", "wide", "both"], default="both")
    args = parser.parse_args()

    axis_odds_max = args.axis_odds_max
    density_min   = args.density_min

    # ── データ読み込み ─────────────────────────────────────────────────────
    log.info("OOF読み込み: %s", _OOF_PARQUET)
    df = pd.read_parquet(_OOF_PARQUET)
    log.info("  shape=%s  races=%d", df.shape, df["race_id"].nunique())

    if _SUBMODEL_PARQUET.exists():
        log.info("サブモデルスコアマージ: %s", _SUBMODEL_PARQUET)
        sub = pd.read_parquet(_SUBMODEL_PARQUET)
        sub_cols = ["race_id", "horse_id"] + [c for c in SUBMODEL_COLS if c in sub.columns]
        df = df.merge(sub[sub_cols], on=["race_id", "horse_id"], how="left")

    # ── データ密度: avg feature_past_starts per race ────────────────────────
    log.info("データ密度計算: %s", _FEAT_PARQUET)
    feat = pd.read_parquet(_FEAT_PARQUET, columns=["race_id", "feature_past_starts"])
    race_density = feat.groupby("race_id")["feature_past_starts"].mean().rename("avg_past_starts")
    df = df.merge(race_density, on="race_id", how="left")
    df["avg_past_starts"] = df["avg_past_starts"].fillna(0.0)

    # ── 期間フィルタ ───────────────────────────────────────────────────────
    df["race_date"] = pd.to_datetime(df["race_date"], errors="coerce")
    if args.since:
        df = df[df["race_date"] >= pd.Timestamp(args.since)]
    if args.until:
        df = df[df["race_date"] <= pd.Timestamp(args.until)]

    # ── 前処理 ─────────────────────────────────────────────────────────────
    df["tan_odds"]         = pd.to_numeric(df["tan_odds"], errors="coerce").fillna(999.9)
    df                     = df[df["kakutei_chakujun"].notna() & (df["kakutei_chakujun"] > 0)].copy()
    df["kakutei_chakujun"] = df["kakutei_chakujun"].astype(int)

    n_before = df["race_id"].nunique()
    log.info("期間フィルタ後: %d 行 / %d レース", len(df), n_before)

    # ── データ密度フィルタ ─────────────────────────────────────────────────
    valid_races = (
        df.drop_duplicates("race_id")
          .query("avg_past_starts >= @density_min")["race_id"]
    )
    df = df[df["race_id"].isin(valid_races)].copy()
    n_after = df["race_id"].nunique()
    log.info("密度フィルタ後 (avg_past_starts >= %.1f): %d 行 / %d レース (除外: %d レース)",
             density_min, len(df), n_after, n_before - n_after)

    # ── セグメント付与 ─────────────────────────────────────────────────────
    race_meta = df.drop_duplicates("race_id")[["race_id", "grade_code", "avg_past_starts"]]
    race_meta = race_meta.assign(
        segment=race_meta.apply(
            lambda r: _classify_race(r["grade_code"], r["avg_past_starts"]), axis=1
        )
    )[["race_id", "segment"]]
    df = df.merge(race_meta, on="race_id", how="left")

    seg_counts = df.drop_duplicates("race_id").groupby("segment", dropna=False).size().to_dict()
    log.info("セグメント別レース数: %s", seg_counts)
    log.info("  軸オッズ上限 = %.1f 倍", axis_odds_max)

    # ── バックテスト ───────────────────────────────────────────────────────
    strategies = _make_strategies()
    log.info("バックテスト実行中 (%d 戦略 x %d レース)...", len(strategies), n_after)

    for race_id, race in df.groupby("race_id"):
        seg = race["segment"].iloc[0]
        _process_race(race, seg, strategies, axis_odds_max)

    # ── 出力 ──────────────────────────────────────────────────────────────
    if args.bet in ("umaren", "both"):
        _print_table(strategies, "馬連")
    if args.bet in ("wide", "both"):
        _print_table(strategies, "ワイド")

    _save_csv(strategies)
    log.info("完了 -> %s", _OUT_CSV)


if __name__ == "__main__":
    main()
