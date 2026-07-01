"""
tests/test_opponent_model_features.py
========================================
pace_bias_ai/opponent_model/features.py の PIT（point-in-time）フィルタの回帰テスト。

対象:
  - _build_opp_agg(): opp_next_date < _cur_date フィルタ（境界値含む）
  - _build_opp_agg(): opp_bn != _bn（自分自身の除外）
  - build_opponent_features(): 当該レース自身の結果情報が特徴量に混入しないこと

守っている不変条件（コードコメントより）:
  「全特徴量はPIT-safe（当該レース結果を使わない）。
   opponent_next_* は『予測日より前に次走を走った馬のみ』でカウント。」
"""
from __future__ import annotations

import pandas as pd
import pytest

from pace_bias_ai.opponent_model.features import (
    FEATURE_COLS,
    _build_opp_agg,
    build_opponent_features,
)


# ─────────────────────────────────────────────────────────────────────────────
# _build_opp_agg: PIT日付フィルタ + 自分自身の除外（境界値テスト）
# ─────────────────────────────────────────────────────────────────────────────

def _slim_one_target(cur_date: str = "20240101") -> pd.DataFrame:
    """対象馬1頭分の slim（_uid, prev_race_id, _bn, _cur_date）。"""
    return pd.DataFrame({
        "_uid":         [0],
        "prev_race_id": ["PRIOR_RACE"],
        "_bn":          ["TARGET"],
        "_cur_date":    [cur_date],
    })


def _opp_next_row(opp_bn: str, next_date: str, next_chaku: int = 1, prev_chaku: int = 1) -> pd.DataFrame:
    return pd.DataFrame({
        "prev_race_id":    ["PRIOR_RACE"],
        "opp_bn":          [opp_bn],
        "opp_prev_chaku":  [prev_chaku],
        "opp_next_chaku":  [next_chaku],
        "opp_next_date":   [next_date],
    })


class TestBuildOppAggPitDateFilter:
    """opp_next_date < _cur_date の境界値テスト。"""

    def test_opponent_next_race_before_cur_date_is_included(self):
        opp_next = _opp_next_row("OPPONENT", next_date="20231215")  # cur_date=20240101 より前
        agg = _build_opp_agg(_slim_one_target(), opp_next, "prev1")
        assert 0 in agg.index
        assert agg.loc[0, "prev1_opp_count"] == 1

    def test_opponent_next_race_after_cur_date_is_excluded(self):
        opp_next = _opp_next_row("OPPONENT", next_date="20240115")  # cur_date=20240101 より後
        agg = _build_opp_agg(_slim_one_target(), opp_next, "prev1")
        # 生き残る行がゼロ → groupby結果にuid=0が存在しない
        assert 0 not in agg.index

    def test_opponent_next_race_same_day_as_cur_date_is_excluded(self):
        """境界値: 同日は『予測日より前』に該当しないため除外される（< であって <= ではない）。"""
        opp_next = _opp_next_row("OPPONENT", next_date="20240101")  # cur_date と同日
        agg = _build_opp_agg(_slim_one_target(), opp_next, "prev1")
        assert 0 not in agg.index

    def test_opponent_next_race_one_day_before_cur_date_is_included(self):
        """境界値: 1日前ギリギリは含まれる。"""
        opp_next = _opp_next_row("OPPONENT", next_date="20231231")
        agg = _build_opp_agg(_slim_one_target(), opp_next, "prev1")
        assert 0 in agg.index
        assert agg.loc[0, "prev1_opp_count"] == 1

    def test_opponent_with_no_next_race_is_excluded(self):
        """opp_next_date が NaT/欠損（次走なし）の対戦相手は集計対象外。"""
        opp_next = pd.DataFrame({
            "prev_race_id":    ["PRIOR_RACE"],
            "opp_bn":          ["OPPONENT"],
            "opp_prev_chaku":  [1],
            "opp_next_chaku":  [None],
            "opp_next_date":   [None],
        })
        agg = _build_opp_agg(_slim_one_target(), opp_next, "prev1")
        assert 0 not in agg.index


class TestBuildOppAggSelfExclusion:
    """opp_bn != _bn（対象馬自身が対戦相手集計に含まれないこと）。"""

    def test_self_is_excluded_even_with_valid_pit_date(self):
        """自分自身の次走日付が予測日より前でも、自分自身は対戦相手として数えない。"""
        opp_next = _opp_next_row("TARGET", next_date="20231215")  # opp_bn == _bn
        agg = _build_opp_agg(_slim_one_target(), opp_next, "prev1")
        assert 0 not in agg.index

    def test_self_excluded_but_other_opponent_still_counted(self):
        """自分自身と他の対戦相手が混在する場合、自分自身の分だけ除外され他は残る。"""
        opp_next = pd.concat([
            _opp_next_row("TARGET", next_date="20231210", next_chaku=1),
            _opp_next_row("OPPONENT", next_date="20231215", next_chaku=2),
        ], ignore_index=True)
        agg = _build_opp_agg(_slim_one_target(), opp_next, "prev1")
        assert agg.loc[0, "prev1_opp_count"] == 1


class TestBuildOppAggMixedScenario:
    """前・後・同日・自分自身が混在する現実的なケースで、正しい部分集合のみ残ること。"""

    def test_only_before_date_opponents_are_aggregated(self):
        opp_next = pd.concat([
            _opp_next_row("OPP_BEFORE", next_date="20231215", next_chaku=1),  # 含む(top3)
            _opp_next_row("OPP_AFTER",  next_date="20240115", next_chaku=5),  # 除外(未来)
            _opp_next_row("OPP_SAME",   next_date="20240101", next_chaku=2),  # 除外(同日)
            _opp_next_row("TARGET",     next_date="20231220", next_chaku=1),  # 除外(自分自身)
        ], ignore_index=True)
        agg = _build_opp_agg(_slim_one_target(), opp_next, "prev1")
        assert agg.loc[0, "prev1_opp_count"] == 1
        assert agg.loc[0, "prev1_opp_top3_rate"] == pytest.approx(1.0)


# ─────────────────────────────────────────────────────────────────────────────
# build_opponent_features: 当該レース自身の結果情報が混入しないこと
# ─────────────────────────────────────────────────────────────────────────────

_PRIOR2 = "2023110100001"   # 前々走
_PRIOR1 = "2023120100001"   # 前走
_TARGET_RACE = "2024010100001"  # 予測対象（当該）レース


def _make_races_df() -> pd.DataFrame:
    return pd.DataFrame({
        "race_id":            [_PRIOR2, _PRIOR1, _TARGET_RACE],
        "grade_code":         ["", "", ""],
        "jyoken_cd_youngest": ["005", "005", "005"],
        "distance":           [2000, 2000, 2000],
        "track_code":         ["11", "11", "11"],
        "keibajo_code":       ["05", "05", "05"],
        "class_rank":         [7, 7, 7],
    })


def _make_entries_df(target_race_result: int) -> pd.DataFrame:
    """TARGET馬の過去2走+当該レースのentries。当該レースの着順(target_race_result)だけを変える。"""
    return pd.DataFrame({
        "blood_no":          ["TARGET", "TARGET", "TARGET"],
        "race_id":           [_PRIOR2, _PRIOR1, _TARGET_RACE],
        "kakutei_chakujun":  [5, 2, target_race_result],
        "race_time":         [120.5, 118.2, 119.0],
        "kinryo":            [540, 540, 540],
        "horse_age":         [4, 4, 4],
        "horse_weight":      [480, 480, 480],
        "umaban":            [3, 5, 7],
    })


def _make_target_df() -> pd.DataFrame:
    return pd.DataFrame({
        "horse_id":   ["TARGET"],
        "race_id":    [_TARGET_RACE],
        "kinryo":     [540],
        "horse_age":  [4],
    })


class TestNoResultLeakage:
    """当該レース（予測対象レース）自身の確定結果が特徴量に混入しないこと。"""

    def test_own_current_race_result_does_not_change_own_features(self):
        """当該レースの kakutei_chakujun を変えても、対象馬自身の特徴量は変化しない
        （prev1/prev2 は shift(1)/(2) で過去のみを参照する設計のため）。"""
        df_races = _make_races_df()
        df_target = _make_target_df()

        result_a = build_opponent_features(df_target, _make_entries_df(target_race_result=1), df_races)
        result_b = build_opponent_features(df_target, _make_entries_df(target_race_result=15), df_races)

        pd.testing.assert_frame_equal(
            result_a.reset_index(drop=True),
            result_b.reset_index(drop=True),
        )

    def test_feature_cols_do_not_include_raw_current_result_columns(self):
        """FEATURE_COLS に当該レースの生の結果カラム（kakutei_chakujun等）が含まれないこと。"""
        forbidden = {"kakutei_chakujun", "race_time", "nyusen_juni"}
        assert forbidden.isdisjoint(set(FEATURE_COLS))

    def test_prev_rank_reflects_prev1_not_current_race(self):
        """prev_rank は前走（prev1）の着順であり、当該レースの着順ではないこと。"""
        df_races = _make_races_df()
        df_target = _make_target_df()
        df_entries = _make_entries_df(target_race_result=99)  # 当該レースの着順は無関係な値

        result = build_opponent_features(df_target, df_entries, df_races)
        # _PRIOR1 (前走) の着順は 2 → prev_rank は 2 であるべき（99 ではない）
        assert result.iloc[0]["prev_rank"] == 2
