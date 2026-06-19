"""
scripts/enrich_pace_sim.py
============================
展開シミュレーション特徴量（predicted_position_norm / predicted_field_pace /
pace_harmony_pre）を既存の Parquet に追記する。

前提:
    pace_features_v4.py が実行済みで avg_c1_norm_5 が Parquet に存在すること。
    （bloodline_features_v1_2022plus.parquet など最終マージ済み Parquet が対象）

Usage:
    py -3.13 scripts/enrich_pace_sim.py
    py -3.13 scripts/enrich_pace_sim.py --parquet outputs/bloodline_features_v1_2022plus.parquet
    py -3.13 scripts/enrich_pace_sim.py --parquet outputs/bloodline_features_v1_2022plus.parquet --out outputs/pace_sim_features_2022plus.parquet
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

from src.features.pace_simulation_v1 import PACE_SIM_COLS, create_pace_simulation_features

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

_DEFAULT_PARQUET = Path("outputs/bloodline_features_v1_2022plus.parquet")


def main(parquet_path: Path, out_path: Path | None) -> None:
    if not parquet_path.exists():
        log.error("Parquet が見つかりません: %s", parquet_path)
        sys.exit(1)

    log.info("Parquet 読み込み: %s", parquet_path)
    df = pd.read_parquet(parquet_path)
    log.info("  shape=%s  races=%d", df.shape, df["race_id"].nunique())

    if "avg_c1_norm_5" not in df.columns:
        log.error(
            "avg_c1_norm_5 が見つかりません。先に pace_features_v4.py を実行してください。"
        )
        sys.exit(1)

    # 既存の sim 列を上書きするため事前に削除
    existing = [c for c in PACE_SIM_COLS if c in df.columns]
    if existing:
        log.info("  既存の sim 列を上書き: %s", existing)
        df = df.drop(columns=existing)

    log.info("展開シミュレーション特徴量を計算中...")
    df = create_pace_simulation_features(df)

    # 統計サマリー
    for col in PACE_SIM_COLS:
        s = df[col]
        log.info(
            "  %-30s mean=%.3f  std=%.3f  min=%.3f  max=%.3f  NaN=%d",
            col, s.mean(), s.std(), s.min(), s.max(), s.isna().sum(),
        )

    effective_out = out_path or parquet_path
    log.info("Parquet 保存: %s  shape=%s", effective_out, df.shape)
    df.to_parquet(effective_out, index=False)
    log.info("完了")
    log.info(
        "次のステップ: py -3.13 scripts/train_v2_submodels.py "
        "--parquet %s --submodel pace_v2",
        effective_out,
    )


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="展開シミュレーション特徴量を Parquet に追記")
    p.add_argument(
        "--parquet", type=Path, default=_DEFAULT_PARQUET,
        help=f"入力 Parquet（デフォルト: {_DEFAULT_PARQUET}）",
    )
    p.add_argument(
        "--out", type=Path, default=None,
        help="出力 Parquet（省略時は入力を上書き）",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    main(args.parquet, args.out)
