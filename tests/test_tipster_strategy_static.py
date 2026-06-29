"""
tests/test_tipster_strategy_static.py
========================================
tipster/strategies/*.json に対する静的チェック。

PLAN.md §5-1 G5a の要件:
  (1) conditions[] に ai_score 系の条件が required:true で設定されていない
  (2) ranking.primary が "ai_score" になっていない
  (3) select_honmei のソートキー順序固定 → test_tipster_engine.py に分離済み

PLAN.md §5-3 BET-2 の要件:
  - 相手選定戦略の conditions が本命選定戦略と同一の条件 ID のみで
    構成されていない（差別化された条件群であること）
  - 相手選定戦略には本命選定戦略にない固有の必須条件（required:true）が存在すること
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

_STRATEGIES_DIR = Path(__file__).parent.parent / "tipster" / "strategies"


def _load_all_strategies() -> dict[str, dict]:
    """tipster/strategies/*.json を全件ロードして {filename_stem: data} を返す。"""
    result = {}
    for p in sorted(_STRATEGIES_DIR.glob("*.json")):
        result[p.stem] = json.loads(p.read_text(encoding="utf-8"))
    return result


def _honmei_strategies(all_strats: dict[str, dict]) -> dict[str, dict]:
    return {k: v for k, v in all_strats.items() if v.get("type") == "honmei"}


def _anaba_strategies(all_strats: dict[str, dict]) -> dict[str, dict]:
    return {k: v for k, v in all_strats.items() if v.get("type") == "anaba"}


# ---------------------------------------------------------------------------
# G5a(1): conditions[] に ai_score 系の条件が required:true で設定されていない
# ---------------------------------------------------------------------------

# AI スコアに基づく条件として扱う condition id パターン
_AI_SCORE_CONDITION_IDS = {"ai_score", "ai_rank", "ai_confidence"}


class TestG5aNoAiScoreRequired:
    """G5a(1): いずれの戦略 JSON も required:true の条件が ai_score 系でないこと。"""

    def _required_condition_ids(self, strategy: dict) -> set[str]:
        return {
            c["id"]
            for c in strategy.get("conditions", [])
            if c.get("required") is True and c.get("enabled", True)
        }

    def test_no_strategy_has_ai_score_as_required_condition(self):
        """全戦略 JSON で ai_score 系 condition が required:true になっていない (G5a-1)。"""
        all_strats = _load_all_strategies()
        violations = []
        for stem, strat in all_strats.items():
            bad = self._required_condition_ids(strat) & _AI_SCORE_CONDITION_IDS
            if bad:
                violations.append(f"{stem}: {bad}")
        assert not violations, (
            "以下の戦略に ai_score 系 condition が required:true で設定されています (G5a-1):\n"
            + "\n".join(violations)
        )

    def test_all_strategy_files_are_loadable(self):
        """全戦略 JSON がパース可能であること（壊れたファイルがないこと）。"""
        strats = _load_all_strategies()
        assert len(strats) >= 3, "最低 3 ファイル（honmei_v1, honmei_v2, anaba_v1）が必要"
        for stem, strat in strats.items():
            assert "name" in strat, f"{stem}: 'name' フィールドが存在しない"
            assert "conditions" in strat, f"{stem}: 'conditions' フィールドが存在しない"
            assert "ranking" in strat, f"{stem}: 'ranking' フィールドが存在しない"


# ---------------------------------------------------------------------------
# G5a(2): ranking.primary が "ai_score" になっていない
# ---------------------------------------------------------------------------


class TestG5aRankingPrimary:
    """G5a(2): いずれの戦略 JSON も ranking.primary が "ai_score" でないこと。"""

    def test_no_strategy_has_ai_score_as_ranking_primary(self):
        """全戦略 JSON の ranking.primary が "ai_score" でないこと (G5a-2)。"""
        all_strats = _load_all_strategies()
        violations = []
        for stem, strat in all_strats.items():
            primary = strat.get("ranking", {}).get("primary")
            if primary == "ai_score":
                violations.append(f"{stem}: ranking.primary = {primary!r}")
        assert not violations, (
            "以下の戦略で ranking.primary が 'ai_score' になっています (G5a-2):\n"
            + "\n".join(violations)
        )

    def test_honmei_strategies_use_condition_count_or_total_score_as_primary(self):
        """本命戦略は ranking.primary が条件系（condition_clear_count / total_score）であること。"""
        all_strats = _load_all_strategies()
        allowed_primaries = {"condition_clear_count", "total_score"}
        honmei_strats = _honmei_strategies(all_strats)
        assert honmei_strats, "本命戦略（type=honmei）が1件も存在しない"
        for stem, strat in honmei_strats.items():
            primary = strat.get("ranking", {}).get("primary")
            assert primary in allowed_primaries, (
                f"{stem}: 本命戦略の ranking.primary={primary!r} は "
                f"{allowed_primaries} のいずれかであること"
            )


# ---------------------------------------------------------------------------
# BET-2: 相手選定戦略と本命選定戦略の条件群の差別化確認
# ---------------------------------------------------------------------------


class TestBet2AnabaVsHonmeiDifferentiation:
    """BET-2: 相手選定戦略（anaba 系）が本命選定戦略と差別化された条件群を持つこと。"""

    def _required_condition_ids(self, strategy: dict) -> set[str]:
        return {
            c["id"]
            for c in strategy.get("conditions", [])
            if c.get("required") is True and c.get("enabled", True)
        }

    def _all_condition_ids(self, strategy: dict) -> set[str]:
        return {c["id"] for c in strategy.get("conditions", [])}

    def test_anaba_strategies_exist(self):
        """相手選定戦略（type=anaba）が最低 1 件存在すること。"""
        all_strats = _load_all_strategies()
        anaba_strats = _anaba_strategies(all_strats)
        assert anaba_strats, "相手選定戦略（type=anaba）が存在しない"

    def test_anaba_has_different_required_conditions_from_all_honmei(self):
        """anaba の必須条件集合が、どの honmei の必須条件集合とも同一でないこと (BET-2)。"""
        all_strats = _load_all_strategies()
        anaba_strats = _anaba_strategies(all_strats)
        honmei_strats = _honmei_strategies(all_strats)
        assert anaba_strats, "anaba 戦略が存在しない"
        assert honmei_strats, "honmei 戦略が存在しない"

        for a_stem, a_strat in anaba_strats.items():
            a_required = self._required_condition_ids(a_strat)
            for h_stem, h_strat in honmei_strats.items():
                h_required = self._required_condition_ids(h_strat)
                assert a_required != h_required, (
                    f"{a_stem} の必須条件集合 {a_required} が "
                    f"{h_stem} の必須条件集合 {h_required} と同一です (BET-2 差別化要件違反)"
                )

    def test_anaba_v1_has_min_odds_as_required(self):
        """anaba_v1 は人気馬を除外する min_odds 条件を required:true で持つこと。

        honmei 戦略には min_odds は存在しない（馬柱で評価されにくい馬を狙う相手選定固有条件）。
        """
        all_strats = _load_all_strategies()
        anaba = all_strats.get("anaba_v1")
        assert anaba is not None, "anaba_v1.json が見つからない"
        required_ids = self._required_condition_ids(anaba)
        assert "min_odds" in required_ids, (
            f"anaba_v1 の required 条件に 'min_odds' が含まれていない: {required_ids}"
        )

    def test_honmei_strategies_do_not_have_min_odds_required(self):
        """本命戦略（honmei 系）は min_odds を required:true で持たないこと。

        min_odds は「人気馬を除外して穴馬を探す」相手選定固有の条件であり、
        本命選定に混入してはならない。
        """
        all_strats = _load_all_strategies()
        honmei_strats = _honmei_strategies(all_strats)
        for stem, strat in honmei_strats.items():
            required_ids = self._required_condition_ids(strat)
            assert "min_odds" not in required_ids, (
                f"{stem}（本命戦略）に min_odds が required:true で設定されている (BET-2 混同禁止)"
            )

    def test_anaba_allows_more_selections_than_honmei(self):
        """相手選定は複数頭（本命の max_selections より多い）を返せる設定であること。

        BET-3 の馬連/三連複組み合わせ生成に最低 2 頭の相手が必要なため。
        """
        all_strats = _load_all_strategies()
        anaba_strats = _anaba_strategies(all_strats)
        honmei_strats = _honmei_strategies(all_strats)

        anaba_max = max(
            s.get("ranking", {}).get("max_selections", 1) for s in anaba_strats.values()
        )
        honmei_max = max(
            s.get("ranking", {}).get("max_selections", 1) for s in honmei_strats.values()
        )
        assert anaba_max >= 2, (
            f"相手選定の max_selections={anaba_max} が 2 未満。"
            "BET-3 の三連複組み合わせ生成には最低 2 頭の相手が必要"
        )
        assert anaba_max > honmei_max, (
            f"相手選定 max_selections={anaba_max} が本命選定 max_selections={honmei_max} 以下。"
            "相手選定は本命より多くの頭数を対象とすること"
        )
