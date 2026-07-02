"""
scripts/train_v2_ensemble.py
==============================
v2_stacked_features.parquet からメインアンサンブル（LightGBM LambdaRank）を
GroupKFold で学習し、models/v2/ensemble/ に配置する。

前提:
    merge_v2_submodel_scores.py の実行済みであること。
    → outputs/v2_stacked_features.parquet が存在すること。

学習の流れ:
    1. outputs/v2_stacked_features.parquet を読み込む
    2. score_* 6列を特徴量に LambdaRank 学習（GroupKFold × 5）
    3. fold モデルを outputs/v2/models/ に保存
    4. outputs/v2/models/lgbm_rank_fold*.lgb → models/v2/ensemble/ にコピー
       （API は models/v2/ensemble/ を参照するため）

Usage:
    py -3.13 scripts/train_v2_ensemble.py
    py -3.13 scripts/train_v2_ensemble.py --parquet outputs/v2_stacked_features.parquet
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.models.v2.config import FEATURES_SUBMODEL
from src.models.v2.train import train

_ENSEMBLE_CONFIG = _ROOT / "config" / "ensemble_config.json"

# 全サブモデルスコア列（デフォルト）
_ALL_SUBMODEL_SCORES = list(FEATURES_SUBMODEL)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

_DEFAULT_PARQUET = Path("outputs/v2_stacked_features.parquet")
_TRAIN_MODEL_DIR = Path("outputs/v2/models")       # src.models.v2.train の出力先
_DEPLOY_DIR      = Path("models/v2/ensemble")       # API がロードするパス（shared/config.py）


def _deploy_models(deploy_dir: Path) -> int:
    """学習済み fold モデルを指定パスへコピーし、コピーした本数を返す。"""
    fold_files = sorted(_TRAIN_MODEL_DIR.glob("lgbm_rank_fold*.lgb"))
    if not fold_files:
        log.error("学習済みモデルが見つかりません: %s", _TRAIN_MODEL_DIR)
        return 0

    deploy_dir.mkdir(parents=True, exist_ok=True)
    for src in fold_files:
        dst = deploy_dir / src.name
        shutil.copy2(src, dst)
        log.info("  デプロイ: %s → %s", src.name, deploy_dir)

    return len(fold_files)


def _build_active_scores(exclude: list[str]) -> list[str]:
    """除外リストから有効なサブモデルスコア列を返す。

    exclude には short name（例: training_v2）または full name（score_training_v2）を受け付ける。
    """
    exclude_set: set[str] = set()
    for name in exclude:
        exclude_set.add(name if name.startswith("score_") else f"score_{name}")
    unknown = exclude_set - set(_ALL_SUBMODEL_SCORES)
    if unknown:
        log.warning("未知のサブモデル名を無視します: %s", sorted(unknown))
    return [s for s in _ALL_SUBMODEL_SCORES if s not in exclude_set]


def main(parquet_path: Path, exclude: list[str], deploy_dir: Path | None = None) -> None:
    if not parquet_path.exists():
        log.error(
            "Parquet が見つかりません: %s\n"
            "  先に merge_v2_submodel_scores.py を実行してください。",
            parquet_path,
        )
        sys.exit(1)

    active_scores = _build_active_scores(exclude)
    excluded_scores = [s for s in _ALL_SUBMODEL_SCORES if s not in active_scores]

    if not active_scores:
        log.error("有効なサブモデルスコアがありません（全て除外されました）")
        sys.exit(1)

    log.info("有効サブモデル (%d): %s", len(active_scores), active_scores)
    if excluded_scores:
        log.info("除外サブモデル (%d): %s", len(excluded_scores), excluded_scores)

    # アンサンブル設定を保存（compute_backtest_v2.py が参照）
    config_data = {
        "active_submodel_scores": active_scores,
        "excluded_submodel_scores": excluded_scores,
        "all_submodel_scores": _ALL_SUBMODEL_SCORES,
    }
    _ENSEMBLE_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    with open(_ENSEMBLE_CONFIG, "w", encoding="utf-8") as f:
        json.dump(config_data, f, ensure_ascii=False, indent=2)
    log.info("アンサンブル設定保存: %s", _ENSEMBLE_CONFIG)

    log.info("=== アンサンブル学習開始: %s ===", parquet_path)
    train(parquet_path, mode="rank", feature_override=active_scores)

    effective_deploy_dir = deploy_dir or _DEPLOY_DIR
    log.info("=== モデルデプロイ: %s → %s ===", _TRAIN_MODEL_DIR, effective_deploy_dir)
    n = _deploy_models(effective_deploy_dir)
    if n == 0:
        sys.exit(1)

    log.info("完了: %d fold モデルを %s に配置しました", n, effective_deploy_dir)
    log.info("API サーバーを再起動して新モデルを反映してください。")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="V2 メインアンサンブル学習 → models/v2/ensemble/ に配置"
    )
    p.add_argument(
        "--parquet",
        type=Path,
        default=_DEFAULT_PARQUET,
        help=f"学習 Parquet（デフォルト: {_DEFAULT_PARQUET}）",
    )
    p.add_argument(
        "--exclude-submodels",
        nargs="*",
        default=[],
        metavar="SUBMODEL",
        dest="exclude_submodels",
        help=(
            "除外するサブモデル名（short: training_v2 / full: score_training_v2）。"
            f"指定可能: {', '.join(_ALL_SUBMODEL_SCORES)}"
        ),
    )
    p.add_argument(
        "--deploy-dir",
        type=Path,
        default=None,
        dest="deploy_dir",
        help=f"デプロイ先ディレクトリ（デフォルト: {_DEPLOY_DIR}）",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    main(args.parquet, exclude=args.exclude_submodels, deploy_dir=args.deploy_dir)
