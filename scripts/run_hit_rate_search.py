"""
scripts/run_hit_rate_search.py
================================
的中率ベースの条件パターン探索（回収率ではなく複勝的中率を軸にする新規分析軸）。

機能A: 鉄板本命探索 — --min-ninki を指定しない（全馬対象）。条件パターンを厳しく
       組み合わせ、高い複勝的中率を示すパターンを探す。
機能B: 妙味馬探索 — --min-ninki 4 等を指定すると、人気順位がそれ以降（不人気側）の
       馬のみに絞って集計する。オッズの値そのものは使わず、人気順位のみ使用する。

既存の tipster/combo_backtest.py・tipster/training_ranker.py・tipster/engine.py・
既存戦略JSON・tipster/conditions.py は一切変更しない。

使用例:
  # 機能A: 鉄板本命探索（全馬、直近1年）
  py -3 scripts/run_hit_rate_search.py --from-date 2025-06-26 --to-date today

  # 機能B: 妙味馬探索（4番人気以降、直近1年）
  py -3 scripts/run_hit_rate_search.py --from-date 2025-06-26 --to-date today --min-ninki 4
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import tipster.conditions_tr1  # noqa: F401  (training_rank_top を CONDITION_REGISTRY に登録するためimport)
import tipster.conditions_v2   # noqa: F401  (v2_* 条件群を CONDITION_REGISTRY に登録するためimport)
from tipster.hit_rate_analysis import compute_hit_rate_for_patterns, load_patterns_config

_DEFAULT_CONFIG = Path(__file__).parent.parent / "tipster" / "hit_rate_patterns_config.json"


def _parse_date(s: str) -> date:
    if s == "today":
        return date.today()
    return date.fromisoformat(s)


def _cli() -> None:
    parser = argparse.ArgumentParser(
        description="的中率ベースの条件パターン探索（鉄板本命探索/妙味馬探索）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--patterns-config", default=str(_DEFAULT_CONFIG), help="パターン定義JSON")
    parser.add_argument("--from-date", required=True, help="集計開始日 YYYY-MM-DD")
    parser.add_argument("--to-date", default="today", help="集計終了日 YYYY-MM-DD or 'today'")
    parser.add_argument(
        "--min-ninki", type=int, default=None,
        help="指定時、人気順位がこの値以降(数字が大きい=不人気)の馬のみ集計（機能B: 妙味馬探索）。"
             "未指定なら全馬対象（機能A: 鉄板本命探索）。オッズの値そのものは使用しない。",
    )
    args = parser.parse_args()

    from_date = _parse_date(args.from_date)
    to_date = _parse_date(args.to_date)
    patterns = load_patterns_config(Path(args.patterns_config))

    mode = f"機能B 妙味馬探索（人気{args.min_ninki}番以降）" if args.min_ninki else "機能A 鉄板本命探索（全馬）"
    print(f"[的中率探索] モード: {mode}")
    print(f"[的中率探索] 期間: {from_date} 〜 {to_date} / パターン数: {len(patterns)}")

    results = compute_hit_rate_for_patterns(patterns, from_date, to_date, min_ninki=args.min_ninki)

    print()
    print(f"{'パターンID':<22}{'複勝的中率':>10}{'該当レース数':>12}{'該当頭数':>10}{'的中数':>8}{'N/A頭数':>9}")
    print("-" * 90)
    for r in results:
        print(
            f"{r.pattern_id:<22}{r.hit_rate:>10.1%}{r.race_count:>12}"
            f"{r.horse_count:>10}{r.hit_count:>8}{r.na_horse_count:>9}"
        )
        print(f"  └ {r.label}")
        if r.warning:
            print(f"  [警告] {r.warning}")


if __name__ == "__main__":
    _cli()
