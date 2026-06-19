"""
scripts/enrich_course_v3.py
============================
pace_features_v4_2022plus.parquet に course_features_v3 の特徴量
（Phase 1 + Phase 2 + Phase 3）を追加する。

生成される特徴量 (16列):
  [Phase 1: 競馬場直接適性]
    apt_venue_starts          今回の競馬場での出走回数（当走前）
    apt_venue_win_rate_5      今回の競馬場での直近5走勝率
    apt_venue_avg_rank_5      今回の競馬場での直近5走平均着順
    apt_venue_fukusho_rate_5  今回の競馬場での直近5走複勝率
  [Phase 2: EG × コース物理特性]
    eg_flat_avg10             平坦コースでの期待値超過（人気−着順）直近10走平均
    eg_steep_avg10            坂ありコースでの期待値超過 直近10走平均
    eg_turn_L_avg10           左回りでの期待値超過 直近10走平均
    eg_turn_R_avg10           右回りでの期待値超過 直近10走平均
    eg_steep_minus_flat       坂適性ギャップ（急坂EG − 平坦EG）
    agari_flat_avg10          平坦コースでの上がり順位 直近10走平均
    agari_steep_avg10         坂ありコースでの上がり順位 直近10走平均
  [Phase 3: ローテーション条件替わり]
    rot_straight_delta        前走比 最終直線距離差（m）
    rot_turn_switch           前走と回り方向が変わったか（0/1）
    rot_slope_shift           前走比 坂カテゴリ差（-2〜+2）
    rot_distance_delta        前走比 距離差（m）
    rot_is_new_venue          今回の競馬場が初出走か（0/1）

入力: outputs/pace_features_v4_2022plus.parquet
出力: outputs/course_features_v3_2022plus.parquet

Usage:
    py -3.13 scripts/enrich_course_v3.py
    py -3.13 scripts/enrich_course_v3.py --in outputs/pace_features_v4_2022plus.parquet
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

from src.features.course_features_v3 import COURSE_V3_COLS, create_course_features_v3

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

_DEFAULT_IN  = Path("outputs/pace_features_v4_2022plus.parquet")
_DEFAULT_OUT = Path("outputs/course_features_v3_2022plus.parquet")


def enrich(in_path: Path, out_path: Path) -> None:
    log.info("入力Parquet読み込み: %s", in_path)
    df = pd.read_parquet(in_path)
    log.info("  %d行 / %dレース / %d列", len(df), df["race_id"].nunique(), len(df.columns))

    # confirmed_rank がなければ kakutei_chakujun から作成（訓練データ用）
    if "confirmed_rank" not in df.columns:
        df["confirmed_rank"] = pd.to_numeric(df["kakutei_chakujun"], errors="coerce")

    log.info("course_features_v3 生成中（Phase 1 + Phase 3）...")
    df_enriched = create_course_features_v3(df)

    existing = [c for c in COURSE_V3_COLS if c in df.columns]
    if existing:
        log.warning("既存列を上書きします: %s", existing)

    for col in COURSE_V3_COLS:
        df[col] = df_enriched[col].to_numpy()

    log.info("=== course_v3 特徴量 NaN率 ===")
    for col in COURSE_V3_COLS:
        nan_pct = df[col].isna().mean() * 100
        log.info("  %-40s %5.1f%%", col, nan_pct)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)
    log.info(
        "出力: %s  (%d行 / %d列)",
        out_path, len(df), len(df.columns),
    )
    log.info(
        "次のステップ: py -3.13 scripts/train_v2_submodels.py --parquet %s", out_path
    )


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="pace_v4 Parquetに course_v3 特徴量を追加する"
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
