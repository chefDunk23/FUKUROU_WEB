"""
api_v1/routers/classic_video.py
=================================
ClassicVideo パイプライン API エンドポイント。

  POST /api/v1/classic/prompt            - 踏み台 JSON 生成 → ファイルダウンロード
  POST /api/v1/classic/render            - LLM記入済み JSON アップロード → ジョブ開始
  GET  /api/v1/classic/jobs/{job_id}     - ジョブ進捗ポーリング
  GET  /api/v1/classic/jobs/{job_id}/mp4 - 完成 MP4 ダウンロード
  GET  /api/v1/classic/voicevox/status   - VoiceVox ヘルスチェック
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from api_v1.services.classic_video_service import (
    JobStatus,
    generate_prompt,
    new_job,
    read_job,
    run_render_job,
)

router = APIRouter(prefix="/api/v1/classic", tags=["classic-video"])


# ── Pydantic バリデーションモデル (Trap 1) ─────────────────────────────────────

class SpeechLineSchema(BaseModel):
    speaker: str
    text:    str
    reading: Optional[str] = None


class PickSchema(BaseModel):
    mark:                str
    umaban:              int
    horse_name:          str
    ai_score:            float
    emp_z:               str
    evaluation_keywords: list[str] = []
    evaluation_reason:   str = ""
    concern:             str = ""


class RaceSchema(BaseModel):
    race_id:           str
    race_label:        str
    race_name:         str = ""
    picks:             list[PickSchema]
    speech_lines:      list[SpeechLineSchema]
    speech_text:       str = ""
    telop:             str = ""
    audio_url:         str = ""
    audio_duration_ms: int = 0


class ClassicVideoInput(BaseModel):
    session: str
    date:    str
    venue:   str
    races:   list[RaceSchema] = Field(min_length=1)


# ── エンドポイント ─────────────────────────────────────────────────────────────

class PromptRequest(BaseModel):
    date:  str
    venue: Optional[str] = None


@router.post("/prompt", summary="踏み台 JSON を生成してダウンロード")
async def create_prompt(req: PromptRequest):
    """
    日付と会場コードを受け取り、LLM に渡す踏み台 JSON を生成して返す。
    レスポンスは JSON ファイルとしてダウンロードされる。
    """
    try:
        json_path = await generate_prompt(req.date, req.venue)
    except FileNotFoundError as e:
        raise HTTPException(404, detail=str(e))
    except Exception as e:
        raise HTTPException(500, detail=f"JSON 生成に失敗しました: {e}")

    return FileResponse(
        path=json_path,
        media_type="application/json",
        filename=json_path.name,
        headers={"Content-Disposition": f'attachment; filename="{json_path.name}"'},
    )


@router.post("/render", summary="LLM記入済み JSON でレンダリングジョブを開始")
async def start_render(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="LLM が記入した draft JSON ファイル"),
):
    """
    LLM が speech_lines / evaluation_reason 等を埋めた JSON をアップロードし、
    TTS 合成 + Remotion レンダーをバックグラウンドで開始する。
    ジョブ ID を返す。進捗は GET /jobs/{job_id} でポーリング可能。
    """
    raw = await file.read()

    # Trap 1a: LLM が markdown コードブロックで包んで返す場合を除去
    text = raw.decode("utf-8", errors="replace").strip()
    text = re.sub(r"^```(?:json)?\s*\n?", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n?```\s*$", "", text, flags=re.MULTILINE)
    text = text.strip()

    # Trap 1b: JSON パース
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise HTTPException(
            422,
            detail={
                "message": "JSON のパースに失敗しました。"
                           "マークダウンや余分なテキストが混入していないか確認してください。",
                "detail": str(e),
            },
        )

    # Trap 1c: Pydantic による構造バリデーション
    try:
        ClassicVideoInput(**data)
    except Exception as e:
        raise HTTPException(
            422,
            detail={
                "message": "JSON の構造が不正です。"
                           "speech_lines / picks のフィールド名を確認してください。",
                "detail": str(e),
            },
        )

    job_id = new_job()
    background_tasks.add_task(run_render_job, job_id, data)
    return {"job_id": job_id, "message": "ジョブを開始しました"}


@router.get("/jobs/{job_id}", summary="ジョブ進捗を取得")
def get_job_status(job_id: str):
    """
    ジョブの現在ステータスを返す。
    - status: pending / tts / render / done / error
    - tts_done / tts_total: TTS 進捗
    - remotion_pct: Remotion レンダー進捗 (0-100)
    - mp4_path: 完了時のみ設定
    - error: エラー時のみ設定
    """
    state = read_job(job_id)
    if not state:
        raise HTTPException(404, detail=f"Job '{job_id}' が見つかりません")
    return state


@router.get("/jobs/{job_id}/mp4", summary="完成 MP4 をダウンロード")
def download_mp4(job_id: str):
    """レンダリングが完了した MP4 ファイルを返す。"""
    state = read_job(job_id)
    if not state:
        raise HTTPException(404, detail=f"Job '{job_id}' が見つかりません")
    if state.get("status") != JobStatus.DONE:
        raise HTTPException(400, detail="レンダリングがまだ完了していません")

    mp4_path = Path(state["mp4_path"])
    if not mp4_path.exists():
        raise HTTPException(404, detail="MP4 ファイルが見つかりません")

    return FileResponse(
        path=mp4_path,
        media_type="video/mp4",
        filename=f"classic_video_{job_id}.mp4",
    )


@router.get("/voicevox/status", summary="VoiceVox ヘルスチェック")
async def voicevox_status():
    """VoiceVox が起動しているかチェックする。"""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get("http://localhost:50021/version")
            return {"running": resp.status_code == 200, "version": resp.text.strip('"')}
    except Exception:
        return {"running": False, "version": None}
