"""
tipster/hit_rate_analysis.py
==============================
的中率ベースの条件パターン探索（機能A: 鉄板本命探索 / 機能B: 妙味馬探索）の
共通集計ロジック。

設計方針:
  - 既存の tipster.backtest / tipster.combo_backtest / tipster.engine の関数・
    既存戦略JSON・tipster/conditions.py は一切変更しない。本ファイルはそれらを
    呼び出すだけの追加モジュール。
  - 回収率（BET-3/BET-5）ではなく複勝的中率を軸にした、別目的の探索のため新設。
  - オッズの値そのもの（tan_odds）は条件として一切使わない。人気順位
    （race_entries.popularity）は機能B（妙味馬探索）でのみ、フィルタ条件として使用する。
  - 条件パターンは tipster/hit_rate_patterns_config.json（training_ranker_config.json
    同様の設計）で定義し、コード変更なしに追加・編集できる。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import text

from .backtest import (
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
from .combo_backtest import _combo_str, _fetch_payouts_bulk
from .conditions import CONDITION_REGISTRY

_PATTERNS_CONFIG_PATH = Path(__file__).parent / "hit_rate_patterns_config.json"

# 該当頭数がこれ未満のパターンは過学習・偶然の可能性に留意する警告を出す。
_MIN_SAMPLE_WARNING_THRESHOLD = 30


@dataclass(frozen=True)
class PatternConditionSpec:
    """パターン内の条件1件（条件ID + パラメータ）。"""
    id: str
    params: dict


@dataclass(frozen=True)
class HitRatePattern:
    """的中率探索の対象となる条件パターン（複数条件のAND組み合わせ）。"""
    pattern_id: str
    label: str
    conditions: list[PatternConditionSpec]


@dataclass(frozen=True)
class HitRateStats:
    """1パターン分の集計結果。"""
    pattern_id: str
    label: str
    race_count: int        # 該当馬が1頭以上いたレース数
    horse_count: int       # 該当頭数（的中率の集計対象。payoutsデータ欠損馬は含まない）
    hit_count: int         # 複勝的中数
    na_horse_count: int    # payoutsデータ欠損のため集計対象外にした該当頭数
    hit_rate: float
    warning: str | None


def load_patterns_config(path: Path = _PATTERNS_CONFIG_PATH) -> list[HitRatePattern]:
    """hit_rate_patterns_config.json を読み込んで HitRatePattern のリストを返す。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    patterns: list[HitRatePattern] = []
    for p in data["patterns"]:
        conditions = [
            PatternConditionSpec(id=c["id"], params=c.get("params", {}))
            for c in p["conditions"]
        ]
        patterns.append(HitRatePattern(pattern_id=p["id"], label=p["label"], conditions=conditions))
    return patterns


def _build_population(from_date: date, to_date: date) -> dict:
    """tipster.combo_backtest.run_combo_backtest と同じ既存ヘルパー群だけを使って
    対象期間のレース群（race_id -> RaceContext）を構築する（既存関数は無変更）。
    """
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

    contexts = {}
    for rid in target_ids:
        ctx = _build_lightweight_context(
            rid, race_groups, race_meta, bias_map, synergy_cache,
            date_jockey_places, jockey_stats, past_race_cache,
            jockey_venue_cache, non_jra_races,
        )
        if ctx is not None:
            contexts[rid] = ctx
    return contexts


def _fetch_popularity_map(race_ids: list[str]) -> dict[str, dict[int, int]]:
    """race_id -> {umaban: 人気順位} を取得する（機能B用）。

    race_entries.popularity（人気順位、整数）を使う。オッズの値そのもの(win_odds)は
    ここでは取得・使用しない。
    """
    if not race_ids:
        return {}
    from ml.db import engine as _engine

    with _engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT race_id, horse_number, popularity FROM race_entries "
                "WHERE race_id = ANY(:ids) AND popularity IS NOT NULL"
            ),
            {"ids": race_ids},
        ).fetchall()
    result: dict[str, dict[int, int]] = {}
    for race_id, umaban, popularity in rows:
        result.setdefault(race_id, {})[umaban] = popularity
    return result


def compute_hit_rate_for_patterns(
    patterns: list[HitRatePattern],
    from_date: date,
    to_date: date,
    min_ninki: int | None = None,
) -> list[HitRateStats]:
    """各パターンについて、該当馬の複勝的中率を集計する。

    Args:
        patterns: 集計対象のパターンリスト（load_patterns_config()の戻り値等）。
        from_date, to_date: 集計対象期間。
        min_ninki: 指定時、人気順位がmin_ninki以降（数字が大きい=不人気側）の馬のみを
            集計対象とする（機能B: 妙味馬探索）。Noneなら全馬対象（機能A）。
            オッズの値そのものはどのモードでも使用しない。

    パターンの「該当」判定: パターン内の全条件が passed is True を返すこと（AND）。
    passed=None（判定不能・保留）やFalseは「非該当」として扱う（厳格なクリア基準のみを対象とする）。
    """
    contexts = _build_population(from_date, to_date)
    payout_map = _fetch_payouts_bulk(list(contexts.keys()))
    popularity_map = _fetch_popularity_map(list(contexts.keys())) if min_ninki is not None else {}

    results: list[HitRateStats] = []
    for pattern in patterns:
        race_ids_matched: set[str] = set()
        horse_count = 0
        hit_count = 0
        na_horse_count = 0

        for rid, ctx in contexts.items():
            for horse in ctx.horses:
                if min_ninki is not None:
                    pop = popularity_map.get(rid, {}).get(horse.umaban)
                    if pop is None or pop < min_ninki:
                        continue

                matched = True
                for cond_spec in pattern.conditions:
                    fn = CONDITION_REGISTRY.get(cond_spec.id)
                    if fn is None:
                        matched = False
                        break
                    result = fn(horse, ctx, cond_spec.params)
                    if result.passed is not True:
                        matched = False
                        break
                if not matched:
                    continue

                race_ids_matched.add(rid)
                rpm = payout_map.get(rid)
                if rpm is None or "fukusho" not in rpm:
                    na_horse_count += 1
                    continue
                horse_count += 1
                if rpm["fukusho"].get(_combo_str(horse.umaban)) is not None:
                    hit_count += 1

        hit_rate = hit_count / horse_count if horse_count > 0 else 0.0
        warning = None
        if horse_count < _MIN_SAMPLE_WARNING_THRESHOLD:
            warning = (
                f"該当頭数{horse_count}件は閾値{_MIN_SAMPLE_WARNING_THRESHOLD}件未満のため、"
                "過学習・偶然の可能性に留意が必要"
            )

        results.append(HitRateStats(
            pattern_id=pattern.pattern_id, label=pattern.label,
            race_count=len(race_ids_matched), horse_count=horse_count,
            hit_count=hit_count, na_horse_count=na_horse_count,
            hit_rate=hit_rate, warning=warning,
        ))
    return results
