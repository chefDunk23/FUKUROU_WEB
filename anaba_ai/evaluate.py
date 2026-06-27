"""
anaba_ai/evaluate.py
=====================
C 期間（ホールドアウト検証）の評価指標を計算する。

評価指標:
    - 単勝的中率・ROI（anaba_score 上位 N 頭）
    - 複勝的中率・ROI（同上）
    - 人気 4 番人気以降に限定した場合の指標
    - 自然確率との比較（穴馬発掘力）
    - 各人気帯での精度
"""
from __future__ import annotations

import logging
from typing import NamedTuple

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# 評価する上位N頭の設定
TOP_N_LIST = [1, 2, 3]

# 穴馬定義（4番人気以降）
ANABA_NINKI_THRESHOLD = 4

# 人気帯定義（分析用）
NINKI_BUCKETS = [
    ("1-3番人気", 1, 3),
    ("4-6番人気", 4, 6),
    ("7-9番人気", 7, 9),
    ("10番人気以降", 10, 99),
]


class EvalResult(NamedTuple):
    """検証結果の格納型。"""
    total_races:    int
    total_bets:     int
    win_hits:       int
    win_hit_rate:   float
    win_roi:        float
    place_hits:     int
    place_hit_rate: float
    place_roi:      float
    avg_ninki:      float


def _payout_win(tan_odds: float) -> float:
    """単勝払戻額（100円賭け）。JRA は 100 円単位切り捨て。"""
    return float(int(tan_odds * 100 / 100) * 100)


def _evaluate_bets(df_bets: pd.DataFrame) -> EvalResult:
    """
    推奨馬群の的中率・ROI を計算する。

    df_bets: 馬ごとの行 + 以下のカラムが必要
        kakutei_chakujun, tan_odds, ninki
    """
    if df_bets.empty:
        return EvalResult(0, 0, 0, 0.0, 0.0, 0, 0.0, 0.0, 0.0)

    total_bets = len(df_bets)

    # 単勝
    win_mask  = df_bets["kakutei_chakujun"] == 1
    win_hits  = int(win_mask.sum())
    win_return = float(
        df_bets.loc[win_mask, "tan_odds"].apply(_payout_win).sum()
    )
    win_roi = win_return / (total_bets * 100) * 100 if total_bets > 0 else 0.0

    # 複勝（暫定: 人気順位から推定払戻を使う。確定払戻がない場合）
    # tan_odds が確定オッズの場合、複勝は別途payoutsから取れないのでROI計算は
    # 複勝的中率のみ計算し、ROIは概算値を記録する
    place_mask  = df_bets["kakutei_chakujun"] <= 3
    place_hits  = int(place_mask.sum())
    place_hit_rate = place_hits / total_bets if total_bets > 0 else 0.0

    # 複勝ROI: 払戻が取れないため N/A（-1 を設定）
    # 実際には payouts テーブルから取れるが本実装では単勝 ROI を主指標にする
    place_roi = -1.0

    avg_ninki = float(df_bets["ninki"].mean()) if "ninki" in df_bets.columns else 0.0

    total_races = df_bets["race_id"].nunique() if "race_id" in df_bets.columns else total_bets

    return EvalResult(
        total_races=total_races,
        total_bets=total_bets,
        win_hits=win_hits,
        win_hit_rate=win_hits / total_bets if total_bets > 0 else 0.0,
        win_roi=win_roi,
        place_hits=place_hits,
        place_hit_rate=place_hit_rate,
        place_roi=place_roi,
        avg_ninki=avg_ninki,
    )


def evaluate_c_period(
    df_C: pd.DataFrame,
    top_n: int = 1,
    anaba_only: bool = False,
) -> dict:
    """
    C 期間の anaba_score 上位 top_n 頭の推奨結果を評価する。

    Args:
        df_C      : C 期間 DataFrame（anaba_score 列を含む）
        top_n     : レースごとに推奨する上位頭数
        anaba_only: True の場合、4番人気以降の馬のみを評価対象にする

    Returns:
        評価指標の dict
    """
    if "anaba_score" not in df_C.columns:
        raise KeyError("anaba_score 列が見つかりません。predict_anaba_score を先に実行してください。")

    required = {"race_id", "kakutei_chakujun", "tan_odds", "ninki", "anaba_score"}
    missing = required - set(df_C.columns)
    if missing:
        raise KeyError(f"必要カラムが不足: {missing}")

    df = df_C.copy()
    df["anaba_score"] = pd.to_numeric(df["anaba_score"], errors="coerce")

    # anaba_only モード: 4番人気以降のみ
    if anaba_only:
        df = df[df["ninki"] >= ANABA_NINKI_THRESHOLD].copy()

    # レースごとに anaba_score でランク付けし、上位 top_n を推奨
    df["anaba_rank"] = df.groupby("race_id")["anaba_score"].rank(
        method="first", ascending=False
    )
    df_picks = df[df["anaba_rank"] <= top_n].copy()

    result = _evaluate_bets(df_picks)

    return {
        "top_n":         top_n,
        "anaba_only":    anaba_only,
        "total_races":   result.total_races,
        "total_bets":    result.total_bets,
        "win_hits":      result.win_hits,
        "win_hit_rate":  round(result.win_hit_rate, 4),
        "win_roi":       round(result.win_roi, 1),
        "place_hits":    result.place_hits,
        "place_hit_rate": round(result.place_hit_rate, 4),
        "avg_ninki":     round(result.avg_ninki, 1),
    }


def evaluate_by_ninki_bucket(df_C: pd.DataFrame) -> list[dict]:
    """人気帯別の的中率・ROI を計算する（anaba_score 上位1頭推奨ベース）。"""
    if "anaba_score" not in df_C.columns:
        raise KeyError("anaba_score 列が見つかりません")

    df = df_C.copy()
    df["anaba_rank"] = df.groupby("race_id")["anaba_score"].rank(
        method="first", ascending=False
    )
    df_top1 = df[df["anaba_rank"] == 1].copy()

    results = []
    for label, lo, hi in NINKI_BUCKETS:
        subset = df_top1[
            df_top1["ninki"].between(lo, hi, inclusive="both")
        ]
        r = _evaluate_bets(subset)
        results.append({
            "ninki_bucket":  label,
            "bets":          r.total_bets,
            "win_hit_rate":  round(r.win_hit_rate, 4),
            "win_roi":       round(r.win_roi, 1),
            "place_hit_rate": round(r.place_hit_rate, 4),
            "avg_ninki":     round(r.avg_ninki, 1),
        })
    return results


def natural_win_rate_by_ninki(df_C: pd.DataFrame) -> dict[str, float]:
    """
    自然確率（ランダム選択）との比較用：人気帯ごとの自然勝率。
    = その人気帯の馬が1着になる割合
    """
    df = df_C.copy()
    df["is_win"] = (df["kakutei_chakujun"] == 1)

    result = {}
    for label, lo, hi in NINKI_BUCKETS:
        subset = df[df["ninki"].between(lo, hi, inclusive="both")]
        if len(subset) == 0:
            result[label] = 0.0
            continue
        result[label] = round(float(subset["is_win"].mean()), 4)
    return result


def full_evaluation_report(df_C: pd.DataFrame) -> dict:
    """
    全評価指標をまとめた dict を返す。

    Returns dict with:
        overall: top_n ごとの評価
        anaba_only: 4番人気以降限定の評価
        by_ninki: 人気帯別の評価
        natural_rates: 自然確率との比較
    """
    log.info("C 期間評価開始: %d行 / %d races", len(df_C), df_C["race_id"].nunique())

    overall = [evaluate_c_period(df_C, top_n=n) for n in TOP_N_LIST]
    anaba_only = [evaluate_c_period(df_C, top_n=n, anaba_only=True) for n in TOP_N_LIST]
    by_ninki = evaluate_by_ninki_bucket(df_C)
    natural = natural_win_rate_by_ninki(df_C)

    # 評価結果ログ出力
    log.info("=== C 期間検証結果 ===")
    for r in overall:
        log.info(
            "TOP%d全馬: 的中率=%.1f%% ROI=%.1f%% 複勝率=%.1f%% 平均人気=%.1f 賭数=%d",
            r["top_n"], r["win_hit_rate"] * 100, r["win_roi"],
            r["place_hit_rate"] * 100, r["avg_ninki"], r["total_bets"],
        )

    log.info("--- 4番人気以降限定 ---")
    for r in anaba_only:
        log.info(
            "TOP%d穴馬限定: 的中率=%.1f%% ROI=%.1f%% 複勝率=%.1f%% 平均人気=%.1f 賭数=%d",
            r["top_n"], r["win_hit_rate"] * 100, r["win_roi"],
            r["place_hit_rate"] * 100, r["avg_ninki"], r["total_bets"],
        )

    log.info("--- 人気帯別 (TOP1推奨) ---")
    for r in by_ninki:
        nat = natural.get(r["ninki_bucket"], 0.0)
        log.info(
            "%s: AI的中率=%.1f%% / 自然確率=%.1f%% / ROI=%.1f%% (N=%d)",
            r["ninki_bucket"], r["win_hit_rate"] * 100, nat * 100, r["win_roi"], r["bets"],
        )

    return {
        "overall":       overall,
        "anaba_only":    anaba_only,
        "by_ninki":      by_ninki,
        "natural_rates": natural,
    }
