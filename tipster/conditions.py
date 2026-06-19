"""
tipster/conditions.py
======================
予想家フレームワークの個別条件実装（プラグイン方式）。

各条件関数は `(horse: HorseContext, race_ctx: RaceContext, params: dict) -> ConditionResult`
の同一インターフェースを持つ。`@register_condition(id)` で CONDITION_REGISTRY に登録され、
戦略 JSON の conditions[].id から engine.py が呼び出す。
"""
from __future__ import annotations

from typing import Callable

from .models import ConditionResult, HorseContext, RaceContext

CONDITION_REGISTRY: dict[str, Callable[[HorseContext, RaceContext, dict], ConditionResult]] = {}

# jockeys.apprentice_code / license_type が現行 DB では未投入のため、
# 減量騎手判定はキャリア勝利数の少なさで近似する。
_APPRENTICE_CAREER_WINS_THRESHOLD = 50

# races.grade_code -> グレード区分（A=G1/B=G2/C=G3、未該当は "default"）
_GRADE_CODE_TO_LABEL = {"A": "G1", "B": "G2", "C": "G3"}

_DEFAULT_RACE_LEVEL_THRESHOLDS = {
    "G1":      {"place_rate": 0.20, "winner_max_rank": 7},
    "G2":      {"place_rate": 0.25, "winner_max_rank": 5},
    "G3":      {"place_rate": 0.30, "winner_max_rank": 5},
    "default": {"place_rate": 0.33, "winner_max_rank": 3},
}


def _grade_label(grade_code: str | None) -> str:
    return _GRADE_CODE_TO_LABEL.get((grade_code or "").strip(), "default")


def register_condition(condition_id: str):
    def decorator(fn):
        CONDITION_REGISTRY[condition_id] = fn
        return fn
    return decorator


# ─────────────────────────────────────────────────────────────────────────
# ベース条件①: レースレベル証明
# ─────────────────────────────────────────────────────────────────────────


@register_condition("race_level")
def check_race_level(horse: HorseContext, race_ctx: RaceContext, params: dict) -> ConditionResult:
    """前走・前々走の対戦相手の次走成績からレースレベルを検証する（前走 OR 前々走でクリアならOK）。

    - 次走出走済み馬が min_next_race_horses 未満の過去走はスキップする。
    - 必要な次走内率(place_rate)は前走自体のグレード(G1/G2/G3/default)で変える
      （強いレースほど対戦相手の次走順位が伸びにくいため、閾値を緩和する）。
    - 自身がその過去走で3着以内なら、次走順位の許容ランクを winner_max_rank に緩和する
      （G1の2-3着馬が次走で4-5着でもレースレベルの証明になる）。
    """
    min_next = params.get("min_next_race_horses", 3)
    thresholds = params.get("thresholds", _DEFAULT_RACE_LEVEL_THRESHOLDS)

    if not horse.past_races:
        return ConditionResult(passed=True, score=0.0, reason="前走データなし(判定保留)")

    attempts: list[dict] = []
    for prev in horse.past_races[:2]:
        known = [o for o in prev.opponents_next_races if o.next_race_rank is not None]
        if len(known) < min_next:
            continue

        grade_label = _grade_label(prev.grade_code)
        th = thresholds.get(grade_label, thresholds.get("default", _DEFAULT_RACE_LEVEL_THRESHOLDS["default"]))
        place_rate_required = th["place_rate"]

        threshold_rank = th["winner_max_rank"] if (prev.rank is not None and prev.rank <= 3) else 3

        hits = sum(1 for o in known if o.next_race_rank is not None and o.next_race_rank <= threshold_rank)
        rate = hits / len(known)
        passed = rate >= place_rate_required

        attempt = {
            "race_name": prev.race_name or prev.race_id,
            "grade_label": grade_label,
            "threshold_rank": threshold_rank,
            "rate": rate,
            "hits": hits,
            "known_count": len(known),
            "required_rate": place_rate_required,
        }
        attempts.append(attempt)

        if passed:
            return ConditionResult(
                passed=True,
                score=1.0,
                reason=(
                    f"前走({attempt['race_name']}/{grade_label})メンバー次走{threshold_rank}着内率"
                    f"{rate:.0%}({attempt['hits']}/{attempt['known_count']})>=基準{place_rate_required:.0%}"
                ),
                detail=attempt,
            )

    if not attempts:
        return ConditionResult(passed=True, score=0.0, reason="次走実績馬数不足のため判定保留")

    best = max(attempts, key=lambda a: a["rate"])
    return ConditionResult(
        passed=False,
        score=-1.0,
        reason=(
            f"前走/前々走いずれも基準未達(最良: {best['race_name']}/{best['grade_label']} "
            f"{best['threshold_rank']}着内率{best['rate']:.0%}<基準{best['required_rate']:.0%})"
        ),
        detail={"attempts": attempts},
    )


# ─────────────────────────────────────────────────────────────────────────
# ベース条件②: 着差足切り
# ─────────────────────────────────────────────────────────────────────────


@register_condition("time_gap")
def check_time_gap(horse: HorseContext, race_ctx: RaceContext, params: dict) -> ConditionResult:
    """前走の勝ち馬との着差（秒）が距離区分ごとの基準以内かを判定する。"""
    if not horse.past_races:
        return ConditionResult(passed=True, score=0.0, reason="前走データなし(判定保留)")

    prev = horse.past_races[0]
    own = next((o for o in prev.opponents_next_races if o.horse_id == horse.horse_id), None)
    if own is None or own.this_margin is None:
        return ConditionResult(passed=True, score=0.0, reason="前走着差データなし(判定保留)")

    margin = own.this_margin
    sprint_threshold_m = params.get("sprint_threshold_m", 1400)
    is_sprint = (prev.distance or 0) <= sprint_threshold_m

    if is_sprint:
        max_sec = params.get("sprint_max_sec", 1.0)
        fallback_sec = params.get("sprint_fallback_sec", 1.5)
    else:
        max_sec = params.get("mile_max_sec", 1.5)
        fallback_sec = params.get("mile_fallback_sec", 2.0)

    if margin <= max_sec:
        return ConditionResult(passed=True, score=0.0, reason=f"前走着差{margin:.1f}秒(基準{max_sec}秒以内)")
    if margin <= fallback_sec:
        return ConditionResult(passed=True, score=-0.5, reason=f"前走着差{margin:.1f}秒(救済範囲内,減点)")
    return ConditionResult(passed=False, score=-1.0, reason=f"前走着差{margin:.1f}秒(基準{fallback_sec}秒超過)")


# ─────────────────────────────────────────────────────────────────────────
# 展開条件: トラックバイアスと脚質の適合
# ─────────────────────────────────────────────────────────────────────────


@register_condition("track_bias_fit")
def check_track_bias_fit(horse: HorseContext, race_ctx: RaceContext, params: dict) -> ConditionResult:
    """race_ctx の前残り/差しバイアスと horse.position_tendency(0=逃げ〜1=追込) の適合を判定する。"""
    exclude_on_mismatch = params.get("exclude_on_mismatch", True)
    bias_threshold = params.get("bias_threshold", 0.2)

    pos = horse.position_tendency
    front_bias = race_ctx.front_bias_pit
    if pos is None or front_bias is None:
        return ConditionResult(passed=True, score=0.0, reason="脚質/バイアスデータ不足(判定保留)")

    mismatch = False
    if front_bias > bias_threshold and pos >= 0.6:
        mismatch = True
        reason = "前残り想定 vs 追込脚質(不適合)"
    elif front_bias < -bias_threshold and pos <= 0.4:
        mismatch = True
        reason = "差し決着想定 vs 先行脚質(不適合)"
    else:
        reason = "脚質とバイアスは適合"

    if mismatch:
        return ConditionResult(
            passed=not exclude_on_mismatch,
            score=-1.0,
            reason=reason,
            detail={"position_tendency": pos, "front_bias_pit": front_bias, "bias_source": race_ctx.bias_source},
        )
    return ConditionResult(
        passed=True, score=0.0, reason=reason,
        detail={"position_tendency": pos, "front_bias_pit": front_bias, "bias_source": race_ctx.bias_source},
    )


# ─────────────────────────────────────────────────────────────────────────
# 状態条件: 斤量増減
# ─────────────────────────────────────────────────────────────────────────


@register_condition("weight_change")
def check_weight_change(horse: HorseContext, race_ctx: RaceContext, params: dict) -> ConditionResult:
    """前走と今回の斤量(burden_weight)を比較する。減量騎手の影響は apprentice_bonus_disabled で無効化可能。"""
    increase_penalty = params.get("increase_penalty", -1)
    decrease_bonus = params.get("decrease_bonus", 1)
    apprentice_bonus_disabled = params.get("apprentice_bonus_disabled", True)

    if horse.burden_weight is None or horse.prev_burden_weight is None:
        return ConditionResult(passed=True, score=0.0, reason="斤量データ不足(判定保留)")

    diff = horse.burden_weight - horse.prev_burden_weight
    if abs(diff) < 0.5:
        return ConditionResult(passed=True, score=0.0, reason="斤量変化なし")

    if diff > 0:
        return ConditionResult(passed=True, score=increase_penalty, reason=f"斤量増(+{diff:.1f}kg)")

    is_new_apprentice = (
        apprentice_bonus_disabled
        and horse.jockey_career_wins is not None
        and horse.jockey_career_wins < _APPRENTICE_CAREER_WINS_THRESHOLD
        and horse.jockey_id != horse.prev_jockey_id
    )
    if is_new_apprentice:
        return ConditionResult(passed=True, score=0.0, reason=f"斤量減({diff:.1f}kg)だが減量騎手効果のため無効化")
    return ConditionResult(passed=True, score=decrease_bonus, reason=f"斤量減({diff:.1f}kg)")


# ─────────────────────────────────────────────────────────────────────────
# 勝負気配条件: 騎手乗り替わり3段階判定
# ─────────────────────────────────────────────────────────────────────────


@register_condition("jockey_change")
def check_jockey_change(horse: HorseContext, race_ctx: RaceContext, params: dict) -> ConditionResult:
    """Step1: 前走騎手が同レース内の別馬に騎乗→減点 / Step2: 同日別会場で騎乗→ノーカウント /
    Step3: 新騎手×厩舎の相性が良い→免除/加点。
    """
    top_jockey_threshold = params.get("top_jockey_threshold", 30)
    min_rides = params.get("stable_affinity_min_rides", 10)
    min_winrate = params.get("stable_affinity_min_winrate", 0.15)

    if not horse.prev_jockey_id or not horse.jockey_id:
        return ConditionResult(passed=True, score=0.0, reason="騎手データ不足(判定保留)")
    if horse.jockey_id == horse.prev_jockey_id:
        return ConditionResult(passed=True, score=0.0, reason="騎手継続")

    affinity = horse.jockey_change_affinity
    good_affinity = bool(
        affinity
        and affinity.get("combo_count", 0) >= min_rides
        and (affinity.get("combo_win_rate") or 0) >= min_winrate
    )

    if horse.jockey_change_step1_same_race:
        if good_affinity:
            return ConditionResult(
                passed=True, score=1.0,
                reason="前走騎手は他馬へ乗り替わりだが新騎手×厩舎相性良好のため加点",
                detail={"affinity": affinity},
            )
        return ConditionResult(passed=True, score=-1.0, reason="前走騎手が同レースの他馬へ乗り替わり(マイナス)")

    if horse.jockey_change_step2_other_venue:
        return ConditionResult(passed=True, score=0.0, reason="前走騎手は同日別会場へ騎乗(ノーカウント)")

    if horse.jockey_yr_wins is not None and horse.jockey_yr_wins >= top_jockey_threshold:
        return ConditionResult(passed=True, score=1.0, reason=f"上位騎手(年間{horse.jockey_yr_wins}勝)への乗り替わり")

    if good_affinity:
        return ConditionResult(passed=True, score=0.5, reason="新騎手×厩舎相性良好", detail={"affinity": affinity})

    return ConditionResult(passed=True, score=0.0, reason="騎手変更(中立)")


# ─────────────────────────────────────────────────────────────────────────
# オッズ条件: 単勝オッズ下限（穴馬抽出向け）
# ─────────────────────────────────────────────────────────────────────────


@register_condition("min_odds")
def check_min_odds(horse: HorseContext, race_ctx: RaceContext, params: dict) -> ConditionResult:
    """単勝オッズが min_tan_odds 以上かを判定する。人気馬を除外し穴馬を絞り込む。"""
    min_tan_odds = params.get("min_tan_odds", 10.0)

    if horse.tan_odds is None:
        return ConditionResult(passed=True, score=0.0, reason="オッズデータなし(判定保留)")
    if horse.tan_odds >= min_tan_odds:
        return ConditionResult(passed=True, score=0.0, reason=f"単勝{horse.tan_odds:.1f}倍(基準{min_tan_odds}倍以上)")
    return ConditionResult(passed=False, score=-1.0, reason=f"単勝{horse.tan_odds:.1f}倍(基準{min_tan_odds}倍未満)")
