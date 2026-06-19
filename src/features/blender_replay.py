"""
src/features/blender_replay.py
================================
Blender 3Dリプレイ動画用キーフレームデータ生成コア。

各馬の「スタート → 各コーナー → ゴール」のキーフレームを計算し、
Blender が読み込みやすい (frame, horse_name, progress, x_offset) 形式で返す。

キーフレームのみ出力（スタート・各コーナー通過・ゴール）。
Blender 側は中間フレームをスプライン補間で自動生成する。

設計方針:
  progress : コース進捗 0.0=スタート, 1.0=ゴール
  x_offset : 内側からの横距離 [m]。最内=0.0 付近、外回りほど大きい。
  frame    : 30fps 基準のフレーム番号。winner_race_time × FPS が最終フレーム。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import NamedTuple


# ── コーナー進捗比率テーブル ──────────────────────────────────────────────────
# 距離カテゴリ → {corner_number: progress_fraction}
# JRA 標準的なコース形状から概算。正確な幾何より「動きの自然さ」優先。
# c1=第1コーナー, c2=第2コーナー, c3=第3コーナー, c4=第4コーナー（最終）

_CORNER_FRACS_BY_DIST: list[tuple[int, dict[int, float]]] = [
    # (distance_upper_bound, {corner: frac})
    (1050, {}),                                              # 直線コース (1000m)
    (1300, {3: 0.42, 4: 0.68}),                             # スプリント 1200m
    (1500, {3: 0.36, 4: 0.65}),                             # スプリント 1400m
    (1700, {1: 0.10, 2: 0.25, 3: 0.60, 4: 0.76}),          # マイル 1600m
    (1900, {1: 0.12, 2: 0.28, 3: 0.62, 4: 0.78}),          # 1800m
    (2100, {1: 0.12, 2: 0.25, 3: 0.58, 4: 0.74}),          # 2000m
    (2300, {1: 0.10, 2: 0.23, 3: 0.56, 4: 0.73}),          # 2200m
    (2500, {1: 0.10, 2: 0.22, 3: 0.54, 4: 0.71}),          # 2400m
    (2800, {1: 0.08, 2: 0.20, 3: 0.52, 4: 0.70}),          # 2600m
    (9999, {1: 0.08, 2: 0.18, 3: 0.50, 4: 0.68}),          # 3000m+
]

# コーナー間での馬間隔の物理パラメータ
METERS_PER_RANK: float = 5.0     # コーナー通過順1ランク差 ≈ 馬5m分（約2馬身）
LANE_WIDTH_M:    float = 1.2     # 横1レーン幅 [m]
RAIL_OFFSET_M:   float = 0.5     # 最内枠から内柵まで [m]
MAX_LANES:        int  = 10      # 横に並べる最大レーン数（x_offset の上限制御）

# ゲート（枠番）スタート位置パラメータ
GATE_WIDTH_M: float = 1.5        # ゲート間隔 [m]
GATE_OFFSET_M: float = 0.3       # 最内枠のオフセット [m]


class KeyFrame(NamedTuple):
    """1頭1キーフレームのデータ。"""
    frame:      int
    horse_name: str
    progress:   float   # 0.0 – 1.0
    x_offset:   float   # [m], 内側=小
    label:      str     # "start" / "c1" / "c2" / "c3" / "c4" / "finish"


@dataclass
class HorseEntry:
    """1頭のレース結果データ（DB から取得した生データに対応）。"""
    horse_id:       str
    horse_name:     str
    umaban:         int           # 馬番
    wakuban:        int | None    # 枠番（None なら umaban で代替）
    final_rank:     int           # 確定着順
    race_time:      float | None  # 走破タイム [秒]
    go_3f_time:     float | None  # 上がり3F [秒]
    time_diff_secs: float         # 勝ち馬からの差 [秒] (0=勝ち馬)
    corners: dict[int, int] = field(default_factory=dict)
    # corners: {1: rank, 2: rank, 3: rank, 4: rank}, 0/None = 未記録


def _corner_fracs(distance: int) -> dict[int, float]:
    """距離 [m] → {コーナー番号: 進捗比率} を返す。"""
    for upper, fracs in _CORNER_FRACS_BY_DIST:
        if distance <= upper:
            return fracs
    return _CORNER_FRACS_BY_DIST[-1][1]


def _x_offset_for_corner(rank: int, field_size: int) -> float:
    """コーナー通過順位 → 横位置 [m]。先頭が内側、後方が外側。"""
    if field_size <= 1:
        return RAIL_OFFSET_M
    norm = (rank - 1) / max(field_size - 1, 1)  # 0=先頭, 1=最後方
    # 内側 RAIL_OFFSET_M から最大 MAX_LANES × LANE_WIDTH_M まで広がる
    lanes = min(norm * MAX_LANES, MAX_LANES)
    return round(RAIL_OFFSET_M + lanes * LANE_WIDTH_M, 2)


def _x_offset_for_finish(final_rank: int, field_size: int) -> float:
    """着順 → 横位置 [m]。直線は縦長に圧縮されるため狭め。"""
    if field_size <= 1:
        return RAIL_OFFSET_M
    norm = (final_rank - 1) / max(field_size - 1, 1)
    lanes = min(norm * MAX_LANES * 0.5, MAX_LANES * 0.5)  # 直線は半分に圧縮
    return round(RAIL_OFFSET_M + lanes * LANE_WIDTH_M, 2)


def _x_offset_for_start(wakuban: int | None, umaban: int) -> float:
    """枠番/馬番 → スタート時の横位置 [m]。"""
    gate = wakuban if wakuban and wakuban > 0 else umaban
    return round(GATE_OFFSET_M + (gate - 1) * GATE_WIDTH_M, 2)


def build_keyframes(
    entries: list[HorseEntry],
    distance: int,
    fps: int = 30,
) -> list[KeyFrame]:
    """
    全出走馬のキーフレームリストを生成する。

    Parameters
    ----------
    entries  : 全出走馬のエントリデータ
    distance : レース距離 [m]
    fps      : フレームレート（デフォルト 30fps）

    Returns
    -------
    list[KeyFrame] — CSV に直接書き出せるキーフレームのフラットリスト。
    frame 昇順 → horse_name 順にソートされる。
    """
    if not entries:
        return []

    field_size = len(entries)

    # ── 勝ち馬のタイムを基準フレーム計算に使用 ──────────────────────────────
    winner = min(entries, key=lambda e: e.final_rank)
    winner_time = winner.race_time
    if winner_time is None or winner_time <= 0:
        # race_time が無い場合は time_diff=0 の馬を winner として処理できないため fallback
        times = [e.race_time for e in entries if e.race_time and e.race_time > 0]
        winner_time = min(times) if times else 120.0

    corner_fracs = _corner_fracs(distance)
    keyframes: list[KeyFrame] = []

    # ── コーナー到達フレーム（全馬が同じフレームでそのコーナー付近に居る） ────
    corner_frames: dict[int, int] = {
        cn: round(winner_time * frac * fps)
        for cn, frac in corner_fracs.items()
    }

    for entry in entries:
        horse_name = entry.horse_name

        # ── 各馬の走破タイム（秒）を確定 ──────────────────────────────────
        if entry.race_time and entry.race_time > 0:
            horse_time = entry.race_time
        else:
            horse_time = winner_time + entry.time_diff_secs

        finish_frame = round(horse_time * fps)

        # ── スタートキーフレーム (frame=0) ────────────────────────────────
        keyframes.append(KeyFrame(
            frame=0,
            horse_name=horse_name,
            progress=0.0,
            x_offset=_x_offset_for_start(entry.wakuban, entry.umaban),
            label="start",
        ))

        # ── コーナーキーフレーム ───────────────────────────────────────────
        for cn, frac in corner_fracs.items():
            rank = entry.corners.get(cn, 0)
            if not rank or rank <= 0:
                # 未記録コーナー → スキップ（Blender が前後のキーフレームで補間）
                continue

            # このコーナーでの進捗: リーダーのコーナー進捗から順位ギャップ分を引く
            gap = (rank - 1) * METERS_PER_RANK / distance
            progress = max(0.001, frac - gap)

            keyframes.append(KeyFrame(
                frame=corner_frames[cn],
                horse_name=horse_name,
                progress=round(progress, 4),
                x_offset=_x_offset_for_corner(rank, field_size),
                label=f"c{cn}",
            ))

        # ── ゴールキーフレーム ─────────────────────────────────────────────
        keyframes.append(KeyFrame(
            frame=finish_frame,
            horse_name=horse_name,
            progress=1.0,
            x_offset=_x_offset_for_finish(entry.final_rank, field_size),
            label="finish",
        ))

    keyframes.sort(key=lambda k: (k.frame, k.horse_name))
    return keyframes
