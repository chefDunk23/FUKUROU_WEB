"""
tipster/conditions_v2.py
=========================
Phase 1 新条件群（本命選定・穴馬選定思想 v2）。

設計方針:
  - 既存 conditions.py / conditions_tr1.py は一切変更しない。
  - 条件関数はすべて (horse: HorseContext, race_ctx: RaceContext, params: dict) -> ConditionResult
    シグネチャ。conditions.py の CONDITION_REGISTRY / register_condition を共用する。
  - 第1層（ポテンシャル確認）+ 第2層（今回レースへの嵌まり度）の 2 層構造。
  - データ不足時は passed=None（保留）を返し、clear_count には加算しない（BET-6 ルール）。

フェーズ2 送り（Phase 1 では未実装）:
  - track_condition（馬場状態 良/稍/重/不良）: races テーブルに存在するが pipeline 未収録
  - 前走枠番不利: prev1_bracket_number が _BULK_SQL に未収録
  - 前走コーナー通過位置（展開不利判定）: HorseContext 未収録
  - f3_time（上がり3ハロン）: _BULK_SQL 未収録
  - v2_training_relative（調教前走比較 TR-1 相対版）: 複雑な時系列比較が必要、Phase 2 予定

詳細は PHASE1_DATA_AUDIT.md を参照。
"""
from __future__ import annotations

from .conditions import register_condition
from .models import ConditionResult, HorseContext, RaceContext


# ─────────────────────────────────────────────────────────────────────────
# ヘルパー
# ─────────────────────────────────────────────────────────────────────────

def _own_margin_sec(horse: HorseContext, past_idx: int) -> float | None:
    """past_races[past_idx] における自馬の勝ち馬との着差（秒）を返す。
    opponents_next_races の中から horse_id が一致するエントリを探す。
    """
    if past_idx >= len(horse.past_races):
        return None
    pr = horse.past_races[past_idx]
    for opp in pr.opponents_next_races:
        if opp.horse_id == horse.horse_id and opp.this_margin is not None:
            return opp.this_margin
    return None


def _is_leading_jockey(yr_wins: int | None, threshold: int) -> bool:
    return yr_wins is not None and yr_wins >= threshold


# ─────────────────────────────────────────────────────────────────────────
# 第1層: ポテンシャル確認条件
# ─────────────────────────────────────────────────────────────────────────


@register_condition("v2_past_margin")
def check_v2_past_margin(
    horse: HorseContext, race_ctx: RaceContext, params: dict
) -> ConditionResult:
    """第1層: 過去 N 走以内に勝ち馬との差 ≤ max_sec 秒の好走歴があるか。

    params:
        lookback     (int,   default 3):   参照走数（最大 5）
        max_sec      (float, default 1.0): 着差秒の上限
        bonus_score  (float, default 1.0): 好走歴あり時の加点
    """
    lookback = int(params.get("lookback", 3))
    max_sec = float(params.get("max_sec", 1.0))
    bonus_score = float(params.get("bonus_score", 1.0))

    margins_checked = 0
    best_margin = None
    for i in range(min(lookback, len(horse.past_races))):
        margin = _own_margin_sec(horse, i)
        if margin is None:
            continue
        margins_checked += 1
        if best_margin is None or margin < best_margin:
            best_margin = margin
        if margin <= max_sec:
            rank = horse.past_races[i].rank
            return ConditionResult(
                passed=True,
                score=bonus_score,
                reason=f"過去{i + 1}走前に着差{margin:.2f}秒（≤{max_sec}秒）の好走（{rank}着）",
            )

    if margins_checked == 0:
        return ConditionResult(passed=None, score=0.0, reason="着差データなし（判定保留）")

    return ConditionResult(
        passed=False,
        score=0.0,
        reason=f"過去{lookback}走以内に{max_sec}秒以内好走なし（最小着差{best_margin:.2f}秒）",
    )


@register_condition("v2_race_quality")
def check_v2_race_quality(
    horse: HorseContext, race_ctx: RaceContext, params: dict
) -> ConditionResult:
    """第1層: 前走の上位 N 頭が次走でも好成績を残したか（レースレベル評価）。

    既存 race_level と同系統だが複勝率（top3率）に特化し閾値を統一化。

    params:
        top_n              (int,   default 3):    前走上位 N 頭を評価対象
        min_next_horses    (int,   default 3):    次走データが必要な最小頭数
        min_place_rate     (float, default 0.35): 上位馬の次走複勝率下限
        bonus_score        (float, default 1.0):  通過時の加点
    """
    top_n = int(params.get("top_n", 3))
    min_next = int(params.get("min_next_horses", 3))
    min_place_rate = float(params.get("min_place_rate", 0.35))
    bonus_score = float(params.get("bonus_score", 1.0))

    if not horse.past_races:
        return ConditionResult(passed=None, score=0.0, reason="前走データなし")

    pr = horse.past_races[0]
    top_opps = [o for o in pr.opponents_next_races if o.this_rank is not None and o.this_rank <= top_n]
    with_next = [o for o in top_opps if o.next_race_rank is not None]

    if len(with_next) < min_next:
        return ConditionResult(
            passed=None,
            score=0.0,
            reason=f"前走上位{top_n}頭の次走データが{len(with_next)}頭のみ（要{min_next}頭）",
        )

    place_count = sum(1 for o in with_next if o.next_race_rank <= 3)
    place_rate = place_count / len(with_next)

    if place_rate >= min_place_rate:
        return ConditionResult(
            passed=True,
            score=bonus_score,
            reason=f"前走上位{top_n}頭の次走複勝率{place_rate:.0%}（≥{min_place_rate:.0%}）",
        )
    return ConditionResult(
        passed=False,
        score=0.0,
        reason=f"前走上位{top_n}頭の次走複勝率{place_rate:.0%}（<{min_place_rate:.0%}）",
    )


@register_condition("v2_class_change")
def check_v2_class_change(
    horse: HorseContext, race_ctx: RaceContext, params: dict
) -> ConditionResult:
    """第1層: クラス変化評価（昇級=様子見 / 同クラス=デフォルト通過 / 降級=積極評価）。

    今回クラスと前走クラスの差分:
      昇級（今回 > 前走）: upgrade_as_none=True なら passed=None（様子見）
      同クラス（差分 0）:   passed=True, score=0
      降級（今回 < 前走）: passed=True, score=downgrade_bonus（積極評価）

    params:
        downgrade_bonus  (float, default 1.0): 降級時の加点
        upgrade_as_none  (bool,  default true): 昇級を None（様子見）扱いにする
    """
    downgrade_bonus = float(params.get("downgrade_bonus", 1.0))
    upgrade_as_none = bool(params.get("upgrade_as_none", True))

    today_level = race_ctx.class_level
    if today_level is None:
        return ConditionResult(passed=None, score=0.0, reason="今回クラスデータなし")
    if not horse.past_races or horse.past_races[0].class_level is None:
        return ConditionResult(passed=None, score=0.0, reason="前走クラスデータなし")

    prev_level = horse.past_races[0].class_level
    diff = today_level - prev_level

    if diff > 0:
        if upgrade_as_none:
            return ConditionResult(
                passed=None,
                score=0.0,
                reason=f"昇級（L{prev_level}→L{today_level}）: 様子見",
            )
        return ConditionResult(passed=True, score=0.0, reason=f"昇級（L{prev_level}→L{today_level}）")
    elif diff < 0:
        return ConditionResult(
            passed=True,
            score=downgrade_bonus,
            reason=f"降級（L{prev_level}→L{today_level}）: 積極評価",
        )
    return ConditionResult(passed=True, score=0.0, reason=f"同クラス継続（L{today_level}）")


# ─────────────────────────────────────────────────────────────────────────
# 第2層: 今回レースへの嵌まり度条件
# ─────────────────────────────────────────────────────────────────────────


@register_condition("v2_distance_match")
def check_v2_distance_match(
    horse: HorseContext, race_ctx: RaceContext, params: dict
) -> ConditionResult:
    """第2層: 距離適性（前走比大幅距離変化 → 適性回復期待 / 同距離帯好走歴確認）。

    評価ロジック:
      1. 前走との距離差が band_big (default 400m) 以上 → 距離ミスマッチ解消としてボーナス加点
      2. 上記でなければ過去 lookback 走で今回距離帯（±band_margin m）の好走数を確認

    params:
        band_big     (int,   default 400):  大幅距離変化と見なすm数
        band_margin  (int,   default 200):  同距離帯の許容幅（m）
        bonus_score  (float, default 0.5):  条件クリア時の加点
        lookback     (int,   default 3):    過去好走歴確認走数
    """
    band_big = int(params.get("band_big", 400))
    band_margin = int(params.get("band_margin", 200))
    bonus_score = float(params.get("bonus_score", 0.5))
    lookback = int(params.get("lookback", 3))

    today_dist = race_ctx.distance
    if today_dist is None:
        return ConditionResult(passed=None, score=0.0, reason="今回距離データなし")

    # 前走との大幅距離変化チェック
    if horse.past_races and horse.past_races[0].distance is not None:
        prev_dist = horse.past_races[0].distance
        diff = abs(today_dist - prev_dist)
        if diff >= band_big:
            direction = "短縮" if today_dist < prev_dist else "延長"
            return ConditionResult(
                passed=True,
                score=bonus_score,
                reason=f"大幅距離{direction}（{prev_dist}m→{today_dist}m, 差{diff}m）: 距離適性回復期待",
            )

    # 過去 N 走の同距離帯好走歴
    same_dist_runs = 0
    good_runs = 0
    for i in range(min(lookback, len(horse.past_races))):
        pr = horse.past_races[i]
        if pr.distance is None:
            continue
        if abs(pr.distance - today_dist) <= band_margin:
            same_dist_runs += 1
            if pr.rank is not None and pr.rank <= 3:
                good_runs += 1

    if same_dist_runs == 0:
        return ConditionResult(passed=None, score=0.0, reason="同距離帯実績なし（判定保留）")
    if good_runs > 0:
        return ConditionResult(
            passed=True,
            score=bonus_score,
            reason=f"同距離帯（±{band_margin}m）で過去{good_runs}/{same_dist_runs}走好走",
        )
    return ConditionResult(
        passed=True,
        score=0.0,
        reason=f"同距離帯実績あり（{same_dist_runs}走、好走なし）",
    )


@register_condition("v2_jockey_positive")
def check_v2_jockey_positive(
    horse: HorseContext, race_ctx: RaceContext, params: dict
) -> ConditionResult:
    """第2層: 騎手評価（継続騎乗 or リーディング乗り替わり = 好評価）。

    3 値評価:
      継続騎乗（同じ騎手）                → passed=True, score=base_score
      リーディング乗り替わり（年 N 勝以上）  → passed=True, score=upgrade_bonus
      非リーディング乗り替わり              → passed=False

    params:
        top_jockey_threshold  (int,   default 30): リーディング年間勝利数閾値
        base_score            (float, default 0.5): 継続騎乗時の加点
        upgrade_bonus         (float, default 1.0): リーディング替わり時の加点
    """
    top_threshold = int(params.get("top_jockey_threshold", 30))
    base_score = float(params.get("base_score", 0.5))
    upgrade_bonus = float(params.get("upgrade_bonus", 1.0))

    jockey_id = horse.jockey_id
    if jockey_id is None:
        return ConditionResult(passed=None, score=0.0, reason="騎手データなし")

    prev_jockey_id = horse.prev_jockey_id
    if prev_jockey_id is None or jockey_id == prev_jockey_id:
        return ConditionResult(passed=True, score=base_score, reason="継続騎乗")

    # 乗り替わりの場合: 今回騎手がリーディングか
    if _is_leading_jockey(horse.jockey_yr_wins, top_threshold):
        return ConditionResult(
            passed=True,
            score=upgrade_bonus,
            reason=f"リーディング騎手（年{horse.jockey_yr_wins}勝）への乗り替わり",
        )
    return ConditionResult(
        passed=False,
        score=0.0,
        reason=f"非リーディング騎手（年{horse.jockey_yr_wins or 0}勝）への乗り替わり",
    )


@register_condition("v2_weight_favor")
def check_v2_weight_favor(
    horse: HorseContext, race_ctx: RaceContext, params: dict
) -> ConditionResult:
    """第2層: 斤量前走比較（軽減 = 有利 / 増量 = 不利）。

    params:
        decrease_threshold  (float, default 0.5): 軽減と見なす最小 kg
        increase_threshold  (float, default 0.5): 増量と見なす最小 kg
        decrease_bonus      (float, default 0.5): 軽減時の加点
        increase_penalty    (float, default -0.5): 増量時のスコアペナルティ（負値）
    """
    decrease_threshold = float(params.get("decrease_threshold", 0.5))
    increase_threshold = float(params.get("increase_threshold", 0.5))
    decrease_bonus = float(params.get("decrease_bonus", 0.5))
    increase_penalty = float(params.get("increase_penalty", -0.5))

    bw = horse.burden_weight
    pbw = horse.prev_burden_weight
    if bw is None or pbw is None:
        return ConditionResult(passed=None, score=0.0, reason="斤量データなし")

    diff = bw - pbw  # 正=増量, 負=軽減
    if diff <= -decrease_threshold:
        return ConditionResult(
            passed=True,
            score=decrease_bonus,
            reason=f"斤量軽減（{pbw}kg→{bw}kg, -{abs(diff):.1f}kg）",
        )
    if diff >= increase_threshold:
        return ConditionResult(
            passed=False,
            score=increase_penalty,
            reason=f"斤量増量（{pbw}kg→{bw}kg, +{diff:.1f}kg）",
        )
    return ConditionResult(
        passed=True,
        score=0.0,
        reason=f"斤量変化軽微（{pbw}kg→{bw}kg）",
    )


@register_condition("v2_interval_optimal")
def check_v2_interval_optimal(
    horse: HorseContext, race_ctx: RaceContext, params: dict
) -> ConditionResult:
    """第2層: 出走間隔評価（適正間隔 = 中2〜3週: 15〜28日）。

    params:
        optimal_min   (int,   default 15): 適正間隔下限（日）
        optimal_max   (int,   default 28): 適正間隔上限（日）
        bonus_score   (float, default 0.5): 適正間隔内の加点
        long_rest_min (int,   default 60): 長期休養明け判定日数（→ None で保留）
    """
    optimal_min = int(params.get("optimal_min", 15))
    optimal_max = int(params.get("optimal_max", 28))
    bonus_score = float(params.get("bonus_score", 0.5))
    long_rest_min = int(params.get("long_rest_min", 60))

    days = horse.prev_race_days_ago
    if days is None:
        return ConditionResult(passed=None, score=0.0, reason="出走間隔データなし（初出走等）")

    if optimal_min <= days <= optimal_max:
        return ConditionResult(
            passed=True,
            score=bonus_score,
            reason=f"適正間隔（{days}日）",
        )
    if days < optimal_min:
        return ConditionResult(
            passed=False,
            score=0.0,
            reason=f"短すぎる間隔（{days}日 < {optimal_min}日）",
        )
    if days >= long_rest_min:
        return ConditionResult(
            passed=None,
            score=0.0,
            reason=f"長期休養明け（{days}日）: 判定保留",
        )
    return ConditionResult(
        passed=True,
        score=0.0,
        reason=f"やや長め間隔（{days}日）",
    )


@register_condition("v2_surface_history")
def check_v2_surface_history(
    horse: HorseContext, race_ctx: RaceContext, params: dict
) -> ConditionResult:
    """第2層: 馬場適性（今回と同じ馬場種別での過去好走歴）。

    過去 lookback 走で今回と同じ surface（芝/ダート）での好走（rank ≤ min_place_rank）を確認。

    params:
        lookback         (int,   default 5): 参照走数
        min_place_rank   (int,   default 3): 好走と見なす着順上限
        bonus_score      (float, default 0.5): 好走歴あり時の加点
    """
    lookback = int(params.get("lookback", 5))
    min_place_rank = int(params.get("min_place_rank", 3))
    bonus_score = float(params.get("bonus_score", 0.5))

    today_surface = race_ctx.surface
    if today_surface is None:
        return ConditionResult(passed=None, score=0.0, reason="今回馬場データなし")

    same_surface_runs = 0
    good_runs = 0
    for i in range(min(lookback, len(horse.past_races))):
        pr = horse.past_races[i]
        if pr.surface != today_surface:
            continue
        same_surface_runs += 1
        if pr.rank is not None and pr.rank <= min_place_rank:
            good_runs += 1

    if same_surface_runs == 0:
        return ConditionResult(
            passed=None,
            score=0.0,
            reason=f"過去{lookback}走に同馬場（{today_surface}）実績なし（判定保留）",
        )
    if good_runs > 0:
        return ConditionResult(
            passed=True,
            score=bonus_score,
            reason=f"同馬場（{today_surface}）で過去{good_runs}/{same_surface_runs}走好走",
        )
    return ConditionResult(
        passed=False,
        score=0.0,
        reason=f"同馬場（{today_surface}）実績{same_surface_runs}走中好走なし",
    )


# ─────────────────────────────────────────────────────────────────────────
# Phase 2 新条件（Step0 追加）
# NOTE: 以下の条件は past_races に f3_time / track_condition が追加された場合に
#       fully functional になる。現状では pipeline 未収録のため passed=None を返す。
#       セグメント探索スクリプト (scripts/run_segment_search.py) では
#       拡張 SQL で直接取得した pandas データを使って同等のロジックを実装している。
# ─────────────────────────────────────────────────────────────────────────


@register_condition("v2_f3_superiority")
def check_v2_f3_superiority(
    horse: HorseContext, race_ctx: RaceContext, params: dict
) -> ConditionResult:
    """Phase2 Step0: 過去 N 走以内に上がり 3F が出走馬中上位だったか。

    末脚の質を評価する条件。race_entries.f3_time をレース内でランク付けし、
    上位 top_pct (デフォルト 33%) 以内なら True。

    現状: past_races に f3_time_rank_pct が未収録のため passed=None を返す。
    Pipeline 更新後（PastRaceInfo.f3_time_rank_pct 追加）に有効化予定。

    params:
        lookback   (int,   default 2):   参照走数
        top_pct    (float, default 0.33): 上位何%以内（0.33=上位3分の1）
        bonus_score(float, default 0.5): 条件クリア時の加点
    """
    return ConditionResult(
        passed=None,
        score=0.0,
        reason="f3_time_rank_pct は pipeline 未収録（フェーズ2 pipeline 整備後に有効化）",
    )


@register_condition("v2_track_condition_skill")
def check_v2_track_condition_skill(
    horse: HorseContext, race_ctx: RaceContext, params: dict
) -> ConditionResult:
    """Phase2 Step0: 過去走で特定の馬場状態（良/稍重/重/不良）での好走歴があるか。

    当日の馬場状態は使わない。「過去にどの馬場状態で好走したか」という持ちスキル評価。
    例: 過去 3 走以内に重馬場（track_condition=3 or 4）でランク 3 位以内の実績があれば True。

    現状: past_races に track_condition が未収録のため passed=None を返す。
    Pipeline 更新後（PastRaceInfo.track_condition 追加）に有効化予定。

    params:
        lookback       (int,   default 3):       参照走数
        target_conditions (list, default [3,4]): 対象馬場状態コード（3=重, 4=不良）
        min_place_rank (int,   default 3):       好走と見なす着順上限
        bonus_score    (float, default 0.5):     条件クリア時の加点
    """
    return ConditionResult(
        passed=None,
        score=0.0,
        reason="past_races.track_condition は pipeline 未収録（フェーズ2 pipeline 整備後に有効化）",
    )


@register_condition("v2_sire_surface_fit")
def check_v2_sire_surface_fit(
    horse: HorseContext, race_ctx: RaceContext, params: dict
) -> ConditionResult:
    """Phase2 Step0: 種牡馬の芝/ダート適性が今回コースと一致するか。

    bloodline_feature_store の sire_turf_wr / sire_dirt_wr を使い、
    今回コース（芝/ダート）に対して種牡馬が強い場合 True。

    現状: HorseContext に bloodline 情報が未収録のため passed=None を返す。
    Pipeline 更新後（HorseContext.sire_turf_wr / sire_dirt_wr 追加）に有効化予定。

    params:
        min_advantage (float, default 0.02): 反対コース比で何 %pt 以上優位なら True
        bonus_score   (float, default 0.5):  条件クリア時の加点
    """
    return ConditionResult(
        passed=None,
        score=0.0,
        reason="sire_turf_wr / sire_dirt_wr は pipeline 未収録（フェーズ2 pipeline 整備後に有効化）",
    )


@register_condition("v2_sire_distance_fit")
def check_v2_sire_distance_fit(
    horse: HorseContext, race_ctx: RaceContext, params: dict
) -> ConditionResult:
    """Phase2 Step0: 種牡馬の距離帯適性が今回距離と一致するか。

    bloodline_feature_store の sire_sprint_wr / sire_mile_wr / sire_middle_wr / sire_long_wr から
    今回距離帯に対応する勝率を取得し、種牡馬産駒の得意距離帯かどうかを評価する。

    現状: HorseContext に bloodline 情報が未収録のため passed=None を返す。

    params:
        bonus_score (float, default 0.5): 条件クリア時の加点
    """
    return ConditionResult(
        passed=None,
        score=0.0,
        reason="sire 距離適性データは pipeline 未収録（フェーズ2 pipeline 整備後に有効化）",
    )
