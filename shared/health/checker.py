"""
shared/health/checker.py
=========================
ダッシュボード用ヘルスチェック集計ロジック。
api_admin/routers/health.py と scripts/health_check.py から共用する。

staleness 判定（仕様準拠）:
  - feature_store: warn=3日超, critical=7日超
  - keiba_v2_last_race_date: warn=14日超
  - jobs: 24h以内に failed があれば warn
  - overall_status: いずれか critical → critical, warn → warn, else ok
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any

import psycopg2
import psycopg2.extras

from shared.config import DB_JVDL, DB_V2
from shared.services.model_version import get_model_version

logger = logging.getLogger(__name__)

# ── 定数 ─────────────────────────────────────────────────────────────────────

STORE_NAMES: list[str] = [
    "jockey_feature_store",
    "trainer_feature_store",
    "sire_feature_store",
    "horse_rating_store",
    "chokyo_scores",
    "aptitude_scores",
    "training_feature_store",
]

_STORE_WARN_DAYS  = 3
_STORE_CRIT_DAYS  = 7
_V2_WARN_DAYS     = 14

# 各ストアの最終更新日を取得するクエリ
_MAX_DATE_SQL: dict[str, str] = {
    "jockey_feature_store":   "SELECT MAX(target_date) FROM jockey_feature_store",
    "trainer_feature_store":  "SELECT MAX(target_date) FROM trainer_feature_store",
    "sire_feature_store":     "SELECT MAX(target_date) FROM sire_feature_store",
    "horse_rating_store":     "SELECT MAX(race_date)   FROM horse_rating_store",
    "training_feature_store": "SELECT MAX(target_date) FROM training_feature_store",
    "chokyo_scores":          "SELECT MAX(computed_at::date) FROM chokyo_scores",
    "aptitude_scores":        "SELECT MAX(computed_at::date) FROM aptitude_scores",
}


# ── データクラス（Pydantic 依存なし。APIルータ側で変換）─────────────────────

class FeatureStoreInfo:
    __slots__ = ("name", "last_updated", "row_count", "staleness_days", "status")

    def __init__(
        self,
        name: str,
        last_updated: str | None,
        row_count: int,
        staleness_days: int,
        status: str,
    ) -> None:
        self.name          = name
        self.last_updated  = last_updated
        self.row_count     = row_count
        self.staleness_days = staleness_days
        self.status        = status


class JobsSummaryData:
    __slots__ = ("last_24h_done", "last_24h_failed", "last_failed_at", "last_failed_type")

    def __init__(
        self,
        last_24h_done: int,
        last_24h_failed: int,
        last_failed_at: str | None,
        last_failed_type: str | None,
    ) -> None:
        self.last_24h_done    = last_24h_done
        self.last_24h_failed  = last_24h_failed
        self.last_failed_at   = last_failed_at
        self.last_failed_type = last_failed_type


class CacheSummaryData:
    __slots__ = ("race_predictions_today", "race_detail_cache_today", "model_version")

    def __init__(
        self,
        race_predictions_today: int,
        race_detail_cache_today: int,
        model_version: str,
    ) -> None:
        self.race_predictions_today  = race_predictions_today
        self.race_detail_cache_today = race_detail_cache_today
        self.model_version           = model_version


class DashboardData:
    __slots__ = (
        "checked_at", "feature_stores", "jobs_summary",
        "cache_summary", "keiba_v2_last_race_date", "overall_status",
    )

    def __init__(
        self,
        checked_at: str,
        feature_stores: list[FeatureStoreInfo],
        jobs_summary: JobsSummaryData,
        cache_summary: CacheSummaryData,
        keiba_v2_last_race_date: str | None,
        overall_status: str,
    ) -> None:
        self.checked_at              = checked_at
        self.feature_stores          = feature_stores
        self.jobs_summary            = jobs_summary
        self.cache_summary           = cache_summary
        self.keiba_v2_last_race_date = keiba_v2_last_race_date
        self.overall_status          = overall_status


# ── 内部ヘルパー ──────────────────────────────────────────────────────────────

def _jvdl_conn() -> Any:
    return psycopg2.connect(**DB_JVDL)


def _v2_conn() -> Any:
    return psycopg2.connect(**DB_V2)


def _to_date(val: Any) -> date | None:
    if val is None:
        return None
    return val if isinstance(val, date) else val.date()


def _staleness_status(lag: int) -> str:
    if lag >= _STORE_CRIT_DAYS:
        return "critical"
    if lag >= _STORE_WARN_DAYS:
        return "warn"
    return "ok"


# ── 集計関数 ──────────────────────────────────────────────────────────────────

def collect_feature_stores(today: date) -> list[FeatureStoreInfo]:
    results: list[FeatureStoreInfo] = []
    try:
        conn = _jvdl_conn()
        cur = conn.cursor()
        for name in STORE_NAMES:
            last_updated: str | None = None
            row_count = 0
            staleness_days = 9999
            status = "critical"
            try:
                # 最終更新日
                cur.execute(_MAX_DATE_SQL[name])
                row = cur.fetchone()
                last_date = _to_date(row[0]) if row else None
                if last_date is not None:
                    staleness_days = (today - last_date).days
                    last_updated = last_date.isoformat()
                    status = _staleness_status(staleness_days)
                # 行数
                cur.execute(f"SELECT COUNT(*) FROM {name}")  # noqa: S608
                cnt_row = cur.fetchone()
                row_count = int(cnt_row[0]) if cnt_row else 0
            except Exception as e:
                logger.warning("[checker] %s クエリ失敗: %s", name, e)
            results.append(FeatureStoreInfo(
                name=name,
                last_updated=last_updated,
                row_count=row_count,
                staleness_days=staleness_days,
                status=status,
            ))
        conn.close()
    except Exception as e:
        logger.error("[checker] JVDL 接続失敗: %s", e)
        for name in STORE_NAMES:
            if not any(r.name == name for r in results):
                results.append(FeatureStoreInfo(
                    name=name, last_updated=None,
                    row_count=0, staleness_days=9999, status="critical",
                ))
    return results


def collect_jobs_summary() -> JobsSummaryData:
    defaults = JobsSummaryData(
        last_24h_done=0, last_24h_failed=0,
        last_failed_at=None, last_failed_type=None,
    )
    try:
        conn = _jvdl_conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE status = 'done')   AS done,
                COUNT(*) FILTER (WHERE status = 'failed') AS failed
            FROM jobs
            WHERE created_at >= NOW() - INTERVAL '24 hours'
        """)
        row = cur.fetchone()
        done   = int(row["done"])   if row else 0
        failed = int(row["failed"]) if row else 0

        last_failed_at:   str | None = None
        last_failed_type: str | None = None
        if failed > 0:
            cur.execute("""
                SELECT job_type, finished_at
                FROM   jobs
                WHERE  status = 'failed'
                  AND  created_at >= NOW() - INTERVAL '24 hours'
                ORDER  BY finished_at DESC NULLS LAST
                LIMIT  1
            """)
            fr = cur.fetchone()
            if fr:
                last_failed_type = str(fr["job_type"])
                if fr["finished_at"]:
                    ts = fr["finished_at"]
                    if not ts.tzinfo:
                        ts = ts.replace(tzinfo=timezone.utc)
                    last_failed_at = ts.isoformat()
        conn.close()
        return JobsSummaryData(
            last_24h_done=done,
            last_24h_failed=failed,
            last_failed_at=last_failed_at,
            last_failed_type=last_failed_type,
        )
    except Exception as e:
        logger.error("[checker] jobs 集計失敗: %s", e)
        return defaults


def collect_cache_summary() -> CacheSummaryData:
    predictions_today = 0
    detail_today      = 0
    model_version     = "unknown"
    try:
        conn = _jvdl_conn()
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT COUNT(*) FROM race_predictions"
                " WHERE predicted_at::date = CURRENT_DATE"
            )
            r = cur.fetchone()
            predictions_today = int(r[0]) if r else 0
        except Exception:
            pass
        try:
            cur.execute(
                "SELECT COUNT(*) FROM race_detail_cache"
                " WHERE computed_at::date = CURRENT_DATE"
            )
            r = cur.fetchone()
            detail_today = int(r[0]) if r else 0
        except Exception:
            pass
        conn.close()
    except Exception as e:
        logger.error("[checker] cache 集計失敗: %s", e)

    try:
        model_version = get_model_version()
    except Exception:
        pass

    return CacheSummaryData(
        race_predictions_today=predictions_today,
        race_detail_cache_today=detail_today,
        model_version=model_version,
    )


def collect_keiba_v2_last(today: date) -> tuple[str | None, str]:
    """(ISO date string | None, status)

    DB_V2.races と DB_JVDL.races_v2 の両方を確認し、新しい方の日付を返す。
    sync_races_from_jvdl ジョブ実行前でも races_v2 のデータが反映される。
    """
    candidates: list[date] = []

    # DB_V2 (fukurou_keiba_v2)
    try:
        conn = _v2_conn()
        cur = conn.cursor()
        cur.execute("SELECT MAX(race_date) FROM races")
        row = cur.fetchone()
        conn.close()
        d = _to_date(row[0]) if row else None
        if d:
            candidates.append(d)
    except Exception as e:
        logger.warning("[checker] keiba_v2.races 失敗: %s", e)

    # DB_JVDL (races_v2) — bulk_ingest_v2.py の投入先
    try:
        conn = _jvdl_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT MAX(kaisai_year || kaisai_monthday) FROM races_v2"
            " WHERE LENGTH(kaisai_year) = 4 AND LENGTH(kaisai_monthday) = 4"
        )
        row = cur.fetchone()
        conn.close()
        raw = row[0] if row else None
        if raw and len(raw) == 8:
            d2 = date.fromisoformat(f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}")
            candidates.append(d2)
    except Exception as e:
        logger.warning("[checker] jvdl.races_v2 失敗: %s", e)

    if not candidates:
        return None, "warn"

    last = max(candidates)
    lag = (today - last).days
    status = "warn" if lag >= _V2_WARN_DAYS else "ok"
    return last.isoformat(), status


def _calc_overall(
    stores: list[FeatureStoreInfo],
    jobs: JobsSummaryData,
    v2_status: str,
) -> str:
    statuses: list[str] = [s.status for s in stores] + [v2_status]
    if jobs.last_24h_failed > 0:
        statuses.append("warn")
    if "critical" in statuses:
        return "critical"
    if "warn" in statuses:
        return "warn"
    return "ok"


# ── メイン集計エントリポイント ─────────────────────────────────────────────────

def collect_dashboard() -> DashboardData:
    now   = datetime.now(tz=timezone.utc)
    today = now.date()

    stores     = collect_feature_stores(today)
    jobs       = collect_jobs_summary()
    cache      = collect_cache_summary()
    v2_date, v2_status = collect_keiba_v2_last(today)
    overall    = _calc_overall(stores, jobs, v2_status)

    return DashboardData(
        checked_at              = now.isoformat(),
        feature_stores          = stores,
        jobs_summary            = jobs,
        cache_summary           = cache,
        keiba_v2_last_race_date = v2_date,
        overall_status          = overall,
    )
