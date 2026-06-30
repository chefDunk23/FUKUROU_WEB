"""
pace_bias_ai/features/rotation_flag.py
=======================================
条件B: ローテーション・本気度

陣営がこのレースをどれだけ本気で狙いに来ているか推定する。

## 出力特徴量

| 列名 | 意味 |
|------|------|
| rotation_type | ローテーションパターン (0=標準, 1=本気度高, -1=叩き台/疑問) |
| is_genuine | 本気出走フラグ (中2〜4週 + クラス据え置き) |
| is_step | 叩き台疑惑フラグ (長期休養明け or 大幅格下げ) |
| transport_flag | 長距離輸送フラグ（所属と今走競馬場が東西反対）|
| class_vs_best | 過去最高クラスと今走のクラス差（正=格上参戦, 負=未知クラス）|

## 競馬場の東西分類
- 東系(美浦圏): keibajo_code 01-06（札幌・函館・福島・新潟・東京・中山）
- 西系(栗東圏): keibajo_code 07-10（中京・京都・阪神・小倉）

## PIT対策
- trainer_feature_store の target_date < race_date のレコードを参照
- 各馬の過去出走（race_id < 今走 race_id）から東西傾向を計算
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import sqlalchemy
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)

# 競馬場コードの東西分類
EAST_VENUES = frozenset(['01', '02', '03', '04', '05', '06'])
WEST_VENUES  = frozenset(['07', '08', '09', '10'])

# ローテーション判定
GENUINE_MIN_DAYS    = 15  # 中2週以上
GENUINE_MAX_DAYS    = 28  # 中4週以下
GENUINE_PREV_MAX_RANK = 5  # 前走5着以内（掲示板）が必須
STEP_REST_DAYS      = 90  # 90日以上休養 = 叩き台疑惑
BIG_DROP_RANKS      = 3   # 3ランク以上格下げ = 叩き台疑惑

# クラス序列（grade_code準拠）
_GRADE_RANK = {'A': 1, 'B': 2, 'C': 3, 'L': 4, 'D': 5}
_DEFAULT_GRADE_RANK = 6  # 条件戦（None/E等）

ROTATION_COLS = [
    'rotation_type',
    'is_genuine',
    'is_step',
    'transport_flag',
    'class_vs_best',
]


def _grade_rank(code) -> int:
    if not code:
        return _DEFAULT_GRADE_RANK
    return _GRADE_RANK.get(str(code).upper(), _DEFAULT_GRADE_RANK)


def _venue_side(keibajo_code: str) -> str:
    code = str(keibajo_code).zfill(2)
    if code in EAST_VENUES:
        return 'east'
    if code in WEST_VENUES:
        return 'west'
    return 'other'


def build_rotation_flags(
    df_target: pd.DataFrame,
    engine: Engine,
    horse_id_col: str = 'horse_id',
) -> pd.DataFrame:
    """
    条件B: ローテーション・本気度フラグを計算する。

    Args:
        df_target: 対象馬DataFrame。以下の列を含むこと:
            horse_id, race_id, race_date,
            race_interval (日数), cur_grade_code, prev_grade_code,
            keibajo_code, chokyosi_code
        engine: SQLAlchemy Engine

    Returns:
        ROTATION_COLS の列を持つDataFrame（df_target と同インデックス）
    """
    blood_nos = df_target[horse_id_col].astype(str).unique().tolist()
    log.info("rotation_flag: %d頭の過去走をロード中...", len(blood_nos))

    # 過去走の競馬場・クラスを取得
    rows = []
    with engine.connect() as conn:
        for i in range(0, len(blood_nos), 2000):
            chunk = blood_nos[i:i + 2000]
            res = conn.execute(sqlalchemy.text("""
                SELECT e.blood_no, e.race_id, e.kakutei_chakujun,
                       r.keibajo_code, r.grade_code
                FROM race_entries_v2 e
                JOIN races_v2 r ON e.race_id = r.race_id
                WHERE e.blood_no = ANY(:bns)
                  AND e.kakutei_chakujun IS NOT NULL
                ORDER BY e.blood_no, e.race_id
            """), {"bns": chunk}).fetchall()
            rows.extend([dict(r._mapping) for r in res])

    hist = pd.DataFrame(rows) if rows else pd.DataFrame()
    if not hist.empty:
        hist['blood_no'] = hist['blood_no'].astype(str)
        hist['grade_rank'] = hist['grade_code'].apply(_grade_rank)

    results = []
    for _, row in df_target.iterrows():
        horse_id = str(row[horse_id_col])
        cur_date = str(row.get('race_date', row['race_id'][:8]))
        cur_race_id = str(row['race_id'])

        # 過去走（今走より前）
        if not hist.empty:
            past = hist[(hist['blood_no'] == horse_id) & (hist['race_id'] < cur_race_id)]
        else:
            past = pd.DataFrame()

        # ─── rotation_type / is_genuine / is_step ─────────────────────────
        interval = pd.to_numeric(row.get('race_interval', np.nan), errors='coerce')
        cur_grade_rank  = _grade_rank(row.get('cur_grade_code') or row.get('grade_code'))
        prev_grade_rank = _grade_rank(row.get('prev_grade_code'))
        class_drop = prev_grade_rank - cur_grade_rank  # 正=格下げ（rank大→小）

        # 前走着順（past の最後のレース）
        prev_chaku = np.nan
        if not past.empty:
            prev_chaku = pd.to_numeric(
                past.iloc[-1].get('kakutei_chakujun', np.nan), errors='coerce'
            )

        # 本気ローテ: 中2〜4週 + 格下げ小幅 + 前走掲示板（5着以内）
        is_genuine = int(
            (GENUINE_MIN_DAYS <= interval <= GENUINE_MAX_DAYS) and
            (abs(class_drop) <= 1) and
            (pd.notna(prev_chaku) and prev_chaku <= GENUINE_PREV_MAX_RANK)
        )
        is_step = int(
            (interval >= STEP_REST_DAYS) or
            (class_drop >= BIG_DROP_RANKS)
        )
        if is_genuine:
            rotation_type = 1
        elif is_step:
            rotation_type = -1
        else:
            rotation_type = 0

        # ─── transport_flag ────────────────────────────────────────────────
        cur_venue_side = _venue_side(str(row.get('keibajo_code', '')))
        if not past.empty and len(past) >= 3:
            past_sides = past['keibajo_code'].apply(lambda x: _venue_side(str(x)))
            east_cnt = (past_sides == 'east').sum()
            west_cnt = (past_sides == 'west').sum()
            total = east_cnt + west_cnt
            if total > 0:
                east_ratio = east_cnt / total
                # 東系が多い調教師が西系競馬場に来る場合 or その逆
                if east_ratio >= 0.65 and cur_venue_side == 'west':
                    transport_flag = 1
                elif east_ratio <= 0.35 and cur_venue_side == 'east':
                    transport_flag = 1
                else:
                    transport_flag = 0
            else:
                transport_flag = np.nan
        else:
            transport_flag = np.nan

        # ─── class_vs_best ────────────────────────────────────────────────
        if not past.empty:
            best_class_rank = past['grade_rank'].min()  # 最高クラス（rank最小）
            cur_grade_rank_v = _grade_rank(row.get('cur_grade_code') or row.get('grade_code'))
            class_vs_best = best_class_rank - cur_grade_rank_v  # 正=今走が格上（格上参戦）
        else:
            class_vs_best = np.nan

        results.append({
            'rotation_type': rotation_type,
            'is_genuine': is_genuine,
            'is_step': is_step,
            'transport_flag': transport_flag,
            'class_vs_best': class_vs_best,
        })

    return pd.DataFrame(results, index=df_target.index)
