"""
api_v2/routers/race_level.py
==============================
GET /api/v2/race-level/{race_id} — レースレベル検証エンドポイント。

指定レースの全出走馬次走成績 + レース点数（RaceScore）を返す。
フロントエンドの RaceLevelPanel / RaceLevelModal がこのデータを使用する。
"""
from __future__ import annotations

import logging
from collections import defaultdict

import psycopg2.extras
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from shared.db.jvdata import get_conn as get_v2_conn

from ._race_common import _KEIBAJO_NAME, _surface_str
from .races import (
    OpponentResult,
    PastRaceRecord,
    RaceScore,
    _build_race_score,
    _fetch_daily_time_stats,
    _fetch_horse_name_map,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v2", tags=["v2-race-level"])

# ── Pydantic モデル ───────────────────────────────────────────────────────────


class RaceLevelRaceInfo(BaseModel):
    """GET /api/v2/race-level/:race_id — 対象レース基本情報。"""
    race_name:               str | None
    race_date:               str
    keibajo:                 str | None
    distance:                int | None
    surface:                 str | None
    grade_code:              str | None
    head_count:              int
    track_condition_warning: bool


class RaceLevelOpponentDetail(BaseModel):
    """同レース出走馬1頭の出走成績 + 次走情報。"""
    horse_id:        str
    horse_name:      str | None
    this_rank:       int
    this_margin:     float | None
    gate_num:        int | None    # 枠番（内外バイアス分析用）
    agari_3f:        float | None  # 上がり3F秒（前残り/差し決着分析用）
    next_race_id:    str | None
    next_race_name:  str | None
    next_race_date:  str | None
    next_grade_code: str | None
    next_race_rank:  int | None
    next_head_count: int | None


class RaceLevelResponse(BaseModel):
    """GET /api/v2/race-level/:race_id レスポンス。"""
    race_id:    str
    race_info:  RaceLevelRaceInfo
    race_score: RaceScore | None
    opponents:  list[RaceLevelOpponentDetail]


# ── SQL ───────────────────────────────────────────────────────────────────────

_SQL_RACE_LEVEL_INFO = """
SELECT
    id               AS race_id,
    race_date,
    race_name_hondai AS race_name,
    race_num,
    keibajo_code,
    distance,
    track_code,
    grade_code,
    shiba_baba_code,
    dirt_baba_code
FROM races
WHERE id = %s
"""

_SQL_RACE_LEVEL_ENTRIES = """
SELECT
    e.horse_id,
    e.kakutei_chakujun AS this_rank,
    e.race_time,
    e.wakuban,
    e.go_3f_time,
    CASE
        WHEN TRIM(e.time_diff) ~ '^[+-][0-9]+$'
        THEN GREATEST(0.0, TRIM(e.time_diff)::integer / 10.0)
        ELSE NULL
    END AS this_margin
FROM race_entries e
WHERE e.race_id = %s
  AND e.kakutei_chakujun IS NOT NULL
  AND e.kakutei_chakujun > 0
ORDER BY e.kakutei_chakujun
"""

# next_head_count: 次走レース全体の完走馬数
_SQL_RACE_LEVEL_NEXT_BULK = """
SELECT
    e.horse_id,
    r.id               AS next_race_id,
    r.race_date        AS next_race_date,
    r.race_name_hondai AS next_race_name,
    r.grade_code       AS next_grade_code,
    e.kakutei_chakujun AS next_rank,
    rc.head_count      AS next_head_count
FROM race_entries e
JOIN races r ON r.id = e.race_id
JOIN (
    SELECT race_id, COUNT(*) AS head_count
    FROM race_entries
    WHERE kakutei_chakujun IS NOT NULL AND kakutei_chakujun > 0
    GROUP BY race_id
) rc ON rc.race_id = r.id
WHERE e.horse_id = ANY(%s)
  AND r.race_date >= %s
  AND e.kakutei_chakujun IS NOT NULL
  AND e.kakutei_chakujun > 0
ORDER BY e.horse_id, r.race_date ASC, r.id ASC
"""

# ── ヘルパー ─────────────────────────────────────────────────────────────────


def _fetch_race_level(race_id: str) -> RaceLevelResponse | None:
    """GET /api/v2/race-level/{race_id} のデータを3クエリで取得する。
    ① race info (v2)  ② entries + next races (v2)  ③ horse names (jvdl)
    """
    try:
        with get_v2_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(_SQL_RACE_LEVEL_INFO, (race_id,))
                race_row = cur.fetchone()
                if not race_row:
                    return None

                cur.execute(_SQL_RACE_LEVEL_ENTRIES, (race_id,))
                entry_rows = cur.fetchall()

                if not entry_rows:
                    return None

                horse_ids = [str(r["horse_id"]) for r in entry_rows]
                race_date = race_row["race_date"]

                cur.execute(_SQL_RACE_LEVEL_NEXT_BULK, (horse_ids, race_date))
                next_rows = cur.fetchall()

    except Exception as e:
        logger.warning("[RaceLevel] DB取得失敗: %s", e)
        return None

    horse_name_map = _fetch_horse_name_map(horse_ids)

    # horse_id → 最初の次走行 (race_date > focal race_date)
    future_by_horse: dict[str, list] = defaultdict(list)
    for nr in next_rows:
        future_by_horse[str(nr["horse_id"])].append(nr)

    next_by_horse: dict[str, dict] = {}
    for hid, rows in future_by_horse.items():
        for nr in rows:
            if nr["next_race_date"] > race_date:
                next_by_horse[hid] = nr
                break

    # OpponentResult リスト（_build_race_score の member_level_score 計算用）
    opp_results: list[OpponentResult] = []
    opponents:   list[RaceLevelOpponentDetail] = []
    winner_time: float | None = None

    for row in entry_rows:
        hid        = str(row["horse_id"])
        margin_raw = row.get("this_margin")
        this_rank  = int(row["this_rank"])
        rt         = row.get("race_time")

        if this_rank == 1 and rt and float(rt) > 0:
            winner_time = float(rt)

        next_race = next_by_horse.get(hid)
        next_rank = int(next_race["next_rank"]) if next_race else None

        opp_results.append(OpponentResult(
            horse_id       = hid,
            this_rank      = this_rank,
            this_margin    = float(margin_raw) if margin_raw is not None else None,
            next_race_rank = next_rank,
        ))
        gate_raw  = row.get("wakuban")
        agari_raw = row.get("go_3f_time")
        opponents.append(RaceLevelOpponentDetail(
            horse_id        = hid,
            horse_name      = horse_name_map.get(hid),
            this_rank       = this_rank,
            this_margin     = float(margin_raw) if margin_raw is not None else None,
            gate_num        = int(gate_raw)      if gate_raw  is not None else None,
            agari_3f        = float(agari_raw)   if agari_raw is not None and float(agari_raw) > 0 else None,
            next_race_id    = str(next_race["next_race_id"])        if next_race else None,
            next_race_name  = next_race.get("next_race_name")       if next_race else None,
            next_race_date  = str(next_race["next_race_date"])      if next_race else None,
            next_grade_code = next_race.get("next_grade_code")      if next_race else None,
            next_race_rank  = next_rank,
            next_head_count = int(next_race["next_head_count"])     if next_race and next_race.get("next_head_count") else None,
        ))

    kc      = str(race_row["keibajo_code"]).strip().zfill(2)
    grade   = str(race_row["grade_code"]).strip() if race_row.get("grade_code") else None
    surface = _surface_str(race_row.get("track_code"))

    # race_score 計算（_fetch_daily_time_stats + _build_race_score）
    race_meta_map = {
        race_id: {
            "date":         race_row["race_date"],
            "keibajo_code": kc,
            "distance":     int(race_row["distance"]) if race_row.get("distance") else None,
            "track_code":   str(race_row["track_code"]).strip() if race_row.get("track_code") else None,
            "grade_code":   grade,
        }
    }
    time_stats_map = _fetch_daily_time_stats(race_meta_map)
    time_stats     = time_stats_map.get(race_id)

    pr_for_score = PastRaceRecord(
        race_id               = race_id,
        date                  = str(race_row["race_date"]),
        race_name             = race_row.get("race_name"),
        race_time             = winner_time,
        opponents_next_races  = opp_results,
    )
    race_score = _build_race_score(pr_for_score, time_stats, grade)

    tc_warning = bool(time_stats.get("track_condition_warning", False)) if time_stats else False

    return RaceLevelResponse(
        race_id   = race_id,
        race_info = RaceLevelRaceInfo(
            race_name               = race_row.get("race_name"),
            race_date               = str(race_row["race_date"]),
            keibajo                 = _KEIBAJO_NAME.get(kc, kc),
            distance                = int(race_row["distance"]) if race_row.get("distance") else None,
            surface                 = surface,
            grade_code              = grade,
            head_count              = len(entry_rows),
            track_condition_warning = tc_warning,
        ),
        race_score = race_score,
        opponents  = opponents,
    )


# ── エンドポイント ────────────────────────────────────────────────────────────

@router.get("/race-level/{race_id}", response_model=RaceLevelResponse)
def get_race_level(race_id: str) -> RaceLevelResponse:
    """レースレベル検証: 指定レースの全出走馬の次走成績を返す。

    出走馬の次走好走率からそのレースの価値（レベル）を可視化するための
    データを提供する。RaceScore（member_level + time_score + class_score）
    も算出して返す。
    """
    data = _fetch_race_level(race_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"race_id={race_id!r} が見つかりません")
    return data
