"""
tests/test_tipster_engine.py
==============================
tipster/engine.py の統合テスト。
race_detail_cache に実データが必要な検証は、DB未接続/対象レース未キャッシュの場合スキップする。
"""
from __future__ import annotations

import pytest

from tipster.engine import evaluate_race, fetch_race_context, load_strategy

_SKIP_REASON = ""
_TEST_RACE_ID = "202606140911"

try:
    from ml.db import engine as _engine
    from sqlalchemy import text as _text

    with _engine.connect() as _conn:
        _row = _conn.execute(
            _text("SELECT 1 FROM race_detail_cache WHERE race_id = :rid"),
            {"rid": _TEST_RACE_ID},
        ).fetchone()
    if _row is None:
        _SKIP_REASON = f"テスト用race_id={_TEST_RACE_ID} のキャッシュが存在しないためスキップ"
except Exception as e:
    _SKIP_REASON = f"DB 未接続のためスキップ: {e}"


def test_load_strategy_honmei_v1():
    strategy = load_strategy("honmei_v1")
    assert strategy.tipster == "fukurou"
    assert strategy.type == "honmei"
    assert len(strategy.conditions) == 5


def test_load_strategy_anaba_v1():
    strategy = load_strategy("anaba_v1")
    assert strategy.type == "anaba"
    assert strategy.ranking.max_selections == 5


@pytest.mark.skipif(bool(_SKIP_REASON), reason=_SKIP_REASON or "DB unavailable")
def test_fetch_race_context_returns_horses():
    ctx = fetch_race_context(_TEST_RACE_ID)
    assert ctx.race_id == _TEST_RACE_ID
    assert len(ctx.horses) > 0


@pytest.mark.skipif(bool(_SKIP_REASON), reason=_SKIP_REASON or "DB unavailable")
def test_evaluate_race_honmei_v1():
    evaluation = evaluate_race(_TEST_RACE_ID, "honmei_v1")
    assert evaluation.race_id == _TEST_RACE_ID
    assert len(evaluation.candidates) <= 3
    assert evaluation.eliminated_count == len(evaluation.eliminated_horses)


@pytest.mark.skipif(bool(_SKIP_REASON), reason=_SKIP_REASON or "DB unavailable")
def test_evaluate_race_anaba_v1_uses_different_ranking():
    evaluation = evaluate_race(_TEST_RACE_ID, "anaba_v1")
    assert evaluation.strategy == "穴馬抽出 v1"
    assert len(evaluation.candidates) <= 5
