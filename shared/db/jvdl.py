"""
shared/db/jvdl.py
==================
fukurou_jvdl DB（Feature Store）への接続ヘルパー。

使い方:
    from shared.db.jvdl import get_conn, query_df
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pandas as pd
import psycopg2
import psycopg2.extras

from shared.config import DB_JVDL


@contextmanager
def get_conn():
    """jvdl DB への接続コンテキストマネージャー。"""
    conn = psycopg2.connect(**DB_JVDL)
    try:
        yield conn
    finally:
        conn.close()


def query_df(sql: str, params: tuple[Any, ...] | None = None) -> pd.DataFrame:
    """SQL を実行して DataFrame を返す。"""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    return pd.DataFrame(rows)


def query_one(sql: str, params: tuple[Any, ...] | None = None) -> dict | None:
    """SQL を実行して最初の1行を dict で返す（0件は None）。"""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
    return dict(row) if row else None
