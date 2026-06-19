"""
scripts/enrich_pace_v4.py
==========================
rich_features_v3_2022plus.parquet に pace_features_v4 の特徴量
（頭数正規化 + 距離区分別脚質 + 馬場別上がり適性）を追加する。

v3 との違い:
    - コーナー順位を頭数で正規化 (0.0=先頭, 1.0=最後尾)
    - 距離区分別 (スプリント/マイル/中距離/長距離) の脚質特徴量を追加
    - 芝/ダート別の上がり3F順位を生成

生成される特徴量 (20列):
    avg_c1_norm_5              直近5走の1コーナー正規化順位平均
    avg_c4_norm_5              直近5走の4コーナー正規化順位平均
    avg_pos_advance_norm_5     直近5走の正規化順位変化平均 (正=追い込み)
    running_style_std_norm_5   直近5走の1コーナー正規化順位の標準偏差
    avg_c1_norm_5_{sprint/mile/mid/long}  距離区分別 1コーナー
    avg_c4_norm_5_{sprint/mile/mid/long}  距離区分別 4コーナー
    avg_pos_advance_norm_5_{sprint/mile/mid/long}  距離区分別順位変化
    avg_go3f_rank_5_turf       直近5走 (芝) の上がり3F順位平均
    go3f_rank_std_5_turf       直近5走 (芝) の上がり3F順位標準偏差
    avg_go3f_rank_5_dirt       直近5走 (ダート) の上がり3F順位平均
    go3f_rank_std_5_dirt       直近5走 (ダート) の上がり3F順位標準偏差

入力: outputs/rich_features_v3_2022plus.parquet  (ability_v3 追加済み)
出力: outputs/pace_features_v4_2022plus.parquet  (v3 を置き換える学習最終入力)

Usage:
    py -3.13 scripts/enrich_pace_v4.py
    py -3.13 scripts/enrich_pace_v4.py --in outputs/rich_features_v3_2022plus.parquet
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

from src.features.pace_features_v4 import PACE_V4_COLS, create_pace_features_v4

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

_DEFAULT_IN  = Path("outputs/rich_features_v3_2022plus.parquet")
_DEFAULT_OUT = Path("outputs/pace_features_v4_2022plus.parquet")

# v3 生成の特徴量はここで廃止する（頭数非正規化の旧バージョン）
_V3_DEPRECATED = [
    "avg_c1_pos_5",
    "avg_c4_pos_5",
    "avg_pos_advance_5",
    "running_style_std_5",
    "avg_go3f_rank_5",
    "go3f_rank_std_5",
]


def enrich(in_path: Path, out_path: Path) -> None:
    log.info("入力Parquet読み込み: %s", in_path)
    df = pd.read_parquet(in_path)
    log.info("  %d行 / %dレース / %d特徴量", len(df), df["race_id"].nunique(), len(df.columns))

    # v3 特徴量を廃止（存在する場合のみ）
    dropped = [c for c in _V3_DEPRECATED if c in df.columns]
    if dropped:
        df = df.drop(columns=dropped)
        log.info("  v3 特徴量廃止: %s", dropped)

    log.info("pace_features_v4 生成中...")
    df_enriched = create_pace_features_v4(df)

    for col in PACE_V4_COLS:
        if col in df.columns:
            log.warning("列 %r は既に存在します — 上書きします", col)
        df[col] = df_enriched[col].to_numpy()

    log.info("=== pace_v4 特徴量 NaN率 ===")
    for col in PACE_V4_COLS:
        nan_pct = df[col].isna().mean() * 100
        log.info("  %-42s %5.1f%%", col, nan_pct)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)
    log.info(
        "出力: %s  (%d行 / %d特徴量)",
        out_path, len(df), len(df.columns),
    )
    log.info("次のステップ: py -3.13 scripts/train_v2_submodels.py --parquet %s", out_path)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="rich_features_v3 Parquetに pace_v4 特徴量を追加する"
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
