"""
api_admin/services/video_db.py
================================
video_projects / video_templates / video_audio_assets（fukurou_jvdl）へのDBアクセス。
api_admin/routers/jobs.py と同じく raw psycopg2.connect(**DB_JVDL) を使う（プール未使用、リクエスト単位接続）。
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any

import psycopg2
import psycopg2.extras

from shared.config import DB_JVDL, DB_V2


def _conn():
    return psycopg2.connect(**DB_JVDL)


def list_available_dates() -> list[str]:
    """枠順確定済み・未出走（data_kubun='2'）のレースが存在する日付一覧を返す（fukurou_keiba_v2.races）。

    data_kubun は同一レースの状態が 1(出走馬名表)→2(出馬表/枠順確定)→3〜7(速報〜確定成績) と
    週を通じて進行する。過去に開催済みの日付は既に3〜7へ進んでいるため、'2'だけを見れば
    「まだ走っていないが枠順は確定済み」＝動画化対象日を自然に絞り込める。
    """
    conn = psycopg2.connect(**DB_V2)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT race_date
                FROM races
                WHERE data_kubun = '2'
                ORDER BY race_date
                """
            )
            return [row[0].isoformat() for row in cur.fetchall()]
    finally:
        conn.close()


def create_project(
    target_date: date,
    props_json: dict[str, Any],
    audio_rows: list[dict[str, Any]],
    template_id: int | None,
    overrides_json: dict[str, Any],
) -> dict[str, Any]:
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO video_projects (target_date, props_json, overrides_json, template_id, status)
                VALUES (%s, %s, %s, %s, 'draft')
                RETURNING *
                """,
                (target_date, json.dumps(props_json), json.dumps(overrides_json), template_id),
            )
            project = dict(cur.fetchone())

            for row in audio_rows:
                cur.execute(
                    """
                    INSERT INTO video_audio_assets (project_id, scene_index, script_text, speaker)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (project["id"], row["scene_index"], row["script_text"], row["speaker"]),
                )
        conn.commit()
        return project
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_project(project_id: int) -> dict[str, Any] | None:
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM video_projects WHERE id = %s", (project_id,))
            row = cur.fetchone()
            if not row:
                return None
            project = dict(row)

            cur.execute(
                "SELECT * FROM video_audio_assets WHERE project_id = %s ORDER BY scene_index",
                (project_id,),
            )
            project["audio_assets"] = [dict(r) for r in cur.fetchall()]
            return project
    finally:
        conn.close()


def get_template(template_id: int) -> dict[str, Any] | None:
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM video_templates WHERE id = %s", (template_id,))
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def update_project_props(project_id: int, props_json: dict[str, Any]) -> dict[str, Any] | None:
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                UPDATE video_projects
                SET props_json = %s, updated_at = now()
                WHERE id = %s
                RETURNING *
                """,
                (json.dumps(props_json), project_id),
            )
            row = cur.fetchone()
        conn.commit()
        return dict(row) if row else None
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_audio_assets(project_id: int) -> list[dict[str, Any]]:
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM video_audio_assets WHERE project_id = %s ORDER BY scene_index",
                (project_id,),
            )
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def update_audio_asset(asset_id: int, wav_path: str, duration_sec: float) -> None:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE video_audio_assets
                SET wav_path = %s, duration_sec = %s
                WHERE id = %s
                """,
                (wav_path, duration_sec, asset_id),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_project_status(project_id: int, status: str) -> None:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE video_projects SET status = %s, updated_at = now() WHERE id = %s",
                (status, project_id),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
