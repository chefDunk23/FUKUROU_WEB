"""
scripts/run_strategy_experiment.py
===================================
BET-5: 本命×相手の組み合わせバックテスト結果を JSON ファイルに保存する実験管理スクリプト。

既存の tipster/combo_backtest.py・engine.py・select_honmei()/select_aite() は変更しない。
本スクリプトは run_combo_backtest() を呼び出してその結果を永続化するだけの薄いラッパー。

使用例:
  # honmei_v1 × anaba_v1 の 3ヶ月・6ヶ月・1年の結果を保存
  py -3 scripts/run_strategy_experiment.py \\
    --honmei-strategy honmei_v1 --aite-strategy anaba_v1

  # honmei_v3 × anaba_v2 の 3ヶ月の結果を保存（新戦略バリアントとの比較用）
  py -3 scripts/run_strategy_experiment.py \\
    --honmei-strategy honmei_v3 --aite-strategy anaba_v2 --periods 3m

  # グレード絞り込み（芝のG3以上）
  py -3 scripts/run_strategy_experiment.py \\
    --honmei-strategy honmei_v1 --aite-strategy anaba_v1 \\
    --periods 6m --grade-filter A,B

出力先: data/output/tipster/backtest_results/{honmei}__{aite}__{period}_{YYYY-MM-DD}.json
  - 同一戦略ペア・同一期間ラベルを異なる日付で複数回実行しても上書きされない（日付サフィックス）
  - compare_strategy_results.py でこれらのファイルを読み込んで比較する

PLAN.md §3 BET-5 実装方針:
  - 既存ファイルを変更しない（新規 JSON / スクリプトのみ追加）
  - MLflow 等の外部ツールは使用しない
  - 戦略ファイルの指定変更だけで異なるパターンを試行・記録できること
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tipster.combo_backtest import run_combo_backtest
from tipster.models import ComboBacktestResult

_DEFAULT_OUTPUT_DIR = (
    Path(__file__).parent.parent / "data" / "output" / "tipster" / "backtest_results"
)


def save_experiment(
    honmei_strategy: str,
    aite_strategy: str,
    reference_date: str = "today",
    periods: list[str] | None = None,
    grade_filter: list[str] | None = None,
    distance_filter: list[str] | None = None,
    output_dir: Path = _DEFAULT_OUTPUT_DIR,
) -> list[Path]:
    """バックテストを実行し、結果を JSON ファイルとして出力ディレクトリに保存する。

    Args:
        honmei_strategy: 本命戦略名または JSON パス（"honmei_v1" 等の短縮名可）
        aite_strategy:   相手戦略名または JSON パス（"anaba_v1" 等の短縮名可）
        reference_date:  基準日 ("today" または "YYYY-MM-DD")
        periods:         期間リスト（["3m", "6m", "1y"] 等）
        grade_filter:    グレードフィルタ（["A", "B"] 等、None で全グレード）
        distance_filter: 距離フィルタ（["sprint", "mile"] 等、None で全距離）
        output_dir:      保存先ディレクトリ（存在しない場合は自動作成）

    Returns:
        保存したファイルパスのリスト（期間ごとに 1 ファイル）

    出力規約 (PLAN.md BET-3/BET-5 共通):
        各 ComboBacktestResult の各 ComboStats に race_count / bet_count が含まれる。
        検証対象 4 賭式（単勝・複勝・馬連・ワイド）の件数が JSON に保存される。
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    results: dict[str, ComboBacktestResult] = run_combo_backtest(
        honmei_strategy_path=honmei_strategy,
        aite_strategy_path=aite_strategy,
        reference_date=reference_date,
        periods=periods,
        grade_filter=grade_filter,
        distance_filter=distance_filter,
    )

    today_str = date.today().isoformat()
    saved_paths: list[Path] = []

    for period_label, result in results.items():
        filename = f"{honmei_strategy}__{aite_strategy}__{period_label}_{today_str}.json"
        out_path = output_dir / filename
        out_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        saved_paths.append(out_path)
        print(f"  保存: {out_path.name}")

    return saved_paths


def _cli() -> None:
    parser = argparse.ArgumentParser(
        description="BET-5: 本命×相手バックテスト結果を JSON として保存する実験管理スクリプト",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "例:\n"
            "  py -3 scripts/run_strategy_experiment.py "
            "--honmei-strategy honmei_v1 --aite-strategy anaba_v1\n"
            "  py -3 scripts/run_strategy_experiment.py "
            "--honmei-strategy honmei_v3 --aite-strategy anaba_v2 --periods 3m\n"
        ),
    )
    parser.add_argument("--honmei-strategy", required=True, help="本命戦略名 (例: honmei_v1)")
    parser.add_argument("--aite-strategy", required=True, help="相手戦略名 (例: anaba_v1)")
    parser.add_argument("--reference-date", default="today", help="基準日 (YYYY-MM-DD or 'today')")
    parser.add_argument("--periods", default="3m,6m,1y", help="期間リスト (カンマ区切り)")
    parser.add_argument("--grade-filter", default=None, help="グレードフィルタ (例: A,B)")
    parser.add_argument("--distance-filter", default=None, help="距離フィルタ (例: sprint,mile)")
    parser.add_argument(
        "--output-dir",
        default=str(_DEFAULT_OUTPUT_DIR),
        help=f"結果保存ディレクトリ (デフォルト: {_DEFAULT_OUTPUT_DIR})",
    )
    args = parser.parse_args()

    periods = [p.strip() for p in args.periods.split(",") if p.strip()]
    grade_filter = (
        [g.strip() for g in args.grade_filter.split(",")]
        if args.grade_filter
        else None
    )
    distance_filter = (
        [d.strip() for d in args.distance_filter.split(",")]
        if args.distance_filter
        else None
    )
    output_dir = Path(args.output_dir)

    print(
        f"[BET-5] 実験実行: {args.honmei_strategy} × {args.aite_strategy} "
        f"/ 期間: {periods}"
    )
    saved = save_experiment(
        honmei_strategy=args.honmei_strategy,
        aite_strategy=args.aite_strategy,
        reference_date=args.reference_date,
        periods=periods,
        grade_filter=grade_filter,
        distance_filter=distance_filter,
        output_dir=output_dir,
    )
    print(f"[BET-5] 完了: {len(saved)} ファイルを保存しました → {output_dir}")


if __name__ == "__main__":
    _cli()
