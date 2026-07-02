"""
scripts/check_migrations.py
============================
deploy.md の DDL ファイルと実 DB テーブルを突き合わせて未適用 DDL を警告する。

ワーカー起動時スニペット（shared/worker/job_runner.py から呼び出す想定）:
    from scripts.check_migrations import check_migrations
    check_migrations()

コマンドラインでも使用可能:
    py scripts/check_migrations.py
"""
from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logger = logging.getLogger(__name__)

_SCRIPTS_DIR = Path(__file__).parent
_MIGRATE_GLOB = "migrate_*.sql"

# 各 SQL ファイルで作成されるテーブルが属する DB
_SQL_TO_DB: dict[str, str] = {
    "migrate_v2_jvdl_tables.sql":   "fukurou_jvdl",
    "migrate_add_predictions.sql":  "fukurou_jvdl",
    "migrate_add_detail_cache.sql": "fukurou_jvdl",
    "migrate_add_jobs_table.sql":   "fukurou_jvdl",
    "migrate_v2_nar_policy.sql":    "fukurou_jvdl",
    "migrate_add_video_tables.sql": "fukurou_jvdl",
}


def _tables_from_sql(sql_path: Path) -> list[str]:
    sql = sql_path.read_text(encoding="utf-8")
    return re.findall(r"CREATE TABLE IF NOT EXISTS\s+(\w+)", sql, re.IGNORECASE)


def _existing_tables(dbname: str) -> set[str]:
    try:
        import psycopg2
        from shared.config import DB_JVDL

        conn = psycopg2.connect(**{**DB_JVDL, "dbname": dbname})
        cur = conn.cursor()
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public'"
        )
        result = {r[0] for r in cur.fetchall()}
        conn.close()
        return result
    except Exception as exc:
        logger.warning("[check_migrations] DB 接続失敗 (%s): %s", dbname, exc)
        return set()


def check_migrations(warn_only: bool = True) -> list[str]:
    """未適用 DDL の不足テーブル一覧を返す。warn_only=True ならログ警告のみ（例外なし）。"""
    missing: list[str] = []

    db_table_cache: dict[str, set[str]] = {}

    for sql_file in sorted(_SCRIPTS_DIR.glob(_MIGRATE_GLOB)):
        fname = sql_file.name
        dbname = _SQL_TO_DB.get(fname, "fukurou_jvdl")
        expected_tables = _tables_from_sql(sql_file)
        if not expected_tables:
            continue

        if dbname not in db_table_cache:
            db_table_cache[dbname] = _existing_tables(dbname)
        existing = db_table_cache[dbname]

        for tbl in expected_tables:
            if tbl not in existing:
                msg = f"[未適用 DDL] {fname} が作成する '{tbl}' が {dbname} に存在しません"
                missing.append(msg)
                logger.warning(msg)

    if not missing:
        logger.info("[check_migrations] 全 DDL 適用済み (%d ファイル確認)", len(list(_SCRIPTS_DIR.glob(_MIGRATE_GLOB))))
    elif not warn_only:
        raise RuntimeError("未適用 DDL が検出されました:\n" + "\n".join(missing))

    return missing


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )
    result = check_migrations()
    if result:
        print("\n未適用 DDL 一覧:")
        for m in result:
            print(f"  {m}")
        sys.exit(1)
    else:
        print("OK: 全 DDL 適用済み")
        sys.exit(0)
