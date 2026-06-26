"""
scripts/train_v2_submodels.py
==============================
V2 スタックアンサンブル用サブモデル 6本 を Parquet から学習する。

各サブモデルは binary classification（単勝予測）で学習し、
OOF スコアを race_id × horse_id キーで Parquet に保存する。

Usage:
    py -3.13 scripts/train_v2_submodels.py
    py -3.13 scripts/train_v2_submodels.py --parquet outputs/bloodline_features_v1_2022plus.parquet
"""
from __future__ import annotations

import argparse
import json
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

_FEATURE_SELECTION_CONFIG = _ROOT / "config" / "selected_features.json"

from shared.config import EVAL_START_DATE, TRAIN_END_DATE
from src.features.pace_simulation_v1 import create_pace_simulation_features
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

_DEFAULT_PARQUET = Path("outputs/bloodline_features_v1_2022plus.parquet")
_SUBMODEL_BASE   = Path("models/v2/submodels")

# ── サブモデル定義 ────────────────────────────────────────────────────────────
#   name: 保存ディレクトリ名 & スコアカラム名 (score_{name})
#   features: 使用する特徴量カラム名リスト

SUBMODEL_DEFS: list[dict] = [
    {
        "name": "ability_v2",
        "description": "馬の基礎能力（過去戦績 + 直近フォーム + クラス補正 + 瞬発力 + 馬体・属性）※オッズプロキシ除外",
        "features": [
            # ── 累積過去戦績（feature_store 由来） ──────────
            # pre_race_rating は除外: オッズと強相関するプロキシのため
            "feature_past_starts",
            "feature_past_wins",
            "feature_past_top3",
            "feature_past_win_rate",
            "feature_past_fukusho_rate",
            # ── Phase 1: 直近フォーム（ability_features_v3）─
            "prev1_rank",
            "avg_rank_3",
            "avg_rank_5",
            "recent_win_rate_5",
            "recent_fukusho_rate_5",
            # ── Phase 2: クラス補正（ability_features_v3）───
            "max_grade_won",
            "class_win_rate",
            "prev1_rank_class_adj",
            # ── 瞬発力（物理的ポテンシャル: pace_v2 から移管）
            "avg_go3f_rank_5_turf",
            "go3f_rank_std_5_turf",
            "avg_go3f_rank_5_dirt",
            "go3f_rank_std_5_dirt",
            # ── 馬体・属性 ────────────────────────────────
            "horse_weight",
            "weight_diff",
            "basis_weight",
            "horse_age",
            "horse_sex",
            "grade_code",
        ],
    },
    {
        "name": "course_v2",
        "description": "コース適性（コース物理特性 + 適性スコア + 経験回数 + EG × 地形 + ローテーション）※着順ベース重複特徴量除外",
        "features": [
            # ── コース物理特性 ─────────────────────────────
            "straight_dist",
            "dist_to_corner1",
            "elevation_diff",
            "last_straight_hill_flag",
            # ── 適性スコア ────────────────────────────────
            "apt_distance_shift",
            "apt_bias_fit",
            "apt_seasonal",
            # ── 競馬場経験回数のみ（着順・勝率は ability_v2 と重複のため除外）
            "apt_venue_starts",
            # apt_venue_win_rate_5 / avg_rank_5 / fukusho_rate_5 は除外:
            # ability_v2 の avg_rank_* と高相関のためサブモデル独立性を破壊
            # ── course_v3: Expectation Gap × 物理特性（Phase 2）─
            "eg_flat_avg10",
            "eg_steep_avg10",
            "eg_turn_L_avg10",
            "eg_turn_R_avg10",
            "eg_steep_minus_flat",
            "agari_flat_avg10",
            "agari_steep_avg10",
            # ── course_v3: ローテーション条件替わり（Phase 3）─
            "rot_straight_delta",
            "rot_turn_switch",
            "rot_slope_shift",
            "rot_distance_delta",
            "rot_is_new_venue",
            # ── レース条件 ────────────────────────────────
            "distance",
            "keibajo_code",
            "track_code",
            "tenko_code",
            "shiba_baba_code",
            "dirt_baba_code",
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
        "description": (
            "脚質適性（頭数正規化コーナー通過順位 + 距離区分別脚質 +"
            " 展開シミュレーション — 事前入手可能な過去走データのみ）"
        ),
        "features": [
            # ── 頭数正規化ベース（pace_features_v4 / shift(1)済み過去走集計）────
            # NOTE: pace_index/lap_variance/lap_std/pace_type は当走の事後データ。
            #       pace_x_front/pace_x_late も pace_index（事後）を使う。
            #       → これらは DATA LEAK のため永久に除外。
            "avg_c1_norm_5",
            "avg_c4_norm_5",
            "avg_pos_advance_norm_5",
            "running_style_std_norm_5",
            # ── 距離区分別脚質（pace_features_v4） ────────────────────────────
            "avg_c1_norm_5_sprint",          "avg_c4_norm_5_sprint",          "avg_pos_advance_norm_5_sprint",
            "avg_c1_norm_5_mile",            "avg_c4_norm_5_mile",            "avg_pos_advance_norm_5_mile",
            "avg_c1_norm_5_mid",             "avg_c4_norm_5_mid",             "avg_pos_advance_norm_5_mid",
            "avg_c1_norm_5_long",            "avg_c4_norm_5_long",            "avg_pos_advance_norm_5_long",
            # ── 展開シミュレーション（pace_simulation_v1 / 完全事前データ）─────
            # WARNING: DO NOT REMOVE — calculated from pre-race data only.
            # 警告: 削除厳禁 — 過去走データ＋今回枠順のみから計算。リークなし。
            "predicted_position_norm",  # 枠順×他馬の先行傾向を考慮した推定ポジション
            "predicted_field_pace",     # フィールド全体の推定ペース指数
            "pace_harmony_pre",         # 脚質×ペース合致度（展開利不利スコア）
        ],
    },
    {
        "name": "pedigree_v1",
        "description": "血統適性（父・母父の馬場面別・距離区分別・競馬場別勝率 + 道悪・成長曲線・性別・馬体重クロス + P1-P5 PIT血統特徴量）",
        "features": [
            # ── 旧 sire_feature_store ベース ──────────────────────────────────
            # 基本
            "sire_total_win_rate",    "sire_total_top3_rate",    "sire_count",
            "bms_total_win_rate",     "bms_total_top3_rate",     "bms_count",
            # 適性判定（コンテキスト適応）
            "sire_surface_win_rate",  "sire_surface_top3_rate",
            "sire_dist_win_rate",     "sire_venue_win_rate",
            "bms_surface_win_rate",   "bms_surface_top3_rate",
            "bms_dist_win_rate",      "bms_venue_win_rate",
            # 道悪適性
            "sire_heavy_win_rate",    "bms_heavy_win_rate",
            # 成長曲線
            "sire_age_win_rate",      "bms_age_win_rate",
            "sire_growth_factor",     "bms_growth_factor",
            # 性別・馬体重クロス
            "sire_sex_win_rate",      "bms_sex_win_rate",
            "sire_weight_gap",        "bms_weight_gap",
            # ── P1: 父 Point-in-Time 成績（bloodline_feature_store）────────────
            "sire_wr", "sire_turf_wr", "sire_dirt_wr",
            "sire_sprint_wr", "sire_mile_wr", "sire_middle_wr", "sire_long_wr",
            "sire_heavy_wr", "sire_growth_delta", "sire_n_starts",
            # ── P2: 母父 Point-in-Time 成績 ─────────────────────────────────
            "bms_wr", "bms_turf_wr", "bms_dirt_wr",
            "bms_sprint_wr", "bms_mile_wr", "bms_middle_wr", "bms_long_wr",
            "bms_heavy_wr", "bms_growth_delta", "bms_n_starts",
            # ── P3: 個体クロス ───────────────────────────────────────────────
            "sire_sex_wr",    "p3_weight_gap",
            # ── P4: 突然変異スコア（祖先と父の適性乖離）─────────────────────
            "p4_mutation_turf", "p4_mutation_dirt", "p4_n_ancestors",
            # ── P5: 自己主張度（BMS 分散）────────────────────────────────────
            "p5_dominance_score", "p5_n_bms_groups",
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

    # ── データ分割リーク防止ガード（BET-4） ─────────────────────────────────
    # 学習データは TRAIN_END_DATE 以前のみ使用する（shared.config で一元管理）。
    # race_id の先頭8文字が YYYYMMDD 形式であることを前提にする（12桁・16桁いずれも同様）。
    # ランダムシャッフルは行わない。時系列順の除外のみ。
    eval_start_raw = EVAL_START_DATE.replace("-", "")  # "20250601"
    dates = df["race_id"].astype(str).str[:8]
    leak_mask = dates >= eval_start_raw
    if leak_mask.any():
        leaked_count = int(leak_mask.sum())
        log.warning(
            "  [BET-4] 検証データ期間（%s 以降）の行が %d 件含まれています → 除外します",
            EVAL_START_DATE,
            leaked_count,
        )
        df = df[~leak_mask].reset_index(drop=True)
        log.info(
            "  [BET-4] 除外後: %d 行 / %d レース",
            len(df),
            df["race_id"].nunique(),
        )
    else:
        log.info(
            "  [BET-4] データ分割チェック: 検証期間(%s 以降)の行 0 件 → リークなし ✓",
            EVAL_START_DATE,
        )
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


def _load_feature_selection(only: str | None) -> dict[str, list[str]] | None:
    """
    config/selected_features.json を読み込み、{submodel_name: [features]} を返す。
    ファイルが存在しない場合は None を返す。
    """
    if not _FEATURE_SELECTION_CONFIG.exists():
        log.error(
            "selected_features.json が見つかりません: %s\n"
            "先に py -3.13 scripts/feature_selection_main.py を実行してください。",
            _FEATURE_SELECTION_CONFIG,
        )
        sys.exit(1)

    cfg = json.loads(_FEATURE_SELECTION_CONFIG.read_text(encoding="utf-8"))
    result: dict[str, list[str]] = {}
    for name, info in cfg.get("submodels", {}).items():
        if only is None or name == only:
            result[name] = info["features"]
            log.info(
                "[FeatureSelection] %s: %d 特徴量 (最適戦略: %s, Baseline→Best AUC: %.4f→%.4f)",
                name, len(info["features"]), info["optimal_cutoff"],
                info["baseline_auc"], info["best_auc"],
            )
    return result


def train_all(
    parquet_path: Path,
    only: str | None = None,
    use_feature_selection: bool = False,
) -> None:
    df = _load_parquet(parquet_path)

    # 展開シミュレーション特徴量を注入（完全事前データ — DATA LEAK なし）
    # WARNING: DO NOT REPLACE with pace_type / pace_index / zen_3f / go_3f.
    # 警告: pace_type/pace_index/zen_3f/go_3f への置き換え厳禁（事後データ）。
    df = create_pace_simulation_features(df)

    # --use-feature-selection: config から特徴量を上書きする
    fs_overrides: dict[str, list[str]] | None = None
    if use_feature_selection:
        fs_overrides = _load_feature_selection(only)
        log.info("=== Feature Selection モード: %d サブモデルの特徴量を上書き ===",
                 len(fs_overrides))

    targets = SUBMODEL_DEFS
    if only is not None:
        targets = [d for d in SUBMODEL_DEFS if d["name"] == only]
        if not targets:
            log.error("--submodel %r は SUBMODEL_DEFS に存在しません。利用可能: %s",
                      only, [d["name"] for d in SUBMODEL_DEFS])
            sys.exit(1)

    # 特徴量選択の上書きを適用
    if fs_overrides:
        targets = [
            {**d, "features": fs_overrides[d["name"]]}
            if d["name"] in fs_overrides else d
            for d in targets
        ]

    all_oof: list[pd.DataFrame] = []
    for feat_def in targets:
        oof = _train_one_submodel(df, feat_def)
        all_oof.append(oof)

    out_path = _SUBMODEL_BASE / "oof_scores_v2.parquet"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # 単一サブモデル再訓練時は既存 OOF の該当列だけを差し替える
    if only is not None and out_path.exists():
        merged = pd.read_parquet(out_path)
        for oof in all_oof:
            score_col = [c for c in oof.columns if c.startswith("score_")][0]
            merged[score_col] = oof[score_col].values
    else:
        merged = all_oof[0][["race_id", "horse_id", "is_win"]].copy()
        for oof in all_oof:
            score_col = [c for c in oof.columns if c.startswith("score_")][0]
            merged[score_col] = oof[score_col].values

    merged.to_parquet(out_path, index=False)
    log.info("OOF スコア保存: %s  shape=%s", out_path, merged.shape)

    # サマリー表示
    score_cols = [c for c in merged.columns if c.startswith("score_")]
    log.info("─── 完了サマリー ───")
    for col in score_cols:
        nan_count = merged[col].isna().sum()
        log.info("  %-30s  NaN=%d", col, nan_count)

    log.info("次のステップ: py -3.13 scripts/merge_v2_submodel_scores.py --parquet %s", parquet_path)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="V2 サブモデル 6本を学習して OOF スコアを保存する")
    p.add_argument(
        "--parquet",
        type=Path,
        default=_DEFAULT_PARQUET,
        help=f"学習に使う Parquet（デフォルト: {_DEFAULT_PARQUET}）",
    )
    p.add_argument(
        "--submodel",
        type=str,
        default=None,
        help="特定サブモデルだけ再訓練する（例: course_v2）。省略時は全6本を訓練。",
    )
    p.add_argument(
        "--use-feature-selection",
        action="store_true",
        default=False,
        dest="use_feature_selection",
        help=(
            "config/selected_features.json の最適特徴量で学習する。"
            "OFF 時は SUBMODEL_DEFS のデフォルト特徴量を使用（いつでも元に戻せる）。"
        ),
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    if not args.parquet.exists():
        log.error("Parquet が見つかりません: %s", args.parquet)
        sys.exit(1)
    train_all(args.parquet, only=args.submodel, use_feature_selection=args.use_feature_selection)
