"""
pace_bias_ai/features/same_day_time.py
=======================================
条件C: 同日同コースのタイム比較

同じ開催日・同じコース（距離±100m、同芝ダート）で
別クラスが行われた場合、走破タイムを比較して
「このレベルなら通用するか」を測る。

## 出力特徴量

| 列名 | 意味 |
|------|------|
| same_day_pace_diff | 今走クラスの期待タイムvs過去走タイムの差（正=今走が速いコース）|
| same_day_class_gap | 同日同コースで比較した上位クラスとのタイム差（秒/m）|
| same_day_n_ref | 参照できた先行レース数（0ならNaN）|

## PIT対策
- 当日レースで自分より前（race_num < 今走の race_num）のみ参照
- 土曜R1〜R3はデータ不足のため対象外（n_ref < 3 なら NaN）
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import sqlalchemy
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)

DIST_MARGIN = 100   # 距離の許容差（m）
MIN_REF_RACES = 3   # 参照レース数の最小値（未満はNaN）

SAME_DAY_TIME_COLS = [
    'same_day_pace_diff',
    'same_day_class_gap',
    'same_day_n_ref',
]


def _surface_code_prefix(track_code: str) -> str:
    """track_code の先頭1桁で芝/ダートを判別する。"""
    return str(track_code)[:1] if track_code else ''


def build_same_day_time(
    df_target: pd.DataFrame,
    engine: Engine,
    horse_id_col: str = 'horse_id',
) -> pd.DataFrame:
    """
    条件C: 同日同コースのタイム比較。

    Args:
        df_target: 対象馬DataFrame。以下の列を含むこと:
            horse_id, race_id, race_date,
            keibajo_code, track_code, distance,
            race_num (レース番号),
            race_time (今走走破タイム — 予測時はNaN)
        engine: SQLAlchemy Engine

    Returns:
        SAME_DAY_TIME_COLS の列を持つDataFrame（df_target と同インデックス）
    """
    # 対象の日付・開催場を取得
    dates = df_target['race_date'].astype(str).unique().tolist()
    log.info("same_day_time: %d日分の同日レースをロード中...", len(dates))

    # 対象日の全レース・全エントリ（走破タイム付き）をロード
    rows = []
    with engine.connect() as conn:
        for i in range(0, len(dates), 50):
            chunk = dates[i:i + 50]
            res = conn.execute(sqlalchemy.text("""
                SELECT r.race_id, r.keibajo_code, r.track_code, r.distance,
                       r.race_num, r.grade_code,
                       e.kakutei_chakujun, e.race_time
                FROM races_v2 r
                JOIN race_entries_v2 e ON r.race_id = e.race_id
                WHERE LEFT(r.race_id, 8) = ANY(:dates)
                  AND e.kakutei_chakujun = 1
                  AND e.race_time IS NOT NULL
                  AND e.race_time > 0
                  AND r.is_jra = TRUE
            """), {"dates": chunk}).fetchall()
            rows.extend([dict(r._mapping) for r in res])

    if not rows:
        log.warning("same_day_time: 同日レースデータなし")
        return pd.DataFrame(
            {c: np.nan for c in SAME_DAY_TIME_COLS},
            index=df_target.index,
        )

    same_day_all = pd.DataFrame(rows)
    same_day_all['race_date'] = same_day_all['race_id'].astype(str).str[:8]
    same_day_all['race_time_f'] = pd.to_numeric(same_day_all['race_time'], errors='coerce')
    same_day_all['distance_f']  = pd.to_numeric(same_day_all['distance'],  errors='coerce')
    same_day_all['pace_norm']   = same_day_all['race_time_f'] / same_day_all['distance_f']
    same_day_all['race_num_i']  = pd.to_numeric(same_day_all['race_num'],  errors='coerce')
    same_day_all['surf_prefix'] = same_day_all['track_code'].astype(str).str[:1]

    results = []
    for _, row in df_target.iterrows():
        cur_date    = str(row.get('race_date', str(row['race_id'])[:8]))
        cur_venue   = str(row.get('keibajo_code', ''))
        cur_dist    = pd.to_numeric(row.get('distance', np.nan), errors='coerce')
        cur_surf    = str(row.get('track_code', ''))[:1]
        cur_race_num = pd.to_numeric(row.get('race_num', np.nan), errors='coerce')

        if pd.isna(cur_dist) or pd.isna(cur_race_num):
            results.append({c: np.nan for c in SAME_DAY_TIME_COLS})
            continue

        # 同日・同会場・同芝ダート・距離±100m・自分より前のレースのみ
        mask = (
            (same_day_all['race_date'] == cur_date) &
            (same_day_all['keibajo_code'] == cur_venue) &
            (same_day_all['surf_prefix'] == cur_surf) &
            ((same_day_all['distance_f'] - cur_dist).abs() <= DIST_MARGIN) &
            (same_day_all['race_num_i'] < cur_race_num)
        )
        ref = same_day_all[mask].dropna(subset=['pace_norm'])
        n_ref = len(ref)

        if n_ref < MIN_REF_RACES:
            results.append({
                'same_day_pace_diff': np.nan,
                'same_day_class_gap': np.nan,
                'same_day_n_ref': float(n_ref),
            })
            continue

        # 参照レースの平均ペース（秒/m）
        ref_avg_pace = ref['pace_norm'].mean()

        # 今走馬の過去走タイムとの比較（horse's best time / distance）
        horse_id = str(row.get(horse_id_col, ''))
        horse_past_pace = pd.to_numeric(row.get('prev_pace_norm', np.nan), errors='coerce')
        same_day_pace_diff = (ref_avg_pace - horse_past_pace) if not pd.isna(horse_past_pace) else np.nan

        # 同日同コースで最も速いクラス（low grade_rank = higher class）と比較
        ref_best_pace = ref['pace_norm'].min()  # 最も速いレースのペース
        same_day_class_gap = horse_past_pace - ref_best_pace if not pd.isna(horse_past_pace) else np.nan

        results.append({
            'same_day_pace_diff': same_day_pace_diff,
            'same_day_class_gap': same_day_class_gap,
            'same_day_n_ref': float(n_ref),
        })

    return pd.DataFrame(results, index=df_target.index)
