"""
pace_bias_ai/features/graded_confidence.py
============================================
重賞（grade_code in A/B/C/L/E）専用の自信度判定ロジック。

学習期間 2022-01〜2025-05、グレード+OP/L (N=4,092) で検証・線引き済みの
条件セット。検証期間(2025-05-31〜)での最終評価: 一押し61.0%(N=41) /
見送り44.2%、分離幅16.8pt（docs/validation/GRADED_CONFIDENCE_ANALYSIS.md 参照）。

通常レース（grade_codeなし）の判定ロジック（scripts/generate_ai_picks.py
の _compute_confidence 標準分岐）には一切影響しない。この分岐は
grade_code in ('A','B','C','L','E') のレースにのみ適用される。

## 無効化する条件（重賞では効かないと実証済み）
- is_step（重賞の計画的休養を誤検知するため）
- is_genuine
- long_rest（休み明け3ヶ月以上は重賞では26.3%と最高、ネガ扱い禁止。
  is_step の判定条件の一部のため is_step 無効化で自動的に無効化される）
- transport_flag（再計測で-2〜3ptのみ、無効化）

## 採用する条件
- クラス移動: 格下げ/同格ローテ=ポジ（29.9%/26.5%）、
  格上挑戦・条件戦からの挑戦=ネガ（18.7%/15.9%）
- 調教①該当（tipster/training_ranker.py 条件①）: ポジ（+5.4pt）
- 度外視（前走G1/G2 かつ 着差0.5秒以内）: ポジ（32.4%）
- 高齢（7歳以上）: ネガ（重賞でも有効）
"""
from __future__ import annotations

import math

# 重賞用confidence判定の対象grade_code
GRADED_RACE_GRADE_CODES: frozenset[str] = frozenset({"A", "B", "C", "L", "E"})

# 度外視: 前走が G1/G2（grade_code A/B）
EXCUSE_ELIGIBLE_GRADE_CODES: frozenset[str] = frozenset({"A", "B"})
EXCUSE_MARGIN_THRESHOLD_SEC = 0.5

AGE_VETERAN_THRESHOLD = 7


def is_graded_race(grade_code: str | None) -> bool:
    """レースが重賞用confidence判定の対象か判定する。"""
    if not grade_code:
        return False
    return str(grade_code).strip().upper() in GRADED_RACE_GRADE_CODES


def classify_class_transition(
    class_vs_best: float | None,
    best_class_rank: float | None,
) -> str | None:
    """クラス移動を4分類する。

    Args:
        class_vs_best: pace_bias_ai.features.rotation_flag.build_rotation_flags()
            の class_vs_best（best_class_rank - cur_grade_rank_v、正=今走が格上）
        best_class_rank: 同 best_class_rank（1=G1...5=D, 6=条件戦のみ=デフォルト）

    Returns:
        "downgrade"   格下げ（今走 < 過去最高クラス相当、class_vs_best < 0）
        "same"        同格ローテ（class_vs_best == 0）
        "upgrade"     格上挑戦（過去に重賞/L経験ありでの格上挑戦、best_class_rank <= 5）
        "from_conditions"  条件戦からの重賞挑戦（best_class_rank == 6、条件戦のみの経験）
        None          判定不能（初出走等でclass_vs_bestが欠損）
    """
    if class_vs_best is None or (isinstance(class_vs_best, float) and math.isnan(class_vs_best)):
        return None
    if best_class_rank is None or (isinstance(best_class_rank, float) and math.isnan(best_class_rank)):
        return None

    if class_vs_best < 0:
        return "downgrade"
    if class_vs_best == 0:
        return "same"
    # class_vs_best > 0 (格上方向)
    if best_class_rank >= 6:
        return "from_conditions"
    return "upgrade"


def class_transition_is_positive(transition: str | None) -> bool | None:
    """クラス移動分類がポジ材料かネガ材料かを返す。None=判定不能（スコア加減なし）。"""
    if transition is None:
        return None
    return transition in ("downgrade", "same")


def is_excuse_margin_eligible(
    prev_grade_code: str | None,
    prev_margin_sec: float | None,
) -> bool:
    """度外視（前走G1/G2 かつ 着差0.5秒以内）に該当するか判定する。

    Args:
        prev_grade_code: 前走の grade_code
        prev_margin_sec: 前走の着差（秒、絶対値。0=1着）
    """
    if not prev_grade_code or prev_margin_sec is None:
        return False
    if str(prev_grade_code).strip().upper() not in EXCUSE_ELIGIBLE_GRADE_CODES:
        return False
    if isinstance(prev_margin_sec, float) and math.isnan(prev_margin_sec):
        return False
    return abs(prev_margin_sec) <= EXCUSE_MARGIN_THRESHOLD_SEC


def is_age_veteran(horse_age: int | None) -> bool:
    """高齢（7歳以上）判定。"""
    if horse_age is None:
        return False
    return int(horse_age) >= AGE_VETERAN_THRESHOLD
