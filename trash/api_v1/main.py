"""
api_v1/main.py
==============
V1 YouTube 動画生成 API — FastAPI アプリケーション初期化。

起動:
    uvicorn api_v1.main:app --host 0.0.0.0 --port 8001 --reload

エンドポイント一覧:
    POST /api/v1/video/generate          — 動画生成ジョブキック
    POST /api/v1/script/generate         — 台本テキスト生成
    GET  /api/v1/script/tts-preview      — TTS 読み上げプレビュー（漢字変換済み）
    GET  /healthz                        — ヘルスチェック

NOTE: このAPIは DEV_MODE=true のローカル環境専用。
      フロントエンドは /video-gen ルート経由でのみアクセスする。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api_v1.routers import classic_video, data, pipeline, script, video

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s [V1] %(message)s",
    datefmt="%H:%M:%S",
)

app = FastAPI(
    title="福郎 V1 動画生成 API",
    description="YouTube ショート動画台本・音声・動画を生成する開発者専用 API",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(video.router)
app.include_router(script.router)
app.include_router(pipeline.router)
app.include_router(classic_video.router)
app.include_router(data.router)


@app.get("/healthz", tags=["system"])
def health() -> dict:
    return {"status": "ok", "api": "v1"}
