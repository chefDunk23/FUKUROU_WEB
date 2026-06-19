"""
api_v1/services/race_fetcher.py
================================
今週末（土・日）のレース一覧を fukurou_jvdl DB から取得する。

fukurou_keiba_v2 は ETL が済んだ過去データのみ保持するため、
今週末の未来レースは fukurou_jvdl.races + race_entries を参照する。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

import psycopg2.extras

from shared.db.jvdl import get_conn

logger = logging.getLogger(__name__)

# JRA 場コード → 場名
_KEIBAJO_NAME: dict[str, str] = {
    "01": "札幌", "02": "函館", "03": "福島", "04": "新潟",
    "05": "東京", "06": "中山", "07": "中京", "08": "京都",
    "09": "阪神", "10": "小倉",
}

# course_type テキスト → 数値 track_code（フロントの surfaceLabel() 用）
_COURSE_TYPE_TO_CODE: dict[str, str] = {
    "芝":    "10",
    "ダート": "20",
    "障害":   "51",
}

TARGET_RACE_NUMS: list[int] = [9, 10, 11, 12]

# jvdl スキーマ: races(id, date, place_code, race_number, name, course_type, distance, grade_code)
#               race_entries(race_id, horse_number, ...)
_SQL = """
SELECT
    r.id                                            AS race_id,
    r.race_number                                   AS race_num,
    r.place_code                                    AS keibajo_code,
    COALESCE(NULLIF(TRIM(r.name), ''), '')          AS race_name,
    r.date::date                                    AS race_date,
    r.distance,
    r.course_type,
    r.grade_code,
    COUNT(e.horse_id)                               AS syusso_tosu
FROM races r
LEFT JOIN race_entries e ON e.race_id = r.id
WHERE r.date >= %s
  AND r.date <  %s
  AND r.race_number = ANY(%s)
GROUP BY r.id, r.race_number, r.place_code, r.name, r.date, r.distance, r.course_type, r.grade_code
ORDER BY r.date, r.place_code, r.race_number
"""


@dataclass
class RaceInfo:
    race_id: str
    race_num: int
    race_name: str
    keibajo_code: str
    keibajo_name: str
    distance: int
    track_code: str | None
    grade_code: str | None
    race_date: str
    syusso_tosu: int | None


@dataclass
class VenueDay:
    date: str
    keibajo_code: str
    keibajo_name: str
    races: list[RaceInfo] = field(default_factory=list)


@dataclass
class WeekendRaces:
    weekend_start: str
    venues: list[VenueDay]


def _this_weekend() -> tuple[date, date]:
    """今週土曜・日曜の日付を返す。"""
    today = date.today()
    weekday = today.weekday()  # 0=Mon … 5=Sat 6=Sun
    if weekday == 5:
        sat = today
    elif weekday == 6:
        sat = today + timedelta(days=6)
    else:
        sat = today + timedelta(days=(5 - weekday))
    sun = sat + timedelta(days=1)
    return sat, sun


def fetch_weekend_races() -> WeekendRaces:
    """今週末の対象レース（9R〜12R）を fukurou_jvdl から取得して会場・日付でグループ化する。"""
    sat, sun = _this_weekend()
    # date オブジェクトで渡す（timestamp 型カラムに文字列を渡すと型エラーになるため）
    sun_next = sun + timedelta(days=1)
    logger.info("[RaceFetcher] jvdl 参照 対象日: %s 〜 %s", sat, sun)

    venues_map: dict[tuple[str, str], VenueDay] = {}

    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(_SQL, (sat, sun_next, TARGET_RACE_NUMS))
            rows = cur.fetchall()

    logger.info("[RaceFetcher] 取得件数: %d", len(rows))

    for row in rows:
        kc  = str(row["keibajo_code"]).strip().zfill(2)
        rd  = str(row["race_date"])  # already cast to date in SQL
        key = (rd, kc)
        if key not in venues_map:
            venues_map[key] = VenueDay(
                date=rd,
                keibajo_code=kc,
                keibajo_name=_KEIBAJO_NAME.get(kc, kc),
            )
        course_type = str(row["course_type"] or "").strip()
        track_code  = _COURSE_TYPE_TO_CODE.get(course_type)
        venues_map[key].races.append(RaceInfo(
            race_id     = str(row["race_id"]),
            race_num    = int(row["race_num"]),
            race_name   = str(row["race_name"]).strip() or f"{row['race_num']}R",
            keibajo_code= kc,
            keibajo_name= _KEIBAJO_NAME.get(kc, kc),
            distance    = int(row["distance"]) if row["distance"] else 0,
            track_code  = track_code,
            grade_code  = str(row["grade_code"]).strip() if row["grade_code"] else None,
            race_date   = rd,
            syusso_tosu = int(row["syusso_tosu"]) if row["syusso_tosu"] else None,
        ))

    return WeekendRaces(
        weekend_start=str(sat),
        venues=list(venues_map.values()),
    )
