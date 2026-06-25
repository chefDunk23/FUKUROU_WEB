"""
tipster/training_ranker.py
==========================
TR-1 調教AIフィルタリング — 調教タイム・ラップデータによる馬の優先度順位付け

DB source: fukurou_jvdl: training_slope / training_wood
          (TR0_FINDINGS.md にてフィールド意味確定済み)

出力: 推奨順位リストのみ。
      買い目（賭式・点数）の構築ロジックは一切含まない。
      PLAN.md §3.5 G-TR2 / §5-4 G-TR2 参照。

閾値設定: tipster/training_ranker_config.json
          Pythonコードへの直接埋め込みは行わない。PLAN.md §5-4 G-TR3 参照。

優先度条件 ①〜⑦ (PLAN.md §3.5 TR-1):
  ① 坂路ラスト1F ≤ 11.9秒 かつ 全区間加速ラップ             (training_slope)
  ② 坂路ラスト2F目（残り400-200m）≤ 11.9秒                 (training_slope)
  ③ 坂路全体時計（4F累積）≤ 52.9秒 かつ 全区間加速ラップ   (training_slope)
  ④ ウッドラスト1F ≤ 11.5秒 かつ 5F時計 ≤ 67.0秒 かつ 終い2F加速 (training_wood)
  ⑤ 前週（6-8日前）坂路で終い12.9秒以下加速 かつ 当週最終ウッドラスト1F ≤ 11.9秒
  ⑥ 栗東坂路ラスト1F ≤ 12.9秒 かつ 全区間加速ラップ          (training_slope)
  ⑦ 美浦坂路ラスト1F ≤ 12.9秒 かつ 全区間加速ラップ          (training_slope)

加速ラップの定義（PLAN.md 用語定義・ユニットテストで固定）:
  各区間タイムが直前の区間より「厳密に短い」こと。
  同タイム（停滞）は加速ラップとはみなさない（>= ではなく > で判定）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

_CONFIG_PATH = Path(__file__).parent / "training_ranker_config.json"

# 坂路系条件 → tie-break: time_4f / ウッド系条件 → tie-break: time_5f
_SLOPE_CONDITIONS: frozenset[int] = frozenset({1, 2, 3, 6, 7})
_WOOD_CONDITIONS: frozenset[int] = frozenset({4, 5})

_CONDITION_LABELS: dict[int, str] = {
    1: "①", 2: "②", 3: "③", 4: "④", 5: "⑤", 6: "⑥", 7: "⑦"
}


# ─────────────────────────────────────────────────────────────────────────────
# データクラス（入力）
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SlopeRow:
    """training_slope の1行分のデータ（TR-1 に必要なフィールドのみ）。

    フィールド意味は TR0_FINDINGS.md §1・§2 にて確定済み:
      time_4f   : ラスト4F〜ゴールまでの累積タイム（坂路全体時計）
      lap_l4_l3 : ラスト4F〜3F 区間タイム
      lap_l3_l2 : ラスト3F〜2F 区間タイム
      lap_l2_l1 : ラスト2F〜1F 区間タイム（残り400-200m）
      lap_l1    : ラスト1F 区間タイム（残り200m〜ゴール）
      center_cd : '0'=美浦 / '1'=栗東
    """

    blood_no: str
    chokyo_date: str        # "YYYYMMDD"
    chokyo_time: str        # "HHMM"  — 複数回計測の区別に使用
    center_cd: str          # '0'=美浦 / '1'=栗東
    time_4f: Optional[float]
    lap_l4_l3: Optional[float]
    lap_l3_l2: Optional[float]
    lap_l2_l1: Optional[float]
    lap_l1: Optional[float]


@dataclass(frozen=True)
class WoodRow:
    """training_wood の1行分のデータ（TR-1 に必要なフィールドのみ）。

    フィールド意味は TR0_FINDINGS.md §1 にて確定済み:
      time_5f   : ラスト5F〜ゴールまでの累積タイム（ウッド5F時計）
      lap_l2_l1 : ラスト2F〜1F 区間タイム（終い加速判定に使用）
      lap_l1    : ラスト1F 区間タイム
    """

    blood_no: str
    chokyo_date: str        # "YYYYMMDD"
    chokyo_time: str        # "HHMM"
    time_5f: Optional[float]
    lap_l2_l1: Optional[float]
    lap_l1: Optional[float]


# ─────────────────────────────────────────────────────────────────────────────
# データクラス（出力）
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class RankedHorse:
    """TR-1 優先度順位付け結果（1頭分）。

    出力は推奨順位の提示のみ。賭式・点数等の買い目は含まない。
    PLAN.md §3.5 G-TR2 / §5-4 G-TR2 参照。
    """

    blood_no: str
    umaban: Optional[str]           # 馬番（呼び出し元が提供した場合のみ設定）
    priority: int                   # 1〜7（数字が小さいほど優先度高）
    condition_label: str            # "①"〜"⑦"
    tiebreak_time_sec: Optional[float]  # 同一優先度内の tie-break 用時計値
    rank: int                       # 最終順位（完全同タイムは同着）


# ─────────────────────────────────────────────────────────────────────────────
# 設定読み込み
# ─────────────────────────────────────────────────────────────────────────────


def load_config(path: Path = _CONFIG_PATH) -> dict:
    """training_ranker_config.json を読み込んで返す。"""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ─────────────────────────────────────────────────────────────────────────────
# 内部ヘルパー
# ─────────────────────────────────────────────────────────────────────────────


def _is_full_acceleration(row: SlopeRow) -> bool:
    """坂路の全区間加速ラップ判定。

    lap_l4_l3 > lap_l3_l2 > lap_l2_l1 > lap_l1 が全て「厳密に成立」すること。
    いずれかが同タイム（停滞）または逆転の場合は False。
    PLAN.md 用語定義「加速ラップ」: 同タイムは非加速（>= ではなく > で判定する）。
    """
    a, b, c, d = row.lap_l4_l3, row.lap_l3_l2, row.lap_l2_l1, row.lap_l1
    if any(v is None for v in (a, b, c, d)):
        return False
    return a > b > c > d  # type: ignore[operator]


def _is_final_2f_acceleration(row: WoodRow) -> bool:
    """ウッドの終い2F加速ラップ判定: lap_l2_l1 > lap_l1（厳密）。"""
    a, b = row.lap_l2_l1, row.lap_l1
    if a is None or b is None:
        return False
    return a > b  # type: ignore[operator]


def _latest_slope(rows: list[SlopeRow]) -> Optional[SlopeRow]:
    """複数行から最新 chokyo_date + chokyo_time の1行を選ぶ（TR0_FINDINGS.md §4-1 方針）。"""
    valid = [r for r in rows if r.chokyo_date and r.chokyo_time]
    if not valid:
        return None
    return max(valid, key=lambda r: (r.chokyo_date, r.chokyo_time))


def _latest_wood(rows: list[WoodRow]) -> Optional[WoodRow]:
    """複数行から最新 chokyo_date + chokyo_time の1行を選ぶ。"""
    valid = [r for r in rows if r.chokyo_date and r.chokyo_time]
    if not valid:
        return None
    return max(valid, key=lambda r: (r.chokyo_date, r.chokyo_time))


def _days_before(race_date: str, chokyo_date: str) -> int:
    """race_date から見て chokyo_date が何日前か（正値＝前、負値＝未来）。"""
    rd = datetime.strptime(race_date, "%Y%m%d")
    cd = datetime.strptime(chokyo_date, "%Y%m%d")
    return (rd - cd).days


# ─────────────────────────────────────────────────────────────────────────────
# 条件チェック関数（1条件につき1関数）
# ─────────────────────────────────────────────────────────────────────────────


def _check_condition_1(slope: Optional[SlopeRow], cfg: dict) -> bool:
    """① 坂路ラスト1F ≤ threshold かつ 全区間加速ラップ。"""
    if slope is None or slope.lap_l1 is None:
        return False
    return slope.lap_l1 <= cfg["slope_last_1f_max_sec"] and _is_full_acceleration(slope)


def _check_condition_2(slope: Optional[SlopeRow], cfg: dict) -> bool:
    """② 坂路ラスト2F目（残り400-200m）≤ threshold。ラスト1Fの減速は許容。"""
    if slope is None or slope.lap_l2_l1 is None:
        return False
    return slope.lap_l2_l1 <= cfg["slope_last_2f_max_sec"]


def _check_condition_3(slope: Optional[SlopeRow], cfg: dict) -> bool:
    """③ 坂路全体時計（4F累積）≤ threshold かつ 全区間加速ラップ。"""
    if slope is None or slope.time_4f is None:
        return False
    return slope.time_4f <= cfg["slope_total_time_max_sec"] and _is_full_acceleration(slope)


def _check_condition_4(wood: Optional[WoodRow], cfg: dict) -> bool:
    """④ ウッドラスト1F ≤ threshold かつ 5F時計 ≤ threshold かつ 終い2F加速ラップ。"""
    if wood is None or wood.lap_l1 is None or wood.time_5f is None:
        return False
    return (
        wood.lap_l1 <= cfg["wood_last_1f_max_sec"]
        and wood.time_5f <= cfg["wood_5f_time_max_sec"]
        and _is_final_2f_acceleration(wood)
    )


def _check_condition_5(
    slope_rows: list[SlopeRow],
    wood_rows: list[WoodRow],
    race_date: str,
    cfg: dict,
) -> bool:
    """⑤ 前週（6-8日前）坂路で終い12.9秒以下の加速ラップあり
       かつ 当週最終追い切りがウッドでラスト1F ≤ threshold。

    前週データなし、または当週ウッドデータなし → False（エラーにしない）。
    PLAN.md 用語定義「前週」: レース日の6〜8日前。
    """
    try:
        min_d = cfg["prev_week_min_days_before"]
        max_d = cfg["prev_week_max_days_before"]

        # 前週（6-8日前）の坂路行を抽出
        prev_slopes = [
            r for r in slope_rows
            if r.chokyo_date and min_d <= _days_before(race_date, r.chokyo_date) <= max_d
        ]
        if not prev_slopes:
            return False  # 前週データなし → False

        # 前週坂路のいずれかが「終い12.9秒以下 かつ 全区間加速ラップ」を満たすか
        prev_ok = any(
            r.lap_l1 is not None
            and r.lap_l1 <= cfg["prev_week_slope_last_1f_max_sec"]
            and _is_full_acceleration(r)
            for r in prev_slopes
        )
        if not prev_ok:
            return False

        # 当週最終追い切りウッド（最新行）のラスト1Fを確認
        wood = _latest_wood(wood_rows)
        if wood is None or wood.lap_l1 is None:
            return False
        return wood.lap_l1 <= cfg["current_week_wood_last_1f_max_sec"]
    except Exception:
        return False


def _check_condition_6(slope: Optional[SlopeRow], cfg: dict) -> bool:
    """⑥ 栗東（center_cd='1'）坂路ラスト1F ≤ threshold かつ 全区間加速ラップ。"""
    if slope is None or slope.lap_l1 is None:
        return False
    return (
        slope.center_cd == cfg["center_cd"]
        and slope.lap_l1 <= cfg["slope_last_1f_max_sec"]
        and _is_full_acceleration(slope)
    )


def _check_condition_7(slope: Optional[SlopeRow], cfg: dict) -> bool:
    """⑦ 美浦（center_cd='0'）坂路ラスト1F ≤ threshold かつ 全区間加速ラップ。"""
    if slope is None or slope.lap_l1 is None:
        return False
    return (
        slope.center_cd == cfg["center_cd"]
        and slope.lap_l1 <= cfg["slope_last_1f_max_sec"]
        and _is_full_acceleration(slope)
    )


_CONDITION_CHECKERS = {
    1: _check_condition_1,
    2: _check_condition_2,
    3: _check_condition_3,
    6: _check_condition_6,
    7: _check_condition_7,
}


# ─────────────────────────────────────────────────────────────────────────────
# メイン関数
# ─────────────────────────────────────────────────────────────────────────────


def rank_horses_by_training(
    blood_nos: list[str],
    slope_rows_by_horse: dict[str, list[SlopeRow]],
    wood_rows_by_horse: dict[str, list[WoodRow]],
    race_date: str,
    config: Optional[dict] = None,
    umaban_by_blood_no: Optional[dict[str, str]] = None,
) -> list[RankedHorse]:
    """
    対象レースの出走馬を調教データで優先度順に順位付けして返す。

    Args:
        blood_nos: 対象レースの血統登録番号リスト（race_entries_v2.blood_no）
        slope_rows_by_horse: blood_no → training_slope 行リスト
                              （呼び出し元が適切な期間のデータを提供すること）
        wood_rows_by_horse:  blood_no → training_wood 行リスト（同上）
        race_date:   レース日 "YYYYMMDD"
        config:      設定 dict（None の場合は training_ranker_config.json を読む）
        umaban_by_blood_no: blood_no → 馬番 のマッピング（省略可）

    Returns:
        優先度・tie-break 順に並んだ RankedHorse リスト。
        いずれの条件にも該当しない馬は除外される。
        出力に買い目（賭式・点数）は含まれない。PLAN.md §3.5 G-TR2 参照。

    Tie-break ルール（PLAN.md §3.5 TR-1）:
        - 坂路系条件（①②③⑥⑦）: 坂路全体時計（time_4f）の速い順
        - ウッド系条件（④⑤）: ウッド5F時計（time_5f）の速い順
        - 完全同タイムは同着（同一 rank）
    """
    if config is None:
        config = load_config()
    conds_cfg = config["conditions"]

    # (priority, tiebreak_sec, blood_no) のリストを構築
    matched: list[tuple[int, Optional[float], str]] = []

    for blood_no in blood_nos:
        slope_rows = slope_rows_by_horse.get(blood_no, [])
        wood_rows = wood_rows_by_horse.get(blood_no, [])
        best_slope = _latest_slope(slope_rows)
        best_wood = _latest_wood(wood_rows)

        matched_priority: Optional[int] = None
        tiebreak: Optional[float] = None

        for priority in range(1, 8):
            cfg_p = conds_cfg[str(priority)]

            if priority in (1, 2, 3, 6, 7):
                ok = _CONDITION_CHECKERS[priority](best_slope, cfg_p)
            elif priority == 4:
                ok = _check_condition_4(best_wood, cfg_p)
            elif priority == 5:
                ok = _check_condition_5(slope_rows, wood_rows, race_date, cfg_p)
            else:
                ok = False

            if ok:
                matched_priority = priority
                if priority in _SLOPE_CONDITIONS:
                    tiebreak = best_slope.time_4f if best_slope else None
                else:
                    tiebreak = best_wood.time_5f if best_wood else None
                break

        if matched_priority is not None:
            matched.append((matched_priority, tiebreak, blood_no))

    # ソート: 優先度昇順 → tiebreak 昇順（None は末尾）
    def _sort_key(item: tuple) -> tuple:
        priority, tb, _ = item
        return (priority, tb if tb is not None else float("inf"))

    matched.sort(key=_sort_key)

    # ランク計算（同優先度・同タイムは同着）
    result: list[RankedHorse] = []
    current_rank = 1
    for i, (priority, tb, blood_no) in enumerate(matched):
        if i > 0:
            prev_priority, prev_tb, _ = matched[i - 1]
            if priority != prev_priority or tb != prev_tb:
                current_rank = i + 1
        result.append(
            RankedHorse(
                blood_no=blood_no,
                umaban=(umaban_by_blood_no or {}).get(blood_no),
                priority=priority,
                condition_label=_CONDITION_LABELS[priority],
                tiebreak_time_sec=tb,
                rank=current_rank,
            )
        )

    return result
