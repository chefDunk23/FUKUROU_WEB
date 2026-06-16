"""api_admin/deps.py — 管理 API の認証依存関係。"""
from __future__ import annotations

import hmac
import os

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from shared.config import DEV_MODE

_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# 管理 API 専用キー。未設定なら共通 API_KEY にフォールバック。
_ADMIN_API_KEY: str = os.environ.get("ADMIN_API_KEY") or os.environ.get("API_KEY", "")


async def verify_admin_key(key: str | None = Security(_header)) -> None:
    if DEV_MODE:
        return
    if not _ADMIN_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ADMIN_API_KEY が設定されていません",
        )
    if key is None or not hmac.compare_digest(key, _ADMIN_API_KEY):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="無効な X-API-Key",
        )
