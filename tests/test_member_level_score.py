"""
tests/test_member_level_score.py
==================================
_compute_member_level_score の境界値ユニットテスト。

サンプル < 3 件 → 15.0（中間値）
サンプル >= 3 件 → 実測率 × 30
"""
import sys
import types
from pathlib import Path

# api_v2 を import パスに追加
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

# _compute_member_level_score を直接テストするため、
# 依存の少ない形で取り込む
from api_v2.routers.races import _compute_member_level_score, OpponentResult


def _opp(this_rank: int, next_rank: int | None) -> OpponentResult:
    return OpponentResult(
        horse_id="x",
        this_rank=this_rank,
        this_margin=None,
        next_race_rank=next_rank,
    )


class TestComputeMemberLevelScore:
    # ── サンプル不足 → 中間値 15.0 ────────────────────────────────────────
    def test_empty_returns_midpoint(self):
        assert _compute_member_level_score([]) == 15.0

    def test_one_eligible_returns_midpoint(self):
        # next_race_rank あり・this_rank=1 → eligible=1 件 < 3
        opps = [_opp(1, 1)]
        assert _compute_member_level_score(opps) == 15.0

    def test_two_eligible_returns_midpoint(self):
        opps = [_opp(1, 1), _opp(2, 4)]
        assert _compute_member_level_score(opps) == 15.0

    # ── next_race_rank が None のものは eligible から除外される ────────────
    def test_none_next_rank_excluded(self):
        # eligible になるのは 0 件 → 15.0
        opps = [_opp(1, None), _opp(2, None)]
        assert _compute_member_level_score(opps) == 15.0

    # ── this_rank > 5 は eligible から除外される ────────────────────────
    def test_rank_over5_excluded(self):
        # this_rank=6 は除外 → eligible=0 件 → 15.0
        opps = [_opp(6, 1), _opp(7, 1), _opp(8, 1)]
        assert _compute_member_level_score(opps) == 15.0

    # ── サンプル 3 件以上 → 実測値計算 ───────────────────────────────────
    def test_three_eligible_all_top3(self):
        # 3/3=100% → 30.0
        opps = [_opp(1, 1), _opp(2, 2), _opp(3, 3)]
        assert _compute_member_level_score(opps) == 30.0

    def test_three_eligible_none_top3(self):
        # 0/3=0% → 0.0
        opps = [_opp(1, 4), _opp(2, 5), _opp(3, 6)]
        assert _compute_member_level_score(opps) == 0.0

    def test_three_eligible_partial(self):
        # 2/3 ≈ 66.67% → 20.0
        opps = [_opp(1, 1), _opp(2, 3), _opp(3, 4)]
        assert _compute_member_level_score(opps) == 20.0

    def test_five_eligible_mixed(self):
        # 3/5=60% → 18.0
        opps = [_opp(i + 1, i + 1) for i in range(5)]
        # rank 1,2,3 → top3 / rank 4,5 → not
        assert _compute_member_level_score(opps) == 18.0

    # ── 境界: this_rank=5 は含まれる ──────────────────────────────────────
    def test_rank5_included(self):
        opps = [_opp(5, 1), _opp(5, 1), _opp(5, 4)]  # 2/3 → 20.0
        assert _compute_member_level_score(opps) == 20.0

    # ── 戻り値の上限・下限クリップはなし（0〜30 の範囲内に収まることを確認）─
    def test_score_in_valid_range(self):
        opps = [_opp(1, 1)] * 10  # 10/10=100% → 30.0
        score = _compute_member_level_score(opps)
        assert 0.0 <= score <= 30.0
