"""
前走メンバーレベルAIサブモデルの学習・推論モジュール。

layer2_model.py と同じ LightGBM lambdarank パターンを使用。
v1 モデルとは完全に独立した別モデルとして管理する。
"""
from __future__ import annotations

from pace_bias_ai.models.layer2_model import (
    DEFAULT_FOLDS,
    DEFAULT_LGB_PARAMS,
    CATEGORICAL_FEATURES,
    FoldResult,
    compute_filter_metrics,
    compute_random_baseline,
    compute_shap_importance,
    walk_forward_oof,
    train_full_model,
)

# opponent_model 用のカテゴリ変数（layer2 と共通で良い）
OPPONENT_CATEGORICAL: list[str] = ["dist_cat", "surface_code"]

__all__ = [
    "DEFAULT_FOLDS",
    "DEFAULT_LGB_PARAMS",
    "CATEGORICAL_FEATURES",
    "OPPONENT_CATEGORICAL",
    "FoldResult",
    "compute_filter_metrics",
    "compute_random_baseline",
    "compute_shap_importance",
    "walk_forward_oof",
    "train_full_model",
]
