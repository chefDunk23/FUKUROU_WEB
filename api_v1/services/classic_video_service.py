"""
api_v1/services/classic_video_service.py
==========================================
ClassicVideo パイプラインのサービス層。
run.ps1（CLI）と api_v1 ルーター の両方から呼び出せる。

ジョブ進捗は data/jobs/{job_id}.json に永続化する（Uvicorn reload 対策）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Optional

# FastAPI と同じ Python インタープリターを使う（PATH 依存を排除）
_PY = sys.executable

log = logging.getLogger(__name__)

_ROOT     = Path(__file__).parent.parent.parent
_SCRIPTS  = _ROOT / "scripts"
_DATA     = _ROOT / "data"
_JOBS_DIR = _DATA / "jobs"
_OWL      = _ROOT / "owl_video"

# Windows では npx は npx.cmd として登録されており、shell=False だと見つからない
_NPX = "npx.cmd" if sys.platform == "win32" else "npx"

_JOBS_DIR.mkdir(parents=True, exist_ok=True)


# ── ジョブ状態 ────────────────────────────────────────────────────────────────

class JobStatus:
    PENDING  = "pending"
    TTS      = "tts"
    RENDER   = "render"
    DONE     = "done"
    ERROR    = "error"


def _job_path(job_id: str) -> Path:
    return _JOBS_DIR / f"{job_id}.json"


def read_job(job_id: str) -> dict:
    p = _job_path(job_id)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {}


def _write_job(job_id: str, state: dict) -> None:
    _job_path(job_id).write_text(
        json.dumps(state, ensure_ascii=False), encoding="utf-8"
    )


def new_job() -> str:
    job_id = uuid.uuid4().hex[:12]
    _write_job(job_id, {
        "status":       JobStatus.PENDING,
        "tts_done":     0,
        "tts_total":    0,
        "remotion_pct": 0,
        "error":        None,
        "mp4_path":     None,
    })
    return job_id


def _patch_job(job_id: str, **kwargs: object) -> None:
    state = read_job(job_id)
    state.update(kwargs)
    _write_job(job_id, state)


# ── 環境変数 ──────────────────────────────────────────────────────────────────

def _env() -> dict[str, str]:
    return {**os.environ, "PYTHONUNBUFFERED": "1"}


# ── Phase 1: Prompt JSON 生成 ─────────────────────────────────────────────────

async def generate_prompt(date: str, venue: Optional[str] = None) -> Path:
    """generate_prompt.py をサブプロセスで実行し、出力 JSON パスを返す。"""
    cmd = [_PY, str(_SCRIPTS / "generate_prompt.py"), "--date", date]
    if venue:
        cmd += ["--venue", venue]

    log.info("generate_prompt: %s venue=%s  (interpreter=%s)", date, venue, _PY)

    def _run() -> subprocess.CompletedProcess:
        return subprocess.run(
            cmd,
            cwd=str(_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_env(),
        )

    result = await asyncio.to_thread(_run)
    output = result.stdout + result.stderr
    if result.returncode != 0:
        log.error("generate_prompt failed (code %d):\n%s", result.returncode, output)
        raise RuntimeError(output or f"プロセスが終了コード {result.returncode} で失敗しました")

    date_compact = date.replace("-", "")
    suffix   = venue if venue else "all"
    out_path = _DATA / "output" / f"raw_race_data_{date_compact}_{suffix}.json"
    if not out_path.exists():
        raise FileNotFoundError(f"JSON が見つかりません: {out_path}")
    return out_path


# ── Phase 3: TTS + Remotion レンダー (非同期ジョブ) ──────────────────────────

async def run_render_job(job_id: str, draft_json: dict) -> None:
    """TTS → Remotion を非同期バックグラウンドで実行し、進捗を job ファイルに書く。"""
    try:
        # 入力 JSON を data/input に保存
        draft_path  = _DATA / "input" / f"draft_{job_id}.json"
        output_path = _DATA / "output" / f"classic_video_data_{job_id}.json"
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        draft_path.write_text(
            json.dumps(draft_json, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # TTS
        await _run_tts(job_id, draft_path, output_path)

        # Remotion public/ にコピー
        remotion_dst = _OWL / "public" / "data" / "classic_video_data.json"
        remotion_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(output_path, remotion_dst)

        # Remotion レンダー（出力先: data/videos/classic/）
        mp4_path = _DATA / "videos" / "classic" / f"classic_video_{job_id}.mp4"
        mp4_path.parent.mkdir(parents=True, exist_ok=True)
        await _run_remotion(job_id, mp4_path)

        _patch_job(job_id, status=JobStatus.DONE, mp4_path=str(mp4_path))
        log.info("Job %s: DONE → %s", job_id, mp4_path)

    except Exception as exc:
        log.error("Job %s failed: %s", job_id, exc)
        _patch_job(job_id, status=JobStatus.ERROR, error=str(exc))


# ── TTS ───────────────────────────────────────────────────────────────────────

async def _run_tts(job_id: str, draft_path: Path, output_path: Path) -> None:
    _patch_job(job_id, status=JobStatus.TTS, tts_done=0)

    cmd = [
        _PY, str(_SCRIPTS / "generate_tts_classic.py"),
        "--input",  str(draft_path),
        "--output", str(output_path),
    ]
    # TTS は長時間かかるのでスレッドで実行しながら進捗を追跡する
    def _run_tts_sync() -> None:
        tts_done = 0
        with subprocess.Popen(
            cmd,
            cwd=str(_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_env(),
        ) as proc:
            for line in proc.stdout:  # type: ignore[union-attr]
                line = line.rstrip()
                if re.search(r"\bOK:\s+\S+\s+\d+行", line):
                    tts_done += 1
                    _patch_job(job_id, tts_done=tts_done)
                elif m := re.search(r"合成完了:\s*(\d+)件", line):
                    _patch_job(job_id, tts_total=int(m.group(1)))
            proc.wait()
            if proc.returncode != 0:
                raise RuntimeError(f"TTS プロセスが失敗しました (code {proc.returncode})")

    await asyncio.to_thread(_run_tts_sync)


# ── Remotion レンダー ─────────────────────────────────────────────────────────

async def _run_remotion(job_id: str, mp4_path: Path) -> None:
    _patch_job(job_id, status=JobStatus.RENDER, remotion_pct=0)

    # 絶対パスで渡す（Remotion は絶対パスをサポート）
    cmd = [_NPX, "remotion", "render", "ClassicVideo", str(mp4_path)]

    def _run_remotion_sync() -> None:
        buf = ""
        with subprocess.Popen(
            cmd,
            cwd=str(_OWL),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=False,          # バイト読み込み（\r 対策 Trap 3）
            env=_env(),
        ) as proc:
            while True:
                chunk = proc.stdout.read(4096)  # type: ignore[union-attr]
                if not chunk:
                    break
                # \r と \n の両方で分割して Remotion の上書き進捗に対応
                buf += chunk.decode("utf-8", errors="replace")
                parts = re.split(r"[\r\n]+", buf)
                buf = parts[-1]
                for part in parts[:-1]:
                    if m := re.search(r"Rendered\s+(\d+)/(\d+)", part):
                        n, total = int(m.group(1)), int(m.group(2))
                        pct = int(n / total * 100) if total > 0 else 0
                        _patch_job(job_id, remotion_pct=pct)
            proc.wait()
            if proc.returncode != 0:
                raise RuntimeError(f"Remotion レンダーが失敗しました (code {proc.returncode})")

    await asyncio.to_thread(_run_remotion_sync)
