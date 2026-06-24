"""
tests/test_tipster_engine.py
==============================
tipster/engine.py の統合テスト。
race_detail_cache に実データが必要な検証は、DB未接続/対象レース未キャッシュの場合スキップする。
"""
from __future__ import annotations

import pytest

from tipster.engine import compute_confidence, evaluate_race, fetch_race_context, load_strategy, select_honmei
from tipster.models import ConditionResult, HorseEvaluation

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


def _ev(horse_id: str, score: float, clear_count: int = 1, ai_score: float = 0.0) -> HorseEvaluation:
    """clear_count 個の合格条件（合計スコア=score）を持つ HorseEvaluation を作る（テスト用）。"""
    conditions = [ConditionResult(passed=True, score=score)]
    conditions += [ConditionResult(passed=True, score=0.0) for _ in range(clear_count - 1)]
    return HorseEvaluation(horse_id=horse_id, ai_score=ai_score, conditions=conditions)


class TestSelectHonmei:
    def test_picks_higher_clear_count_first(self):
        candidates = [_ev("A", score=5.0, clear_count=1), _ev("B", score=1.0, clear_count=2)]
        honmei = select_honmei(candidates, {"A": 1, "B": 2})
        assert honmei.horse_id == "B"

    def test_tiebreak_falls_to_total_score(self):
        candidates = [_ev("A", score=1.0, clear_count=2), _ev("B", score=2.0, clear_count=2)]
        honmei = select_honmei(candidates, {"A": 1, "B": 2})
        assert honmei.horse_id == "B"

    def test_tiebreak_falls_to_ai_score(self):
        candidates = [
            _ev("A", score=1.0, clear_count=1, ai_score=0.5),
            _ev("B", score=1.0, clear_count=1, ai_score=0.8),
        ]
        honmei = select_honmei(candidates, {"A": 1, "B": 2})
        assert honmei.horse_id == "B"

    def test_tiebreak_falls_to_lowest_umaban(self):
        candidates = [_ev("A", score=1.0), _ev("B", score=1.0)]
        honmei = select_honmei(candidates, {"A": 5, "B": 2})
        assert honmei.horse_id == "B"

    def test_empty_candidates_returns_none(self):
        assert select_honmei([], {}) is None

    def test_min_total_score_excludes_low_scorers(self):
        candidates = [_ev("A", score=2.0), _ev("B", score=4.0)]
        honmei = select_honmei(candidates, {"A": 1, "B": 2}, min_total_score=3.0)
        assert honmei.horse_id == "B"

    def test_min_total_score_returns_none_when_all_below_threshold(self):
        candidates = [_ev("A", score=2.0), _ev("B", score=2.5)]
        honmei = select_honmei(candidates, {"A": 1, "B": 2}, min_total_score=3.0)
        assert honmei is None

    def test_max_candidates_for_honmei_returns_none_when_too_many(self):
        candidates = [_ev("A", score=5.0), _ev("B", score=4.0), _ev("C", score=3.0)]
        honmei = select_honmei(candidates, {"A": 1, "B": 2, "C": 3}, max_candidates_for_honmei=2)
        assert honmei is None

    def test_max_candidates_for_honmei_allows_when_within_limit(self):
        candidates = [_ev("A", score=5.0), _ev("B", score=4.0)]
        honmei = select_honmei(candidates, {"A": 1, "B": 2}, max_candidates_for_honmei=2)
        assert honmei.horse_id == "A"


class TestComputeConfidence:
    def test_none_honmei_is_grade_c(self):
        assert compute_confidence(None, eligible_count=3) == "C"

    def test_high_score_and_few_candidates_is_grade_s(self):
        honmei = _ev("A", score=5.0)
        assert compute_confidence(honmei, eligible_count=5) == "S"

    def test_high_score_but_many_candidates_falls_to_a(self):
        honmei = _ev("A", score=5.0)
        assert compute_confidence(honmei, eligible_count=8) == "A"

    def test_mid_score_is_grade_a(self):
        honmei = _ev("A", score=3.0)
        assert compute_confidence(honmei, eligible_count=8) == "A"

    def test_mid_score_many_candidates_falls_to_b(self):
        honmei = _ev("A", score=3.0)
        assert compute_confidence(honmei, eligible_count=9) == "B"

    def test_low_score_is_grade_b(self):
        honmei = _ev("A", score=2.0)
        assert compute_confidence(honmei, eligible_count=100) == "B"

    def test_very_low_score_is_grade_c(self):
        honmei = _ev("A", score=1.0)
        assert compute_confidence(honmei, eligible_count=1) == "C"
