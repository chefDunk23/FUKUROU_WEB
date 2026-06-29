"""
api_v1/routers/data.py
========================
データ管理エンドポイント。

  GET  /api/v1/data/status                  — Parquet / DB の状態確認
  POST /api/v1/data/fetch-races             — RACE取得ジョブ開始（step 1）
  POST /api/v1/data/full-update             — 月曜フル更新ジョブ開始（stage 3）
  GET  /api/v1/data/update-job/{job_id}     — 更新ジョブの進捗確認
  POST /api/v1/data/rebuild-parquet         — V2 Parquet 再生成
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api_v1.services.data_manager import (
    fetch_races,
    full_update,
    get_db_info,
    get_parquet_info,
    read_update_job,
    rebuild_parquet,
)

router = APIRouter(prefix="/api/v1/data", tags=["data-management"])


@router.get("/status", summary="Parquet / DB の現在の状態を返す")
def data_status():
    return {
        "parquet": get_parquet_info(),
        "db":      get_db_info(),
    }


@router.post("/fetch-races", summary="RACE データを jvdl に取り込む（step 1）")
async def trigger_fetch_races():
    """
    AI_FUKUROU_KEIBA_Ver2 の step 1 を実行し JRA-VAN から RACE データを取得して
    fukurou_jvdl に書き込む。所要時間: 5〜15 分。

    レース予想画面でレース一覧が空の場合の自動トリガーとして使用。
    """
    return await fetch_races()


@router.post("/full-update", summary="月曜フル更新（stage 3）")
async def trigger_full_update():
    """
    DIFN + RACE + MING データを取得し DB 構築・特徴量抽出・モデル再学習まで実行する。
    月曜 14:05 以降に手動実行。所要時間: 30〜60 分。
    """
    return await full_update()


@router.get("/update-job/{job_id}", summary="更新ジョブの進捗確認")
def get_update_job(job_id: str):
    state = read_update_job(job_id)
    if not state:
        raise HTTPException(404, detail=f"ジョブ '{job_id}' が見つかりません")
    return state


@router.post("/rebuild-parquet", summary="V2 Parquet を再生成する")
async def trigger_rebuild_parquet():
    """merge_v2_submodel_scores.py を実行して v2_stacked_features.parquet を再生成する。"""
    return await rebuild_parquet()
