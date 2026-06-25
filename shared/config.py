"""
shared/config.py
================
全 API（v1 / v2）が参照する共通設定。
値は必ず環境変数から読み込む。.env ファイルを自動ロードする。

使い方:
    from shared.config import DB_V2, DB_JVDL, PATHS
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# プロジェクトルートの .env を自動ロード（存在しない場合は何もしない）
_ROOT = Path(__file__).parent.parent
load_dotenv(_ROOT / ".env")


# ── DB: JV-Data ETL（fukurou_keiba_v2）──────────────────────────────────────
DB_V2: dict = {
    "host":     os.getenv("DB_V2_HOST",     "localhost"),
    "port":     int(os.getenv("DB_V2_PORT", "5432")),
    "dbname":   os.getenv("DB_V2_NAME",     "fukurou_keiba_v2"),
    "user":     os.getenv("DB_V2_USER",     "postgres"),
    "password": os.getenv("DB_V2_PASSWORD", ""),
}

# ── DB: Feature Store（fukurou_jvdl）────────────────────────────────────────
DB_JVDL: dict = {
    "host":     os.getenv("DB_JVDL_HOST",     "localhost"),
    "port":     int(os.getenv("DB_JVDL_PORT", "5432")),
    "dbname":   os.getenv("DB_JVDL_NAME",     "fukurou_jvdl"),
    "user":     os.getenv("DB_JVDL_USER",     "postgres"),
    "password": os.getenv("DB_JVDL_PASSWORD", ""),
}


# ── ファイルパス ─────────────────────────────────────────────────────────────
class _Paths:
    """プロジェクトルート基準のパス定数。環境変数で上書き可能。"""

    root: Path = _ROOT

    @property
    def model_dir_v2(self) -> Path:
        return _ROOT / os.getenv("MODEL_DIR_V2", "models/v2/ensemble")

    @property
    def model_dir_v2_dirt(self) -> Path:
        return _ROOT / os.getenv("MODEL_DIR_V2_DIRT", "models/v2/ensemble_dirt")

    @property
    def submodel_dir_v2(self) -> Path:
        return _ROOT / os.getenv("SUBMODEL_DIR_V2", "models/v2/submodels")

    @property
    def master_dir(self) -> Path:
        return _ROOT / os.getenv("MASTER_DIR", "data/masters")

    @property
    def course_master_csv(self) -> Path:
        return self.master_dir / "course_physical_master.csv"


PATHS = _Paths()


# ── Redis ────────────────────────────────────────────────────────────────────
REDIS_HOST: str = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT: int = int(os.getenv("REDIS_PORT", "6379"))

# ── API 認証 ─────────────────────────────────────────────────────────────────
# 空文字 = 開発環境（認証スキップ）。本番では必ず設定すること。
API_KEY: str = os.getenv("API_KEY", "")

# ── データ分割境界（学習 / 検証）────────────────────────────────────────────
# 学習データ: ~ TRAIN_END_DATE (含む)
# 検証データ: EVAL_START_DATE ~ (含む)
# tipster/backtest.py および学習スクリプト (scripts/train_v2_*.py) はこの定数を参照すること。
# ランダムシャッフル分割は禁止。時系列順の分割のみ許可。
TRAIN_END_DATE: str = os.getenv("TRAIN_END_DATE", "2025-05-31")
EVAL_START_DATE: str = os.getenv("EVAL_START_DATE", "2025-06-01")

# ── 機能フラグ ───────────────────────────────────────────────────────────────
DEV_MODE: bool = os.getenv("DEV_MODE", "false").lower() == "true"

PORT_V1: int = int(os.getenv("PORT_V1", "8001"))
PORT_V2: int = int(os.getenv("PORT_V2", "8002"))
