"""
tests/test_tipster_combo_backtest.py
=====================================
BET-3 実装のユニットテスト。

DB 依存なしで検証できる関数（組み合わせ生成・集計ロジック）を対象とする。
run_combo_backtest() は DB 接続を必要とするため本テストでは対象外。

PLAN.md §5-3 BET-3 Blocker 確認項目:
- 組み合わせ数の数学的正しさ（馬連 N 点、三連複 C(N,2) 点）
- 回収率に race_count / bet_count が同じ階層で出力されること
- N/A（データ欠損）と不的中（リターン 0）が正しく区別されること
- サンプル数が少なくても return_rate を除外・null化しないこと
"""
import pytest

from tipster.combo_backtest import (
    _COMBO_BET_TYPES,
    _accumulate_stats,
    _combo_str,
    _new_acc,
    _to_combo_stats,
    gen_sanrenfuku_combos,
    gen_umaren_combos,
    gen_wide_combos,
)
from tipster.models import ComboStats


# ─────────────────────────────────────────────────────────────────────────
# _combo_str
# ─────────────────────────────────────────────────────────────────────────


class TestComboStr:
    def test_single_horse_two_digits(self):
        assert _combo_str(11) == "11"

    def test_single_horse_zero_padded(self):
        assert _combo_str(1) == "01"

    def test_two_horses_sorted_ascending(self):
        assert _combo_str(11, 6) == "06-11"

    def test_two_horses_already_in_order(self):
        assert _combo_str(6, 11) == "06-11"

    def test_three_horses_sorted(self):
        assert _combo_str(11, 6, 10) == "06-10-11"

    def test_two_horses_both_padded(self):
        assert _combo_str(1, 2) == "01-02"


# ─────────────────────────────────────────────────────────────────────────
# gen_umaren_combos
# ─────────────────────────────────────────────────────────────────────────


class TestGenUmarenCombos:
    def test_n_aite_gives_n_combos(self):
        assert len(gen_umaren_combos(11, [6, 10, 14])) == 3

    def test_single_aite_sorted(self):
        assert gen_umaren_combos(11, [6]) == ["06-11"]

    def test_honmei_lower_than_aite(self):
        assert gen_umaren_combos(3, [11]) == ["03-11"]

    def test_empty_aite_returns_empty(self):
        assert gen_umaren_combos(11, []) == []

    def test_all_combos_contain_honmei(self):
        combos = gen_umaren_combos(11, [6, 10, 14])
        for combo in combos:
            parts = [int(p) for p in combo.split("-")]
            assert 11 in parts


# ─────────────────────────────────────────────────────────────────────────
# gen_wide_combos
# ─────────────────────────────────────────────────────────────────────────


class TestGenWideCombos:
    def test_same_format_as_umaren(self):
        assert gen_wide_combos(11, [6, 10]) == gen_umaren_combos(11, [6, 10])

    def test_n_aite_gives_n_combos(self):
        assert len(gen_wide_combos(5, [1, 2, 3, 4])) == 4


# ─────────────────────────────────────────────────────────────────────────
# gen_sanrenfuku_combos
# ─────────────────────────────────────────────────────────────────────────


class TestGenSanrenfukuCombos:
    def test_empty_when_zero_aite(self):
        assert gen_sanrenfuku_combos(11, []) == []

    def test_empty_when_one_aite(self):
        assert gen_sanrenfuku_combos(11, [6]) == []

    def test_c_2_2_equals_1_combo(self):
        combos = gen_sanrenfuku_combos(11, [6, 10])
        assert len(combos) == 1
        assert combos == ["06-10-11"]

    def test_c_3_2_equals_3_combos(self):
        combos = gen_sanrenfuku_combos(11, [6, 10, 14])
        assert len(combos) == 3

    def test_c_4_2_equals_6_combos(self):
        combos = gen_sanrenfuku_combos(11, [1, 2, 3, 4])
        assert len(combos) == 6

    def test_honmei_present_in_all_combos(self):
        combos = gen_sanrenfuku_combos(11, [6, 10, 14])
        for combo in combos:
            parts = [int(p) for p in combo.split("-")]
            assert 11 in parts

    def test_each_combo_has_three_horses(self):
        combos = gen_sanrenfuku_combos(11, [6, 10, 14])
        for combo in combos:
            assert len(combo.split("-")) == 3


# ─────────────────────────────────────────────────────────────────────────
# ComboStats モデル
# ─────────────────────────────────────────────────────────────────────────


class TestComboStatsModel:
    def test_default_values(self):
        s = ComboStats()
        assert s.race_count == 0
        assert s.bet_count == 0
        assert s.hit_count == 0
        assert s.return_amount == 0
        assert s.return_rate == 0.0
        assert s.na_race_count == 0

    def test_fields_are_independent_per_instance(self):
        s1 = ComboStats(race_count=5, bet_count=10)
        s2 = ComboStats()
        assert s2.race_count == 0


# ─────────────────────────────────────────────────────────────────────────
# _accumulate_stats
# ─────────────────────────────────────────────────────────────────────────


class TestAccumulateStats:
    def _full_payout_map(self, tansho=550, fukusho=200, umaren=4800, wide=1200, sanrenpuku=8000):
        """テスト用フル payout_map（全賭式データあり）。honmei=11, aite=[6, 10] 想定。"""
        return {
            "tansho": {"11": tansho},
            "fukusho": {"11": fukusho},
            "umaren": {"06-11": umaren},
            "wide": {"06-11": wide, "10-11": 900},
            "sanrenpuku": {"06-10-11": sanrenpuku},
        }

    def test_tansho_hit(self):
        acc = _new_acc()
        _accumulate_stats(acc, 11, [6], self._full_payout_map())
        assert acc["tansho"]["race_count"] == 1
        assert acc["tansho"]["bet_count"] == 1
        assert acc["tansho"]["hit_count"] == 1
        assert acc["tansho"]["return_amount"] == 550

    def test_tansho_miss(self):
        acc = _new_acc()
        # honmei=11 だが tansho combination="03"（別の馬）が的中
        payout_map = {"tansho": {"03": 800}, "fukusho": {"03": 300, "07": 200},
                      "umaren": {}, "wide": {}, "sanrenpuku": {}}
        _accumulate_stats(acc, 11, [6], payout_map)
        assert acc["tansho"]["bet_count"] == 1
        assert acc["tansho"]["hit_count"] == 0
        assert acc["tansho"]["return_amount"] == 0

    def test_na_when_payout_map_is_none(self):
        """レース全体のデータ欠損は組み合わせが生成できた賭式を全て N/A として計上する。
        sanrenpuku は相手 2 頭以上の場合のみ組み合わせが生成されるため、
        2 頭 aite で確認する。"""
        acc = _new_acc()
        _accumulate_stats(acc, 11, [6, 10], None)  # 2 aite → sanrenpuku も対象
        for bt in ("tansho", "fukusho", "umaren", "wide", "sanrenpuku"):
            assert acc[bt]["na_race_count"] == 1, f"{bt} should be N/A"
            assert acc[bt]["race_count"] == 0
            assert acc[bt]["bet_count"] == 0

    def test_na_when_payout_map_is_none_single_aite(self):
        """相手 1 頭の場合、三連複は組み合わせ未生成のためカウントしない（N/A でもない）。"""
        acc = _new_acc()
        _accumulate_stats(acc, 11, [6], None)  # 1 aite → sanrenpuku スキップ
        for bt in ("tansho", "fukusho", "umaren", "wide"):
            assert acc[bt]["na_race_count"] == 1
        assert acc["sanrenpuku"]["na_race_count"] == 0  # 組み合わせ生成不可 ≠ データ欠損

    def test_na_when_bet_type_missing(self):
        """賭式単位のデータ欠損は該当賭式のみ N/A として計上する（2 aite で確認）。"""
        acc = _new_acc()
        _accumulate_stats(acc, 11, [6, 10], {"tansho": {"11": 550}})
        assert acc["tansho"]["race_count"] == 1
        assert acc["tansho"]["na_race_count"] == 0
        assert acc["fukusho"]["na_race_count"] == 1
        assert acc["umaren"]["na_race_count"] == 1
        assert acc["wide"]["na_race_count"] == 1
        assert acc["sanrenpuku"]["na_race_count"] == 1

    def test_sanrenpuku_skipped_with_one_aite(self):
        """相手が 1 頭では三連複の組み合わせが生成できないためカウントしない。"""
        acc = _new_acc()
        payout_map = {bt: {} for bt in ("tansho", "fukusho", "umaren", "wide", "sanrenpuku")}
        payout_map["tansho"]["11"] = 550
        _accumulate_stats(acc, 11, [6], payout_map)  # aite 1頭 → sanrenpuku なし
        assert acc["sanrenpuku"]["race_count"] == 0
        assert acc["sanrenpuku"]["bet_count"] == 0
        assert acc["sanrenpuku"]["na_race_count"] == 0

    def test_umaren_n_aite_gives_n_bet_count(self):
        """相手 N 頭で馬連 N 点購入。"""
        acc = _new_acc()
        payout_map = self._full_payout_map()
        payout_map["umaren"]["10-11"] = 3200
        _accumulate_stats(acc, 11, [6, 10], payout_map)
        assert acc["umaren"]["bet_count"] == 2
        assert acc["umaren"]["hit_count"] == 2
        assert acc["umaren"]["return_amount"] == 4800 + 3200

    def test_sanrenpuku_c_n_2_combos(self):
        """相手 3 頭で三連複 C(3,2)=3 点購入。"""
        acc = _new_acc()
        payout_map = self._full_payout_map()
        payout_map["sanrenpuku"]["06-11-14"] = 15000
        payout_map["sanrenpuku"]["10-11-14"] = 12000
        _accumulate_stats(acc, 11, [6, 10, 14], payout_map)
        assert acc["sanrenpuku"]["bet_count"] == 3
        # 06-10-11, 06-11-14, 10-11-14 すべて的中
        assert acc["sanrenpuku"]["hit_count"] == 3

    def test_multiple_races_accumulate(self):
        """複数レース分を累積できること。"""
        acc = _new_acc()
        payout_map = {"tansho": {"11": 550}, "fukusho": {"11": 200},
                      "umaren": {}, "wide": {}, "sanrenpuku": {}}
        _accumulate_stats(acc, 11, [6], payout_map)
        _accumulate_stats(acc, 11, [6], payout_map)
        assert acc["tansho"]["race_count"] == 2
        assert acc["tansho"]["hit_count"] == 2
        assert acc["tansho"]["return_amount"] == 1100


# ─────────────────────────────────────────────────────────────────────────
# _to_combo_stats
# ─────────────────────────────────────────────────────────────────────────


class TestToComboStats:
    def test_return_rate_calculation(self):
        """return_rate = return_amount / (100 * bet_count)"""
        entry = {
            "race_count": 10, "bet_count": 10,
            "hit_count": 1, "return_amount": 4800, "na_race_count": 2,
        }
        stats = _to_combo_stats(entry)
        assert stats.race_count == 10
        assert stats.bet_count == 10
        assert stats.return_rate == pytest.approx(4800 / 1000, rel=1e-3)
        assert stats.na_race_count == 2

    def test_zero_bet_count_gives_zero_rate(self):
        entry = {
            "race_count": 0, "bet_count": 0,
            "hit_count": 0, "return_amount": 0, "na_race_count": 5,
        }
        stats = _to_combo_stats(entry)
        assert stats.return_rate == 0.0

    def test_over_100_percent_return_not_suppressed(self):
        """PLAN.md BET-3: 回収率 100% 超えを除外・null化してはいけない（可視化優先）。"""
        entry = {
            "race_count": 1, "bet_count": 1,
            "hit_count": 1, "return_amount": 50000, "na_race_count": 0,
        }
        stats = _to_combo_stats(entry)
        assert stats.return_rate > 1.0, "100% 超えは除外してはいけない"
        assert stats.race_count == 1
        assert stats.bet_count == 1

    def test_race_count_and_bet_count_always_present(self):
        """PLAN.md BET-3 Blocker: race_count と bet_count が出力されること。"""
        entry = {
            "race_count": 3, "bet_count": 9,
            "hit_count": 1, "return_amount": 8000, "na_race_count": 1,
        }
        stats = _to_combo_stats(entry)
        # race_count と bet_count が ComboStats フィールドとして存在する（Blocker 要件）
        assert hasattr(stats, "race_count")
        assert hasattr(stats, "bet_count")
        assert stats.race_count == 3
        assert stats.bet_count == 9


# ─────────────────────────────────────────────────────────────────────────
# acc key mapping regression test (BET-3 Blocker Fix)
# ─────────────────────────────────────────────────────────────────────────


class TestAccKeyMapping:
    """_new_acc() のキーが ComboBacktestResult のフィールド参照と一致することを保証する。

    BET-3 Blocker: acc["sanrenfuku"] (存在しない) を参照する KeyError が再発しないこと。
    _COMBO_BET_TYPES のキー "sanrenpuku" が _new_acc() に存在し、
    combo_backtest.py 内でアクセスしていること。
    """

    def test_new_acc_contains_sanrenpuku_not_sanrenfuku(self):
        """_new_acc() のキーに "sanrenpuku" が存在し "sanrenfuku" が存在しないこと。"""
        acc = _new_acc()
        assert "sanrenpuku" in acc, "acc には sanrenpuku キーが必要"
        assert "sanrenfuku" not in acc, "acc に sanrenfuku キーは存在しない（typo防止）"

    def test_combo_bet_types_matches_new_acc_keys(self):
        """_COMBO_BET_TYPES の全要素が _new_acc() のキーとして存在すること。"""
        acc = _new_acc()
        for bt in _COMBO_BET_TYPES:
            assert bt in acc, f"_new_acc() に {bt!r} キーが存在しない"

    def test_to_combo_stats_on_all_new_acc_keys(self):
        """_new_acc() の全キーに対して _to_combo_stats() が例外なく動作すること。"""
        acc = _new_acc()
        for bt in _COMBO_BET_TYPES:
            stats = _to_combo_stats(acc[bt])
            assert stats.return_rate == 0.0
            assert stats.race_count == 0
            assert stats.bet_count == 0
