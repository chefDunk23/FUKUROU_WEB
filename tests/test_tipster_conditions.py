"""
tests/test_tipster_conditions.py
==================================
tipster/conditions.py の5条件関数の単体テスト（モックコンテキスト、DB不要）。
"""
from __future__ import annotations

from tipster.conditions import (
    check_class_direction,
    check_course_fitness,
    check_jockey_change,
    check_jockey_intent,
    check_min_odds,
    check_pace_position,
    check_race_level,
    check_rest_interval,
    check_time_gap,
    check_track_bias_fit,
    check_weight_change,
)
from tipster.models import HorseContext, PastRaceInfo, PastRaceOpponent, RaceContext


def _horse(**overrides) -> HorseContext:
    defaults = dict(
        horse_id="H1", horse_name="テスト馬", umaban=1, wakuban=1,
        jockey_id="J1", jockey_name="騎手A", trainer_id="T1", trainer_name="調教師A",
        burden_weight=56.0, horse_weight=460.0, ai_score=0.5, ai_rank=1,
        chokyo_score=50.0, position_tendency=0.5,
        prev_race_rank=None, prev_race_grade=None, prev_race_days_ago=None,
        past_races=[],
    )
    defaults.update(overrides)
    return HorseContext(**defaults)


def _race(**overrides) -> RaceContext:
    defaults = dict(
        race_id="R1", race_name="テストレース", race_date="2026-06-20",
        place_code="05", keibajo_name="東京", distance=1600, surface="芝",
        class_label="3勝クラス", grade_code=None, horses=[],
        front_bias_pit=None, inner_bias_pit=None, bias_source="none",
    )
    defaults.update(overrides)
    return RaceContext(**defaults)


# ── race_level ──────────────────────────────────────────────────────────────


def test_race_level_insufficient_data_holds_neutral():
    horse = _horse(past_races=[])
    result = check_race_level(horse, _race(), {})
    assert result.passed is True
    assert result.score == 0.0


def test_race_level_passes_when_top_rate_high_default_grade():
    opponents = [
        PastRaceOpponent(horse_id=f"H{i}", this_rank=i, this_margin=0.1 * i, next_race_rank=r)
        for i, r in enumerate([1, 2, 3, 10], start=1)
    ]
    prev = PastRaceInfo(
        race_id="P1", date="2026-05-01", rank=4, distance=1600, surface="芝",
        head_count=10, race_name="前走", class_score=10.0, time_score=10.0,
        member_level_score=10.0, opponents_next_races=opponents, grade_code=None,
    )
    horse = _horse(past_races=[prev])
    result = check_race_level(horse, _race(), {
        "min_next_race_horses": 3,
        "thresholds": {"default": {"place_rate": 0.5, "winner_max_rank": 3}},
    })
    assert result.passed is True
    assert result.score == 1.0


def test_race_level_fails_when_top_rate_low():
    opponents = [
        PastRaceOpponent(horse_id=f"H{i}", this_rank=i, this_margin=0.1 * i, next_race_rank=r)
        for i, r in enumerate([10, 11, 12, 13], start=1)
    ]
    prev = PastRaceInfo(
        race_id="P1", date="2026-05-01", rank=4, distance=1600, surface="芝",
        head_count=13, race_name="前走", class_score=10.0, time_score=10.0,
        member_level_score=10.0, opponents_next_races=opponents, grade_code=None,
    )
    horse = _horse(past_races=[prev])
    result = check_race_level(horse, _race(), {
        "min_next_race_horses": 3,
        "thresholds": {"default": {"place_rate": 0.5, "winner_max_rank": 3}},
    })
    assert result.passed is False
    assert result.score == -1.0


def test_race_level_uses_grade_specific_threshold():
    # 前走が G1(grade_code="A") かつ自身は4着(top3に該当しない) -> threshold_rank=3 のまま、
    # place_rate のみ G1基準(0.20)が適用される
    opponents = [
        PastRaceOpponent(horse_id=f"H{i}", this_rank=i, this_margin=0.1 * i, next_race_rank=r)
        for i, r in enumerate([2, 20, 21, 22], start=1)
    ]
    prev = PastRaceInfo(
        race_id="P1", date="2026-05-01", rank=4, distance=1600, surface="芝",
        head_count=10, race_name="G1前走", class_score=10.0, time_score=10.0,
        member_level_score=10.0, opponents_next_races=opponents, grade_code="A",
    )
    horse = _horse(past_races=[prev])
    thresholds = {
        "G1": {"place_rate": 0.20, "winner_max_rank": 7},
        "default": {"place_rate": 0.50, "winner_max_rank": 3},
    }
    result = check_race_level(horse, _race(), {"min_next_race_horses": 3, "thresholds": thresholds})
    # 1/4 = 25% >= G1基準20% なので通過（defaultの50%基準なら不合格になるはず）
    assert result.passed is True
    assert result.detail["grade_label"] == "G1"
    assert result.detail["threshold_rank"] == 3


def test_race_level_winner_exception_extends_to_top3():
    # 前走2着(rank=2、勝利でなくてもtop3) + grade=G1 -> winner_max_rank=7 に緩和される
    opponents = [
        PastRaceOpponent(horse_id=f"H{i}", this_rank=i, this_margin=0.1 * i, next_race_rank=r)
        for i, r in enumerate([5, 6, 7, 20], start=1)
    ]
    prev = PastRaceInfo(
        race_id="P1", date="2026-05-01", rank=2, distance=1600, surface="芝",
        head_count=10, race_name="前走", class_score=10.0, time_score=10.0,
        member_level_score=10.0, opponents_next_races=opponents, grade_code="A",
    )
    horse = _horse(past_races=[prev])
    thresholds = {"G1": {"place_rate": 0.5, "winner_max_rank": 7}, "default": {"place_rate": 0.33, "winner_max_rank": 3}}
    result = check_race_level(horse, _race(), {"min_next_race_horses": 3, "thresholds": thresholds})
    assert result.detail["threshold_rank"] == 7
    assert result.passed is True  # 3/4 が7着内 -> 0.75 >= 0.5


def test_race_level_or_falls_back_to_second_past_race():
    # 前走は基準未達、前々走が基準クリア -> 全体としては合格(OR条件)
    fail_opponents = [
        PastRaceOpponent(horse_id=f"H{i}", this_rank=i, this_margin=0.1 * i, next_race_rank=r)
        for i, r in enumerate([20, 21, 22], start=1)
    ]
    pass_opponents = [
        PastRaceOpponent(horse_id=f"H{i}", this_rank=i, this_margin=0.1 * i, next_race_rank=r)
        for i, r in enumerate([1, 2, 3], start=1)
    ]
    prev1 = PastRaceInfo(
        race_id="P1", date="2026-05-01", rank=4, distance=1600, surface="芝",
        head_count=10, race_name="前走(不利)", class_score=None, time_score=None,
        member_level_score=None, opponents_next_races=fail_opponents, grade_code=None,
    )
    prev2 = PastRaceInfo(
        race_id="P0", date="2026-03-01", rank=4, distance=1600, surface="芝",
        head_count=10, race_name="前々走(好走)", class_score=None, time_score=None,
        member_level_score=None, opponents_next_races=pass_opponents, grade_code=None,
    )
    horse = _horse(past_races=[prev1, prev2])
    thresholds = {"default": {"place_rate": 0.5, "winner_max_rank": 3}}
    result = check_race_level(horse, _race(), {"min_next_race_horses": 3, "thresholds": thresholds})
    assert result.passed is True
    assert "前々走(好走)" in result.reason


# ── time_gap ────────────────────────────────────────────────────────────────


def test_time_gap_no_past_race_holds_neutral():
    horse = _horse(past_races=[])
    result = check_time_gap(horse, _race(), {})
    assert result.passed is True and result.score == 0.0


def test_time_gap_within_threshold_passes():
    opponents = [PastRaceOpponent(horse_id="H1", this_rank=3, this_margin=0.5, next_race_rank=None)]
    prev = PastRaceInfo(
        race_id="P1", date="2026-05-01", rank=3, distance=1400, surface="芝",
        head_count=10, race_name="前走", class_score=None, time_score=None,
        member_level_score=None, opponents_next_races=opponents,
    )
    horse = _horse(horse_id="H1", past_races=[prev])
    result = check_time_gap(horse, _race(), {"sprint_max_sec": 1.0})
    assert result.passed is True
    assert result.score == 0.0


def test_time_gap_exceeds_fallback_fails():
    opponents = [PastRaceOpponent(horse_id="H1", this_rank=10, this_margin=3.0, next_race_rank=None)]
    prev = PastRaceInfo(
        race_id="P1", date="2026-05-01", rank=10, distance=1400, surface="芝",
        head_count=10, race_name="前走", class_score=None, time_score=None,
        member_level_score=None, opponents_next_races=opponents,
    )
    horse = _horse(horse_id="H1", past_races=[prev])
    result = check_time_gap(horse, _race(), {"sprint_max_sec": 1.0, "sprint_fallback_sec": 1.5})
    assert result.passed is False
    assert result.score == -1.0


def test_time_gap_within_fallback_range_penalized_but_passes():
    opponents = [PastRaceOpponent(horse_id="H1", this_rank=5, this_margin=1.2, next_race_rank=None)]
    prev = PastRaceInfo(
        race_id="P1", date="2026-05-01", rank=5, distance=1400, surface="芝",
        head_count=10, race_name="前走", class_score=None, time_score=None,
        member_level_score=None, opponents_next_races=opponents,
    )
    horse = _horse(horse_id="H1", past_races=[prev])
    result = check_time_gap(horse, _race(), {"sprint_max_sec": 1.0, "sprint_fallback_sec": 1.5})
    assert result.passed is True
    assert result.score == -0.5


# ── track_bias_fit ───────────────────────────────────────────────────────────


def test_track_bias_fit_missing_data_holds_neutral():
    horse = _horse(position_tendency=None)
    result = check_track_bias_fit(horse, _race(front_bias_pit=None), {})
    assert result.passed is True and result.score == 0.0


def test_track_bias_fit_mismatch_excludes_when_configured():
    horse = _horse(position_tendency=0.9)  # 追込
    race = _race(front_bias_pit=0.5)       # 前残り想定
    result = check_track_bias_fit(horse, race, {"exclude_on_mismatch": True})
    assert result.passed is False
    assert result.score == -1.0


def test_track_bias_fit_mismatch_not_excluded_when_disabled():
    horse = _horse(position_tendency=0.9)
    race = _race(front_bias_pit=0.5)
    result = check_track_bias_fit(horse, race, {"exclude_on_mismatch": False})
    assert result.passed is True
    assert result.score == -1.0


def test_track_bias_fit_match_passes():
    horse = _horse(position_tendency=0.1)  # 先行
    race = _race(front_bias_pit=0.5)       # 前残り想定 -> 適合
    result = check_track_bias_fit(horse, race, {})
    assert result.passed is True
    assert result.score == 0.0


# ── weight_change ────────────────────────────────────────────────────────────


def test_weight_change_no_data_holds_neutral():
    horse = _horse(burden_weight=None, prev_burden_weight=None)
    result = check_weight_change(horse, _race(), {})
    assert result.passed is True and result.score == 0.0


def test_weight_change_increase_penalized():
    horse = _horse(burden_weight=58.0, prev_burden_weight=56.0)
    result = check_weight_change(horse, _race(), {"increase_penalty": -1})
    assert result.score == -1


def test_weight_change_decrease_bonus():
    horse = _horse(
        burden_weight=54.0, prev_burden_weight=56.0,
        jockey_career_wins=200, jockey_id="J1", prev_jockey_id="J1",
    )
    result = check_weight_change(horse, _race(), {"decrease_bonus": 1})
    assert result.score == 1


def test_weight_change_decrease_suppressed_for_new_apprentice():
    horse = _horse(
        burden_weight=54.0, prev_burden_weight=56.0,
        jockey_id="J_NEW", prev_jockey_id="J_OLD", jockey_career_wins=10,
    )
    result = check_weight_change(horse, _race(), {"apprentice_bonus_disabled": True})
    assert result.score == 0.0


# ── jockey_change ────────────────────────────────────────────────────────────


def test_jockey_change_unchanged_neutral():
    horse = _horse(jockey_id="J1", prev_jockey_id="J1")
    result = check_jockey_change(horse, _race(), {})
    assert result.passed is True and result.score == 0.0


def test_jockey_change_step1_penalized():
    horse = _horse(jockey_id="J_NEW", prev_jockey_id="J_OLD", jockey_change_step1_same_race=True)
    result = check_jockey_change(horse, _race(), {})
    assert result.score == -1.0


def test_jockey_change_step1_overridden_by_affinity():
    horse = _horse(
        jockey_id="J_NEW", prev_jockey_id="J_OLD", jockey_change_step1_same_race=True,
        jockey_change_affinity={"combo_count": 20, "combo_win_rate": 0.3, "combo_top3_rate": 0.5},
    )
    result = check_jockey_change(
        horse, _race(), {"stable_affinity_min_rides": 10, "stable_affinity_min_winrate": 0.15}
    )
    assert result.score == 1.0


def test_jockey_change_step2_no_count():
    horse = _horse(jockey_id="J_NEW", prev_jockey_id="J_OLD", jockey_change_step2_other_venue=True)
    result = check_jockey_change(horse, _race(), {})
    assert result.score == 0.0


def test_jockey_change_top_jockey_bonus():
    horse = _horse(jockey_id="J_NEW", prev_jockey_id="J_OLD", jockey_yr_wins=50)
    result = check_jockey_change(horse, _race(), {"top_jockey_threshold": 30})
    assert result.score == 1.0


# ── min_odds ─────────────────────────────────────────────────────────────────


def test_min_odds_no_data_holds_neutral():
    horse = _horse(tan_odds=None)
    result = check_min_odds(horse, _race(), {"min_tan_odds": 10.0})
    assert result.passed is True and result.score == 0.0


def test_min_odds_above_threshold_passes():
    horse = _horse(tan_odds=15.0)
    result = check_min_odds(horse, _race(), {"min_tan_odds": 10.0})
    assert result.passed is True and result.score == 0.0


def test_min_odds_below_threshold_fails():
    horse = _horse(tan_odds=3.0)
    result = check_min_odds(horse, _race(), {"min_tan_odds": 10.0})
    assert result.passed is False and result.score == -1.0


# ── course_fitness ───────────────────────────────────────────────────────────


def test_course_fitness_no_data_holds_neutral():
    horse = _horse(past_races=[])
    result = check_course_fitness(horse, _race(place_code="05", distance=1600), {})
    assert result.passed is True and result.score == 0.0


def test_course_fitness_same_course_good_run():
    prev = PastRaceInfo(
        race_id="P1", date="2026-01-01", rank=2, distance=1600, surface="芝",
        head_count=10, race_name="前走", class_score=None, time_score=None,
        member_level_score=None, opponents_next_races=[], place_code="05",
    )
    horse = _horse(past_races=[prev])
    race = _race(place_code="05", distance=1700, surface="芝")
    result = check_course_fitness(horse, race, {"distance_tolerance": 200})
    assert result.score == 2.0
    assert "好走歴" in result.reason


def test_course_fitness_same_course_bad_run():
    prev = PastRaceInfo(
        race_id="P1", date="2026-01-01", rank=10, distance=1600, surface="芝",
        head_count=10, race_name="前走", class_score=None, time_score=None,
        member_level_score=None, opponents_next_races=[], place_code="05",
    )
    horse = _horse(past_races=[prev])
    race = _race(place_code="05", distance=1600, surface="芝")
    result = check_course_fitness(horse, race, {"distance_tolerance": 200})
    assert result.score == -1.0


def test_course_fitness_similar_course_good_run():
    prev = PastRaceInfo(
        race_id="P1", date="2026-01-01", rank=1, distance=1600, surface="芝",
        head_count=10, race_name="前走", class_score=None, time_score=None,
        member_level_score=None, opponents_next_races=[], place_code="06",
    )
    horse = _horse(past_races=[prev])
    race = _race(place_code="05", distance=1600, surface="芝")
    result = check_course_fitness(horse, race, {"distance_tolerance": 200, "similar_courses": {"05": ["06"]}})
    assert result.score == 1.0
    assert "類似コース" in result.reason


def test_course_fitness_no_experience_neutral():
    prev = PastRaceInfo(
        race_id="P1", date="2026-01-01", rank=1, distance=2400, surface="ダート",
        head_count=10, race_name="前走", class_score=None, time_score=None,
        member_level_score=None, opponents_next_races=[], place_code="08",
    )
    horse = _horse(past_races=[prev])
    race = _race(place_code="05", distance=1600, surface="芝")
    result = check_course_fitness(horse, race, {"distance_tolerance": 200, "similar_courses": {}})
    assert result.score == 0.0


# ── pace_position ────────────────────────────────────────────────────────────


def test_pace_position_no_data_holds_neutral():
    horse = _horse(position_tendency=None)
    result = check_pace_position(horse, _race(pace_prediction=None), {})
    assert result.passed is True and result.score == 0.0


def test_pace_position_fast_closer_bonus():
    horse = _horse(position_tendency=0.8)
    result = check_pace_position(horse, _race(pace_prediction="fast"), {})
    assert result.score == 2.0


def test_pace_position_fast_front_penalty():
    horse = _horse(position_tendency=0.1)
    result = check_pace_position(horse, _race(pace_prediction="fast"), {})
    assert result.score == -1.0


def test_pace_position_slow_front_bonus():
    horse = _horse(position_tendency=0.1)
    result = check_pace_position(horse, _race(pace_prediction="slow"), {})
    assert result.score == 2.0


def test_pace_position_slow_closer_penalty():
    horse = _horse(position_tendency=0.8)
    result = check_pace_position(horse, _race(pace_prediction="slow"), {})
    assert result.score == -1.0


def test_pace_position_medium_neutral():
    horse = _horse(position_tendency=0.5)
    result = check_pace_position(horse, _race(pace_prediction="medium"), {})
    assert result.score == 0.0


# ── class_direction ──────────────────────────────────────────────────────────


def test_class_direction_no_data_holds_neutral():
    horse = _horse(past_races=[])
    result = check_class_direction(horse, _race(class_level=None), {})
    assert result.passed is True and result.score == 0.0


def test_class_direction_downgrade():
    prev = PastRaceInfo(
        race_id="P1", date="2026-01-01", rank=5, distance=1600, surface="芝",
        head_count=10, race_name="前走", class_score=None, time_score=None,
        member_level_score=None, opponents_next_races=[], class_level=6,
    )
    horse = _horse(past_races=[prev])
    result = check_class_direction(horse, _race(class_level=4), {})
    assert result.score == 2.0


def test_class_direction_same():
    prev = PastRaceInfo(
        race_id="P1", date="2026-01-01", rank=5, distance=1600, surface="芝",
        head_count=10, race_name="前走", class_score=None, time_score=None,
        member_level_score=None, opponents_next_races=[], class_level=4,
    )
    horse = _horse(past_races=[prev])
    result = check_class_direction(horse, _race(class_level=4), {})
    assert result.score == 1.0


def test_class_direction_upgrade():
    prev = PastRaceInfo(
        race_id="P1", date="2026-01-01", rank=1, distance=1600, surface="芝",
        head_count=10, race_name="前走", class_score=None, time_score=None,
        member_level_score=None, opponents_next_races=[], class_level=4,
    )
    horse = _horse(past_races=[prev])
    result = check_class_direction(horse, _race(class_level=6), {})
    assert result.score == -1.0


def test_class_direction_g1_to_g1():
    prev = PastRaceInfo(
        race_id="P1", date="2026-01-01", rank=1, distance=2000, surface="芝",
        head_count=10, race_name="前走", class_score=None, time_score=None,
        member_level_score=None, opponents_next_races=[], class_level=10,
    )
    horse = _horse(past_races=[prev])
    result = check_class_direction(horse, _race(class_level=10), {})
    assert result.score == 3.0


# ── rest_interval ────────────────────────────────────────────────────────────


def test_rest_interval_no_data_holds_neutral():
    horse = _horse(prev_race_days_ago=None, past_races=[])
    result = check_rest_interval(horse, _race(), {})
    assert result.passed is True and result.score == 0.0


def test_rest_interval_rentou_penalty():
    horse = _horse(prev_race_days_ago=7, past_races=[])
    result = check_rest_interval(horse, _race(), {})
    assert result.score == -1.0


def test_rest_interval_optimal_bonus():
    horse = _horse(prev_race_days_ago=21, past_races=[])
    result = check_rest_interval(horse, _race(), {"optimal_min": 15, "optimal_max": 35})
    assert result.score == 1.0


def test_rest_interval_slightly_short_neutral():
    # _RENTOU_THRESHOLD_DAYS(14)より長いが、カスタムoptimal_min(20)未満の「やや短い」帯
    horse = _horse(prev_race_days_ago=17, past_races=[])
    result = check_rest_interval(horse, _race(), {"optimal_min": 20, "optimal_max": 35})
    assert result.score == 0.0


def test_rest_interval_slightly_long_neutral():
    horse = _horse(prev_race_days_ago=50, past_races=[])
    result = check_rest_interval(horse, _race(), {"optimal_min": 15, "optimal_max": 35, "long_rest_threshold": 71})
    assert result.score == 0.0


def test_rest_interval_long_rest_penalty():
    horse = _horse(prev_race_days_ago=90, past_races=[])
    result = check_rest_interval(horse, _race(), {"long_rest_threshold": 71})
    assert result.score == -1.0


def test_rest_interval_overseas_penalty_via_field():
    horse = _horse(prev_race_days_ago=30, past_races=[], overseas_interim_place_code="A4")
    result = check_rest_interval(horse, _race(), {"overseas_penalty": -2})
    assert result.score == -2.0
    assert "海外" in result.reason


def test_rest_interval_overseas_penalty_via_past_race_place_code():
    prev = PastRaceInfo(
        race_id="P1", date="2026-01-01", rank=1, distance=2000, surface="芝",
        head_count=10, race_name="前走", class_score=None, time_score=None,
        member_level_score=None, opponents_next_races=[], place_code="B2",
    )
    horse = _horse(prev_race_days_ago=30, past_races=[prev])
    result = check_rest_interval(horse, _race(), {"overseas_penalty": -2})
    assert result.score == -2.0


# ── jockey_intent ────────────────────────────────────────────────────────────


def test_jockey_intent_delegates_to_jockey_change_base():
    horse = _horse(jockey_id="J_NEW", prev_jockey_id="J_OLD", jockey_change_step1_same_race=True)
    result = check_jockey_intent(horse, _race(), {})
    assert result.score == -1.0  # check_jockey_change の Step1 ペナルティがそのまま伝播


def test_jockey_intent_course_specialist_bonus():
    horse = _horse(
        jockey_id="J_NEW", prev_jockey_id="J_OLD",
        jockey_venue_win_rate=0.20, jockey_overall_win_rate=0.10,
    )
    result = check_jockey_intent(horse, _race(), {"course_winrate_bonus_pct": 20})
    # base(騎手変更中立 score=0.0) + コース巧者加点(+1.0)
    assert result.score == 1.0
    assert "コース巧者" in result.reason


def test_jockey_intent_no_bonus_when_not_specialist():
    horse = _horse(
        jockey_id="J_NEW", prev_jockey_id="J_OLD",
        jockey_venue_win_rate=0.11, jockey_overall_win_rate=0.10,
    )
    result = check_jockey_intent(horse, _race(), {"course_winrate_bonus_pct": 20})
    assert result.score == 0.0
