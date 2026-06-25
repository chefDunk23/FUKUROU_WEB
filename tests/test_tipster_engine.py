"""
tests/test_tipster_engine.py
==============================
tipster/engine.py の統合テスト。
race_detail_cache に実データが必要な検証は、DB未接続/対象レース未キャッシュの場合スキップする。
"""
from __future__ import annotations

import pytest

from tipster.engine import compute_confidence, evaluate_race, fetch_race_context, load_strategy, select_aite, select_honmei
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

    def test_clear_count_beats_ai_score(self):
        """clear_count が少ない馬の ai_score が高くても、clear_count 上位が選ばれること。
        AIスコアはタイブレーカーとしてのみ機能し、条件クリア数に優先しない（G5a-3）。"""
        candidates = [
            _ev("A", score=1.0, clear_count=2, ai_score=0.1),  # clear_count 高・ai_score 低
            _ev("B", score=1.0, clear_count=1, ai_score=0.9),  # clear_count 低・ai_score 高
        ]
        honmei = select_honmei(candidates, {"A": 1, "B": 2})
        assert honmei.horse_id == "A"

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


class TestSelectAite:
    """select_aite() のユニットテスト（BET-2）。"""

    def test_returns_all_candidates_when_no_honmei(self):
        """honmei_horse_id=None のとき全候補を返す。"""
        candidates = [_ev("A", score=3.0), _ev("B", score=2.0), _ev("C", score=1.0)]
        aite = select_aite(candidates)
        assert [c.horse_id for c in aite] == ["A", "B", "C"]

    def test_excludes_honmei_from_aite(self):
        """本命馬が相手候補から除外される。"""
        candidates = [_ev("A", score=3.0), _ev("B", score=2.0), _ev("C", score=1.0)]
        aite = select_aite(candidates, honmei_horse_id="A")
        assert [c.horse_id for c in aite] == ["B", "C"]

    def test_max_aite_limits_selection(self):
        """max_aite による上位N頭カットが機能する。"""
        candidates = [_ev("A", score=3.0), _ev("B", score=2.0), _ev("C", score=1.0)]
        aite = select_aite(candidates, max_aite=2)
        assert len(aite) == 2
        assert aite[0].horse_id == "A"

    def test_excludes_honmei_then_caps(self):
        """本命除外 → 上位N頭カットの順序が正しい。"""
        candidates = [_ev("A", score=4.0), _ev("B", score=3.0), _ev("C", score=2.0), _ev("D", score=1.0)]
        aite = select_aite(candidates, honmei_horse_id="A", max_aite=2)
        assert [c.horse_id for c in aite] == ["B", "C"]

    def test_empty_candidates_returns_empty(self):
        """候補が空なら空リストを返す。"""
        assert select_aite([]) == []

    def test_preserves_ranking_order_from_strategy(self):
        """candidates の既存ランキング順序を維持する（ソート不変）。"""
        candidates = [_ev("X", score=5.0), _ev("Y", score=3.0), _ev("Z", score=1.0)]
        aite = select_aite(candidates, honmei_horse_id="X")
        assert aite[0].horse_id == "Y"
        assert aite[1].horse_id == "Z"

    def test_honmei_not_in_candidates_returns_all(self):
        """本命 horse_id が candidates に存在しない場合、全員を返す（安全な挙動）。"""
        candidates = [_ev("A", score=2.0), _ev("B", score=1.0)]
        aite = select_aite(candidates, honmei_horse_id="Z")
        assert len(aite) == 2
