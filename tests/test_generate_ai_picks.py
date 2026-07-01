"""
tests/test_generate_ai_picks.py
==================================
scripts/generate_ai_picks.py（本番AI推奨生成: v1×opponent_v3アンサンブル）の
スモークテスト。DB接続不要（純粋関数 + インメモリDataFrameのみ）。

対象:
  - _blend_normalized: レース内min-max正規化 + alpha=0.5ブレンド
  - field_size 補完: umaban未確定時の field_size_meta(syusso_tosu)フォールバック
    （2026-07-01 8b182c5 で修正されたバグの回帰テスト）
  - compute_unified_rank: rank×confidence_labelの統合推奨ラベル判定
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import pytest

from scripts.generate_ai_picks import (
    _ALPHA,
    _blend_normalized,
    _build_pred_row,
    _compute_v1_features,
    _empty_pace_hist,
    _minmax_within_race,
    compute_unified_rank,
)

# build_layer1_features/build_layer2_features の内部失敗ログでテスト出力が汚れるのを防ぐ
logging.disable(logging.CRITICAL)


# ─────────────────────────────────────────────────────────────────────────────
# _minmax_within_race
# ─────────────────────────────────────────────────────────────────────────────

class TestMinmaxWithinRace:
    def test_normal_range_scaled_to_0_1(self):
        scores = pd.Series([10.0, 20.0, 30.0])
        result = _minmax_within_race(scores)
        assert result.iloc[0] == pytest.approx(0.0)
        assert result.iloc[1] == pytest.approx(0.5)
        assert result.iloc[2] == pytest.approx(1.0)

    def test_all_same_score_falls_back_to_0_5(self):
        """全馬同スコアの場合、0除算を避けて全馬 0.5 を返す。"""
        scores = pd.Series([7.0, 7.0, 7.0])
        result = _minmax_within_race(scores)
        assert (result == 0.5).all()

    def test_single_horse_falls_back_to_0_5(self):
        scores = pd.Series([42.0])
        result = _minmax_within_race(scores)
        assert (result == 0.5).all()

    def test_preserves_index(self):
        scores = pd.Series([1.0, 2.0], index=[10, 20])
        result = _minmax_within_race(scores)
        assert list(result.index) == [10, 20]


# ─────────────────────────────────────────────────────────────────────────────
# _blend_normalized
# ─────────────────────────────────────────────────────────────────────────────

class TestBlendNormalized:
    def test_alpha_0_5_averages_v1_and_opp(self):
        v1  = pd.Series([0.0, 5.0, 10.0])
        opp = pd.Series([10.0, 5.0, 0.0])
        v1_norm, opp_norm, blend = _blend_normalized(v1, opp, alpha=0.5)
        # v1_norm = [0, 0.5, 1], opp_norm = [1, 0.5, 0] → blend = 0.5*v1+0.5*opp = [0.5, 0.5, 0.5]
        assert blend.iloc[0] == pytest.approx(0.5)
        assert blend.iloc[1] == pytest.approx(0.5)
        assert blend.iloc[2] == pytest.approx(0.5)

    def test_default_alpha_is_0_5(self):
        assert _ALPHA == 0.5

    def test_alpha_1_uses_only_v1(self):
        v1  = pd.Series([0.0, 10.0])
        opp = pd.Series([100.0, 0.0])
        v1_norm, opp_norm, blend = _blend_normalized(v1, opp, alpha=1.0)
        pd.testing.assert_series_equal(blend, v1_norm, check_names=False)

    def test_alpha_0_uses_only_opp(self):
        v1  = pd.Series([0.0, 10.0])
        opp = pd.Series([100.0, 0.0])
        v1_norm, opp_norm, blend = _blend_normalized(v1, opp, alpha=0.0)
        pd.testing.assert_series_equal(blend, opp_norm, check_names=False)

    def test_all_same_score_both_sides_yields_0_5_blend(self):
        """全馬同スコア（v1・opp とも）の場合、0.5 の min-max フォールバックが
        alpha ブレンドされても 0.5 のまま。"""
        v1  = pd.Series([3.0, 3.0, 3.0])
        opp = pd.Series([9.0, 9.0, 9.0])
        v1_norm, opp_norm, blend = _blend_normalized(v1, opp, alpha=0.5)
        assert (v1_norm == 0.5).all()
        assert (opp_norm == 0.5).all()
        assert (blend == 0.5).all()

    def test_nan_scores_filled_with_median_before_normalizing(self):
        v1  = pd.Series([1.0, np.nan, 3.0])
        opp = pd.Series([1.0, 1.0, 1.0])
        v1_norm, opp_norm, blend = _blend_normalized(v1, opp, alpha=0.5)
        # NaN は median(=2.0)で補完される → 3値中央 → v1_norm の中央値インデックスは 0.5 になる
        assert not v1_norm.isna().any()
        assert v1_norm.iloc[1] == pytest.approx(0.5)

    def test_blend_values_are_within_0_1_range(self):
        v1  = pd.Series([-5.0, 2.0, 100.0])
        opp = pd.Series([0.0, -3.0, 7.0])
        v1_norm, opp_norm, blend = _blend_normalized(v1, opp, alpha=0.5)
        assert (blend >= 0.0).all() and (blend <= 1.0).all()


# ─────────────────────────────────────────────────────────────────────────────
# field_size 補完（バグ1-2の回帰テスト）
# ─────────────────────────────────────────────────────────────────────────────

def _race_meta(field_size_meta: int) -> dict:
    return {
        "race_id":       "2024122100011",
        "race_date":     "2024-12-21",
        "keibajo_code":  "05",
        "distance":      2000,
        "track_code":    "11",
        "grade_code":    "",
        "field_size":    field_size_meta,
    }


def _entry(horse_id: str, umaban: int) -> dict:
    return {
        "horse_id":     horse_id,
        "umaban":       umaban,
        "horse_name":   horse_id,
        "basis_weight": 55.0,
        "jockey_cd":    "05",
        "horse_age":    4,
        "horse_weight": 480.0,
        "trainer_cd":   "001",
        "sire_id":      "S001",
    }


class TestFieldSizeMetaFallback:
    """2026-07-01 8b182c5 の回帰テスト: 枠番(umaban)未確定時に
    races.syusso_tosu (field_size_meta) から出走頭数を補完すること。

    修正前は umaban が全馬 0（未確定）の場合、
    combined.groupby('race_id')['umaban'].transform('max') が 0 になり、
    16頭立てのレースが field_size=0（2頭立て相当）に潰れていた。
    """

    def test_unconfirmed_umaban_falls_back_to_field_size_meta(self):
        """umaban が全馬0（未確定）→ field_size は syusso_tosu(=16) を使う。"""
        entries = [_entry(f"H{i:03d}", umaban=0) for i in range(1, 17)]
        pred_rows = [_build_pred_row(e, _race_meta(field_size_meta=16)) for e in entries]

        out = _compute_v1_features(pred_rows, _empty_pace_hist())

        assert len(out) == 16
        assert (out["field_size"] == 16.0).all(), (
            "umaban未確定(全馬0)の16頭立てレースで field_size が正しく16に"
            "補完されていない（bug1-2の回帰）"
        )

    def test_confirmed_umaban_does_not_use_meta_fallback(self):
        """umaban が確定済み(1〜8)の場合、syusso_tosu(=16)ではなく実際の頭数(8)を使う。"""
        entries = [_entry(f"H{i:03d}", umaban=i) for i in range(1, 9)]
        pred_rows = [_build_pred_row(e, _race_meta(field_size_meta=16)) for e in entries]

        out = _compute_v1_features(pred_rows, _empty_pace_hist())

        assert len(out) == 8
        assert (out["field_size"] == 8.0).all(), (
            "umaban確定済みなのに field_size_meta へフォールバックしてしまっている"
        )

    def test_partially_confirmed_umaban_uses_actual_max(self):
        """一部の馬だけ umaban が確定していれば(最大値>0)、meta ではなく実測値を使う。"""
        entries = [_entry("H001", umaban=5), _entry("H002", umaban=0)]
        pred_rows = [_build_pred_row(e, _race_meta(field_size_meta=18)) for e in entries]

        out = _compute_v1_features(pred_rows, _empty_pace_hist())

        # 同一レース内 umaban の最大値(5) > 0 のため meta へはフォールバックしない
        assert (out["field_size"] == 5.0).all()


# ─────────────────────────────────────────────────────────────────────────────
# compute_unified_rank
# ─────────────────────────────────────────────────────────────────────────────

class TestComputeUnifiedRank:
    def test_rank1_confidence_a_is_ichioshi(self):
        assert compute_unified_rank(1, "A") == "一押し"

    def test_rank1_confidence_b_is_nioshi(self):
        assert compute_unified_rank(1, "B") == "二押し"

    def test_rank2_confidence_a_is_nioshi(self):
        assert compute_unified_rank(2, "A") == "二押し"

    def test_rank1_confidence_c_is_miokuri(self):
        assert compute_unified_rank(1, "C") == "見送り"

    def test_rank5_confidence_c_is_miokuri(self):
        assert compute_unified_rank(5, "C") == "見送り"

    def test_rank2_confidence_b_is_sanoshi(self):
        assert compute_unified_rank(2, "B") == "三押し"

    def test_rank3_confidence_a_is_sanoshi(self):
        """rank3×Aは『二押し』の特別条件に該当しないため三押し。"""
        assert compute_unified_rank(3, "A") == "三押し"

    def test_rank5_confidence_b_is_sanoshi(self):
        assert compute_unified_rank(5, "B") == "三押し"

    def test_rank6_returns_none(self):
        """rank<=5 の範囲外は無印(None)。"""
        assert compute_unified_rank(6, "A") is None
        assert compute_unified_rank(6, "C") is None

    @pytest.mark.parametrize("rank", [1, 2, 3, 4, 5])
    @pytest.mark.parametrize("label", ["A", "B", "C"])
    def test_always_returns_valid_label_or_none(self, rank, label):
        result = compute_unified_rank(rank, label)
        assert result in {"一押し", "二押し", "三押し", "見送り", None}
