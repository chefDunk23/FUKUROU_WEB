"""
api_admin/routers/health.py
============================
GET /health/dashboard — システムヘルスダッシュボード（認証付き）
"""
from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel

from shared.health.checker import collect_dashboard

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


# ── レスポンスモデル ───────────────────────────────────────────────────────────

class FeatureStoreItem(BaseModel):
    name:           str
    last_updated:   str | None
    row_count:      int
    staleness_days: int
    status:         str   # "ok" | "warn" | "critical"


class JobsSummary(BaseModel):
    last_24h_done:    int
    last_24h_failed:  int
    last_failed_at:   str | None
    last_failed_type: str | None


class CacheSummary(BaseModel):
    race_predictions_today:  int
    race_detail_cache_today: int
    model_version:           str


class DashboardResponse(BaseModel):
    checked_at:              str
    feature_stores:          list[FeatureStoreItem]
    jobs_summary:            JobsSummary
    cache_summary:           CacheSummary
    keiba_v2_last_race_date: str | None
    overall_status:          str   # "ok" | "warn" | "critical"


# ── エンドポイント ─────────────────────────────────────────────────────────────

@router.get("/health/dashboard", response_model=DashboardResponse)
def get_health_dashboard() -> DashboardResponse:
    """
    システム全体のヘルスダッシュボード。

    - feature_stores: 全7ストアの staleness_days と status
    - jobs_summary:   24h 以内の完了/失敗件数
    - cache_summary:  本日分の予測キャッシュ件数 + モデルバージョン
    - overall_status: critical > warn > ok の優先順で集約
    """
    data = collect_dashboard()
    return DashboardResponse(
        checked_at = data.checked_at,
        feature_stores = [
            FeatureStoreItem(
                name           = s.name,
                last_updated   = s.last_updated,
                row_count      = s.row_count,
                staleness_days = s.staleness_days,
                status         = s.status,
            )
            for s in data.feature_stores
        ],
        jobs_summary = JobsSummary(
            last_24h_done    = data.jobs_summary.last_24h_done,
            last_24h_failed  = data.jobs_summary.last_24h_failed,
            last_failed_at   = data.jobs_summary.last_failed_at,
            last_failed_type = data.jobs_summary.last_failed_type,
        ),
        cache_summary = CacheSummary(
            race_predictions_today  = data.cache_summary.race_predictions_today,
            race_detail_cache_today = data.cache_summary.race_detail_cache_today,
            model_version           = data.cache_summary.model_version,
        ),
        keiba_v2_last_race_date = data.keiba_v2_last_race_date,
        overall_status          = data.overall_status,
    )
