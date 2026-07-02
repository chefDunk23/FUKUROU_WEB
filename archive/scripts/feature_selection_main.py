"""
scripts/feature_selection_main.py
===================================
各サブモデルの特徴量重要度（Gain）を算出し、複数の足切り閾値で CV 比較を行い、
最適な特徴量セットを自動探索してサブモデルごとに保存する。

【探索戦略】
  baseline    : 現行の全特徴量（比較基準）
  cum_90/95/99: 累積 Gain 上位 90/95/99% を保持
  drop_btm_10/20/30: 重要度下位 10/20/30% を除外

結果: config/selected_features.json
      scripts/feature_selection_report.csv

Usage:
    py -3.13 scripts/feature_selection_main.py
    py -3.13 scripts/feature_selection_main.py --submodel course_v2
    py -3.13 scripts/feature_selection_main.py --parquet outputs/bloodline_features_v1_2022plus.parquet
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.model_selection import GroupKFold

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.models.v2.config import (
    CV_FOLDS,
    EARLY_STOPPING_ROUNDS,
    LGBM_PARAMS_BINARY,
    NUM_BOOST_ROUND,
)
from src.models.v2.dataset import _prepare_numerics
from scripts.train_v2_submodels import SUBMODEL_DEFS, _check_features

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

_DEFAULT_PARQUET = Path("outputs/bloodline_features_v1_2022plus.parquet")
_CONFIG_OUT      = _ROOT / "config" / "selected_features.json"
_REPORT_OUT      = Path(__file__).parent / "feature_selection_report.csv"

# 足切り戦略の定義（(name, 選択比率判定関数)）
# fi は [feature, gain, gain_pct, cumulative] を持つ DataFrame（重要度降順ソート済み）
_CUTOFF_STRATEGIES: list[tuple[str, object]] = [
    ("baseline",     lambda fi: [True] * len(fi)),
    ("cum_99",       lambda fi: (fi["cumulative"].shift(1, fill_value=0.0) < 0.99).tolist()),
    ("cum_95",       lambda fi: (fi["cumulative"].shift(1, fill_value=0.0) < 0.95).tolist()),
    ("cum_90",       lambda fi: (fi["cumulative"].shift(1, fill_value=0.0) < 0.90).tolist()),
    ("drop_btm_10",  lambda fi: (fi["gain_pct"] >= fi["gain_pct"].quantile(0.10)).tolist()),
    ("drop_btm_20",  lambda fi: (fi["gain_pct"] >= fi["gain_pct"].quantile(0.20)).tolist()),
    ("drop_btm_30",  lambda fi: (fi["gain_pct"] >= fi["gain_pct"].quantile(0.30)).tolist()),
]


# ─────────────────────────────────────────────────────────────────────────────
# データ準備
# ─────────────────────────────────────────────────────────────────────────────

def load_data(path: Path) -> pd.DataFrame:
    log.info("Parquet 読み込み: %s", path)
    df = pd.read_parquet(path)
    df = df[df["kakutei_chakujun"].notna() & (df["kakutei_chakujun"] > 0)].copy()
    df = df.sort_values(["race_id", "umaban"]).reset_index(drop=True)
    df = _prepare_numerics(df)
    log.info("  %d 行 / %d レース", len(df), df["race_id"].nunique())
    return df


# ─────────────────────────────────────────────────────────────────────────────
# 特徴量重要度算出（全特徴量で1回 CV を回してブースター重要度を取得）
# ─────────────────────────────────────────────────────────────────────────────

def _cv_auc(
    df: pd.DataFrame,
    features: list[str],
    n_splits: int = CV_FOLDS,
) -> tuple[float, list[float]]:
    """GroupKFold 5-fold CV AUC を返す（高速化のため early stopping あり）。"""
    X      = df[features].copy()
    y      = (df["kakutei_chakujun"] == 1).astype(int)
    groups = df["race_id"]

    gkf  = GroupKFold(n_splits=n_splits)
    aucs: list[float] = []

    for tr_idx, va_idx in gkf.split(X, y, groups):
        dtrain = lgb.Dataset(X.iloc[tr_idx], label=y.iloc[tr_idx])
        dval   = lgb.Dataset(X.iloc[va_idx],  label=y.iloc[va_idx], reference=dtrain)
        model  = lgb.train(
            LGBM_PARAMS_BINARY,
            dtrain,
            num_boost_round=NUM_BOOST_ROUND,
            valid_sets=[dval],
            callbacks=[
                lgb.early_stopping(EARLY_STOPPING_ROUNDS, verbose=False),
                lgb.log_evaluation(-1),
            ],
        )
        aucs.append(model.best_score["valid_0"]["auc"])

    return float(np.mean(aucs)), aucs


def compute_importance(
    df: pd.DataFrame,
    features: list[str],
) -> pd.DataFrame:
    """全特徴量で 1 fold だけ学習し、Gain 重要度を返す（探索前の高速算出）。"""
    X = df[features].copy()
    y = (df["kakutei_chakujun"] == 1).astype(int)

    # 1 fold（先頭 20% をバリデーション）
    n_val = len(df) // 5
    tr_idx = list(range(n_val, len(df)))
    va_idx = list(range(n_val))

    dtrain = lgb.Dataset(X.iloc[tr_idx], label=y.iloc[tr_idx])
    dval   = lgb.Dataset(X.iloc[va_idx],  label=y.iloc[va_idx], reference=dtrain)

    model = lgb.train(
        LGBM_PARAMS_BINARY,
        dtrain,
        num_boost_round=NUM_BOOST_ROUND,
        valid_sets=[dval],
        callbacks=[
            lgb.early_stopping(EARLY_STOPPING_ROUNDS, verbose=False),
            lgb.log_evaluation(-1),
        ],
    )

    gains = model.feature_importance(importance_type="gain")
    fi = pd.DataFrame({"feature": features, "gain": gains})
    fi = fi.sort_values("gain", ascending=False).reset_index(drop=True)
    total = fi["gain"].sum()
    fi["gain_pct"]    = fi["gain"] / total if total > 0 else 0.0
    fi["cumulative"]  = fi["gain_pct"].cumsum()
    return fi


# ─────────────────────────────────────────────────────────────────────────────
# 1 サブモデルの特徴量選択
# ──────��──────────────────────────────────────────────────────────────────────

def select_for_submodel(
    df: pd.DataFrame,
    feat_def: dict,
) -> dict:
    """
    1 サブモデルに対して全足切り戦略を CV 評価し、最適な特徴量セットを返す。

    Returns
    -------
    dict with keys:
        name, baseline_auc, best_cutoff, best_auc, improvement,
        n_original, n_selected, selected_features, fi_table (list of dicts)
    """
    name     = feat_def["name"]
    all_feats = _check_features(df, feat_def)

    if len(all_feats) < 3:
        log.warning("[%s] 有効な特徴量が3本未満、スキップ", name)
        return {}

    log.info("━━━━ [%s] 特徴量重要度算出（%d 列）...", name, len(all_feats))

    # ── Step 1: Gain 重要度を 1 fold で高速取得 ──────────────────────────────
    fi = compute_importance(df, all_feats)

    log.info("  TOP-10 Gain:")
    for _, row in fi.head(10).iterrows():
        log.info("    %-40s gain_pct=%5.2f%%  cum=%5.2f%%",
                 row["feature"], row["gain_pct"] * 100, row["cumulative"] * 100)

    # ── Step 2: 全足切り戦略を CV 評価 ──────────────────────────────────────
    rows: list[dict] = []
    best_cutoff = "baseline"
    best_auc    = 0.0
    best_feats  = all_feats

    for cutoff_name, mask_fn in _CUTOFF_STRATEGIES:
        mask    = mask_fn(fi)
        sel_feats = fi.loc[mask, "feature"].tolist()

        if len(sel_feats) < 2:
            log.info("  [%s] 選択特徴量が 2 本未満、スキップ", cutoff_name)
            continue

        log.info("  [%s] CV評価: %d/%d 特徴量...",
                 cutoff_name, len(sel_feats), len(all_feats))
        auc, fold_aucs = _cv_auc(df, sel_feats)
        log.info("    AUC=%.4f  folds=%s", auc,
                 " ".join(f"{a:.4f}" for a in fold_aucs))

        rows.append({
            "submodel":    name,
            "cutoff":      cutoff_name,
            "n_features":  len(sel_feats),
            "cv_auc":      round(auc, 4),
            "fold_aucs":   fold_aucs,
            "features":    sel_feats,
        })

        if auc > best_auc:
            best_auc    = auc
            best_cutoff = cutoff_name
            best_feats  = sel_feats

    baseline_auc = next(r["cv_auc"] for r in rows if r["cutoff"] == "baseline")
    improvement  = best_auc - baseline_auc

    log.info("  ✓ [%s] 最適: %s  AUC %.4f → %.4f  (Δ%+.4f)  %d/%d 特徴量",
             name, best_cutoff, baseline_auc, best_auc, improvement,
             len(best_feats), len(all_feats))

    # 除外された特徴量
    dropped = [f for f in all_feats if f not in best_feats]
    if dropped:
        log.info("  除外: %s", dropped)

    return {
        "name":              name,
        "baseline_auc":      baseline_auc,
        "best_cutoff":       best_cutoff,
        "best_auc":          best_auc,
        "improvement":       round(improvement, 4),
        "n_original":        len(all_feats),
        "n_selected":        len(best_feats),
        "n_dropped":         len(dropped),
        "dropped_features":  dropped,
        "selected_features": best_feats,
        "cv_table":          rows,
        "fi_table":          fi.to_dict(orient="records"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# メイン
# ──────────────────────���──────────────────────────────────────────────────────

def run_selection(parquet_path: Path, only: str | None = None) -> None:
    df = load_data(parquet_path)

    targets = SUBMODEL_DEFS
    if only:
        targets = [d for d in SUBMODEL_DEFS if d["name"] == only]
        if not targets:
            log.error("--submodel %r は SUBMODEL_DEFS に存在しません", only)
            sys.exit(1)

    results: dict[str, dict] = {}

    for feat_def in targets:
        res = select_for_submodel(df, feat_def)
        if res:
            results[res["name"]] = res

    # ── config/selected_features.json 保存 ──────────────────────────────────
    config_out: dict = {
        "version":    "v2_selected",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "parquet":    str(parquet_path),
        "submodels":  {},
    }

    for name, res in results.items():
        config_out["submodels"][name] = {
            "optimal_cutoff":    res["best_cutoff"],
            "baseline_auc":      res["baseline_auc"],
            "best_auc":          res["best_auc"],
            "improvement":       res["improvement"],
            "n_features_original": res["n_original"],
            "n_features_selected": res["n_selected"],
            "features":          res["selected_features"],
        }

    _CONFIG_OUT.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_OUT.write_text(
        json.dumps(config_out, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("設定ファイル保存: %s", _CONFIG_OUT)

    # ── CSV レポート保存 ──────────────────────────────────────────────────────
    report_rows: list[dict] = []
    for res in results.values():
        for row in res["cv_table"]:
            report_rows.append({
                "submodel":   row["submodel"],
                "cutoff":     row["cutoff"],
                "n_features": row["n_features"],
                "cv_auc":     row["cv_auc"],
                "is_best":    row["cutoff"] == res["best_cutoff"],
            })

    report_df = pd.DataFrame(report_rows)
    _REPORT_OUT.parent.mkdir(parents=True, exist_ok=True)
    report_df.to_csv(_REPORT_OUT, index=False, encoding="utf-8-sig")
    log.info("CSV レポート保存: %s", _REPORT_OUT)

    # ── サマリー表示 ─────────────────────────────────────────────────────────
    sep = "=" * 90
    print(f"\n{sep}")
    print("  Feature Selection サマリー（自動最適化結果）")
    print(sep)
    print(f"  {'サブモデル':<20} {'最適戦略':<15} {'特徴量':<12} "
          f"{'Baseline AUC':>13} {'Best AUC':>10} {'改善':>8}")
    print("-" * 90)
    total_orig = total_sel = 0
    for name, res in results.items():
        marker = "◎" if res["improvement"] > 0 else "  "
        print(
            f"  {marker} {name:<19} {res['best_cutoff']:<15} "
            f"{res['n_selected']:>3}/{res['n_original']:<8} "
            f"{res['baseline_auc']:>13.4f} {res['best_auc']:>10.4f} "
            f"{res['improvement']:>+8.4f}"
        )
        total_orig += res["n_original"]
        total_sel  += res["n_selected"]
    print("-" * 90)
    print(f"  合計特徴量: {total_orig} → {total_sel} （削減 {total_orig - total_sel} 列）")
    print()

    # ── 除外特徴量の詳細 ─────────────────────────────────────────────────────
    print(f"  {'サブモデル':<20} 除外された特徴量")
    print("-" * 90)
    for name, res in results.items():
        if res["dropped_features"]:
            dropped_str = ", ".join(res["dropped_features"])
            print(f"  {name:<20} {dropped_str}")
        else:
            print(f"  {name:<20} （削除なし）")
    print(sep)
    print(f"\n次のステップ:")
    print(f"  py -3.13 scripts/train_v2_submodels.py --use-feature-selection")
    print(f"  ※ config/selected_features.json の特徴量で全サブモデルを再訓練します")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="サブモデル特徴量重要度を分析し最適特徴量セットを保存する")
    p.add_argument(
        "--parquet", type=Path, default=_DEFAULT_PARQUET,
        help=f"学習 Parquet（デフォルト: {_DEFAULT_PARQUET}）",
    )
    p.add_argument(
        "--submodel", type=str, default=None,
        help="特定サブモデルのみ実行（例: course_v2）",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    if not args.parquet.exists():
        log.error("Parquet が見つかりません: %s", args.parquet)
        sys.exit(1)
    run_selection(args.parquet, only=args.submodel)
