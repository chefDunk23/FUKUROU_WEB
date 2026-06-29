"""
anaba_ai/models/meta_model.py
==============================
残差学習メタモデル（LightGBM regression）。

入力: A 期間で学習したサブモデル 5 本の B 期間 OOF スコア
目的変数: residual = y_actual - p_market
        = 「実際の勝率 − 市場の予測確率」
        > 0 → 市場が過小評価した馬（穴馬候補）
        < 0 → 市場が正しかった or 過大評価した馬

出力: anaba_score ∈ (-1, 1) の残差予測値
"""
from __future__ import annotations

import logging
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from ..config import (
    EARLY_STOPPING,
    LGBM_META_PARAMS,
    MODEL_DIR,
    NUM_BOOST_ROUND,
    SUBMODEL_DEFS,
)

log = logging.getLogger(__name__)

SCORE_COLS = [f"score_{d['name']}" for d in SUBMODEL_DEFS]
META_MODEL_FILE = "meta_model.lgb"


def _validate_score_cols(df: pd.DataFrame) -> list[str]:
    """DataFrameにサブモデルスコア列が存在するか確認。"""
    available = [c for c in SCORE_COLS if c in df.columns]
    missing   = [c for c in SCORE_COLS if c not in df.columns]
    if missing:
        raise KeyError(f"サブモデルスコア列が不足しています: {missing}")
    return available


def train_meta_model(
    df_B_with_scores: pd.DataFrame,
    save_dir: Path | None = None,
) -> lgb.Booster:
    """
    B 期間データ（サブモデルスコア付き）でメタモデルを学習する。

    B 期間の df にはすでにサブモデルスコア（predict_submodels で付与）が
    入っている前提。

    Args:
        df_B_with_scores : B 期間 DataFrame（score_{name} 列を含む）
        save_dir         : モデル保存先

    Returns:
        学習済みメタモデル（lgb.Booster）
    """
    if save_dir is None:
        save_dir = MODEL_DIR / "meta"
    save_dir.mkdir(parents=True, exist_ok=True)

    score_cols = _validate_score_cols(df_B_with_scores)

    # 残差が計算できる行のみ
    valid_mask = (
        df_B_with_scores["residual"].notna()
        & df_B_with_scores[score_cols].notna().all(axis=1)
    )
    df_t = df_B_with_scores[valid_mask].copy()

    if len(df_t) == 0:
        raise ValueError("メタモデル学習データが 0 件です。スコア計算を確認してください。")

    X = df_t[score_cols].copy()
    for col in X.columns:
        X[col] = pd.to_numeric(X[col], errors="coerce")

    y = df_t["residual"].astype(float)

    log.info("メタモデル学習開始: %d行 × %d特徴量", len(X), X.shape[1])
    log.info("残差統計: mean=%.4f std=%.4f min=%.4f max=%.4f",
             y.mean(), y.std(), y.min(), y.max())

    # GroupKFold で early stopping（B 期間内でのみ）
    from sklearn.model_selection import GroupKFold
    groups = df_t["race_id"]
    gkf = GroupKFold(n_splits=5)
    tr_idx, va_idx = next(gkf.split(X, y, groups))  # 1-fold だけで early stopping 境界検出

    dtrain = lgb.Dataset(X.iloc[tr_idx], label=y.iloc[tr_idx])
    dval   = lgb.Dataset(X.iloc[va_idx], label=y.iloc[va_idx], reference=dtrain)

    model = lgb.train(
        LGBM_META_PARAMS,
        dtrain,
        num_boost_round=NUM_BOOST_ROUND,
        valid_sets=[dval],
        callbacks=[
            lgb.early_stopping(EARLY_STOPPING, verbose=False),
            lgb.log_evaluation(100),
        ],
    )

    rmse = model.best_score["valid_0"]["rmse"]
    log.info("メタモデル学習完了: RMSE=%.4f @ iter=%d", rmse, model.best_iteration)

    # 特徴量重要度（サブモデル寄与度）
    imp = model.feature_importance(importance_type="gain")
    total = imp.sum()
    log.info("メタモデル サブモデル寄与度:")
    for feat, val in sorted(zip(score_cols, imp), key=lambda x: -x[1]):
        log.info("  %-25s %.1f%%", feat, val / total * 100)

    # 保存
    model_path = save_dir / META_MODEL_FILE
    model.save_model(str(model_path))
    log.info("メタモデル保存: %s", model_path)

    return model


def predict_anaba_score(
    df: pd.DataFrame,
    model_dir: Path | None = None,
) -> pd.Series:
    """
    学習済みメタモデルで anaba_score を予測する。

    Returns:
        anaba_score Series（df と同インデックス）。
        値が高い ＝ 市場が過小評価している可能性が高い馬。
    """
    if model_dir is None:
        model_dir = MODEL_DIR / "meta"

    model_path = model_dir / META_MODEL_FILE
    if not model_path.exists():
        raise FileNotFoundError(f"メタモデルが見つかりません: {model_path}")

    model = lgb.Booster(model_file=str(model_path))
    score_cols = _validate_score_cols(df)

    X = df[score_cols].copy()
    for col in X.columns:
        X[col] = pd.to_numeric(X[col], errors="coerce")

    scores = model.predict(X)
    return pd.Series(scores, index=df.index, name="anaba_score")


def get_submodel_importances(model_dir: Path | None = None) -> dict[str, float]:
    """メタモデルからサブモデル寄与度（gain重要度の割合）を返す。"""
    if model_dir is None:
        model_dir = MODEL_DIR / "meta"

    model = lgb.Booster(model_file=str(model_dir / META_MODEL_FILE))
    imp = model.feature_importance(importance_type="gain")
    feat_names = model.feature_name()
    total = imp.sum()
    return {f: float(v / total) for f, v in zip(feat_names, imp)}
