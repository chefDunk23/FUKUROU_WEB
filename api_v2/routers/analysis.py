"""
api_v2/routers/analysis.py
============================
GET /api/v2/analysis/ev — 過去レースの期待値（EV）分析。

クエリパラメータ:
    year_from  (int, default=2022)
    year_to    (int, default=今年)
    min_ev     (float, default=1.15)
    limit      (int, default=200)
"""
from __future__ import annotations

import datetime
import logging

import psycopg2.extras
from fastapi import APIRouter, Query
from fastapi.exceptions import HTTPException
from pydantic import BaseModel

from shared.db.jvdata import get_conn

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v2", tags=["v2-analysis"])


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


# レース結果から AI スコアを再現するには学習済みモデルが必要なため、
# このエンドポイントは「確定着順データ」に基づく参照のみ提供する。
# Phase 3（フロント実装）以降で、事前計算済みスコアをDBに保存する拡張を行う。
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
    year_from: int = Query(2022, description="集計開始年"),
    year_to:   int = Query(datetime.date.today().year, description="集計終了年"),
    min_ev:    float = Query(1.15, description="表示する最低EV"),
    limit:     int = Query(200, le=2000, description="最大取得件数"),
) -> EvAnalysisResponse:
    """
    過去レースの確定着順・オッズを元に単純 EV（1着=tan_odds, 外れ=-1）を算出する。

    NOTE: このエンドポイントは AI スコアではなくオッズ単体の EV 分析を提供する。
    AI 予測 EV（ai_score × odds）は Phase 3 でフロントエンドに DB 保存フローを
    追加した後に拡張する。
    """
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

    # min_ev フィルタ（勝ち馬のみ EV が正になるので、全 1着馬を返す）
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
