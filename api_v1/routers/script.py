"""
api_v1/routers/script.py
=========================
POST /api/v1/script/generate   — 台本テキスト生成
GET  /api/v1/script/tts-preview — TTS 読み上げプレビュー（漢字変換済み）
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from api_v1.services.tts_pipeline import prepare_for_tts, to_ssml
from shared.config import DEV_MODE

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["v1-script"])


class TtsPreviewResponse(BaseModel):
    original: str
    converted: str
    ssml: str


@router.get("/script/tts-preview", response_model=TtsPreviewResponse)
def tts_preview(
    text: str = Query(..., description="変換前のテキスト"),
) -> TtsPreviewResponse:
    """
    入力テキストを競馬特化辞書で変換した結果と SSML をプレビューする。
    動画生成前に読み上げ内容を確認するためのエンドポイント。
    """
    return TtsPreviewResponse(
        original=text,
        converted=prepare_for_tts(text),
        ssml=to_ssml(text),
    )


class ScriptGenerateRequest(BaseModel):
    race_id: str = Field(..., description="16文字 JV-Data race_id")
    script_type: str = Field("prediction", description="'prediction'=予想 / 'review'=レビュー")


class ScriptGenerateResponse(BaseModel):
    race_id: str
    script_type: str
    raw_text: str
    tts_text: str
    ssml: str


@router.post("/script/generate", response_model=ScriptGenerateResponse)
def generate_script(req: ScriptGenerateRequest) -> ScriptGenerateResponse:
    """
    レースIDから台本テキストを生成し、TTS 用に変換した結果を返す。
    """
    if not DEV_MODE:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=403,
            detail="台本生成は DEV_MODE=true の環境でのみ利用できます。",
        )

    logger.info("[V1Script] 台本生成: race_id=%s type=%s", req.race_id, req.script_type)

    # TODO: Phase 3 で実際のスクリプト生成ロジックを呼び出す
    raw_text = f"【{req.race_id}】の台本生成機能は Phase 3 で実装予定です。"

    return ScriptGenerateResponse(
        race_id=req.race_id,
        script_type=req.script_type,
        raw_text=raw_text,
        tts_text=prepare_for_tts(raw_text),
        ssml=to_ssml(raw_text),
    )
