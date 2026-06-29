"""
scripts/run_v2_combo_search.py
================================
Phase 2 条件数最適化探索:
conditions_v2.py の 8 条件から 5/6/7 条件の全組み合わせ
（C(8,5)+C(8,6)+C(8,7) = 92 パターン）を一括評価し、
複勝的中率・単勝的中率・近似回収率・該当頭数・日別分布を集計する。

目標ゾーン（変更後）:
  - 複勝的中率 60% 以上 または 単勝的中率 25% 以上（両方集計・比較）
  - 該当頭数 年間 100 件以上（統計的最低ライン）
  - 1日平均 1〜5 頭（「0〜1 件」も許容）

機能 A（全馬）+ 機能 B（4 番人気以降）を同時集計。
目標ゾーン到達パターンは 4 ヶ月×3 期間の安定性確認も実施。

使用例:
  py -3 scripts/run_v2_combo_search.py --from-date 2025-06-27 --to-date 2026-06-27

既存ロジック（engine.py / conditions.py / 既存戦略 JSON）は一切変更しない。
"""
from __future__ import annotations

import argparse
import itertools
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import tipster.conditions_tr1  # noqa: F401  (CONDITION_REGISTRY 登録のため)
import tipster.conditions_v2   # noqa: F401  (v2_* 条件群登録のため)

from tipster.backtest import (
    _build_date_jockey_places,
    _build_lightweight_context,
    _build_race_groups,
    _build_race_meta,
    _collect_synergy_pairs,
    _fetch_bias_map,
    _fetch_jockey_stats,
    _fetch_non_jra_interim_races,
    _JockeyVenueCache,
    _load_bulk_data,
    _LOOKBACK_DAYS,
    _SynergyCache,
)
from tipster.combo_backtest import _combo_str, _fetch_payouts_bulk
from tipster.conditions import CONDITION_REGISTRY
from tipster.hit_rate_analysis import _fetch_popularity_map

# ─────────────────────────────────────────────────────────────────────────
# 8 条件定義（honmei_v6.json と同じデフォルトパラメータ）
# ─────────────────────────────────────────────────────────────────────────

V2_CONDITIONS: list[tuple[str, dict]] = [
    ("v2_past_margin",     {"lookback": 3, "max_sec": 1.0}),
    ("v2_race_quality",    {"top_n": 3, "min_next_horses": 3, "min_place_rate": 0.35}),
    ("v2_class_change",    {"upgrade_as_none": True}),
    ("v2_distance_match",  {"band_big": 400, "band_margin": 200, "bonus_score": 0.5, "lookback": 3}),
    ("v2_jockey_positive", {"top_jockey_threshold": 30}),
    ("v2_weight_favor",    {}),
    ("v2_interval_optimal", {"optimal_min": 15, "optimal_max": 28}),
    ("v2_surface_history", {}),
]

_COND_IDS = [c[0] for c in V2_CONDITIONS]

# 短縮名（表示用）: インデックス順
_SHORT = [
    "margin",    # 0
    "quality",   # 1
    "class",     # 2
    "dist",      # 3
    "jockey",    # 4
    "weight",    # 5
    "interval",  # 6
    "surface",   # 7
]


# ─────────────────────────────────────────────────────────────────────────
# データロード（既存ヘルパー群をそのまま使用）
# ─────────────────────────────────────────────────────────────────────────

def _build_population(from_date: date, to_date: date) -> dict:
    load_start = from_date - timedelta(days=_LOOKBACK_DAYS)
    bulk_df = _load_bulk_data(load_start, to_date)
    race_groups = _build_race_groups(bulk_df)
    race_meta = _build_race_meta(race_groups)

    target_ids = [
        rid for rid, meta in race_meta.items()
        if meta["is_jra"] and from_date <= meta["date"].date() <= to_date
    ]

    date_jockey_places = _build_date_jockey_places(bulk_df)
    horse_ids = {hid for rid in target_ids for hid in race_groups[rid]["horse_id"].dropna().tolist()}
    jockey_ids = {jid for rid in target_ids for jid in race_groups[rid]["jockey_id"].dropna().tolist()}
    jockey_stats = _fetch_jockey_stats(jockey_ids)
    bias_map = _fetch_bias_map(target_ids, race_meta)
    synergy_cache = _SynergyCache()
    synergy_cache.preload(_collect_synergy_pairs(race_groups, target_ids), load_start, to_date)
    jockey_venue_cache = _JockeyVenueCache()
    jockey_venue_cache.preload(jockey_ids, load_start, to_date)
    non_jra_races = _fetch_non_jra_interim_races(horse_ids, load_start, to_date)
    past_race_cache: dict = {}

    contexts: dict = {}
    for rid in target_ids:
        ctx = _build_lightweight_context(
            rid, race_groups, race_meta, bias_map, synergy_cache,
            date_jockey_places, jockey_stats, past_race_cache,
            jockey_venue_cache, non_jra_races,
        )
        if ctx is not None:
            contexts[rid] = ctx
    return contexts


# ─────────────────────────────────────────────────────────────────────────
# 条件評価の事前一括計算
# ─────────────────────────────────────────────────────────────────────────

def _build_horse_records(
    contexts: dict,
    payout_map: dict,
    popularity_map: dict,
    min_ninki_b: int = 4,
) -> tuple[list, list]:
    """各馬の 8 条件評価を 8-bit マスクで事前計算する。

    戻り値: (records_a, records_b)
      records_*: [(rid, umaban, race_date_str, mask, fukusho_payout_or_none, tansho_payout_or_none, is_na)]
      records_a: 機能A（全馬）
      records_b: 機能B（popularity >= min_ninki_b の馬）
    """
    fns = [(CONDITION_REGISTRY.get(cid), params) for cid, params in V2_CONDITIONS]

    records_a: list = []
    records_b: list = []

    for rid, ctx in contexts.items():
        rpm = payout_map.get(rid)
        pop_map = popularity_map.get(rid, {})

        for horse in ctx.horses:
            # 条件評価（8 ビットマスク）
            mask = 0
            for i, (fn, params) in enumerate(fns):
                if fn is None:
                    continue
                res = fn(horse, ctx, params)
                if res.passed is True:
                    mask |= (1 << i)

            # payout 取得
            is_na = rpm is None or "fukusho" not in rpm
            fukusho_payout = None
            tansho_payout = None
            if not is_na and horse.umaban is not None:
                combo = _combo_str(horse.umaban)
                fukusho_payout = rpm["fukusho"].get(combo)
                tansho_payout = rpm.get("tansho", {}).get(combo)

            rec = (rid, horse.umaban, ctx.race_date, mask, fukusho_payout, tansho_payout, is_na)
            records_a.append(rec)

            pop = pop_map.get(horse.umaban)
            if pop is not None and pop >= min_ninki_b:
                records_b.append(rec)

    return records_a, records_b


# ─────────────────────────────────────────────────────────────────────────
# 1 パターン分の統計集計
# ─────────────────────────────────────────────────────────────────────────

def _calc_stats(
    records: list,
    combo_indices: tuple,
    from_date: date,
    to_date: date,
) -> dict:
    """records から combo_indices に対応するマスクが全て立っている馬を集計する。"""
    required_mask = sum(1 << i for i in combo_indices)
    from_str = from_date.isoformat()
    to_str = to_date.isoformat()

    date_cnt: dict[str, int] = defaultdict(int)
    horse_count = 0
    hit_place = 0
    hit_win = 0
    na_count = 0
    place_return_yen = 0
    win_return_yen = 0
    race_ids: set[str] = set()

    for rid, umaban, race_date, mask, fukusho_p, tansho_p, is_na in records:
        if race_date < from_str or race_date > to_str:
            continue
        if (mask & required_mask) != required_mask:
            continue

        if is_na or umaban is None:
            na_count += 1
            continue

        race_ids.add(rid)
        horse_count += 1
        date_cnt[race_date] += 1

        if fukusho_p is not None:
            hit_place += 1
            place_return_yen += fukusho_p
        if tansho_p is not None:
            hit_win += 1
            win_return_yen += tansho_p

    total_cal_days = (to_date - from_date).days + 1
    days_with_any = len(date_cnt)
    days_zero = total_cal_days - days_with_any
    vals = list(date_cnt.values())
    days_1 = sum(1 for v in vals if v == 1)
    days_2_3 = sum(1 for v in vals if 2 <= v <= 3)
    days_4plus = sum(1 for v in vals if v >= 4)

    n = horse_count
    place_rate = hit_place / n if n else 0.0
    win_rate = hit_win / n if n else 0.0
    place_roi = place_return_yen / (n * 100) if n else 0.0
    win_roi = win_return_yen / (n * 100) if n else 0.0

    return {
        "horse_count": n,
        "hit_place": hit_place,
        "hit_win": hit_win,
        "na_count": na_count,
        "race_count": len(race_ids),
        "place_rate": place_rate,
        "win_rate": win_rate,
        "place_roi": place_roi,
        "win_roi": win_roi,
        "avg_per_day": n / total_cal_days if total_cal_days else 0.0,
        "days_zero": days_zero,
        "days_1": days_1,
        "days_2_3": days_2_3,
        "days_4plus": days_4plus,
    }


# ─────────────────────────────────────────────────────────────────────────
# 全組み合わせ探索
# ─────────────────────────────────────────────────────────────────────────

def _search_all_combos(records: list, from_date: date, to_date: date) -> list[dict]:
    """C(8,5)+C(8,6)+C(8,7) = 92 パターンを全評価して結果リストを返す。"""
    results = []
    for k in (5, 6, 7):
        for combo in itertools.combinations(range(8), k):
            stats = _calc_stats(records, combo, from_date, to_date)
            label = "+".join(_SHORT[i] for i in combo)
            results.append({"combo": combo, "n_conds": k, "label": label, **stats})
    results.sort(key=lambda x: (-x["place_rate"], -x["horse_count"]))
    return results


# ─────────────────────────────────────────────────────────────────────────
# 出力ヘルパー
# ─────────────────────────────────────────────────────────────────────────

_HDR = (
    f"{'N':>2} {'複勝率':>7} {'単勝率':>7} {'複勝ROI':>8} {'単勝ROI':>8} "
    f"{'年間頭':>7} {'レース':>7} {'日均':>5} "
    f"{'0頭日':>5} {'1頭日':>5} {'2-3頭':>6} {'4+頭':>5}  条件"
)
_SEP = "-" * 110


def _fmt_row(r: dict, marker: str = " ") -> str:
    return (
        f"{r['n_conds']:>2} {r['place_rate']:>7.1%} {r['win_rate']:>7.1%} "
        f"{r['place_roi']:>8.1%} {r['win_roi']:>8.1%} "
        f"{r['horse_count']:>7,} {r['race_count']:>7,} {r['avg_per_day']:>5.2f} "
        f"{r['days_zero']:>5} {r['days_1']:>5} {r['days_2_3']:>6} {r['days_4plus']:>5} {marker}{r['label']}"
    )


def _print_section(title: str, results: list, place_thresh: float, win_thresh: float, min_n: int) -> list[dict]:
    print()
    print(f"=== {title} （全{len(results)}パターン、複勝率降順） ===")
    print(_HDR)
    print(_SEP)
    targets = []
    for r in results:
        meets = (
            (r["place_rate"] >= place_thresh or r["win_rate"] >= win_thresh)
            and r["horse_count"] >= min_n
        )
        m = "★ " if meets else "  "
        print(_fmt_row(r, m))
        if meets:
            targets.append(r)
    return targets


def _print_stability(
    title: str, targets: list, records: list,
    from_date: date, to_date: date, top_n: int,
) -> None:
    if not targets:
        print(f"\n[{title}] 目標ゾーン到達パターンなし。")
        return

    total_days = (to_date - from_date).days
    pd_days = total_days // 3
    periods = [
        (from_date, from_date + timedelta(days=pd_days - 1), "P1"),
        (from_date + timedelta(days=pd_days), from_date + timedelta(days=pd_days * 2 - 1), "P2"),
        (from_date + timedelta(days=pd_days * 2), to_date, "P3"),
    ]

    print(f"\n=== {title} — 目標ゾーン {len(targets)}件・安定性確認（4ヶ月×3期間） ===")
    print(f"  P1: {periods[0][0]} 〜 {periods[0][1]}")
    print(f"  P2: {periods[1][0]} 〜 {periods[1][1]}")
    print(f"  P3: {periods[2][0]} 〜 {periods[2][1]}")

    for r in targets[:top_n]:
        print(f"\n  【{r['label']}】({r['n_conds']}条件)")
        print(
            f"    全期間: 複勝{r['place_rate']:.1%}(ROI{r['place_roi']:.1%}) "
            f"単勝{r['win_rate']:.1%}(ROI{r['win_roi']:.1%}) "
            f"{r['horse_count']}頭/{r['race_count']}R 日均{r['avg_per_day']:.2f}頭"
        )
        period_rates = []
        for p_from, p_to, p_lbl in periods:
            ps = _calc_stats(records, r["combo"], p_from, p_to)
            print(
                f"    {p_lbl}({p_from}〜{p_to}): "
                f"複勝{ps['place_rate']:.1%}(ROI{ps['place_roi']:.1%}) "
                f"単勝{ps['win_rate']:.1%}(ROI{ps['win_roi']:.1%}) "
                f"{ps['horse_count']}頭/{ps['race_count']}R"
            )
            period_rates.append(ps["place_rate"])
        if len(period_rates) == 3 and all(r > 0 for r in period_rates):
            spread = max(period_rates) - min(period_rates)
            stability = "安定" if spread <= 0.15 else "不安定"
            print(f"    → 期間間ばらつき: {spread:.1%} [{stability}]")


# ─────────────────────────────────────────────────────────────────────────
# CLI エントリポイント
# ─────────────────────────────────────────────────────────────────────────

def _parse_date(s: str) -> date:
    return date.today() if s == "today" else date.fromisoformat(s)


def _cli() -> None:
    parser = argparse.ArgumentParser(
        description="v2 条件 5/6/7 組み合わせ探索（機能A全馬 + 機能B穴馬）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--from-date", default="2025-06-27")
    parser.add_argument("--to-date", default="today")
    parser.add_argument("--place-threshold", type=float, default=0.60, help="機能A 複勝目標（デフォルト60%%）")
    parser.add_argument("--win-threshold", type=float, default=0.25, help="機能A 単勝目標（デフォルト25%%）")
    parser.add_argument("--min-samples", type=int, default=100, help="年間最低頭数（デフォルト100）")
    parser.add_argument("--top-n", type=int, default=10, help="安定性確認を行う上位N件")
    parser.add_argument("--ninki-b", type=int, default=4, help="機能B 人気順位閾値（デフォルト4）")
    args = parser.parse_args()

    from_date = _parse_date(args.from_date)
    to_date = _parse_date(args.to_date)

    print(f"[v2組み合わせ探索] 期間: {from_date} 〜 {to_date}")
    print(f"[v2組み合わせ探索] 目標A: 複勝率>={args.place_threshold:.0%} OR 単勝率>={args.win_threshold:.0%}, 年間>={args.min_samples}頭")
    print(f"[v2組み合わせ探索] 機能B: {args.ninki_b}番人気以降 複勝率>=25%")
    print("[v2組み合わせ探索] データ読み込み中...")

    contexts = _build_population(from_date, to_date)
    payout_map = _fetch_payouts_bulk(list(contexts.keys()))
    popularity_map = _fetch_popularity_map(list(contexts.keys()))

    print(f"[v2組み合わせ探索] レース数: {len(contexts)} / 条件事前評価中...")

    records_a, records_b = _build_horse_records(contexts, payout_map, popularity_map, args.ninki_b)

    print(f"[v2組み合わせ探索] 機能A 馬レコード数: {len(records_a)} / 機能B({args.ninki_b}番以降): {len(records_b)}")
    print(f"[v2組み合わせ探索] C(8,5)+C(8,6)+C(8,7) = 92 パターン探索開始...")

    # ── 機能 A ──────────────────────────────────────────────────────────
    results_a = _search_all_combos(records_a, from_date, to_date)
    targets_a = _print_section(
        "機能A（全馬）", results_a, args.place_threshold, args.win_threshold, args.min_samples
    )
    _print_stability("機能A", targets_a, records_a, from_date, to_date, args.top_n)

    # ── 機能 B ──────────────────────────────────────────────────────────
    results_b = _search_all_combos(records_b, from_date, to_date)
    # 機能B 目標: 複勝率 25% 以上、頭数 50 件以上
    targets_b = _print_section(
        f"機能B（{args.ninki_b}番人気以降）", results_b,
        place_thresh=0.25, win_thresh=0.15, min_n=50,
    )
    _print_stability(f"機能B({args.ninki_b}番人気以降)", targets_b, records_b, from_date, to_date, args.top_n)

    # ── サマリー ───────────────────────────────────────────────────────
    print()
    print("=== サマリー ===")
    print(f"機能A 目標ゾーン到達: {len(targets_a)}件 / 全92件")
    print(f"機能B 目標ゾーン到達: {len(targets_b)}件 / 全92件")

    if targets_a:
        best = targets_a[0]
        print(
            f"機能A Best: [{best['label']}] "
            f"複勝{best['place_rate']:.1%}(ROI{best['place_roi']:.1%}) "
            f"単勝{best['win_rate']:.1%}(ROI{best['win_roi']:.1%}) "
            f"{best['horse_count']}頭"
        )
    if targets_b:
        best_b = targets_b[0]
        print(
            f"機能B Best: [{best_b['label']}] "
            f"複勝{best_b['place_rate']:.1%}(ROI{best_b['place_roi']:.1%}) "
            f"{best_b['horse_count']}頭"
        )

    print("\n[注意] 上記 ROI は単馬券（全該当馬に各々1点）の概算。")
    print("[注意] 馬連/ワイド回収率は combo_backtest.py による本命×相手選定が別途必要。")


if __name__ == "__main__":
    _cli()
