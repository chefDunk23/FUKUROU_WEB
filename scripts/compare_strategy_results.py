"""
scripts/compare_strategy_results.py
=====================================
BET-5: 複数の実験結果 JSON ファイルを読み込み、4 賭式の回収率を並べて比較するスクリプト。

出力規約 (PLAN.md BET-3/BET-5 共通):
  - 全回収率に「該当レース数」「該当ベット数（購入点数合計）」を同じ行で出力する。
  - 検証対象賭式: 単勝・複勝・馬連・ワイド（三連複は表示対象外）。

使用例:
  # ディレクトリ内の全 JSON を自動検出して比較
  py -3 scripts/compare_strategy_results.py \\
    --results-dir data/output/tipster/backtest_results

  # 特定のファイルのみ比較（同一期間の複数戦略を並べる場合など）
  py -3 scripts/compare_strategy_results.py \\
    --result-files \\
      data/output/tipster/backtest_results/honmei_v1__anaba_v1__3m_2026-06-26.json \\
      data/output/tipster/backtest_results/honmei_v3__anaba_v2__3m_2026-06-26.json

  # 特定の賭式・期間のみ絞り込んで比較
  py -3 scripts/compare_strategy_results.py \\
    --results-dir data/output/tipster/backtest_results \\
    --bet-types tansho,umaren --period-filter 3m

PLAN.md §3 BET-5 Done 条件:
  - 2 つ以上の戦略パターンを切り替えて同一レース群の 4 賭式回収率を並べて比較できること。
  - 比較のために Python コードを変更する必要がないこと（戦略ファイルの指定変更のみ）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

sys.path.insert(0, str(Path(__file__).parent.parent))

from tipster.models import ComboBacktestResult, ComboStats

# 検証対象 4 賭式（PLAN.md BET-5: 三連複は対象外）
_BET_TYPES_4 = ("tansho", "fukusho", "umaren", "wide")

_BET_LABELS: dict[str, str] = {
    "tansho": "単勝  ",
    "fukusho": "複勝  ",
    "umaren": "馬連  ",
    "wide": "ワイド",
}

_DEFAULT_RESULTS_DIR = (
    Path(__file__).parent.parent / "data" / "output" / "tipster" / "backtest_results"
)


def load_result(json_path: Path) -> ComboBacktestResult:
    """JSON ファイルを ComboBacktestResult としてロードする。"""
    return ComboBacktestResult.model_validate_json(json_path.read_text(encoding="utf-8"))


def collect_result_files(
    results_dir: Path | None = None,
    result_files: Sequence[str] | None = None,
) -> list[Path]:
    """結果 JSON ファイルのリストを収集する。

    results_dir と result_files の両方が指定された場合は両方を合算する。
    """
    paths: list[Path] = []
    if results_dir and results_dir.is_dir():
        paths.extend(sorted(results_dir.glob("*.json")))
    if result_files:
        for f in result_files:
            p = Path(f)
            if p not in paths:
                paths.append(p)
    return paths


def get_combo_stats(result: ComboBacktestResult, bet_type: str) -> ComboStats:
    """ComboBacktestResult から指定 bet_type の ComboStats を取得する。"""
    field_map = {
        "tansho": result.tansho,
        "fukusho": result.fukusho,
        "umaren": result.umaren,
        "wide": result.wide,
        "sanrenfuku": result.sanrenfuku,
    }
    return field_map[bet_type]


def format_stats(stats: ComboStats) -> str:
    """ComboStats を「回収率 / レース数R / ベット数B / 的中N」形式の文字列に変換する。

    出力規約: 回収率と同じ行に race_count / bet_count を必ず表示する（PLAN.md BET-3/BET-5）。
    """
    return (
        f"{stats.return_rate:.1%}"
        f" / {stats.race_count}R"
        f" / {stats.bet_count}B"
        f" / {stats.hit_count}的中"
    )


def build_strategy_label(result: ComboBacktestResult) -> str:
    """結果ファイルから戦略ペアのラベル文字列を生成する。"""
    return f"{result.honmei_strategy} × {result.aite_strategy}"


def print_comparison_table(
    results: list[ComboBacktestResult],
    bet_types: Sequence[str] = _BET_TYPES_4,
    period_filter: str | None = None,
) -> None:
    """複数の結果を賭式別・戦略別に並べて表示する。

    各行に回収率・レース数・ベット数・的中数を必ず表示する（PLAN.md BET-5 出力規約）。
    三連複（sanrenfuku）はデフォルトで表示対象外（bet_types に含めない限り非表示）。

    Args:
        results:       比較対象の ComboBacktestResult リスト
        bet_types:     表示する賭式リスト（デフォルト: 単勝・複勝・馬連・ワイド）
        period_filter: 期間ラベルで絞り込む場合に指定（例: "3m"）
    """
    if not results:
        print("比較対象の結果がありません。")
        return

    filtered = results
    if period_filter:
        filtered = [r for r in results if r.period_label == period_filter]
        if not filtered:
            print(f"期間 '{period_filter}' に該当する結果がありません。")
            return

    # 表示する期間ラベルの一意リスト（ファイル読み込み順を維持）
    seen_periods: list[str] = []
    for r in filtered:
        if r.period_label not in seen_periods:
            seen_periods.append(r.period_label)

    # ヘッダー
    header_line = "=" * 100
    print(header_line)
    print("BET-5 戦略比較レポート")
    print(f"検証対象賭式: {' / '.join(_BET_LABELS.get(bt, bt) for bt in bet_types)}")
    print("出力規約: 回収率 / レース数 / ベット数 / 的中数（件数なしの回収率は掲載しない）")
    print(header_line)

    for period in seen_periods:
        period_results = [r for r in filtered if r.period_label == period]
        if not period_results:
            continue

        # 期間ヘッダー（代表的な日付範囲を表示）
        ref = period_results[0]
        print(f"\n【期間: {period} ({ref.from_date} 〜 {ref.to_date})】")
        print(f"{'賭式':<8}  {'戦略ペア':<28}  {'回収率':>7}  {'レース数':>7}  {'ベット数':>8}  {'的中':>6}  {'N/A':>5}")
        print("-" * 90)

        for bt in bet_types:
            label = _BET_LABELS.get(bt, bt)
            for result in period_results:
                stats = get_combo_stats(result, bt)
                strategy_label = build_strategy_label(result)
                print(
                    f"{label:<8}  "
                    f"{strategy_label:<28}  "
                    f"{stats.return_rate:>7.1%}  "
                    f"{stats.race_count:>7}  "
                    f"{stats.bet_count:>8}  "
                    f"{stats.hit_count:>6}  "
                    f"{stats.na_race_count:>5}"
                )

    print("\n" + header_line)
    print(f"合計 {len(filtered)} 件の結果を表示しました。")


def _cli() -> None:
    parser = argparse.ArgumentParser(
        description="BET-5: 複数の実験結果 JSON を読み込んで 4 賭式回収率を比較するスクリプト",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "例:\n"
            "  py -3 scripts/compare_strategy_results.py "
            "--results-dir data/output/tipster/backtest_results\n"
            "  py -3 scripts/compare_strategy_results.py "
            "--result-files result1.json result2.json\n"
        ),
    )
    parser.add_argument(
        "--results-dir",
        default=None,
        help=f"結果 JSON を含むディレクトリ (デフォルト: {_DEFAULT_RESULTS_DIR})",
    )
    parser.add_argument(
        "--result-files",
        nargs="+",
        default=None,
        help="比較対象の JSON ファイルを直接指定（複数可）",
    )
    parser.add_argument(
        "--bet-types",
        default="tansho,fukusho,umaren,wide",
        help="表示する賭式（カンマ区切り。デフォルト: 4 賭式）",
    )
    parser.add_argument(
        "--period-filter",
        default=None,
        help="期間ラベルで絞り込む (例: 3m / 6m / 1y)",
    )
    args = parser.parse_args()

    results_dir = Path(args.results_dir) if args.results_dir else None
    if results_dir is None and args.result_files is None:
        # デフォルト: 標準出力ディレクトリを使用
        results_dir = _DEFAULT_RESULTS_DIR

    bet_types = [bt.strip() for bt in args.bet_types.split(",") if bt.strip()]

    json_paths = collect_result_files(results_dir, args.result_files)
    if not json_paths:
        print("比較対象の JSON ファイルが見つかりません。")
        print(f"  --results-dir または --result-files を指定してください。")
        sys.exit(1)

    results: list[ComboBacktestResult] = []
    for p in json_paths:
        try:
            results.append(load_result(p))
        except Exception as e:
            print(f"  警告: {p.name} の読み込みに失敗しました: {e}", file=sys.stderr)

    if not results:
        print("有効な結果ファイルがありません。")
        sys.exit(1)

    print_comparison_table(results, bet_types=bet_types, period_filter=args.period_filter)


if __name__ == "__main__":
    _cli()
