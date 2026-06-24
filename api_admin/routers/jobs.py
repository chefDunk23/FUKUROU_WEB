"""
api_admin/routers/jobs.py
==========================
POST /jobs      — ジョブ投入
GET  /jobs      — ジョブ一覧（直近 N 件）
GET  /jobs/{id} — ジョブ詳細 + ログ
POST /jobs/{id}/cancel — キャンセル（queued のみ）
"""
from __future__ import annotations

import logging

import psycopg2
import psycopg2.extras
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from shared.config import DB_JVDL

logger = logging.getLogger(__name__)
router = APIRouter(tags=["jobs"])

# 受け付ける job_type 一覧（ドキュメント + バリデーション用）
_KNOWN_JOB_TYPES = frozenset({
    "recompute_predictions",
    "update_feature_stores",
    "sync_races_from_jvdl",
    "sync_jvdata",
    "train_v2_submodels",
    "train_v2_ensemble",
    "merge_v2_submodel_scores",
    "enrich_ability_v3",
    "backtest_strategies_v3",
    "classic_video_generate_prompt",
    "classic_video_render",
    "import_bloodline_masters",
    "run_tipster_evaluation",
    "run_tipster_backtest",
})


# ── モデル ────────────────────────────────────────────────────────────────────

class JobSubmitRequest(BaseModel):
    job_type: str = Field(..., description=f"ジョブ種別。有効値: {sorted(_KNOWN_JOB_TYPES)}")
    params: dict = Field(default_factory=dict, description="ジョブパラメータ (jsonb)")


class JobResponse(BaseModel):
    id: int
    job_type: str
    params: dict
    status: str
    progress: int
    log_tail: str | None
    artifact_path: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None


# ── ヘルパー ──────────────────────────────────────────────────────────────────

def _row_to_response(row: dict) -> JobResponse:
    return JobResponse(
        id=row["id"],
        job_type=row["job_type"],
        params=row["params"] or {},
        status=row["status"],
        progress=row["progress"],
        log_tail=row.get("log_tail"),
        artifact_path=row.get("artifact_path"),
        created_at=row["created_at"].isoformat(),
        started_at=row["started_at"].isoformat() if row.get("started_at") else None,
        finished_at=row["finished_at"].isoformat() if row.get("finished_at") else None,
    )


def _get_conn():
    return psycopg2.connect(**DB_JVDL)


# ── エンドポイント ─────────────────────────────────────────────────────────────

@router.post("/jobs", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
def submit_job(req: JobSubmitRequest) -> JobResponse:
    """ジョブをキューに投入する。ワーカーが次のポーリングで実行する。"""
    if req.job_type not in _KNOWN_JOB_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"不明な job_type: {req.job_type!r}。有効値: {sorted(_KNOWN_JOB_TYPES)}",
        )
    with _get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "INSERT INTO jobs (job_type, params) VALUES (%s, %s) RETURNING *",
                (req.job_type, psycopg2.extras.Json(req.params)),
            )
            row = dict(cur.fetchone())
        conn.commit()

    logger.info("[Admin] ジョブ投入: id=%d type=%s", row["id"], row["job_type"])
    return _row_to_response(row)


@router.get("/jobs", response_model=list[JobResponse])
def list_jobs(
    status_filter: str | None = None,
    limit: int = 50,
) -> list[JobResponse]:
    """ジョブ一覧を返す（デフォルト: 直近 50 件、作成降順）。"""
    limit = max(1, min(limit, 200))
    sql = "SELECT * FROM jobs"
    args: list = []
    if status_filter:
        sql += " WHERE status = %s"
        args.append(status_filter)
    sql += " ORDER BY created_at DESC LIMIT %s"
    args.append(limit)

    with _get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, args)
            rows = [dict(r) for r in cur.fetchall()]

    return [_row_to_response(r) for r in rows]


@router.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: int) -> JobResponse:
    """ジョブ詳細（状態・ログ・成果物パス）を返す。"""
    with _get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM jobs WHERE id = %s", (job_id,))
            row = cur.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    return _row_to_response(dict(row))


@router.post("/jobs/{job_id}/cancel", response_model=JobResponse)
def cancel_job(job_id: int) -> JobResponse:
    """キューイング中のジョブをキャンセルする（running は不可）。"""
    with _get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "UPDATE jobs SET status = 'cancelled', finished_at = now()"
                " WHERE id = %s AND status = 'queued'"
                " RETURNING *",
                (job_id,),
            )
            row = cur.fetchone()
        conn.commit()

    if row is None:
        # queued でないか存在しない
        with _get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM jobs WHERE id = %s", (job_id,))
                existing = cur.fetchone()
        if existing is None:
            raise HTTPException(status_code=404, detail=f"job {job_id} not found")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"job {job_id} は status={existing['status']} のためキャンセル不可",
        )

    logger.info("[Admin] ジョブキャンセル: id=%d", job_id)
    return _row_to_response(dict(row))
