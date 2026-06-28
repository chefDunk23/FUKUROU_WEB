"""
tipster/lab_adapter.py
======================
条件ラボ用アダプター。

LabConditionSet → Strategy 変換と、バックテスト実行ラッパーを提供する。
既存の combo_backtest.py / engine.py / conditions_v2.py は一切変更しない。
"""
from __future__ import annotations

import json
import tempfile
import uuid
from pathlib import Path
from typing import Any

from .models import ConditionConfig, RankingConfig, Strategy


# ─────────────────────────────────────────────────────────────────────────
# 組み込み条件カタログ（conditions_v2.py で register_condition されたもの）
# ─────────────────────────────────────────────────────────────────────────

BUILTIN_CONDITIONS: list[dict[str, Any]] = [
    {
        "id": "v2_past_margin",
        "name": "過去走着差チェック",
        "description": "過去N走以内に勝ち馬との差≤秒数の好走歴があるか確認",
        "layer": "第1層: ポテンシャル確認",
        "type": "scoring",
        "params_schema": {
            "lookback":    {"type": "int",   "default": 3,   "min": 1, "max": 5,   "label": "参照走数"},
            "max_sec":     {"type": "float", "default": 1.0, "min": 0.1, "max": 3.0, "label": "着差上限(秒)"},
            "bonus_score": {"type": "float", "default": 1.0, "min": 0.0, "max": 3.0, "label": "加点"},
        },
    },
    {
        "id": "v2_race_quality",
        "name": "レースレベル評価",
        "description": "前走上位N頭が次走でも好成績を残したか（レースの質確認）",
        "layer": "第1層: ポテンシャル確認",
        "type": "scoring",
        "params_schema": {
            "top_n":           {"type": "int",   "default": 3,    "min": 1, "max": 5,  "label": "参照頭数"},
            "min_next_horses": {"type": "int",   "default": 3,    "min": 1, "max": 10, "label": "最小次走数"},
            "min_place_rate":  {"type": "float", "default": 0.35, "min": 0.1, "max": 0.7, "label": "複勝率下限"},
            "bonus_score":     {"type": "float", "default": 1.0,  "min": 0.0, "max": 3.0, "label": "加点"},
        },
    },
    {
        "id": "v2_class_change",
        "name": "クラス変化評価",
        "description": "昇降級の方向を評価。降級は加点、昇級は様子見(None)",
        "layer": "第1層: ポテンシャル確認",
        "type": "scoring",
        "params_schema": {
            "downgrade_bonus":  {"type": "float", "default": 1.0,  "min": 0.0, "max": 3.0, "label": "降級加点"},
            "upgrade_as_none":  {"type": "bool",  "default": True,  "label": "昇級は保留扱い"},
        },
    },
    {
        "id": "v2_distance_match",
        "name": "距離適性",
        "description": "前走との距離変化と同距離帯での過去好走歴を評価",
        "layer": "第2層: 今回レース嵌まり度",
        "type": "scoring",
        "params_schema": {
            "band_big":    {"type": "int",   "default": 400, "min": 100, "max": 800, "label": "大幅変化判定(m)"},
            "band_margin": {"type": "int",   "default": 200, "min": 50,  "max": 400, "label": "距離帯マージン(m)"},
            "bonus_score": {"type": "float", "default": 0.5, "min": 0.0, "max": 3.0, "label": "加点"},
            "lookback":    {"type": "int",   "default": 3,   "min": 1,   "max": 5,   "label": "参照走数"},
        },
    },
    {
        "id": "v2_jockey_positive",
        "name": "騎手評価",
        "description": "継続騎乗・リーディング乗り替わりは加点、非リーディング乗り替わりは不可",
        "layer": "第2層: 今回レース嵌まり度",
        "type": "scoring",
        "params_schema": {
            "top_jockey_threshold": {"type": "int",   "default": 30,  "min": 10, "max": 100, "label": "リーディング閾値(年間勝数)"},
            "base_score":           {"type": "float", "default": 0.5, "min": 0.0, "max": 2.0, "label": "継続騎乗加点"},
            "upgrade_bonus":        {"type": "float", "default": 1.0, "min": 0.0, "max": 3.0, "label": "リーディング乗替加点"},
        },
    },
    {
        "id": "v2_weight_favor",
        "name": "斤量評価",
        "description": "前走比の斤量増減を評価。軽減は加点、増量は減点",
        "layer": "第2層: 今回レース嵌まり度",
        "type": "scoring",
        "params_schema": {
            "decrease_threshold": {"type": "float", "default": 0.5, "min": 0.0, "max": 2.0, "label": "軽減判定(kg)"},
            "increase_threshold": {"type": "float", "default": 0.5, "min": 0.0, "max": 2.0, "label": "増量判定(kg)"},
            "decrease_bonus":     {"type": "float", "default": 0.5, "min": 0.0, "max": 2.0, "label": "軽減加点"},
            "increase_penalty":   {"type": "float", "default": -0.5, "min": -3.0, "max": 0.0, "label": "増量減点"},
        },
    },
    {
        "id": "v2_interval_optimal",
        "name": "出走間隔",
        "description": "適正間隔(15-28日)は加点。長期休養明け(60日以上)は保留",
        "layer": "第2層: 今回レース嵌まり度",
        "type": "scoring",
        "params_schema": {
            "optimal_min":  {"type": "int",   "default": 15,  "min": 7,  "max": 30, "label": "適正間隔最小(日)"},
            "optimal_max":  {"type": "int",   "default": 28,  "min": 14, "max": 60, "label": "適正間隔最大(日)"},
            "bonus_score":  {"type": "float", "default": 0.5, "min": 0.0, "max": 2.0, "label": "加点"},
            "long_rest_min": {"type": "int",  "default": 60,  "min": 30, "max": 180, "label": "長期休養判定(日)"},
        },
    },
    {
        "id": "v2_surface_history",
        "name": "馬場適性（芝/ダート）",
        "description": "今回と同馬場（芝/ダート）での過去好走歴を評価",
        "layer": "第2層: 今回レース嵌まり度",
        "type": "scoring",
        "params_schema": {
            "lookback":       {"type": "int",   "default": 5,   "min": 1, "max": 10, "label": "参照走数"},
            "min_place_rank": {"type": "int",   "default": 3,   "min": 1, "max": 5,  "label": "複勝圏着順"},
            "bonus_score":    {"type": "float", "default": 0.5, "min": 0.0, "max": 2.0, "label": "加点"},
        },
    },
    {
        "id": "v2_f3_top",
        "name": "上がり3F順位（S-1）",
        "description": "過去走で上がり3Fが上位N%以内の実績があるか",
        "layer": "Phase2 S-1/B-2",
        "type": "scoring",
        "params_schema": {
            "top_pct":     {"type": "float", "default": 0.33, "min": 0.1, "max": 0.5, "label": "上位割合"},
            "bonus_score": {"type": "float", "default": 1.0,  "min": 0.0, "max": 3.0, "label": "加点"},
        },
    },
    {
        "id": "v2_hill_fit",
        "name": "坂あり/なし適性（S-1）",
        "description": "今回競馬場の坂区分（坂あり/なし）での過去好走歴",
        "layer": "Phase2 S-1/B-2",
        "type": "scoring",
        "params_schema": {
            "lookback":       {"type": "int",   "default": 5,   "min": 1, "max": 10, "label": "参照走数"},
            "min_place_rank": {"type": "int",   "default": 3,   "min": 1, "max": 5,  "label": "複勝圏着順"},
            "bonus_score":    {"type": "float", "default": 1.0, "min": 0.0, "max": 3.0, "label": "加点"},
        },
    },
    {
        "id": "v2_sire_venue",
        "name": "種牡馬会場適性（S-1）",
        "description": "産駒の当該会場top3率が全体top3率を上回るか",
        "layer": "Phase2 S-1/B-2",
        "type": "scoring",
        "params_schema": {
            "bonus_score": {"type": "float", "default": 1.0, "min": 0.0, "max": 3.0, "label": "加点"},
        },
    },
    {
        "id": "v2_baba_track_record",
        "name": "馬場状態別成績（BET-7）",
        "description": "指定馬場状態（良/稍重/重/不良）での過去複勝率を評価",
        "layer": "BET-7 馬場別",
        "type": "scoring",
        "params_schema": {
            "baba":          {"type": "str",   "default": "良",   "choices": ["良", "稍重", "重", "不良"], "label": "馬場状態"},
            "min_runs":      {"type": "int",   "default": 3,      "min": 1, "max": 10, "label": "最小出走数"},
            "pass_rate":     {"type": "float", "default": 0.30,   "min": 0.0, "max": 1.0, "label": "合格複勝率"},
            "fail_rate":     {"type": "float", "default": 0.15,   "min": 0.0, "max": 1.0, "label": "不合格複勝率"},
            "bonus_score":   {"type": "float", "default": 1.0,    "min": 0.0, "max": 3.0, "label": "加点"},
            "penalty_score": {"type": "float", "default": -1.0,   "min": -3.0, "max": 0.0, "label": "減点"},
        },
    },
    {
        "id": "v2_sire_baba_fit",
        "name": "種牡馬馬場適性（BET-7）",
        "description": "sire_feature_storeの馬場別優位度を評価（PIT-safe）",
        "layer": "BET-7 馬場別",
        "type": "scoring",
        "params_schema": {
            "baba":        {"type": "str",   "default": "良",  "choices": ["良", "稍重", "重", "不良"], "label": "馬場状態"},
            "threshold":   {"type": "float", "default": 0.02,  "min": 0.0, "max": 0.2, "label": "優位判定閾値"},
            "bonus_score": {"type": "float", "default": 0.5,   "min": 0.0, "max": 2.0, "label": "加点"},
        },
    },
    {
        "id": "v2_heavy_track_stamina",
        "name": "道悪スタミナ（BET-7）",
        "description": "重・不良馬場での過去複勝率（良/稍重は保留）",
        "layer": "BET-7 馬場別",
        "type": "scoring",
        "params_schema": {
            "baba":          {"type": "str",   "default": "良",   "choices": ["良", "稍重", "重", "不良"], "label": "馬場状態"},
            "min_runs":      {"type": "int",   "default": 3,      "min": 1, "max": 10, "label": "最小出走数"},
            "pass_rate":     {"type": "float", "default": 0.30,   "min": 0.0, "max": 1.0, "label": "合格複勝率"},
            "fail_rate":     {"type": "float", "default": 0.15,   "min": 0.0, "max": 1.0, "label": "不合格複勝率"},
            "bonus_score":   {"type": "float", "default": 1.0,    "min": 0.0, "max": 3.0, "label": "加点"},
            "penalty_score": {"type": "float", "default": -1.5,   "min": -3.0, "max": 0.0, "label": "減点"},
        },
    },
]

BUILTIN_IDS: frozenset[str] = frozenset(c["id"] for c in BUILTIN_CONDITIONS)

# ─────────────────────────────────────────────────────────────────────────
# Strategy 変換
# ─────────────────────────────────────────────────────────────────────────

_LAB_STRATEGIES_DIR = Path(tempfile.gettempdir()) / "fukurou_lab_strategies"
_LAB_STRATEGIES_DIR.mkdir(exist_ok=True)


def condition_set_to_strategy(condition_set: dict[str, Any]) -> Strategy:
    """LabConditionSet dict → Strategy モデルに変換する。"""
    condition_configs = [
        ConditionConfig(
            id=entry["condition_id"],
            enabled=entry.get("enabled", True),
            required=entry.get("mode") == "filter",
            params=entry.get("params", {}),
        )
        for entry in condition_set.get("conditions", [])
    ]
    ranking_cfg = RankingConfig(
        primary=condition_set.get("ranking", {}).get("primary", "condition_clear_count"),
        secondary=condition_set.get("ranking", {}).get("secondary", "ai_score"),
        max_selections=condition_set.get("ranking", {}).get("max_selections", 3),
    )
    return Strategy(
        name=condition_set.get("name", "Lab条件セット"),
        tipster="fukurou_lab",
        type="honmei",
        version="lab",
        conditions=condition_configs,
        ranking=ranking_cfg,
    )


def write_temp_strategy(strategy: Strategy) -> Path:
    """Strategy を一時JSONファイルとして書き出し、そのパスを返す。"""
    tmp_path = _LAB_STRATEGIES_DIR / f"lab_{uuid.uuid4().hex}.json"
    tmp_path.write_text(
        strategy.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return tmp_path


def remove_temp_strategy(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


# ─────────────────────────────────────────────────────────────────────────
# バックテスト実行ラッパー
# ─────────────────────────────────────────────────────────────────────────

def run_lab_backtest(
    honmei_set: dict[str, Any],
    aite_strategy_name: str = "anaba_v5",
    periods: list[str] | None = None,
    grade_filter: list[str] | None = None,
    distance_filter: list[str] | None = None,
) -> dict:
    """条件セットを本命戦略として combo_backtest を実行する。

    honmei_set: LabConditionSet dict
    aite_strategy_name: 相手戦略の短縮名 ("anaba_v5" 等)
    periods: ["3m", "6m", "1y"] 等
    """
    from .combo_backtest import run_combo_backtest

    honmei_strategy = condition_set_to_strategy(honmei_set)
    tmp_path = write_temp_strategy(honmei_strategy)
    try:
        results = run_combo_backtest(
            honmei_strategy_path=str(tmp_path),
            aite_strategy_path=aite_strategy_name,
            periods=periods or ["3m", "6m", "1y"],
            grade_filter=grade_filter,
            distance_filter=distance_filter,
        )
        return {
            period: result.model_dump()
            for period, result in results.items()
        }
    finally:
        remove_temp_strategy(tmp_path)
