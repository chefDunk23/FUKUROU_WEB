"""LightGBM V2 学習エントリーポイント

Usage:
    python -m src.models.v2.train outputs/pace_features.parquet
    python -m src.models.v2.train outputs/pace_features.parquet --mode binary_win
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import lightgbm as lgb
import numpy as np
from sklearn.model_selection import GroupKFold

from .config import (
    CV_FOLDS,
    EARLY_STOPPING_ROUNDS,
    LGBM_PARAMS_BINARY,
    LGBM_PARAMS_RANK,
    NUM_BOOST_ROUND,
)
from .dataset import load
from .evaluate import evaluate_by_race, summarize

_OUTPUT_DIR = Path("outputs/v2")
_MODEL_DIR  = _OUTPUT_DIR / "models"
_EVAL_DIR   = _OUTPUT_DIR / "evaluations"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def _group_sizes(groups: "pd.Series") -> np.ndarray:
    """race_id の連続ブロックをランレングス符号化してLightGBM用グループサイズ配列を返す。

    LightGBM が期待するのは [レース1頭数, レース2頭数, ...] という1レース1要素の配列。
    groups はレース単位でソート済み（contiguous）であることが前提。
    """
    arr = groups.to_numpy()
    boundaries = np.concatenate([[0], np.where(arr[:-1] != arr[1:])[0] + 1, [len(arr)]])
    return np.diff(boundaries).astype(np.int32)


def train(parquet_path: str | Path, mode: str = "rank") -> None:
    """
    GroupKFold CV でLightGBMを学習し、各foldのモデルと評価結果を outputs/v2/ に保存する。

    Args:
        parquet_path: generate_pace_features.py が出力したParquetのパス
        mode: "rank"（lambdarank）| "binary_win"（単勝2値分類）
    """
    _MODEL_DIR.mkdir(parents=True, exist_ok=True)
    _EVAL_DIR.mkdir(parents=True, exist_ok=True)

    ds = load(parquet_path, mode=mode)
    log.info("データ読み込み完了: %d行 / %dレース / %d特徴量",
             len(ds.X), ds.groups.nunique(), ds.X.shape[1])
    log.info("使用特徴量: %s", ds.X.columns.tolist())

    params = LGBM_PARAMS_RANK if mode == "rank" else LGBM_PARAMS_BINARY

    gkf = GroupKFold(n_splits=CV_FOLDS)
    fold_summaries: list[dict] = []

    for fold, (tr_idx, va_idx) in enumerate(
        gkf.split(ds.X, ds.y, ds.groups), start=1
    ):
        X_tr, X_va = ds.X.iloc[tr_idx], ds.X.iloc[va_idx]
        y_tr, y_va = ds.y.iloc[tr_idx], ds.y.iloc[va_idx]
        g_tr = ds.groups.iloc[tr_idx]
        g_va = ds.groups.iloc[va_idx]

        if mode == "rank":
            dtrain = lgb.Dataset(X_tr, label=y_tr, group=_group_sizes(g_tr))
            dval   = lgb.Dataset(X_va, label=y_va, group=_group_sizes(g_va),
                                 reference=dtrain)
        else:
            dtrain = lgb.Dataset(X_tr, label=y_tr)
            dval   = lgb.Dataset(X_va, label=y_va, reference=dtrain)

        model = lgb.train(
            params,
            dtrain,
            num_boost_round=NUM_BOOST_ROUND,
            valid_sets=[dval],
            callbacks=[
                lgb.early_stopping(EARLY_STOPPING_ROUNDS, verbose=False),
                lgb.log_evaluation(50),
            ],
        )

        best_metric_name = list(model.best_score["valid_0"].keys())[-1]
        best_score = model.best_score["valid_0"][best_metric_name]
        log.info("Fold %d: %s=%.4f @ iter %d",
                 fold, best_metric_name, best_score, model.best_iteration)

        model_path = _MODEL_DIR / f"lgbm_{mode}_fold{fold}.lgb"
        model.save_model(str(model_path))
        log.info("モデル保存: %s", model_path)

        va_df = ds.raw.iloc[va_idx].copy()
        va_df["score"] = model.predict(X_va)
        eval_df = evaluate_by_race(va_df)
        metrics = summarize(eval_df)

        eval_path = _EVAL_DIR / f"eval_{mode}_fold{fold}.csv"
        eval_df.to_csv(eval_path, index=False)

        log.info(
            "  NDCG@3=%.4f  単勝的中率=%.3f  EV_top1=%.3f",
            metrics["ndcg@3"]["mean"],
            metrics["win_hit"]["mean"],
            metrics["ev_top1"]["mean"],
        )

        fold_summaries.append({
            "fold": fold,
            "best_iter": model.best_iteration,
            "lgbm_score": best_score,
            **{f"{k}_mean": v["mean"] for k, v in metrics.items()},
        })

    summary = {
        "mode": mode,
        "parquet": str(parquet_path),
        "cv_folds": CV_FOLDS,
        "feature_cols": ds.X.columns.tolist(),
        "folds": fold_summaries,
        "ndcg3_cv_mean": float(np.mean([f["ndcg@3_mean"] for f in fold_summaries])),
        "win_hit_cv_mean": float(np.mean([f["win_hit_mean"] for f in fold_summaries])),
        "ev_top1_cv_mean": float(np.mean([f["ev_top1_mean"] for f in fold_summaries])),
    }

    summary_path = _EVAL_DIR / f"summary_{mode}.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    log.info(
        "CV完了 — NDCG@3=%.4f  単勝的中率=%.3f  EV=%.3f",
        summary["ndcg3_cv_mean"],
        summary["win_hit_cv_mean"],
        summary["ev_top1_cv_mean"],
    )
    log.info("サマリー保存: %s", summary_path)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="LightGBM V2 学習スクリプト（GroupKFold CV）",
    )
    parser.add_argument("parquet", help="generate_pace_features.py の出力Parquetパス")
    parser.add_argument(
        "--mode",
        choices=["rank", "binary_win"],
        default="rank",
        help="rank=lambdarank（デフォルト）/ binary_win=単勝2値分類",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    train(args.parquet, mode=args.mode)
