"""
tests/test_video_audio.py
============================
api_admin/services/video_audio.py の純粋関数・オーケストレーションテスト。
DB (video_db) と VoicevoxClient を monkeypatch でモックし、実DB・実VOICEVOX非依存で検証する。

重点確認事項:
  - VOICEVOX未接続時のfail-fast（無音WAVへの静かなフォールバックをしない）
  - 既に wav_path がある行はスキップ（force=Falseの既定動作）
  - 全シーン合成完了時のみ video_projects.status を audio_ready に進める
  - 部分失敗時は status を draft のまま維持する
"""
from __future__ import annotations

import json

import pytest

from api_admin.services import video_audio as va


class _FakeClient:
    def __init__(self, healthy=True, fail_scenes: set[int] | None = None):
        self._healthy = healthy
        self._fail_scenes = fail_scenes or set()
        self.calls: list[tuple[str, int]] = []
        self.base_url = "http://fake-voicevox:50021"

    def health_check(self) -> bool:
        return self._healthy

    def text_to_wav(self, text: str, speaker_id: int) -> bytes:
        self.calls.append((text, speaker_id))
        # scene番号をテキストに仕込んでおき、失敗させたいシーンを判定する
        for idx in self._fail_scenes:
            if f"scene{idx}" in text:
                from api_admin.services.voicevox_client import VoicevoxError
                raise VoicevoxError("synthesis failed")
        return _make_wav_bytes()


def _make_wav_bytes(duration_sec: float = 1.0) -> bytes:
    import io
    import wave
    framerate = 24000
    n_frames = int(framerate * duration_sec)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(framerate)
        wf.writeframes(b"\x00\x00" * n_frames)
    return buf.getvalue()


@pytest.fixture()
def voicevox_config(tmp_path, monkeypatch):
    config_path = tmp_path / "voicevox.config.json"
    config_path.write_text(
        json.dumps({"speakers": {"hina": {"voicevox_speaker_id": 3}, "hakase": {"voicevox_speaker_id": 13}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(va, "_VOICEVOX_CONFIG_PATH", config_path)
    return config_path


@pytest.fixture()
def audio_output_dir(tmp_path, monkeypatch):
    out_dir = tmp_path / "public_audio"
    monkeypatch.setattr(va, "_AUDIO_OUTPUT_ROOT", out_dir)
    return out_dir


@pytest.fixture()
def fake_db(monkeypatch):
    """video_db.list_audio_assets / update_audio_asset / update_project_status をインメモリで差し替える。"""
    state = {"assets": [], "updates": [], "status": None}

    def _list_audio_assets(project_id):
        return state["assets"]

    def _update_audio_asset(asset_id, wav_path, duration_sec):
        state["updates"].append((asset_id, wav_path, duration_sec))

    def _update_project_status(project_id, status):
        state["status"] = status

    monkeypatch.setattr(va.video_db, "list_audio_assets", _list_audio_assets)
    monkeypatch.setattr(va.video_db, "update_audio_asset", _update_audio_asset)
    monkeypatch.setattr(va.video_db, "update_project_status", _update_project_status)
    return state


class TestLoadSpeakerIds:
    def test_loads_from_config(self, voicevox_config):
        result = va.load_speaker_ids()
        assert result == {"hina": 3, "hakase": 13}

    def test_missing_file_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(va, "_VOICEVOX_CONFIG_PATH", tmp_path / "does_not_exist.json")
        with pytest.raises(va.VideoAudioError, match="voicevox.config.json"):
            va.load_speaker_ids()


class TestSynthesizeProjectAudio:
    def test_fails_fast_when_voicevox_unreachable(self, voicevox_config, audio_output_dir, fake_db):
        """VOICEVOX未接続時は無音WAVへ静かにフォールバックせず、即座に例外を投げる。"""
        fake_db["assets"] = [
            {"id": 1, "scene_index": 0, "script_text": "scene0 text", "speaker": "hina", "wav_path": None},
        ]
        client = _FakeClient(healthy=False)

        with pytest.raises(va.VideoAudioError, match="VOICEVOX"):
            va.synthesize_project_audio(1, client=client)

        assert fake_db["updates"] == []
        assert fake_db["status"] is None

    def test_empty_assets_raises(self, voicevox_config, audio_output_dir, fake_db):
        fake_db["assets"] = []
        client = _FakeClient(healthy=True)
        with pytest.raises(va.VideoAudioError, match="空です"):
            va.synthesize_project_audio(1, client=client)

    def test_synthesizes_all_scenes_and_marks_audio_ready(self, voicevox_config, audio_output_dir, fake_db):
        fake_db["assets"] = [
            {"id": 1, "scene_index": 0, "script_text": "scene0 title", "speaker": "hina", "wav_path": None},
            {"id": 2, "scene_index": 1, "script_text": "scene1 pick", "speaker": "hakase", "wav_path": None},
        ]
        client = _FakeClient(healthy=True)

        result = va.synthesize_project_audio(1, client=client)

        assert result["synthesized"] == 2
        assert result["failed"] == 0
        assert result["status"] == "audio_ready"
        assert fake_db["status"] == "audio_ready"
        assert len(fake_db["updates"]) == 2
        # speaker_id が正しくクライアントに渡っていること
        assert (client.calls[0][1], client.calls[1][1]) == (3, 13)
        # WAVファイルが実際に書き出されていること
        assert (audio_output_dir / "1" / "000.wav").exists()
        assert (audio_output_dir / "1" / "001.wav").exists()

    def test_skips_scenes_with_existing_wav_path_by_default(self, voicevox_config, audio_output_dir, fake_db):
        fake_db["assets"] = [
            {"id": 1, "scene_index": 0, "script_text": "scene0", "speaker": "hina", "wav_path": "audio/1/000.wav"},
        ]
        client = _FakeClient(healthy=True)

        result = va.synthesize_project_audio(1, client=client)

        assert result["skipped"] == 1
        assert result["synthesized"] == 0
        assert client.calls == []
        assert result["status"] == "audio_ready"  # 既存分のみでも全件揃っていれば audio_ready

    def test_force_resynthesizes_existing_scenes(self, voicevox_config, audio_output_dir, fake_db):
        fake_db["assets"] = [
            {"id": 1, "scene_index": 0, "script_text": "scene0", "speaker": "hina", "wav_path": "audio/1/000.wav"},
        ]
        client = _FakeClient(healthy=True)

        result = va.synthesize_project_audio(1, client=client, force=True)

        assert result["synthesized"] == 1
        assert result["skipped"] == 0
        assert len(client.calls) == 1

    def test_partial_failure_keeps_status_draft(self, voicevox_config, audio_output_dir, fake_db):
        fake_db["assets"] = [
            {"id": 1, "scene_index": 0, "script_text": "scene0 ok", "speaker": "hina", "wav_path": None},
            {"id": 2, "scene_index": 1, "script_text": "scene1 fail", "speaker": "hina", "wav_path": None},
        ]
        client = _FakeClient(healthy=True, fail_scenes={1})

        result = va.synthesize_project_audio(1, client=client)

        assert result["synthesized"] == 1
        assert result["failed"] == 1
        assert len(result["errors"]) == 1
        assert result["status"] == "draft"
        assert fake_db["status"] is None  # audio_ready へは進めない

    def test_unknown_speaker_counts_as_failure(self, voicevox_config, audio_output_dir, fake_db):
        fake_db["assets"] = [
            {"id": 1, "scene_index": 0, "script_text": "scene0", "speaker": "unknown_speaker", "wav_path": None},
        ]
        client = _FakeClient(healthy=True)

        result = va.synthesize_project_audio(1, client=client)

        assert result["failed"] == 1
        assert result["status"] == "draft"
        assert client.calls == []
