"""
api_admin/routers/video.py
============================
動画生成管理API。api_admin配下の他ルーター（jobs.py, health.py）と同じく、
このサーバー自体が「管理API」なのでパスに /api/admin プレフィックスは付けない
（frontend/src/api/admin.ts の adminFetch も同じ規約でベースURLに直接パスを結合する）。

GET  /video/available-dates      — 枠順確定済み開催日一覧
POST /video/projects             — 前処理実行（props_json + 台本生成）
GET  /video/projects/{id}        — プロジェクト詳細（props/overrides/audio状態）
PATCH /video/projects/{id}       — props_json の部分修正
POST /video/projects/{id}/synthesize-audio — VOICEVOX音声合成（draft→audio_ready）
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api_admin.services import video_audio, video_db, video_preprocessing
from api_admin.services.video_audio import VideoAudioError
from api_admin.services.video_preprocessing import VideoPreprocessingError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/video", tags=["video"])


# ── モデル ────────────────────────────────────────────────────────────────────


class ProjectCreateRequest(BaseModel):
    target_date: date
    apply_template_id: int | None = None


class ProjectPatchRequest(BaseModel):
    props_json: dict[str, Any] = Field(..., description="部分修正後のprops_json全体を渡す")


class SynthesizeAudioRequest(BaseModel):
    force: bool = Field(False, description="Trueなら既に合成済みのシーンも再合成する")


def _row_to_response(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "target_date": row["target_date"].isoformat(),
        "props_json": row["props_json"],
        "overrides_json": row["overrides_json"],
        "template_id": row.get("template_id"),
        "status": row["status"],
        "output_path": row.get("output_path"),
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
        "audio_assets": [
            {
                "scene_index": a["scene_index"],
                "script_text": a["script_text"],
                "wav_path": a.get("wav_path"),
                "duration_sec": float(a["duration_sec"]) if a.get("duration_sec") is not None else None,
                "speaker": a["speaker"],
            }
            for a in row.get("audio_assets", [])
        ] if "audio_assets" in row else None,
    }


# ── エンドポイント ────────────────────────────────────────────────────────────

@router.get("/available-dates")
def get_available_dates() -> dict[str, list[str]]:
    return {"dates": video_db.list_available_dates()}


@router.post("/projects")
def create_project(req: ProjectCreateRequest) -> dict[str, Any]:
    try:
        reading_dict = video_preprocessing.load_reading_dict()
        races = video_preprocessing.select_target_races(req.target_date)
        props_json = video_preprocessing.build_props_json(races, reading_dict, req.target_date)
        audio_rows = video_preprocessing.generate_scripts(props_json, reading_dict)
    except VideoPreprocessingError as e:
        raise HTTPException(422, str(e))

    overrides_json: dict[str, Any] = {}
    if req.apply_template_id is not None:
        template = video_db.get_template(req.apply_template_id)
        if template is None:
            raise HTTPException(404, f"template_id={req.apply_template_id} が見つかりません")
        overrides_json = template["overrides_json"]

    project = video_db.create_project(
        target_date=req.target_date,
        props_json=props_json,
        audio_rows=audio_rows,
        template_id=req.apply_template_id,
        overrides_json=overrides_json,
    )
    return _row_to_response({**project, "audio_assets": audio_rows})


@router.get("/projects/{project_id}")
def get_project(project_id: int) -> dict[str, Any]:
    project = video_db.get_project(project_id)
    if project is None:
        raise HTTPException(404, f"project_id={project_id} が見つかりません")
    return _row_to_response(project)


@router.patch("/projects/{project_id}")
def patch_project(project_id: int, req: ProjectPatchRequest) -> dict[str, Any]:
    project = video_db.update_project_props(project_id, req.props_json)
    if project is None:
        raise HTTPException(404, f"project_id={project_id} が見つかりません")
    return _row_to_response(project)


@router.post("/projects/{project_id}/synthesize-audio")
def synthesize_audio(project_id: int, req: SynthesizeAudioRequest = SynthesizeAudioRequest()) -> dict[str, Any]:
    if video_db.get_project(project_id) is None:
        raise HTTPException(404, f"project_id={project_id} が見つかりません")
    try:
        result = video_audio.synthesize_project_audio(project_id, force=req.force)
    except VideoAudioError as e:
        raise HTTPException(422, str(e))
    return result
