"""
api_admin/main.py
==================
内部管理 API — FastAPI アプリ（PORT 8003、127.0.0.1 バインド固定）。

⚠️  絶対に外部公開しないこと。
    nginx / ファイアウォールで 8003 を LAN/WAN からブロックすること。
    docs/operations/deploy.md の「8003 ポートに関する注意」を参照。

起動:
    uvicorn api_admin.main:app --host 127.0.0.1 --port 8003 --reload

エンドポイント:
    POST /jobs              — ジョブ投入
    GET  /jobs              — ジョブ一覧
    GET  /jobs/{id}         — ジョブ詳細 + ログ
    POST /jobs/{id}/cancel  — キャンセル
    GET  /healthz           — 死活確認
    GET  /health/dashboard  — システムヘルスダッシュボード
"""
from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api_admin.deps import verify_admin_key
from api_admin.routers import health, jobs, video
from shared.config import API_KEY, DEV_MODE

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s [Admin] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    if not DEV_MODE and not API_KEY:
        raise RuntimeError(
            "API_KEY が設定されていません。"
            ".env に API_KEY=<random_hex> を設定してください。"
        )
    logger.info("startup: api_admin DEV_MODE=%s", DEV_MODE)
    yield
    logger.info("shutdown: api_admin")


app = FastAPI(
    title="フクロウ 管理 API（内部専用）",
    description="ジョブキュー管理。127.0.0.1 バインド専用。外部公開禁止。",
    version="1.0.0",
    docs_url="/docs" if DEV_MODE else None,
    redoc_url=None,
    lifespan=_lifespan,
)

# CORS: メインフロントエンド (5173) のみ許可。外部オリジンは絶対に追加しない
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

_auth = [Depends(verify_admin_key)]
app.include_router(jobs.router,    dependencies=_auth)
app.include_router(health.router,  dependencies=_auth)
app.include_router(video.router,   dependencies=_auth)


@app.get("/healthz", tags=["system"])
def health() -> dict:
    return {"status": "ok", "api": "admin"}
