"""
pace_bias_ai/features/pace_aptitude.py
=======================================
条件A: スピード×ペース適性

各馬の過去好走時（3着以内）のレース条件を集計し、
「速い決着が得意か・時計がかかるレースが得意か」を推定する。

## 出力特徴量

| 列名 | 意味 |
|------|------|
| avg_winning_pace | 好走時の平均ペース（race_time/distance, 秒/m）|
| pace_type | 好走時ペースの傾向（正=高速好み, 負=低速好み）|
| fast_finish_rate | 好走時に上がり上位1/3を使った割合 |
| slow_track_win | 重・不良での好走率 |
| pace_match_today | 今走馬場状態と過去好走時の馬場適性一致度（0/1/NaN）|

## PIT対策
- 過去走データは race_date より前のみ参照
- 当走結果は一切使わない
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd
import sqlalchemy
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)

# 好走閾値（3着以内）
TOP3_THRESH = 3
# 上がり上位1/3判定の分位点
FAST_FINISH_QUANTILE = 1 / 3
# 最小好走回数（未満はNaN）
MIN_WIN_RACES = 2
# 重・不良コード（shiba_baba_code / dirt_baba_code）
HEAVY_BABA_CODES = frozenset(['3', '4'])  # 3=重, 4=不良

PACE_APTITUDE_COLS = [
    'avg_winning_pace',
    'pace_type',
    'fast_finish_rate',
    'slow_track_win',
    'pace_match_today',
]


def _surface_from_track_code(tc) -> str:
    s = str(tc) if tc is not None else ''
    if s.startswith('1'):
        return 'turf'
    if s.startswith('2'):
        return 'dirt'
    return 'other'


def _baba_category(shiba_code, dirt_code, surface: str) -> str:
    """馬場状態を 'good'/'soft'/'heavy' に分類する。"""
    code = str(shiba_code if surface == 'turf' else dirt_code) if (shiba_code or dirt_code) else ''
    if code in HEAVY_BABA_CODES:
        return 'heavy'
    if code == '2':
        return 'soft'
    return 'good'  # 1=良, その他も良扱い


def build_pace_aptitude(
    df_target: pd.DataFrame,
    engine: Engine,
    date_col: str = 'race_date',
    horse_id_col: str = 'horse_id',
    cur_baba_col: str = 'cur_baba_category',
) -> pd.DataFrame:
    """
    条件A: スピード×ペース適性を計算する。

    Args:
        df_target: 対象馬のDataFrame。horse_id, race_date, race_id,
                   shiba_baba_code, dirt_baba_code, track_code を含むこと。
        engine: SQLAlchemy Engine
        date_col: 予測日の列名（PIT基準）
        horse_id_col: 馬ID列名

    Returns:
        PACE_APTITUDE_COLS の列を持つDataFrame（df_target と同インデックス）
    """
    blood_nos = df_target[horse_id_col].astype(str).unique().tolist()
    log.info("pace_aptitude: %d頭の過去走をロード中...", len(blood_nos))

    # 全馬の過去走データをロード
    rows = []
    with engine.connect() as conn:
        for i in range(0, len(blood_nos), 2000):
            chunk = blood_nos[i:i + 2000]
            res = conn.execute(sqlalchemy.text("""
                SELECT e.blood_no, e.race_id, e.kakutei_chakujun, e.race_time,
                       e.kohan_3f, e.corner_4,
                       r.distance, r.track_code, r.shiba_baba_code, r.dirt_baba_code
                FROM race_entries_v2 e
                JOIN races_v2 r ON e.race_id = r.race_id
                WHERE e.blood_no = ANY(:bns)
                  AND e.kakutei_chakujun IS NOT NULL
                  AND e.race_time IS NOT NULL
                  AND e.race_time > 0
                ORDER BY e.blood_no, e.race_id
            """), {"bns": chunk}).fetchall()
            rows.extend([dict(r._mapping) for r in res])

    if not rows:
        log.warning("pace_aptitude: 過去走データなし")
        return pd.DataFrame(
            {c: np.nan for c in PACE_APTITUDE_COLS},
            index=df_target.index,
        )

    hist = pd.DataFrame(rows)
    hist['blood_no'] = hist['blood_no'].astype(str)
    hist['race_date'] = hist['race_id'].astype(str).str[:8]
    hist['chaku'] = pd.to_numeric(hist['kakutei_chakujun'], errors='coerce')
    hist['race_time_f'] = pd.to_numeric(hist['race_time'], errors='coerce')
    hist['kohan_3f_f'] = pd.to_numeric(hist['kohan_3f'], errors='coerce')
    hist['distance_f'] = pd.to_numeric(hist['distance'], errors='coerce')
    hist['surface'] = hist['track_code'].apply(_surface_from_track_code)
    hist['baba_cat'] = hist.apply(
        lambda r: _baba_category(r['shiba_baba_code'], r['dirt_baba_code'], r['surface']),
        axis=1,
    )
    hist['pace_norm'] = hist['race_time_f'] / hist['distance_f']  # 秒/m

    # 上がり順位: レース内での kohan_3f 昇順ランク（小=速い）
    hist = hist.sort_values(['race_id', 'kohan_3f_f'])
    hist['kohan_rank'] = hist.groupby('race_id')['kohan_3f_f'].rank(method='min')
    hist['field_count'] = hist.groupby('race_id')['kohan_3f_f'].transform('count')
    hist['kohan_pct'] = hist['kohan_rank'] / hist['field_count']  # 0=最速

    # 好走フラグ
    hist['is_top3'] = (hist['chaku'] <= TOP3_THRESH) & hist['chaku'].notna()

    results = []
    for _, row in df_target.iterrows():
        horse_id = str(row[horse_id_col])
        cur_date = str(row[date_col])

        # PIT: 今走より前の履歴のみ
        past = hist[(hist['blood_no'] == horse_id) & (hist['race_date'] < cur_date)]
        top3_past = past[past['is_top3']]

        if len(top3_past) < MIN_WIN_RACES:
            results.append({c: np.nan for c in PACE_APTITUDE_COLS})
            continue

        # avg_winning_pace: 好走時の平均ペース
        avg_pace = top3_past['pace_norm'].mean()

        # pace_type: 好走時ペースの傾向（全レース平均との差）
        all_avg_pace = past['pace_norm'].mean() if len(past) > 0 else avg_pace
        pace_type = avg_pace - all_avg_pace  # 正=高速好み（速い決着で好走）

        # fast_finish_rate: 好走時に上がり上位1/3を使った割合
        top3_with_agari = top3_past.dropna(subset=['kohan_3f_f'])
        if len(top3_with_agari) > 0:
            fast_finish_rate = (top3_with_agari['kohan_pct'] <= FAST_FINISH_QUANTILE).mean()
        else:
            fast_finish_rate = np.nan

        # slow_track_win: 重・不良での好走率
        heavy_past = past[past['baba_cat'] == 'heavy']
        if len(heavy_past) >= 2:
            slow_track_win = heavy_past['is_top3'].mean()
        else:
            slow_track_win = np.nan

        # pace_match_today: 今走馬場と過去好走時の馬場適性一致度
        cur_track = str(row.get('track_code', ''))
        cur_surface = _surface_from_track_code(cur_track)
        cur_baba = _baba_category(
            row.get('shiba_baba_code'),
            row.get('dirt_baba_code'),
            cur_surface,
        )
        if len(top3_past) >= MIN_WIN_RACES:
            top3_heavy_rate = (top3_past['baba_cat'] == 'heavy').mean()
            if cur_baba == 'heavy':
                pace_match = 1.0 if top3_heavy_rate >= 0.3 else 0.0
            else:
                pace_match = 1.0 if top3_heavy_rate < 0.5 else 0.0
        else:
            pace_match = np.nan

        results.append({
            'avg_winning_pace': avg_pace,
            'pace_type': pace_type,
            'fast_finish_rate': fast_finish_rate,
            'slow_track_win': slow_track_win,
            'pace_match_today': pace_match,
        })

    return pd.DataFrame(results, index=df_target.index)
