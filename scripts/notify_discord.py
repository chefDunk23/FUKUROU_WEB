"""
scripts/notify_discord.py
==========================
Discord Webhook に任意メッセージを送信する CLI ユーティリティ。

Usage:
    py scripts/notify_discord.py "月曜バッチ失敗"
    py scripts/notify_discord.py --title "バッチ結果" "詳細テキスト"
    py scripts/notify_discord.py --color red "エラーが発生しました"

環境変数:
    DISCORD_WEBHOOK_URL — 未設定時はエラー終了
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv(_ROOT / ".env")

from shared.notification.discord import send_embed, send_message

_COLOR_MAP: dict[str, int] = {
    "green":  0x00FF00,
    "red":    0xFF0000,
    "yellow": 0xFFFF00,
    "blue":   0x0099FF,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Discord Webhook 通知 CLI")
    parser.add_argument("message", help="送信するテキスト")
    parser.add_argument("--title", default="", help="Embed タイトル（省略時はシンプルメッセージ）")
    parser.add_argument(
        "--color", default="green", choices=list(_COLOR_MAP.keys()),
        help="Embed 色 (green/red/yellow/blue)",
    )
    args = parser.parse_args()

    color = _COLOR_MAP[args.color]

    if args.title:
        ok = send_embed(title=args.title, description=args.message, color=color)
    else:
        ok = send_message(args.message, color=color)

    if not ok:
        print("ERROR: Discord 送信失敗（DISCORD_WEBHOOK_URL 未設定またはネットワークエラー）", file=sys.stderr)
        return 1

    print("送信完了")
    return 0


if __name__ == "__main__":
    sys.exit(main())
