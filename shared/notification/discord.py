"""
shared/notification/discord.py
================================
Discord Webhook 通知の薄いラッパー。

環境変数:
    DISCORD_WEBHOOK_URL — 未設定時は fail-open（何もせず False を返す）

依存: requests のみ（discord.py 不要）
"""
from __future__ import annotations

import logging
import os
from typing import Any

import requests

logger = logging.getLogger(__name__)

DISCORD_WEBHOOK_URL: str = os.getenv("DISCORD_WEBHOOK_URL", "")

_TIMEOUT = 10  # 秒


def send_message(text: str, *, color: int = 0x00FF00) -> bool:
    """Discord Webhook にテキストを送信する。URL 未設定時は False を返す（fail-open）。"""
    url = os.getenv("DISCORD_WEBHOOK_URL", DISCORD_WEBHOOK_URL)
    if not url:
        logger.warning("[Discord] DISCORD_WEBHOOK_URL 未設定 — 通知スキップ")
        return False
    try:
        resp = requests.post(url, json={"content": text}, timeout=_TIMEOUT)
        if resp.status_code in (200, 204):
            return True
        logger.error("[Discord] 送信失敗: status=%d body=%s", resp.status_code, resp.text[:200])
        return False
    except Exception as e:
        logger.error("[Discord] 通信エラー: %s", e)
        return False


def send_embed(
    title: str,
    description: str,
    *,
    color: int = 0x00FF00,
    fields: list[dict[str, Any]] | None = None,
) -> bool:
    """Embed 形式で送信する。URL 未設定時は False を返す（fail-open）。"""
    url = os.getenv("DISCORD_WEBHOOK_URL", DISCORD_WEBHOOK_URL)
    if not url:
        logger.warning("[Discord] DISCORD_WEBHOOK_URL 未設定 — 通知スキップ")
        return False
    embed: dict[str, Any] = {"title": title, "description": description, "color": color}
    if fields:
        embed["fields"] = fields
    try:
        resp = requests.post(url, json={"embeds": [embed]}, timeout=_TIMEOUT)
        if resp.status_code in (200, 204):
            return True
        logger.error("[Discord] embed 送信失敗: status=%d body=%s", resp.status_code, resp.text[:200])
        return False
    except Exception as e:
        logger.error("[Discord] 通信エラー: %s", e)
        return False
