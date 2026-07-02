"""
tests/test_voicevox_client.py
================================
api_admin/services/voicevox_client.py の純粋関数・クライアントテスト。
requests.request を monkeypatch でモックし、実際のVOICEVOXサーバー非依存で検証する。
"""
from __future__ import annotations

import io
import wave

import pytest
import requests

from api_admin.services.voicevox_client import (
    VoicevoxClient,
    VoicevoxError,
    get_wav_duration_sec,
)


def _make_wav_bytes(duration_sec: float = 1.5, framerate: int = 24000) -> bytes:
    n_frames = int(framerate * duration_sec)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(framerate)
        wf.writeframes(b"\x00\x00" * n_frames)
    return buf.getvalue()


class _FakeResponse:
    def __init__(self, json_data=None, content=b"", status_code=200):
        self._json = json_data
        self.content = content
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status={self.status_code}")

    def json(self):
        return self._json


class TestGetWavDurationSec:
    def test_returns_correct_duration(self):
        wav_bytes = _make_wav_bytes(duration_sec=2.0)
        assert get_wav_duration_sec(wav_bytes) == pytest.approx(2.0, abs=1e-3)


class TestVoicevoxClientHealthCheck:
    def test_health_check_true_when_reachable(self, monkeypatch):
        client = VoicevoxClient()
        monkeypatch.setattr(requests, "request", lambda *a, **kw: _FakeResponse(json_data="0.25.1"))
        assert client.health_check() is True

    def test_health_check_false_when_unreachable(self, monkeypatch):
        client = VoicevoxClient()

        def _raise(*a, **kw):
            raise requests.ConnectionError("refused")

        monkeypatch.setattr(requests, "request", _raise)
        assert client.health_check() is False


class TestVoicevoxClientSynthesis:
    def test_audio_query_returns_json(self, monkeypatch):
        client = VoicevoxClient()
        monkeypatch.setattr(
            requests, "request",
            lambda *a, **kw: _FakeResponse(json_data={"accent_phrases": []}),
        )
        result = client.audio_query("こんにちは", speaker_id=3)
        assert result == {"accent_phrases": []}

    def test_synthesis_returns_wav_bytes(self, monkeypatch):
        client = VoicevoxClient()
        wav = _make_wav_bytes()
        monkeypatch.setattr(requests, "request", lambda *a, **kw: _FakeResponse(content=wav))
        result = client.synthesis({"accent_phrases": []}, speaker_id=3)
        assert result == wav

    def test_text_to_wav_calls_query_then_synthesis(self, monkeypatch):
        client = VoicevoxClient()
        calls = []

        def _fake_request(method, url, **kwargs):
            calls.append(url)
            if "/audio_query" in url:
                return _FakeResponse(json_data={"q": 1})
            return _FakeResponse(content=b"WAVDATA")

        monkeypatch.setattr(requests, "request", _fake_request)
        result = client.text_to_wav("テスト", speaker_id=13)
        assert result == b"WAVDATA"
        assert any("/audio_query" in c for c in calls)
        assert any("/synthesis" in c for c in calls)

    def test_retries_then_raises_voicevox_error(self, monkeypatch):
        client = VoicevoxClient()
        attempts = []

        def _always_fail(*a, **kw):
            attempts.append(1)
            raise requests.ConnectionError("down")

        monkeypatch.setattr(requests, "request", _always_fail)
        monkeypatch.setattr("time.sleep", lambda *_: None)  # リトライ待機をスキップ

        with pytest.raises(VoicevoxError):
            client.audio_query("x", speaker_id=3)
        assert len(attempts) == 3  # _MAX_RETRIES

    def test_succeeds_after_transient_failure(self, monkeypatch):
        client = VoicevoxClient()
        state = {"n": 0}

        def _fail_once_then_succeed(*a, **kw):
            state["n"] += 1
            if state["n"] == 1:
                raise requests.ConnectionError("transient")
            return _FakeResponse(json_data={"ok": True})

        monkeypatch.setattr(requests, "request", _fail_once_then_succeed)
        monkeypatch.setattr("time.sleep", lambda *_: None)

        result = client.audio_query("x", speaker_id=3)
        assert result == {"ok": True}
