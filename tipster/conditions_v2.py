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
    # 変化なし or 軽微増量（+0.5未満）→ 軽減ではないので False
    if diff < 0:
        return ConditionResult(
            passed=True,
            score=0.0,
            reason=f"軽微な斤量軽減（{pbw}kg→{bw}kg, -{abs(diff):.1f}kg）",
        )
    return ConditionResult(
        passed=False,
        score=0.0,
        reason=f"斤量変化なし・軽微増量（{pbw}kg→{bw}kg）",
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
# Phase 2 S-1/B-2 条件（正式実装: pipeline 対応済み）
# PHASE2_VERIFIED_PATTERNS.md 記載の3条件をそのまま移植。
# run_racecourse_search.py のローカル実装と同一ロジック。
# ─────────────────────────────────────────────────────────────────────────

# 坂あり競馬場コード（racecourse_features.json の has_hill=true）
# 福島(03)/東京(05)/中山(06)/中京(07)/阪神(09)
_HILL_PLACE_CODES: frozenset[str] = frozenset({"03", "05", "06", "07", "09"})


@register_condition("v2_f3_top")
def check_v2_f3_top(
    horse: HorseContext, race_ctx: RaceContext, params: dict
) -> ConditionResult:
    """Phase 2 S-1/B-2: 前走の上がり3Fがそのレースの出走馬中、上位 top_pct 以内か。

    Phase 2 f3_top 条件の本番実装。
    prev1_f3pct = 前走の f3_time_rank_pct (= f3_rank / field_size, 0=最速, 1=最遅)。
    pipeline 対応: PastRaceInfo.f3_time_rank_pct（backtest.py _BULK_SQL で f3_time 追加済み）。

    params:
        top_pct      (float, default 0.33): 上位何分率以内か（0.33=上位1/3）
        lookback     (int,   default 1):    参照走数（Phase 2 は prev1 のみ。拡張可）
        bonus_score  (float, default 0.5):  条件クリア時の加点
    """
    top_pct = float(params.get("top_pct", 0.33))
    lookback = int(params.get("lookback", 1))
    bonus_score = float(params.get("bonus_score", 0.5))

    for i in range(min(lookback, len(horse.past_races))):
        pr = horse.past_races[i]
        if pr.f3_time_rank_pct is None:
            continue
        pct = pr.f3_time_rank_pct
        if pct <= top_pct:
            return ConditionResult(
                passed=True,
                score=bonus_score,
                reason=f"前{i + 1}走の上がり3F順位パーセンタイル{pct:.2f}（≤{top_pct:.2f}=上位{top_pct:.0%}以内）",
            )
        return ConditionResult(
            passed=False,
            score=0.0,
            reason=f"前{i + 1}走の上がり3F順位パーセンタイル{pct:.2f}（>{top_pct:.2f}=上位{top_pct:.0%}外）",
        )

    return ConditionResult(passed=None, score=0.0, reason="上がり3Fデータなし（判定保留）")


@register_condition("v2_hill_fit")
def check_v2_hill_fit(
    horse: HorseContext, race_ctx: RaceContext, params: dict
) -> ConditionResult:
    """Phase 2 S-1: 今回と同じ「坂あり/なし」区分の競馬場で過去好走歴があるか。

    Phase 2 hill_fit 条件の本番実装。
    坂あり競馬場: 福島(03)/東京(05)/中山(06)/中京(07)/阪神(09)

    判定ロジック:
      - 今回の place_code が坂ありかどうかを判定
      - 過去 lookback 走の place_code を同じ坂区分で絞り込み
      - 同区分出走が一度もない → passed=None（判定保留）
      - 同区分出走あり かつ 3着以内が1回以上 → passed=True
      - 同区分出走あり かつ 3着以内なし → passed=False

    params:
        lookback        (int,   default 3): 参照走数
        min_place_rank  (int,   default 3): 好走と見なす着順上限
        bonus_score     (float, default 0.5): 条件クリア時の加点
    """
    lookback = int(params.get("lookback", 3))
    min_place_rank = int(params.get("min_place_rank", 3))
    bonus_score = float(params.get("bonus_score", 0.5))

    today_pc = race_ctx.place_code
    if today_pc is None:
        return ConditionResult(passed=None, score=0.0, reason="今回競馬場コードなし（判定保留）")

    today_is_hill = today_pc in _HILL_PLACE_CODES
    hill_label = "坂あり" if today_is_hill else "坂なし"

    same_type_runs = 0
    good_runs = 0
    for i in range(min(lookback, len(horse.past_races))):
        pr = horse.past_races[i]
        if pr.place_code is None:
            continue
        prev_is_hill = pr.place_code in _HILL_PLACE_CODES
        if prev_is_hill != today_is_hill:
            continue
        same_type_runs += 1
        if pr.rank is not None and pr.rank <= min_place_rank:
            good_runs += 1

    if same_type_runs == 0:
        return ConditionResult(
            passed=None,
            score=0.0,
            reason=f"{hill_label}競馬場の出走実績なし（過去{lookback}走以内、判定保留）",
        )
    if good_runs > 0:
        return ConditionResult(
            passed=True,
            score=bonus_score,
            reason=f"{hill_label}競馬場で過去{good_runs}/{same_type_runs}走好走（3着以内）",
        )
    return ConditionResult(
        passed=False,
        score=0.0,
        reason=f"{hill_label}競馬場の出走{same_type_runs}走中好走なし",
    )


@register_condition("v2_sire_venue")
def check_v2_sire_venue(
    horse: HorseContext, race_ctx: RaceContext, params: dict
) -> ConditionResult:
    """Phase 2 S-1: 種牡馬の産駒の今回競馬場 top3 率が全体 top3 率を上回るか。

    Phase 2 sire_venue 条件の本番実装。
    PIT-safe: HorseContext.sire_venue_top3 はレース日以前の最新 sire_feature_store スナップショット。

    判定ロジック（run_racecourse_search.py と同一）:
      - sire.venue_{XX}_top3_rate > sire.top3_rate（overall）
      - かつ venue_{XX}_count >= min_count

    params:
        min_count    (int,   default 10):   会場別実績の最低サンプル数
        bonus_score  (float, default 0.5):  条件クリア時の加点
    """
    min_count = int(params.get("min_count", 10))
    bonus_score = float(params.get("bonus_score", 0.5))

    today_pc = race_ctx.place_code
    if today_pc is None:
        return ConditionResult(passed=None, score=0.0, reason="今回競馬場コードなし（判定保留）")

    if not horse.sire_venue_top3:
        return ConditionResult(passed=None, score=0.0, reason="種牡馬会場適性データなし（判定保留）")

    overall = horse.sire_venue_top3.get("overall")
    if overall is None:
        return ConditionResult(passed=None, score=0.0, reason="種牡馬全体 top3 率データなし（判定保留）")

    venue_rate = horse.sire_venue_top3.get(today_pc)
    if venue_rate is None:
        return ConditionResult(
            passed=None,
            score=0.0,
            reason=f"種牡馬の当該競馬場({today_pc})実績 {min_count} 頭未満（判定保留）",
        )

    if venue_rate > overall:
        return ConditionResult(
            passed=True,
            score=bonus_score,
            reason=f"種牡馬産駒: 競馬場({today_pc}) top3率 {venue_rate:.1%} > 全体 {overall:.1%}（得意会場）",
        )
    return ConditionResult(
        passed=False,
        score=0.0,
        reason=f"種牡馬産駒: 競馬場({today_pc}) top3率 {venue_rate:.1%} ≤ 全体 {overall:.1%}（優位なし）",
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


# ─────────────────────────────────────────────────────────────────────────
# BET-7: 馬場別条件（v2_baba_track_record / v2_sire_baba_fit / v2_heavy_track_stamina）
# ─────────────────────────────────────────────────────────────────────────

_BABA_LABELS = ["良", "稍重", "重", "不良"]


@register_condition("v2_baba_track_record")
def check_v2_baba_track_record(
    horse: HorseContext, race_ctx: RaceContext, params: dict
) -> ConditionResult:
    """BET-7: 対象馬場での過去成績が基準を満たすか。

    HorseContext.baba_record から params["baba"] の馬場成績を取得し判定する。
    当日の馬場状態は使わない。4パターン（良/稍重/重/不良）の事前計算に対応。

    params:
        baba            (str,   default "良"):    評価対象馬場（"良"/"稍重"/"重"/"不良"）
        min_runs        (int,   default 3):       最小出走数（未満は None）
        pass_rate       (float, default 0.30):    passed=True の複勝率下限
        fail_rate       (float, default 0.15):    passed=False の複勝率上限（苦手判定）
        bonus_score     (float, default 1.0):     passed=True 時加点
        penalty_score   (float, default -1.0):    passed=False 時減点
    """
    baba = str(params.get("baba", "良"))
    min_runs = int(params.get("min_runs", 3))
    pass_rate = float(params.get("pass_rate", 0.30))
    fail_rate = float(params.get("fail_rate", 0.15))
    bonus_score = float(params.get("bonus_score", 1.0))
    penalty_score = float(params.get("penalty_score", -1.0))

    if baba not in _BABA_LABELS:
        return ConditionResult(passed=None, score=0.0, reason=f"不明な馬場: {baba}")

    record = (horse.baba_record or {}).get(baba)
    if record is None:
        return ConditionResult(passed=None, score=0.0, reason=f"{baba}馬場: 出走実績なし（判定保留）")

    runs, placed = record
    if runs < min_runs:
        rate = placed / runs if runs > 0 else 0.0
        return ConditionResult(
            passed=None,
            score=0.0,
            reason=f"{baba}馬場: {placed}/{runs}回 — サンプル少（{min_runs}走未満、判定保留）",
            detail={"runs": runs, "placed": placed},
        )

    rate = placed / runs
    if rate >= pass_rate:
        return ConditionResult(
            passed=True,
            score=bonus_score,
            reason=f"{baba}馬場: {placed}/{runs}回 ({rate:.0%}) — 基準({pass_rate:.0%})超え",
            detail={"runs": runs, "placed": placed, "rate": rate},
        )
    if rate < fail_rate:
        return ConditionResult(
            passed=False,
            score=penalty_score,
            reason=f"{baba}馬場: {placed}/{runs}回 ({rate:.0%}) — 苦手（{fail_rate:.0%}未満）",
            detail={"runs": runs, "placed": placed, "rate": rate},
        )
    return ConditionResult(
        passed=False,
        score=0.0,
        reason=f"{baba}馬場: {placed}/{runs}回 ({rate:.0%}) — 基準({pass_rate:.0%})未満",
        detail={"runs": runs, "placed": placed, "rate": rate},
    )


@register_condition("v2_sire_baba_fit")
def check_v2_sire_baba_fit(
    horse: HorseContext, race_ctx: RaceContext, params: dict
) -> ConditionResult:
    """BET-7: 種牡馬の産駒が対象馬場で全体平均を上回るか（PIT-safe sire_feature_store）。

    sire_baba_top3 の shift 値（全体平均との差）を使う。
    shift > threshold なら True（種牡馬の産駒は当該馬場が得意）。

    params:
        baba            (str,   default "良"):     評価対象馬場
        threshold       (float, default 0.02):    top3_rate_shift の合格下限（+2%pt 以上）
        bonus_score     (float, default 0.5):     passed=True 時加点
    """
    baba = str(params.get("baba", "良"))
    threshold = float(params.get("threshold", 0.02))
    bonus_score = float(params.get("bonus_score", 0.5))

    if baba not in _BABA_LABELS:
        return ConditionResult(passed=None, score=0.0, reason=f"不明な馬場: {baba}")

    if not horse.sire_baba_top3:
        return ConditionResult(passed=None, score=0.0, reason="種牡馬馬場データなし（判定保留）")

    shift = horse.sire_baba_top3.get(baba)
    if shift is None:
        return ConditionResult(passed=None, score=0.0, reason=f"種牡馬の{baba}馬場データなし（判定保留）")

    if shift >= threshold:
        return ConditionResult(
            passed=True,
            score=bonus_score,
            reason=f"種牡馬産駒: {baba}馬場 複勝率shift={shift:+.1%}（全体比優位）",
            detail={"baba": baba, "shift": shift},
        )
    return ConditionResult(
        passed=False,
        score=0.0,
        reason=f"種牡馬産駒: {baba}馬場 複勝率shift={shift:+.1%}（優位なし）",
        detail={"baba": baba, "shift": shift},
    )


@register_condition("v2_heavy_track_stamina")
def check_v2_heavy_track_stamina(
    horse: HorseContext, race_ctx: RaceContext, params: dict
) -> ConditionResult:
    """BET-7: 重馬場/不良馬場でのスタミナ・パワー適性を評価する。

    対象: 重馬場または不良馬場のシミュレーション（params["baba"] が "重" or "不良"）のみ。
    良/稍重の場合は passed=None（評価不要）を返す。

    baba_record の 重+不良 合算成績で判定:
      - 合算 3 走以上 かつ 複勝率 30% 以上 → True（道悪巧者）
      - 合算 3 走以上 かつ 複勝率 15% 未満 → False（道悪苦手）
      - データ不足 → None

    params:
        baba            (str,   default "重"):     評価対象馬場（重/不良のみ有効）
        min_runs        (int,   default 3):        合算最小出走数
        pass_rate       (float, default 0.30):     passed=True の複勝率下限
        fail_rate       (float, default 0.15):     passed=False の複勝率上限
        bonus_score     (float, default 1.0):      passed=True 時加点
        penalty_score   (float, default -1.5):     passed=False 時減点（道悪は致命的）
    """
    baba = str(params.get("baba", "重"))
    min_runs = int(params.get("min_runs", 3))
    pass_rate = float(params.get("pass_rate", 0.30))
    fail_rate = float(params.get("fail_rate", 0.15))
    bonus_score = float(params.get("bonus_score", 1.0))
    penalty_score = float(params.get("penalty_score", -1.5))

    # 良/稍重は対象外（この条件は道悪専用）
    if baba not in ("重", "不良"):
        return ConditionResult(
            passed=None,
            score=0.0,
            reason=f"{baba}馬場ではこの条件は適用されない（道悪専用条件）",
        )

    record = horse.baba_record or {}
    # 重 + 不良 の合算
    total_runs = total_placed = 0
    for label in ("重", "不良"):
        if label in record:
            r, p = record[label]
            total_runs += r
            total_placed += p

    if total_runs == 0:
        return ConditionResult(
            passed=None,
            score=0.0,
            reason="道悪（重/不良）での出走実績なし（判定保留）",
        )
    if total_runs < min_runs:
        return ConditionResult(
            passed=None,
            score=0.0,
            reason=f"道悪合算: {total_placed}/{total_runs}回 — サンプル少（{min_runs}走未満、判定保留）",
        )

    rate = total_placed / total_runs
    if rate >= pass_rate:
        return ConditionResult(
            passed=True,
            score=bonus_score,
            reason=f"道悪巧者: 重/不良合算 {total_placed}/{total_runs}回 ({rate:.0%})",
            detail={"runs": total_runs, "placed": total_placed, "rate": rate},
        )
    if rate < fail_rate:
        return ConditionResult(
            passed=False,
            score=penalty_score,
            reason=f"道悪苦手: 重/不良合算 {total_placed}/{total_runs}回 ({rate:.0%}) — {fail_rate:.0%}未満",
            detail={"runs": total_runs, "placed": total_placed, "rate": rate},
        )
    return ConditionResult(
        passed=False,
        score=0.0,
        reason=f"道悪中程度: 重/不良合算 {total_placed}/{total_runs}回 ({rate:.0%})",
        detail={"runs": total_runs, "placed": total_placed, "rate": rate},
    )


# ─────────────────────────────────────────────────────────────────────────
# 追加条件（2026-06-28）: 展開予測 / 枠順有利不利 / 開催進行度 / 相手勝ち上がり
# ─────────────────────────────────────────────────────────────────────────


@register_condition("v2_pace_match")
def check_v2_pace_match(
    horse: HorseContext, race_ctx: RaceContext, params: dict
) -> ConditionResult:
    """展開予測: 出走馬の脚質分布から今回の展開を推定し対象馬との適合を判定。

    逃げ馬（position_tendency < front_cut）が solo_front 頭以下
      → 逃げ/先行馬に有利（単騎/少数逃げ展開）
    逃げ馬が crowded_front 頭以上
      → ハイペース → 差し/追込馬に有利

    params:
        front_cut     (float, default 0.25): position_tendency がこれ未満を逃げ/先行に分類
        stalker_cut   (float, default 0.60): これ以上を差し/追込に分類
        solo_front    (int,   default 1):    単騎逃げと判定する逃げ馬頭数上限
        crowded_front (int,   default 3):    ハイペース判定の逃げ馬頭数下限
        bonus_score   (float, default 1.0):  マッチした場合の加点
    """
    front_cut     = float(params.get("front_cut", 0.25))
    stalker_cut   = float(params.get("stalker_cut", 0.60))
    solo_front    = int(params.get("solo_front", 1))
    crowded_front = int(params.get("crowded_front", 3))
    bonus_score   = float(params.get("bonus_score", 1.0))

    my_tend = horse.position_tendency
    if my_tend is None:
        return ConditionResult(passed=None, score=0.0, reason="脚質データなし（判定保留）")

    valid_horses = [h for h in race_ctx.horses if h.position_tendency is not None]
    if len(valid_horses) < max(3, len(race_ctx.horses) // 2):
        return ConditionResult(passed=None, score=0.0, reason="脚質データ不足（判定保留）")

    front_count = sum(1 for h in valid_horses if h.position_tendency < front_cut)
    is_front_runner   = my_tend < front_cut
    is_stalker_closer = my_tend >= stalker_cut

    if front_count <= solo_front and is_front_runner:
        return ConditionResult(
            passed=True,
            score=bonus_score,
            reason=f"単騎/少数逃げ展開: 逃げ/先行馬{front_count}頭のみ → 前付け有利",
            detail={"front_count": front_count, "position_tendency": my_tend},
        )
    if front_count >= crowded_front and is_stalker_closer:
        return ConditionResult(
            passed=True,
            score=bonus_score,
            reason=f"多頭逃げのハイペース展開: 逃げ馬{front_count}頭 → 差し/追込有利",
            detail={"front_count": front_count, "position_tendency": my_tend},
        )
    if front_count <= solo_front and is_stalker_closer:
        return ConditionResult(
            passed=False,
            score=0.0,
            reason=f"スローペース想定: 差し/追込馬には不向き（逃げ{front_count}頭）",
            detail={"front_count": front_count, "position_tendency": my_tend},
        )
    if front_count >= crowded_front and is_front_runner:
        return ConditionResult(
            passed=False,
            score=0.0,
            reason=f"ハイペース想定: 前付け馬には苦しい展開（逃げ{front_count}頭）",
            detail={"front_count": front_count, "position_tendency": my_tend},
        )
    return ConditionResult(
        passed=None,
        score=0.0,
        reason=f"展開中立: 逃げ馬{front_count}頭（明確な有利不利なし）",
        detail={"front_count": front_count, "position_tendency": my_tend},
    )


@register_condition("v2_bracket_bias")
def check_v2_bracket_bias(
    horse: HorseContext, race_ctx: RaceContext, params: dict
) -> ConditionResult:
    """枠順の有利不利: inner_bias_pit（コースバイアス）と枠番の組み合わせで判定。

    inner_bias_pit > threshold: 内枠有利 → 枠番1〜inner_cut が加点対象
    inner_bias_pit < -threshold: 外枠有利 → 枠番outer_cut〜8 が加点対象
    ダートでバイアスデータなし: 内枠不利の経験則を適用（dirt_inner_penalty=True 時）

    params:
        bias_threshold     (float, default 0.1):  bias_pit の絶対値がこれ以上で有意とみなす
        inner_cut          (int,   default 3):    内枠と判定する枠番上限
        outer_cut          (int,   default 7):    外枠と判定する枠番下限
        dirt_inner_penalty (bool,  default True): ダート内枠不利をデフォルト適用するか
        bonus_score        (float, default 0.8):  有利枠の加点
        penalty_score      (float, default -0.5): 不利枠の減点
    """
    bias_threshold     = float(params.get("bias_threshold", 0.1))
    inner_cut          = int(params.get("inner_cut", 3))
    outer_cut          = int(params.get("outer_cut", 7))
    dirt_inner_penalty = bool(params.get("dirt_inner_penalty", True))
    bonus_score        = float(params.get("bonus_score", 0.8))
    penalty_score      = float(params.get("penalty_score", -0.5))

    wakuban = horse.wakuban
    if wakuban is None:
        return ConditionResult(passed=None, score=0.0, reason="枠番データなし（判定保留）")

    bias        = race_ctx.inner_bias_pit
    surface     = race_ctx.surface or ""
    bias_source = race_ctx.bias_source

    if bias is None or abs(bias) < bias_threshold:
        if surface == "ダート" and dirt_inner_penalty and bias_source == "none":
            if wakuban <= inner_cut:
                return ConditionResult(
                    passed=False,
                    score=penalty_score,
                    reason=f"ダート内枠不利（経験則）: 枠{wakuban}番 — バイアスデータなし",
                    detail={"wakuban": wakuban, "bias": bias, "source": bias_source},
                )
        return ConditionResult(
            passed=None,
            score=0.0,
            reason=f"枠順バイアス中立: 有利不利なし（inner_bias={bias}）",
            detail={"wakuban": wakuban, "bias": bias},
        )

    is_inner = wakuban <= inner_cut
    is_outer = wakuban >= outer_cut

    if bias > bias_threshold and is_inner:
        return ConditionResult(
            passed=True,
            score=bonus_score,
            reason=f"内枠有利バイアス: 枠{wakuban}番（inner_bias={bias:+.2f}）",
            detail={"wakuban": wakuban, "bias": bias, "source": bias_source},
        )
    if bias < -bias_threshold and is_outer:
        return ConditionResult(
            passed=True,
            score=bonus_score,
            reason=f"外枠有利バイアス: 枠{wakuban}番（inner_bias={bias:+.2f}）",
            detail={"wakuban": wakuban, "bias": bias, "source": bias_source},
        )
    if bias > bias_threshold and is_outer:
        return ConditionResult(
            passed=False,
            score=penalty_score,
            reason=f"内枠有利バイアスで外枠不利: 枠{wakuban}番（inner_bias={bias:+.2f}）",
            detail={"wakuban": wakuban, "bias": bias, "source": bias_source},
        )
    if bias < -bias_threshold and is_inner:
        return ConditionResult(
            passed=False,
            score=penalty_score,
            reason=f"外枠有利バイアスで内枠不利: 枠{wakuban}番（inner_bias={bias:+.2f}）",
            detail={"wakuban": wakuban, "bias": bias, "source": bias_source},
        )
    return ConditionResult(
        passed=None,
        score=0.0,
        reason=f"中枠（バイアスあるが中間枠）: 枠{wakuban}番",
        detail={"wakuban": wakuban, "bias": bias},
    )


@register_condition("v2_race_order")
def check_v2_race_order(
    horse: HorseContext, race_ctx: RaceContext, params: dict
) -> ConditionResult:
    """開催進行度: 後半レース（R9以降）での差し馬有利を評価する。

    JRA race_id は YYYYMMDDPPNN 形式（末尾2桁がレース番号）。
    後半レースは馬場が荒れやすく、差し/追込馬が有利になる傾向がある。

    params:
        late_race_num  (int,   default 9):    後半開催と判定するレース番号下限
        front_cut      (float, default 0.35): position_tendency がこれ未満を前付け馬
        bonus_score    (float, default 0.6):  後半×差し/追込で加点
        penalty_score  (float, default -0.3): 後半×逃げ/先行で減点
    """
    late_race_num = int(params.get("late_race_num", 9))
    front_cut     = float(params.get("front_cut", 0.35))
    bonus_score   = float(params.get("bonus_score", 0.6))
    penalty_score = float(params.get("penalty_score", -0.3))

    race_id = race_ctx.race_id
    if not race_id or len(race_id) < 2:
        return ConditionResult(passed=None, score=0.0, reason="race_id が不正（判定保留）")

    try:
        race_num = int(race_id[-2:])
    except ValueError:
        return ConditionResult(passed=None, score=0.0, reason="race_id からレース番号取得失敗（判定保留）")

    if not (1 <= race_num <= 12):
        return ConditionResult(passed=None, score=0.0, reason=f"レース番号が範囲外: {race_num}（判定保留）")

    is_late = race_num >= late_race_num
    if not is_late:
        return ConditionResult(
            passed=None,
            score=0.0,
            reason=f"前半開催（R{race_num}）: 馬場荒れ前のため中立",
            detail={"race_num": race_num},
        )

    my_tend = horse.position_tendency
    if my_tend is None:
        return ConditionResult(
            passed=None,
            score=0.0,
            reason=f"後半開催（R{race_num}）: 脚質データなし（判定保留）",
            detail={"race_num": race_num},
        )

    if my_tend >= front_cut:
        return ConditionResult(
            passed=True,
            score=bonus_score,
            reason=f"後半開催（R{race_num}）×差し/追込: 荒れ馬場で有利な展開",
            detail={"race_num": race_num, "position_tendency": my_tend},
        )
    return ConditionResult(
        passed=False,
        score=penalty_score,
        reason=f"後半開催（R{race_num}）×前付け: 荒れ馬場では前が苦しくなる可能性",
        detail={"race_num": race_num, "position_tendency": my_tend},
    )


@register_condition("v2_opponent_winners")
def check_v2_opponent_winners(
    horse: HorseContext, race_ctx: RaceContext, params: dict
) -> ConditionResult:
    """過去相手の勝ち上がり頭数: 前走対戦相手のうちその後のレースで勝った馬が多いほど前走レベルが高い。

    PastRaceInfo.opponents_next_races の next_race_rank == 1 を集計。
    タイムラグ（2〜3週）があるため next_race_rank が None の相手は除外。

    params:
        lookback      (int,   default 1):  過去何走をチェックするか（1=前走のみ）
        min_winners   (int,   default 2):  passed=True とする勝ち馬頭数下限
        min_known     (int,   default 4):  次走結果が判明している対戦相手の最小数
        bonus_score   (float, default 1.0): passed=True 時の加点
    """
    lookback    = int(params.get("lookback", 1))
    min_winners = int(params.get("min_winners", 2))
    min_known   = int(params.get("min_known", 4))
    bonus_score = float(params.get("bonus_score", 1.0))

    total_known   = 0
    total_winners = 0

    for i in range(min(lookback, len(horse.past_races))):
        pr = horse.past_races[i]
        for opp in pr.opponents_next_races:
            if opp.next_race_rank is None:
                continue
            total_known += 1
            if opp.next_race_rank == 1:
                total_winners += 1

    if total_known < min_known:
        return ConditionResult(
            passed=None,
            score=0.0,
            reason=f"次走結果判明馬が少ない（{total_known}/{min_known}頭以上必要）（判定保留）",
            detail={"known": total_known, "winners": total_winners},
        )

    if total_winners >= min_winners:
        return ConditionResult(
            passed=True,
            score=bonus_score,
            reason=f"前走対戦馬の勝ち上がり: {total_winners}頭勝利 — 前走レベル高",
            detail={"known": total_known, "winners": total_winners},
        )
    return ConditionResult(
        passed=False,
        score=0.0,
        reason=f"前走対戦馬の勝ち上がり少: {total_winners}頭（{min_winners}頭未満）",
        detail={"known": total_known, "winners": total_winners},
    )
