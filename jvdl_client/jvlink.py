"""
jvdl_client/jvlink.py
======================
JVDTLab.JVLink COM コンポーネントの最小ラッパー。

動作要件:
  - Windows OS (JV-Link は Windows 専用 COM コンポーネント)
  - comtypes: pip install comtypes
  - 環境変数 JVLINK_SID にソフトウェア ID を設定

Linux では import 時点で ComImportError が発生するが、それは想定通り。
テスト環境では tests/test_jvdl_client.py がモックを使用する。
"""
from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterator

logger = logging.getLogger(__name__)

# ── SDK 定数 ──────────────────────────────────────────────────────────────────

_BUF_SIZE = 110_000   # SDK 推奨バッファサイズ (bytes)

# JVOpen option 引数
OPT_STORED      = 1  # 蓄積系 (全データ)
OPT_STORED_DIFF = 2  # 蓄積系 (差分取得)
OPT_REALTIME    = 3  # 速報系 (ダイアログなし)
OPT_SETUP       = 4  # セットアップ (全量再取得)

# JVOpen / JVRead リターンコード
_RC_OK           =  0   # データあり / 正常終了
_RC_FILE_SWITCH  = -1   # ファイル切替 (続行)
_RC_DOWNLOADING  = -3   # ダウンロード中 (retry)
_RC_EOF          =  1   # データなし (正常終了)

_DOWNLOAD_WAIT_SEC = 2.0
_DOWNLOAD_MAX_RETRY = 300   # 最大 10 分待機

# ── COM インポート（Windows 専用） ────────────────────────────────────────────

try:
    import comtypes.client as _cc
    _COM_AVAILABLE = True
except ImportError:
    _COM_AVAILABLE = False
    # Linux / CI 環境。ComImportError は呼び出し側でハンドル。

class ComImportError(RuntimeError):
    """comtypes が利用できない環境で JVLinkClient を使おうとした場合に送出。"""


# ── JVLinkClient ──────────────────────────────────────────────────────────────

class JVLinkClient:
    """JVDTLab.JVLink COM コンポーネントのラッパー。

    使い方:
        with JVLinkClient() as jv:
            for record in jv.fetch_stored("RACE", "20260601000000"):
                process(record)
    """

    def __init__(self) -> None:
        if not _COM_AVAILABLE:
            raise ComImportError(
                "comtypes が利用できません。"
                "JV-Link は Windows 環境専用です。pip install comtypes を実行するか、"
                "Windows 環境で実行してください。"
            )
        sid = os.environ.get("JVLINK_SID", "")
        if not sid:
            raise ValueError(
                "環境変数 JVLINK_SID が設定されていません。"
                "JRA-VAN Data Lab. から取得したソフトウェア ID を設定してください。"
            )

        self._jv = _cc.CreateObject("JVDTLab.JVLink")
        rc = self._jv.JVInit(sid)
        if rc != 0:
            raise RuntimeError(f"JVInit 失敗: return_code={rc}")
        logger.info("[JVLink] JVInit 完了")

    # ── メイン API ────────────────────────────────────────────────────────────

    def fetch_stored(
        self,
        dataspec: str,
        from_time: str,
        option: int = OPT_STORED_DIFF,
    ) -> Iterator[bytes]:
        """JVOpen → JVRead ループで生レコード(bytes)を yield する。

        Args:
            dataspec:  "RACE" / "DIFF" / "SLOP" / "WOOD" 等
            from_time: "YYYYMMDDHHmmss" — この時点以降の差分を取得
            option:    OPT_STORED_DIFF(2) が差分取得のデフォルト
        """
        last_file = ""
        total_count, rc = 0, 0
        rc = self._jv.JVOpen(dataspec, from_time, option, 0, 0, "")
        if rc < 0:
            raise RuntimeError(f"JVOpen 失敗: dataspec={dataspec} rc={rc}")

        logger.info("[JVLink] JVOpen: dataspec=%s from_time=%s option=%d", dataspec, from_time, option)

        retry = 0
        try:
            while True:
                buf, filename, rc = self._jv.JVRead("", _BUF_SIZE, "")

                if rc == _RC_FILE_SWITCH:
                    if filename != last_file:
                        logger.debug("[JVLink] ファイル切替: %s", filename)
                        last_file = filename
                    continue

                if rc == _RC_DOWNLOADING:
                    retry += 1
                    if retry > _DOWNLOAD_MAX_RETRY:
                        raise RuntimeError("JVRead: ダウンロードタイムアウト")
                    if retry % 10 == 1:
                        logger.info("[JVLink] ダウンロード中 (retry=%d)...", retry)
                    time.sleep(_DOWNLOAD_WAIT_SEC)
                    continue

                retry = 0

                if rc == _RC_EOF or rc > 0:
                    logger.info("[JVLink] JVRead 終了: dataspec=%s records=%d", dataspec, total_count)
                    break

                if rc == _RC_OK and buf:
                    data = buf if isinstance(buf, bytes) else buf.encode("cp932", errors="replace")
                    yield data
                    total_count += 1
                    continue

                if rc < 0:
                    raise RuntimeError(f"JVRead エラー: rc={rc}")

        finally:
            self._jv.JVClose()
            logger.info("[JVLink] JVClose: dataspec=%s", dataspec)

    def fetch_realtime(self, dataspec: str) -> Iterator[bytes]:
        """JVRTOpen → JVRead ループ。速報系(0B11 / 0B14 / 0B31 等)用。"""
        rc = self._jv.JVRTOpen(dataspec, "")
        if rc < 0:
            raise RuntimeError(f"JVRTOpen 失敗: dataspec={dataspec} rc={rc}")

        logger.info("[JVLink] JVRTOpen: dataspec=%s", dataspec)
        try:
            while True:
                buf, filename, rc = self._jv.JVRead("", _BUF_SIZE, "")

                if rc == _RC_FILE_SWITCH:
                    continue
                if rc == _RC_DOWNLOADING:
                    time.sleep(_DOWNLOAD_WAIT_SEC)
                    continue
                if rc == _RC_EOF or rc > 0:
                    break
                if rc == _RC_OK and buf:
                    data = buf if isinstance(buf, bytes) else buf.encode("cp932", errors="replace")
                    yield data
                    continue
                if rc < 0:
                    raise RuntimeError(f"JVRead (RT) エラー: rc={rc}")
        finally:
            self._jv.JVClose()

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
        try:
            self._jv.JVClose()
        except Exception:
            pass
        try:
            import comtypes
            comtypes.CoUninitialize()
        except Exception:
            pass
        logger.info("[JVLink] COM 解放完了")
