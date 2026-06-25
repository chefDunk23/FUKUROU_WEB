"""
tipster/combo_backtest.py
=========================
本命×相手の組み合わせ（馬連/ワイド/三連複）の回収率検証 (BET-3)。

接続先:
  - fukurou_jvdl (ml.db.engine): races, race_entries, payouts
    payouts.race_id と races.id は共に 12 桁フォーマット
    (kaisai_year(4)+kaisai_monthday(4)+keibajo_code(2)+race_num(2)) のため
    変換不要・直接照合可能。

PLAN.md §5-3 BET-3 Blocker 実装方針:
  - 全回収率出力に race_count (該当レース数) と bet_count (該当ベット数) を
    ComboStats の同じ階層で出力（件数なしの回収率は判断材料にならない）
  - サンプル数が少なくても return_rate を除外・null化しない（可視化優先）
  - 選定組み合わせが payouts に見つからない → 不的中 (return=0)
  - 該当賭式の payouts 行が 0 件 → N/A (na_race_count に計上、0% で誤集計しない)
"""
from __future__ import annotations

import itertools
from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import text

from .backtest import (
    _apply_filters,
    _build_date_jockey_places,
    _build_lightweight_context,
    _build_race_groups,
    _build_race_meta,
    _collect_synergy_pairs,
    _evaluate_full,
    _fetch_bias_map,
    _fetch_jockey_stats,
    _fetch_non_jra_interim_races,
    _finalize_horse,
    _JockeyVenueCache,
    _load_bulk_data,
    _LOOKBACK_DAYS,
    _parse_period_days,
    _SynergyCache,
)
from .engine import load_strategy, select_aite, select_honmei
from .models import ComboBacktestResult, ComboStats, RaceContext, Strategy


# ─────────────────────────────────────────────────────────────────────────
# 組み合わせ文字列生成
# ─────────────────────────────────────────────────────────────────────────


def _combo_str(*umabans: int) -> str:
    """馬番リストを payouts.combination フォーマットに変換する。

    フォーマット: 昇順ソート・ハイフン区切り・各馬番を 2 桁ゼロ埋め
    例: _combo_str(11, 6)     -> '06-11'
        _combo_str(11, 6, 10) -> '06-10-11'
        _combo_str(11)        -> '11'
    """
    return "-".join(f"{u:02d}" for u in sorted(umabans))


def gen_umaren_combos(honmei_umaban: int, aite_umabans: list[int]) -> list[str]:
    """馬連の購入組み合わせ文字列リストを生成する（本命-相手 N 頭で N 点）。"""
    return [_combo_str(honmei_umaban, a) for a in aite_umabans]


def gen_wide_combos(honmei_umaban: int, aite_umabans: list[int]) -> list[str]:
    """ワイドの購入組み合わせ文字列リストを生成する（馬連と同フォーマット）。"""
    return [_combo_str(honmei_umaban, a) for a in aite_umabans]


def gen_sanrenfuku_combos(honmei_umaban: int, aite_umabans: list[int]) -> list[str]:
    """三連複の購入組み合わせ文字列リストを生成する。

    本命+相手 2 頭の組み合わせ: C(len(aite_umabans), 2) 通り。
    相手が 2 頭未満の場合は空リストを返す。
    """
    if len(aite_umabans) < 2:
        return []
    return [
        _combo_str(honmei_umaban, a1, a2)
        for a1, a2 in itertools.combinations(aite_umabans, 2)
    ]


# ─────────────────────────────────────────────────────────────────────────
# payouts 一括取得
# ─────────────────────────────────────────────────────────────────────────

# PLAN.md §5-3 BET-3 Done 条件が要求する 5 賭式 (payouts.bet_type のテキスト値)
_COMBO_BET_TYPES = ("tansho", "fukusho", "umaren", "wide", "sanrenpuku")


def _fetch_payouts_bulk(
    race_ids: list[str],
) -> dict[str, dict[str, dict[str, int]]]:
    """複数 race_id の確定払戻データを一括取得する。

    戻り値: {race_id: {bet_type: {combination: payout}}}

    race_id に対応する payouts 行が 1 件も無い場合（データ欠損・バックフィル未実施など）、
    その race_id のキーは戻り値に含まれない。この不在を N/A 判定に使う。
    payouts.payout は「100 円ベットで得られるリターン（円）」の単位。
    """
    if not race_ids:
        return {}
    from ml.db import engine as _engine

    with _engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT race_id, bet_type, combination, payout "
                "FROM payouts "
                "WHERE race_id = ANY(:ids) AND bet_type = ANY(:types)"
            ),
            {"ids": race_ids, "types": list(_COMBO_BET_TYPES)},
        ).fetchall()

    result: dict[str, dict[str, dict[str, int]]] = {}
    for race_id, bet_type, combination, payout in rows:
        result.setdefault(race_id, {}).setdefault(bet_type, {})[combination] = payout
    return result


# ─────────────────────────────────────────────────────────────────────────
# 集計ヘルパー
# ─────────────────────────────────────────────────────────────────────────


def _new_acc() -> dict[str, dict]:
    """賭式別の集計辞書を初期化する。"""
    return {
        bt: {
            "race_count": 0,
            "bet_count": 0,
            "hit_count": 0,
            "return_amount": 0,
            "na_race_count": 0,
        }
        for bt in _COMBO_BET_TYPES
    }


def _accumulate_stats(
    acc: dict[str, dict],
    honmei_umaban: int,
    aite_umabans: list[int],
    race_payout_map: dict[str, dict[str, int]] | None,
) -> None:
    """1 レース分の回収結果を累積集計辞書 acc に加算する。

    PLAN.md §5-3 BET-3 N/A 判定ルール:
    - race_payout_map is None        → レース全体がデータ欠損 → 全賭式 na_race_count +1
    - race_payout_map[bet_type] なし → その賭式はデータ欠損  → na_race_count +1
    - combo が bet_type payout 内に存在しない → 不的中 (return 0)
    - combo が bet_type payout 内に存在する   → 的中 (return += payout)

    三連複は相手が 2 頭未満では組み合わせが生成できない（combos 空リスト）。
    この場合は payouts データの有無によらずカウントしない（スキップ）。
    """
    combos_by_type: dict[str, list[str]] = {
        "tansho": [_combo_str(honmei_umaban)],
        "fukusho": [_combo_str(honmei_umaban)],
        "umaren": gen_umaren_combos(honmei_umaban, aite_umabans),
        "wide": gen_wide_combos(honmei_umaban, aite_umabans),
        "sanrenpuku": gen_sanrenfuku_combos(honmei_umaban, aite_umabans),
    }

    for bet_type, combos in combos_by_type.items():
        if not combos:
            # 三連複: 相手 0〜1 頭では組み合わせが生成できない → スキップ
            continue
        entry = acc[bet_type]
        if race_payout_map is None:
            # レース全体のデータ欠損
            entry["na_race_count"] += 1
            continue
        type_payouts = race_payout_map.get(bet_type)
        if type_payouts is None:
            # 賭式単位のデータ欠損
            entry["na_race_count"] += 1
            continue
        # payouts データあり → 集計対象
        entry["race_count"] += 1
        entry["bet_count"] += len(combos)
        for combo in combos:
            payout = type_payouts.get(combo)
            if payout is not None:
                entry["hit_count"] += 1
                entry["return_amount"] += payout


def _to_combo_stats(entry: dict) -> ComboStats:
    """集計辞書エントリを ComboStats モデルに変換する。"""
    bet_count = entry["bet_count"]
    return_amount = entry["return_amount"]
    stake = 100 * bet_count
    return ComboStats(
        race_count=entry["race_count"],
        bet_count=bet_count,
        hit_count=entry["hit_count"],
        return_amount=return_amount,
        return_rate=round(return_amount / stake, 4) if stake > 0 else 0.0,
        na_race_count=entry["na_race_count"],
    )


# ─────────────────────────────────────────────────────────────────────────
# バックテスト実行
# ─────────────────────────────────────────────────────────────────────────


def run_combo_backtest(
    honmei_strategy_path: str,
    aite_strategy_path: str,
    reference_date: str = "today",
    periods: list[str] | None = None,
    grade_filter: list[str] | None = None,
    distance_filter: list[str] | None = None,
) -> dict[str, ComboBacktestResult]:
    """本命×相手の組み合わせ回収率バックテストを実行する（BET-3）。

    honmei_strategy_path: 本命選定戦略 JSON のパス（"honmei_v1" 等の短縮名可）
    aite_strategy_path:   相手選定戦略 JSON のパス（"anaba_v1" 等の短縮名可）

    戻り値: {period_label: ComboBacktestResult}
    各 ComboBacktestResult の各賭式 ComboStats に race_count / bet_count が含まれる。

    payouts.payout の単位: 100 円ベットで得られるリターン（円）。
    return_rate = return_amount / (100 × bet_count)
    """
    periods = periods or ["3m", "6m", "1y"]
    honmei_strategy = load_strategy(honmei_strategy_path)
    aite_strategy = load_strategy(aite_strategy_path)

    ref = date.today() if reference_date == "today" else date.fromisoformat(reference_date)
    period_ranges = {p: (ref - timedelta(days=_parse_period_days(p)), ref) for p in periods}
    earliest_start = min(start for start, _ in period_ranges.values())

    load_start = earliest_start - timedelta(days=_LOOKBACK_DAYS)
    bulk_df = _load_bulk_data(load_start, ref)
    race_groups = _build_race_groups(bulk_df)
    race_meta = _build_race_meta(race_groups)

    all_target_ids = [
        rid for rid, meta in race_meta.items()
        if meta["is_jra"] and earliest_start <= meta["date"].date() <= ref
    ]
    all_target_ids = _apply_filters(all_target_ids, race_meta, grade_filter, distance_filter)

    date_jockey_places = _build_date_jockey_places(bulk_df)
    horse_ids = {
        hid
        for rid in all_target_ids
        for hid in race_groups[rid]["horse_id"].dropna().tolist()
    }
    jockey_ids = {
        jid
        for rid in all_target_ids
        for jid in race_groups[rid]["jockey_id"].dropna().tolist()
    }
    jockey_stats = _fetch_jockey_stats(jockey_ids)
    bias_map = _fetch_bias_map(all_target_ids, race_meta)
    synergy_cache = _SynergyCache()
    synergy_cache.preload(
        _collect_synergy_pairs(race_groups, all_target_ids), load_start, ref
    )
    jockey_venue_cache = _JockeyVenueCache()
    jockey_venue_cache.preload(jockey_ids, load_start, ref)
    non_jra_races = _fetch_non_jra_interim_races(horse_ids, load_start, ref)
    past_race_cache: dict = {}

    contexts: dict[str, RaceContext] = {}
    for rid in all_target_ids:
        ctx = _build_lightweight_context(
            rid, race_groups, race_meta, bias_map, synergy_cache,
            date_jockey_places, jockey_stats, past_race_cache,
            jockey_venue_cache, non_jra_races,
        )
        if ctx is not None:
            contexts[rid] = ctx

    # payouts を全ターゲットレースについて一括取得
    # races.id == payouts.race_id（共に 12 桁フォーマット）のため変換不要
    payout_map = _fetch_payouts_bulk(list(contexts.keys()))

    honmei_cfgs = [c for c in honmei_strategy.conditions if c.enabled]
    aite_cfgs = [c for c in aite_strategy.conditions if c.enabled]
    # 戦略ごとに full_cache を分ける（条件セットが異なるため共用不可）
    honmei_full_cache: dict = {}
    aite_full_cache: dict = {}

    results: dict[str, ComboBacktestResult] = {}
    for p, (start, end) in period_ranges.items():
        period_ids = [
            rid for rid in contexts if start <= race_meta[rid]["date"].date() <= end
        ]
        acc = _new_acc()
        skipped = 0

        for rid in period_ids:
            ctx = contexts[rid]
            if not ctx.horses:
                skipped += 1
                continue

            # ── 本命評価 ──────────────────────────────────────────────
            if rid not in honmei_full_cache:
                honmei_full_cache[rid] = _evaluate_full(ctx, honmei_cfgs)
            honmei_full = honmei_full_cache[rid]
            honmei_results = [
                _finalize_horse(h.horse_id, honmei_full[h.horse_id], None)
                for h in ctx.horses
            ]
            honmei_candidates = [r for r in honmei_results if not r.eliminated]
            umaban_map = {h.horse_id: h.umaban for h in ctx.horses}
            honmei = select_honmei(
                honmei_candidates,
                umaban_map,
                honmei_strategy.ranking.min_total_score,
                honmei_strategy.ranking.max_candidates_for_honmei,
            )
            if honmei is None:
                skipped += 1
                continue

            honmei_umaban = umaban_map.get(honmei.horse_id)
            if honmei_umaban is None:
                skipped += 1
                continue

            # ── 相手評価 ──────────────────────────────────────────────
            if rid not in aite_full_cache:
                aite_full_cache[rid] = _evaluate_full(ctx, aite_cfgs)
            aite_full = aite_full_cache[rid]
            aite_results = [
                _finalize_horse(h.horse_id, aite_full[h.horse_id], None)
                for h in ctx.horses
            ]
            aite_candidates = [r for r in aite_results if not r.eliminated]
            # 相手戦略のランキング順（条件クリア数→合計スコア→AIスコア→馬番）でソート
            aite_candidates_sorted = sorted(
                aite_candidates,
                key=lambda c: (
                    -c.clear_count,
                    -c.total_score,
                    -c.ai_score,
                    umaban_map.get(c.horse_id, 9999) or 9999,
                ),
            )
            aite_list = select_aite(
                aite_candidates_sorted,
                honmei.horse_id,
                aite_strategy.ranking.max_selections,
            )
            if not aite_list:
                skipped += 1
                continue

            aite_umabans = [
                umaban_map[a.horse_id]
                for a in aite_list
                if umaban_map.get(a.horse_id) is not None
            ]
            if not aite_umabans:
                skipped += 1
                continue

            # payouts.get(rid) が None → データ欠損（N/A）
            _accumulate_stats(acc, honmei_umaban, aite_umabans, payout_map.get(rid))

        results[p] = ComboBacktestResult(
            honmei_strategy=honmei_strategy.name,
            aite_strategy=aite_strategy.name,
            from_date=start.isoformat(),
            to_date=end.isoformat(),
            period_label=p,
            total_races=len(period_ids),
            skipped_races=skipped,
            tansho=_to_combo_stats(acc["tansho"]),
            fukusho=_to_combo_stats(acc["fukusho"]),
            umaren=_to_combo_stats(acc["umaren"]),
            wide=_to_combo_stats(acc["wide"]),
            sanrenfuku=_to_combo_stats(acc["sanrenfuku"]),
            generated_at=datetime.now().isoformat(timespec="seconds"),
        )

    return results


# ─────────────────────────────────────────────────────────────────────────
# CLI エントリポイント
# ─────────────────────────────────────────────────────────────────────────


def _cli() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="本命×相手 組み合わせ回収率バックテスト (BET-3)")
    parser.add_argument("--honmei-strategy", default="honmei_v1")
    parser.add_argument("--aite-strategy", default="anaba_v1")
    parser.add_argument("--reference-date", default="today")
    parser.add_argument("--periods", default="3m,6m,1y")
    parser.add_argument("--grade-filter", default=None, help="例: A,B,C")
    parser.add_argument("--distance-filter", default=None, help="例: sprint,mile")
    args = parser.parse_args()

    periods = [p.strip() for p in args.periods.split(",") if p.strip()]
    grade_filter = [g.strip() for g in args.grade_filter.split(",")] if args.grade_filter else None
    distance_filter = [d.strip() for d in args.distance_filter.split(",")] if args.distance_filter else None

    results = run_combo_backtest(
        args.honmei_strategy,
        args.aite_strategy,
        reference_date=args.reference_date,
        periods=periods,
        grade_filter=grade_filter,
        distance_filter=distance_filter,
    )

    for p, r in results.items():
        print(
            f"\n[{p}] {r.from_date}~{r.to_date} "
            f"対象{r.total_races}レース(スキップ{r.skipped_races})"
        )
        for label, stats in [
            ("単勝  ", r.tansho),
            ("複勝  ", r.fukusho),
            ("馬連  ", r.umaren),
            ("ワイド", r.wide),
            ("三連複", r.sanrenfuku),
        ]:
            print(
                f"  {label}: 回収率={stats.return_rate:.1%} "
                f"レース数={stats.race_count} ベット数={stats.bet_count} "
                f"的中={stats.hit_count} N/A={stats.na_race_count}"
            )


if __name__ == "__main__":
    _cli()
