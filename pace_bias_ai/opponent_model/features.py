"""
前走メンバーレベル特徴量の生成モジュール（vectorized実装）。

全特徴量はPIT-safe（当該レース結果を使わない）。
opponent_next_* は「予測日より前に次走を走った馬のみ」でカウント。

クラス序列（class_rank: 低いほど上位）:
  1=G1, 2=G2, 3=G3, 4=OP/L, 5=3勝, 6=2勝, 7=1勝, 8=未勝利, 9=新馬
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd
import sqlalchemy

log = logging.getLogger(__name__)

# ── クラス序列マップ ─────────────────────────────────────────────────────────
GRADE_TO_CLASS: dict[str, int] = {
    'A': 1, 'B': 2, 'C': 3, 'L': 4, 'D': 4,
}
JYOKEN_TO_CLASS: dict[str, int] = {
    '999': 4,  # オープン
    '703': 5,  # 3勝クラス
    '701': 6,  # 2勝クラス
    '005': 7,  # 1勝クラス
    '010': 8,  # 未勝利
    '016': 9,  # 新馬
}

FEATURE_COLS: list[str] = [
    'opponent_next_top3_rate', 'opponent_next_win_rate',
    'opponent_next_avg_rank', 'opponent_count',
    'prev_class_rank', 'cur_class_rank', 'class_change',
    'class_up', 'class_down', 'grade_drop',
    'prev_margin', 'prev_rank', 'prev_rank_norm',
    'kinryo_change', 'kinryo_vs_field',
    'distance_change', 'surface_changed', 'venue_changed',
    'horse_age', 'dist_cat', 'surface_code',
]


def _vec_class_rank(grade_code: pd.Series, jyoken_cd: pd.Series) -> pd.Series:
    result = pd.Series(5, index=grade_code.index, dtype=int)
    for g, v in GRADE_TO_CLASS.items():
        result = result.where(grade_code != g, v)
    mask_no_grade = ~grade_code.isin(GRADE_TO_CLASS)
    for j, v in JYOKEN_TO_CLASS.items():
        result = result.where(~(mask_no_grade & (jyoken_cd == j)), v)
    return result


def load_all_race_history(engine) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    全レース履歴と全レースメタ情報をDBからロード。

    Returns:
        df_entries: blood_no, race_id, kakutei_chakujun, race_time,
                    kinryo, horse_age, horse_weight, umaban
        df_races:   race_id, grade_code, jyoken_cd_youngest, distance,
                    track_code, keibajo_code, class_rank
    """
    with engine.connect() as conn:
        log.info("race_entries_v2 ロード中（2020年以降）...")
        df_entries = pd.read_sql(sqlalchemy.text("""
            SELECT blood_no, race_id, kakutei_chakujun, race_time,
                   kinryo, horse_age, horse_weight, umaban
            FROM race_entries_v2
            WHERE LEFT(race_id,8) >= '20200101'
              AND kakutei_chakujun IS NOT NULL
        """), conn)

        log.info("races_v2 ロード中（2020年以降）...")
        df_races = pd.read_sql(sqlalchemy.text("""
            SELECT race_id, grade_code, jyoken_cd_youngest,
                   distance, track_code, keibajo_code
            FROM races_v2
            WHERE LEFT(race_id,8) >= '20200101'
        """), conn)

    df_entries['blood_no'] = df_entries['blood_no'].astype(str)
    df_entries['race_id']  = df_entries['race_id'].astype(str)
    df_races['race_id']    = df_races['race_id'].astype(str)
    df_races['grade_code'] = df_races['grade_code'].fillna('')
    df_races['jyoken_cd_youngest'] = df_races['jyoken_cd_youngest'].fillna('')

    df_races['class_rank'] = _vec_class_rank(
        df_races['grade_code'], df_races['jyoken_cd_youngest']
    )
    log.info("entries=%d, races=%d", len(df_entries), len(df_races))
    return df_entries, df_races


def build_opponent_features(
    df_target: pd.DataFrame,
    df_entries: pd.DataFrame,
    df_races: pd.DataFrame,
) -> pd.DataFrame:
    """
    vectorized処理で前走メンバーレベル特徴量を付与する。

    Args:
        df_target : 対象馬のDataFrame。必要列:
                      horse_id (=blood_no str), race_id (str)
                      kinryo (int, optional), horse_age (float, optional)
        df_entries: load_all_race_history()の df_entries
        df_races  : load_all_race_history()の df_races

    Returns:
        df_target と同じ行数・インデックスで FEATURE_COLS の列を持つ DataFrame
    """
    tgt = df_target.copy()
    tgt['_bn']       = tgt['horse_id'].astype(str)
    tgt['_rid']      = tgt['race_id'].astype(str)
    tgt['_cur_date'] = tgt['_rid'].str[:8]
    # 一意キー（indexが重複する場合に備えて）
    tgt['_uid'] = range(len(tgt))

    # ── レースメタ付与（今走） ────────────────────────────────────────────
    race_meta = df_races.set_index('race_id')

    cur_meta_vals = race_meta.reindex(tgt['_rid'].values)[
        ['distance', 'track_code', 'keibajo_code', 'class_rank', 'grade_code']
    ].values
    cur_meta_df = pd.DataFrame(
        cur_meta_vals,
        columns=['cur_distance', 'cur_track_code', 'cur_keibajo',
                 'cur_class_rank', 'cur_grade'],
        index=tgt.index,
    )
    tgt = tgt.join(cur_meta_df)

    tgt['dist_cat'] = pd.cut(
        pd.to_numeric(tgt['cur_distance'], errors='coerce'),
        bins=[0, 1400, 1800, 2200, 9999], labels=[0, 1, 2, 3]
    ).astype(float)

    tgt['surface_code'] = (
        ~tgt['cur_track_code'].astype(str).str.startswith('1')
    ).astype(int)

    # kinryo_vs_field
    if 'kinryo' in tgt.columns:
        kin_num = pd.to_numeric(tgt['kinryo'], errors='coerce')
        field_avg = tgt.groupby('_rid')['kinryo'].transform(
            lambda x: pd.to_numeric(x, errors='coerce').mean()
        )
        tgt['kinryo_vs_field'] = (kin_num - field_avg) / 10.0
    else:
        tgt['kinryo_vs_field'] = np.nan

    # ── 前走情報（shift(1)相当） ─────────────────────────────────────────
    # (blood_no, race_id) を一意にしてから shift(1) を適用
    ent = (
        df_entries
        .drop_duplicates(subset=['blood_no', 'race_id'])
        .sort_values(['blood_no', 'race_id'])
        .reset_index(drop=True)
    )
    ent['prev_race_id']   = ent.groupby('blood_no')['race_id'].shift(1)
    ent['prev_chaku']     = ent.groupby('blood_no')['kakutei_chakujun'].shift(1)
    ent['prev_kinryo']    = ent.groupby('blood_no')['kinryo'].shift(1)
    ent['prev_race_time'] = ent.groupby('blood_no')['race_time'].shift(1)

    ent_idx = ent.set_index(['blood_no', 'race_id'])

    midx = pd.MultiIndex.from_arrays(
        [tgt['_bn'].values, tgt['_rid'].values], names=['blood_no', 'race_id']
    )
    prev_vals = ent_idx.reindex(midx)[
        ['prev_race_id', 'prev_chaku', 'prev_kinryo', 'prev_race_time',
         'kinryo', 'horse_age', 'race_time']
    ].values
    prev_df = pd.DataFrame(
        prev_vals,
        columns=['prev_race_id', 'prev_chaku', 'prev_kinryo', 'prev_race_time',
                 'kinryo_ent', 'horse_age_ent', 'cur_race_time'],
        index=tgt.index,
    )
    tgt = tgt.join(prev_df)

    # horse_age の補完（部分的NaNも含めて fallback で埋める）
    if 'horse_age' not in tgt.columns:
        tgt['horse_age'] = pd.to_numeric(tgt['horse_age_ent'], errors='coerce')
    else:
        tgt['horse_age'] = tgt['horse_age'].fillna(
            pd.to_numeric(tgt['horse_age_ent'], errors='coerce')
        )

    tgt['_prev_rid'] = tgt['prev_race_id'].astype(str).where(tgt['prev_race_id'].notna())

    # ── 前走レースメタ ────────────────────────────────────────────────────
    prev_meta_vals = race_meta.reindex(
        tgt['_prev_rid'].fillna('__na__').values
    )[['class_rank', 'distance', 'track_code', 'keibajo_code', 'grade_code']].values
    prev_meta_df = pd.DataFrame(
        prev_meta_vals,
        columns=['prev_class_rank', 'prev_distance', 'prev_track_code',
                 'prev_keibajo', 'prev_grade'],
        index=tgt.index,
    )
    tgt = tgt.join(prev_meta_df)
    # prev_race_idがなかった行を NaN に戻す
    no_prev = tgt['_prev_rid'].isna()
    for c in ['prev_class_rank', 'prev_distance', 'prev_track_code',
              'prev_keibajo', 'prev_grade']:
        tgt.loc[no_prev, c] = np.nan

    # ── クラス変動 ────────────────────────────────────────────────────────
    cur_cr  = pd.to_numeric(tgt['cur_class_rank'],  errors='coerce')
    prev_cr = pd.to_numeric(tgt['prev_class_rank'], errors='coerce')
    tgt['class_change'] = cur_cr - prev_cr
    tgt['class_up']   = (tgt['class_change'] < 0).astype(float)
    tgt['class_down'] = (tgt['class_change'] > 0).astype(float)
    prev_grade_s = tgt['prev_grade'].fillna('')
    cur_grade_s  = tgt['cur_grade'].fillna('')
    tgt['grade_drop'] = (
        prev_grade_s.isin(['A', 'B']) & ~cur_grade_s.isin(['A', 'B'])
    ).astype(float)

    # ── 距離・馬場・競馬場変化 ────────────────────────────────────────────
    tgt['distance_change'] = (
        pd.to_numeric(tgt['cur_distance'],  errors='coerce') -
        pd.to_numeric(tgt['prev_distance'], errors='coerce')
    )

    tgt['_cur_surf'] = (
        ~tgt['cur_track_code'].astype(str).str.startswith('1')
    ).astype(int)
    tgt['_prev_surf'] = (
        ~tgt['prev_track_code'].astype(str).str.startswith('1')
    ).astype(float).where(tgt['_prev_rid'].notna())
    tgt['surface_changed'] = (
        tgt['_cur_surf'] != tgt['_prev_surf']
    ).where(tgt['_prev_rid'].notna()).astype(float)

    tgt['venue_changed'] = (
        tgt['cur_keibajo'].astype(str) != tgt['prev_keibajo'].astype(str)
    ).where(tgt['_prev_rid'].notna()).astype(float)

    # ── 斤量変化 ──────────────────────────────────────────────────────────
    cur_kin_s  = pd.to_numeric(
        tgt['kinryo'] if 'kinryo' in tgt.columns else tgt['kinryo_ent'], errors='coerce'
    )
    prev_kin_s = pd.to_numeric(tgt['prev_kinryo'], errors='coerce')
    tgt['kinryo_change'] = (cur_kin_s - prev_kin_s) / 10.0

    # ── 前走着順・着差 ────────────────────────────────────────────────────
    tgt['prev_rank'] = pd.to_numeric(tgt['prev_chaku'], errors='coerce')

    prev_field_size = ent.groupby('race_id').size()
    tgt['prev_field_size'] = prev_field_size.reindex(
        tgt['_prev_rid'].fillna('__na__').values
    ).values
    tgt.loc[no_prev, 'prev_field_size'] = np.nan
    tgt['prev_rank_norm'] = tgt['prev_rank'] / tgt['prev_field_size']

    win_times = (
        ent[pd.to_numeric(ent['kakutei_chakujun'], errors='coerce') == 1]
        .groupby('race_id')['race_time']
        .first()
    )
    tgt['_win_time'] = win_times.reindex(
        tgt['_prev_rid'].fillna('__na__').values
    ).values
    tgt.loc[no_prev, '_win_time'] = np.nan
    tgt['prev_margin'] = (
        pd.to_numeric(tgt['prev_race_time'], errors='coerce') -
        pd.to_numeric(tgt['_win_time'],      errors='coerce')
    )

    # ── opponent_next系（vectorized + PIT-safe） ──────────────────────────
    log.info("opponent_next 計算中（vectorized）...")

    ent_next = ent.copy()
    ent_next['next_race_id']   = ent_next.groupby('blood_no')['race_id'].shift(-1)
    ent_next['next_chaku']     = ent_next.groupby('blood_no')['kakutei_chakujun'].shift(-1)
    ent_next['next_race_date'] = ent_next['next_race_id'].str[:8]
    opp_next = ent_next[ent_next['next_race_id'].notna()][[
        'race_id', 'blood_no', 'next_chaku', 'next_race_date'
    ]].copy()
    opp_next.columns = ['prev_race_id', 'opp_blood_no', 'opp_next_chaku', 'opp_next_date']

    target_slim = tgt[['_uid', '_prev_rid', '_bn', '_cur_date']].dropna(
        subset=['_prev_rid']
    ).rename(columns={'_prev_rid': 'prev_race_id', '_bn': '_blood_no'})

    merged = target_slim.merge(opp_next, on='prev_race_id', how='left')
    merged = merged[
        merged['opp_next_date'].notna() &
        (merged['opp_next_date'] < merged['_cur_date']) &
        (merged['opp_blood_no'] != merged['_blood_no'])
    ]

    opp_agg = merged.groupby('_uid')['opp_next_chaku'].agg(
        opponent_next_top3_rate=lambda x: (pd.to_numeric(x, errors='coerce') <= 3).mean(),
        opponent_next_win_rate =lambda x: (pd.to_numeric(x, errors='coerce') == 1).mean(),
        opponent_next_avg_rank =lambda x: pd.to_numeric(x, errors='coerce').mean(),
        opponent_count         ='count',
    ).reindex(tgt['_uid'].values)
    opp_agg.index = tgt.index

    tgt = tgt.join(opp_agg)

    # ── 最終整形 ──────────────────────────────────────────────────────────
    for col in FEATURE_COLS:
        if col not in tgt.columns:
            tgt[col] = np.nan

    result = tgt[FEATURE_COLS].copy()
    log.info("特徴量計算完了: %d行 %d列", len(result), len(result.columns))
    return result
