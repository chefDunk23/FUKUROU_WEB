"""
tests/test_tipster_backtest.py
================================
tipster/backtest.py の単体テスト（純粋関数、DB不要） + 実DBでの小規模統合テスト。
"""
from __future__ import annotations

import pytest

import pandas as pd

from tipster.backtest import (
    _aggregate_picks,
    _approx_fuku_odds,
    _build_past_races,
    _distance_bucket,
    _grade_bucket,
    _parse_period_days,
    _to_str,
)

# ── 純粋関数の単体テスト ──────────────────────────────────────────────────────


def test_grade_bucket_g1_g2_g3():
    assert _grade_bucket("A", None) == "G1"
    assert _grade_bucket("B", None) == "G2"
    assert _grade_bucket("C", None) == "G3"


def test_grade_bucket_l_op_and_jouken():
    assert _grade_bucket("L", None) == "L"
    assert _grade_bucket(None, "999") == "OP"
    assert _grade_bucket(None, "701") == "新馬・未勝利"
    assert _grade_bucket(None, "703") == "新馬・未勝利"
    assert _grade_bucket(None, "005") == "条件戦"
    assert _grade_bucket(None, None) == "条件戦"


def test_grade_bucket_handles_nan_float():
    # pandas が NULL を float('nan') として読み込むケースを安全に処理できること
    assert _grade_bucket(float("nan"), float("nan")) == "条件戦"


def test_distance_bucket():
    assert _distance_bucket(1200) == "sprint"
    assert _distance_bucket(1400) == "sprint"
    assert _distance_bucket(1600) == "mile"
    assert _distance_bucket(1800) == "mile"
    assert _distance_bucket(2000) == "middle"
    assert _distance_bucket(2200) == "middle"
    assert _distance_bucket(2400) == "long"
    assert _distance_bucket(None) == "unknown"


def test_approx_fuku_odds():
    assert _approx_fuku_odds(1.0) == 1.0
    assert _approx_fuku_odds(10.0) == pytest.approx(1.0 + 9.0 * 0.25)
    assert _approx_fuku_odds(0.5) == 1.0  # 下限1.0倍


def test_parse_period_days_named():
    assert _parse_period_days("3m") == 90
    assert _parse_period_days("6m") == 180
    assert _parse_period_days("1y") == 365


def test_parse_period_days_custom():
    assert _parse_period_days("10d") == 10
    assert _parse_period_days("2m") == 60
    assert _parse_period_days("2y") == 730


def test_parse_period_days_invalid():
    with pytest.raises(ValueError):
        _parse_period_days("invalid")


def test_to_str_handles_none_and_nan():
    assert _to_str(None) == ""
    assert _to_str(float("nan")) == ""
    assert _to_str("A") == "A"


def test_aggregate_picks_empty():
    stats = _aggregate_picks([])
    assert stats.race_count == 0
    assert stats.win_rate == 0.0


def test_aggregate_picks_basic():
    # 1着(odds5.0), 3着(odds3.0), 5着(odds10.0) の3件
    picks = [(1, 5.0), (3, 3.0), (5, 10.0)]
    stats = _aggregate_picks(picks)
    assert stats.race_count == 3
    assert stats.win_count == 1
    assert stats.place_count == 2
    assert stats.win_rate == pytest.approx(1 / 3, abs=1e-4)
    assert stats.place_rate == pytest.approx(2 / 3, abs=1e-4)
    # 単勝回収率: 500円 / 300円 = 166.7%
    assert stats.tan_return_rate == pytest.approx(500 / 300, abs=1e-3)


# ── データリーク防止（完了基準4） ─────────────────────────────────────────────


def _make_synthetic_race_groups() -> dict[str, pd.DataFrame]:
    """過去走 P1（H1・H2が出走）。H1の次走は2024-02-01、H2の次走は2024-06-01。"""
    return {
        "P1": pd.DataFrame({
            "horse_id": ["H1", "H2"],
            "confirmed_rank": [1, 2],
            "this_margin": [0.0, 0.5],
            "next_race_date": [pd.Timestamp("2024-02-01"), pd.Timestamp("2024-06-01")],
            "next_confirmed_rank": [3, 1],
            "date": [pd.Timestamp("2024-01-01"), pd.Timestamp("2024-01-01")],
            "distance": [1600, 1600],
            "surface": ["芝", "芝"],
            "race_name": ["テストレース", "テストレース"],
            "grade_code": [None, None],
            "place_code": ["05", "05"],
            "jyoken_cd_3": ["999", "999"],
        }),
    }


def test_opponents_next_race_excludes_results_on_or_after_evaluation_date():
    """評価対象レースの発走日(evaluation_date)以降に確定した対戦相手の次走結果は
    None（未確定扱い）になり、データリークしないことを確認する。
    """
    race_groups = _make_synthetic_race_groups()
    row = {"horse_id": "H1", "prev1_race_id": "P1"}

    # 評価日 2024-03-01: H1の次走(2/1, 確定済み)は採用、H2の次走(6/1, 未来)はマスクされるはず
    past_race_cache: dict = {}
    result = _build_past_races(row, race_groups, past_race_cache, pd.Timestamp("2024-03-01"))
    assert len(result) == 1
    opponents = {o.horse_id: o.next_race_rank for o in result[0].opponents_next_races}
    assert opponents["H1"] == 3
    assert opponents["H2"] is None  # 評価日より後の結果はリークとして除外


def test_opponents_next_race_included_once_evaluation_date_passes_it():
    """評価日をH2の次走日より後にずらすと、その次走結果が正しく採用されることを確認する
    （マスクが「常にNone」ではなく日付依存であることの確認）。
    """
    race_groups = _make_synthetic_race_groups()
    row = {"horse_id": "H1", "prev1_race_id": "P1"}

    past_race_cache: dict = {}
    result = _build_past_races(row, race_groups, past_race_cache, pd.Timestamp("2024-07-01"))
    opponents = {o.horse_id: o.next_race_rank for o in result[0].opponents_next_races}
    assert opponents["H1"] == 3
    assert opponents["H2"] == 1


def test_past_race_cache_is_date_agnostic_across_multiple_evaluations():
    """同じ過去走 P1 が異なる評価日から参照された場合でも、それぞれ正しくマスクされること
    （_get_past_race_info のメモ化が評価日に依存しても安全であることの確認）。
    """
    race_groups = _make_synthetic_race_groups()
    row = {"horse_id": "H1", "prev1_race_id": "P1"}
    shared_cache: dict = {}

    early = _build_past_races(row, race_groups, shared_cache, pd.Timestamp("2024-03-01"))
    late = _build_past_races(row, race_groups, shared_cache, pd.Timestamp("2024-07-01"))

    early_opponents = {o.horse_id: o.next_race_rank for o in early[0].opponents_next_races}
    late_opponents = {o.horse_id: o.next_race_rank for o in late[0].opponents_next_races}
    assert early_opponents["H2"] is None
    assert late_opponents["H2"] == 1


def test_aggregate_picks_ignores_none_rank():
    picks = [(1, 5.0), (None, None)]
    stats = _aggregate_picks(picks)
    assert stats.race_count == 1
    assert stats.win_count == 1


# ── 実DB統合テスト（DB未接続時はスキップ） ────────────────────────────────────

_SKIP_REASON = ""
try:
    from ml.db import engine as _engine
    with _engine.connect() as _conn:
        pass
except Exception as e:
    _SKIP_REASON = f"DB 未接続のためスキップ: {e}"


@pytest.mark.skipif(bool(_SKIP_REASON), reason=_SKIP_REASON or "DB unavailable")
def test_run_backtest_small_period_returns_result():
    from tipster.backtest import run_backtest

    results = run_backtest("honmei_v1", reference_date="2026-06-14", periods=["10d"])
    assert "10d" in results
    r = results["10d"]
    assert r.total_races >= 0
    assert r.honmei_results.race_count <= r.total_races
    # race_level / time_gap など honmei_v1 の全条件が分析対象に含まれること
    assert "race_level" in r.condition_analysis
    assert "time_gap" in r.condition_analysis


@pytest.mark.skipif(bool(_SKIP_REASON), reason=_SKIP_REASON or "DB unavailable")
def test_dummy_condition_is_automatically_included(tmp_path):
    """新条件を @register_condition で追加し戦略JSONに含めるだけで、
    backtest.py のコード変更なしに条件分析へ自動反映されることを確認する（完了基準7）。
    """
    import json

    from tipster.conditions import register_condition
    from tipster.engine import load_strategy
    from tipster.models import ConditionResult

    if "dummy_test_condition_pytest" not in __import__("tipster.conditions", fromlist=["CONDITION_REGISTRY"]).CONDITION_REGISTRY:
        @register_condition("dummy_test_condition_pytest")
        def _dummy(horse, race_ctx, params):
            return ConditionResult(passed=True, score=0.0, reason="dummy")

    base = load_strategy("honmei_v1")
    data = base.model_dump()
    data["conditions"].append({
        "id": "dummy_test_condition_pytest", "enabled": True, "required": False, "params": {},
    })
    strategy_path = tmp_path / "honmei_with_dummy.json"
    strategy_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    from tipster.backtest import run_backtest

    results = run_backtest(str(strategy_path), reference_date="2026-06-14", periods=["10d"])
    assert "dummy_test_condition_pytest" in results["10d"].condition_analysis
