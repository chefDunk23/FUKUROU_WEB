"""
src/features/pace_simulation_v1.py
====================================
Pre-race pace & position simulation for the pace_v2 submodel.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WARNING — DO NOT REMOVE OR MODIFY WITHOUT READING THIS DOCSTRING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[ENGLISH]
This module produces THREE output columns that are STRICTLY PRE-RACE
(point-in-time) features. NO FUTURE / RESULT DATA IS LEAKED HERE.

  predicted_position_norm
    Each horse's expected normalized corner-1 position for THIS race,
    simulated from:
      - avg_c1_norm_5   : the horse's mean corner-1 position over its
                          PAST 5 races (computed with shift(1) in
                          pace_features_v4.py — current race excluded)
      - umaban          : post position draw, known before race start

  predicted_field_pace
    Expected pace level of the WHOLE FIELD, derived from the
    distribution of all horses' avg_c1_norm_5 values in this race.
    Races with many natural front-runners → higher predicted pace.

  pace_harmony_pre
    Single-number "scenario match" score for this horse:
      (1 - pos) * (1 - pace) + pos * pace
    = 1.0  when front-runner meets slow pace, OR closer meets fast pace
    = 0.0  when front-runner meets fast pace, OR closer meets slow pace
    Directly captures pace-style interaction without any post-race data.

INPUTS THAT ARE INTENTIONALLY EXCLUDED (post-race / result data):
  pace_type    — JV-Data SE record field "今回レース脚質判定":
                 actual running style OBSERVED after the race. NEVER use.
  pace_index   — zen_3f - go_3f: split times known only after the race.
  lap_variance — computed from actual lap_time_array: post-race.
  lap_std      — same as above.
  zen_3f       — actual front-half split: post-race.
  go_3f        — actual back-half split: post-race.

[日本語]
このモジュールが出力する3つの特徴量は、すべてレース開始前に入手可能な
事前データ（Point-in-Time）のみから計算された完全なリークフリー特徴量です。

  predicted_position_norm
    今回レースで各馬がコーナー1番手付近に就くと予測される正規化位置。
    使用する入力データ:
      - avg_c1_norm_5 : pace_features_v4.py でshift(1)済みの直近5走
                        コーナー1通過位置指数（当走は除外）
      - umaban        : 今回の馬番（レース前に確定する情報）

  predicted_field_pace
    レース全体の想定ペース指数。フィールド内の全馬の avg_c1_norm_5 分布
    から計算する。先行馬が多いほど高い値（=ハイペース予測）。

  pace_harmony_pre
    「この馬の脚質」と「今回の想定ペース」の合致度スコア。
    前づけ馬 × スローペース = 1.0（理想展開）
    追込馬   × ハイペース   = 1.0（理想展開）
    前づけ馬 × ハイペース   = 0.0（最悪展開）

削除禁止の事後データ（以下は決してこの関数の入力に加えないこと）:
  pace_type, pace_index, zen_3f, go_3f, lap_variance, lap_std
  → これらはすべてレース後に確定するデータです。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ALGORITHM
---------
1. Per race: gather avg_c1_norm_5 (tendency, 0=front) and umaban for all
   horses.
2. Compute gate-adjusted aggressiveness:
     aggressiveness  = 1 - tendency  (higher = more front-running)
     gate_bias       = (field_size - umaban) / field_size
                       * MAX_GATE_BIAS * aggressiveness
                       (inner posts help front-runners only)
     adj_agg         = aggressiveness + gate_bias
3. Rank horses by adj_agg descending → predicted rank.
     predicted_position_norm = (rank - 1) / (field_size - 1)
4. Race-level pace = 0.5 * (front-runner ratio) + 0.5 * (mean aggressiveness)
5. pace_harmony_pre = (1 - pos) * (1 - pace) + pos * pace

DESIGN NOTE — why ranking instead of raw score
  The competition for front positions is zero-sum: if Horse A pushes
  further forward, Horse B is forced back. A ranking step captures this
  naturally without needing extra parameters.

SALVAGE NOTE
  Core concepts adapted from:
    AI_FUKUROU_KEIBA_Ver2/src/features/position_features.py
      PositionFeatureEngineer (Phase 10) — gate bias coefficient,
      front-runner threshold, shift+rolling PIT guard
    AI_FUKUROU_KEIBA_Ver2/src/features/pace_adjusted_features.py
      PaceAdjustedFeatureEngineer._compute_pace_harmony() — quadratic
      harmony formula (1-eps)*(1-pps) + eps*pps
  Rewritten for v2 (race_id / horse_id schema, avg_c1_norm_5 base).
"""
from __future__ import annotations

import logging
from typing import Iterable

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# ── チューニングパラメータ ────────────────────────────────────────────────────
# 外枠補正の最大幅（これ以上は前づけ馬でも枠の影響が飽和する）
# source: position_features.py では 0.01/枠 → 18頭で最大 0.18。
#         ここでは 0 を内枠・1 を外枠に正規化した上で最大 0.10 とする。
MAX_GATE_BIAS: float = 0.10

# 先行馬判定の閾値（avg_c4_norm_5 がこれ以下 = 自然な先行傾向あり）
# avg_c4 = 最終コーナー通過順位の正規化値。スプリント含む全距離で利用可能。
FRONT_RUNNER_THRESHOLD: float = 0.30

# 出力カラム名（外部から参照するため定数として公開）
COL_PRED_POS: str = "predicted_position_norm"
COL_FIELD_PACE: str = "predicted_field_pace"
COL_HARMONY: str = "pace_harmony_pre"

PACE_SIM_COLS: list[str] = [COL_PRED_POS, COL_FIELD_PACE, COL_HARMONY]


# ── レース単位のシミュレーション ─────────────────────────────────────────────

def _simulate_one_race(race_df: pd.DataFrame) -> pd.DataFrame:
    """
    Single-race simulation.

    WARNING: DO NOT ADD pace_type, pace_index, zen_3f, go_3f here.
    / 警告: pace_type, pace_index, zen_3f, go_3f をここに追加しないこと。
    """
    n = len(race_df)
    idx = race_df.index

    # ── 入力 ──────────────────────────────────────────────────────────────────
    # avg_first_corner_norm_5: 0=先頭, 1=最後尾
    # 各過去走で最初に記録されたコーナー順位（c1→c2→c3→c4 の優先順）の正規化平均。
    # スプリント(1400m以下)ではc3が最初のコーナーになり、まくり馬の誤認を防ぐ。
    tendency: pd.Series = pd.to_numeric(
        race_df["avg_first_corner_norm_5"], errors="coerce"
    ).fillna(0.5).clip(0.0, 1.0)

    # umaban: 1〜field_size（欠損は中間枠で補完）
    umaban: pd.Series = pd.to_numeric(
        race_df["umaban"], errors="coerce"
    ).fillna(float(n // 2 + 1)).clip(1, n)

    # ── 積極性スコア ──────────────────────────────────────────────────────────
    aggressiveness = 1.0 - tendency  # 0=差し・追込, 1=逃げ

    # 内枠の有利さ: 最内枠(umaban=1)→最大効果, 最外枠→効果ゼロ
    # 先行傾向の強い馬にのみ働く（前づけ馬が枠の影響を受けやすい）
    gate_factor = (n - umaban) / n  # 0=外枠, ~1=内枠
    gate_bias = gate_factor * MAX_GATE_BIAS * aggressiveness
    adj_agg = (aggressiveness + gate_bias).clip(0.0, 1.0)

    # ── 予測ポジション（順位ベース）──────────────────────────────────────────
    # 積極性降順でランク付け → 最も積極的な馬が前（rank=1）
    rank = adj_agg.rank(ascending=False, method="average")
    if n > 1:
        pred_pos = (rank - 1.0) / (n - 1.0)  # 0=最前, 1=最後方
    else:
        pred_pos = pd.Series([0.5], index=idx)

    # ── フィールドペース指数 ──────────────────────────────────────────────────
    n_front = int((tendency < FRONT_RUNNER_THRESHOLD).sum())
    front_ratio = n_front / n
    mean_agg = float(aggressiveness.mean())
    field_pace = float(0.5 * front_ratio + 0.5 * mean_agg)

    # ── ペース合致度 ──────────────────────────────────────────────────────────
    # (1-pos)*(1-pace) + pos*pace
    # 理想展開（前づけ×スロー or 差し×ハイ）= 1.0
    # 最悪展開（前づけ×ハイ or 差し×スロー）= 0.0
    harmony = (1.0 - pred_pos) * (1.0 - field_pace) + pred_pos * field_pace

    result = race_df.copy()
    result[COL_PRED_POS] = pred_pos.values
    result[COL_FIELD_PACE] = field_pace
    result[COL_HARMONY] = harmony.values
    return result


# ── パブリック API ────────────────────────────────────────────────────────────

def create_pace_simulation_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add pace simulation columns to a whole-dataset DataFrame.

    WARNING: DO NOT REMOVE. This function uses ONLY pre-race data.
    / 警告: 削除厳禁。この関数はレース前データのみを使用します。

    Parameters
    ----------
    df : DataFrame
        Must contain columns:
          - race_id                : race identifier
          - umaban                 : post position (int)
          - avg_first_corner_norm_5: horse's mean first-recorded-corner position
                                     over past 5 races (shift(1) in pace_features_v4.py).
                                     Uses c1→c2→c3→c4 priority; sprints use c3.

    Returns
    -------
    DataFrame with three additional columns:
      predicted_position_norm  [0, 1]  0=predicted front
      predicted_field_pace     [0, 1]  0=slow, 1=fast
      pace_harmony_pre         [0, 1]  1=ideal pace-style match

    Notes
    -----
    Salted with explicit anti-leak commentary to prevent future regression.
    DO NOT add pace_type / pace_index / zen_3f / go_3f / lap_* as inputs.
    See module docstring for full rationale.
    """
    required: list[str] = ["race_id", "umaban", "avg_first_corner_norm_5"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise KeyError(
            f"create_pace_simulation_features: 必須カラムが不足: {missing}\n"
            f"  pace_features_v4.py の実行後に呼び出してください。"
        )

    # レースごとにシミュレーション → 結合
    results: list[pd.DataFrame] = []
    for race_id, grp in df.groupby("race_id", sort=False):
        results.append(_simulate_one_race(grp))

    out = pd.concat(results).sort_index()
    log.info(
        "pace_simulation_v1: %d races, %d rows → columns %s added",
        df["race_id"].nunique(), len(df), PACE_SIM_COLS,
    )

    # NaN チェック（前処理上問題が発生していないことを確認）
    for col in PACE_SIM_COLS:
        n_nan = out[col].isna().sum()
        if n_nan:
            log.warning("  %s: %d NaN (fillna 0.5 で補完)", col, n_nan)
            out[col] = out[col].fillna(0.5)

    return out
