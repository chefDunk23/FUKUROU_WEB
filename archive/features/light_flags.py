"""
軽い条件フラグモジュール。

AIとは独立した条件として各フラグを計算する。
全フラグPIT-safe（当該レース結果を使わない）。

## 検証結果サマリー（C期間2024-07〜2025-12）

フラグ1 斤量変化:
  軽くなった 18.8% < 変化なし 23.2% > 重くなった 20.7%
  → 「2kg以上軽」17.6%、「2kg以上重」15.6% がネガティブ
  → AI上位5での差は2〜3pt程度（弱い）→ 参考情報止まり

フラグ3 休み明け（long_rest採用）:
  90日以上: 全体18.3% / AI上位5: 33.1%（全体平均比-7.8pt） ← ネガティブ採用
  中2〜3週（15〜28日）が最高（24.3%）
  連闘（≤14日）は弱いネガティブ（20.7%）

フラグ5 年齢（aged_horse採用）:
  7歳以上: 全体14.1% / AI上位5: 33.1%（AI平均≈40%比-7pt弱） ← ネガティブ採用
  長距離の高齢馬は25%前後と健在（距離限定で影響が変わる）
  短距離×7歳以上: 9.6%

kinryo単位: 550 = 55.0kg（×10整数表現）
"""
from __future__ import annotations

import pandas as pd

# ── フラグ1: 斤量変化 ─────────────────────────────────────────────────────
KIN_LIGHTER_KG = 1.0   # 軽くなった閾値（kg）
KIN_HEAVIER_KG = 1.0   # 重くなった閾値（kg）
KIN_UNIT = 10          # kinryo 1単位 = 0.1kg なので 10単位 = 1.0kg

# ── フラグ3: 休み明け ──────────────────────────────────────────────────────
LONG_REST_DAYS   = 90   # 長期休養（3ヶ月以上）
MEDIUM_REST_DAYS = 60   # 中期休養（2ヶ月以上）
FRESH_DAYS       = 14   # 連闘（2週以内）

# ── フラグ5: 年齢 ─────────────────────────────────────────────────────────
AGED_THRESHOLD   = 7    # 高齢馬の閾値
YOUNG_AGE        = 3    # 若駒の閾値


def build_light_flags(
    df: pd.DataFrame,
    cur_kinryo_col: str = 'cur_kinryo',
    prev_kinryo_col: str = 'prev_kinryo',
    race_interval_col: str = 'race_interval',
    horse_age_col: str = 'horse_age',
) -> pd.DataFrame:
    """
    軽い条件フラグを計算して返す。

    Args:
        df: 1行1馬のDataFrame
        cur_kinryo_col:    今走斤量（kinryo単位: 550=55.0kg）
        prev_kinryo_col:   前走斤量（同上）
        race_interval_col: 前走からのレース間隔（日数、初出走はNaN）
        horse_age_col:     馬齢（歳）

    Returns:
        入力dfと同じインデックスで以下の列を持つDataFrame:
          --- フラグ1: 斤量変化 ---
          prev_weight_lighter  : 前走比1kg以上軽くなった (0/1)
          prev_weight_heavier  : 前走比1kg以上重くなった (0/1)
          big_kin_change       : 2kg以上の大幅変化（軽重どちらも）(0/1)  ← ネガティブ
          --- フラグ3: 休み明け ---
          long_rest            : 90日以上の休み明け (0/1)  ← ネガティブ採用
          medium_rest          : 60〜89日の休み明け (0/1)
          fresh                : 14日以内（連闘） (0/1)
          --- フラグ5: 年齢 ---
          young_horse          : 3歳馬 (0/1)
          aged_horse           : 7歳以上 (0/1)  ← ネガティブ採用
    """
    required = [horse_age_col]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"build_light_flags: 必須列が存在しません: {missing}")

    # --- フラグ1: 斤量変化 ---
    _nan_series = pd.Series(float('nan'), index=df.index)
    cur_kin  = pd.to_numeric(df[cur_kinryo_col]  if cur_kinryo_col  in df.columns else _nan_series, errors='coerce')
    prev_kin = pd.to_numeric(df[prev_kinryo_col] if prev_kinryo_col in df.columns else _nan_series, errors='coerce')
    kin_diff = prev_kin - cur_kin  # 正=軽くなった、負=重くなった
    has_kinryo = cur_kin.notna() & prev_kin.notna()

    prev_weight_lighter = (
        (kin_diff >= KIN_LIGHTER_KG * KIN_UNIT) & has_kinryo
    ).astype(int)
    prev_weight_heavier = (
        (kin_diff <= -(KIN_HEAVIER_KG * KIN_UNIT)) & has_kinryo
    ).astype(int)
    big_kin_change = (
        (kin_diff.abs() >= 2 * KIN_UNIT) & has_kinryo
    ).astype(int)

    # --- フラグ3: 休み明け ---
    interval = pd.to_numeric(df[race_interval_col] if race_interval_col in df.columns else _nan_series, errors='coerce')
    long_rest   = (interval >= LONG_REST_DAYS).astype(int)
    medium_rest = ((interval >= MEDIUM_REST_DAYS) & (interval < LONG_REST_DAYS)).astype(int)
    fresh       = ((interval >= 1) & (interval <= FRESH_DAYS)).astype(int)

    # --- フラグ5: 年齢 ---
    age = pd.to_numeric(df[horse_age_col], errors='coerce')
    young_horse = (age == YOUNG_AGE).astype(int)
    aged_horse  = (age >= AGED_THRESHOLD).astype(int)

    return pd.DataFrame({
        'prev_weight_lighter': prev_weight_lighter,
        'prev_weight_heavier': prev_weight_heavier,
        'big_kin_change': big_kin_change,
        'long_rest': long_rest,
        'medium_rest': medium_rest,
        'fresh': fresh,
        'young_horse': young_horse,
        'aged_horse': aged_horse,
    }, index=df.index)


def compute_race_interval(
    race_id_col: pd.Series,
    prev_race_id_col: pd.Series,
) -> pd.Series:
    """
    race_id（先頭8桁がYYYYMMDD）から今走・前走の日付差（日数）を計算する。

    Args:
        race_id_col:      今走の race_id
        prev_race_id_col: 前走の race_id（初出走はNaN）

    Returns:
        レース間隔（日数）のSeries。前走なしはNaN。
    """
    today = pd.to_datetime(race_id_col.astype(str).str[:8], format='%Y%m%d', errors='coerce')
    prev = pd.to_datetime(
        prev_race_id_col.astype(str).str[:8].where(prev_race_id_col.notna()),
        format='%Y%m%d', errors='coerce'
    )
    return (today - prev).dt.days


LIGHT_FLAG_COLS = [
    'prev_weight_lighter', 'prev_weight_heavier', 'big_kin_change',
    'long_rest', 'medium_rest', 'fresh',
    'young_horse', 'aged_horse',
]

# ── 採用フラグ一覧（検証済み） ───────────────────────────────────────────
# ネガティブフラグ（該当馬を下方評価する際に使う）:
#   long_rest  : AI上位5で-7.8pt（33.1% vs 40.9%）
#   aged_horse : AI上位5で-7pt弱（33.1% vs 40%弱）
#   big_kin_change: 2kg以上軽量/増量で-5〜6pt
# 参考情報（採用保留）:
#   prev_weight_lighter: 単体では逆効果（18.8%）、AI上位5で差小さい
#   fresh               : 弱いネガティブ（-1pt）
#   medium_rest         : 差なし（ニュートラル）
#   young_horse         : 全体高いが条件戦のみの効果で差なし
