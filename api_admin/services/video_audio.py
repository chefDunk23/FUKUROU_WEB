"""
api_admin/services/video_audio.py
====================================
Phase 2: video_audio_assets の script_text を VOICEVOX で実際に音声合成し、
wav_path/duration_sec をDBに書き込む。全シーン合成完了で
video_projects.status を draft → audio_ready に進める。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from api_admin.services import video_db
from api_admin.services.voicevox_client import VoicevoxClient, VoicevoxError, get_wav_duration_sec

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent
_VOICEVOX_CONFIG_PATH = _ROOT / "keiba_pick_video" / "voicevox.config.json"
_AUDIO_OUTPUT_ROOT = _ROOT / "keiba_pick_video" / "public" / "audio"


class VideoAudioError(RuntimeError):
    """音声合成の入力データ不整合・前提条件未達（fail-fast用）。"""


def load_speaker_ids() -> dict[str, int]:
    """voicevox.config.json から {speaker_key: voicevox_speaker_id} を読み込む。"""
    if not _VOICEVOX_CONFIG_PATH.exists():
        raise VideoAudioError(f"voicevox.config.json が見つかりません: {_VOICEVOX_CONFIG_PATH}")
    config = json.loads(_VOICEVOX_CONFIG_PATH.read_text(encoding="utf-8"))
    speakers = config.get("speakers", {})
    if not speakers:
        raise VideoAudioError(f"voicevox.config.json に speakers 定義がありません: {_VOICEVOX_CONFIG_PATH}")
    return {key: entry["voicevox_speaker_id"] for key, entry in speakers.items()}


def synthesize_project_audio(
    project_id: int,
    client: VoicevoxClient | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """project_id の video_audio_assets を VOICEVOX で合成し、DBを更新する。

    音声はキャラクターの声そのもの（尺・演技の核）を左右するため、VOICEVOX未起動時に
    無音WAVへ静かにフォールバックすることはしない（fail-fast、CLAUDE.md §5）。
    合成失敗（個別シーン）は行単位でエラーを蓄積し、他シーンの処理は継続する。

    Args:
        project_id: video_projects.id
        client:     省略時は VoicevoxClient() の既定 URL (localhost:50021) を使う
        force:      True なら wav_path が既にある行も再合成する（既定はスキップ）

    Returns:
        {"project_id", "synthesized", "skipped", "failed", "errors", "status"}
    """
    client = client or VoicevoxClient()
    if not client.health_check():
        raise VideoAudioError(
            f"VOICEVOX ({client.base_url}) に接続できません。VOICEVOXエンジンを起動してから再実行してください。"
        )

    speaker_ids = load_speaker_ids()
    assets = video_db.list_audio_assets(project_id)
    if not assets:
        raise VideoAudioError(f"project_id={project_id} の video_audio_assets が空です。")

    output_dir = _AUDIO_OUTPUT_ROOT / str(project_id)
    output_dir.mkdir(parents=True, exist_ok=True)

    synthesized = skipped = failed = 0
    errors: list[str] = []

    for asset in assets:
        if asset.get("wav_path") and not force:
            skipped += 1
            continue

        speaker_key = asset["speaker"]
        speaker_id = speaker_ids.get(speaker_key)
        if speaker_id is None:
            failed += 1
            errors.append(f"scene_index={asset['scene_index']}: 未知のspeaker '{speaker_key}'")
            continue

        scene_index = asset["scene_index"]
        wav_filename = f"{scene_index:03d}.wav"
        wav_abs_path = output_dir / wav_filename
        # Remotion の staticFile() は public/ からの相対パスを期待する
        wav_relative_path = f"audio/{project_id}/{wav_filename}"

        try:
            wav_bytes = client.text_to_wav(asset["script_text"], speaker_id)
        except VoicevoxError as e:
            failed += 1
            errors.append(f"scene_index={scene_index}: {e}")
            log.warning("音声合成失敗 project_id=%s scene_index=%s: %s", project_id, scene_index, e)
            continue

        duration_sec = get_wav_duration_sec(wav_bytes)
        wav_abs_path.write_bytes(wav_bytes)
        video_db.update_audio_asset(asset["id"], wav_relative_path, duration_sec)
        synthesized += 1
        log.info(
            "音声合成完了 project_id=%s scene_index=%s speaker=%s %.2fs",
            project_id, scene_index, speaker_key, duration_sec,
        )

    all_ready = (skipped + synthesized) == len(assets) and failed == 0
    status = "audio_ready" if all_ready else "draft"
    if all_ready:
        video_db.update_project_status(project_id, status)

    return {
        "project_id": project_id,
        "synthesized": synthesized,
        "skipped": skipped,
        "failed": failed,
        "errors": errors,
        "status": status,
    }
