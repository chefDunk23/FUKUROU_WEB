"""
api_v1/routers/long_video.py
==============================
Developer 専用 — 横型長尺動画 (FukuroLongVideo) 生成パイプライン API。

  POST /api/dev/video/prompt         Phase 1: プロンプトテキスト生成
  POST /api/dev/video/draft          Phase 2: 下書き JSON 保存
  POST /api/dev/video/render         Phase 3: TTS + Remotion レンダリング開始
  GET  /api/dev/video/render/status  Phase 3: レンダリング進捗確認

一般ユーザー向け API（/api/v1/...）とは完全分離。
VITE_DEV_MODE=true 時の開発者専用画面からのみ呼ばれる想定。
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import pandas as pd
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

_ROOT = Path(__file__).parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.generate_tts_assets import process_tts
from src.video_generator.corner_router import KEIBAJO_LABELS, route_session
from src.video_generator.prompt_builder import build_script_prompt

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/dev/video", tags=["dev-long-video"])

# ── パス定数 ──────────────────────────────────────────────────────────────────

_SCORES_PARQUET  = _ROOT / "outputs" / "v2_stacked_features.parquet"
_DRAFT_INPUT     = _ROOT / "data" / "input" / "draft_video_data.json"
_TTS_OUTPUT      = _ROOT / "data" / "output" / "final_video_data.json"
_REMOTION_PUBLIC = _ROOT / "owl_video" / "public" / "data" / "final_video_data.json"
_REMOTION_DIR    = _ROOT / "owl_video"
_VIDEO_OUT       = _ROOT / "owl_video" / "out" / "video.mp4"
_COMPOSITION     = "FukuroLongVideo"

# 場コード → 日本語名（UI ドロップダウン用）
VENUE_OPTIONS: list[dict] = [
    {"code": "01", "name": "札幌"},
    {"code": "02", "name": "函館"},
    {"code": "03", "name": "福島"},
    {"code": "04", "name": "新潟"},
    {"code": "05", "name": "東京"},
    {"code": "06", "name": "中山"},
    {"code": "07", "name": "中京"},
    {"code": "08", "name": "京都"},
    {"code": "09", "name": "阪神"},
    {"code": "10", "name": "小倉"},
]


# ══════════════════════════════════════════════════════════════════════════════
# Pydantic スキーマ
# ══════════════════════════════════════════════════════════════════════════════

class PromptRequest(BaseModel):
    date:  str = Field(..., description="対象日 YYYY-MM-DD")
    venue: str = Field(..., description="JRA 場コード (例: '08'=京都)")


class PromptResponse(BaseModel):
    prompt_text:   str
    session_label: str
    template:      str
    n_teppan:      int
    n_spice:       int
    n_danger:      int
    total_races:   int


class DraftRequest(BaseModel):
    json_text: str = Field(..., description="LLM が出力した scenes 構造 JSON (生文字列)")


class DraftResponse(BaseModel):
    ok:          bool
    path:        str
    scene_count: int
    total_turns: int
    session:     str


class RenderRequest(BaseModel):
    dry_run: bool = Field(False, description="True = VOICEVOX なしで無音ダミー実行")


class RenderStartResponse(BaseModel):
    job_id: str
    status: Literal["running"]


class RenderStatusResponse(BaseModel):
    job_id:      str
    status:      Literal["running", "done", "error"]
    log:         str
    output_path: str | None = None
    elapsed_sec: float | None = None


# ══════════════════════════════════════════════════════════════════════════════
# ジョブ管理（インメモリ、1 プロセス内で十分）
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class _Job:
    job_id:      str
    status:      Literal["running", "done", "error"] = "running"
    log_lines:   list[str] = field(default_factory=list)
    output_path: str | None = None
    started_at:  float = field(default_factory=time.time)
    finished_at: float | None = None


_jobs: dict[str, _Job] = {}
_jobs_lock = threading.Lock()


def _new_job() -> _Job:
    job = _Job(job_id=str(uuid.uuid4())[:8])
    with _jobs_lock:
        _jobs[job.job_id] = job
    return job


def _append_log(job: _Job, msg: str) -> None:
    with _jobs_lock:
        job.log_lines.append(msg)
    logger.info("[render:%s] %s", job.job_id, msg)


def _finish_job(job: _Job, ok: bool, output_path: str | None = None) -> None:
    with _jobs_lock:
        job.status      = "done" if ok else "error"
        job.output_path = output_path
        job.finished_at = time.time()


# ══════════════════════════════════════════════════════════════════════════════
# Phase 1 — プロンプト生成
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/prompt", response_model=PromptResponse)
def generate_prompt(req: PromptRequest) -> PromptResponse:
    """
    スコア Parquet を読み込み、指定日・会場のプロンプトテキストを返す。
    API 呼び出しは一切行わない。
    """
    if not _SCORES_PARQUET.exists():
        raise HTTPException(
            status_code=404,
            detail=(
                f"スコア Parquet が見つかりません: {_SCORES_PARQUET}\n"
                "先に merge_v2_submodel_scores.py を実行してください。"
            ),
        )

    venue_name = KEIBAJO_LABELS.get(req.venue.strip(), f"会場{req.venue}")
    session_label = f"{req.date} {venue_name}"

    try:
        df_all = pd.read_parquet(_SCORES_PARQUET)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Parquet 読み込み失敗: {e}")

    df_all["_date_str"] = pd.to_datetime(df_all["race_date"]).dt.strftime("%Y-%m-%d")
    mask = (df_all["_date_str"] == req.date) & (
        df_all["keibajo_code"].astype(str).str.strip() == req.venue.strip()
    )
    sess_df = df_all[mask].copy()

    if sess_df.empty:
        raise HTTPException(
            status_code=404,
            detail=f"指定条件のデータが見つかりません: date={req.date}, venue={req.venue}",
        )

    try:
        result = route_session(sess_df, session_label=session_label)
        sp     = build_script_prompt(result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"プロンプト構築失敗: {e}")

    separator = "=" * 72
    prompt_text = "\n".join([
        separator,
        "【SYSTEM PROMPT】",
        separator,
        sp.system_prompt,
        "",
        separator,
        "【USER MESSAGE】",
        separator,
        sp.prompt,
    ])

    return PromptResponse(
        prompt_text=prompt_text,
        session_label=session_label,
        template=sp.template,
        n_teppan=sp.n_teppan,
        n_spice=sp.n_spice,
        n_danger=sp.n_danger,
        total_races=result.total_races,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Phase 2 — 下書き JSON 保存
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/draft", response_model=DraftResponse)
def save_draft(req: DraftRequest) -> DraftResponse:
    """
    LLM が出力した JSON 文字列を検証して data/input/draft_video_data.json に保存する。
    """
    try:
        data = json.loads(req.json_text)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=422, detail=f"JSON パースエラー: {e}")

    scenes = data.get("scenes")
    if not isinstance(scenes, list) or len(scenes) == 0:
        raise HTTPException(
            status_code=422,
            detail="JSON に scenes[] が見つかりません。LLM の出力を確認してください。",
        )

    total_turns = sum(len(s.get("dialogue", [])) for s in scenes)

    _DRAFT_INPUT.parent.mkdir(parents=True, exist_ok=True)
    _DRAFT_INPUT.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("下書き JSON 保存: %s  scenes=%d turns=%d", _DRAFT_INPUT, len(scenes), total_turns)

    return DraftResponse(
        ok=True,
        path=str(_DRAFT_INPUT.relative_to(_ROOT)),
        scene_count=len(scenes),
        total_turns=total_turns,
        session=data.get("session", ""),
    )


# ══════════════════════════════════════════════════════════════════════════════
# Phase 3 — TTS + Remotion レンダリング（非同期バックグラウンド）
# ══════════════════════════════════════════════════════════════════════════════

def _run_render(job: _Job, dry_run: bool) -> None:
    """バックグラウンドスレッドで TTS → JSON コピー → Remotion render を実行する。"""
    try:
        # ── 3a. 入力 JSON 確認 ────────────────────────────────────────────
        if not _DRAFT_INPUT.exists():
            raise FileNotFoundError(f"入力 JSON なし: {_DRAFT_INPUT}")
        _append_log(job, f"入力 JSON: {_DRAFT_INPUT}")

        # ── 3b. VOICEVOX TTS 合成 ────────────────────────────────────────
        _append_log(job, f"TTS 合成開始 (dry_run={dry_run})")
        _TTS_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        process_tts(
            input_path=_DRAFT_INPUT,
            output_path=_TTS_OUTPUT,
            dry_run=dry_run,
        )
        _append_log(job, f"TTS 合成完了 → {_TTS_OUTPUT}")

        # ── 3c. JSON を Remotion public/ へコピー ─────────────────────────
        _REMOTION_PUBLIC.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_TTS_OUTPUT, _REMOTION_PUBLIC)
        _append_log(job, f"JSON コピー完了 → {_REMOTION_PUBLIC}")

        # ── 3d. Remotion レンダリング ─────────────────────────────────────
        _VIDEO_OUT.parent.mkdir(parents=True, exist_ok=True)
        _append_log(job, f"Remotion レンダリング開始: {_COMPOSITION}")

        proc = subprocess.Popen(
            ["npx", "remotion", "render", _COMPOSITION, str(_VIDEO_OUT)],
            cwd=str(_REMOTION_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            _append_log(job, line.rstrip())
        proc.wait()

        if proc.returncode != 0:
            raise RuntimeError(f"Remotion が終了コード {proc.returncode} で失敗しました")

        rel_out = str(_VIDEO_OUT.relative_to(_ROOT))
        _append_log(job, f"レンダリング完了: {rel_out}")
        _finish_job(job, ok=True, output_path=rel_out)

    except Exception as exc:
        _append_log(job, f"[ERROR] {exc}")
        _finish_job(job, ok=False)


@router.post("/render", response_model=RenderStartResponse)
def start_render(req: RenderRequest, background_tasks: BackgroundTasks) -> RenderStartResponse:
    """
    TTS 合成 + Remotion MP4 レンダリングをバックグラウンドで開始する。
    ジョブ ID を即座に返す。進捗は /render/status で確認する。
    """
    if not _DRAFT_INPUT.exists():
        raise HTTPException(
            status_code=404,
            detail=f"入力 JSON がありません: {_DRAFT_INPUT}\nStep 2 で下書き JSON を保存してください。",
        )

    job = _new_job()
    _append_log(job, "ジョブ開始")
    background_tasks.add_task(_run_render, job, req.dry_run)

    return RenderStartResponse(job_id=job.job_id, status="running")


@router.get("/render/status", response_model=RenderStatusResponse)
def render_status(job_id: str) -> RenderStatusResponse:
    """レンダリングジョブの進捗を返す。"""
    with _jobs_lock:
        job = _jobs.get(job_id)

    if job is None:
        raise HTTPException(status_code=404, detail=f"job_id が見つかりません: {job_id}")

    elapsed = (
        (job.finished_at or time.time()) - job.started_at
    )
    return RenderStatusResponse(
        job_id=job.job_id,
        status=job.status,
        log="\n".join(job.log_lines),
        output_path=job.output_path,
        elapsed_sec=round(elapsed, 1),
    )


# ── メタ: 会場リスト取得 ──────────────────────────────────────────────────────

@router.get("/venues")
def get_venues() -> list[dict]:
    """フロントエンドの会場ドロップダウン用。"""
    return VENUE_OPTIONS
