"""
api_v1/services/data_manager.py
================================
Parquet / DB の状態確認・更新ジョブ管理。

パイプライン実行フロー:
  fetch_races()    — AI_FUKUROU_KEIBA_Ver2 step 1 (RACE取得 → jvdl書き込み)
  full_update()    — AI_FUKUROU_KEIBA_Ver2 stage 3 (月曜フル: 結果取得・再学習)
  rebuild_parquet() — scripts/merge_v2_submodel_scores.py (V2 Parquet 再生成)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

_PY      = sys.executable
_ROOT    = Path(__file__).parent.parent.parent
_SCRIPTS = _ROOT / "scripts"
_OUTPUTS = _ROOT / "outputs"
_JOBS    = _ROOT / "data" / "jobs"

_PARQUET_PATH = _OUTPUTS / "v2_stacked_features.parquet"

# AI_FUKUROU_KEIBA_Ver2 のパス（環境変数で上書き可能）
_PIPELINE_ROOT = Path(
    os.getenv("AI_KEIBA_PIPELINE_DIR", r"C:\workspace\AI_FUKUROU_KEIBA_Ver2")
)

_JOBS.mkdir(parents=True, exist_ok=True)


def _env() -> dict:
    return {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1"}


# ── 更新ジョブ状態管理 ────────────────────────────────────────────────────────

def _job_path(job_id: str) -> Path:
    return _JOBS / f"update_{job_id}.json"


def _write_job(job_id: str, state: dict) -> None:
    _job_path(job_id).write_text(
        json.dumps(state, ensure_ascii=False), encoding="utf-8"
    )


def read_update_job(job_id: str) -> dict:
    p = _job_path(job_id)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _new_update_job(job_type: str) -> str:
    job_id = uuid.uuid4().hex[:12]
    _write_job(job_id, {
        "type":        job_type,
        "status":      "running",
        "started_at":  datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "finished_at": None,
        "log":         "",
        "error":       None,
    })
    return job_id


def _finish_job(job_id: str, success: bool, log_text: str, error: str | None = None) -> None:
    state = read_update_job(job_id)
    state.update({
        "status":      "done" if success else "error",
        "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "log":         log_text[-4000:],  # 末尾 4000 文字のみ保持
        "error":       error,
    })
    _write_job(job_id, state)


# ── DB / Parquet 状態確認 ──────────────────────────────────────────────────────

def get_parquet_info() -> dict:
    """V2 Parquet の存在確認・日付範囲・行数を返す。"""
    if not _PARQUET_PATH.exists():
        return {"exists": False, "path": str(_PARQUET_PATH)}
    try:
        df = pd.read_parquet(_PARQUET_PATH, columns=["race_date"])
        dates = pd.to_datetime(df["race_date"])
        return {
            "exists":     True,
            "rows":       len(df),
            "date_min":   dates.min().strftime("%Y-%m-%d"),
            "date_max":   dates.max().strftime("%Y-%m-%d"),
            "updated_at": datetime.fromtimestamp(
                _PARQUET_PATH.stat().st_mtime
            ).strftime("%Y-%m-%d %H:%M"),
        }
    except Exception as exc:
        log.warning("Parquet 読み込みエラー: %s", exc)
        return {"exists": True, "error": str(exc)}


def get_db_info() -> dict:
    """fukurou_jvdl の最新 JRA レース日を返す。"""
    try:
        from shared.db.jvdl import query_df
        df = query_df(
            "SELECT MAX(date::date) AS max_date FROM races "
            "WHERE place_code BETWEEN '01' AND '10'"
        )
        max_date = df["max_date"].iloc[0] if not df.empty else None
        return {
            "connected":     True,
            "db_name":       "fukurou_jvdl",
            "max_race_date": str(max_date)[:10] if max_date else None,
        }
    except Exception as exc:
        return {"connected": False, "db_name": "fukurou_jvdl", "error": str(exc)}


# ── パイプライン実行（バックグラウンドジョブ）────────────────────────────────

def _run_pipeline_sync(job_id: str, args: list[str]) -> None:
    """AI_FUKUROU_KEIBA_Ver2/run_pipeline.py を同期実行してジョブ状態を更新する。"""
    if not _PIPELINE_ROOT.exists():
        _finish_job(job_id, False, "",
                    f"パイプラインディレクトリが見つかりません: {_PIPELINE_ROOT}\n"
                    "環境変数 AI_KEIBA_PIPELINE_DIR を設定してください。")
        return

    cmd = [_PY, str(_PIPELINE_ROOT / "run_pipeline.py")] + args
    env = {
        **_env(),
        "PYTHONPATH": str(_PIPELINE_ROOT),
    }
    log.info("パイプライン実行: %s (cwd=%s)", " ".join(cmd), _PIPELINE_ROOT)

    buf: list[str] = []
    try:
        with subprocess.Popen(
            cmd,
            cwd=str(_PIPELINE_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        ) as proc:
            for line in proc.stdout:  # type: ignore[union-attr]
                buf.append(line)
                # 定期的に job ファイルにログを書き込む（進捗確認用）
                if len(buf) % 20 == 0:
                    state = read_update_job(job_id)
                    state["log"] = "".join(buf)[-4000:]
                    _write_job(job_id, state)
            proc.wait()

        output = "".join(buf)
        if proc.returncode != 0:
            log.error("パイプライン失敗 (code %d)", proc.returncode)
            _finish_job(job_id, False, output,
                        f"終了コード {proc.returncode}")
        else:
            log.info("パイプライン完了")
            _finish_job(job_id, True, output)

    except Exception as exc:
        log.exception("パイプライン実行例外: %s", exc)
        _finish_job(job_id, False, "".join(buf), str(exc))


async def fetch_races() -> dict:
    """
    RACE データを jvdl に取り込む（step 1 のみ）。
    今週末の出走確定レースが jvdl にない場合の自動トリガー用。
    所要時間: 5〜15 分。
    """
    job_id = _new_update_job("fetch_races")
    log.info("fetch_races ジョブ開始: %s", job_id)
    asyncio.get_event_loop().run_in_executor(
        None, _run_pipeline_sync, job_id, ["--step", "1"]
    )
    return {"job_id": job_id, "message": "RACE データ取得を開始しました"}


async def full_update() -> dict:
    """
    月曜フル更新: stage 3（DIFN+RACE+MING 取得 → DB 構築 → 特徴量抽出 → 再学習）。
    所要時間: 30〜60 分。
    """
    job_id = _new_update_job("full_update")
    log.info("full_update ジョブ開始: %s", job_id)
    asyncio.get_event_loop().run_in_executor(
        None, _run_pipeline_sync, job_id, ["--stage", "3"]
    )
    return {"job_id": job_id, "message": "月曜フル更新を開始しました"}


# ── Parquet 再生成 ─────────────────────────────────────────────────────────────

async def rebuild_parquet() -> dict:
    """OOF スコアをマージして v2_stacked_features.parquet を再生成する。"""
    oof_path = _ROOT / "models" / "v2" / "submodels" / "oof_scores_v2.parquet"
    if not oof_path.exists():
        return {
            "success": False,
            "error":   f"OOF スコアが見つかりません: {oof_path}\n"
                       "先に scripts/train_v2_submodels.py を実行してください。",
        }

    def _run() -> subprocess.CompletedProcess:
        return subprocess.run(
            [_PY, str(_SCRIPTS / "merge_v2_submodel_scores.py")],
            cwd=str(_ROOT),
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", env=_env(),
        )

    result = await asyncio.to_thread(_run)
    output = result.stdout + result.stderr
    if result.returncode != 0:
        log.error("Parquet 再生成失敗 (code %d)", result.returncode)
        return {"success": False, "error": output or f"終了コード {result.returncode}"}

    info = get_parquet_info()
    log.info("Parquet 再生成完了: %s", info)
    return {"success": True, "parquet": info, "log": output}
