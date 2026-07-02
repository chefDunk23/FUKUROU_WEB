"""
api_v2/main.py
==============
V2 投資用 API — FastAPI アプリケーション初期化。

起動:
    uvicorn api_v2.main:app --host 0.0.0.0 --port 8002 --reload

エンドポイント一覧:
    GET /api/v2/races?date=YYYY-MM-DD       — 指定日のレース一覧
    GET /healthz                            — ヘルスチェック

2026-07: V2アンサンブル引退に伴い prediction ルーター（/api/v2/predict/{race_id}）を削除。
"""
from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# プロジェクトルートを sys.path に追加（src / shared を解決するため）
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api_v2.deps import verify_api_key
from api_v2.routers import db_status, lab, public_races, race_level, races, tipster
from shared.config import API_KEY, DEV_MODE

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s [V2] %(message)s",
    datefmt="%H:%M:%S",
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    if not DEV_MODE and not API_KEY:
        raise RuntimeError(
            "API_KEY が設定されていません。"
            "本番環境では .env に API_KEY=<random_hex> を設定してください。"
            "（開発環境では DEV_MODE=true を設定すると認証をスキップできます）"
        )

    logger.info(
        "startup: DEV_MODE=%s, API_KEY=%s"
        " (スケジューラは shared/worker/job_runner.py に統一)",
        DEV_MODE, "set" if API_KEY else "empty",
    )

    yield

    logger.info("shutdown")


app = FastAPI(
    title="フクロウ 予測 API",
    description="フクロウ競馬予測システム V2 API",
    version="2.0.0",
    docs_url="/docs" if DEV_MODE else None,
    redoc_url="/redoc" if DEV_MODE else None,
    lifespan=_lifespan,
)

# CORS — フロントエンド (5173) のみ許可
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
    ],
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

_auth = [Depends(verify_api_key)]
app.include_router(races.router,         dependencies=_auth)
app.include_router(race_level.router,    dependencies=_auth)
app.include_router(tipster.router,       dependencies=_auth)
app.include_router(db_status.router,     dependencies=_auth)
app.include_router(lab.router,           dependencies=_auth)
# 公開エンドポイント: 認証不要（/api/v2/public/*）
app.include_router(public_races.router)
# admin 系は api_admin (port 8003) に移設済み — docs/operations/deploy.md 参照


@app.get("/healthz", tags=["system"])
def health() -> dict:
    return {"status": "ok", "api": "v2"}
