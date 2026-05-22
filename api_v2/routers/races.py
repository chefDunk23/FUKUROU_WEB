"""
api_v2/routers/races.py
========================
GET /api/v2/races?date=YYYY-MM-DD — 指定日のレース一覧を返す。
"""
from __future__ import annotations

import logging
from datetime import date

import psycopg2.extras
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from shared.db.jvdata import get_conn

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v2", tags=["v2-races"])

# JRA 競馬場コード → 名称（正しいマッピング）
_KEIBAJO_NAME: dict[str, str] = {
    "01": "札幌", "02": "函館", "03": "福島", "04": "新潟",
    "05": "東京", "06": "中山", "07": "中京", "08": "京都",
    "09": "阪神", "10": "小倉",
    # 地方競馬
    "30": "盛岡", "35": "水沢", "42": "金沢", "43": "笠松",
    "44": "名古屋", "46": "園田", "47": "姫路", "48": "高知", "50": "佐賀",
}

_SQL_RACES_BY_DATE = """
SELECT
    id                  AS race_id,
    race_num,
    keibajo_code,
    distance,
    track_code,
    grade_code,
    race_name_hondai,
    race_name_short_10,
    syusso_tosu
FROM   races
WHERE  race_date = %s
ORDER  BY keibajo_code, race_num
"""


class RaceSummary(BaseModel):
    race_id: str
    race_num: int
    keibajo_code: str
    keibajo_name: str
    distance: int
    track_code: str | None
    grade_code: str | None
    race_name: str
    syusso_tosu: int | None


class RaceListResponse(BaseModel):
    date: str
    races: list[RaceSummary]


@router.get("/races", response_model=RaceListResponse)
def list_races(
    date: date = Query(..., description="対象日 (YYYY-MM-DD)"),
) -> RaceListResponse:
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(_SQL_RACES_BY_DATE, (date,))
                rows = cur.fetchall()
    except Exception as exc:
        logger.exception("[V2Races] DBクエリ失敗: %s", exc)
        raise HTTPException(status_code=500, detail=f"DB照会エラー: {exc}")

    summaries: list[RaceSummary] = []
    for row in rows:
        kc       = str(row["keibajo_code"]).strip().zfill(2)
        name_raw = (row.get("race_name_hondai") or row.get("race_name_short_10") or "").strip()
        summaries.append(
            RaceSummary(
                race_id=str(row["race_id"]),
                race_num=int(row["race_num"]),
                keibajo_code=kc,
                keibajo_name=_KEIBAJO_NAME.get(kc, kc),
                distance=int(row["distance"]),
                track_code=str(row["track_code"]).strip() if row["track_code"] else None,
                grade_code=str(row["grade_code"]).strip() if row["grade_code"] else None,
                race_name=name_raw,
                syusso_tosu=int(row["syusso_tosu"]) if row["syusso_tosu"] is not None else None,
            )
        )

    return RaceListResponse(date=str(date), races=summaries)
