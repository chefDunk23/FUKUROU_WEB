"""
api_v2/routers/lab.py
======================
条件ラボ API ルーター。

エンドポイント:
  GET    /api/v2/lab/conditions               - 全条件一覧（組み込み + カスタム）
  POST   /api/v2/lab/conditions               - カスタム条件作成
  PUT    /api/v2/lab/conditions/{id}          - カスタム条件編集
  DELETE /api/v2/lab/conditions/{id}          - カスタム条件削除
  GET    /api/v2/lab/condition-sets           - 条件セット一覧
  POST   /api/v2/lab/condition-sets           - 条件セット作成
  PUT    /api/v2/lab/condition-sets/{id}      - 条件セット編集
  DELETE /api/v2/lab/condition-sets/{id}      - 条件セット削除
  POST   /api/v2/lab/backtest                 - バックテスト実行開始
  GET    /api/v2/lab/backtest/result/{job_id} - バックテスト結果取得
  POST   /api/v2/lab/backtest/compare         - 比較バックテスト実行開始

データ永続化: data/lab/conditions.json
バックテストジョブ: インメモリ辞書（プロセス再起動でリセット）
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from tipster.lab_adapter import BUILTIN_CONDITIONS, BUILTIN_IDS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2/lab", tags=["lab"])

_DATA_FILE = Path(__file__).parent.parent.parent / "data" / "lab" / "conditions.json"

# ─────────────────────────────────────────────────────────────────────────
# データ永続化ヘルパー
# ─────────────────────────────────────────────────────────────────────────


def _load_data() -> dict[str, list]:
    if not _DATA_FILE.exists():
        return {"custom_conditions": [], "condition_sets": []}
    return json.loads(_DATA_FILE.read_text(encoding="utf-8"))


def _save_data(data: dict[str, list]) -> None:
    _DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    _DATA_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────────────────
# Pydantic スキーマ
# ─────────────────────────────────────────────────────────────────────────


class CreateConditionRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    description: str = ""
    base_condition_id: str
    type: str = "scoring"
    params: dict[str, Any] = Field(default_factory=dict)


class UpdateConditionRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    type: str | None = None
    params: dict[str, Any] | None = None


class ConditionEntry(BaseModel):
    condition_id: str
    mode: str = "scoring"
    enabled: bool = True
    params: dict[str, Any] = Field(default_factory=dict)


class RankingConfig(BaseModel):
    primary: str = "condition_clear_count"
    secondary: str = "ai_score"
    max_selections: int = 3


class CreateConditionSetRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    description: str = ""
    conditions: list[ConditionEntry] = Field(default_factory=list)
    ranking: RankingConfig = Field(default_factory=RankingConfig)


class UpdateConditionSetRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    conditions: list[ConditionEntry] | None = None
    ranking: RankingConfig | None = None


class BacktestRequest(BaseModel):
    condition_set_id: str
    aite_strategy: str = "anaba_v5"
    periods: list[str] = Field(default_factory=lambda: ["3m", "6m", "1y"])
    grade_filter: list[str] | None = None
    distance_filter: list[str] | None = None


class CompareBacktestRequest(BaseModel):
    condition_set_id_a: str
    condition_set_id_b: str
    aite_strategy: str = "anaba_v5"
    periods: list[str] = Field(default_factory=lambda: ["3m"])


# ─────────────────────────────────────────────────────────────────────────
# バックテストジョブ管理（インメモリ）
# ─────────────────────────────────────────────────────────────────────────

_jobs: dict[str, dict[str, Any]] = {}


def _new_job(job_type: str = "single") -> str:
    job_id = uuid.uuid4().hex
    _jobs[job_id] = {"status": "pending", "type": job_type, "result": None, "error": None}
    return job_id


def _job_running(job_id: str, result: Any) -> None:
    if job_id in _jobs:
        _jobs[job_id]["status"] = "running"


def _job_done(job_id: str, result: Any) -> None:
    if job_id in _jobs:
        _jobs[job_id]["status"] = "done"
        _jobs[job_id]["result"] = result


def _job_failed(job_id: str, error: str) -> None:
    if job_id in _jobs:
        _jobs[job_id]["status"] = "error"
        _jobs[job_id]["error"] = error


# ─────────────────────────────────────────────────────────────────────────
# 条件エンドポイント
# ─────────────────────────────────────────────────────────────────────────


@router.get("/conditions")
def list_conditions() -> dict:
    """全条件一覧を返す（組み込み + カスタム）。"""
    data = _load_data()
    return {
        "builtin": BUILTIN_CONDITIONS,
        "custom": data["custom_conditions"],
    }


@router.post("/conditions", status_code=201)
def create_condition(req: CreateConditionRequest) -> dict:
    """カスタム条件を新規作成する。"""
    if req.base_condition_id not in BUILTIN_IDS:
        raise HTTPException(
            status_code=400,
            detail=f"base_condition_id '{req.base_condition_id}' は組み込み条件に存在しません",
        )
    data = _load_data()
    new_cond = {
        "id": f"custom_{uuid.uuid4().hex[:8]}",
        "name": req.name,
        "description": req.description,
        "base_condition_id": req.base_condition_id,
        "type": req.type,
        "params": req.params,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    data["custom_conditions"].append(new_cond)
    _save_data(data)
    return new_cond


@router.put("/conditions/{condition_id}")
def update_condition(condition_id: str, req: UpdateConditionRequest) -> dict:
    """カスタム条件を編集する（組み込み条件は変更不可）。"""
    if condition_id in BUILTIN_IDS:
        raise HTTPException(status_code=403, detail="組み込み条件は変更できません")
    data = _load_data()
    target = next((c for c in data["custom_conditions"] if c["id"] == condition_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail=f"条件 '{condition_id}' が見つかりません")

    if req.name is not None:
        target["name"] = req.name
    if req.description is not None:
        target["description"] = req.description
    if req.type is not None:
        target["type"] = req.type
    if req.params is not None:
        target["params"] = req.params
    target["updated_at"] = _now_iso()
    _save_data(data)
    return target


@router.delete("/conditions/{condition_id}", status_code=204)
def delete_condition(condition_id: str) -> None:
    """カスタム条件を削除する（組み込み条件は削除不可）。"""
    if condition_id in BUILTIN_IDS:
        raise HTTPException(status_code=403, detail="組み込み条件は削除できません")
    data = _load_data()
    before = len(data["custom_conditions"])
    data["custom_conditions"] = [c for c in data["custom_conditions"] if c["id"] != condition_id]
    if len(data["custom_conditions"]) == before:
        raise HTTPException(status_code=404, detail=f"条件 '{condition_id}' が見つかりません")
    _save_data(data)


# ─────────────────────────────────────────────────────────────────────────
# 条件セットエンドポイント
# ─────────────────────────────────────────────────────────────────────────


@router.get("/condition-sets")
def list_condition_sets() -> dict:
    """条件セット一覧を返す。"""
    data = _load_data()
    return {"condition_sets": data["condition_sets"]}


@router.post("/condition-sets", status_code=201)
def create_condition_set(req: CreateConditionSetRequest) -> dict:
    """条件セットを新規作成する。"""
    data = _load_data()
    new_set = {
        "id": f"set_{uuid.uuid4().hex[:8]}",
        "name": req.name,
        "description": req.description,
        "conditions": [c.model_dump() for c in req.conditions],
        "ranking": req.ranking.model_dump(),
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    data["condition_sets"].append(new_set)
    _save_data(data)
    return new_set


@router.put("/condition-sets/{set_id}")
def update_condition_set(set_id: str, req: UpdateConditionSetRequest) -> dict:
    """条件セットを編集する。"""
    data = _load_data()
    target = next((s for s in data["condition_sets"] if s["id"] == set_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail=f"条件セット '{set_id}' が見つかりません")

    if req.name is not None:
        target["name"] = req.name
    if req.description is not None:
        target["description"] = req.description
    if req.conditions is not None:
        target["conditions"] = [c.model_dump() for c in req.conditions]
    if req.ranking is not None:
        target["ranking"] = req.ranking.model_dump()
    target["updated_at"] = _now_iso()
    _save_data(data)
    return target


@router.delete("/condition-sets/{set_id}", status_code=204)
def delete_condition_set(set_id: str) -> None:
    """条件セットを削除する。"""
    data = _load_data()
    before = len(data["condition_sets"])
    data["condition_sets"] = [s for s in data["condition_sets"] if s["id"] != set_id]
    if len(data["condition_sets"]) == before:
        raise HTTPException(status_code=404, detail=f"条件セット '{set_id}' が見つかりません")
    _save_data(data)


# ─────────────────────────────────────────────────────────────────────────
# バックテストエンドポイント
# ─────────────────────────────────────────────────────────────────────────


def _find_condition_set(set_id: str) -> dict:
    data = _load_data()
    target = next((s for s in data["condition_sets"] if s["id"] == set_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail=f"条件セット '{set_id}' が見つかりません")
    return target


def _run_backtest_job(job_id: str, honmei_set: dict, aite_strategy: str,
                      periods: list[str], grade_filter: list[str] | None,
                      distance_filter: list[str] | None) -> None:
    """バックグラウンドスレッドで実行するバックテスト処理。"""
    from tipster.lab_adapter import run_lab_backtest

    _job_running(job_id, None)
    try:
        result = run_lab_backtest(
            honmei_set=honmei_set,
            aite_strategy_name=aite_strategy,
            periods=periods,
            grade_filter=grade_filter,
            distance_filter=distance_filter,
        )
        _job_done(job_id, {"type": "single", "results": result})
        logger.info("バックテストジョブ %s 完了", job_id)
    except Exception as exc:
        logger.exception("バックテストジョブ %s 失敗", job_id)
        _job_failed(job_id, str(exc))


def _run_compare_job(job_id: str, set_a: dict, set_b: dict, aite_strategy: str,
                     periods: list[str]) -> None:
    """比較バックテストのバックグラウンド処理。"""
    from tipster.lab_adapter import run_lab_backtest

    _job_running(job_id, None)
    try:
        result_a = run_lab_backtest(set_a, aite_strategy, periods)
        result_b = run_lab_backtest(set_b, aite_strategy, periods)
        _job_done(job_id, {
            "type": "compare",
            "set_a": {"id": set_a["id"], "name": set_a["name"], "results": result_a},
            "set_b": {"id": set_b["id"], "name": set_b["name"], "results": result_b},
        })
        logger.info("比較バックテストジョブ %s 完了", job_id)
    except Exception as exc:
        logger.exception("比較バックテストジョブ %s 失敗", job_id)
        _job_failed(job_id, str(exc))


@router.post("/backtest", status_code=202)
def start_backtest(req: BacktestRequest, bg: BackgroundTasks) -> dict:
    """バックテストをバックグラウンドで開始する。job_id を返す。"""
    honmei_set = _find_condition_set(req.condition_set_id)
    job_id = _new_job("single")
    bg.add_task(
        _run_backtest_job,
        job_id, honmei_set, req.aite_strategy,
        req.periods, req.grade_filter, req.distance_filter,
    )
    logger.info("バックテストジョブ開始: %s (set=%s)", job_id, req.condition_set_id)
    return {"job_id": job_id, "status": "pending"}


@router.post("/backtest/compare", status_code=202)
def start_compare_backtest(req: CompareBacktestRequest, bg: BackgroundTasks) -> dict:
    """比較バックテストをバックグラウンドで開始する。"""
    set_a = _find_condition_set(req.condition_set_id_a)
    set_b = _find_condition_set(req.condition_set_id_b)
    job_id = _new_job("compare")
    bg.add_task(_run_compare_job, job_id, set_a, set_b, req.aite_strategy, req.periods)
    return {"job_id": job_id, "status": "pending"}


@router.get("/backtest/result/{job_id}")
def get_backtest_result(job_id: str) -> dict:
    """バックテストジョブの状態と結果を返す。"""
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"ジョブ '{job_id}' が見つかりません")
    return job
