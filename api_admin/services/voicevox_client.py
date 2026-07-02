"""
api_admin/services/voicevox_client.py
========================================
VOICEVOX HTTP API クライアント。

archive/long_video_project/python/scripts/generate_tts_assets.py の
VoicevoxClient を本プロジェクト向けに移植（audio_query→synthesis の
2段階呼び出し・リトライ・WAV長取得は同一設計）。
"""
from __future__ import annotations

import io
import logging
import time
import wave
from typing import Any

import requests

log = logging.getLogger(__name__)

DEFAULT_VOICEVOX_URL = "http://localhost:50021"
_MAX_RETRIES = 3
_RETRY_BACKOFF_BASE = 1.0  # 秒
_REQUEST_TIMEOUT = 30  # 秒


class VoicevoxError(RuntimeError):
    """VOICEVOX API呼び出し失敗（リトライを使い果たした場合）。"""


class VoicevoxClient:
    """VOICEVOX HTTP API ラッパー（リトライ付き）。"""

    def __init__(self, base_url: str = DEFAULT_VOICEVOX_URL) -> None:
        self.base_url = base_url.rstrip("/")

    def _request(self, method: str, path: str, max_retries: int = _MAX_RETRIES, **kwargs: Any) -> requests.Response:
        url = f"{self.base_url}{path}"
        last_error: Exception | None = None
        for attempt in range(max_retries):
            try:
                resp = requests.request(method, url, timeout=_REQUEST_TIMEOUT, **kwargs)
                resp.raise_for_status()
                return resp
            except requests.RequestException as e:
                last_error = e
                if attempt == max_retries - 1:
                    break
                wait = _RETRY_BACKOFF_BASE * (2 ** attempt)
                log.warning("VOICEVOX リトライ %d/%d: %s (%.1fs 待機)", attempt + 1, max_retries, e, wait)
                time.sleep(wait)
        raise VoicevoxError(f"VOICEVOX API呼び出し失敗 ({method} {path}): {last_error}") from last_error

    def audio_query(self, text: str, speaker_id: int) -> dict:
        """テキストから合成クエリを生成する。"""
        resp = self._request("POST", "/audio_query", params={"text": text, "speaker": speaker_id})
        return resp.json()

    def synthesis(self, query: dict, speaker_id: int) -> bytes:
        """合成クエリから WAV バイト列を生成する。"""
        resp = self._request("POST", "/synthesis", params={"speaker": speaker_id}, json=query)
        return resp.content

    def text_to_wav(self, text: str, speaker_id: int) -> bytes:
        """テキスト → WAV バイト列（audio_query + synthesis を一括実行）。"""
        query = self.audio_query(text, speaker_id)
        return self.synthesis(query, speaker_id)

    def health_check(self) -> bool:
        """サーバーが起動しているか確認する。"""
        try:
            self._request("GET", "/version", max_retries=1)
            return True
        except Exception:
            return False


def get_wav_duration_sec(wav_bytes: bytes) -> float:
    """WAV バイト列から正確な再生時間（秒）を返す。"""
    with wave.open(io.BytesIO(wav_bytes)) as wf:
        frames = wf.getnframes()
        framerate = wf.getframerate()
        return frames / framerate
