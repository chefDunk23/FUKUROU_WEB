"""
shared/db/jvdata.py
====================
fukurou_keiba_v2 DB（JV-Data ETL）への接続ヘルパー。
ThreadedConnectionPool によるプール管理（接続を都度 close しない）。

使い方:
    from shared.db.jvdata import get_conn, query_df

    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT ...")
            rows = cur.fetchall()
"""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from typing import Any

import pandas as pd
import psycopg2
import psycopg2.extras
import psycopg2.pool

from shared.config import DB_V2

_pool: psycopg2.pool.ThreadedConnectionPool | None = None
_pool_lock = threading.Lock()

_POOL_MIN = int(os.getenv("DB_V2_POOL_MIN", "1"))
_POOL_MAX = int(os.getenv("DB_V2_POOL_MAX", "8"))


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is None:
            _pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=_POOL_MIN,
                maxconn=_POOL_MAX,
                **DB_V2,
            )
    return _pool


@contextmanager
def get_conn():
    """V2 DB への接続コンテキストマネージャー（プールから貸し出し）。

    正常終了: rollback() してプールに返却（idle-in-transaction 防止）。
    例外終了: 壊れた接続は close=True でプールに戻さず破棄し、例外を再送出。
    """
    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
    except Exception:
        pool.putconn(conn, close=True)
        raise
    else:
        try:
            conn.rollback()
        except Exception:
            pool.putconn(conn, close=True)
            return
        pool.putconn(conn)


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
