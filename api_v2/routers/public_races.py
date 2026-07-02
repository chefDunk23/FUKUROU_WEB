"""
api_v2/routers/public_races.py
================================
認証不要の公開エンドポイント群。

GET /api/v2/public/analysis/bloodline  — 血統コーナー（父別単勝回収率集計、2026-07時点で一時無効化中）

2026-07: V2アンサンブル引退に伴い「公開用レース詳細（GET /api/v2/public/races/{race_id}）」
関連のPydanticモデル・変換関数を削除した（同エンドポイント自体は2026-06-27時点で既に廃止済み）。
削除内容は archive/v2_ensemble/api_v2/routers/public_races_detail_removed_snippet.py 参照。

設計原則:
  - JRA-VAN 生データの二次配布禁止を遵守: AI スコア・DB 集計のみ公開
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from shared.cache import CACHE_PFX

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v2/public", tags=["v2-public"])

# ── Redis（bloodline キャッシュ用）────────────────────────────────────────────
# 接続ロジック本体は shared/cache.py に共通化済み（get_redis_client）。

_BLOODLINE_CACHE_TTL = 3600
_BLOODLINE_CACHE_PFX = f"{CACHE_PFX}bloodline:v1:"


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


# 2026-07: 一時無効化。fukurou_jvdl の旧スキーマ(races/race_entries/horses、
# confirmed_rank・win_odds・course_type 等の列名)を前提にしており、
# races_v2/race_entries_v2 への書き換えは列名・単位変換を伴うため、
# 実データ検証なしでは回収率の正確性を保証できない。参考のため残す。
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

    2026-07 時点で一時無効化: _SQL_BLOODLINE が旧スキーマ
    (fukurou_jvdl.races/race_entries/horses) を前提にしており、
    races_v2/race_entries_v2 への書き換えには実データでの列名・単位検証が
    必要なため。races_v2 ベースでの再実装後に有効化すること。
    """
    raise HTTPException(
        status_code=503,
        detail="血統コーナーは一時的に利用できません（races_v2移行作業中）",
    )
