"""
scripts/verify_data_split.py
==============================
学習用 Parquet ファイルに検証データ期間（EVAL_START_DATE 以降）のレースが
含まれていないか検証する。

BET-4 Done条件:
  検証データ期間のレースIDが学習データ生成スクリプトの入力に含まれていないことを
  コードレビュー+データ検証スクリプトで確認できること（PLAN.md §5-3）。

分割境界（shared.config で一元管理）:
  学習データ: race_date <= TRAIN_END_DATE (2025-05-31)
  検証データ: race_date >= EVAL_START_DATE (2025-06-01)

Usage:
    # 通常チェック（警告表示のみ、exit code は常に 0）
    py -3 scripts/verify_data_split.py --parquet outputs/bloodline_features_v1_2022plus.parquet

    # 厳格チェック（リーク検出時に exit code 1）
    py -3 scripts/verify_data_split.py --parquet outputs/bloodline_features_v1_2022plus.parquet --strict
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd

from shared.config import EVAL_START_DATE, TRAIN_END_DATE


def _race_id_to_date_str(race_id: str) -> str:
    """race_id の先頭8文字（YYYYMMDD）を YYYY-MM-DD 形式に変換する。

    12桁（payouts 形式）・16桁（races_v2 形式）いずれにも対応。
    先頭8文字が kaisai_year(4) + kaisai_monthday(4) で共通。
    """
    raw = str(race_id)[:8]
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"


def verify_no_eval_leakage(
    df: pd.DataFrame,
    eval_start: str = EVAL_START_DATE,
) -> dict:
    """DataFrame 中に eval_start 以降の race_id が含まれていないか確認する。

    Args:
        df: "race_id" 列を含む DataFrame（学習用 Parquet）
        eval_start: 検証データ開始日（YYYY-MM-DD 形式）

    Returns:
        dict:
            total_rows:       DataFrame の総行数
            total_races:      ユニーク race_id 数
            eval_rows:        eval_start 以降の行数（リーク件数）
            leaked_race_ids:  リーク race_id のサンプル（最大10件）
            passed:           リークが 0 件なら True
    """
    eval_start_raw = eval_start.replace("-", "")  # "20250601"
    race_ids = df["race_id"].astype(str)
    dates_raw = race_ids.str[:8]
    leak_mask = dates_raw >= eval_start_raw
    leaked = df.loc[leak_mask, "race_id"].unique().tolist()
    return {
        "total_rows": len(df),
        "total_races": int(df["race_id"].nunique()),
        "eval_rows": int(leak_mask.sum()),
        "leaked_race_ids": leaked[:10],
        "passed": len(leaked) == 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="学習用 Parquet のデータ分割リーク検証（BET-4）"
    )
    parser.add_argument(
        "--parquet",
        required=True,
        type=Path,
        help="検証する Parquet ファイルパス",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="リーク検出時に exit code 1 で終了する（CI/CD 向け）",
    )
    args = parser.parse_args()

    if not args.parquet.exists():
        print(f"[ERROR] Parquet が見つかりません: {args.parquet}", file=sys.stderr)
        return 1

    print(f"分割境界 (shared.config): 学習 ~ {TRAIN_END_DATE} / 検証 {EVAL_START_DATE} ~")
    print(f"検証ファイル: {args.parquet}")

    df = pd.read_parquet(args.parquet, columns=["race_id"])
    result = verify_no_eval_leakage(df)

    print(f"総行数      : {result['total_rows']:,}")
    print(f"総レース数  : {result['total_races']:,}")
    print(f"検証期間({EVAL_START_DATE}以降)の行数: {result['eval_rows']:,}")

    if result["passed"]:
        print(f"[PASS] 検証データ期間({EVAL_START_DATE}以降)のリーク: 0 件 ✓")
        return 0

    print(
        f"[FAIL] リーク検出: {result['eval_rows']} 行 / "
        f"race_id サンプル: {result['leaked_race_ids']}"
    )
    if args.strict:
        return 1
    print("[WARNING] --strict 未指定のため exit code は 0 で続行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
