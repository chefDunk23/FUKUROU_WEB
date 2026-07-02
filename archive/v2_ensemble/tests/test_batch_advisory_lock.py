"""
tests/test_batch_advisory_lock.py
==================================
K-1 実証テスト: バッチ多重起動防止（pg_try_advisory_lock）の検証。

前提:
    - fukurou_jvdl DB に接続できること（環境変数 DB_JVDL_* が設定済み）
    - psycopg2 がインストール済みであること

テスト方針:
    実際に DB 接続してセッションレベル advisory lock を事前保持し、
    _run_batch() がロック取得失敗時に即 return 0 することを確認する。
    DB 接続不可の場合はスキップ（CI 環境でも安全）。
"""
from __future__ import annotations

import logging
import threading
from unittest.mock import patch

import pytest

_SKIP_REASON = ""

try:
    import psycopg2
    from shared.config import DB_JVDL
    psycopg2.connect(**DB_JVDL).close()
except Exception as e:
    _SKIP_REASON = f"DB 未接続のためスキップ: {e}"


@pytest.mark.skipif(bool(_SKIP_REASON), reason=_SKIP_REASON or "DB unavailable")
def test_advisory_lock_skip_when_held(caplog):
    """外部からロックを先取りしている場合、_run_batch() が 0 を返してスキップログを出すこと。"""
    from api_v2.services.batch_predictor import _BATCH_LOCK_KEY, _run_batch

    # ── ロックを先に取得する専用接続 ─────────────────────────────────────
    holder_conn = psycopg2.connect(**DB_JVDL)
    holder_conn.autocommit = True
    with holder_conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (_BATCH_LOCK_KEY,))
        held = cur.fetchone()[0]
    assert held, "テスト用ロック取得に失敗（他テストが保持中の可能性）"

    try:
        # ── _run_batch() を呼び出す（ロック取得不可 → スキップするはず）────
        with caplog.at_level(logging.INFO, logger="api_v2.services.batch_predictor"):
            result = _run_batch(["dummy_race_id"], "test")

        saved, failed_cnt, skipped = result
        assert saved == 0 and failed_cnt == 0 and skipped == 0, (
            f"スキップ時の戻り値は (0, 0, 0) であるべきだが {result} が返った"
        )
        assert any("別ワーカーが実行中のためスキップ" in r.message for r in caplog.records), (
            "スキップログが出力されていない"
        )
    finally:
        # ロック解放
        with holder_conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (_BATCH_LOCK_KEY,))
        holder_conn.close()


@pytest.mark.skipif(bool(_SKIP_REASON), reason=_SKIP_REASON or "DB unavailable")
def test_advisory_lock_acquired_and_released(caplog):
    """ロックが空き状態なら取得され、処理後に解放されること（後続が取得可能になる）。"""
    from api_v2.services.batch_predictor import _BATCH_LOCK_KEY, _run_batch

    # ── _run_batch() 内の実計算は全部 mock して即 return させる ────────────
    with (
        patch("api_v2.routers.prediction._compute_prediction", return_value=None),
        patch("api_v2.routers.prediction._save_prediction_cache"),
        patch("api_v2.routers.races._compute_detail", return_value=None),
        patch("api_v2.routers.races._save_detail_cache"),
        patch("shared.services.model_version.get_model_version", return_value="test_ver"),
    ):
        result = _run_batch([], "test_empty")

    saved, _failed, _skipped = result
    assert saved == 0

    # _run_batch 終了後にロックが解放されているか確認
    check_conn = psycopg2.connect(**DB_JVDL)
    check_conn.autocommit = True
    try:
        with check_conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (_BATCH_LOCK_KEY,))
            acquirable = cur.fetchone()[0]
            if acquirable:
                cur.execute("SELECT pg_advisory_unlock(%s)", (_BATCH_LOCK_KEY,))
    finally:
        check_conn.close()

    assert acquirable, "バッチ終了後に advisory lock が解放されていない"
