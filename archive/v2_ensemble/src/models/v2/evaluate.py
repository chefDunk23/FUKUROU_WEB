"""評価指標算出 — NDCG・単勝的中率・単勝EV"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ndcg_at_k(relevance: np.ndarray, score: np.ndarray, k: int) -> float:
    """NDCG@k（relevanceは高いほど良い馬、scoreは高いほど上位予測）"""
    order = np.argsort(score)[::-1][:k]
    gains = relevance[order].astype(float)
    ideal = np.sort(relevance)[::-1][:k].astype(float)

    dcg  = float(np.sum(gains  / np.log2(np.arange(1, len(gains)  + 1) + 1)))
    idcg = float(np.sum(ideal  / np.log2(np.arange(1, len(ideal)  + 1) + 1)))
    return dcg / idcg if idcg > 0 else 0.0


def win_hit_rate(chakujun: np.ndarray, score: np.ndarray) -> float:
    """スコア最上位予測馬が実際に1着かどうか（1=的中 / 0=外れ）"""
    return float(chakujun[np.argmax(score)] == 1)


def expected_value_top1(
    tan_odds: np.ndarray,
    score: np.ndarray,
    chakujun: np.ndarray,
) -> float:
    """単勝EV = tan_odds × 的中率 − 1（スコア最上位馬の単勝を買う想定）"""
    top_idx = int(np.argmax(score))
    hit = float(chakujun[top_idx] == 1)
    return float(tan_odds[top_idx] * hit - 1)


def evaluate_by_race(
    raw: pd.DataFrame,
    score_col: str = "score",
) -> pd.DataFrame:
    """レースごとに評価指標を計算して DataFrame で返す。

    raw には少なくとも以下のカラムが必要:
        race_id, kakutei_chakujun, tan_odds, <score_col>
    """
    records = []
    for race_id, grp in raw.groupby("race_id"):
        chakujun = grp["kakutei_chakujun"].values.astype(int)
        score    = grp[score_col].values.astype(float)
        odds     = grp["tan_odds"].values.astype(float) if "tan_odds" in grp.columns else np.ones(len(grp))

        # lambdarank relevance（1着=2, 2-3着=1, 他=0）
        rel = np.where(chakujun == 1, 2, np.where(chakujun <= 3, 1, 0))

        records.append({
            "race_id":  race_id,
            "n_horses": len(grp),
            "ndcg@1":   ndcg_at_k(rel, score, k=1),
            "ndcg@3":   ndcg_at_k(rel, score, k=3),
            "ndcg@5":   ndcg_at_k(rel, score, k=5),
            "win_hit":  win_hit_rate(chakujun, score),
            "ev_top1":  expected_value_top1(odds, score, chakujun),
        })

    return pd.DataFrame(records)


def summarize(eval_df: pd.DataFrame) -> dict:
    """evaluate_by_race の結果から全体サマリーを返す"""
    metric_cols = ["ndcg@1", "ndcg@3", "ndcg@5", "win_hit", "ev_top1"]
    return {
        col: {
            "mean": float(eval_df[col].mean()),
            "std":  float(eval_df[col].std()),
        }
        for col in metric_cols
        if col in eval_df.columns
    }
