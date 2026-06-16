"""
ml/db.py
=========
fukurou_v2_app の ML バッチ層が使う SQLAlchemy セットアップ。

DB 接続先: shared.config.DB_JVDL (fukurou_jvdl)
"""
from __future__ import annotations

import os
import warnings
from pathlib import Path

import pandas as pd
import psycopg2
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from shared.config import DB_JVDL


def _jvdl_url() -> str:
    cfg = DB_JVDL
    return (
        f"postgresql+psycopg2://{cfg['user']}:{cfg['password']}"
        f"@{cfg['host']}:{cfg['port']}/{cfg['dbname']}"
    )


engine = create_engine(_jvdl_url(), pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    pass


class DBConnector:
    """
    psycopg2 ベースの読み取り専用 DB アクセサ。
    web_service.batch.external_factor_store の DBConnector 互換 shim。
    """

    def __init__(self) -> None:
        self._conn: psycopg2.extensions.connection | None = None

    def connect(self) -> None:
        self._conn = psycopg2.connect(**DB_JVDL)

    def disconnect(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def fetch_data(self, query: str) -> pd.DataFrame:
        if not self._conn or self._conn.closed:
            self.connect()
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="pandas only supports SQLAlchemy connectable",
                category=UserWarning,
            )
            return pd.read_sql_query(query, self._conn)

    def execute_query(self, query: str) -> None:
        if not self._conn or self._conn.closed:
            self.connect()
        with self._conn.cursor() as cur:
            cur.execute(query)
        self._conn.commit()
