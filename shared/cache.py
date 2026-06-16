"""
shared/cache.py
=================
Redis クライアントの共通アクセサ。fail-open + サーキットブレーカー設計。

api_v2/routers/races.py と api_v2/routers/public_races.py に重複していた
Redis 接続ロジックをここに一本化する。

設計:
  - Redis 未起動でもエンドポイントは正常動作する（fail-open）
  - 初回接続失敗で以降の接続試行を即座にスキップする（サーキットブレーカー）
  - Redis が復帰した場合はプロセス再起動でフラグがリセットされる
"""
from __future__ import annotations

import logging

from shared.config import REDIS_HOST, REDIS_PORT

logger = logging.getLogger(__name__)

try:
    import redis as _redis_mod
    _REDIS_AVAILABLE = True
except ImportError:
    _REDIS_AVAILABLE = False

# ── 共通キー名前空間 ──────────────────────────────────────────────────────────

CACHE_PFX             = "keiba:"
RACE_DETAIL_CACHE_PFX = f"{CACHE_PFX}race_detail:"

_redis_client: object | None = None
_REDIS_CIRCUIT_OPEN = False


def get_redis_client():
    """Redis クライアントを返す。

    サーキットブレーカー付き fail-open 設計:
    - 初回接続失敗で _REDIS_CIRCUIT_OPEN = True にセット
    - 以降の呼び出しはフラグ確認のみで即 None を返す（ブロッキングなし）
    - Redis が復帰した場合はプロセス再起動でフラグがリセットされる
    """
    global _redis_client, _REDIS_CIRCUIT_OPEN

    if not _REDIS_AVAILABLE or _REDIS_CIRCUIT_OPEN:
        return None

    if _redis_client is not None:
        return _redis_client

    try:
        _redis_client = _redis_mod.Redis(  # type: ignore[union-attr]
            host=REDIS_HOST, port=REDIS_PORT, decode_responses=True,
            socket_connect_timeout=0.3, socket_timeout=0.3,
        )
        _redis_client.ping()  # type: ignore[union-attr]
        logger.info("[Cache] Redis connected (%s:%s)", REDIS_HOST, REDIS_PORT)
    except Exception as e:
        logger.info("[Cache] Redis unavailable (%s) — circuit open, caching disabled", e)
        _redis_client       = None
        _REDIS_CIRCUIT_OPEN = True

    return _redis_client
