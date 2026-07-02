"""
scripts/enrich_ability_v3.py
============================
rich_features_2022plus.parquet に ability_features_v3 の特徴量
（直近フォーム Phase 1 + クラス補正 Phase 2）を追加する。

生成される特徴量 (9列):
    grade_value           今走グレード数値 (1〜10)
    prev1_rank            前走着順
    avg_rank_3            直近3走平均着順
    avg_rank_5            直近5走平均着順
    recent_win_rate_5     直近5走勝率
    recent_fukusho_rate_5 直近5走複勝率
    max_grade_won         過去最高グレード勝利
    class_win_rate        同クラス過去勝率
    prev1_rank_class_adj  前走クラス補正着順

Usage:
    py -3.13 scripts/enrich_ability_v3.py
    py -3.13 scripts/enrich_ability_v3.py \\
        --in  outputs/rich_features_2022plus.parquet \\
        --out outputs/rich_features_v3_2022plus.parquet

カラム名マッピング:
    Parquet の kakutei_chakujun → ability_features_v3 の confirmed_rank
    出力 Parquet は元の kakutei_chakujun をそのまま保持する。
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.features.ability_features_v3 import create_ability_features_v3

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

_DEFAULT_IN  = Path("outputs/rich_features_2022plus.parquet")
_DEFAULT_OUT = Path("outputs/rich_features_v3_2022plus.parquet")

_ABILITY_V3_COLS: list[str] = [
    "grade_value",
    "prev1_rank",
    "avg_rank_3",
    "avg_rank_5",
    "recent_win_rate_5",
    "recent_fukusho_rate_5",
    "max_grade_won",
    "class_win_rate",
    "prev1_rank_class_adj",
]


def enrich(in_path: Path, out_path: Path) -> None:
    log.info("入力Parquet読み込み: %s", in_path)
    df = pd.read_parquet(in_path)
    log.info("  %d行 / %dレース / %d特徴量", len(df), df["race_id"].nunique(), len(df.columns))

    # ability_features_v3 は confirmed_rank カラムを要求するが
    # 学習Parquetでは kakutei_chakujun という列名を使っている。
    # assign で confirmed_rank を追加したビューを渡し、元の列名は変更しない。
    if "confirmed_rank" not in df.columns:
        if "kakutei_chakujun" not in df.columns:
            log.error("kakutei_chakujun / confirmed_rank のどちらも見つかりません")
            sys.exit(1)
        df_in = df.assign(confirmed_rank=df["kakutei_chakujun"])
    else:
        df_in = df

    log.info("ability_features_v3 生成中...")
    df_enriched = create_ability_features_v3(df_in)

    # 既存列の上書き防止: まだ存在しない列だけ追加
    for col in _ABILITY_V3_COLS:
        if col in df.columns:
            log.warning("列 %r は既に存在します — 上書きします", col)
        df[col] = df_enriched[col].to_numpy()

    log.info("=== ability_v3 特徴量 NaN率 ===")
    for col in _ABILITY_V3_COLS:
        nan_pct = df[col].isna().mean() * 100
        log.info("  %-35s %5.1f%%", col, nan_pct)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)
    log.info(
        "出力: %s  (%d行 / %d特徴量)",
        out_path, len(df), len(df.columns),
    )
    log.info("次のステップ: py -3.13 scripts/train_v2_submodels.py --parquet %s", out_path)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="rich_features Parquetに ability_v3 特徴量を追加する"
    )
    p.add_argument(
        "--in",
        dest="in_path",
        type=Path,
        default=_DEFAULT_IN,
        help=f"入力Parquetパス（デフォルト: {_DEFAULT_IN}）",
    )
    p.add_argument(
        "--out",
        dest="out_path",
        type=Path,
        default=_DEFAULT_OUT,
        help=f"出力Parquetパス（デフォルト: {_DEFAULT_OUT}）",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    if not args.in_path.exists():
        log.error("入力Parquetが見つかりません: %s", args.in_path)
        sys.exit(1)
    enrich(args.in_path, args.out_path)
