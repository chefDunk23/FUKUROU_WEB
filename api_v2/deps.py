"""
api_v2/deps.py
==============
FastAPI 依存注入: 認証・レートリミット・共通ガード。

API キー認証:
  リクエストヘッダー X-Api-Key の値を shared.config.API_KEY と照合する。
  API_KEY が空文字（未設定）の場合は開発モードとして認証をスキップする。
  本番デプロイ時は必ず .env に API_KEY=<random_hex> を設定すること。

レートリミット:
  スライディングウィンドウ方式（外部依存なし）。
  /predict/{race_id} 等の重い推論エンドポイントに適用する。
  デフォルト: IP あたり 1分間に 10 リクエストまで。
"""
from __future__ import annotations

import hmac
import logging
import threading
import time
from collections import deque

from fastapi import HTTPException, Request, Security
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


# ── レートリミット（スライディングウィンドウ） ─────────────────────────────────

class _RateLimiter:
    """IP ベースのスライディングウィンドウ方式レートリミッター（外部依存なし）。"""

    def __init__(self, max_requests: int, window_seconds: int) -> None:
        self._max = max_requests
        self._window = window_seconds
        self._lock = threading.Lock()
        self._store: dict[str, deque[float]] = {}

    def is_allowed(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self._window
        with self._lock:
            dq = self._store.setdefault(key, deque())
            while dq and dq[0] < cutoff:
                dq.popleft()
            if len(dq) >= self._max:
                return False
            dq.append(now)
            return True


# 推論エンドポイント用: IP あたり 1分間に 10 リクエスト
_predict_limiter = _RateLimiter(max_requests=10, window_seconds=60)


def rate_limit_predict(request: Request) -> None:
    """重い ML 推論エンドポイントに付与するレートリミット依存。"""
    client_ip = request.client.host if request.client else "unknown"
    if not _predict_limiter.is_allowed(client_ip):
        raise HTTPException(
            status_code=429,
            detail="リクエストが多すぎます。1分後に再試行してください。",
            headers={"Retry-After": "60"},
        )
