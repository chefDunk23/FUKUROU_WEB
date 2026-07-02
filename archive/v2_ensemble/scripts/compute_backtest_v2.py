"""
scripts/compute_backtest_v2.py
====================================
アンサンブル OOF 予測を用いて AI 1番手推奨の単勝・複勝実績を計算。

GroupKFold (n=5) で fold ごとに対応するアンサンブルモデルで予測し、
各レースの AI 推奨順位を付与した上で 1番手馬のデータを保存する。

出力:
    outputs/v2/evaluations/backtest_oof.parquet
    outputs/v2/evaluations/backtest_summary.json

Usage:
    py -3.13 scripts/compute_backtest_v2.py
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.model_selection import GroupKFold

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

_STACKED_PARQUET  = _ROOT / "outputs" / "v2_stacked_features.parquet"
_ENSEMBLE_DIR     = _ROOT / "models" / "v2" / "ensemble"
_ENSEMBLE_CONFIG  = _ROOT / "config" / "ensemble_config.json"
_OUT_DIR          = _ROOT / "outputs" / "v2" / "evaluations"
_OUT_PARQUET      = _OUT_DIR / "backtest_oof.parquet"
_OUT_SUMMARY      = _OUT_DIR / "backtest_summary.json"

_DEFAULT_FEATURE_COLS = [
    "score_ability_v2",
    "score_course_v2",
    "score_team_v2",
    "score_training_v2",
    "score_pace_v2",
    "score_pedigree_v1",
]

CV_FOLDS = 5


def _load_feature_cols() -> list[str]:
    """ensemble_config.json があればその active_submodel_scores を使う。なければデフォルト。"""
    if _ENSEMBLE_CONFIG.exists():
        with open(_ENSEMBLE_CONFIG, encoding="utf-8") as f:
            cfg = json.load(f)
        cols = cfg.get("active_submodel_scores", _DEFAULT_FEATURE_COLS)
        log.info("アンサンブル設定読み込み: %s  有効列=%s", _ENSEMBLE_CONFIG.name, cols)
        return cols
    log.info("ensemble_config.json なし → デフォルト特徴量 %d 列を使用", len(_DEFAULT_FEATURE_COLS))
    return _DEFAULT_FEATURE_COLS

# オッズバケット定義: (label, min_odds, max_odds)
ODDS_BUCKETS = [
    ("1.0-1.9",   1.0,   1.99),
    ("2.0-2.9",   2.0,   2.99),
    ("3.0-4.9",   3.0,   4.99),
    ("5.0-9.9",   5.0,   9.99),
    ("10.0-19.9", 10.0,  19.99),
    ("20.0-49.9", 20.0,  49.99),
    ("50.0+",     50.0, 9999.0),
]


def _load_models() -> list[lgb.Booster]:
    models = []
    for fold in range(1, CV_FOLDS + 1):
        path = _ENSEMBLE_DIR / f"lgbm_rank_fold{fold}.lgb"
        if not path.exists():
            raise FileNotFoundError(f"アンサンブルモデルが見つかりません: {path}")
        models.append(lgb.Booster(model_file=str(path)))
    log.info("アンサンブルモデル %d 本ロード完了", len(models))
    return models


def _compute_oof(df: pd.DataFrame, models: list[lgb.Booster], feature_cols: list[str]) -> np.ndarray:
    """GroupKFold で OOF スコアを計算して返す。"""
    X      = df[feature_cols].values.astype(np.float32)
    groups = df["race_id"]
    gkf    = GroupKFold(n_splits=CV_FOLDS)
    scores = np.full(len(df), np.nan)

    for fold_idx, (_, va_idx) in enumerate(gkf.split(X, groups=groups)):
        model = models[fold_idx]
        scores[va_idx] = model.predict(X[va_idx])
        log.info("  Fold %d 予測完了 (%d 行)", fold_idx + 1, len(va_idx))

    nan_pct = np.isnan(scores).mean()
    if nan_pct > 0:
        log.warning("OOF スコアに NaN %.1f%% 残存", nan_pct * 100)
    return scores


def _odds_bucket_stats(top1: pd.DataFrame) -> list[dict]:
    """オッズバケット別に集計して dict リストを返す。"""
    results = []
    for label, lo, hi in ODDS_BUCKETS:
        mask = (top1["tan_odds_f"] >= lo) & (top1["tan_odds_f"] <= hi)
        sub  = top1[mask]
        if len(sub) == 0:
            continue
        bets         = len(sub)
        win_hits     = int(sub["is_win"].sum())
        place_hits   = int(sub["is_place"].sum())
        total_return = float((sub["tan_odds_f"] * sub["is_win"]).sum())
        results.append({
            "odds_bucket":      label,
            "odds_min":         lo,
            "odds_max":         hi,
            "bets":             bets,
            "win_hits":         win_hits,
            "win_hit_rate":     round(win_hits / bets, 4),
            "win_return_rate":  round(total_return / bets * 100, 1),
            "place_hits":       place_hits,
            "place_hit_rate":   round(place_hits / bets, 4),
        })
    return results


def _optimal_window(top1: pd.DataFrame, min_bets: int = 100) -> dict:
    """回収率最大となる連続オッズ区間を探索する。"""
    top1 = top1.copy()
    lo_vals = np.arange(1.0, 30.1, 0.5)
    hi_vals = np.arange(3.0, 100.1, 1.0)

    best: dict = {}
    best_return = -1.0

    for lo in lo_vals:
        for hi in hi_vals:
            if hi <= lo:
                continue
            mask = (top1["tan_odds_f"] >= lo) & (top1["tan_odds_f"] <= hi)
            sub  = top1[mask]
            if len(sub) < min_bets:
                continue
            ret = float((sub["tan_odds_f"] * sub["is_win"]).sum()) / len(sub) * 100
            if ret > best_return:
                best_return = ret
                best = {
                    "odds_min":        lo,
                    "odds_max":        hi,
                    "bets":            len(sub),
                    "win_hit_rate":    round(sub["is_win"].mean(), 4),
                    "win_return_rate": round(ret, 1),
                    "place_hit_rate":  round(sub["is_place"].mean(), 4),
                }

    return best


def main() -> None:
    if not _STACKED_PARQUET.exists():
        log.error("Parquet が見つかりません: %s", _STACKED_PARQUET)
        sys.exit(1)

    _OUT_DIR.mkdir(parents=True, exist_ok=True)

    # ── データ読み込み ──────────────────────────────────────────────────────
    log.info("Parquet 読み込み: %s", _STACKED_PARQUET)
    df = pd.read_parquet(_STACKED_PARQUET)
    df = df[df["kakutei_chakujun"].notna() & (df["kakutei_chakujun"] > 0)].copy()
    df = df.sort_values(["race_id", "umaban"]).reset_index(drop=True)
    log.info("  %d 行 / %d レース", len(df), df["race_id"].nunique())

    # ── 特徴量列の決定（ensemble_config.json 優先） ─────────────────────────
    feature_cols = _load_feature_cols()

    # ── OOF 予測 ────────────────────────────────────────────────────────────
    models = _load_models()
    df["oof_score"] = _compute_oof(df, models, feature_cols)

    # ── レース内順位付け ────────────────────────────────────────────────────
    df["ai_rank"] = (
        df.groupby("race_id")["oof_score"]
        .rank(ascending=False, method="first")
        .astype(int)
    )

    # ── 勝敗フラグ ──────────────────────────────────────────────────────────
    df["is_win"]   = (df["kakutei_chakujun"] == 1).astype(int)
    df["is_place"] = (df["kakutei_chakujun"] <= 3).astype(int)

    # ── tan_odds を float 変換 ───────────────────────────────────────────────
    df["tan_odds_f"] = pd.to_numeric(df["tan_odds"], errors="coerce")

    # ── 全馬保存（ai_rank 1〜全頭） ─────────────────────────────────────────
    save_cols = [
        "race_id", "race_date", "keibajo_code", "distance", "grade_code",
        "horse_id", "umaban", "oof_score", "ai_rank",
        "kakutei_chakujun", "tan_odds_f", "ninki",
        "is_win", "is_place",
    ]
    out_df = df[save_cols].rename(columns={"tan_odds_f": "tan_odds"})
    out_df.to_parquet(_OUT_PARQUET, index=False)
    log.info("OOF 予測保存: %s  shape=%s", _OUT_PARQUET, out_df.shape)

    # ── 1番手のみで集計 ─────────────────────────────────────────────────────
    top1 = df[df["ai_rank"] == 1].copy()
    log.info("1番手レース数: %d", len(top1))

    total_bets        = len(top1)
    win_hits          = int(top1["is_win"].sum())
    place_hits        = int(top1["is_place"].sum())
    total_win_return  = float((top1["tan_odds_f"] * top1["is_win"]).sum())

    summary = {
        "total_races":        total_bets,
        "win_hits":           win_hits,
        "win_hit_rate":       round(win_hits / total_bets, 4),
        "win_return_rate":    round(total_win_return / total_bets * 100, 1),
        "place_hits":         place_hits,
        "place_hit_rate":     round(place_hits / total_bets, 4),
        "avg_tan_odds":       round(float(top1["tan_odds_f"].mean()), 2),
        "odds_buckets":       _odds_bucket_stats(top1),
        "optimal_odds_window": _optimal_window(top1),
    }

    with open(_OUT_SUMMARY, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    log.info("サマリー保存: %s", _OUT_SUMMARY)

    log.info("=== 全体集計 ===")
    log.info("  総ベット数  : %d レース", total_bets)
    log.info("  単勝的中率  : %.1f%%", win_hits / total_bets * 100)
    log.info("  単勝回収率  : %.1f%%", total_win_return / total_bets * 100)
    log.info("  複勝的中率  : %.1f%%", place_hits / total_bets * 100)
    log.info("  平均単勝オッズ: %.1f 倍", float(top1["tan_odds_f"].mean()))

    opt = summary["optimal_odds_window"]
    if opt:
        log.info(
            "=== 最適オッズ帯 ===  %.1f〜%.1f倍  回収率 %.1f%%  的中率 %.1f%%  (%d ベット)",
            opt["odds_min"], opt["odds_max"],
            opt["win_return_rate"], opt["win_hit_rate"] * 100,
            opt["bets"],
        )


if __name__ == "__main__":
    main()
