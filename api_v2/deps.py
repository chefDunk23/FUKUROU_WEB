"""
api_v2/deps.py
==============
FastAPI 依存注入: 認証・共通ガード。

API キー認証:
  リクエストヘッダー X-Api-Key の値を shared.config.API_KEY と照合する。
  API_KEY が空文字（未設定）の場合は開発モードとして認証をスキップする。
  本番デプロイ時は必ず .env に API_KEY=<random_hex> を設定すること。

2026-07: V2アンサンブル引退に伴い /predict/{race_id} 用のレートリミット
（rate_limit_predict, _RateLimiter）を削除した。
"""
from __future__ import annotations

import hmac
import logging

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

from shared.config import API_KEY

logger = logging.getLogger(__name__)

# ── API キー認証 ──────────────────────────────────────────────────────────────

_api_key_scheme = APIKeyHeader(name="X-Api-Key", auto_error=False)


def verify_api_key(api_key: str | None = Security(_api_key_scheme)) -> str:
    """全 /api/v2 エンドポイントに付与する API キー検証依存。

    API_KEY 未設定（空文字）= 開発モード: 認証スキップ。
    API_KEY 設定済み: X-Api-Key ヘッダーが一致しない場合 401 を返す。
    """
    if not API_KEY:
        return "dev"
    if not api_key or not hmac.compare_digest(api_key, API_KEY):
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    return api_key
