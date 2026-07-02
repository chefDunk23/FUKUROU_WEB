"""
pace_bias_ai/features/win_pattern.py
=======================================
条件D: 過去の勝ちパターンとの類似度

この馬が過去に勝った時のレース条件・走り方と、
今回のレースがどれだけ似ているかを計算する。

## 出力特徴量

| 列名 | 意味 |
|------|------|
| win_pattern_score | 今走との類似度合計（0〜3点）|
| win_dist_match | 距離帯一致（±200m以内）(0/1/NaN) |
| win_surface_match | 芝/ダート一致 (0/1/NaN) |
| win_style_match | 脚質一致（先行/差し）(0/1/NaN) |
| win_has_history | 過去に勝ち鞍があるか (0/1) |

## 実装方針
- 「直近の勝ち鞍1つ」を基準に計算（全勝ち鞍の集計は重いため）
- 脚質判定: corner_4 / field_count <= 0.4 → 先行、> 0.4 → 差し

## PIT対策
- 今走の race_id より前の勝ち鞍のみ参照
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import sqlalchemy
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)

DIST_MATCH_MARGIN = 200  # 距離帯一致の許容差（m）
FRONT_STYLE_THRESH = 0.4  # corner_4 / field_count の先行判定閾値

WIN_PATTERN_COLS = [
    'win_pattern_score',
    'win_dist_match',
    'win_surface_match',
    'win_style_match',
    'win_has_history',
]


def _surface_from_track_code(tc) -> str:
    s = str(tc) if tc is not None else ''
    if s.startswith('1'):
        return 'turf'
    if s.startswith('2'):
        return 'dirt'
    return 'other'


def build_win_pattern(
    df_target: pd.DataFrame,
    engine: Engine,
    horse_id_col: str = 'horse_id',
) -> pd.DataFrame:
    """
    条件D: 過去の勝ちパターンとの類似度を計算する。

    Args:
        df_target: 対象馬DataFrame。以下の列を含むこと:
            horse_id, race_id,
            distance, track_code, corner_4 (or field_count)
        engine: SQLAlchemy Engine

    Returns:
        WIN_PATTERN_COLS の列を持つDataFrame（df_target と同インデックス）
    """
    blood_nos = df_target[horse_id_col].astype(str).unique().tolist()
    log.info("win_pattern: %d頭の勝ち鞍をロード中...", len(blood_nos))

    rows = []
    with engine.connect() as conn:
        for i in range(0, len(blood_nos), 2000):
            chunk = blood_nos[i:i + 2000]
            res = conn.execute(sqlalchemy.text("""
                SELECT e.blood_no, e.race_id, e.corner_4,
                       r.distance, r.track_code, r.shusso_tosu
                FROM race_entries_v2 e
                JOIN races_v2 r ON e.race_id = r.race_id
                WHERE e.blood_no = ANY(:bns)
                  AND e.kakutei_chakujun = 1
                ORDER BY e.blood_no, e.race_id DESC
            """), {"bns": chunk}).fetchall()
            rows.extend([dict(r._mapping) for r in res])

    if not rows:
        log.warning("win_pattern: 勝ち鞍データなし")
        return pd.DataFrame(
            {c: np.nan for c in WIN_PATTERN_COLS},
            index=df_target.index,
        )

    wins = pd.DataFrame(rows)
    wins['blood_no'] = wins['blood_no'].astype(str)
    wins['surface'] = wins['track_code'].apply(_surface_from_track_code)
    wins['distance_f'] = pd.to_numeric(wins['distance'], errors='coerce')
    wins['corner_4_f'] = pd.to_numeric(wins['corner_4'], errors='coerce')
    wins['field_size'] = pd.to_numeric(wins['shusso_tosu'], errors='coerce').fillna(16)
    wins['c4_pct'] = wins['corner_4_f'] / wins['field_size']
    wins['is_front'] = wins['c4_pct'] <= FRONT_STYLE_THRESH  # True=先行

    results = []
    for _, row in df_target.iterrows():
        horse_id = str(row[horse_id_col])
        cur_race_id = str(row['race_id'])

        # 今走より前の勝ち鞍（直近1つ）
        past_wins = wins[
            (wins['blood_no'] == horse_id) & (wins['race_id'] < cur_race_id)
        ]

        win_has_history = int(len(past_wins) > 0)

        if len(past_wins) == 0:
            results.append({
                'win_pattern_score': np.nan,
                'win_dist_match': np.nan,
                'win_surface_match': np.nan,
                'win_style_match': np.nan,
                'win_has_history': win_has_history,
            })
            continue

        # 直近の勝ち鞍（race_id降順で先頭）
        latest = past_wins.iloc[0]

        cur_dist    = pd.to_numeric(row.get('distance', np.nan), errors='coerce')
        cur_surf    = _surface_from_track_code(row.get('track_code', ''))
        cur_c4      = pd.to_numeric(row.get('corner_4', np.nan), errors='coerce')
        cur_field   = pd.to_numeric(row.get('field_size', row.get('shusso_tosu', 16)), errors='coerce')
        cur_c4_pct  = cur_c4 / cur_field if (not pd.isna(cur_c4) and cur_field > 0) else np.nan
        cur_front   = cur_c4_pct <= FRONT_STYLE_THRESH if not pd.isna(cur_c4_pct) else None

        # 距離一致
        win_dist = latest['distance_f']
        if pd.isna(cur_dist) or pd.isna(win_dist):
            win_dist_match = np.nan
        else:
            win_dist_match = int(abs(cur_dist - win_dist) <= DIST_MATCH_MARGIN)

        # 芝ダート一致
        win_surf = latest['surface']
        win_surface_match = int(cur_surf == win_surf) if (cur_surf != 'other' and win_surf != 'other') else np.nan

        # 脚質一致
        win_front = latest['is_front']
        if cur_front is None or pd.isna(latest['c4_pct']):
            win_style_match = np.nan
        else:
            win_style_match = int(cur_front == bool(win_front))

        # 合計スコア（NaN は0扱い）
        score_parts = [win_dist_match, win_surface_match, win_style_match]
        valid_parts = [p for p in score_parts if not (isinstance(p, float) and np.isnan(p))]
        win_pattern_score = float(sum(valid_parts)) if valid_parts else np.nan

        results.append({
            'win_pattern_score': win_pattern_score,
            'win_dist_match': win_dist_match,
            'win_surface_match': win_surface_match,
            'win_style_match': win_style_match,
            'win_has_history': win_has_history,
        })

    return pd.DataFrame(results, index=df_target.index)
