"""
api_v2/routers/db_status.py
============================
DB状態管理エンドポイント。

  GET  /api/v2/db-status   — テーブル行数・最新日付・同期状況・ワーカー稼働状態を返す
  POST /api/v2/db-sync     — sync_jvdata / sync_races_from_jvdl ジョブをキューに投入
  GET  /api/v2/db-sync/{job_id} — ジョブ状態を返す

運用方針（常駐ワーカーなし）:
  ジョブはキューに積むだけで、このAPIプロセス内では実行しない
  （旧: BackgroundTasks によるインプロセス実行は --reload 再起動時に
  ジョブが無言で消失する不具合があったため廃止）。
  実行するには `worker.bat` でワーカーを起動する（起動すると
  溜まっているジョブを全て処理し、アイドルになると自動終了する）。
"""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Any

import psycopg2
import psycopg2.extras
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from shared.config import DB_JVDL, DB_V2

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v2", tags=["db-status"])

# ── 許可ジョブ種別 ────────────────────────────────────────────────────────────

_ALLOWED_JOB_TYPES = frozenset({"sync_jvdata", "sync_races_from_jvdl"})


# ── ヘルパー ──────────────────────────────────────────────────────────────────

def _jvdl_conn():
    return psycopg2.connect(**DB_JVDL)


def _v2_conn():
    return psycopg2.connect(**DB_V2)


def _is_worker_running() -> bool:
    """shared/worker/job_runner.py が現在起動中かを advisory lock の保持有無で判定する。

    job_runner.py は起動時に pg_try_advisory_lock(_ADVISORY_LOCK_KEY) を保持し続ける。
    ここで同じキーの取得を試み、取得できれば「誰も保持していない=ワーカー未起動」、
    取得できなければ「誰かが保持している=ワーカー起動中」と判定する
    （取得できた場合は直ちに解放し、本来のワーカーの動作に影響を与えない）。
    """
    from shared.worker.job_runner import _ADVISORY_LOCK_KEY

    try:
        conn = _jvdl_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_try_advisory_lock(%s)", (_ADVISORY_LOCK_KEY,))
                acquired = bool(cur.fetchone()[0])
                if acquired:
                    cur.execute("SELECT pg_advisory_unlock(%s)", (_ADVISORY_LOCK_KEY,))
            conn.commit()
            return not acquired
        finally:
            conn.close()
    except Exception as e:
        logger.warning("ワーカー稼働状態の判定に失敗: %s", e)
        return False


def _race_id_to_date(race_id: str | None) -> str | None:
    """race_id の先頭8桁を YYYY-MM-DD 形式に変換する。"""
    if not race_id or len(race_id) < 8:
        return None
    s = str(race_id)[:8]
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def _chokyo_date_to_date(d: Any) -> str | None:
    """chokyo_date（date型 or str）を YYYY-MM-DD 文字列に変換する。"""
    if d is None:
        return None
    if hasattr(d, "isoformat"):
        return d.isoformat()
    s = str(d).replace("-", "")
    if len(s) >= 8:
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return str(d)


def _safe_query(cur, sql: str, params=()) -> Any:
    """エラー時に None を返すクエリラッパー。"""
    try:
        cur.execute(sql, params)
        return cur.fetchone()
    except Exception as e:
        logger.warning("クエリ失敗 [%s...]: %s", sql[:60], e)
        return None


# ── エンドポイント: GET /api/v2/db-status ─────────────────────────────────────

@router.get("/db-status")
def get_db_status():
    """テーブル行数・最新データ日付・同期ウォーターマーク・ジョブ履歴を返す。"""

    # ─── JVDL DB ─────────────────────────────────────────────────────────────
    jvdl_tables: dict[str, dict] = {}
    watermarks: list[dict] = []
    sync_jobs: list[dict] = []

    try:
        conn_jv = _jvdl_conn()
        with conn_jv.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

            # sync_watermark
            row = _safe_query(cur, "SELECT dataspec, last_synced_at, updated_at FROM sync_watermark ORDER BY dataspec")
            cur.execute("SELECT dataspec, last_synced_at, updated_at FROM sync_watermark ORDER BY dataspec")
            for r in cur.fetchall():
                watermarks.append({
                    "dataspec":      r["dataspec"],
                    "last_synced_at": str(r["last_synced_at"]),
                    "updated_at":    r["updated_at"].isoformat() if r["updated_at"] else None,
                })

            # payouts
            r = _safe_query(cur, "SELECT MAX(race_id) AS max_id, COUNT(*) AS cnt FROM payouts")
            jvdl_tables["payouts"] = {
                "max_date": _race_id_to_date(r["max_id"] if r else None),
                "count":    int(r["cnt"]) if r else 0,
            }

            # races_v2 / race_entries_v2（改良版スキーマ = 予測パイプラインが実際に使用するテーブル）
            r = _safe_query(cur, "SELECT MAX(race_id) AS max_id, COUNT(*) AS cnt FROM races_v2")
            jvdl_tables["races"] = {
                "max_date": _race_id_to_date(r["max_id"] if r else None),
                "count":    int(r["cnt"]) if r else 0,
            }

            r = _safe_query(cur, "SELECT MAX(race_id) AS max_id, COUNT(*) AS cnt FROM race_entries_v2")
            jvdl_tables["race_entries"] = {
                "max_date": _race_id_to_date(r["max_id"] if r else None),
                "count":    int(r["cnt"]) if r else 0,
            }

            # races / race_entries（JVDLフォーマット・旧スキーマ）
            # bulk_ingest_v2.py / jvdl_parser.sink はこのテーブルには一切書き込まない
            # （races_v2 / race_entries_v2 に統合済み）。予測パイプラインからも未参照。
            # 参考表示のみのため画面上は「旧・未使用」と明記する。
            r = _safe_query(cur, "SELECT MAX(id) AS max_id, COUNT(*) AS cnt FROM races")
            jvdl_tables["races_legacy"] = {
                "max_date": _race_id_to_date(r["max_id"] if r else None),
                "count":    int(r["cnt"]) if r else 0,
            }

            r = _safe_query(cur, "SELECT MAX(race_id) AS max_id, COUNT(*) AS cnt FROM race_entries")
            jvdl_tables["race_entries_legacy"] = {
                "max_date": _race_id_to_date(r["max_id"] if r else None),
                "count":    int(r["cnt"]) if r else 0,
            }

            # training_slope
            r = _safe_query(cur, "SELECT MAX(chokyo_date) AS max_d, COUNT(*) AS cnt FROM training_slope")
            jvdl_tables["training_slope"] = {
                "max_date": _chokyo_date_to_date(r["max_d"] if r else None),
                "count":    int(r["cnt"]) if r else 0,
            }

            # training_wood
            r = _safe_query(cur, "SELECT MAX(chokyo_date) AS max_d, COUNT(*) AS cnt FROM training_wood")
            jvdl_tables["training_wood"] = {
                "max_date": _chokyo_date_to_date(r["max_d"] if r else None),
                "count":    int(r["cnt"]) if r else 0,
            }

            # horse_weights（馬体重）
            r = _safe_query(cur, "SELECT COUNT(*) AS cnt FROM horse_weights")
            jvdl_tables["horse_weights"] = {
                "max_date": None,
                "count":    int(r["cnt"]) if r else 0,
            }

            # 最新ジョブ（sync_jvdata / sync_races_from_jvdl）
            cur.execute("""
                SELECT DISTINCT ON (job_type)
                    id, job_type, status, progress, log_tail,
                    created_at, started_at, finished_at
                FROM jobs
                WHERE job_type IN ('sync_jvdata', 'sync_races_from_jvdl')
                ORDER BY job_type, id DESC
            """)
            for r in cur.fetchall():
                sync_jobs.append({
                    "id":          r["id"],
                    "job_type":    r["job_type"],
                    "status":      r["status"],
                    "progress":    r["progress"],
                    "log_tail":    r["log_tail"],
                    "created_at":  r["created_at"].isoformat() if r["created_at"] else None,
                    "started_at":  r["started_at"].isoformat() if r["started_at"] else None,
                    "finished_at": r["finished_at"].isoformat() if r["finished_at"] else None,
                })

        conn_jv.close()
    except Exception as e:
        logger.error("JVDL DB 接続失敗: %s", e)

    # ─── V2 DB ───────────────────────────────────────────────────────────────
    v2_tables: dict[str, dict] = {}
    weekend_status: dict = {}

    try:
        conn_v2 = _v2_conn()
        with conn_v2.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

            # races (V2)
            r = _safe_query(cur, "SELECT MAX(id) AS max_id, COUNT(*) AS cnt FROM races")
            v2_tables["races"] = {
                "max_date": _race_id_to_date(r["max_id"] if r else None),
                "count":    int(r["cnt"]) if r else 0,
            }

            # race_entries (V2)
            r = _safe_query(cur, "SELECT MAX(race_id) AS max_id, COUNT(*) AS cnt FROM race_entries")
            v2_tables["race_entries"] = {
                "max_date": _race_id_to_date(r["max_id"] if r else None),
                "count":    int(r["cnt"]) if r else 0,
            }

            # 今週末（土日）のレース状況
            today = date.today()
            # 今週土曜
            sat = today + timedelta(days=(5 - today.weekday()) % 7)
            sun = sat + timedelta(days=1)

            cur.execute("""
                SELECT r.race_date,
                       COUNT(DISTINCT r.id)           AS race_cnt,
                       COUNT(re.horse_id)             AS entry_cnt,
                       MIN(r.syusso_tosu)             AS min_tosu,
                       MAX(r.syusso_tosu)             AS max_tosu
                FROM   races r
                LEFT JOIN race_entries re ON re.race_id = r.id
                WHERE  r.race_date IN (%s, %s)
                GROUP  BY r.race_date
                ORDER  BY r.race_date
            """, (sat, sun))
            weekend_rows = []
            for row in cur.fetchall():
                weekend_rows.append({
                    "date":       row["race_date"].isoformat(),
                    "race_count": int(row["race_cnt"]),
                    "entry_count": int(row["entry_cnt"]),
                    "min_tosu":   row["min_tosu"],
                    "max_tosu":   row["max_tosu"],
                })
            weekend_status = {
                "sat": sat.isoformat(),
                "sun": sun.isoformat(),
                "days": weekend_rows,
                "total_races":   sum(d["race_count"]  for d in weekend_rows),
                "total_entries": sum(d["entry_count"] for d in weekend_rows),
            }

        conn_v2.close()
    except Exception as e:
        logger.error("V2 DB 接続失敗: %s", e)

    return {
        "jvdl_tables":    jvdl_tables,
        "v2_tables":      v2_tables,
        "watermarks":     watermarks,
        "sync_jobs":      sync_jobs,
        "weekend_status": weekend_status,
        "worker_running": _is_worker_running(),
    }


# ── エンドポイント: POST /api/v2/db-sync ─────────────────────────────────────

class SyncRequest(BaseModel):
    job_type: str


@router.post("/db-sync", status_code=201)
def post_db_sync(req: SyncRequest):
    """ジョブをキューに投入する（このAPIプロセス内では実行しない）。

    実行するには worker.bat でワーカーを起動すること。
    ワーカーが既に起動中であれば数秒以内に自動的に処理される。
    """
    if req.job_type not in _ALLOWED_JOB_TYPES:
        raise HTTPException(400, f"不正な job_type: {req.job_type!r}")

    try:
        conn = _jvdl_conn()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "INSERT INTO jobs (job_type, params) VALUES (%s, %s) RETURNING id, job_type, status, created_at",
                (req.job_type, psycopg2.extras.Json({})),
            )
            row = dict(cur.fetchone())
        conn.commit()
        conn.close()
    except Exception as e:
        raise HTTPException(503, f"ジョブ投入失敗: {e}")

    logger.info("db-sync ジョブ投入: id=%d type=%s", row["id"], row["job_type"])
    worker_running = _is_worker_running()
    return {
        "job_id":    row["id"],
        "job_type":  row["job_type"],
        "status":    row["status"],
        "created_at": row["created_at"].isoformat(),
        "worker_running": worker_running,
        "message": (
            None if worker_running
            else "ワーカーが起動していません。worker.bat を実行するとジョブが処理されます。"
        ),
    }


# ── エンドポイント: GET /api/v2/db-sync/{job_id} ──────────────────────────────

@router.get("/db-sync/{job_id}")
def get_db_sync_status(job_id: int):
    """指定ジョブの状態・進捗・ログを返す。"""
    try:
        conn = _jvdl_conn()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, job_type, status, progress, log_tail, created_at, started_at, finished_at FROM jobs WHERE id = %s",
                (job_id,),
            )
            row = cur.fetchone()
        conn.close()
    except Exception as e:
        raise HTTPException(503, f"DB接続失敗: {e}")

    if not row:
        raise HTTPException(404, f"ジョブ id={job_id} が見つかりません")

    return {
        "id":          row["id"],
        "job_type":    row["job_type"],
        "status":      row["status"],
        "progress":    row["progress"],
        "log_tail":    row["log_tail"],
        "created_at":  row["created_at"].isoformat() if row["created_at"] else None,
        "started_at":  row["started_at"].isoformat() if row["started_at"] else None,
        "finished_at": row["finished_at"].isoformat() if row["finished_at"] else None,
    }
