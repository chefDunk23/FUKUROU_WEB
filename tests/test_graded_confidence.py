"""
tests/test_graded_confidence.py
==================================
重賞用confidence判定（pace_bias_ai/features/graded_confidence.py +
scripts/generate_ai_picks.py の is_graded 分岐）の回帰テスト。
DB接続不要（純粋関数のみ）。

対象:
  - is_graded_race: grade_code A/B/C/L/E 判定
  - classify_class_transition / class_transition_is_positive: クラス移動分類
  - is_excuse_margin_eligible: 度外視（前走G1/G2 かつ 着差0.5秒以内）
  - is_age_veteran: 高齢（7歳以上）
  - _compute_confidence: is_graded=False で標準ロジックが完全に不変であること
  - _compute_confidence_graded: 重賞専用ロジックの各条件の効き方
  - _TIME_DIFF_RE / 着差変換ロジック

docs/validation/GRADED_CONFIDENCE_ANALYSIS.md 参照。
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from pace_bias_ai.features.graded_confidence import (
    class_transition_is_positive,
    classify_class_transition,
    is_age_veteran,
    is_excuse_margin_eligible,
    is_graded_race,
)
from scripts.generate_ai_picks import (
    _TIME_DIFF_RE,
    _compute_confidence,
    _compute_confidence_graded,
)


# ─────────────────────────────────────────────────────────────────────────────
# is_graded_race
# ─────────────────────────────────────────────────────────────────────────────

class TestIsGradedRace:
    @pytest.mark.parametrize("grade_code", ["A", "B", "C", "L", "E", "a", "e"])
    def test_graded_codes_return_true(self, grade_code):
        assert is_graded_race(grade_code) is True

    @pytest.mark.parametrize("grade_code", ["D", "F", "G", "H", None, "", "  "])
    def test_non_graded_codes_return_false(self, grade_code):
        assert is_graded_race(grade_code) is False


# ─────────────────────────────────────────────────────────────────────────────
# classify_class_transition / class_transition_is_positive
# ─────────────────────────────────────────────────────────────────────────────

class TestClassifyClassTransition:
    def test_negative_class_vs_best_is_downgrade(self):
        """class_vs_best < 0 = 今走が過去最高より低クラス → 格下げ。"""
        assert classify_class_transition(-1, 2) == "downgrade"

    def test_zero_class_vs_best_is_same(self):
        assert classify_class_transition(0, 2) == "same"

    def test_positive_with_graded_history_is_upgrade(self):
        """過去にG1/G2/G3/L経験あり(best_class_rank<=5)での格上挑戦。"""
        assert classify_class_transition(1, 3) == "upgrade"

    def test_positive_with_no_graded_history_is_from_conditions(self):
        """過去に重賞経験なし(best_class_rank==6=デフォルト)からの重賞挑戦。"""
        assert classify_class_transition(5, 6) == "from_conditions"

    def test_missing_class_vs_best_returns_none(self):
        assert classify_class_transition(None, 2) is None
        assert classify_class_transition(np.nan, 2) is None

    def test_missing_best_class_rank_returns_none(self):
        assert classify_class_transition(1, None) is None
        assert classify_class_transition(1, np.nan) is None


class TestClassTransitionIsPositive:
    def test_downgrade_and_same_are_positive(self):
        assert class_transition_is_positive("downgrade") is True
        assert class_transition_is_positive("same") is True

    def test_upgrade_and_from_conditions_are_negative(self):
        assert class_transition_is_positive("upgrade") is False
        assert class_transition_is_positive("from_conditions") is False

    def test_none_returns_none(self):
        assert class_transition_is_positive(None) is None


# ─────────────────────────────────────────────────────────────────────────────
# is_excuse_margin_eligible
# ─────────────────────────────────────────────────────────────────────────────

class TestIsExcuseMarginEligible:
    def test_g1_within_half_second_is_eligible(self):
        assert is_excuse_margin_eligible("A", 0.3) is True

    def test_g1_exactly_half_second_is_eligible(self):
        """境界値: 0.5秒ちょうどは該当(<=)。"""
        assert is_excuse_margin_eligible("A", 0.5) is True

    def test_g1_over_half_second_is_not_eligible(self):
        assert is_excuse_margin_eligible("A", 0.6) is False

    def test_g2_within_half_second_is_eligible(self):
        assert is_excuse_margin_eligible("B", 0.4) is True

    def test_g3_is_not_eligible_regardless_of_margin(self):
        """G3(C)は対象外（G1/G2のみ）。"""
        assert is_excuse_margin_eligible("C", 0.1) is False

    def test_missing_grade_code_is_not_eligible(self):
        assert is_excuse_margin_eligible(None, 0.2) is False

    def test_missing_margin_is_not_eligible(self):
        assert is_excuse_margin_eligible("A", None) is False

    def test_nan_margin_is_not_eligible(self):
        assert is_excuse_margin_eligible("A", float("nan")) is False


# ─────────────────────────────────────────────────────────────────────────────
# is_age_veteran
# ─────────────────────────────────────────────────────────────────────────────

class TestIsAgeVeteran:
    def test_age_6_is_not_veteran(self):
        assert is_age_veteran(6) is False

    def test_age_7_is_veteran(self):
        assert is_age_veteran(7) is True

    def test_age_10_is_veteran(self):
        assert is_age_veteran(10) is True

    def test_none_is_not_veteran(self):
        assert is_age_veteran(None) is False


# ─────────────────────────────────────────────────────────────────────────────
# time_diff 正規表現（度外視の着差変換）
# ─────────────────────────────────────────────────────────────────────────────

class TestTimeDiffRegex:
    @pytest.mark.parametrize("raw,expected_sec", [
        ("+024", 2.4),
        ("-000", 0.0),
        ("+002", 0.2),
        ("+500", 50.0),
    ])
    def test_valid_time_diff_converts_to_seconds(self, raw, expected_sec):
        assert _TIME_DIFF_RE.match(raw) is not None
        assert abs(abs(int(raw)) / 10.0 - expected_sec) < 1e-9

    @pytest.mark.parametrize("raw", ["", "abc", "024", "+02.4", None])
    def test_invalid_time_diff_does_not_match(self, raw):
        if raw is None:
            assert _TIME_DIFF_RE.match("") is None
        else:
            assert _TIME_DIFF_RE.match(raw) is None


# ─────────────────────────────────────────────────────────────────────────────
# _compute_confidence: is_graded=False で標準ロジックが完全に不変であること
# ─────────────────────────────────────────────────────────────────────────────

def _v1_row(avg_rank_3=None) -> pd.Series:
    return pd.Series({"avg_rank_3": avg_rank_3 if avg_rank_3 is not None else np.nan})


class TestStandardConfidenceUnaffectedByGradedBranch:
    """通常レースの判定ロジックに一切影響を出さないことの回帰テスト
    （分岐の追加のみで、is_graded=False時のスコアが従来と完全一致すること）。"""

    def test_default_is_graded_false_matches_explicit_false(self):
        """is_graded引数を省略した場合と明示的にFalseを渡した場合が同じ結果になること。"""
        flags = {"is_genuine": 1, "is_step": 0, "won_and_classup": 0, "transport_flag": 0}
        race_meta = {"distance": 2000, "keibajo_code": "05"}
        row = _v1_row(avg_rank_3=2.0)

        default_result = _compute_confidence(flags, race_meta, row)
        explicit_result = _compute_confidence(flags, race_meta, row, is_graded=False)
        assert default_result == explicit_result

    def test_standard_negative_flags_still_apply_when_not_graded(self):
        """is_graded=False の場合、is_step/transport_flag は従来通りネガ判定に使われる。"""
        flags_with_neg = {"is_genuine": 0, "is_step": 1, "won_and_classup": 0, "transport_flag": 1}
        flags_without_neg = {"is_genuine": 0, "is_step": 0, "won_and_classup": 0, "transport_flag": 0}
        race_meta = {"distance": 1200, "keibajo_code": "01"}
        row = _v1_row()

        score_with_neg, _ = _compute_confidence(flags_with_neg, race_meta, row, is_graded=False)
        score_without_neg, _ = _compute_confidence(flags_without_neg, race_meta, row, is_graded=False)
        # is_step + transport_flag の2つがネガ判定される分、スコアが下がる
        assert score_with_neg < score_without_neg

    def test_graded_extra_is_ignored_when_not_graded(self):
        """is_graded=False の場合、graded_extra を渡してもスコアに一切影響しないこと。"""
        flags = {"is_genuine": 1, "is_step": 0, "won_and_classup": 0, "transport_flag": 0}
        race_meta = {"distance": 2000, "keibajo_code": "05"}
        row = _v1_row(avg_rank_3=2.0)

        without_extra = _compute_confidence(flags, race_meta, row, is_graded=False)
        with_extra = _compute_confidence(
            flags, race_meta, row, is_graded=False,
            graded_extra={"training_condition1": True, "excuse_margin": True, "age_veteran": True},
        )
        assert without_extra == with_extra


# ─────────────────────────────────────────────────────────────────────────────
# _compute_confidence_graded: 重賞専用ロジックの効き方
# ─────────────────────────────────────────────────────────────────────────────

class TestGradedConfidenceBranch:
    def test_is_step_and_transport_flag_are_ignored_in_graded_branch(self):
        """重賞では is_step / transport_flag がスコアに影響しないこと（無効化条件）。"""
        base_flags = {"won_and_classup": 0, "class_vs_best": np.nan, "best_class_rank": np.nan}
        flags_with_neg = {**base_flags, "is_step": 1, "transport_flag": 1, "is_genuine": 0}
        flags_without_neg = {**base_flags, "is_step": 0, "transport_flag": 0, "is_genuine": 0}
        race_meta = {"distance": 2000, "keibajo_code": "05"}
        row = _v1_row()

        score_with, _ = _compute_confidence_graded(flags_with_neg, race_meta, row, {})
        score_without, _ = _compute_confidence_graded(flags_without_neg, race_meta, row, {})
        assert score_with == score_without

    def test_is_genuine_is_ignored_in_graded_branch(self):
        """重賞では is_genuine がスコアに影響しないこと（無効化条件）。"""
        base_flags = {"won_and_classup": 0, "is_step": 0, "transport_flag": 0,
                       "class_vs_best": np.nan, "best_class_rank": np.nan}
        race_meta = {"distance": 2000, "keibajo_code": "05"}
        row = _v1_row()

        score_genuine, _ = _compute_confidence_graded({**base_flags, "is_genuine": 1}, race_meta, row, {})
        score_not_genuine, _ = _compute_confidence_graded({**base_flags, "is_genuine": 0}, race_meta, row, {})
        assert score_genuine == score_not_genuine

    def test_won_and_classup_still_applies_in_graded_branch(self):
        """won_and_classup は重賞でも据え置きでネガ判定されること。"""
        base_flags = {"is_step": 0, "transport_flag": 0, "is_genuine": 0,
                       "class_vs_best": np.nan, "best_class_rank": np.nan}
        race_meta = {"distance": 2000, "keibajo_code": "05"}
        row = _v1_row()

        score_won, _ = _compute_confidence_graded({**base_flags, "won_and_classup": 1}, race_meta, row, {})
        score_not_won, _ = _compute_confidence_graded({**base_flags, "won_and_classup": 0}, race_meta, row, {})
        assert score_won < score_not_won

    def test_downgrade_class_transition_adds_point(self):
        flags_downgrade = {"won_and_classup": 0, "is_step": 0, "transport_flag": 0, "is_genuine": 0,
                            "class_vs_best": -1, "best_class_rank": 2}
        flags_none = {"won_and_classup": 0, "is_step": 0, "transport_flag": 0, "is_genuine": 0,
                      "class_vs_best": np.nan, "best_class_rank": np.nan}
        race_meta = {"distance": 2000, "keibajo_code": "05"}
        row = _v1_row()

        score_downgrade, _ = _compute_confidence_graded(flags_downgrade, race_meta, row, {})
        score_none, _ = _compute_confidence_graded(flags_none, race_meta, row, {})
        assert score_downgrade > score_none

    def test_from_conditions_class_transition_subtracts_point(self):
        flags_from_cond = {"won_and_classup": 0, "is_step": 0, "transport_flag": 0, "is_genuine": 0,
                            "class_vs_best": 4, "best_class_rank": 6}
        flags_none = {"won_and_classup": 0, "is_step": 0, "transport_flag": 0, "is_genuine": 0,
                      "class_vs_best": np.nan, "best_class_rank": np.nan}
        race_meta = {"distance": 2000, "keibajo_code": "05"}
        row = _v1_row()

        score_from_cond, _ = _compute_confidence_graded(flags_from_cond, race_meta, row, {})
        score_none, _ = _compute_confidence_graded(flags_none, race_meta, row, {})
        assert score_from_cond < score_none

    def test_training_condition1_adds_point(self):
        flags = {"won_and_classup": 0, "is_step": 0, "transport_flag": 0, "is_genuine": 0,
                 "class_vs_best": np.nan, "best_class_rank": np.nan}
        race_meta = {"distance": 2000, "keibajo_code": "05"}
        row = _v1_row()

        score_with, _ = _compute_confidence_graded(flags, race_meta, row, {"training_condition1": True})
        score_without, _ = _compute_confidence_graded(flags, race_meta, row, {"training_condition1": False})
        assert score_with == score_without + 1

    def test_excuse_margin_adds_point(self):
        flags = {"won_and_classup": 0, "is_step": 0, "transport_flag": 0, "is_genuine": 0,
                 "class_vs_best": np.nan, "best_class_rank": np.nan}
        race_meta = {"distance": 2000, "keibajo_code": "05"}
        row = _v1_row()

        score_with, _ = _compute_confidence_graded(flags, race_meta, row, {"excuse_margin": True})
        score_without, _ = _compute_confidence_graded(flags, race_meta, row, {"excuse_margin": False})
        assert score_with == score_without + 1

    def test_age_veteran_subtracts_point(self):
        flags = {"won_and_classup": 0, "is_step": 0, "transport_flag": 0, "is_genuine": 0,
                 "class_vs_best": np.nan, "best_class_rank": np.nan}
        race_meta = {"distance": 2000, "keibajo_code": "05"}
        row = _v1_row()

        score_with, _ = _compute_confidence_graded(flags, race_meta, row, {"age_veteran": True})
        score_without, _ = _compute_confidence_graded(flags, race_meta, row, {"age_veteran": False})
        assert score_with == score_without - 1

    def test_compute_confidence_dispatches_to_graded_when_is_graded_true(self):
        """_compute_confidence(is_graded=True) が _compute_confidence_graded と
        同一結果を返すこと（ディスパッチの正しさ）。"""
        flags = {"won_and_classup": 1, "is_step": 1, "transport_flag": 1, "is_genuine": 1,
                 "class_vs_best": -1, "best_class_rank": 2}
        race_meta = {"distance": 2000, "keibajo_code": "05"}
        row = _v1_row(avg_rank_3=2.0)
        extra = {"training_condition1": True, "excuse_margin": True, "age_veteran": True}

        via_dispatch = _compute_confidence(flags, race_meta, row, is_graded=True, graded_extra=extra)
        direct = _compute_confidence_graded(flags, race_meta, row, extra)
        assert via_dispatch == direct

    def test_label_thresholds_same_as_standard(self):
        """A(>=3)/B(1-2)/C(<=0) の閾値が標準レースと同一であること。"""
        flags = {"won_and_classup": 0, "is_step": 0, "transport_flag": 0, "is_genuine": 0,
                 "class_vs_best": np.nan, "best_class_rank": np.nan}
        race_meta = {"distance": 2000, "keibajo_code": "05"}
        row = _v1_row(avg_rank_3=2.0)
        # 得意セグメント+1, 近走好成績+1, ネガ条件ゼロ+1 = 3点 → A
        score, label = _compute_confidence_graded(flags, race_meta, row, {})
        assert score == 3
        assert label == "A"
