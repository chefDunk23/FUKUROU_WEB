"""
api_v2/main.py
==============
V2 投資用 API — FastAPI アプリケーション初期化。

起動:
    uvicorn api_v2.main:app --host 0.0.0.0 --port 8002 --reload

エンドポイント一覧:
    GET /api/v2/races?date=YYYY-MM-DD       — 指定日のレース一覧
    GET /api/v2/predict/{race_id}           — V2 スタックアンサンブル予測
    GET /api/v2/analysis/ev                 — 期待値分析（過去統計）
    GET /healthz                            — ヘルスチェック
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

# プロジェクトルートを sys.path に追加（src / shared を解決するため）
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api_v2.routers import analysis, prediction, races

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s [V2] %(message)s",
    datefmt="%H:%M:%S",
)

app = FastAPI(
    title="福郎 V2 投資用 API",
    description="V2 LightGBM スタックアンサンブルによる競馬予測 API",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS — フロントエンド（Vite dev :5173 / prod :3000）から叩けるように許可
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(races.router)
app.include_router(prediction.router)
app.include_router(analysis.router)


@app.get("/healthz", tags=["system"])
def health() -> dict:
    return {"status": "ok", "api": "v2"}
