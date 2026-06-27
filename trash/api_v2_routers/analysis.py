"""
api_v2/routers/analysis.py
============================
GET /api/v2/analysis/ev        — 過去レースの期待値（EV）分析（レガシー）
GET /api/v2/analysis/backtest  — AI 1番手推奨の OOF バックテスト実績・オッズ最適化
"""
from __future__ import annotations

import datetime
import logging
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2.extras
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from shared.db.jvdata import get_conn

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v2", tags=["v2-analysis"])

_BACKTEST_PARQUET = Path("outputs/v2/evaluations/backtest_oof.parquet")
_SUMMARY_JSON     = Path("outputs/v2/evaluations/backtest_summary.json")

# ── OOF データのキャッシュ（メモリ常駐、プロセス起動時に 1 回ロード） ─────────
_oof_cache: pd.DataFrame | None = None


def _load_oof() -> pd.DataFrame:
    global _oof_cache
    if _oof_cache is None:
        if not _BACKTEST_PARQUET.exists():
            raise FileNotFoundError(
                f"バックテスト Parquet が見つかりません: {_BACKTEST_PARQUET}\n"
                "py -3.13 scripts/compute_backtest_v2.py を実行してください。"
            )
        df = pd.read_parquet(_BACKTEST_PARQUET)
        df["tan_odds"] = pd.to_numeric(df["tan_odds"], errors="coerce")
        df["race_date"] = pd.to_datetime(df["race_date"])
        _oof_cache = df
        logger.info("[Backtest] OOF キャッシュロード: %d 行", len(df))
    return _oof_cache


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic モデル
# ─────────────────────────────────────────────────────────────────────────────

class OddsBucketStat(BaseModel):
    odds_bucket: str
    odds_min: float
    odds_max: float
    bets: int
    win_hits: int
    win_hit_rate: float
    win_return_rate: float
    place_hits: int
    place_hit_rate: float


class OptimalWindow(BaseModel):
    odds_min: float
    odds_max: float
    bets: int
    win_hit_rate: float
    win_return_rate: float
    place_hit_rate: float


class BacktestFilteredSummary(BaseModel):
    total_races: int
    win_hits: int
    win_hit_rate: float
    win_return_rate: float
    place_hits: int
    place_hit_rate: float
    avg_tan_odds: float


class BacktestResponse(BaseModel):
    year_from: int
    year_to: int
    summary: BacktestFilteredSummary
    odds_buckets: list[OddsBucketStat]
    optimal_odds_window: OptimalWindow | None
    custom_range: BacktestFilteredSummary | None


# ─────────────────────────────────────────────────────────────────────────────
# ヘルパー
# ─────────────────────────────────────────────────────────────────────────────

_ODDS_BUCKETS = [
    ("1.0-1.9",    1.0,   1.99),
    ("2.0-2.9",    2.0,   2.99),
    ("3.0-4.9",    3.0,   4.99),
    ("5.0-9.9",    5.0,   9.99),
    ("10.0-19.9",  10.0,  19.99),
    ("20.0-49.9",  20.0,  49.99),
    ("50.0+",      50.0,  9999.0),
]


def _bucket_stats(top1: pd.DataFrame) -> list[OddsBucketStat]:
    results = []
    for label, lo, hi in _ODDS_BUCKETS:
        sub = top1[(top1["tan_odds"] >= lo) & (top1["tan_odds"] <= hi)]
        if len(sub) == 0:
            continue
        bets        = len(sub)
        win_hits    = int(sub["is_win"].sum())
        place_hits  = int(sub["is_place"].sum())
        total_ret   = float((sub["tan_odds"] * sub["is_win"]).sum())
        results.append(OddsBucketStat(
            odds_bucket=label,
            odds_min=lo,
            odds_max=hi,
            bets=bets,
            win_hits=win_hits,
            win_hit_rate=round(win_hits / bets, 4),
            win_return_rate=round(total_ret / bets * 100, 1),
            place_hits=place_hits,
            place_hit_rate=round(place_hits / bets, 4),
        ))
    return results


def _filtered_summary(top1: pd.DataFrame) -> BacktestFilteredSummary | None:
    if len(top1) == 0:
        return None
    bets       = len(top1)
    win_hits   = int(top1["is_win"].sum())
    place_hits = int(top1["is_place"].sum())
    total_ret  = float((top1["tan_odds"] * top1["is_win"]).sum())
    return BacktestFilteredSummary(
        total_races=bets,
        win_hits=win_hits,
        win_hit_rate=round(win_hits / bets, 4),
        win_return_rate=round(total_ret / bets * 100, 1),
        place_hits=place_hits,
        place_hit_rate=round(place_hits / bets, 4),
        avg_tan_odds=round(float(top1["tan_odds"].mean()), 2),
    )


def _optimal_window(top1: pd.DataFrame, min_bets: int = 50) -> OptimalWindow | None:
    lo_vals = np.arange(1.0, 30.1, 0.5)
    hi_vals = np.arange(3.0, 100.1, 1.0)
    best_ret  = -1.0
    best_dict: dict = {}

    for lo in lo_vals:
        for hi in hi_vals:
            if hi <= lo + 1.0:
                continue
            sub = top1[(top1["tan_odds"] >= lo) & (top1["tan_odds"] <= hi)]
            if len(sub) < min_bets:
                continue
            ret = float((sub["tan_odds"] * sub["is_win"]).sum()) / len(sub) * 100
            if ret > best_ret:
                best_ret = ret
                best_dict = {
                    "odds_min":        float(lo),
                    "odds_max":        float(hi),
                    "bets":            len(sub),
                    "win_hit_rate":    round(float(sub["is_win"].mean()), 4),
                    "win_return_rate": round(ret, 1),
                    "place_hit_rate":  round(float(sub["is_place"].mean()), 4),
                }

    return OptimalWindow(**best_dict) if best_dict else None


# ─────────────────────────────────────────────────────────────────────────────
# エンドポイント
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/analysis/backtest", response_model=BacktestResponse)
def backtest_analysis(
    year_from:  int   = Query(2022, description="集計開始年"),
    year_to:    int   = Query(datetime.date.today().year, description="集計終了年"),
    odds_min:   float = Query(None, description="カスタム範囲: 最小オッズ"),
    odds_max:   float = Query(None, description="カスタム範囲: 最大オッズ"),
    min_bets:   int   = Query(50, ge=10, description="最適窓探索の最低ベット数"),
) -> BacktestResponse:
    """
    AI 1番手推奨馬（ai_rank=1）の OOF バックテスト実績を返す。

    - summary: 年度フィルタ後の全体集計
    - odds_buckets: オッズ帯別の的中率・回収率
    - optimal_odds_window: 回収率が最大となるオッズ区間
    - custom_range: odds_min/odds_max 指定時の絞り込み集計
    """
    try:
        df = _load_oof()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    # 年度フィルタ
    date_from = pd.Timestamp(year_from, 1, 1)
    date_to   = pd.Timestamp(year_to, 12, 31)
    df_yr = df[(df["race_date"] >= date_from) & (df["race_date"] <= date_to)]

    # 1番手のみ
    top1 = df_yr[df_yr["ai_rank"] == 1].copy()

    if len(top1) == 0:
        raise HTTPException(status_code=404, detail="対象データが存在しません")

    summary      = _filtered_summary(top1)
    odds_buckets = _bucket_stats(top1)

    # 最適窓（少し時間がかかる）
    opt_window = _optimal_window(top1, min_bets=min_bets)

    # カスタム範囲絞り込み
    custom: BacktestFilteredSummary | None = None
    if odds_min is not None or odds_max is not None:
        lo = odds_min if odds_min is not None else 1.0
        hi = odds_max if odds_max is not None else 9999.0
        sub = top1[(top1["tan_odds"] >= lo) & (top1["tan_odds"] <= hi)]
        custom = _filtered_summary(sub)

    return BacktestResponse(
        year_from=year_from,
        year_to=year_to,
        summary=summary,
        odds_buckets=odds_buckets,
        optimal_odds_window=opt_window,
        custom_range=custom,
    )


# ─────────────────────────────────────────────────────────────────────────────
# レガシー: /analysis/ev
# ─────────────────────────────────────────────────────────────────────────────

class EvRecord(BaseModel):
    race_id: str
    race_date: str
    keibajo_code: str
    distance: int
    grade_code: str | None
    umaban: int
    horse_name: str | None
    ai_rank: int
    tan_odds: float
    kakutei_chakujun: int
    ev: float
    is_hit: bool


class EvAnalysisResponse(BaseModel):
    total_races: int
    hit_count: int
    hit_rate: float
    avg_ev: float
    records: list[EvRecord]


_SQL_EV_RECORDS = """
SELECT
    r.id              AS race_id,
    r.race_date::text AS race_date,
    r.keibajo_code,
    r.distance,
    r.grade_code,
    e.umaban,
    e.horse_name,
    e.tan_odds,
    e.kakutei_chakujun
FROM   races r
JOIN   race_entries e ON e.race_id = r.id
WHERE  r.race_date BETWEEN %s AND %s
  AND  e.tan_odds IS NOT NULL
  AND  e.kakutei_chakujun IS NOT NULL
  AND  e.kakutei_chakujun > 0
  AND  e.tan_odds::numeric >= 1.0
ORDER  BY r.race_date DESC, r.keibajo_code, r.race_num, e.umaban
LIMIT  %s
"""


@router.get("/analysis/ev", response_model=EvAnalysisResponse)
def ev_analysis(
    year_from: int   = Query(2022, description="集計開始年"),
    year_to:   int   = Query(datetime.date.today().year, description="集計終了年"),
    min_ev:    float = Query(1.15, description="表示する最低EV"),
    limit:     int   = Query(200, le=2000, description="最大取得件数"),
) -> EvAnalysisResponse:
    date_from = datetime.date(year_from, 1, 1)
    date_to   = datetime.date(year_to, 12, 31)

    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(_SQL_EV_RECORDS, (date_from, date_to, limit * 10))
                rows = cur.fetchall()
    except Exception as exc:
        logger.exception("[EVAnalysis] DB照会エラー: %s", exc)
        raise HTTPException(status_code=500, detail=f"DB照会エラー: {exc}")

    records: list[EvRecord] = []
    for row in rows:
        try:
            odds   = float(row["tan_odds"])
            rank   = int(row["kakutei_chakujun"])
            is_hit = rank == 1
            ev     = round(odds * int(is_hit) - 1, 3)
        except (TypeError, ValueError):
            continue

        records.append(EvRecord(
            race_id=str(row["race_id"]),
            race_date=str(row["race_date"]),
            keibajo_code=str(row["keibajo_code"]).zfill(2),
            distance=int(row["distance"]),
            grade_code=str(row["grade_code"]) if row["grade_code"] else None,
            umaban=int(row["umaban"]),
            horse_name=row.get("horse_name") or None,
            ai_rank=1,
            tan_odds=odds,
            kakutei_chakujun=rank,
            ev=ev,
            is_hit=is_hit,
        ))

    filtered = [r for r in records if r.is_hit and r.tan_odds >= min_ev][:limit]
    hit_count = sum(1 for r in records if r.is_hit)
    hit_rate  = hit_count / len(records) if records else 0.0
    avg_ev    = sum(r.ev for r in filtered) / len(filtered) if filtered else 0.0

    return EvAnalysisResponse(
        total_races=len(records),
        hit_count=hit_count,
        hit_rate=round(hit_rate, 4),
        avg_ev=round(avg_ev, 3),
        records=filtered,
    )
