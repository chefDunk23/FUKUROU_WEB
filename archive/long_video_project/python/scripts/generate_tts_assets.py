"""
scripts/generate_tts_assets.py
================================
VOICEVOX TTS パイプライン。

Claude API が生成した「scenes 構造 JSON」を入力として受け取り、
各 dialogue.text を VOICEVOX で音声合成して WAV ファイルを保存し、
audio_url / audio_duration_ms を付与した「完全版 JSON」を出力する。

パイプラインフロー:
  1. Input JSON 読み込み（scenes[] + dialogue[]）
  2. 各 dialogue を VOICEVOX API に送信 → WAV 生成
  3. WAV を owl_video/public/audio/{session}/{scene_id}_{index:03d}.wav に保存
  4. WAV ヘッダーから正確な再生時間（ms）を取得
  5. audio_url / audio_duration_ms を JSON に書き込み
  6. 完全版 JSON を data/output/final_video_data.json に保存

Usage:
    # VOICEVOX が起動中であることを確認してから実行
    py -3.13 scripts/generate_tts_assets.py --input data/output/dialogue_20260517_kyoto.json
    py -3.13 scripts/generate_tts_assets.py --input X.json --voicevox http://localhost:50021
    py -3.13 scripts/generate_tts_assets.py --input X.json --dry-run   # API不要・ダミー音声
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import sys
import time
import wave
from pathlib import Path
from typing import Any

import requests

# Windows UTF-8 出力
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.video_generator.script_generator import VOICEVOX_SPEAKER_IDS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── 定数 ──────────────────────────────────────────────────────────────────────

_DEFAULT_VOICEVOX_URL   = "http://localhost:50021"
_DEFAULT_INPUT          = Path("data/input/draft_video_data.json")
_DEFAULT_OUTPUT         = Path("data/output/final_video_data.json")
_REMOTION_AUDIO_ROOT    = Path(_ROOT / "owl_video" / "public" / "audio")
_FALLBACK_DURATION_MS   = 5000   # VOICEVOX 失敗時のデフォルト尺（5秒）
_MAX_RETRIES            = 3
_RETRY_BACKOFF_BASE     = 1.0    # 秒


# ══════════════════════════════════════════════════════════════════════════════
# VOICEVOX クライアント
# ══════════════════════════════════════════════════════════════════════════════

class VoicevoxClient:
    """VOICEVOX HTTP API ラッパー（リトライ付き）。"""

    def __init__(self, base_url: str = _DEFAULT_VOICEVOX_URL) -> None:
        self.base_url = base_url.rstrip("/")

    def _request(
        self,
        method: str,
        path: str,
        max_retries: int = _MAX_RETRIES,
        **kwargs: Any,
    ) -> requests.Response:
        url = f"{self.base_url}{path}"
        for attempt in range(max_retries):
            try:
                resp = requests.request(method, url, timeout=30, **kwargs)
                resp.raise_for_status()
                return resp
            except requests.RequestException as e:
                if attempt == max_retries - 1:
                    raise
                wait = _RETRY_BACKOFF_BASE * (2 ** attempt)
                log.warning(
                    "VOICEVOX リトライ %d/%d: %s (%.1fs 待機)",
                    attempt + 1, max_retries, e, wait,
                )
                time.sleep(wait)
        raise RuntimeError("到達不能")  # pragma: no cover

    def audio_query(self, text: str, speaker_id: int) -> dict:
        """テキストから合成クエリを生成する。"""
        resp = self._request(
            "POST",
            "/audio_query",
            params={"text": text, "speaker": speaker_id},
        )
        return resp.json()

    def synthesis(self, query: dict, speaker_id: int) -> bytes:
        """合成クエリから WAV バイト列を生成する。"""
        resp = self._request(
            "POST",
            "/synthesis",
            params={"speaker": speaker_id},
            json=query,
        )
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


# ══════════════════════════════════════════════════════════════════════════════
# WAV ユーティリティ
# ══════════════════════════════════════════════════════════════════════════════

def get_wav_duration_ms(wav_bytes: bytes) -> int:
    """WAV バイト列から正確な再生時間（ミリ秒）を返す。"""
    with wave.open(io.BytesIO(wav_bytes)) as wf:
        frames   = wf.getnframes()
        framerate = wf.getframerate()
        return int(frames / framerate * 1000)


def _make_silence_wav(duration_ms: int = _FALLBACK_DURATION_MS) -> bytes:
    """フォールバック用の無音 WAV を生成する（モノラル 24kHz 16bit）。"""
    framerate  = 24000
    n_frames   = int(framerate * duration_ms / 1000)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(framerate)
        wf.writeframes(b"\x00\x00" * n_frames)
    return buf.getvalue()


# ══════════════════════════════════════════════════════════════════════════════
# セッション名 → ファイルシステム安全な文字列
# ══════════════════════════════════════════════════════════════════════════════

def _sanitize_session(session_label: str) -> str:
    """
    "2026-05-17 京都" → "20260517_京都"
    Remotion 側と既存の audio ファイル命名規則に合わせる。
    """
    return session_label.replace("-", "").replace(" ", "_").replace("/", "")


# ══════════════════════════════════════════════════════════════════════════════
# メインパイプライン
# ══════════════════════════════════════════════════════════════════════════════

def process_tts(
    input_path: Path,
    output_path: Path = _DEFAULT_OUTPUT,
    voicevox_url: str = _DEFAULT_VOICEVOX_URL,
    dry_run: bool = False,
) -> dict:
    """
    JSON を読み込み、TTS 合成を実行して完全版 JSON を返す。

    Parameters
    ----------
    input_path   : 入力 JSON ファイルパス
    output_path  : 出力 JSON ファイルパス
    voicevox_url : VOICEVOX サーバーの URL
    dry_run      : True → VOICEVOX を叩かず無音WAVで代替（テスト用）

    Returns
    -------
    dict — audio_url / audio_duration_ms が全件埋まった完全版 JSON
    """
    # ── 1. 入力 JSON 読み込み ──────────────────────────────────────────────
    log.info("入力 JSON 読み込み: %s", input_path)
    data = json.loads(input_path.read_text(encoding="utf-8"))

    session_label = data.get("session", "unknown")
    session_dir   = _sanitize_session(session_label)
    audio_base    = _REMOTION_AUDIO_ROOT / session_dir
    audio_base.mkdir(parents=True, exist_ok=True)
    log.info("  セッション: %s  音声出力先: %s", session_label, audio_base)

    # ── 2. VOICEVOX クライアント初期化 ────────────────────────────────────
    client = VoicevoxClient(voicevox_url)
    if not dry_run:
        if not client.health_check():
            log.warning(
                "VOICEVOX (%s) に接続できません。dry_run モードに切り替えます。",
                voicevox_url,
            )
            dry_run = True
        else:
            log.info("  VOICEVOX 接続確認: OK")

    # ── 3. 各 dialogue を合成 ─────────────────────────────────────────────
    total_turns = success = failed = 0

    for scene in data.get("scenes", []):
        scene_id = scene.get("scene_id", "scene_unknown")
        for idx, dlg in enumerate(scene.get("dialogue", [])):
            total_turns += 1
            text       = dlg.get("text", "")
            speaker    = dlg.get("speaker", "")
            speaker_id = VOICEVOX_SPEAKER_IDS.get(speaker, 3)  # デフォルト=ずんだもん

            wav_filename = f"{scene_id}_{idx:03d}.wav"
            wav_path     = audio_base / wav_filename
            # Remotion は staticFile("audio/...") で参照する相対パス
            audio_url    = f"audio/{session_dir}/{wav_filename}"

            wav_bytes: bytes | None = None

            if dry_run:
                wav_bytes = _make_silence_wav(_FALLBACK_DURATION_MS)
                log.debug("  [DRY-RUN] %s / %s", scene_id, wav_filename)
            else:
                try:
                    wav_bytes = client.text_to_wav(text, speaker_id)
                    success += 1
                    log.debug("  OK: %s  %dms", wav_filename,
                              get_wav_duration_ms(wav_bytes))
                except Exception as e:
                    log.warning(
                        "  [FAIL] %s (speaker=%d): %s → フォールバック5秒",
                        wav_filename, speaker_id, e,
                    )
                    wav_bytes = _make_silence_wav(_FALLBACK_DURATION_MS)
                    failed += 1

            # WAV ファイル書き込み
            wav_path.write_bytes(wav_bytes)

            # JSON 更新
            dlg["audio_url"]         = audio_url
            dlg["audio_duration_ms"] = get_wav_duration_ms(wav_bytes)

    log.info(
        "合成完了: 総%d件  成功=%d  失敗=%d（フォールバック済み）",
        total_turns, success if not dry_run else 0, failed,
    )

    # ── 4. 完全版 JSON を保存 ─────────────────────────────────────────────
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("完全版 JSON 保存: %s", output_path)

    # ── 5. サマリー出力 ───────────────────────────────────────────────────
    _print_summary(data)
    return data


def _print_summary(data: dict) -> None:
    """完全版 JSON の統計サマリーをコンソールに表示する。"""
    sep = "=" * 60
    print(f"\n{sep}")
    print(f"  完全版 JSON サマリー — {data.get('session', '?')}")
    print(sep)
    total_ms = 0
    for scene in data.get("scenes", []):
        sid  = scene.get("scene_id", "?")
        stype = scene.get("scene_type", "?")
        dlgs = scene.get("dialogue", [])
        scene_ms = sum(d.get("audio_duration_ms", 0) for d in dlgs)
        total_ms += scene_ms
        print(f"  {sid:<22}  type={stype:<10}  {len(dlgs):>2}turns  {scene_ms/1000:5.1f}s")
    print(f"  {'─'*56}")
    print(f"  総尺（推定）: {total_ms/1000:.1f}s  ({total_ms/60000:.1f}分)")
    print(sep)
    print()


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="VOICEVOX TTS パイプライン")
    p.add_argument(
        "--input", "-i", type=Path, default=_DEFAULT_INPUT,
        help=f"入力 JSON ファイル（LLM 生成 scenes 構造、デフォルト: {_DEFAULT_INPUT}）",
    )
    p.add_argument(
        "--output", "-o", type=Path, default=_DEFAULT_OUTPUT,
        help=f"完全版 JSON の出力先（デフォルト: {_DEFAULT_OUTPUT}）",
    )
    p.add_argument(
        "--voicevox", type=str, default=_DEFAULT_VOICEVOX_URL,
        help=f"VOICEVOX サーバー URL（デフォルト: {_DEFAULT_VOICEVOX_URL}）",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="VOICEVOX を叩かず無音 WAV で代替（動作確認・CI 用）",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    if not args.input.exists():
        log.error("入力 JSON が見つかりません: %s", args.input)
        sys.exit(1)
    process_tts(
        input_path=args.input,
        output_path=args.output,
        voicevox_url=args.voicevox,
        dry_run=args.dry_run,
    )
