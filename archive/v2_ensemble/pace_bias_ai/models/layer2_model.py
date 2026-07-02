"""
pace_bias_ai/models/layer2_model.py
=====================================
第2層 LightGBM lambdarank モデル

設計:
    - objective: lambdarank（グループ=race_id単位でのランキング最適化）
    - ターゲット: 1 - target_norm（1=1着、0=最下位）→ relevance として扱う
    - Walk-forward OOF でA期間精度を評価（時系列境界を厳守）
    - フィルター精度（カバー率・候補精度）で評価
    - SHAP値で特徴量重要度を確認

Walk-forward Foldの設計:
    Fold1: 学習 2020-01〜2021-06, OOF 2021-07〜2022-06
    Fold2: 学習 2020-01〜2022-06, OOF 2022-07〜2023-06
    OOF期間: 2021-07〜2023-06（A期間の後半2年分）

リーク対策:
    - Fold境界: train_end < val_start を厳守
    - 早期停止の val_ds: 各Foldのtrain内の最後3ヶ月を使用（val_startより前）
"""
from __future__ import annotations

import logging
import math
from typing import NamedTuple

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# ── デフォルト Folds ─────────────────────────────────────────────────────────
DEFAULT_FOLDS: list[tuple[str, str, str, str]] = [
    # DBの race_entries_v2 は2022年以降のみ存在するため、2022年から設定
    # Fold1: 2022年を学習、2023年をOOF評価
    ("2022-01-01", "2022-12-31", "2023-01-01", "2023-12-31"),
    # Fold2: 2022-2023年を学習、2024年前半をOOF評価
    ("2022-01-01", "2023-12-31", "2024-01-01", "2024-06-30"),
]

# ── LightGBM パラメータ ──────────────────────────────────────────────────────
DEFAULT_LGB_PARAMS: dict = {
    "objective":        "lambdarank",
    "metric":           "ndcg",
    "ndcg_eval_at":     [5],
    # label_gain: 整数ラベル0〜4の各関連度に対する NDCG 重み
    # 0=6着以下, 1=4-5着, 2=3着, 3=2着, 4=1着 → 指数的に上位を重視
    "label_gain":       [0, 1, 3, 7, 15],
    "learning_rate":    0.05,
    "num_leaves":       63,
    "min_data_in_leaf": 20,
    "max_depth":        -1,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq":     5,
    "lambda_l1":        0.1,
    "lambda_l2":        0.1,
    "verbose":          -1,
    "seed":             42,
    "n_jobs":           -1,
}

_NUM_BOOST_ROUND   = 600
_EARLY_STOP_ROUNDS = 50

# カテゴリ変数（LightGBMにカテゴリとして渡す）
CATEGORICAL_FEATURES: list[str] = ["dist_cat", "surface_code"]


class FoldResult(NamedTuple):
    fold:      int
    val_start: str
    val_end:   str
    n_races:   int
    cover3_5:  float   # 上位5頭のカバー率
    top52_5:   float   # 上位5頭の候補精度
    cover3_1:  float   # 上位1頭の複勝率
    best_iter: int


def _make_groups(df: pd.DataFrame) -> list[int]:
    """race_id 順に各レースの出走頭数リストを返す（LightGBM group 引数用）。

    df はあらかじめ race_id でソートされていること。
    """
    return df.groupby("race_id", sort=False).size().tolist()


def _build_target(df: pd.DataFrame) -> np.ndarray:
    """着順から整数の関連度（0〜4）を生成。LightGBM lambdarank 用。

    1着=4, 2着=3, 3着=2, 4〜5着=1, 6着以下=0
    整数ラベルが必須（LightGBM lambdarank の仕様）。

    label_gain = [0, 1, 3, 7, 15] を併用することで
    NDCG 計算時に上位着順を指数的に重視する。
    """
    rank = pd.to_numeric(df["kakutei_chakujun"], errors="coerce").fillna(99)
    relevance = np.select(
        [rank == 1, rank == 2, rank == 3, rank <= 5],
        [4,         3,         2,         1],
        default=0,
    )
    return relevance.astype(np.int32)


def compute_filter_metrics(
    df: pd.DataFrame,
    score_col: str,
    n_top: int = 5,
) -> dict:
    """フィルター精度（カバー率・候補精度・上位1頭複勝率）を計算。

    Args:
        df       : kakutei_chakujun と score_col を含む DataFrame
        score_col: スコアカラム名（高いほど上位候補）
        n_top    : 上位選出数

    Returns:
        {'n_races', 'cover3', 'top5_2', 'cover3_r1'}
    """
    results = []
    for _, grp in df.groupby("race_id"):
        grp = grp.dropna(subset=["kakutei_chakujun"])
        if len(grp) < n_top:
            continue
        top_n = grp.nlargest(n_top, score_col)
        ranks  = top_n["kakutei_chakujun"].values
        results.append({
            "cover3": int((ranks <= 3).any()),
            "top5_2": int((ranks <= 5).sum() >= 2),
            "r1_cv3": int(grp.nlargest(1, score_col)["kakutei_chakujun"].iloc[0] <= 3),
        })
    if not results:
        return {"n_races": 0, "cover3": 0.0, "top5_2": 0.0, "cover3_r1": 0.0}
    df_r = pd.DataFrame(results)
    return {
        "n_races":   len(df_r),
        "cover3":    float(df_r["cover3"].mean()),
        "top5_2":    float(df_r["top5_2"].mean()),
        "cover3_r1": float(df_r["r1_cv3"].mean()),
    }


def compute_random_baseline(df: pd.DataFrame, n_top: int = 5) -> dict:
    """ランダム選択のカバー率・候補精度を解析的に計算（比較用）。"""
    cover3_list, top52_list = [], []
    for _, grp in df.groupby("race_id"):
        grp   = grp.dropna(subset=["kakutei_chakujun"])
        field = len(grp)
        if field < n_top:
            continue
        in3 = min(3, field)
        p_c3 = 1.0 - (
            math.comb(max(field - in3, 0), n_top) / math.comb(field, n_top)
        )
        in5  = min(5, field)
        p_t52 = 1.0 - sum(
            math.comb(in5, k) * math.comb(max(field - in5, 0), n_top - k) / math.comb(field, n_top)
            for k in range(min(2, n_top + 1))
            if 0 <= n_top - k <= field - in5
        )
        cover3_list.append(p_c3)
        top52_list.append(p_t52)
    return {
        "cover3": float(np.mean(cover3_list)) if cover3_list else 0.0,
        "top5_2": float(np.mean(top52_list))  if top52_list  else 0.0,
    }


def walk_forward_oof(
    df: pd.DataFrame,
    feature_cols: list[str],
    folds: list[tuple[str, str, str, str]] | None = None,
    lgb_params: dict | None = None,
) -> tuple[pd.Series, list[FoldResult], object]:
    """Walk-forward OOF予測を実行。

    Args:
        df          : 第1層+第2層特徴量が揃ったDataFrame
        feature_cols: 特徴量カラム名リスト
        folds       : Noneの場合はDEFAULT_FOLDSを使用
        lgb_params  : Noneの場合はDEFAULT_LGB_PARAMSを使用

    Returns:
        (oof_scores, fold_results, last_model)
        oof_scores: df.index に対応したOOF予測スコア（高いほど上位候補）
                    OOF期間外の行はNaN
        fold_results: 各FoldのFoldResult
        last_model: 最後のFoldで学習したモデル（SHAP値計算用）
    """
    try:
        import lightgbm as lgb
    except ImportError:
        raise RuntimeError("lightgbm がインストールされていません: pip install lightgbm")

    folds      = folds      or DEFAULT_FOLDS
    lgb_params = lgb_params or DEFAULT_LGB_PARAMS

    df = df.copy()
    df["race_date"] = pd.to_datetime(df["race_date"])

    oof_scores    = pd.Series(np.nan, index=df.index)
    fold_results  = []
    last_model    = None

    for fold_idx, (tr_start, tr_end, val_start, val_end) in enumerate(folds, start=1):
        log.info("[OOF Fold%d] 学習 %s〜%s | 評価 %s〜%s",
                 fold_idx, tr_start, tr_end, val_start, val_end)

        # 学習データ・評価データの分割
        tr_mask  = (df["race_date"] >= tr_start) & (df["race_date"] <= tr_end)
        val_mask = (df["race_date"] >= val_start) & (df["race_date"] <= val_end)

        df_tr  = df[tr_mask].copy()
        df_val = df[val_mask].copy()

        if len(df_tr) == 0 or len(df_val) == 0:
            log.warning("[OOF Fold%d] データなし → スキップ", fold_idx)
            continue

        # 着順不明（取消等）を除外
        df_tr  = df_tr.dropna(subset=["kakutei_chakujun"])
        df_val = df_val.dropna(subset=["kakutei_chakujun"])

        # race_id でソート（group引数の正確性のため必須）
        # reset_index を行わない → 元のdfのindexを保持して oof_scores への書き戻しに使用
        df_tr  = df_tr.sort_values(["race_id", "umaban"])
        df_val = df_val.sort_values(["race_id", "umaban"])

        X_tr  = df_tr[feature_cols].values.astype(np.float32)
        y_tr  = _build_target(df_tr)
        grp_tr = _make_groups(df_tr)

        # 早期停止用: 学習データの最後3ヶ月を内部Validationに使用
        es_cutoff  = pd.Timestamp(tr_end) - pd.DateOffset(months=3)
        es_tr_mask = df_tr["race_date"] <= es_cutoff
        es_vl_mask = ~es_tr_mask

        lgb_tr = lgb.Dataset(
            X_tr[es_tr_mask.values],
            label=y_tr[es_tr_mask.values],
            group=df_tr[es_tr_mask].groupby("race_id", sort=False).size().tolist(),
            feature_name=feature_cols,
            categorical_feature=[f for f in CATEGORICAL_FEATURES if f in feature_cols],
            free_raw_data=False,
        )
        lgb_es = lgb.Dataset(
            X_tr[es_vl_mask.values],
            label=y_tr[es_vl_mask.values],
            group=df_tr[es_vl_mask].groupby("race_id", sort=False).size().tolist(),
            feature_name=feature_cols,
            categorical_feature=[f for f in CATEGORICAL_FEATURES if f in feature_cols],
            free_raw_data=False,
        )

        model = lgb.train(
            lgb_params,
            lgb_tr,
            num_boost_round=_NUM_BOOST_ROUND,
            valid_sets=[lgb_es],
            valid_names=["es_val"],
            callbacks=[
                lgb.early_stopping(_EARLY_STOP_ROUNDS, verbose=False),
                lgb.log_evaluation(100),
            ],
        )

        last_model = model

        # OOF 予測（df_val は sort_values のみで reset_index していないので
        # df_val.index は元の df のインデックスを保持している）
        X_val = df_val[feature_cols].values.astype(np.float32)
        preds = model.predict(X_val)
        oof_scores.loc[df_val.index] = preds

        # フィルター精度計算
        df_val["_score"] = preds
        m = compute_filter_metrics(df_val, "_score", n_top=5)
        fr = FoldResult(
            fold      = fold_idx,
            val_start = val_start,
            val_end   = val_end,
            n_races   = m["n_races"],
            cover3_5  = m["cover3"],
            top52_5   = m["top5_2"],
            cover3_1  = m["cover3_r1"],
            best_iter = model.best_iteration,
        )
        fold_results.append(fr)
        log.info(
            "[OOF Fold%d] カバー率=%.3f 候補精度=%.3f 複勝率=%.3f iter=%d N=%d",
            fold_idx, fr.cover3_5, fr.top52_5, fr.cover3_1, fr.best_iter, fr.n_races,
        )

    return oof_scores, fold_results, last_model


def train_full_model(
    df: pd.DataFrame,
    feature_cols: list[str],
    lgb_params: dict | None = None,
    num_rounds: int | None = None,
) -> object:
    """A期間全データでフルモデルを学習する（B/C期間予測用）。

    Args:
        df           : 第1層+第2層特徴量が揃ったDataFrame
        feature_cols : 特徴量カラム名リスト
        lgb_params   : Noneの場合はDEFAULT_LGB_PARAMSを使用
        num_rounds   : 学習ラウンド数。NoneならDEFAULT（OOFのbest_iterの平均推奨）

    Returns:
        lgb.Booster
    """
    try:
        import lightgbm as lgb
    except ImportError:
        raise RuntimeError("lightgbm がインストールされていません")

    lgb_params = lgb_params or DEFAULT_LGB_PARAMS
    n_rounds   = num_rounds or _NUM_BOOST_ROUND

    df = df.copy().dropna(subset=["kakutei_chakujun"])
    df = df.sort_values(["race_id", "umaban"]).reset_index(drop=True)

    X   = df[feature_cols].values.astype(np.float32)
    y   = _build_target(df)
    grp = _make_groups(df)

    ds = lgb.Dataset(
        X, label=y, group=grp,
        feature_name=feature_cols,
        categorical_feature=[f for f in CATEGORICAL_FEATURES if f in feature_cols],
        free_raw_data=False,
    )
    model = lgb.train(lgb_params, ds, num_boost_round=n_rounds)
    return model


def compute_shap_importance(
    model,
    df_sample: pd.DataFrame,
    feature_cols: list[str],
    top_n: int = 15,
) -> pd.DataFrame:
    """SHAP値で特徴量重要度を計算（上位top_n件を返す）。

    Args:
        model       : lgb.Boosterまたは同等のモデル
        df_sample   : SHAPを計算するサンプル（数千行程度で十分）
        feature_cols: 特徴量カラム名リスト
        top_n       : 上位件数

    Returns:
        feature / mean_abs_shap / direction (positive=高いほど良い方向) のDataFrame
    """
    try:
        import shap
    except ImportError:
        log.warning("shap がインストールされていません: pip install shap")
        return pd.DataFrame()

    X_sample = df_sample[feature_cols].values.astype(np.float32)
    explainer = shap.TreeExplainer(model)
    shap_vals = explainer.shap_values(X_sample)

    mean_abs = np.abs(shap_vals).mean(axis=0)
    mean_dir = shap_vals.mean(axis=0)

    importance = pd.DataFrame({
        "feature":        feature_cols,
        "mean_abs_shap":  mean_abs,
        "mean_shap":      mean_dir,
    }).sort_values("mean_abs_shap", ascending=False).head(top_n).reset_index(drop=True)

    importance["direction"] = importance["mean_shap"].apply(
        lambda v: "positive (高→上位)" if v > 0 else "negative (低→上位)"
    )
    return importance
