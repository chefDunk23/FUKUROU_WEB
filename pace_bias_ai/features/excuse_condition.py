"""
前走大敗の「度外視」条件判定モジュール。

AIとは独立した条件として、前走の着順を度外視すべき理由があるか判定する。
前走情報のみ使用（今走結果を使わない → PIT-safe）。

パターン定義:
  1. G1/G2帰り: 前走G1/G2 かつ 6着以下 かつ 今走G3以下
  2. 距離不適: |前走距離 - 今走距離| >= 400m かつ 6着以下
  3. 先行大敗: 前走corner_4 <= 5（先行）かつ 6着以下
              ※テン3Fデータなしのため「先行して大敗した事実」で代替

grade_code対応（JV-Data準拠）:
  'A' = G1, 'B' = G2, 'C' = G3
  'D' = 特別競走, 'E' = 未勝利/新馬, 'L' = リステッド, None = 条件戦
"""
from __future__ import annotations

import numpy as np
import pandas as pd

POOR_FINISH_THRESHOLD = 6  # 6着以下を「大敗」と定義
DISTANCE_EXCUSE_MIN = 400  # 距離差の閾値（m）
LEADING_CORNER4_MAX = 5    # 先行判定の4角通過順閾値

G1G2_GRADES = frozenset(['A', 'B'])


def build_excuse_flags(
    df: pd.DataFrame,
    prev_chakujun_col: str = 'prev_kakutei_chakujun',
    prev_grade_col: str = 'prev_grade_code',
    prev_dist_col: str = 'prev_distance',
    prev_corner4_col: str = 'prev_corner_4',
    cur_grade_col: str = 'cur_grade_code',
    cur_dist_col: str = 'cur_distance',
) -> pd.DataFrame:
    """
    度外視フラグを計算して返す。

    入力 df には以下の列が必要:
        prev_kakutei_chakujun, prev_grade_code, prev_distance,
        prev_corner_4, cur_grade_code, cur_distance

    Returns:
        入力dfと同じインデックスで以下の列を持つDataFrame:
            prev_big_loss    : 前走6着以下フラグ (bool)
            excuse_grade     : G1/G2帰り度外視 (0/1 int)
            excuse_distance  : 距離不適度外視 (0/1 int)
            excuse_pace      : 先行大敗度外視 (0/1 int)
            excuse_any       : いずれかに該当 (0/1 int)
    """
    required_cols = [prev_chakujun_col, prev_grade_col, prev_dist_col,
                     prev_corner4_col, cur_grade_col, cur_dist_col]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(f"build_excuse_flags: 必須列が存在しません: {missing}")

    prev_chaku = pd.to_numeric(df[prev_chakujun_col], errors='coerce')
    prev_big_loss = (prev_chaku >= POOR_FINISH_THRESHOLD) & prev_chaku.notna()

    # パターン1: G1/G2帰り
    prev_is_g1g2 = df[prev_grade_col].isin(G1G2_GRADES)
    cur_not_g1g2 = ~df[cur_grade_col].isin(G1G2_GRADES)
    excuse_grade = (prev_is_g1g2 & prev_big_loss & cur_not_g1g2).astype(int)

    # パターン2: 距離不適
    prev_dist = pd.to_numeric(df[prev_dist_col], errors='coerce')
    cur_dist = pd.to_numeric(df[cur_dist_col], errors='coerce')
    dist_diff = (prev_dist - cur_dist).abs()
    excuse_distance = (
        (dist_diff >= DISTANCE_EXCUSE_MIN) & prev_big_loss & dist_diff.notna()
    ).astype(int)

    # パターン3: 先行大敗（テン3F代替）
    prev_c4 = pd.to_numeric(df[prev_corner4_col], errors='coerce')
    excuse_pace = (
        (prev_c4 >= 1) & (prev_c4 <= LEADING_CORNER4_MAX) & prev_big_loss & prev_c4.notna()
    ).astype(int)

    excuse_any = ((excuse_grade == 1) | (excuse_distance == 1) | (excuse_pace == 1)).astype(int)

    return pd.DataFrame({
        'prev_big_loss': prev_big_loss,
        'excuse_grade': excuse_grade,
        'excuse_distance': excuse_distance,
        'excuse_pace': excuse_pace,
        'excuse_any': excuse_any,
    }, index=df.index)


def fetch_prev_race_info(
    blood_nos: list[str],
    engine,
    chunk_size: int = 3000,
) -> pd.DataFrame:
    """
    指定血統番号の全レース履歴をDBから取得し、各レースに「前走情報」を付与して返す。

    Args:
        blood_nos: 対象血統番号のリスト
        engine: SQLAlchemy エンジン
        chunk_size: 一度にDBへ投げる血統番号の最大数

    Returns:
        DataFrame with columns:
            blood_no, race_id, kakutei_chakujun, corner_4, distance, grade_code,
            prev_race_id, prev_kakutei_chakujun, prev_corner_4, prev_distance, prev_grade_code
    """
    if chunk_size <= 0:
        raise ValueError(f"chunk_size は正の整数である必要があります: {chunk_size}")

    import sqlalchemy

    rows: list[dict] = []
    with engine.connect() as conn:
        for i in range(0, len(blood_nos), chunk_size):
            chunk = blood_nos[i:i + chunk_size]
            result = conn.execute(sqlalchemy.text("""
                SELECT e.blood_no, e.race_id, e.kakutei_chakujun, e.corner_4,
                       r.distance, r.grade_code
                FROM race_entries_v2 e
                JOIN races_v2 r ON e.race_id = r.race_id
                WHERE e.blood_no = ANY(:bns)
                  AND e.kakutei_chakujun IS NOT NULL
                ORDER BY e.blood_no, e.race_id
            """), {"bns": chunk}).fetchall()
            rows.extend([dict(r._mapping) for r in result])

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    df['blood_no'] = df['blood_no'].astype(str)
    df = df.sort_values(['blood_no', 'race_id']).reset_index(drop=True)

    for col in ['race_id', 'kakutei_chakujun', 'corner_4', 'distance', 'grade_code']:
        df[f'prev_{col}'] = df.groupby('blood_no')[col].shift(1)

    return df


EXCUSE_COLS = ['prev_big_loss', 'excuse_grade', 'excuse_distance', 'excuse_pace', 'excuse_any']
