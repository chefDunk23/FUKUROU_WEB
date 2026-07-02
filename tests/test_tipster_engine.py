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


def test_evaluate_race_context_required_condition_returning_none_does_not_eliminate():
    """BET-6: passed=None（判定不能・保留）を返す条件が required:true でも、馬は失格にならない。

    race_level/time_gap は past_races が無いと passed=None を返す（tipster/conditions.py）。
    旧実装(`not result.passed`)では None も False 同様に失格判定されてしまうバグがあった
    （engine.py:499 修正の回帰防止テスト）。
    """
    from tipster.engine import evaluate_race_context
    from tipster.models import ConditionConfig, HorseContext, RaceContext, RankingConfig, Strategy

    horse = HorseContext(
        horse_id="H1", horse_name="テスト馬", umaban=1, wakuban=1,
        jockey_id="J1", jockey_name=None, trainer_id=None, trainer_name=None,
        burden_weight=56.0, horse_weight=460.0, ai_score=0.5, ai_rank=1,
        chokyo_score=None, position_tendency=None,
        prev_race_rank=None, prev_race_grade=None, prev_race_days_ago=None,
        past_races=[],  # race_level/time_gap が passed=None を返す条件
    )
    race_ctx = RaceContext(
        race_id="TESTRACE", race_name="テストレース", race_date="2026-01-01",
        place_code="05", keibajo_name="東京", distance=2000, surface="芝",
        class_label=None, grade_code=None, horses=[horse],
    )
    strategy = Strategy(
        name="テスト戦略", tipster="test", type="honmei", version="1.0",
        conditions=[
            ConditionConfig(id="race_level", enabled=True, required=True, params={}),
            ConditionConfig(id="time_gap", enabled=True, required=True, params={}),
        ],
        ranking=RankingConfig(),
    )

    evaluation = evaluate_race_context(race_ctx, strategy)
    assert evaluation.candidates[0].horse_id == "H1"
    assert evaluation.candidates[0].eliminated is False
    assert all(c.passed is None for c in evaluation.candidates[0].conditions)


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


# ── _compute_payload_live 回帰テスト ─────────────────────────────────────────
# 2026-07-03: race_detail_cache に対象レースのキャッシュが無い場合の
# フォールバック経路(_compute_payload_live)が、V2アンサンブル引退で削除済みの
# api_v2.routers.races._compute_detail を直接importしようとして ImportError に
# なっていた。未来レース(7/4-5)へのpicks生成が72レース全滅する形で発覚した。
# 既存テスト(test_fetch_race_context_returns_horses等)は race_detail_cache に
# キャッシュがある場合のみ実行されるため、このバグを検出できていなかった。

def test_compute_payload_live_does_not_import_deleted_compute_detail():
    """tipster.engine が削除済みの api_v2.routers.races._compute_detail を
    参照していないこと（モジュールとして存在しないことを確認する回帰テスト）。"""
    import api_v2.routers.races as races_module

    assert not hasattr(races_module, "_compute_detail"), (
        "_compute_detail は V2アンサンブル引退で削除済みのはずだが races.py に復活している。"
        "tipster/engine.py が再びこれをimportしようとしていないか確認すること。"
    )


# _compute_payload_live は fukurou_keiba_v2.races/race_entries を直接クエリするため、
# race_detail_cache 用の _TEST_RACE_ID（旧フォーマットの短縮ID）とは別に、
# 実在する16桁 race_id を用意する。
_LIVE_SKIP_REASON = ""
_LIVE_TEST_RACE_ID = ""
try:
    import psycopg2 as _psycopg2

    from shared.config import DB_V2 as _DB_V2

    _conn2 = _psycopg2.connect(**_DB_V2)
    try:
        _cur2 = _conn2.cursor()
        _cur2.execute(
            "SELECT r.id FROM races r JOIN race_entries e ON e.race_id = r.id "
            "WHERE r.race_date = '2026-06-14' GROUP BY r.id "
            "HAVING COUNT(e.umaban) > 0 ORDER BY r.id LIMIT 1"
        )
        _row2 = _cur2.fetchone()
        if _row2 is None:
            _LIVE_SKIP_REASON = "テスト用レース（2026-06-14）がDBに存在しないためスキップ"
        else:
            _LIVE_TEST_RACE_ID = _row2[0]
    finally:
        _conn2.close()
except Exception as _e2:
    _LIVE_SKIP_REASON = f"DB 未接続のためスキップ: {_e2}"


@pytest.mark.skipif(bool(_LIVE_SKIP_REASON), reason=_LIVE_SKIP_REASON or "DB unavailable")
def test_compute_payload_live_returns_dict_with_ai_score_none():
    """_compute_payload_live が ai_score/ai_rank=None でも正しく dict を返すこと
    （V2アンサンブル非依存の軽量版であることの確認）。"""
    from tipster.engine import _compute_payload_live

    payload = _compute_payload_live(_LIVE_TEST_RACE_ID)

    assert isinstance(payload, dict)
    assert "horses" in payload
    assert len(payload["horses"]) > 0
    for h in payload["horses"]:
        assert h["ai_score"] is None
        assert h["ai_rank"] is None
        assert "horse_id" in h
        assert "extra" in h


@pytest.mark.skipif(bool(_LIVE_SKIP_REASON), reason=_LIVE_SKIP_REASON or "DB unavailable")
def test_fetch_race_context_works_without_cache():
    """race_detail_cache が存在しないレースでも fetch_race_context が
    例外を送出せず RaceContext を返すこと（フォールバック経路の疎通確認）。"""
    from unittest.mock import patch

    from tipster.engine import fetch_race_context

    with patch("tipster.engine._load_cached_payload", return_value=None):
        ctx = fetch_race_context(_LIVE_TEST_RACE_ID)

    assert ctx.race_id == _LIVE_TEST_RACE_ID
    assert len(ctx.horses) > 0
