"""
scripts/generate_tts_classic.py
=================================
Phase 3a — ClassicVideo 形式 JSON への VOICEVOX TTS 合成。

input:  data/input/draft_classic_video.json  (LLM 生成 races[] 構造)
output: data/output/classic_video_data.json  (audio_url / audio_duration_ms 付き)
        owl_video/public/audio/classic/{session_dir}/{race_id}.wav

speech_text が「博士：「...」\n助手：「...」」形式の場合、
キャラクター別のスピーカー ID で合成して1つの WAV に連結する。
  博士（フクロウ博士）= speaker 13
  助手（ひよこ）      = speaker 2（四国めたん ノーマル）

VOICEVOX が起動していない場合は自動的に dry-run（無音 WAV）にフォールバック。

Usage:
    py -3.13 scripts/generate_tts_classic.py
    py -3.13 scripts/generate_tts_classic.py --input  data/input/draft_classic_video.json
    py -3.13 scripts/generate_tts_classic.py --dry-run
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import re
import sys
import time
import wave
from pathlib import Path
from typing import Any

import requests

try:
    import pykakasi as _pykakasi
    _kks = _pykakasi.kakasi()
    _KAKASI_AVAILABLE = True
except ImportError:
    _kks = None
    _KAKASI_AVAILABLE = False

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── 定数 ─────────────────────────────────────────────────────────────────────

_DEFAULT_VOICEVOX_URL = "http://localhost:50021"
_DEFAULT_INPUT        = Path("data/input/draft_classic_video.json")
_DEFAULT_OUTPUT       = Path("data/output/classic_video_data.json")
_REMOTION_AUDIO_ROOT  = _ROOT / "owl_video" / "public" / "audio" / "classic"
_FALLBACK_MS          = 8000
_MAX_RETRIES          = 3
_RETRY_BACKOFF        = 1.0

# フクロウ博士 = 青山龍星 ノーマル (VOICEVOX ID 13)
_FUKURO_SPEAKER_ID    = 13
# 助手 = 春日部つむぎ ノーマル (VOICEVOX ID 8)
_JOSHU_SPEAKER_ID     = 8

# スピーカーラベル → VOICEVOX speaker ID マッピング
_SPEAKER_MAP: dict[str, int] = {
    "博士": _FUKURO_SPEAKER_ID,
    "助手": _JOSHU_SPEAKER_ID,
}

# 読み上げ速度（1.0 = 標準）
_SPEECH_SPEED = 1.0
# 各発話ライン間のポーズ（ms）— 会話の間を作る
_LINE_PAUSE_MS = 400


# ── VOICEVOX クライアント ─────────────────────────────────────────────────────

class VoicevoxClient:
    def __init__(self, base_url: str = _DEFAULT_VOICEVOX_URL) -> None:
        self.base_url = base_url.rstrip("/")

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        url = f"{self.base_url}{path}"
        for attempt in range(_MAX_RETRIES):
            try:
                resp = requests.request(method, url, timeout=30, **kwargs)
                resp.raise_for_status()
                return resp
            except requests.RequestException as exc:
                if attempt == _MAX_RETRIES - 1:
                    raise
                wait = _RETRY_BACKOFF * (2 ** attempt)
                log.warning("VOICEVOX リトライ %d/%d: %s (%.1fs 待機)", attempt + 1, _MAX_RETRIES, exc, wait)
                time.sleep(wait)
        raise RuntimeError("到達不能")  # pragma: no cover

    def health_check(self) -> bool:
        try:
            self._request("GET", "/version", max_retries=1)  # type: ignore[call-arg]
            return True
        except Exception:
            try:
                self._request("GET", "/version")
                return True
            except Exception:
                return False

    def text_to_wav(
        self,
        text: str,
        speaker_id: int = _FUKURO_SPEAKER_ID,
        speed: float = _SPEECH_SPEED,
    ) -> bytes:
        query = self._request(
            "POST", "/audio_query",
            params={"text": text, "speaker": speaker_id},
        ).json()
        query["speedScale"] = speed
        return self._request(
            "POST", "/synthesis",
            params={"speaker": speaker_id},
            json=query,
        ).content


# ── WAV ユーティリティ ────────────────────────────────────────────────────────

def _wav_duration_ms(wav_bytes: bytes) -> int:
    with wave.open(io.BytesIO(wav_bytes)) as wf:
        return int(wf.getnframes() / wf.getframerate() * 1000)


def _silence_wav(duration_ms: int = _FALLBACK_MS) -> bytes:
    framerate = 24000
    n_frames  = int(framerate * duration_ms / 1000)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(framerate)
        wf.writeframes(b"\x00\x00" * n_frames)
    return buf.getvalue()


def _strip_citations(text: str) -> str:
    """LLM が混入させる引用注釈 [cite: N] を除去する。"""
    return re.sub(r'\s*\[cite:[^\]]*\]', '', text).strip()


def _parse_dialogue(text: str) -> list[tuple[str, str]]:
    """「博士：「...」」形式を (speaker, content) のリストに変換する。
    ラベルなし行は博士として扱う（後方互換）。"""
    lines = text.split("\n")
    result: list[tuple[str, str]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        m = re.match(r'^(.+?)：「(.+?)」\s*$', line)
        if m:
            result.append((m.group(1), m.group(2)))
        else:
            result.append(("博士", line))
    return result


def _clean_speech(text: str) -> str:
    """「博士：「...」\n助手：「...」」形式のラベルを除去してテロップ表示用テキストに変換。"""
    return "".join(content for _, content in _parse_dialogue(text))


def _concat_wavs(wav_bytes_list: list[bytes]) -> bytes:
    """同一フォーマットの WAV バイト列を連結して1つの WAV を返す。"""
    all_frames = b""
    framerate: int | None = None
    nchannels: int | None = None
    sampwidth: int | None = None

    for wb in wav_bytes_list:
        with wave.open(io.BytesIO(wb)) as wf:
            if framerate is None:
                framerate = wf.getframerate()
                nchannels = wf.getnchannels()
                sampwidth = wf.getsampwidth()
            all_frames += wf.readframes(wf.getnframes())

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(nchannels or 1)
        wf.setsampwidth(sampwidth or 2)
        wf.setframerate(framerate or 24000)
        wf.writeframes(all_frames)
    return buf.getvalue()


def _to_reading(text: str) -> str:
    """VoiceVox の内部辞書に任せるため変換なしで返す。
    pykakasi は逆に誤変換を増やすケースがあるため現在は無効。"""
    return text


# 略語など VoiceVox が誤読しやすいパターンを事前に変換する
_TTS_REPLACEMENTS: list[tuple[str, str]] = [
    ("G1",  "ジーワン"),
    ("G2",  "ジーツー"),
    ("G3",  "ジースリー"),
]


def _prepare_tts_text(text: str) -> str:
    """自然な日本語テキスト（漢字混じり）を VoiceVox に渡す前処理。
    ・助詞「は」は漢字テキストとして渡すことで VoiceVox が「わ」と正しく読む
    ・G1 等の競馬略語のみ事前に変換する
    """
    for src, dst in _TTS_REPLACEMENTS:
        text = text.replace(src, dst)
    return text


def _format_date_jp(session: str) -> str:
    """「2026-05-17」→「2026年5月17日」に変換してVoiceVoxが正しく読める形にする。"""
    try:
        y, m, d = session.split("-")
        return f"{y}年{int(m)}月{int(d)}日"
    except Exception:
        return session


def _normalize_display_text(text: str) -> str:
    """テロップ表示用テキストの正規化。LLMがカタカナで書いた用語を正しい表記に戻す。"""
    return text.replace("エーアイ", "AI")


def _build_intro_dialogue(data: dict) -> list[tuple[str, str]]:
    """目次画面専用の読み上げ台本を自動生成する。
    各レース解説の冒頭と重複しないよう、挨拶と番組紹介にとどめる。"""
    session = data.get("session", "")
    venue   = data.get("venue", "")
    total   = len(data.get("races", []))
    date_jp = _format_date_jp(session)
    return [
        ("助手", "みなさん、こんにちは！フクロウ博士のAI競馬予想へようこそ！"),
        ("博士", f"{date_jp}、{venue}競馬の注目{total}レースをご紹介するぞ。"),
        ("助手", "画面の一覧で本命馬をご確認ください！"),
        ("博士", "それでは参ろう。"),
    ]


def _sanitize(s: str) -> str:
    """セッション文字列からファイルシステム安全なASCII文字列を生成する。"""
    return s.replace("-", "").replace(" ", "_").replace("/", "")


# ── メインパイプライン ─────────────────────────────────────────────────────────

def process_tts_classic(
    input_path:   Path = _DEFAULT_INPUT,
    output_path:  Path = _DEFAULT_OUTPUT,
    voicevox_url: str  = _DEFAULT_VOICEVOX_URL,
    dry_run:      bool = False,
) -> dict:
    log.info("入力 JSON 読み込み: %s", input_path)
    data = json.loads(input_path.read_text(encoding="utf-8"))

    session_dir = _sanitize(data.get("session", "unknown"))
    audio_base  = _REMOTION_AUDIO_ROOT / session_dir
    audio_base.mkdir(parents=True, exist_ok=True)
    log.info("  セッション: %s  音声出力先: %s", data.get("session"), audio_base)

    client = VoicevoxClient(voicevox_url)
    if not dry_run:
        if not client.health_check():
            log.warning("VOICEVOX (%s) に接続できません → dry-run に切り替え", voicevox_url)
            dry_run = True
        else:
            log.info("  VOICEVOX 接続: OK")

    success = failed = 0

    # ── intro 音声合成（目次画面用） ───────────────────────────────────────────
    intro_dialogue = _build_intro_dialogue(data)
    intro_wav_path = audio_base / "intro.wav"
    intro_url      = f"audio/classic/{session_dir}/intro.wav"

    if dry_run:
        intro_wav = _silence_wav(max(len(intro_dialogue) * (3000 + _LINE_PAUSE_MS), _FALLBACK_MS))
        log.debug("  [DRY-RUN] intro  %d行", len(intro_dialogue))
    else:
        try:
            intro_parts: list[bytes] = []
            for i, (speaker, content) in enumerate(intro_dialogue):
                sid = _SPEAKER_MAP.get(speaker, _FUKURO_SPEAKER_ID)
                intro_parts.append(client.text_to_wav(_to_reading(content), speaker_id=sid))
                if i < len(intro_dialogue) - 1:
                    intro_parts.append(_silence_wav(_LINE_PAUSE_MS))
            intro_wav = _concat_wavs(intro_parts) if len(intro_parts) > 1 else intro_parts[0]
            ms = _wav_duration_ms(intro_wav)
            log.info("  intro: %d行 %dms", len(intro_dialogue), ms)
            success += 1
        except Exception as exc:
            log.warning("  [FAIL] intro: %s → フォールバック", exc)
            intro_wav = _silence_wav(_FALLBACK_MS)
            failed += 1

    intro_wav_path.write_bytes(intro_wav)
    data["intro_audio_url"]         = intro_url
    data["intro_audio_duration_ms"] = _wav_duration_ms(intro_wav)

    for race in data.get("races", []):
        race_id = race.get("race_id", "unknown")

        # speech_lines（配列形式）優先、なければ speech_text（後方互換）にフォールバック
        raw_lines: list[dict] = race.get("speech_lines") or []
        if raw_lines:
            dialogue = [
                # 自然な日本語テキストを渡す（助詞「は」が「わ」と正しく読まれる）
                (d["speaker"], _prepare_tts_text(_strip_citations(d["text"])))
                for d in raw_lines if d.get("text")
            ]
        else:
            raw_speech = _strip_citations(race.get("speech_text", ""))
            dialogue = _parse_dialogue(raw_speech) if raw_speech else []

        # _seq.index == 1: 冒頭の助手挨拶行を除去（目次画面で既出のため）
        # 最初の「博士」発話が出てくるまでの助手行をスキップする
        seq_index = race.get("_seq", {}).get("index", 0)
        _skip_count = 0  # speech_lines インデックスのオフセット（タイミング書き戻し用）
        if seq_index == 1 and dialogue:
            first_hakase = next(
                (i for i, (spk, _) in enumerate(dialogue) if spk == "博士"), 0
            )
            if first_hakase > 0:
                log.info("  _seq.index=1: 冒頭%d行をスキップ（挨拶除去）", first_hakase)
                _skip_count = first_hakase
                dialogue = dialogue[first_hakase:]

        # テロップ表示用テキストを speech_text に書き込む（TelopBar が使用）
        race["speech_text"] = "".join(text for _, text in dialogue)

        # speech_lines を更新: text は表示用（漢字）、reading は読み上げ用（ひらがな）を保持
        if raw_lines:
            race["speech_lines"] = [
                {
                    "speaker": d["speaker"],
                    "text": _normalize_display_text(_strip_citations(d["text"])),  # テロップ表示用
                    **( {"reading": _strip_citations(d["reading"])} if d.get("reading") else {} ),
                }
                for d in raw_lines if d.get("text")
            ]

        # telop の [cite:...] も除去
        if race.get("telop"):
            race["telop"] = _strip_citations(race["telop"])
        # evaluation_reason / concern の [cite:...] も除去
        for pick in race.get("picks", []):
            if pick.get("evaluation_reason"):
                pick["evaluation_reason"] = _strip_citations(pick["evaluation_reason"])
            if pick.get("concern"):
                pick["concern"] = _strip_citations(pick["concern"])

        wav_file  = f"{race_id}.wav"
        wav_path  = audio_base / wav_file
        audio_url = f"audio/classic/{session_dir}/{wav_file}"

        line_durations_ms: list[int] = []  # 各行の実尺 + ポーズ (ms)

        if dry_run or not dialogue:
            est_ms = max(len(dialogue) * 3000, _FALLBACK_MS) if dialogue else _FALLBACK_MS
            wav_bytes = _silence_wav(est_ms)
            if not dialogue:
                log.debug("  speech_lines/speech_text なし → 無音: %s", race_id)
            else:
                log.debug("  [DRY-RUN] %s  %d行 推定%dms", race_id, len(dialogue), est_ms)
        else:
            try:
                parts: list[bytes] = []
                for i, (speaker, content) in enumerate(dialogue):
                    sid = _SPEAKER_MAP.get(speaker, _FUKURO_SPEAKER_ID)
                    wav = client.text_to_wav(_to_reading(content), speaker_id=sid)
                    dur = _wav_duration_ms(wav)
                    parts.append(wav)
                    # ポーズ込みの行尺（最終行はポーズなし）
                    line_total = dur + (_LINE_PAUSE_MS if i < len(dialogue) - 1 else 0)
                    line_durations_ms.append(line_total)
                    if i < len(dialogue) - 1:
                        parts.append(_silence_wav(_LINE_PAUSE_MS))
                wav_bytes = _concat_wavs(parts) if len(parts) > 1 else parts[0]
                ms = _wav_duration_ms(wav_bytes)
                log.info("  OK: %s  %d行 %dms", race_id, len(dialogue), ms)
                success += 1
            except Exception as exc:
                log.warning("  [FAIL] %s: %s → フォールバック", race_id, exc)
                wav_bytes = _silence_wav(_FALLBACK_MS)
                line_durations_ms = []
                failed += 1

        wav_path.write_bytes(wav_bytes)
        race["audio_url"]         = audio_url
        race["audio_duration_ms"] = _wav_duration_ms(wav_bytes)

        # speech_lines に行ごとのタイミングを付与（TelopBar の正確同期に使用）
        if line_durations_ms and race.get("speech_lines"):
            cum_ms = 0
            for j, line in enumerate(race["speech_lines"]):
                spoken_idx = j - _skip_count
                if 0 <= spoken_idx < len(line_durations_ms):
                    line["line_duration_ms"] = line_durations_ms[spoken_idx]
                    line["line_offset_ms"]   = cum_ms
                    cum_ms += line_durations_ms[spoken_idx]

    log.info("合成完了: %d件  成功=%d  失敗=%d", len(data.get("races", [])), success, failed)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("完全版 JSON 保存: %s", output_path)

    _print_summary(data)
    return data


def _print_summary(data: dict) -> None:
    sep = "=" * 60
    print(f"\n{sep}")
    print(f"  ClassicVideo TTS サマリー — {data.get('date', '?')} {data.get('venue', '?')}")
    print(sep)
    total_ms = 0
    for race in data.get("races", []):
        ms = race.get("audio_duration_ms", 0)
        total_ms += ms
        label = race.get("race_label", race.get("race_id", "?"))[:30]
        print(f"  {label:<30}  {ms/1000:5.1f}s")
    print(f"  {'─'*52}")
    print(f"  総尺（推定）: {total_ms/1000:.1f}s  ({total_ms/60000:.1f}分)")
    print(f"{sep}\n")


# ── CLI ──────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="ClassicVideo VOICEVOX TTS パイプライン")
    p.add_argument("--input",    "-i", type=Path, default=_DEFAULT_INPUT)
    p.add_argument("--output",   "-o", type=Path, default=_DEFAULT_OUTPUT)
    p.add_argument("--voicevox", type=str, default=_DEFAULT_VOICEVOX_URL)
    p.add_argument("--dry-run",  action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    if not args.input.exists():
        log.error("入力 JSON が見つかりません: %s", args.input)
        sys.exit(1)
    process_tts_classic(
        input_path=args.input,
        output_path=args.output,
        voicevox_url=args.voicevox,
        dry_run=args.dry_run,
    )
