"""
前走大敗の「度外視」条件判定モジュール。

AIとは独立した条件として、前走の着順を度外視すべき理由があるか判定する。
度外視 = 「前走を参考外とし、前々走の成績で馬を再評価する」判断。

## 検証済みパターン（C期間2024-07〜2025-12）

パターン1 — G1/G2帰り（excuse_grade）:
  前走G1/G2(grade_code=A/B) かつ 6着以下 かつ 今走G3以下
  → 前走大敗ベースライン13.1% に対し 29.0%（+16.2pt）✅
  → 前々走成績に関係なく有効

パターン2 — 先行大敗（excuse_pace）:
  前走corner_4 ≤ 5（先行〜3番手）かつ 6着以下
  → 単体: +5.5pt（ベースライン→17.0%）✅
  → 前々走3着以内との組み合わせで24.3%（+11pt）✅✅
  → 前々走も6着以下なら効果なし（13.0%）
  ※ テン3Fデータなしのため「先行して大敗した事実」で代替

廃止パターン — コース変わり・距離不適:
  芝↔ダート変更 / 同コース距離差±400〜600m = 全て逆効果（⚠️）
  → コース変わりは前々走好走時のみ弱い効果（17.5%）だが単独条件としては不採用

## 推奨使用方法
  1. excuse_grade: 前々走不問で採用
  2. excuse_pace_with_prev2_good: 前々走3着以内と組み合わせて採用（24.3%）
  3. excuse_pace: 前々走情報なしの場合のフォールバック

grade_code対応（JV-Data準拠）:
  'A' = G1, 'B' = G2, 'C' = G3
  'D' = 特別競走, 'E' = 未勝利/新馬, 'L' = リステッド, None = 条件戦
"""
from __future__ import annotations

import pandas as pd

POOR_FINISH_THRESHOLD = 6    # 6着以下を「大敗」と定義
LEADING_CORNER4_MAX = 5      # 先行判定の4角通過順閾値
GOOD_FINISH_THRESHOLD = 3    # 好走（前々走再評価用）

G1G2_GRADES = frozenset(['A', 'B'])


def build_excuse_flags(
    df: pd.DataFrame,
    prev_chakujun_col: str = 'prev_kakutei_chakujun',
    prev_grade_col: str = 'prev_grade_code',
    prev_corner4_col: str = 'prev_corner_4',
    cur_grade_col: str = 'cur_grade_code',
    prev2_chakujun_col: str | None = None,
) -> pd.DataFrame:
    """
    度外視フラグを計算して返す。

    Args:
        df: 1行1馬のDataFrame
        prev_chakujun_col: 前走着順の列名
        prev_grade_col: 前走grade_codeの列名
        prev_corner4_col: 前走4角通過順の列名
        cur_grade_col: 今走grade_codeの列名
        prev2_chakujun_col: 前々走着順の列名（Noneなら前々走フィルターを省略）

    Returns:
        入力dfと同じインデックスで以下の列を持つDataFrame:
            prev_big_loss          : 前走6着以下フラグ (bool)
            excuse_grade           : G1/G2帰り度外視 (0/1)
            excuse_pace            : 先行大敗度外視 (0/1)
            excuse_pace_prev2_good : 先行大敗 × 前々走好走 (0/1)
            excuse_any             : いずれかに該当 (0/1)
    """
    required = [prev_chakujun_col, prev_grade_col, prev_corner4_col, cur_grade_col]
    if prev2_chakujun_col:
        required.append(prev2_chakujun_col)
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"build_excuse_flags: 必須列が存在しません: {missing}")

    prev_chaku = pd.to_numeric(df[prev_chakujun_col], errors='coerce')
    prev_big_loss = (prev_chaku >= POOR_FINISH_THRESHOLD) & prev_chaku.notna()

    # パターン1: G1/G2帰り（今走G3以下 = 格下がり）
    prev_is_g1g2 = df[prev_grade_col].isin(G1G2_GRADES)
    cur_not_g1g2 = ~df[cur_grade_col].isin(G1G2_GRADES)
    excuse_grade = (prev_is_g1g2 & prev_big_loss & cur_not_g1g2).astype(int)

    # パターン2: 先行大敗
    prev_c4 = pd.to_numeric(df[prev_corner4_col], errors='coerce')
    excuse_pace = (
        (prev_c4 >= 1) & (prev_c4 <= LEADING_CORNER4_MAX) & prev_big_loss & prev_c4.notna()
    ).astype(int)

    # パターン2強化: 先行大敗 × 前々走好走（前々走3着以内）
    if prev2_chakujun_col:
        prev2_chaku = pd.to_numeric(df[prev2_chakujun_col], errors='coerce')
        prev2_good = (prev2_chaku <= GOOD_FINISH_THRESHOLD) & prev2_chaku.notna()
        excuse_pace_prev2_good = (excuse_pace == 1) & prev2_good
        excuse_pace_prev2_good = excuse_pace_prev2_good.astype(int)
    else:
        excuse_pace_prev2_good = pd.Series(0, index=df.index, dtype=int)

    excuse_any = ((excuse_grade == 1) | (excuse_pace == 1)).astype(int)

    return pd.DataFrame({
        'prev_big_loss': prev_big_loss,
        'excuse_grade': excuse_grade,
        'excuse_pace': excuse_pace,
        'excuse_pace_prev2_good': excuse_pace_prev2_good,
        'excuse_any': excuse_any,
    }, index=df.index)


def fetch_prev_race_info(
    blood_nos: list[str],
    engine,
    chunk_size: int = 3000,
    include_prev2: bool = True,
) -> pd.DataFrame:
    """
    指定血統番号の全レース履歴をDBから取得し、各レースに前走・前々走情報を付与して返す。

    Args:
        blood_nos: 対象血統番号のリスト
        engine: SQLAlchemy エンジン
        chunk_size: 一度にDBへ投げる血統番号の最大数
        include_prev2: Trueなら前々走情報（prev2_*）も計算する

    Returns:
        DataFrame with columns:
            blood_no, race_id, kakutei_chakujun, corner_4, distance, grade_code,
            track_code, surface,
            prev_{...}, [prev2_{...} if include_prev2]
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
                       r.distance, r.grade_code, r.track_code
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
    df['surface'] = df['track_code'].apply(_track_to_surface)

    shift_cols = ['race_id', 'kakutei_chakujun', 'corner_4', 'distance', 'grade_code', 'surface']
    grp = df.groupby('blood_no')
    for col in shift_cols:
        df[f'prev_{col}'] = grp[col].shift(1)
        if include_prev2:
            df[f'prev2_{col}'] = grp[col].shift(2)

    return df


def _track_to_surface(track_code) -> str:
    tc = str(track_code) if track_code is not None else ''
    if tc.startswith('1'):
        return 'turf'
    if tc.startswith('2'):
        return 'dirt'
    return 'other'


EXCUSE_COLS = ['prev_big_loss', 'excuse_grade', 'excuse_pace',
               'excuse_pace_prev2_good', 'excuse_any']
