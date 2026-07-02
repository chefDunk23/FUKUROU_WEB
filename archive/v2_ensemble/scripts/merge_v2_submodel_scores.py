"""
scripts/merge_v2_submodel_scores.py
=====================================
V2 サブモデル OOF スコア（6列）を rich_features Parquet にマージし、
スタックアンサンブル学習用の新 Parquet を出力する。

入力:
    outputs/bloodline_features_v1_2022plus.parquet — 元の学習データ（最終エンリッチ済み）
    models/v2/submodels/oof_scores_v2.parquet      — train_v2_submodels.py の出力

出力:
    outputs/v2_stacked_features.parquet          — 6 サブモデルスコア列を追加したもの

Usage:
    py -3.13 scripts/merge_v2_submodel_scores.py
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

_SRC_PARQUET  = Path("outputs/bloodline_features_v1_2022plus.parquet")
_OOF_PARQUET  = Path("models/v2/submodels/oof_scores_v2.parquet")
_OUT_PARQUET  = Path("outputs/v2_stacked_features.parquet")

_SCORE_COLS = [
    "score_ability_v2",
    "score_course_v2",
    "score_team_v2",
    "score_training_v2",
    "score_pace_v2",
    "score_pedigree_v1",
]


def main() -> None:
    if not _SRC_PARQUET.exists():
        log.error("元 Parquet が見つかりません: %s", _SRC_PARQUET)
        sys.exit(1)
    if not _OOF_PARQUET.exists():
        log.error("OOF Parquet が見つかりません: %s  (先に train_v2_submodels.py を実行してください)", _OOF_PARQUET)
        sys.exit(1)

    log.info("元 Parquet 読み込み: %s", _SRC_PARQUET)
    df = pd.read_parquet(_SRC_PARQUET)
    log.info("  shape=%s", df.shape)

    log.info("OOF スコア読み込み: %s", _OOF_PARQUET)
    oof = pd.read_parquet(_OOF_PARQUET)
    log.info("  shape=%s  cols=%s", oof.shape, list(oof.columns))

    # race_id × horse_id でジョイン
    merge_cols = ["race_id", "horse_id"] + [c for c in _SCORE_COLS if c in oof.columns]
    missing_score_cols = [c for c in _SCORE_COLS if c not in oof.columns]
    if missing_score_cols:
        log.warning("OOF Parquet に存在しないスコア列: %s", missing_score_cols)

    oof_subset = oof[merge_cols].copy()

    # race_id, horse_id の型を合わせる（str に統一）
    df["race_id"]    = df["race_id"].astype(str)
    df["horse_id"]   = df["horse_id"].astype(str)
    oof_subset["race_id"]  = oof_subset["race_id"].astype(str)
    oof_subset["horse_id"] = oof_subset["horse_id"].astype(str)

    before_len = len(df)
    merged = df.merge(oof_subset, on=["race_id", "horse_id"], how="left")
    assert len(merged) == before_len, f"マージ後行数が変わりました: {before_len} → {len(merged)}"

    # マージ結果確認
    for col in [c for c in _SCORE_COLS if c in merged.columns]:
        nan_count = merged[col].isna().sum()
        nan_pct   = nan_count / len(merged) * 100
        log.info("  %-30s  NaN=%d (%.1f%%)", col, nan_count, nan_pct)

    _OUT_PARQUET.parent.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(_OUT_PARQUET, index=False)
    log.info("出力 Parquet 保存: %s  shape=%s", _OUT_PARQUET, merged.shape)
    log.info("次のステップ: py -3.13 -m src.models.v2.train outputs/v2_stacked_features.parquet")


if __name__ == "__main__":
    main()
