"""
src/video_generator/corner_router.py
======================================
YouTube横動画コーナー振り分けロジック。

デュアルエンジンAI推論結果（サブモデルスコア）から各レースを4コーナーに振り分ける:

  鉄板枠      — ability_v2 Z ≥ +2.5 かつ2位差 1.0σ以上。ダート戦のみ。（spec §6.1）
  スパイス枠  — ability_v2 は平凡（rank≥4）だが pace_v2/pedigree_v1 ≥ +2.0。（spec §6.2）
  危険な人気馬 — ability_v2 は高いが course_v2 or pace_v2 がマイナス沈降。（spec §6.3）
  サクサク枠  — 残りレース（簡潔推奨フォーマット）

入力データ要件:
  DataFrame には以下のカラムが必要:
    必須: race_id, track_code, distance, grade_code, keibajo_code,
          score_ability_v2, score_pace_v2, score_pedigree_v1
    推奨: score_course_v2, score_team_v2, score_training_v2,
          horse_id, umaban, tan_odds, ninki, race_num, race_date
    任意: pace_harmony_pre, horse_name

NOTE: このモジュールは一切の事後データ（kakutei_chakujun 等の結果）を参照しない。
      dry-run 検証時はラベル照合のため結果列が渡されても構わないが、
      振り分け判断には使用しない。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd

# ── 振り分けパラメータ ────────────────────────────────────────────────────────

TEPPAN_ABILITY_Z_MIN: float   = 2.5   # 鉄板: ability_v2 Z-score 閾値（spec §6.1: +2.5以上）
TEPPAN_ABILITY_GAP_MIN: float = 1.0   # 鉄板: 2位との ability_v2 Z の差

SPICE_Z_MIN: float            = 2.0   # スパイス: pace_v2 / pedigree_v1 Z-score 閾値
SPICE_HARMONY_BONUS: float    = 0.65  # スパイス: pace_harmony_pre ボーナス閾値

DANGER_ABILITY_Z_MIN: float   = 1.5   # 危険: ability_v2 Z の最低値（人気馬ライン）
DANGER_WEAK_Z_MAX:    float   = -0.8  # 危険: course_v2 or pace_v2 がこれ以下で「崩れリスク」

# セッション内上限
SESSION_MAX_TEPPAN: int = 2
SESSION_MAX_SPICE:  int = 3
SESSION_MAX_DANGER: int = 2

# ── マスタ辞書 ────────────────────────────────────────────────────────────────

GRADE_LABELS: dict[str, str] = {
    "G": "G1",
    "F": "G2",
    "D": "G3",
    "L": "Listed",
    "B": "OP特別",
    "A": "オープン",
    "C": "3勝クラス",
    "H": "2勝クラス",
    "E": "1勝クラス",
}

KEIBAJO_LABELS: dict[str, str] = {
    "01": "札幌", "1": "札幌",
    "02": "函館", "2": "函館",
    "03": "福島", "3": "福島",
    "04": "新潟", "4": "新潟",
    "05": "東京", "5": "東京",
    "06": "中山", "6": "中山",
    "07": "中京", "7": "中京",
    "08": "京都", "8": "京都",
    "09": "阪神", "9": "阪神",
    "10": "小倉",
}

# JV-Data track_code: 1x = 芝, 2x = ダート/障害
def _is_dirt(track_code) -> bool:
    try:
        return int(str(track_code)) >= 20
    except (TypeError, ValueError):
        return False

def _surface_label(track_code) -> str:
    return "ダート" if _is_dirt(track_code) else "芝"

def _keibajo(code) -> str:
    s = str(code).strip() if code is not None and not (isinstance(code, float) and np.isnan(code)) else ""
    return KEIBAJO_LABELS.get(s, f"会場{s}")

def _grade(code) -> str:
    if code is None:
        return "新馬/未勝利"
    s = str(code).strip()
    if s == "" or s.lower() == "nan":
        return "新馬/未勝利"
    try:
        if np.isnan(float(s)):
            return "新馬/未勝利"
    except ValueError:
        pass
    return GRADE_LABELS.get(s, s)

def _zscores_within_race(series: pd.Series) -> pd.Series:
    """レース内 Z スコア。std < 1e-9 のときは全馬 0.0。"""
    s = pd.to_numeric(series, errors="coerce").fillna(0.0)
    mean, std = s.mean(), s.std()
    if std < 1e-9:
        return pd.Series(0.0, index=series.index)
    return (s - mean) / std


# ── データ型 ──────────────────────────────────────────────────────────────────

class Corner(str, Enum):
    TEPPAN   = "teppan"
    SPICE    = "spice"
    DANGER   = "danger"    # 危険な人気馬（ability高いが course/pace がマイナス）
    SAKUSAKU = "sakusaku"


@dataclass
class CornerPick:
    """1レース1馬の振り分け結果。"""
    race_id:     str
    race_label:  str          # "東京11R ダート1600m (オープン)"
    corner:      Corner
    horse_id:    str
    horse_name:  str          # horse_id フォールバック済み
    umaban:      int
    ai_rank:     int          # レース内 Z-ensemble 順位
    is_dirt:     bool
    grade:       str

    # 主要 Z スコア (レース内)
    ability_z:   float = 0.0
    pace_z:      float = 0.0
    pedigree_z:  float = 0.0
    course_z:    float = 0.0
    team_z:      float = 0.0

    # 判定補助値
    ability_gap:      float = 0.0  # 2位との ability_v2 Z 差
    ability_v2_rank:  int   = 0    # レース内 ability_v2 単独ランク（スパイス用）
    pace_harmony:     float = 0.0  # pace_harmony_pre (なければ 0)
    tan_odds:       Optional[float] = None
    ninki:          Optional[int]   = None

    # 選出理由
    reason:       str = ""
    reason_parts: list[str] = field(default_factory=list)

    # ── dry-run 専用: 事後検証用 ────────────────────────────────────────────
    actual_rank:  Optional[int]   = None   # 実際の着順（dry-run のみ）
    hit:          Optional[bool]  = None   # 1着的中フラグ（dry-run のみ）


@dataclass
class SessionResult:
    """1セッション（日付×開催場）の振り分け結果。"""
    session_label: str                     # "2026-05-17 東京・京都"
    teppan:        list[CornerPick]
    spice:         list[CornerPick]
    danger:        list[CornerPick]        # 危険な人気馬（spec §6.3）
    sakusaku_labels: list[str]             # サクサク枠レース名リスト
    total_races:   int


# ── 1レース単位のルーティング ─────────────────────────────────────────────────

SUB_COLS = [
    "score_ability_v2",
    "score_course_v2",
    "score_team_v2",
    "score_training_v2",
    "score_pace_v2",
    "score_pedigree_v1",
]


def _race_label(row: pd.Series) -> str:
    keibajo  = _keibajo(row.get("keibajo_code"))
    race_num = int(row.get("race_num", 0) or 0)
    surface  = _surface_label(row.get("track_code"))
    dist     = int(row.get("distance", 0) or 0)
    grade    = _grade(row.get("grade_code"))
    return f"{keibajo}{race_num}R {surface}{dist}m ({grade})"


def route_race(race_df: pd.DataFrame) -> list[CornerPick]:
    """
    1レース分の推論結果からコーナー候補を生成する。

    Returns:
        list[CornerPick] — 鉄板1件 + スパイス1件（条件を満たした場合のみ）
    """
    race_df = race_df.copy().reset_index(drop=True)
    race_id = str(race_df["race_id"].iloc[0])
    sample  = race_df.iloc[0]

    available_sub = [c for c in SUB_COLS if c in race_df.columns]
    if not available_sub:
        return []

    # ── レース内 Z スコア計算 ─────────────────────────────────────────────────
    z: dict[str, pd.Series] = {}
    for col in available_sub:
        z[col] = _zscores_within_race(race_df[col])

    # アンサンブル Z (available サブモデルの平均)
    z_ens = pd.concat(list(z.values()), axis=1).mean(axis=1)
    ai_ranks = z_ens.rank(ascending=False, method="first").astype(int)

    # ── レース属性 ──────────────────────────────────────────────────────────
    label       = _race_label(sample)
    is_dirt     = _is_dirt(sample.get("track_code"))
    grade_code  = str(sample.get("grade_code", "") or "")
    grade_str   = _grade(grade_code)
    n           = len(race_df)

    # ── ability_v2 Z の 1位/2位差 ──────────────────────────────────────────
    ability_z_col = "score_ability_v2"
    if ability_z_col in z:
        sorted_az = z[ability_z_col].sort_values(ascending=False).values
        ability_gap = float(sorted_az[0] - sorted_az[1]) if n >= 2 else 0.0
    else:
        ability_gap = 0.0

    # ── ヘルパー ────────────────────────────────────────────────────────────
    def _get_z(col: str, idx: int) -> float:
        return float(z[col].iloc[idx]) if col in z else 0.0

    def _horse_name(idx: int) -> str:
        for col in ("horse_name", "uma_name"):
            if col in race_df.columns:
                v = race_df[col].iloc[idx]
                if v and str(v) not in ("nan", "None", ""):
                    return str(v)
        hid = str(race_df["horse_id"].iloc[idx]) if "horse_id" in race_df.columns else str(idx)
        return f"馬ID:{hid}"

    def _horse_id(idx: int) -> str:
        return str(race_df["horse_id"].iloc[idx]) if "horse_id" in race_df.columns else str(idx)

    def _umaban(idx: int) -> int:
        v = race_df["umaban"].iloc[idx] if "umaban" in race_df.columns else 0
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

    def _odds(idx: int) -> Optional[float]:
        for col in ("tan_odds", "tan_odds_f"):
            if col in race_df.columns:
                v = pd.to_numeric(race_df[col].iloc[idx], errors="coerce")
                if not pd.isna(v):
                    return float(v)
        return None

    def _ninki(idx: int) -> Optional[int]:
        if "ninki" not in race_df.columns:
            return None
        v = pd.to_numeric(race_df["ninki"].iloc[idx], errors="coerce")
        return None if pd.isna(v) else int(v)

    def _pace_harmony(idx: int) -> float:
        if "pace_harmony_pre" not in race_df.columns:
            return 0.0
        v = pd.to_numeric(race_df["pace_harmony_pre"].iloc[idx], errors="coerce")
        return 0.0 if pd.isna(v) else float(v)

    picks: list[CornerPick] = []

    # ── ability_v2 単独ランク（スパイス足切りに使用） ───────────────────────
    if ability_z_col in z:
        ability_v2_ranks = z[ability_z_col].rank(ascending=False, method="first").astype(int)
    else:
        ability_v2_ranks = pd.Series(1, index=race_df.index)

    # ══════════════════════════════════════════════════════════════════════════
    # 【鉄板枠】
    #   条件: ダート戦 (is_dirt) ← 必須
    #         AI1番手 の ability_v2 Z ≥ TEPPAN_ABILITY_Z_MIN
    #         かつ 2位との ability_v2 Z 差 ≥ TEPPAN_ABILITY_GAP_MIN
    # ══════════════════════════════════════════════════════════════════════════
    top_idx = int(ai_ranks[ai_ranks == 1].index[0])
    top_ability_z = _get_z(ability_z_col, top_idx)

    if is_dirt and top_ability_z >= TEPPAN_ABILITY_Z_MIN and ability_gap >= TEPPAN_ABILITY_GAP_MIN:
        rp = [f"ability_v2 Z={top_ability_z:+.2f}"]
        rp.append(f"2位差={ability_gap:.2f}σ")
        if is_dirt:
            rp.append("ダート戦【優先】")
        ph = _pace_harmony(top_idx)
        if ph >= SPICE_HARMONY_BONUS:
            rp.append(f"展開合致={ph:.2f}")

        picks.append(CornerPick(
            race_id=race_id, race_label=label, corner=Corner.TEPPAN,
            horse_id=_horse_id(top_idx), horse_name=_horse_name(top_idx),
            umaban=_umaban(top_idx), ai_rank=1,
            is_dirt=is_dirt, grade=grade_str,
            ability_z=top_ability_z,
            pace_z=_get_z("score_pace_v2", top_idx),
            pedigree_z=_get_z("score_pedigree_v1", top_idx),
            course_z=_get_z("score_course_v2", top_idx),
            team_z=_get_z("score_team_v2", top_idx),
            ability_gap=ability_gap,
            pace_harmony=_pace_harmony(top_idx),
            tan_odds=_odds(top_idx), ninki=_ninki(top_idx),
            reason_parts=rp, reason=", ".join(rp),
        ))

    # ══════════════════════════════════════════════════════════════════════════
    # 【スパイス枠】
    #   対象レース: 芝戦 OR 1勝/2勝クラス (grade E, H)
    #   候補馬: ability_v2 rank ≥ 4（基礎能力上位3頭を除外し、真の穴馬のみ対象）
    #           かつ pace_v2 Z ≥ SPICE_Z_MIN OR pedigree_v1 Z ≥ SPICE_Z_MIN
    #   複数いる場合は (pace_z + pedigree_z) 合計が最大の馬を採用
    # ══════════════════════════════════════════════════════════════════════════
    spice_race_eligible = (not is_dirt) or (grade_code in ("E", "H"))

    if spice_race_eligible:
        best_spice: tuple[float, int] | None = None  # (combined_z, idx)
        for i in range(n):
            # ability_v2 rank 4位以下のみスパイス候補（1-3位は「人気馬混入」防止）
            if int(ability_v2_ranks.iloc[i]) < 4:
                continue
            pv2_z  = _get_z("score_pace_v2",     i)
            pg_z   = _get_z("score_pedigree_v1",  i)
            if pv2_z >= SPICE_Z_MIN or pg_z >= SPICE_Z_MIN:
                combined = pv2_z + pg_z
                if best_spice is None or combined > best_spice[0]:
                    best_spice = (combined, i)

        if best_spice is not None:
            idx    = best_spice[1]
            pv2_z  = _get_z("score_pace_v2",    idx)
            pg_z   = _get_z("score_pedigree_v1", idx)
            ph     = _pace_harmony(idx)
            rp: list[str] = []
            if pv2_z >= SPICE_Z_MIN:
                rp.append(f"pace_v2 Z={pv2_z:+.2f}")
            if pg_z >= SPICE_Z_MIN:
                rp.append(f"pedigree_v1 Z={pg_z:+.2f}")
            if ph >= SPICE_HARMONY_BONUS:
                rp.append(f"展開合致={ph:.2f}")
            rp.append(f"AI{int(ai_ranks.iloc[idx])}番手（穴狙い）")
            if grade_code in ("E", "H"):
                rp.append(f"{grade_str}（波乱クラス）")

            picks.append(CornerPick(
                race_id=race_id, race_label=label, corner=Corner.SPICE,
                horse_id=_horse_id(idx), horse_name=_horse_name(idx),
                umaban=_umaban(idx), ai_rank=int(ai_ranks.iloc[idx]),
                is_dirt=is_dirt, grade=grade_str,
                ability_z=_get_z("score_ability_v2", idx),
                pace_z=pv2_z,
                pedigree_z=pg_z,
                course_z=_get_z("score_course_v2", idx),
                team_z=_get_z("score_team_v2", idx),
                ability_gap=ability_gap,
                ability_v2_rank=int(ability_v2_ranks.iloc[idx]),
                pace_harmony=ph,
                tan_odds=_odds(idx), ninki=_ninki(idx),
                reason_parts=rp, reason=", ".join(rp),
            ))

    # ══════════════════════════════════════════════════════════════════════════
    # 【危険な人気馬】  spec §6.3
    #   条件: ability_v2 rank 1 かつ ability_z ≥ DANGER_ABILITY_Z_MIN
    #         かつ course_v2 Z ≤ DANGER_WEAK_Z_MAX OR pace_v2 Z ≤ DANGER_WEAK_Z_MAX
    #   → 実績は高く人気必至だが、コース or 展開で崩れるリスクを内包する馬
    # ══════════════════════════════════════════════════════════════════════════
    top_course_z_d = _get_z("score_course_v2", top_idx)
    top_pace_z_d   = _get_z("score_pace_v2",   top_idx)

    if (top_ability_z >= DANGER_ABILITY_Z_MIN
            and (top_course_z_d <= DANGER_WEAK_Z_MAX or top_pace_z_d <= DANGER_WEAK_Z_MAX)):

        weak_parts: list[str] = []
        if top_course_z_d <= DANGER_WEAK_Z_MAX:
            weak_parts.append(f"course_v2 Z={top_course_z_d:+.2f}（コース適性危険）")
        if top_pace_z_d <= DANGER_WEAK_Z_MAX:
            weak_parts.append(f"pace_v2 Z={top_pace_z_d:+.2f}（展開不利の懸念）")

        rp_d = [f"ability_v2 Z={top_ability_z:+.2f}（高実績・人気必至）"] + weak_parts

        picks.append(CornerPick(
            race_id=race_id, race_label=label, corner=Corner.DANGER,
            horse_id=_horse_id(top_idx), horse_name=_horse_name(top_idx),
            umaban=_umaban(top_idx), ai_rank=1,
            is_dirt=is_dirt, grade=grade_str,
            ability_z=top_ability_z,
            pace_z=top_pace_z_d,
            pedigree_z=_get_z("score_pedigree_v1", top_idx),
            course_z=top_course_z_d,
            team_z=_get_z("score_team_v2", top_idx),
            ability_gap=ability_gap,
            pace_harmony=_pace_harmony(top_idx),
            tan_odds=_odds(top_idx), ninki=_ninki(top_idx),
            reason_parts=rp_d, reason=", ".join(rp_d),
        ))

    return picks


# ── セッション単位のルーティング ───────────────────────────────────────────────

def route_session(
    df: pd.DataFrame,
    session_label: str = "",
    max_teppan: int = SESSION_MAX_TEPPAN,
    max_spice:  int = SESSION_MAX_SPICE,
    max_danger: int = SESSION_MAX_DANGER,
) -> SessionResult:
    """
    1セッション（例: 土曜日の全レース）を振り分けて SessionResult を返す。

    Parameters
    ----------
    df : DataFrame
        セッション内の全馬データ。race_id ごとにグループ化して処理。
    session_label : str
        レポートヘッダ用ラベル（例: "2026-05-17 東京・京都"）
    max_teppan, max_spice, max_danger : int
        セッション内の最大選出数。スコアが高い順に絞り込む。
    """
    all_teppan: list[CornerPick] = []
    all_spice:  list[CornerPick] = []
    all_danger: list[CornerPick] = []
    race_labels: dict[str, str]  = {}  # race_id → race_label

    for race_id, race_df in df.groupby("race_id", sort=True):
        picks = route_race(race_df)
        sample = race_df.iloc[0]
        race_labels[str(race_id)] = _race_label(sample)
        for p in picks:
            if p.corner == Corner.TEPPAN:
                all_teppan.append(p)
            elif p.corner == Corner.SPICE:
                all_spice.append(p)
            elif p.corner == Corner.DANGER:
                all_danger.append(p)

    # 鉄板: ダート優先 → ability_z 降順 → ability_gap 降順
    all_teppan.sort(key=lambda p: (-int(p.is_dirt), -p.ability_z, -p.ability_gap))
    # スパイス: (pace_z + pedigree_z) 降順
    all_spice.sort(key=lambda p: -(p.pace_z + p.pedigree_z))
    # 危険: 弱点スコアが最も低い（課題が深い）順 → min(course_z, pace_z) 昇順
    all_danger.sort(key=lambda p: min(p.course_z, p.pace_z))

    teppan = all_teppan[:max_teppan]
    spice  = all_spice[:max_spice]
    danger = all_danger[:max_danger]

    selected_ids = {p.race_id for p in teppan + spice}
    sakusaku = [lbl for rid, lbl in race_labels.items() if rid not in selected_ids]
    sakusaku.sort(key=lambda s: s)

    return SessionResult(
        session_label=session_label,
        teppan=teppan,
        spice=spice,
        danger=danger,
        sakusaku_labels=sakusaku,
        total_races=len(race_labels),
    )


# ── テキストレポート生成 ───────────────────────────────────────────────────────

def _odds_label(tan_odds: Optional[float], ninki: Optional[int]) -> str:
    parts = []
    if ninki is not None:
        parts.append(f"{ninki}番人気")
    if tan_odds is not None:
        parts.append(f"{tan_odds:.1f}倍")
    return f"({', '.join(parts)})" if parts else ""


def _hit_label(pick: CornerPick) -> str:
    if pick.actual_rank is None:
        return ""
    if pick.hit:
        return f"  ✓ 1着【的中】"
    return f"  着順:{pick.actual_rank}"


def build_report(result: SessionResult, include_sakusaku_detail: bool = True) -> str:
    """SessionResult を人間が読めるテキストレポートに変換する。"""
    lines: list[str] = []
    sep = "━" * 60

    lines.append(sep)
    lines.append(f"  検証レポート: {result.session_label}")
    lines.append(f"  対象レース数: {result.total_races}R")
    lines.append(sep)

    # ── 鉄板枠 ─────────────────────────────────────────────────────────────
    lines.append("")
    lines.append("■ 鉄板枠【選出】")
    if result.teppan:
        for p in result.teppan:
            od = _odds_label(p.tan_odds, p.ninki)
            hit = _hit_label(p)
            lines.append(f"  {p.race_label}")
            lines.append(f"    馬番{p.umaban:>2}番  {p.horse_name}  {od}{hit}")
            lines.append(f"    根拠: {p.reason}")
            lines.append(f"    Z分布: ability={p.ability_z:+.2f}  pace={p.pace_z:+.2f}"
                          f"  pedigree={p.pedigree_z:+.2f}  course={p.course_z:+.2f}")
            lines.append("")
    else:
        lines.append("  （該当レースなし）")
        lines.append("")

    # ── 危険な人気馬 ───────────────────────────────────────────────────────
    lines.append("■ 危険な人気馬【検出】")
    if result.danger:
        for p in result.danger:
            od = _odds_label(p.tan_odds, p.ninki)
            hit = _hit_label(p)
            lines.append(f"  {p.race_label}")
            lines.append(f"    馬番{p.umaban:>2}番  {p.horse_name}  {od}{hit}")
            lines.append(f"    根拠: {p.reason}")
            lines.append(f"    Z分布: ability={p.ability_z:+.2f}  pace={p.pace_z:+.2f}"
                          f"  pedigree={p.pedigree_z:+.2f}  course={p.course_z:+.2f}")
            lines.append("")
    else:
        lines.append("  （該当レースなし）")
        lines.append("")

    # ── スパイス枠 ─────────────────────────────────────────────────────────
    lines.append("■ スパイス枠【選出】")
    if result.spice:
        for p in result.spice:
            od = _odds_label(p.tan_odds, p.ninki)
            hit = _hit_label(p)
            lines.append(f"  {p.race_label}")
            lines.append(f"    馬番{p.umaban:>2}番  {p.horse_name}  {od}{hit}")
            lines.append(f"    根拠: {p.reason}")
            lines.append(f"    Z分布: ability={p.ability_z:+.2f}  pace={p.pace_z:+.2f}"
                          f"  pedigree={p.pedigree_z:+.2f}  course={p.course_z:+.2f}")
            lines.append("")
    else:
        lines.append("  （該当レースなし）")
        lines.append("")

    # ── サクサク枠 ─────────────────────────────────────────────────────────
    lines.append(f"■ サクサク枠  残り{len(result.sakusaku_labels)}レース")
    if include_sakusaku_detail:
        for lbl in result.sakusaku_labels:
            lines.append(f"    {lbl}")
    lines.append("")
    lines.append(sep)

    return "\n".join(lines)
