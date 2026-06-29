"""
api_v1/routers/video.py
========================
POST /api/v1/video/generate — YouTube ショート動画生成ジョブをキックする。

NOTE: 実際の動画レンダリング（Remotion / ffmpeg 等）は Phase 3 で接続する。
      このルーターは受け口（スキーマ定義・バリデーション）を先行実装する。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field

from shared.config import DEV_MODE

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["v1-video"])


class VideoGenerateRequest(BaseModel):
    race_id: str = Field(..., description="16文字 JV-Data race_id")
    video_type: str = Field("short", description="'short'=縦型ショート / 'review'=レビュー")
    voice_id: str = Field("zundamon", description="VOICEVOX 話者ID")
    include_shap: bool = Field(True, description="SHAP 根拠を台本に含めるか")


class VideoGenerateResponse(BaseModel):
    job_id: str
    status: str
    message: str


@router.post("/video/generate", response_model=VideoGenerateResponse)
def generate_video(req: VideoGenerateRequest) -> VideoGenerateResponse:
    """
    動画生成ジョブをキックする（開発者専用エンドポイント）。

    DEV_MODE=false の場合、403 を返す。
    """
    if not DEV_MODE:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=403,
            detail="動画生成は DEV_MODE=true の環境でのみ利用できます。",
        )

    logger.info("[V1Video] ジョブ受付: race_id=%s type=%s", req.race_id, req.video_type)

    # TODO: Phase 3 で実際の動画生成パイプラインを呼び出す
    # from api_v1.services.video_pipeline import kick_job
    # job_id = kick_job(req)

    return VideoGenerateResponse(
        job_id=f"job_{req.race_id}_{req.video_type}",
        status="queued",
        message=f"動画生成ジョブを受け付けました。race_id={req.race_id}",
    )
