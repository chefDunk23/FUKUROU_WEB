"""
api_v1/services/voicevox_client.py
=====================================
VOICEVOX エンジン（localhost:50021）を使った音声生成クライアント。
speaker_id=2（四国めたん ノーマル）固定。
"""
from __future__ import annotations

import logging
import os
import re
import wave as wave_module
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

_VOICEVOX_BASE_URL = os.getenv("VOICEVOX_BASE_URL", "http://127.0.0.1:50021")
_SPEAKER_ID        = int(os.getenv("VOICEVOX_SPEAKER_ID", "2"))

_SPEED      = 1.00
_PITCH      = 0.00
_INTONATION = 1.38
_VOLUME     = 1.00
_PRE_PHONE  = 0.10
_POST_PHONE = 0.10


def check_connection() -> bool:
    """VOICEVOX エンジンへの疎通確認。接続できれば True。"""
    try:
        resp = requests.get(f"{_VOICEVOX_BASE_URL}/version", timeout=5)
        if resp.ok:
            logger.info("[VOICEVOX] 接続OK  ver=%s  speaker=%d", resp.text.strip('"'), _SPEAKER_ID)
            return True
        logger.error("[VOICEVOX] /version HTTP %d", resp.status_code)
        return False
    except requests.exceptions.ConnectionError:
        logger.error("[VOICEVOX] 接続失敗 — VOICEVOX が起動していません: %s", _VOICEVOX_BASE_URL)
        return False
    except requests.exceptions.Timeout:
        logger.error("[VOICEVOX] タイムアウト: %s", _VOICEVOX_BASE_URL)
        return False


def _preprocess(text: str) -> str:
    """TTS 読み上げ前の正規化（誤読防止）。"""
    text = re.sub(r"(\d+)R", r"\1レース", text)
    text = re.sub(r"ホ[ゥウ](?![ァ-ヶ])", "ホー", text)
    for src, dst in [("＋", "と"), ("+", "と"), ("＆", "と"), ("&", "と"),
                     ("／", "や"), ("/", "や")]:
        text = text.replace(src, dst)
    return text


def _read_wav_duration(wav_path: Path) -> float:
    try:
        with wave_module.open(str(wav_path), "rb") as wf:
            frames = wf.getnframes()
            rate   = wf.getframerate()
            return frames / rate if rate > 0 else 0.0
    except Exception as exc:
        logger.warning("[VOICEVOX] WAV 秒数読み取り失敗 %s: %s", wav_path, exc)
        return 0.0


def generate_audio(
    text:      str,
    wav_path:  Path,
) -> tuple[float, bool]:
    """
    text を合成して wav_path に保存する。
    Returns (duration_seconds, success)
    """
    processed = _preprocess(text)

    try:
        q_resp = requests.post(
            f"{_VOICEVOX_BASE_URL}/audio_query",
            params={"text": processed, "speaker": _SPEAKER_ID},
            timeout=30,
        )
    except requests.exceptions.RequestException as exc:
        logger.error("[VOICEVOX] audio_query 失敗: %s", exc)
        return 0.0, False

    if not q_resp.ok:
        logger.error("[VOICEVOX] audio_query HTTP %d: %s", q_resp.status_code, q_resp.text[:200])
        return 0.0, False

    q_data = q_resp.json()
    q_data.update({
        "speedScale":       _SPEED,
        "pitchScale":       _PITCH,
        "intonationScale":  _INTONATION,
        "volumeScale":      _VOLUME,
        "prePhonemeLength": _PRE_PHONE,
        "postPhonemeLength":_POST_PHONE,
    })

    try:
        s_resp = requests.post(
            f"{_VOICEVOX_BASE_URL}/synthesis",
            params={"speaker": _SPEAKER_ID},
            json=q_data,
            timeout=60,
        )
    except requests.exceptions.RequestException as exc:
        logger.error("[VOICEVOX] synthesis 失敗: %s", exc)
        return 0.0, False

    if not s_resp.ok or len(s_resp.content) < 100:
        logger.error("[VOICEVOX] synthesis 失敗 HTTP %d  bytes=%d", s_resp.status_code, len(s_resp.content))
        return 0.0, False

    try:
        wav_path.write_bytes(s_resp.content)
    except OSError as exc:
        logger.error("[VOICEVOX] WAV 書き込み失敗 %s: %s", wav_path, exc)
        return 0.0, False

    duration = _read_wav_duration(wav_path)
    logger.info("[VOICEVOX] 完了: %s  %.2fs", wav_path.name, duration)
    return duration, True
