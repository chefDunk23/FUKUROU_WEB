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
from tipster.conditions_v2 import (
    check_v2_bracket_bias,
    check_v2_f3_top,
    check_v2_hill_fit,
    check_v2_opponent_winners,
    check_v2_pace_match,
    check_v2_race_order,
    check_v2_sire_venue,
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
    # BET-6: 判定不能(データ不足)はpassed=None(中立)。passed=Trueの「クリア」とは区別する。
    assert result.passed is None
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
    # BET-6: 判定不能(データ不足)はpassed=None(中立)。
    assert result.passed is None and result.score == 0.0


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
    # BET-6: 判定不能(データ不足)はpassed=None(中立)。
    assert result.passed is None and result.score == 0.0


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
    # BET-6: +2.0kgはデフォルトしきい値(3.0kg)未満のため、scoreは減点でもpassedはTrueのまま。
    assert result.passed is True


def test_weight_change_significant_increase_fails():
    """BET-6: false_threshold_kg(デフォルト3.0kg)以上の斤量増はpassed=Falseになる。"""
    horse = _horse(burden_weight=59.5, prev_burden_weight=56.0)
    result = check_weight_change(horse, _race(), {"increase_penalty": -1})
    assert result.passed is False
    assert result.score == -1  # scoreの計算自体は変更しない


def test_weight_change_custom_false_threshold():
    horse = _horse(burden_weight=58.0, prev_burden_weight=56.0)
    result = check_weight_change(horse, _race(), {"increase_penalty": -1, "false_threshold_kg": 1.5})
    assert result.passed is False
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
    # BET-6: 相性データでも好材料がない乗り替わりマイナスは、既存データで判断可能な明確に
    # 不利なケースとしてpassed=Falseとする。
    assert result.passed is False


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


# ── v2_f3_top ────────────────────────────────────────────────────────────


def _past_with_f3(f3pct: float | None, rank: int = 3) -> PastRaceInfo:
    return PastRaceInfo(
        race_id="P1", date="2026-05-01", rank=rank, distance=1600, surface="ダート",
        head_count=10, race_name="前走", class_score=None, time_score=None,
        member_level_score=None, opponents_next_races=[], grade_code=None,
        f3_time_rank_pct=f3pct,
    )


def test_v2_f3_top_passes_when_pct_within_threshold():
    horse = _horse(past_races=[_past_with_f3(0.20)])
    result = check_v2_f3_top(horse, _race(), {})
    assert result.passed is True
    assert result.score > 0


def test_v2_f3_top_fails_when_pct_above_threshold():
    horse = _horse(past_races=[_past_with_f3(0.60)])
    result = check_v2_f3_top(horse, _race(), {})
    assert result.passed is False
    assert result.score == 0.0


def test_v2_f3_top_neutral_when_no_data():
    horse = _horse(past_races=[_past_with_f3(None)])
    result = check_v2_f3_top(horse, _race(), {})
    assert result.passed is None

    horse_no_past = _horse(past_races=[])
    result2 = check_v2_f3_top(horse_no_past, _race(), {})
    assert result2.passed is None


def test_v2_f3_top_boundary_at_threshold():
    # 0.33 以下 → True
    horse = _horse(past_races=[_past_with_f3(0.33)])
    result = check_v2_f3_top(horse, _race(), {"top_pct": 0.33})
    assert result.passed is True

    # 0.34 は境界超え → False
    horse2 = _horse(past_races=[_past_with_f3(0.34)])
    result2 = check_v2_f3_top(horse2, _race(), {"top_pct": 0.33})
    assert result2.passed is False


# ── v2_hill_fit ───────────────────────────────────────────────────────────


def _past_at(place_code: str, rank: int = 1) -> PastRaceInfo:
    return PastRaceInfo(
        race_id="P1", date="2026-05-01", rank=rank, distance=1800, surface="ダート",
        head_count=10, race_name="前走", class_score=None, time_score=None,
        member_level_score=None, opponents_next_races=[], place_code=place_code,
    )


def test_v2_hill_fit_passes_when_hill_good_run_at_hill_venue():
    # 今回: 東京(05)=坂あり / 前走: 中山(06)=坂あり 1着
    horse = _horse(past_races=[_past_at("06", rank=1)])
    race = _race(place_code="05")
    result = check_v2_hill_fit(horse, race, {})
    assert result.passed is True


def test_v2_hill_fit_fails_when_hill_bad_run_at_hill_venue():
    # 今回: 東京(05)=坂あり / 前走: 中山(06)=坂あり 5着
    horse = _horse(past_races=[_past_at("06", rank=5)])
    race = _race(place_code="05")
    result = check_v2_hill_fit(horse, race, {})
    assert result.passed is False


def test_v2_hill_fit_neutral_when_no_hill_experience():
    # 今回: 東京(05)=坂あり / 前走: 新潟(04)=坂なし
    horse = _horse(past_races=[_past_at("04", rank=1)])
    race = _race(place_code="05")
    result = check_v2_hill_fit(horse, race, {})
    assert result.passed is None


def test_v2_hill_fit_neutral_when_past_place_code_missing():
    past = PastRaceInfo(
        race_id="P1", date="2026-05-01", rank=1, distance=1800, surface="ダート",
        head_count=10, race_name="前走", class_score=None, time_score=None,
        member_level_score=None, opponents_next_races=[], place_code=None,
    )
    horse = _horse(past_races=[past])
    result = check_v2_hill_fit(horse, _race(place_code="05"), {})
    assert result.passed is None


def test_v2_hill_fit_flat_venue_good_run_at_flat():
    # 今回: 新潟(04)=坂なし / 前走: 小倉(10)=坂なし 2着
    horse = _horse(past_races=[_past_at("10", rank=2)])
    race = _race(place_code="04")
    result = check_v2_hill_fit(horse, race, {})
    assert result.passed is True


# ── v2_sire_venue ─────────────────────────────────────────────────────────


def test_v2_sire_venue_passes_when_venue_rate_above_overall():
    horse = _horse(sire_venue_top3={"overall": 0.30, "05": 0.42})
    race = _race(place_code="05")
    result = check_v2_sire_venue(horse, race, {})
    assert result.passed is True
    assert result.score > 0


def test_v2_sire_venue_fails_when_venue_rate_below_overall():
    horse = _horse(sire_venue_top3={"overall": 0.35, "05": 0.28})
    race = _race(place_code="05")
    result = check_v2_sire_venue(horse, race, {})
    assert result.passed is False


def test_v2_sire_venue_neutral_when_no_data():
    horse = _horse(sire_venue_top3=None)
    result = check_v2_sire_venue(horse, _race(place_code="05"), {})
    assert result.passed is None


def test_v2_sire_venue_neutral_when_venue_count_too_low():
    # place_code "05" が辞書にない = count < 10 でフィルタ済み → None
    horse = _horse(sire_venue_top3={"overall": 0.30})
    race = _race(place_code="05")
    result = check_v2_sire_venue(horse, race, {})
    assert result.passed is None


def test_v2_sire_venue_neutral_when_place_code_missing():
    horse = _horse(sire_venue_top3={"overall": 0.30, "05": 0.40})
    race = _race(place_code=None)
    result = check_v2_sire_venue(horse, race, {})
    assert result.passed is None


# ── v2_pace_match ────────────────────────────────────────────────────────────


def _horse_with_tendency(tend: float, **overrides) -> HorseContext:
    return _horse(position_tendency=tend, **overrides)


def _race_with_horses(horses: list[HorseContext], **overrides) -> RaceContext:
    return _race(horses=horses, **overrides)


def test_v2_pace_match_solo_front_benefits_front_runner():
    front = _horse_with_tendency(0.1, horse_id="H1")
    others = [_horse_with_tendency(0.7, horse_id=f"H{i}") for i in range(2, 9)]
    race = _race_with_horses([front] + others)
    result = check_v2_pace_match(front, race, {})
    assert result.passed is True
    assert result.score > 0


def test_v2_pace_match_crowded_front_benefits_closer():
    fronts = [_horse_with_tendency(0.15, horse_id=f"H{i}") for i in range(1, 5)]
    closer = _horse_with_tendency(0.75, horse_id="H5")
    rest   = [_horse_with_tendency(0.8, horse_id=f"H{i}") for i in range(6, 9)]
    race   = _race_with_horses(fronts + [closer] + rest)
    result = check_v2_pace_match(closer, race, {})
    assert result.passed is True


def test_v2_pace_match_solo_front_penalises_closer():
    front  = _horse_with_tendency(0.1, horse_id="H1")
    closer = _horse_with_tendency(0.8, horse_id="H2")
    rest   = [_horse_with_tendency(0.7, horse_id=f"H{i}") for i in range(3, 9)]
    race   = _race_with_horses([front, closer] + rest)
    result = check_v2_pace_match(closer, race, {})
    assert result.passed is False


def test_v2_pace_match_neutral_when_no_tendency():
    horse = _horse(position_tendency=None)
    race  = _race_with_horses([horse])
    result = check_v2_pace_match(horse, race, {})
    assert result.passed is None


def test_v2_pace_match_neutral_when_insufficient_data():
    horse = _horse_with_tendency(0.1, horse_id="H1")
    race  = _race_with_horses([horse])  # only 1 horse with data (< 3)
    result = check_v2_pace_match(horse, race, {})
    assert result.passed is None


# ── v2_bracket_bias ──────────────────────────────────────────────────────────


def test_v2_bracket_bias_inner_favored_inner_horse():
    horse = _horse(wakuban=2)
    race  = _race(inner_bias_pit=0.3, bias_source="track_bias_pit", surface="芝")
    result = check_v2_bracket_bias(horse, race, {})
    assert result.passed is True
    assert result.score > 0


def test_v2_bracket_bias_inner_favored_outer_horse():
    horse = _horse(wakuban=8)
    race  = _race(inner_bias_pit=0.3, bias_source="track_bias_pit", surface="芝")
    result = check_v2_bracket_bias(horse, race, {})
    assert result.passed is False


def test_v2_bracket_bias_outer_favored_outer_horse():
    horse = _horse(wakuban=8)
    race  = _race(inner_bias_pit=-0.25, bias_source="course_profile_store", surface="芝")
    result = check_v2_bracket_bias(horse, race, {})
    assert result.passed is True


def test_v2_bracket_bias_neutral_when_bias_small():
    horse = _horse(wakuban=1)
    race  = _race(inner_bias_pit=0.05, bias_source="track_bias_pit", surface="芝")
    result = check_v2_bracket_bias(horse, race, {})
    assert result.passed is None


def test_v2_bracket_bias_dirt_inner_penalty_applied():
    horse = _horse(wakuban=2)
    race  = _race(inner_bias_pit=None, bias_source="none", surface="ダート")
    result = check_v2_bracket_bias(horse, race, {"dirt_inner_penalty": True})
    assert result.passed is False


def test_v2_bracket_bias_no_wakuban_is_none():
    horse = _horse(wakuban=None)
    result = check_v2_bracket_bias(horse, _race(), {})
    assert result.passed is None


# ── v2_race_order ────────────────────────────────────────────────────────────


def test_v2_race_order_late_race_closer_benefits():
    horse = _horse(position_tendency=0.7)
    race  = _race(race_id="202506280109")  # R09
    result = check_v2_race_order(horse, race, {})
    assert result.passed is True
    assert result.score > 0


def test_v2_race_order_late_race_front_runner_penalised():
    horse = _horse(position_tendency=0.2)
    race  = _race(race_id="202506280112")  # R12
    result = check_v2_race_order(horse, race, {})
    assert result.passed is False


def test_v2_race_order_early_race_is_neutral():
    horse = _horse(position_tendency=0.7)
    race  = _race(race_id="202506280105")  # R05
    result = check_v2_race_order(horse, race, {})
    assert result.passed is None


def test_v2_race_order_invalid_race_id_is_none():
    horse = _horse(position_tendency=0.7)
    race  = _race(race_id="")
    result = check_v2_race_order(horse, race, {})
    assert result.passed is None


def test_v2_race_order_no_tendency_late_race_is_none():
    horse = _horse(position_tendency=None)
    race  = _race(race_id="202506280110")
    result = check_v2_race_order(horse, race, {})
    assert result.passed is None


# ── v2_opponent_winners ───────────────────────────────────────────────────────


def _prev_with_opponents(opps: list[tuple[str, int | None]]) -> PastRaceInfo:
    opponents = [
        PastRaceOpponent(horse_id=hid, this_rank=i + 1, this_margin=0.1 * i, next_race_rank=nxt)
        for i, (hid, nxt) in enumerate(opps)
    ]
    return PastRaceInfo(
        race_id="PR1", date="2026-05-01", rank=5, distance=1600, surface="芝",
        head_count=8, race_name="前走", class_score=10.0, time_score=10.0,
        member_level_score=10.0, opponents_next_races=opponents,
    )


def test_v2_opponent_winners_passes_when_enough_winners():
    prev = _prev_with_opponents([
        ("A", 1), ("B", 1), ("C", 2), ("D", 3), ("E", 4),
    ])
    horse = _horse(past_races=[prev])
    result = check_v2_opponent_winners(horse, _race(), {})
    assert result.passed is True
    assert result.detail["winners"] == 2


def test_v2_opponent_winners_fails_when_few_winners():
    prev = _prev_with_opponents([
        ("A", 5), ("B", 6), ("C", 7), ("D", 8), ("E", 9),
    ])
    horse = _horse(past_races=[prev])
    result = check_v2_opponent_winners(horse, _race(), {})
    assert result.passed is False
    assert result.detail["winners"] == 0


def test_v2_opponent_winners_neutral_when_few_known():
    prev = _prev_with_opponents([
        ("A", None), ("B", None), ("C", None),
    ])
    horse = _horse(past_races=[prev])
    result = check_v2_opponent_winners(horse, _race(), {})
    assert result.passed is None


def test_v2_opponent_winners_excludes_unknown_next_rank():
    prev = _prev_with_opponents([
        ("A", 1), ("B", None), ("C", 2), ("D", None), ("E", 3),
    ])
    horse = _horse(past_races=[prev])
    # min_known=4 だが known=3 のため保留
    result = check_v2_opponent_winners(horse, _race(), {"min_known": 4})
    assert result.passed is None


def test_v2_opponent_winners_no_past_races_is_none():
    horse = _horse(past_races=[])
    result = check_v2_opponent_winners(horse, _race(), {})
    assert result.passed is None
