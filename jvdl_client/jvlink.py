"""
jvdl_client/jvlink.py
======================
JV-Link COM コンポーネントへの 64-bit → 32-bit ブリッジ。

JV-Link (JVDTLab.JVLink) は 32-bit COM コンポーネントのため、
64-bit Python から直接 CreateObject すると REGDB_E_CLASSNOTREG が発生する。

代わりに py -3.13-32 サブプロセス (jvdl_client._downloader_32bit) を起動し、
COM 呼び出しを委譲する。データ交換はファイル経由で行う。

動作要件:
  - Windows OS + JV-Link インストール済み
  - C:\\Users\\kaise\\AppData\\Local\\Programs\\Python\\Launcher\\py.exe (Python Launcher)
  - py -3.13-32 (32-bit Python 3.13) + comtypes インストール済み
"""
from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).parent.parent
_PY_LAUNCHER = Path(r"C:\Users\kaise\AppData\Local\Programs\Python\Launcher\py.exe")
_PY_32BIT = "-3.13-32"

# JVOpen option 引数 (loader.py に準拠)
OPT_STORED      = 1  # 蓄積系: from_time 以降の全データ
OPT_STORED_DIFF = 1  # alias — loader.py の慣例に合わせて 1 を使用
OPT_SETUP       = 4  # セットアップ: 全量再取得


class ComImportError(RuntimeError):
    """32-bit Python launcher が見つからず JV-Link にアクセスできない場合に送出。"""


class JVLinkClient:
    """JV-Link COM への 64-bit → 32-bit ブリッジ。

    使い方:
        with JVLinkClient() as jv:
            for record in jv.fetch_stored("RACE", "20260601000000"):
                process(record)
    """

    def __init__(self) -> None:
        if not _PY_LAUNCHER.exists():
            raise ComImportError(
                f"32-bit Python launcher が見つかりません: {_PY_LAUNCHER}\n"
                "Python Launcher (py.exe) をインストールし、"
                f"py {_PY_32BIT} + comtypes を用意してください。"
            )
        logger.info("[JVLink] 32-bit bridge 初期化: launcher=%s", _PY_LAUNCHER)

    # ── メイン API ────────────────────────────────────────────────────────────

    def fetch_stored(
        self,
        dataspec: str,
        from_time: str,
        option: int = OPT_STORED_DIFF,
        _tmp_dir: str | None = None,
    ) -> Iterator[bytes]:
        """py -3.13-32 サブプロセス経由で JVGets を呼び出し、生レコードを yield する。

        Args:
            dataspec:  "RACE" / "DIFF" / "SLOP" / "WOOD" 等
            from_time: "YYYYMMDDHHmmss" — この時点以降のデータを取得
            option:    OPT_STORED(1) が差分取得のデフォルト、OPT_SETUP(4) が全量
            _tmp_dir:  テスト用一時ディレクトリ（省略時は data/input/）
        """
        tmp_dir = Path(_tmp_dir) if _tmp_dir else _ROOT / "data" / "input"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_file = tmp_dir / f"_tmp_raw_{dataspec}_{os.getpid()}.txt"

        try:
            cmd = [
                str(_PY_LAUNCHER), _PY_32BIT,
                "-m", "jvdl_client._downloader_32bit",
                dataspec, from_time, str(option), str(tmp_file),
            ]
            env = {**os.environ, "PYTHONPATH": str(_ROOT)}
            logger.info("[JVLink] 32-bit downloader 起動: %s %s option=%d", dataspec, from_time, option)

            result = subprocess.run(cmd, env=env, timeout=3600)
            if result.returncode != 0:
                raise RuntimeError(
                    f"32-bit downloader 失敗 (returncode={result.returncode}): dataspec={dataspec}"
                )

            if not tmp_file.exists():
                logger.warning("[JVLink] 出力ファイルが見つかりません: %s", tmp_file)
                return

            count = 0
            with open(tmp_file, "rb") as f:
                for line in f:
                    stripped = line.rstrip(b"\n")
                    if stripped:
                        yield stripped
                        count += 1

            logger.info("[JVLink] %s: %d レコード読み込み完了", dataspec, count)

        finally:
            if tmp_file.exists():
                try:
                    tmp_file.unlink()
                except Exception:
                    pass

    # ── ウォーターマーク管理 ─────────────────────────────────────────────────

    @staticmethod
    def get_watermark(conn, dataspec: str, default: str = "20220101000000") -> str:
        """sync_watermark テーブルから前回同期時刻を取得する。"""
        with conn.cursor() as cur:
            cur.execute(
                "SELECT last_synced_at FROM sync_watermark WHERE dataspec = %s",
                (dataspec,),
            )
            row = cur.fetchone()
        return row[0] if row else default

    @staticmethod
    def set_watermark(conn, dataspec: str, synced_at: str) -> None:
        """同期完了時刻を sync_watermark に記録する。"""
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO sync_watermark (dataspec, last_synced_at, updated_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (dataspec) DO UPDATE
                SET last_synced_at = EXCLUDED.last_synced_at,
                    updated_at     = NOW()
                """,
                (dataspec, synced_at),
            )
        conn.commit()

    # ── コンテキストマネージャ ────────────────────────────────────────────────

    def __enter__(self) -> "JVLinkClient":
        return self

    def __exit__(self, *_) -> None:
        logger.info("[JVLink] ブリッジ終了")
