"""
shared/db/jvdata.py
====================
fukurou_keiba_v2 DB（JV-Data ETL）への接続ヘルパー。

使い方:
    from shared.db.jvdata import get_conn, query_df

    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT ...")
            rows = cur.fetchall()

    # または DataFrame で受け取る
    df = query_df("SELECT * FROM races WHERE race_date = %s", ("2024-06-01",))
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pandas as pd
import psycopg2
import psycopg2.extras

from shared.config import DB_V2


@contextmanager
def get_conn():
    """V2 DB への接続コンテキストマネージャー。with ブロック終了時に自動 close。"""
    conn = psycopg2.connect(**DB_V2)
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
