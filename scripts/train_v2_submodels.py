"""
scripts/train_v2_submodels.py
==============================
V2 スタックアンサンブル用サブモデル 6本 を Parquet から学習する。

各サブモデルは binary classification（単勝予測）で学習し、
OOF スコアを race_id × horse_id キーで Parquet に保存する。

Usage:
    py -3.13 scripts/train_v2_submodels.py
    py -3.13 scripts/train_v2_submodels.py --parquet outputs/rich_features_2022plus.parquet
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.model_selection import GroupKFold

# ── パス解決 ─────────────────────────────────────────────────────────────────
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.models.submodel_registry import SubmodelManager
from src.models.v2.config import (
    CV_FOLDS,
    EARLY_STOPPING_ROUNDS,
    GRADE_CODE_MAP,
    LGBM_PARAMS_BINARY,
    NUM_BOOST_ROUND,
    NUMERIC_CODE_COLS,
)
from src.models.v2.dataset import _prepare_numerics

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

_DEFAULT_PARQUET = Path("outputs/rich_features_2022plus.parquet")
_SUBMODEL_BASE   = Path("models/submodels/v2")

# ── サブモデル定義 ────────────────────────────────────────────────────────────
#   name: 保存ディレクトリ名 & スコアカラム名 (score_{name})
#   features: 使用する特徴量カラム名リスト

SUBMODEL_DEFS: list[dict] = [
    {
        "name": "ability_v2",
        "description": "馬の基礎能力（レーティング + 過去戦績）",
        "features": [
            "pre_race_rating",
            "feature_past_starts",
            "feature_past_wins",
            "feature_past_top3",
            "feature_past_win_rate",
            "feature_past_fukusho_rate",
        ],
    },
    {
        "name": "course_v2",
        "description": "コース適性（コース物理特性 + 適性スコア）",
        "features": [
            "straight_dist",
            "dist_to_corner1",
            "elevation_diff",
            "last_straight_hill_flag",
            "apt_distance_shift",
            "apt_bias_fit",
            "apt_seasonal",
        ],
    },
    {
        "name": "team_v2",
        "description": "人馬チーム力（騎手フォーム + 調教師フォーム）",
        "features": [
            "jockey_win_rate",
            "jockey_turf_win_rate",
            "jockey_dirt_win_rate",
            "jockey_turf_win_shift",
            "jockey_dirt_win_shift",
            "trainer_win_rate",
            "trainer_turf_win_rate",
            "trainer_dirt_win_rate",
        ],
    },
    {
        "name": "training_v2",
        "description": "調教仕上がり（調教Zスコア + 調教内容スコア）",
        "features": [
            "best_z_total",
            "z_trend_slope",
            "avg_accel",
            "session_count",
            "slope_ratio",
            "chokyo_master_score",
            "s1_time_score",
            "accel_bonus",
        ],
    },
    {
        "name": "pace_v2",
        "description": "ペース・展開適性（コース内ペース指数 + ラップ分散）",
        "features": [
            "pace_index",
            "lap_variance",
            "lap_std",
        ],
    },
    {
        "name": "condition_v2",
        "description": "レース条件・馬体条件（距離・馬場・馬体重・クラス）",
        "features": [
            "horse_weight",
            "weight_diff",
            "basis_weight",
            "distance",
            "keibajo_code",
            "track_code",
            "tenko_code",
            "shiba_baba_code",
            "dirt_baba_code",
            "grade_code",
        ],
    },
]


def _load_parquet(path: Path) -> pd.DataFrame:
    log.info("Parquet 読み込み: %s", path)
    df = pd.read_parquet(path)
    df = df[df["kakutei_chakujun"].notna() & (df["kakutei_chakujun"] > 0)].copy()
    df = df.sort_values(["race_id", "umaban"]).reset_index(drop=True)
    df = _prepare_numerics(df)
    log.info("  %d 行 / %d レース", len(df), df["race_id"].nunique())
    return df


def _check_features(df: pd.DataFrame, feat_def: dict) -> list[str]:
    """定義された特徴量のうち Parquet に存在するものだけを返す（不足は警告）。"""
    available = []
    for col in feat_def["features"]:
        if col in df.columns:
            available.append(col)
        else:
            log.warning("[%s] 特徴量 %r は Parquet に存在しません（スキップ）", feat_def["name"], col)
    return available


def _train_one_submodel(
    df: pd.DataFrame,
    feat_def: dict,
) -> pd.DataFrame:
    """
    1 サブモデルを 5-Fold GroupKFold で学習し OOF スコアを返す。

    Returns:
        OOF スコア DataFrame（race_id, horse_id, is_win, score_{name}）
    """
    name = feat_def["name"]
    feature_cols = _check_features(df, feat_def)

    if len(feature_cols) < 2:
        raise ValueError(f"[{name}] 有効な特徴量が 2 本未満のため学習不可")

    log.info("─── %s  %d 特徴量: %s", name, len(feature_cols), feature_cols)

    X     = df[feature_cols].copy()
    y     = (df["kakutei_chakujun"] == 1).astype(int)
    groups = df["race_id"]

    oof_scores = np.full(len(df), np.nan)
    gkf = GroupKFold(n_splits=CV_FOLDS)
    fold_aucs: list[float] = []
    best_boosters: list[lgb.Booster] = []

    for fold, (tr_idx, va_idx) in enumerate(
        gkf.split(X, y, groups), start=1
    ):
        X_tr, X_va = X.iloc[tr_idx], X.iloc[va_idx]
        y_tr, y_va = y.iloc[tr_idx], y.iloc[va_idx]

        dtrain = lgb.Dataset(X_tr, label=y_tr)
        dval   = lgb.Dataset(X_va, label=y_va, reference=dtrain)

        model = lgb.train(
            LGBM_PARAMS_BINARY,
            dtrain,
            num_boost_round=NUM_BOOST_ROUND,
            valid_sets=[dval],
            callbacks=[
                lgb.early_stopping(EARLY_STOPPING_ROUNDS, verbose=False),
                lgb.log_evaluation(100),
            ],
        )

        auc = model.best_score["valid_0"]["auc"]
        fold_aucs.append(auc)
        log.info("  Fold %d: AUC=%.4f @ iter %d", fold, auc, model.best_iteration)

        oof_scores[va_idx] = model.predict(X_va)
        best_boosters.append(model)

    cv_auc = float(np.mean(fold_aucs))
    log.info("  %s CV-AUC=%.4f", name, cv_auc)

    # 全データで最終モデルを学習（推論用）
    log.info("  %s 最終モデル（全データ）学習中...", name)
    final_dtrain = lgb.Dataset(X, label=y)
    avg_best_iter = int(np.mean([b.best_iteration for b in best_boosters]))
    final_model = lgb.train(
        LGBM_PARAMS_BINARY,
        final_dtrain,
        num_boost_round=avg_best_iter,
        callbacks=[lgb.log_evaluation(100)],
    )

    mgr = SubmodelManager(_SUBMODEL_BASE / name)
    mgr.save(
        final_model,
        feature_cols,
        metadata={
            "name": name,
            "description": feat_def["description"],
            "version": "v2",
            "cv_folds": CV_FOLDS,
            "cv_auc": cv_auc,
            "fold_aucs": fold_aucs,
            "avg_best_iter": avg_best_iter,
            "n_rows": len(df),
            "n_races": int(groups.nunique()),
        },
    )
    log.info("  保存完了: %s", _SUBMODEL_BASE / name)

    oof_df = pd.DataFrame({
        "race_id":  df["race_id"].values,
        "horse_id": df["horse_id"].values,
        "is_win":   y.values,
        f"score_{name}": oof_scores,
    })
    return oof_df


def train_all(parquet_path: Path) -> None:
    df = _load_parquet(parquet_path)

    all_oof: list[pd.DataFrame] = []
    for feat_def in SUBMODEL_DEFS:
        oof = _train_one_submodel(df, feat_def)
        all_oof.append(oof)

    # OOF スコアを結合して 1 ファイルに保存
    merged = all_oof[0][["race_id", "horse_id", "is_win"]].copy()
    for oof in all_oof:
        score_col = [c for c in oof.columns if c.startswith("score_")][0]
        merged[score_col] = oof[score_col].values

    out_path = _SUBMODEL_BASE / "oof_scores_v2.parquet"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(out_path, index=False)
    log.info("OOF スコア保存: %s  shape=%s", out_path, merged.shape)

    # サマリー表示
    score_cols = [c for c in merged.columns if c.startswith("score_")]
    log.info("─── 完了サマリー ───")
    for col in score_cols:
        nan_count = merged[col].isna().sum()
        log.info("  %-30s  NaN=%d", col, nan_count)

    log.info("次のステップ: py -3.13 scripts/merge_v2_submodel_scores.py")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="V2 サブモデル 6本を学習して OOF スコアを保存する")
    p.add_argument(
        "--parquet",
        type=Path,
        default=_DEFAULT_PARQUET,
        help=f"学習に使う Parquet（デフォルト: {_DEFAULT_PARQUET}）",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    if not args.parquet.exists():
        log.error("Parquet が見つかりません: %s", args.parquet)
        sys.exit(1)
    train_all(args.parquet)
