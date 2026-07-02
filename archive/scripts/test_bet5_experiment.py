"""
tests/test_bet5_experiment.py
==============================
BET-5: 実験管理レイヤーのユニットテスト。

テスト対象:
  - scripts/compare_strategy_results.py の各ユーティリティ関数
  - tipster/strategies/honmei_v3.json・anaba_v2.json の静的整合性
  - ComboBacktestResult の JSON シリアライズ / デシリアライズ

PLAN.md §3 BET-5 Done 条件確認:
  - 2 つ以上の戦略パターンを切り替えて 4 賭式回収率を並べて比較できること
  - 比較のために Python コードを変更する必要がないこと（戦略ファイルの指定変更のみ）
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import date
from io import StringIO
from pathlib import Path

import pytest

# プロジェクトルートを sys.path に追加
sys.path.insert(0, str(Path(__file__).parent.parent))

from tipster.models import ComboBacktestResult, ComboStats

# compare_strategy_results のユーティリティをテスト対象としてインポート
from scripts.compare_strategy_results import (
    _BET_TYPES_4,
    build_strategy_label,
    collect_result_files,
    format_stats,
    get_combo_stats,
    load_result,
    print_comparison_table,
)

_STRATEGIES_DIR = Path(__file__).parent.parent / "tipster" / "strategies"


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------


def _make_combo_stats(
    race_count: int = 100,
    bet_count: int = 100,
    hit_count: int = 20,
    return_amount: int = 8500,
    return_rate: float = 0.85,
    na_race_count: int = 0,
) -> ComboStats:
    return ComboStats(
        race_count=race_count,
        bet_count=bet_count,
        hit_count=hit_count,
        return_amount=return_amount,
        return_rate=return_rate,
        na_race_count=na_race_count,
    )


def _make_result(
    honmei: str = "honmei_v1",
    aite: str = "anaba_v1",
    period: str = "3m",
    from_date: str = "2026-03-27",
    to_date: str = "2026-06-26",
    **stats_kwargs,
) -> ComboBacktestResult:
    s = _make_combo_stats(**stats_kwargs)
    return ComboBacktestResult(
        honmei_strategy=honmei,
        aite_strategy=aite,
        from_date=from_date,
        to_date=to_date,
        period_label=period,
        total_races=110,
        skipped_races=10,
        tansho=s,
        fukusho=_make_combo_stats(race_count=100, bet_count=100, hit_count=30, return_amount=7000, return_rate=0.70),
        umaren=_make_combo_stats(race_count=100, bet_count=450, hit_count=5, return_amount=3000, return_rate=0.067),
        wide=_make_combo_stats(race_count=100, bet_count=450, hit_count=15, return_amount=3500, return_rate=0.078),
        sanrenfuku=_make_combo_stats(race_count=90, bet_count=700, hit_count=2, return_amount=2000, return_rate=0.029),
        generated_at=date.today().isoformat(),
    )


# ---------------------------------------------------------------------------
# 1. 新規戦略 JSON の静的整合性
# ---------------------------------------------------------------------------


class TestNewStrategyJsons:
    """honmei_v3.json / anaba_v2.json が既存の静的チェックを通過すること。"""

    def _load(self, name: str) -> dict:
        return json.loads((_STRATEGIES_DIR / f"{name}.json").read_text(encoding="utf-8"))

    def test_honmei_v3_exists(self):
        """honmei_v3.json が存在すること。"""
        assert (_STRATEGIES_DIR / "honmei_v3.json").exists()

    def test_anaba_v2_exists(self):
        """anaba_v2.json が存在すること。"""
        assert (_STRATEGIES_DIR / "anaba_v2.json").exists()

    def test_honmei_v3_type_is_honmei(self):
        s = self._load("honmei_v3")
        assert s["type"] == "honmei"

    def test_anaba_v2_type_is_anaba(self):
        s = self._load("anaba_v2")
        assert s["type"] == "anaba"

    def test_honmei_v3_ranking_primary_not_ai_score(self):
        """honmei_v3 の ranking.primary が 'ai_score' でないこと（G5a-2）。"""
        s = self._load("honmei_v3")
        assert s["ranking"]["primary"] != "ai_score"

    def test_anaba_v2_ranking_primary_not_ai_score(self):
        """anaba_v2 の ranking.primary が 'ai_score' でないこと（G5a-2）。"""
        s = self._load("anaba_v2")
        assert s["ranking"]["primary"] != "ai_score"

    def test_honmei_v3_no_ai_score_required_condition(self):
        """honmei_v3 に ai_score 系 condition が required:true で含まれていないこと（G5a-1）。"""
        s = self._load("honmei_v3")
        ai_ids = {"ai_score", "ai_rank", "ai_confidence"}
        required_ids = {
            c["id"] for c in s.get("conditions", [])
            if c.get("required") is True and c.get("enabled", True)
        }
        assert not (required_ids & ai_ids)

    def test_anaba_v2_no_ai_score_required_condition(self):
        """anaba_v2 に ai_score 系 condition が required:true で含まれていないこと（G5a-1）。"""
        s = self._load("anaba_v2")
        ai_ids = {"ai_score", "ai_rank", "ai_confidence"}
        required_ids = {
            c["id"] for c in s.get("conditions", [])
            if c.get("required") is True and c.get("enabled", True)
        }
        assert not (required_ids & ai_ids)

    def test_honmei_v3_no_min_odds_required(self):
        """honmei_v3 に min_odds が required:true で含まれていないこと（BET-2: 本命に相手専用条件を混入しない）。"""
        s = self._load("honmei_v3")
        required_ids = {
            c["id"] for c in s.get("conditions", [])
            if c.get("required") is True and c.get("enabled", True)
        }
        assert "min_odds" not in required_ids

    def test_honmei_v3_track_bias_fit_is_required(self):
        """honmei_v3 は track_bias_fit が required:true であること（v3 のキーとなる差別化条件）。"""
        s = self._load("honmei_v3")
        track_bias = next(
            (c for c in s.get("conditions", []) if c["id"] == "track_bias_fit"), None
        )
        assert track_bias is not None, "honmei_v3 に track_bias_fit 条件が存在しない"
        assert track_bias.get("required") is True
        assert track_bias.get("enabled") is True

    def test_anaba_v2_min_odds_lower_than_v1(self):
        """anaba_v2 の min_odds 閾値が anaba_v1 より低いこと（BET-5 実験の差別化点）。"""
        v1 = self._load("anaba_v1")
        v2 = self._load("anaba_v2")

        def _get_min_odds(s: dict) -> float:
            for c in s.get("conditions", []):
                if c["id"] == "min_odds":
                    return c["params"]["min_tan_odds"]
            return float("inf")

        assert _get_min_odds(v2) < _get_min_odds(v1)

    def test_anaba_v2_has_min_odds_required(self):
        """anaba_v2 も min_odds が required:true であること（相手選定固有条件）。"""
        s = self._load("anaba_v2")
        required_ids = {
            c["id"] for c in s.get("conditions", [])
            if c.get("required") is True and c.get("enabled", True)
        }
        assert "min_odds" in required_ids

    def test_honmei_v3_max_selections_does_not_exceed_anaba_max(self):
        """honmei_v3 の max_selections が anaba_v1 を超えないこと（BET-2 テスト保護）。"""
        anaba_v1 = self._load("anaba_v1")
        honmei_v3 = self._load("honmei_v3")
        anaba_v1_max = anaba_v1["ranking"]["max_selections"]
        honmei_v3_max = honmei_v3["ranking"]["max_selections"]
        assert honmei_v3_max < anaba_v1_max, (
            f"honmei_v3.max_selections={honmei_v3_max} が "
            f"anaba_v1.max_selections={anaba_v1_max} 以上になっている "
            "（test_anaba_allows_more_selections_than_honmei が失敗する可能性）"
        )


# ---------------------------------------------------------------------------
# 2. JSON シリアライズ / デシリアライズ
# ---------------------------------------------------------------------------


class TestComboBacktestResultSerialization:
    """ComboBacktestResult が JSON で正しくシリアライズ / デシリアライズできること。"""

    def test_model_dump_json_roundtrip(self):
        """ComboBacktestResult → JSON 文字列 → ComboBacktestResult が等価であること。"""
        original = _make_result("honmei_v1", "anaba_v1")
        json_str = original.model_dump_json(indent=2)
        restored = ComboBacktestResult.model_validate_json(json_str)
        assert restored.honmei_strategy == original.honmei_strategy
        assert restored.aite_strategy == original.aite_strategy
        assert restored.period_label == original.period_label
        assert restored.tansho.race_count == original.tansho.race_count
        assert restored.tansho.bet_count == original.tansho.bet_count
        assert restored.tansho.return_rate == original.tansho.return_rate

    def test_json_file_roundtrip(self, tmp_path: Path):
        """JSON ファイル保存 → 読み込みが等価であること（load_result のテスト）。"""
        original = _make_result("honmei_v3", "anaba_v2", period="3m")
        p = tmp_path / "test_result.json"
        p.write_text(original.model_dump_json(indent=2), encoding="utf-8")
        restored = load_result(p)
        assert restored.honmei_strategy == "honmei_v3"
        assert restored.aite_strategy == "anaba_v2"
        assert restored.umaren.race_count == original.umaren.race_count
        assert restored.wide.bet_count == original.wide.bet_count

    def test_race_count_and_bet_count_preserved_in_json(self, tmp_path: Path):
        """JSON に race_count / bet_count が保存され、読み込み後も保持されること（出力規約）。"""
        result = _make_result(race_count=759, bet_count=759)
        p = tmp_path / "roundtrip.json"
        p.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        loaded = load_result(p)
        assert loaded.tansho.race_count == 759
        assert loaded.tansho.bet_count == 759


# ---------------------------------------------------------------------------
# 3. collect_result_files
# ---------------------------------------------------------------------------


class TestCollectResultFiles:
    """collect_result_files がファイルを正しく収集できること。"""

    def test_from_directory(self, tmp_path: Path):
        """ディレクトリ内の全 JSON を検出できること。"""
        (tmp_path / "result_a.json").write_text("{}", encoding="utf-8")
        (tmp_path / "result_b.json").write_text("{}", encoding="utf-8")
        (tmp_path / "other.txt").write_text("ignore", encoding="utf-8")
        paths = collect_result_files(results_dir=tmp_path)
        assert len(paths) == 2
        assert all(p.suffix == ".json" for p in paths)

    def test_from_explicit_files(self, tmp_path: Path):
        """--result-files で直接指定したファイルを収集できること。"""
        p1 = tmp_path / "a.json"
        p2 = tmp_path / "b.json"
        p1.write_text("{}", encoding="utf-8")
        p2.write_text("{}", encoding="utf-8")
        paths = collect_result_files(result_files=[str(p1), str(p2)])
        assert len(paths) == 2

    def test_empty_directory_returns_empty(self, tmp_path: Path):
        """空ディレクトリは空リストを返すこと。"""
        paths = collect_result_files(results_dir=tmp_path)
        assert paths == []

    def test_none_inputs_return_empty(self):
        """両引数が None の場合、空リストを返すこと。"""
        paths = collect_result_files(results_dir=None, result_files=None)
        assert paths == []


# ---------------------------------------------------------------------------
# 4. get_combo_stats / format_stats
# ---------------------------------------------------------------------------


class TestGetComboStats:
    """get_combo_stats が各賭式フィールドを正しく返すこと。"""

    def test_tansho(self):
        r = _make_result()
        stats = get_combo_stats(r, "tansho")
        assert stats.race_count == 100

    def test_fukusho(self):
        r = _make_result()
        stats = get_combo_stats(r, "fukusho")
        assert stats.hit_count == 30

    def test_umaren(self):
        r = _make_result()
        stats = get_combo_stats(r, "umaren")
        assert stats.bet_count == 450

    def test_wide(self):
        r = _make_result()
        stats = get_combo_stats(r, "wide")
        assert stats.bet_count == 450

    def test_all_4_bet_types_accessible(self):
        """4 賭式全てが get_combo_stats で取得できること（BET-5 出力規約）。"""
        r = _make_result()
        for bt in _BET_TYPES_4:
            stats = get_combo_stats(r, bt)
            assert isinstance(stats, ComboStats)


class TestFormatStats:
    """format_stats が出力規約に従う文字列を生成すること。"""

    def test_contains_return_rate(self):
        s = _make_combo_stats(return_rate=0.819)
        out = format_stats(s)
        assert "81.9%" in out

    def test_contains_race_count(self):
        s = _make_combo_stats(race_count=759)
        out = format_stats(s)
        assert "759R" in out

    def test_contains_bet_count(self):
        s = _make_combo_stats(bet_count=3473)
        out = format_stats(s)
        assert "3473B" in out

    def test_contains_hit_count(self):
        s = _make_combo_stats(hit_count=85)
        out = format_stats(s)
        assert "85" in out


# ---------------------------------------------------------------------------
# 5. build_strategy_label
# ---------------------------------------------------------------------------


class TestBuildStrategyLabel:
    def test_label_contains_both_strategies(self):
        r = _make_result("honmei_v3", "anaba_v2")
        label = build_strategy_label(r)
        assert "honmei_v3" in label
        assert "anaba_v2" in label


# ---------------------------------------------------------------------------
# 6. print_comparison_table
# ---------------------------------------------------------------------------


class TestPrintComparisonTable:
    """print_comparison_table が正しい出力を生成すること。"""

    def _capture(self, results, **kwargs) -> str:
        buf = StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            print_comparison_table(results, **kwargs)
        finally:
            sys.stdout = old_stdout
        return buf.getvalue()

    def test_empty_results_outputs_message(self):
        out = self._capture([])
        assert "ありません" in out

    def test_shows_4_bet_types_by_default(self):
        """デフォルトで 4 賭式（単勝・複勝・馬連・ワイド）が表示されること。"""
        r = _make_result()
        out = self._capture([r])
        for label in ["単勝", "複勝", "馬連", "ワイド"]:
            assert label in out

    def test_sanrenfuku_not_in_default_output(self):
        """三連複（sanrenfuku/三連複）がデフォルト出力に含まれないこと（BET-5 規約）。"""
        r = _make_result()
        out = self._capture([r])
        assert "三連複" not in out
        assert "sanrenfuku" not in out

    def test_shows_race_count_and_bet_count(self):
        """出力に race_count と bet_count が含まれること（出力規約）。"""
        r = _make_result(race_count=759, bet_count=759)
        out = self._capture([r])
        assert "759" in out

    def test_two_strategies_both_appear(self):
        """2 つの戦略パターンが両方出力されること（BET-5 Done 条件）。"""
        r1 = _make_result("honmei_v1", "anaba_v1")
        r2 = _make_result("honmei_v3", "anaba_v2")
        out = self._capture([r1, r2])
        assert "honmei_v1" in out
        assert "honmei_v3" in out
        assert "anaba_v1" in out
        assert "anaba_v2" in out

    def test_period_filter_works(self):
        """期間フィルタで指定した期間のみ表示されること。"""
        r1 = _make_result(period="3m", from_date="2026-03-27", to_date="2026-06-26")
        r2 = _make_result(period="6m", from_date="2025-12-27", to_date="2026-06-26")
        out = self._capture([r1, r2], period_filter="3m")
        assert "3m" in out
        # 6m 分のデータは含まれない（同日付として重複するレコードが出ないことを確認）
        assert "2025-12-27" not in out

    def test_return_rate_shown_as_percentage(self):
        """回収率がパーセント表示であること。"""
        s = _make_combo_stats(return_rate=0.819)
        r = _make_result()
        r = r.model_copy(update={"tansho": s})
        out = self._capture([r], bet_types=["tansho"])
        assert "81.9%" in out

    def test_bet5_done_condition_no_code_change_needed(self):
        """BET-5 Done 条件: 戦略名の切り替えのみで比較できること（コード変更が不要なことをテストで明示）。

        このテストは「honmei_v3 × anaba_v2 の結果を honmei_v1 × anaba_v1 の結果と
        並べて比較するのに Python コードの変更が不要」であることを示す。
        比較はデータ（ComboBacktestResult の honmei_strategy / aite_strategy フィールド）
        によって駆動されており、コードへの分岐追加は一切不要。
        """
        r1 = _make_result(honmei="honmei_v1", aite="anaba_v1")
        r2 = _make_result(honmei="honmei_v3", aite="anaba_v2")
        # 同じ print_comparison_table() 呼び出しで 2 戦略が比較される
        out = self._capture([r1, r2])
        assert "honmei_v1" in out
        assert "honmei_v3" in out


# ---------------------------------------------------------------------------
# 7. BET-5 出力規約（4 賭式すべてに race_count/bet_count が同じ階層で出力）
# ---------------------------------------------------------------------------


class TestBet5OutputRegulation:
    """BET-5 出力規約: 4 賭式の全てに race_count / bet_count が同じ階層で出力されること。"""

    def test_4_bet_types_defined(self):
        """_BET_TYPES_4 が 4 賭式（tansho/fukusho/umaren/wide）を含むこと。"""
        assert set(_BET_TYPES_4) == {"tansho", "fukusho", "umaren", "wide"}

    def test_sanrenfuku_excluded_from_4_bet_types(self):
        """_BET_TYPES_4 に三連複（sanrenfuku）が含まれないこと。"""
        assert "sanrenfuku" not in _BET_TYPES_4

    def test_all_4_bet_types_have_race_count_and_bet_count(self):
        """4 賭式の全てに race_count / bet_count フィールドが存在すること（モデル保証）。"""
        r = _make_result()
        for bt in _BET_TYPES_4:
            stats = get_combo_stats(r, bt)
            assert hasattr(stats, "race_count"), f"{bt}: race_count フィールドが存在しない"
            assert hasattr(stats, "bet_count"), f"{bt}: bet_count フィールドが存在しない"
            assert hasattr(stats, "return_rate"), f"{bt}: return_rate フィールドが存在しない"
