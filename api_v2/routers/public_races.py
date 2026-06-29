"""
api_v2/routers/public_races.py
================================
認証不要の公開エンドポイント群。

GET /api/v2/public/races/{race_id}     — 公開用レース詳細（フィールド絞り込み版）
GET /api/v2/public/analysis/bloodline  — 血統コーナー（父別単勝回収率集計）

設計原則:
  - past_races / opponents_next_races / submodel_scores / sire_name を含まない
  - 既存の race_detail_cache をそのまま流用（二重推論なし）
  - JRA-VAN 生データの二次配布禁止を遵守: AI スコア・DB 集計のみ公開
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import psycopg2.extras
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from shared.cache import CACHE_PFX, get_redis_client
from shared.db.jvdl import get_conn as get_jvdl_conn
from shared.services.model_version import get_model_version

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v2/public", tags=["v2-public"])

# ── Redis（bloodline キャッシュ用）────────────────────────────────────────────
# 接続ロジック本体は shared/cache.py に共通化済み（get_redis_client）。

_BLOODLINE_CACHE_TTL = 3600
_BLOODLINE_CACHE_PFX = f"{CACHE_PFX}bloodline:v1:"


# ── タスク1: 公開用レース詳細 Pydantic モデル ──────────────────────────────────

class PublicHorseExtra(BaseModel):
    """JRA-VAN 二次配布禁止: past_races / sire_name / dam_sire_name を除外。"""
    prev_race_grade:      str | None = None
    prev_race_rank:       int | None = None
    prev_race_days_ago:   int | None = None
    chokyo_score:         float | None = None
    ten_index:            float | None = None
    agari_index:          float | None = None
    position_tendency:    float | None = None
    predicted_field_pace: float | None = None
    pace_harmony:         float | None = None


class PublicRaceDetailHorse(BaseModel):
    """submodel_scores を除外した公開版出走馬情報。"""
    umaban:        int
    wakuban:       int | None = None
    horse_id:      str
    horse_name:    str | None = None
    jockey_name:   str | None = None
    trainer_name:  str | None = None
    horse_weight:  int | None = None
    weight_diff:   int | None = None
    burden_weight: float
    tan_odds:      float | None = None
    ninki:         int | None = None
    ai_score:      float
    ai_rank:       int
    extra:         PublicHorseExtra


class PublicRaceInfo(BaseModel):
    pace_prediction: str
    bias_note:       str
    positioning_map: dict | None = None
    track_bias:      dict | None = None


class PublicRaceDetailResponse(BaseModel):
    race_id:         str
    race_date:       str
    keibajo_name:    str
    race_num:        int
    race_name:       str
    distance:        int
    track_code:      str
    grade_code:      str | None = None
    class_label:     str | None = None
    is_special:      bool = False
    syusso_tosu:     int
    weather:         str
    track_condition: str
    race_info:       PublicRaceInfo
    horses:          list[PublicRaceDetailHorse]
    computed_at:     str | None = None


def _to_public_response(full: object) -> PublicRaceDetailResponse:
    """RaceDetailResponse → PublicRaceDetailResponse にフィールドを絞り込む。"""
    public_horses: list[PublicRaceDetailHorse] = []
    for h in full.horses:  # type: ignore[attr-defined]
        public_horses.append(PublicRaceDetailHorse(
            umaban        = h.umaban,
            wakuban       = h.wakuban,
            horse_id      = h.horse_id,
            horse_name    = h.horse_name,
            jockey_name   = h.jockey_name,
            trainer_name  = h.trainer_name,
            horse_weight  = h.horse_weight,
            weight_diff   = h.weight_diff,
            burden_weight = h.burden_weight,
            tan_odds      = h.tan_odds,
            ninki         = h.ninki,
            ai_score      = h.ai_score,
            ai_rank       = h.ai_rank,
            extra         = PublicHorseExtra(
                prev_race_grade      = h.extra.prev_race_grade,
                prev_race_rank       = h.extra.prev_race_rank,
                prev_race_days_ago   = h.extra.prev_race_days_ago,
                chokyo_score         = h.extra.chokyo_score,
                ten_index            = h.extra.ten_index,
                agari_index          = h.extra.agari_index,
                position_tendency    = h.extra.position_tendency,
                predicted_field_pace = h.extra.predicted_field_pace,
                pace_harmony         = h.extra.pace_harmony,
            ),
        ))

    ri = full.race_info  # type: ignore[attr-defined]
    return PublicRaceDetailResponse(
        race_id         = full.race_id,  # type: ignore[attr-defined]
        race_date       = full.race_date,  # type: ignore[attr-defined]
        keibajo_name    = full.keibajo_name,  # type: ignore[attr-defined]
        race_num        = full.race_num,  # type: ignore[attr-defined]
        race_name       = full.race_name,  # type: ignore[attr-defined]
        distance        = full.distance,  # type: ignore[attr-defined]
        track_code      = full.track_code,  # type: ignore[attr-defined]
        grade_code      = full.grade_code,  # type: ignore[attr-defined]
        class_label     = full.class_label,  # type: ignore[attr-defined]
        is_special      = full.is_special,  # type: ignore[attr-defined]
        syusso_tosu     = full.syusso_tosu,  # type: ignore[attr-defined]
        weather         = full.weather,  # type: ignore[attr-defined]
        track_condition = full.track_condition,  # type: ignore[attr-defined]
        race_info       = PublicRaceInfo(
            pace_prediction = ri.pace_prediction,
            bias_note       = ri.bias_note,
            positioning_map = ri.positioning_map.model_dump() if ri.positioning_map else None,
            track_bias      = ri.track_bias.model_dump()      if ri.track_bias      else None,
        ),
        horses      = public_horses,
        computed_at = full.computed_at,  # type: ignore[attr-defined]
    )


# GET /api/v2/public/races/{race_id} は廃止済み（2026-06-27 Phase B）
# 将来必要になったら新規作成すること。Pydanticモデルはテスト資産として維持。

# ── 血統コーナー ─────────────────────────────────────────────────────────────

class BloodlineInsight(BaseModel):
    sire_name:       str
    sire_id:         str
    surface:         str    # "芝" | "ダ"
    run_count:       int
    tan_return_rate: float  # 単勝回収率 (%)
    win_rate:        float  # 勝率 (%)
    place_rate:      float  # 複勝率 (%)


class BloodlineResponse(BaseModel):
    insights:     list[BloodlineInsight]
    total_count:  int
    generated_at: str


_SQL_BLOODLINE = """
SELECT
    h_sire.id                                            AS sire_id,
    COALESCE(NULLIF(TRIM(h_sire.name), ''), h_sire.id)  AS sire_name,
    CASE WHEN r.course_type = 'ダート' THEN 'ダ' ELSE '芝' END AS surface,
    COUNT(*)                                             AS run_count,
    SUM(CASE WHEN e.confirmed_rank = 1
             THEN COALESCE(e.win_odds, 0) * 100
             ELSE 0 END
    ) / NULLIF(COUNT(*), 0)                              AS tan_return_rate,
    SUM(CASE WHEN e.confirmed_rank = 1 THEN 1 ELSE 0 END)
        * 100.0 / NULLIF(COUNT(*), 0)                    AS win_rate,
    SUM(CASE WHEN e.confirmed_rank <= 3 THEN 1 ELSE 0 END)
        * 100.0 / NULLIF(COUNT(*), 0)                    AS place_rate
FROM race_entries e
JOIN races r        ON r.id      = e.race_id
JOIN horses h_self  ON h_self.id = e.horse_id
JOIN horses h_sire  ON h_sire.id = h_self.sire_id
WHERE e.confirmed_rank IS NOT NULL
  AND e.confirmed_rank  > 0
  AND h_self.sire_id IS NOT NULL
  AND r.course_type IN ('芝', 'ダート')
  AND r.date >= '2022-01-01'
  AND r.date  < CURRENT_DATE
  AND (%(surface)s IS NULL
       OR (%(surface)s = 'ダ' AND r.course_type = 'ダート')
       OR (%(surface)s = '芝' AND r.course_type = '芝'))
  AND (%(keibajo_code)s IS NULL OR r.place_code = %(keibajo_code)s)
  AND (%(dist_min)s IS NULL OR r.distance >= %(dist_min)s)
  AND (%(dist_max)s IS NULL OR r.distance <= %(dist_max)s)
GROUP BY h_sire.id, h_sire.name, surface
HAVING COUNT(*) >= 30
   AND SUM(CASE WHEN e.confirmed_rank = 1
                THEN COALESCE(e.win_odds, 0) * 100
                ELSE 0 END
       ) / NULLIF(COUNT(*), 0) >= %(min_return_rate)s
ORDER BY tan_return_rate DESC
LIMIT %(limit_)s
"""


@router.get("/analysis/bloodline", response_model=BloodlineResponse)
def get_bloodline_analysis(
    surface:         str | None = Query(None,  description="'芝' or 'ダ'"),
    keibajo_code:    str | None = Query(None,  description="競馬場コード (例: '05'=東京)"),
    dist_min:        int | None = Query(None,  description="距離下限 (m)"),
    dist_max:        int | None = Query(None,  description="距離上限 (m)"),
    min_return_rate: float      = Query(100.0, description="単勝回収率下限 (%)"),
    limit:           int        = Query(50,    ge=1, le=200),
) -> BloodlineResponse:
    """
    血統コーナー: 父別単勝回収率ランキング。

    条件: 出走数 >= 30 AND 単勝回収率 >= min_return_rate
    キャッシュ: Redis TTL 3600s
    """
    cache_key = (
        f"{_BLOODLINE_CACHE_PFX}{surface}:{keibajo_code}:{dist_min}:{dist_max}"
        f":{min_return_rate}:{limit}"
    )
    r = get_redis_client()
    if r:
        try:
            cached = r.get(cache_key)  # type: ignore[union-attr]
            if cached:
                return BloodlineResponse.model_validate(json.loads(cached))
        except Exception as e:
            logger.warning("[Bloodline] Redis get失敗: %s", e)

    try:
        with get_jvdl_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(_SQL_BLOODLINE, {
                    "surface":         surface,
                    "keibajo_code":    keibajo_code,
                    "dist_min":        dist_min,
                    "dist_max":        dist_max,
                    "min_return_rate": min_return_rate,
                    "limit_":          limit,
                })
                rows = cur.fetchall()
    except Exception as e:
        logger.exception("[Bloodline] クエリ失敗: %s", e)
        raise HTTPException(status_code=500, detail="血統データ取得エラー")

    insights: list[BloodlineInsight] = []
    for row in rows:
        trr = float(row["tan_return_rate"] or 0.0)
        insights.append(BloodlineInsight(
            sire_name       = str(row["sire_name"] or "不明"),
            sire_id         = str(row["sire_id"]),
            surface         = str(row["surface"]),
            run_count       = int(row["run_count"]),
            tan_return_rate = round(trr, 1),
            win_rate        = round(float(row["win_rate"]   or 0.0), 1),
            place_rate      = round(float(row["place_rate"] or 0.0), 1),
        ))

    result = BloodlineResponse(
        insights     = insights,
        total_count  = len(insights),
        generated_at = datetime.now(timezone.utc).isoformat(),
    )

    if r:
        try:
            r.setex(cache_key, _BLOODLINE_CACHE_TTL, result.model_dump_json())  # type: ignore[union-attr]
        except Exception as e:
            logger.warning("[Bloodline] Redis set失敗: %s", e)

    return result
