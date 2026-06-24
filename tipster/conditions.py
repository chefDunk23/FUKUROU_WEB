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


# クラス序列（class_direction 用）: 新馬=1 〜 G1=10
_JYOKEN_TO_CLASS_LEVEL = {
    "701": 1, "702": 1,  # 新馬・未出走
    "703": 2,            # 未勝利
    "005": 3,            # 1勝クラス
    "010": 4,            # 2勝クラス
    "016": 5,            # 3勝クラス
    "999": 6,            # OP
}
_GRADE_CODE_TO_CLASS_LEVEL = {"L": 7, "C": 8, "B": 9, "A": 10}


def _class_level_from_codes(grade_code: str | None, jyoken_cd_3: str | None) -> int | None:
    """races.grade_code / jyoken_cd_3 から新馬=1〜G1=10のクラス序列を返す。判定不能なら None。

    注意: race_detail_cache.payload.grade_code はこの A/B/C/L 表記とは別の
    数値エンコーディング（JV-Data生コード）であり、ここには渡せない。
    ライブパスでは _class_level_from_label（class_label文字列ベース）を使うこと。
    """
    g = (grade_code or "").strip()
    level = _GRADE_CODE_TO_CLASS_LEVEL.get(g)
    if level is not None:
        return level
    jy = (jyoken_cd_3 or "").strip()
    return _JYOKEN_TO_CLASS_LEVEL.get(jy)


_CLASS_LABEL_TO_LEVEL = {
    "G1": 10, "G2": 9, "G3": 8, "Listed": 7, "L": 7,
    "オープン": 6, "OP": 6,
    "3勝クラス": 5, "2勝クラス": 4, "1勝クラス": 3,
    "未勝利": 2, "未出走": 1, "新馬": 1,
}


def _class_level_from_label(class_label: str | None) -> int | None:
    """payload.class_label（"G1"/"3勝クラス"等の人間可読文字列）からクラス序列を返す。"""
    return _CLASS_LABEL_TO_LEVEL.get((class_label or "").strip())


def classify_pace_prediction(horses: list[HorseContext]) -> str | None:
    """出走馬の脚質構成（position_tendency）からペースを簡易予想する（"fast"/"medium"/"slow"）。

    本物のペースシミュレーション(src/features/pace_simulation_v1.py)はAI推論を伴い
    バックテスト全件には使えないため、「先行馬の割合が多いほど競り合いでハイペースに
    なりやすい」という簡易ヒューリスティックで代用する。engine.py / backtest.py で共用。
    """
    tendencies = [h.position_tendency for h in horses if h.position_tendency is not None]
    if len(tendencies) < 3:
        return None
    front_share = sum(1 for t in tendencies if t < 0.25) / len(tendencies)
    if front_share >= 0.35:
        return "fast"
    if front_share <= 0.10:
        return "slow"
    return "medium"


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


# ─────────────────────────────────────────────────────────────────────────
# 舞台適性: 同コース or 類似コースでの過去成績
# ─────────────────────────────────────────────────────────────────────────


@register_condition("course_fitness")
def check_course_fitness(horse: HorseContext, race_ctx: RaceContext, params: dict) -> ConditionResult:
    """同コース（競馬場×距離帯×芝ダート）、無ければ類似コースでの過去の好走歴を見る。"""
    distance_tolerance = params.get("distance_tolerance", 200)
    similar_courses: dict[str, list[str]] = params.get("similar_courses", {})

    if not horse.past_races or race_ctx.place_code is None or race_ctx.distance is None:
        return ConditionResult(passed=True, score=0.0, reason="コース経験データ不足(判定保留)")

    def _matches(pr, place_codes: set[str]) -> bool:
        if pr.place_code not in place_codes or pr.surface != race_ctx.surface or pr.distance is None:
            return False
        return abs(pr.distance - race_ctx.distance) <= distance_tolerance

    same_course = [pr for pr in horse.past_races if _matches(pr, {race_ctx.place_code})]
    if same_course:
        ranks = [pr.rank for pr in same_course if pr.rank is not None]
        if ranks and min(ranks) <= 3:
            return ConditionResult(
                passed=True, score=2.0, reason=f"同コース好走歴あり({min(ranks)}着)",
                detail={"races": len(same_course)},
            )
        return ConditionResult(passed=True, score=-1.0, reason="同コース惨敗歴のみ", detail={"races": len(same_course)})

    similar_codes = set(similar_courses.get(race_ctx.place_code, []))
    if similar_codes:
        similar = [pr for pr in horse.past_races if _matches(pr, similar_codes)]
        ranks = [pr.rank for pr in similar if pr.rank is not None]
        if ranks and min(ranks) <= 3:
            return ConditionResult(
                passed=True, score=1.0, reason=f"類似コースで好走({min(ranks)}着)",
                detail={"races": len(similar)},
            )

    return ConditionResult(passed=True, score=0.0, reason="コース経験なし(中立)")


# ─────────────────────────────────────────────────────────────────────────
# ペース展開 × ポジション適性
# ─────────────────────────────────────────────────────────────────────────


@register_condition("pace_position")
def check_pace_position(horse: HorseContext, race_ctx: RaceContext, params: dict) -> ConditionResult:
    """race_ctx.pace_prediction(今回のレース構成からの簡易予想) × horse.position_tendency(脚質)。

    track_bias_fit は過去のコースバイアス統計、pace_position は今回の出走馬構成からの
    動的なペース予想という違いがあるため、両条件は独立して併用する。
    """
    pace = race_ctx.pace_prediction
    pos = horse.position_tendency
    if pace is None or pos is None:
        return ConditionResult(passed=True, score=0.0, reason="ペース/脚質データ不足(判定保留)")

    style = "逃げ・先行" if pos < 0.25 else ("追込" if pos > 0.60 else "好位・差し")

    if pace == "fast":
        if style in ("好位・差し", "追込"):
            return ConditionResult(passed=True, score=2.0, reason=f"ハイペースで{style}有利")
        return ConditionResult(passed=True, score=-1.0, reason="ハイペースで前崩れリスク")
    if pace == "slow":
        if style == "逃げ・先行":
            return ConditionResult(passed=True, score=2.0, reason="スローで先行有利")
        if style == "追込":
            return ConditionResult(passed=True, score=-1.0, reason="スローで届かないリスク")
        return ConditionResult(passed=True, score=0.0, reason="スロー想定(好位・差しは中立)")
    return ConditionResult(passed=True, score=0.0, reason="平均ペース想定(中立)")


# ─────────────────────────────────────────────────────────────────────────
# クラスの方向: 昇級/据え置き/降級
# ─────────────────────────────────────────────────────────────────────────


@register_condition("class_direction")
def check_class_direction(horse: HorseContext, race_ctx: RaceContext, params: dict) -> ConditionResult:
    """前走クラスと今回クラスの序列(新馬=1〜G1=10)を比較し、昇級/据え置き/降級を判定する。"""
    today_level = race_ctx.class_level
    if today_level is None or not horse.past_races:
        return ConditionResult(passed=True, score=0.0, reason="クラスデータ不足(判定保留)")

    prev_level = horse.past_races[0].class_level
    if prev_level is None:
        return ConditionResult(passed=True, score=0.0, reason="前走クラスデータ不足(判定保留)")

    if today_level == 10 and prev_level == 10:
        return ConditionResult(passed=True, score=3.0, reason="G1からG1(最高峰据え置き)")
    if today_level < prev_level:
        return ConditionResult(
            passed=True, score=2.0, reason=f"クラス降級(前走level{prev_level}→今回level{today_level})",
        )
    if today_level == prev_level:
        return ConditionResult(passed=True, score=1.0, reason="クラス据え置き")
    return ConditionResult(
        passed=True, score=-1.0, reason=f"クラス昇級(前走level{prev_level}→今回level{today_level})",
    )


# ─────────────────────────────────────────────────────────────────────────
# 出走間隔適性 + 海外帰り判定
# ─────────────────────────────────────────────────────────────────────────

_JRA_PLACE_CODES = {f"{i:02d}" for i in range(1, 11)}
_RENTOU_THRESHOLD_DAYS = 14  # これ以下は連闘とみなす（spec未パラメータ化の固定値）


@register_condition("rest_interval")
def check_rest_interval(horse: HorseContext, race_ctx: RaceContext, params: dict) -> ConditionResult:
    """前走からの出走間隔の適性、および前走が海外/地方だった場合の減点を判定する。"""
    optimal_min = params.get("optimal_min", 15)
    optimal_max = params.get("optimal_max", 35)
    long_rest_threshold = params.get("long_rest_threshold", 71)
    overseas_penalty = params.get("overseas_penalty", -2)

    # overseas_interim_place_code: 直近のJRA前走と今回の間に地方/海外出走が見つかった場合に設定される
    # （バックテストのバルクロードはJRA限定のため、past_races[].place_code は常にJRAになる）。
    prev_place = horse.overseas_interim_place_code or (
        horse.past_races[0].place_code if horse.past_races else None
    )
    if prev_place is not None and prev_place not in _JRA_PLACE_CODES:
        return ConditionResult(
            passed=True, score=overseas_penalty, reason=f"前走が海外/地方(place_code={prev_place})",
        )

    days_ago = horse.prev_race_days_ago
    if days_ago is None:
        return ConditionResult(passed=True, score=0.0, reason="出走間隔データ不足(判定保留)")

    if days_ago <= _RENTOU_THRESHOLD_DAYS:
        return ConditionResult(passed=True, score=-1.0, reason=f"連闘(中{days_ago}日,消耗リスク)")
    if days_ago < optimal_min:
        return ConditionResult(passed=True, score=0.0, reason=f"間隔やや短い(中{days_ago}日,中立)")
    if days_ago <= optimal_max:
        return ConditionResult(passed=True, score=1.0, reason=f"適正間隔(中{days_ago}日)")
    if days_ago < long_rest_threshold:
        return ConditionResult(passed=True, score=0.0, reason=f"やや間隔空き(中{days_ago}日,中立)")
    return ConditionResult(passed=True, score=-1.0, reason=f"休み明け(中{days_ago}日)")


# ─────────────────────────────────────────────────────────────────────────
# 騎手の勝負気配(jockey_change拡張) + 当該コース巧者判定
# ─────────────────────────────────────────────────────────────────────────


@register_condition("jockey_intent")
def check_jockey_intent(horse: HorseContext, race_ctx: RaceContext, params: dict) -> ConditionResult:
    """jockey_change の3段階判定を内包し、騎手の当該競馬場での巧者度による加点を追加する。"""
    base = check_jockey_change(horse, race_ctx, params)

    bonus_pct = params.get("course_winrate_bonus_pct", 20)
    venue_rate = horse.jockey_venue_win_rate
    overall_rate = horse.jockey_overall_win_rate
    if venue_rate is not None and overall_rate is not None and overall_rate > 1e-9:
        relative_gain = (venue_rate - overall_rate) / overall_rate * 100
        if relative_gain >= bonus_pct:
            return ConditionResult(
                passed=base.passed,
                score=base.score + 1.0,
                reason=f"{base.reason} + コース巧者(当該場勝率{venue_rate:.1%}, 全体比+{relative_gain:.0f}%)",
                detail={"base_reason": base.reason, "venue_win_rate": venue_rate, "overall_win_rate": overall_rate},
            )
    return base
