"""
anaba_ai/models/sub_models.py
==============================
5本のサブモデル（LightGBM binary）を A 期間で GroupKFold 学習し、
OOF スコアを返す。

各サブモデルは「1着 vs 非1着」を binary classification で学習する。
→ OOFスコア = その馬の「勝ちやすさ」のサブモデル評価

特徴量重要度警告:
    単一特徴量が重要度 IMPORTANCE_DOMINATE_THRESHOLD (30%) 以上を占める場合、
    市場織り込み済み情報への依存の兆候として警告を出力する。
"""
from __future__ import annotations

import logging
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.model_selection import GroupKFold

from ..config import (
    CV_FOLDS,
    EARLY_STOPPING,
    IMPORTANCE_DOMINATE_THRESHOLD,
    LGBM_SUBMODEL_PARAMS,
    MODEL_DIR,
    NUM_BOOST_ROUND,
    SUBMODEL_DEFS,
)
from ..pipeline import get_feature_cols

log = logging.getLogger(__name__)


def _group_sizes(groups: pd.Series) -> np.ndarray:
    """レース単位のグループサイズ配列（LightGBM lambdarank 用、ここでは使わないが共通化）。"""
    arr = groups.to_numpy()
    boundaries = np.concatenate([[0], np.where(arr[:-1] != arr[1:])[0] + 1, [len(arr)]])
    return np.diff(boundaries).astype(np.int32)


def _check_importance_dominance(model: lgb.Booster, name: str) -> dict[str, float]:
    """特徴量重要度を計算し、単一支配があれば警告する。"""
    imp = model.feature_importance(importance_type="gain")
    feat_names = model.feature_name()
    total = imp.sum()
    if total == 0:
        return {}

    imp_dict = {f: float(v / total) for f, v in zip(feat_names, imp)}
    top_feat, top_share = max(imp_dict.items(), key=lambda x: x[1])

    if top_share >= IMPORTANCE_DOMINATE_THRESHOLD:
        log.warning(
            "[%s] 特徴量支配警告: '%s' が重要度の %.1f%% を占めています "
            "(閾値 %.0f%%) — 市場織り込み済み情報の可能性",
            name, top_feat, top_share * 100, IMPORTANCE_DOMINATE_THRESHOLD * 100,
        )
    else:
        log.info("[%s] 重要度分散 OK: top='%s' (%.1f%%)", name, top_feat, top_share * 100)

    return imp_dict


def train_submodel(
    name: str,
    df_train: pd.DataFrame,
    save_dir: Path,
) -> tuple[np.ndarray, list[dict[str, float]]]:
    """
    1サブモデルを GroupKFold(n=5) で学習する。

    Args:
        name      : サブモデル名（SUBMODEL_DEFS の name と一致）
        df_train  : A 期間の DataFrame（race_date で既にフィルタ済み）
        save_dir  : モデル保存先ディレクトリ

    Returns:
        oof_scores : OOF スコアの ndarray（df_train と同じ長さ）
        fold_importances : fold ごとの特徴量重要度リスト
    """
    save_dir.mkdir(parents=True, exist_ok=True)

    feat_cols = get_feature_cols(name, df_train)
    if not feat_cols:
        raise ValueError(f"[{name}] 有効な特徴量カラムが 0 件です")

    log.info("[%s] 学習開始: %d行 × %d特徴量", name, len(df_train), len(feat_cols))

    # 対象行: 1着かどうかのラベル
    valid_mask = df_train["kakutei_chakujun"].notna() & (df_train["kakutei_chakujun"] > 0)
    df_t = df_train[valid_mask].copy()
    X = df_t[feat_cols].copy()
    y = (df_t["kakutei_chakujun"] == 1).astype(int)
    groups = df_t["race_id"]

    # 数値変換
    for col in X.columns:
        X[col] = pd.to_numeric(X[col], errors="coerce")

    oof_scores = np.full(len(df_t), np.nan)
    fold_importances: list[dict[str, float]] = []

    gkf = GroupKFold(n_splits=CV_FOLDS)
    for fold_idx, (tr_idx, va_idx) in enumerate(gkf.split(X, y, groups), start=1):
        X_tr, X_va = X.iloc[tr_idx], X.iloc[va_idx]
        y_tr, y_va = y.iloc[tr_idx], y.iloc[va_idx]

        dtrain = lgb.Dataset(X_tr, label=y_tr)
        dval   = lgb.Dataset(X_va, label=y_va, reference=dtrain)

        model = lgb.train(
            LGBM_SUBMODEL_PARAMS,
            dtrain,
            num_boost_round=NUM_BOOST_ROUND,
            valid_sets=[dval],
            callbacks=[
                lgb.early_stopping(EARLY_STOPPING, verbose=False),
                lgb.log_evaluation(100),
            ],
        )

        oof_scores[va_idx] = model.predict(X_va)

        auc = model.best_score["valid_0"]["auc"]
        log.info("[%s] Fold %d: AUC=%.4f @ iter=%d", name, fold_idx, auc, model.best_iteration)

        imp = _check_importance_dominance(model, f"{name}_fold{fold_idx}")
        fold_importances.append(imp)

        # 最終fold のモデルを保存（推論用）
        model_path = save_dir / f"{name}_fold{fold_idx}.lgb"
        model.save_model(str(model_path))

    nan_pct = np.isnan(oof_scores).mean() * 100
    log.info("[%s] OOF完了: NaN率=%.1f%%", name, nan_pct)

    # OOFスコアを元の df_train に対応させる
    full_oof = np.full(len(df_train), np.nan)
    full_oof[valid_mask.values] = oof_scores

    return full_oof, fold_importances


def train_all_submodels(
    df_A: pd.DataFrame,
    save_dir: Path | None = None,
) -> pd.DataFrame:
    """
    全5サブモデルを A 期間で学習し、OOF スコアを df_A に追加して返す。

    Returns:
        df_A に score_speed_v1 … score_breed_v1 の 5 列が追加された DataFrame
    """
    if save_dir is None:
        save_dir = MODEL_DIR / "submodels"

    df_out = df_A.copy()
    all_importances: dict[str, list[dict[str, float]]] = {}

    for sdef in SUBMODEL_DEFS:
        name = sdef["name"]
        sub_dir = save_dir / name

        oof, imps = train_submodel(name, df_A, sub_dir)
        df_out[f"score_{name}"] = oof
        all_importances[name] = imps

    # 重要度サマリーを集計（fold平均）
    df_out.attrs["submodel_importances"] = all_importances
    return df_out


def predict_submodels(
    df: pd.DataFrame,
    model_dir: Path | None = None,
    fold: int = 1,
) -> pd.DataFrame:
    """
    学習済みサブモデルで推論し、score_{name} 列を追加して返す。

    Args:
        df        : 推論対象 DataFrame
        model_dir : モデル保存ディレクトリ（デフォルト: MODEL_DIR/submodels）
        fold      : 使用するフォールド番号（デフォルト: fold 1）
    """
    if model_dir is None:
        model_dir = MODEL_DIR / "submodels"

    df_out = df.copy()

    for sdef in SUBMODEL_DEFS:
        name = sdef["name"]
        feat_cols = get_feature_cols(name, df)

        model_path = model_dir / name / f"{name}_fold{fold}.lgb"
        if not model_path.exists():
            log.warning("[%s] モデルファイルが見つかりません: %s", name, model_path)
            df_out[f"score_{name}"] = np.nan
            continue

        model = lgb.Booster(model_file=str(model_path))
        X = df[feat_cols].copy()
        for col in X.columns:
            X[col] = pd.to_numeric(X[col], errors="coerce")

        df_out[f"score_{name}"] = model.predict(X)
        log.info("[%s] 推論完了: %d行", name, len(df))

    return df_out
