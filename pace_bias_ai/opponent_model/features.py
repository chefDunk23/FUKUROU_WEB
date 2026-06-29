"""
前走メンバーレベル特徴量の生成モジュール（v2: 前々走ベース + competitiveness_score）。

全特徴量はPIT-safe（当該レース結果を使わない）。
opponent_next_* は「予測日より前に次走を走った馬のみ」でカウント。

v2の主な変更:
- prev2（前々走）ベースのopponent_next系を追加（欠損率改善・頭数+45%）
- prev1（前走）ベースも維持し、AIに両者を使い分けさせる
- competitiveness_score: レースレベル × 着差の複合指標
- prev_grade_rank, prev2_grade_rank: グレード連続値
- grade_change: グレード変化方向

クラス序列（class_rank: 低いほど上位）:
  1=G1, 2=G2, 3=G3, 4=OP/L, 5=3勝, 6=2勝, 7=1勝, 8=未勝利, 9=新馬
"""
from __future__ import annotations

import logging

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
    # ── 前走パフォーマンス ───────────────────────────────────────────────────
    'prev_rank_norm',       # 前走着順/出走頭数（最重要シグナル）
    'prev_rank',            # 前走着順（生値）
    'prev_margin',          # 前走勝ち馬との着差（秒）

    # ── 前々走ベースのレースレベル（メイン） ─────────────────────────────────
    'prev2_opp_top3_rate',  # 前々走の対戦相手の次走3着以内率
    'prev2_opp_top3_count', # 前々走の対戦相手の次走3着以内頭数
    'prev2_opp_count',      # 前々走の対戦相手の次走データ数（PIT後）
    'prev2_top3_next_avg',  # 前々走の1〜3着馬の次走平均着順
    'prev2_top3_next_rate', # 前々走の1〜3着馬の次走3着以内率

    # ── 前走ベースのレースレベル（補助） ─────────────────────────────────────
    'prev1_opp_top3_rate',  # 前走の対戦相手の次走3着以内率
    'prev1_opp_top3_count', # 前走の対戦相手の次走3着以内頭数
    'prev1_opp_count',      # 前走の対戦相手の次走データ数（PIT後）

    # ── レースレベル × 着差（複合指標） ─────────────────────────────────────
    'competitiveness_score',  # prev2_opp_top3_rate / (1 + prev_margin)

    # ── クラス情報 ────────────────────────────────────────────────────────
    'prev_grade_rank',      # 前走クラス連続値（G1=1〜条件戦=5）
    'prev2_grade_rank',     # 前々走クラス連続値
    'grade_change',         # 今走vs前走のクラス変化（正=格上げ、負=格下げ）
    'cur_class_rank',
    'class_change',
    'class_up',
    'class_down',
    'grade_drop',           # G1/G2→今走格下フラグ

    # ── 斤量 ────────────────────────────────────────────────────────────
    'kinryo_change',
    'kinryo_vs_field',

    # ── 条件変化 ─────────────────────────────────────────────────────────
    'distance_change',
    'surface_changed',
    'venue_changed',

    # ── 馬属性・レースコンテキスト ─────────────────────────────────────────
    'horse_age',
    'dist_cat',
    'surface_code',
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
    全レース履歴と全レースメタ情報をDBからロード（2019年以降）。

    Returns:
        df_entries: blood_no, race_id, kakutei_chakujun, race_time,
                    kinryo, horse_age, horse_weight, umaban
        df_races:   race_id, grade_code, jyoken_cd_youngest, distance,
                    track_code, keibajo_code, class_rank
    """
    with engine.connect() as conn:
        log.info("race_entries_v2 ロード中（2019年以降）...")
        df_entries = pd.read_sql(sqlalchemy.text("""
            SELECT blood_no, race_id, kakutei_chakujun, race_time,
                   kinryo, horse_age, horse_weight, umaban
            FROM race_entries_v2
            WHERE LEFT(race_id,8) >= '20190101'
              AND kakutei_chakujun IS NOT NULL
        """), conn)

        log.info("races_v2 ロード中（2019年以降）...")
        df_races = pd.read_sql(sqlalchemy.text("""
            SELECT race_id, grade_code, jyoken_cd_youngest,
                   distance, track_code, keibajo_code
            FROM races_v2
            WHERE LEFT(race_id,8) >= '20190101'
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


def _build_opp_agg(
    slim: pd.DataFrame,
    opp_next: pd.DataFrame,
    prefix: str,
    include_top3_filter: bool = False,
) -> pd.DataFrame:
    """
    前走または前々走ベースの opponent_next 集計を vectorized で実行する。

    Args:
        slim:      _uid, prev_race_id, _bn, _cur_date の4列
        opp_next:  prev_race_id, opp_bn, opp_prev_chaku, opp_next_chaku, opp_next_date
        prefix:    特徴量列名のプレフィックス（'prev1' or 'prev2'）
        include_top3_filter: Trueのとき、前走1〜3着馬限定の集計も返す

    Returns:
        _uid をインデックスにした集計 DataFrame
    """
    slim_valid = slim.dropna(subset=['prev_race_id'])

    merged = slim_valid.merge(opp_next, on='prev_race_id', how='left')
    merged = merged[
        merged['opp_next_date'].notna() &
        (merged['opp_next_date'] < merged['_cur_date']) &
        (merged['opp_bn'] != merged['_bn'])
    ]
    merged['_next_n'] = pd.to_numeric(merged['opp_next_chaku'], errors='coerce')

    agg = merged.groupby('_uid')['_next_n'].agg(
        **{
            f'{prefix}_opp_top3_rate':  lambda x: (x <= 3).mean(),
            f'{prefix}_opp_top3_count': lambda x: int((x <= 3).sum()),
            f'{prefix}_opp_count':      'count',
        }
    )

    if include_top3_filter:
        merged['_prev_n'] = pd.to_numeric(merged['opp_prev_chaku'], errors='coerce')
        top3 = merged[merged['_prev_n'] <= 3]
        top3_agg = top3.groupby('_uid')['_next_n'].agg(
            **{
                f'{prefix}_top3_next_avg':  'mean',
                f'{prefix}_top3_next_rate': lambda x: (x <= 3).mean(),
            }
        )
        agg = agg.join(top3_agg, how='left')

    return agg


def build_opponent_features(
    df_target: pd.DataFrame,
    df_entries: pd.DataFrame,
    df_races: pd.DataFrame,
) -> pd.DataFrame:
    """
    前々走ベース + 前走ベースの opponent_next 特徴量を vectorized で付与する。

    Args:
        df_target : 対象馬のDataFrame。必要列:
                      horse_id (=blood_no str), race_id (str)
                      kinryo (int, optional), horse_age (float, optional)
        df_entries: load_all_race_history()の df_entries（2019年以降）
        df_races  : load_all_race_history()の df_races

    Returns:
        df_target と同じ行数・インデックスで FEATURE_COLS の列を持つ DataFrame
    """
    tgt = df_target.copy().reset_index(drop=True)
    tgt['_bn']       = tgt['horse_id'].astype(str)
    tgt['_rid']      = tgt['race_id'].astype(str)
    tgt['_cur_date'] = tgt['_rid'].str[:8]
    tgt['_uid']      = tgt.index  # 0〜N-1

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

    if 'kinryo' in tgt.columns:
        kin_num   = pd.to_numeric(tgt['kinryo'], errors='coerce')
        field_avg = tgt.groupby('_rid')['kinryo'].transform(
            lambda x: pd.to_numeric(x, errors='coerce').mean()
        )
        tgt['kinryo_vs_field'] = (kin_num - field_avg) / 10.0
    else:
        tgt['kinryo_vs_field'] = np.nan

    # ── 前走・前々走情報（shift） ─────────────────────────────────────────
    ent = (
        df_entries
        .drop_duplicates(subset=['blood_no', 'race_id'])
        .sort_values(['blood_no', 'race_id'])
        .reset_index(drop=True)
    )
    ent['prev1_race_id']   = ent.groupby('blood_no')['race_id'].shift(1)
    ent['prev2_race_id']   = ent.groupby('blood_no')['race_id'].shift(2)
    ent['prev1_chaku']     = ent.groupby('blood_no')['kakutei_chakujun'].shift(1)
    ent['prev1_kinryo']    = ent.groupby('blood_no')['kinryo'].shift(1)
    ent['prev1_race_time'] = ent.groupby('blood_no')['race_time'].shift(1)
    ent['next_race_id']    = ent.groupby('blood_no')['race_id'].shift(-1)
    ent['next_chaku']      = ent.groupby('blood_no')['kakutei_chakujun'].shift(-1)
    ent['next_date']       = ent['next_race_id'].str[:8]

    ent_idx = ent.set_index(['blood_no', 'race_id'])
    midx = pd.MultiIndex.from_arrays(
        [tgt['_bn'].values, tgt['_rid'].values], names=['blood_no', 'race_id']
    )
    fetch_cols = ['prev1_race_id', 'prev2_race_id', 'prev1_chaku', 'prev1_kinryo',
                  'prev1_race_time', 'kinryo', 'horse_age', 'race_time']
    prev_vals = ent_idx.reindex(midx)[fetch_cols].values
    prev_df = pd.DataFrame(prev_vals, columns=fetch_cols, index=tgt.index)
    prev_df.rename(columns={'kinryo': 'kinryo_ent', 'horse_age': 'horse_age_ent',
                             'race_time': 'cur_race_time'}, inplace=True)
    tgt = tgt.join(prev_df)

    if 'horse_age' not in tgt.columns:
        tgt['horse_age'] = pd.to_numeric(tgt['horse_age_ent'], errors='coerce')
    else:
        tgt['horse_age'] = tgt['horse_age'].fillna(
            pd.to_numeric(tgt['horse_age_ent'], errors='coerce')
        )

    # ── 前走/前々走レースメタ ─────────────────────────────────────────────
    def _attach_race_meta(rid_series: pd.Series, prefix: str) -> pd.DataFrame:
        fill = rid_series.fillna('__na__').values
        vals = race_meta.reindex(fill)[
            ['class_rank', 'distance', 'track_code', 'keibajo_code', 'grade_code']
        ].values
        df = pd.DataFrame(
            vals,
            columns=[f'{prefix}_class_rank', f'{prefix}_distance',
                     f'{prefix}_track_code', f'{prefix}_keibajo', f'{prefix}_grade'],
            index=tgt.index,
        )
        df.loc[rid_series.isna().values] = np.nan
        return df

    prev1_rid = tgt['prev1_race_id'].where(tgt['prev1_race_id'].notna())
    prev2_rid = tgt['prev2_race_id'].where(tgt['prev2_race_id'].notna())

    tgt = tgt.join(_attach_race_meta(prev1_rid, 'prev1'))
    tgt = tgt.join(_attach_race_meta(prev2_rid, 'prev2'))

    # ── クラス変動 ────────────────────────────────────────────────────────
    cur_cr   = pd.to_numeric(tgt['cur_class_rank'],    errors='coerce')
    prev1_cr = pd.to_numeric(tgt['prev1_class_rank'],  errors='coerce')
    prev2_cr = pd.to_numeric(tgt['prev2_class_rank'],  errors='coerce')
    tgt['class_change'] = cur_cr - prev1_cr
    tgt['class_up']   = (tgt['class_change'] < 0).astype(float)
    tgt['class_down'] = (tgt['class_change'] > 0).astype(float)
    tgt['grade_drop'] = (
        tgt['prev1_grade'].fillna('').isin(['A', 'B']) &
        ~tgt['cur_grade'].fillna('').isin(['A', 'B'])
    ).astype(float)

    tgt['prev_grade_rank']  = prev1_cr
    tgt['prev2_grade_rank'] = prev2_cr
    tgt['grade_change']     = cur_cr - prev1_cr

    # ── 距離・馬場・競馬場変化 ────────────────────────────────────────────
    tgt['distance_change'] = (
        pd.to_numeric(tgt['cur_distance'],    errors='coerce') -
        pd.to_numeric(tgt['prev1_distance'],  errors='coerce')
    )
    cur_surf  = (~tgt['cur_track_code'].astype(str).str.startswith('1')).astype(int)
    prev_surf = (~tgt['prev1_track_code'].astype(str).str.startswith('1')).astype(float)
    prev_surf = prev_surf.where(prev1_rid.notna())
    tgt['surface_changed'] = (cur_surf != prev_surf).where(prev1_rid.notna()).astype(float)
    tgt['venue_changed'] = (
        tgt['cur_keibajo'].astype(str) != tgt['prev1_keibajo'].astype(str)
    ).where(prev1_rid.notna()).astype(float)

    # ── 斤量変化 ──────────────────────────────────────────────────────────
    cur_kin = pd.to_numeric(
        tgt['kinryo'] if 'kinryo' in tgt.columns else tgt['kinryo_ent'], errors='coerce'
    )
    tgt['kinryo_change'] = (cur_kin - pd.to_numeric(tgt['prev1_kinryo'], errors='coerce')) / 10.0

    # ── 前走着順・着差 ────────────────────────────────────────────────────
    tgt['prev_rank'] = pd.to_numeric(tgt['prev1_chaku'], errors='coerce')
    prev_field_size  = ent.groupby('race_id').size().rename('field_size')
    tgt['prev_field_size'] = prev_field_size.reindex(
        prev1_rid.fillna('__na__').values
    ).values
    tgt.loc[prev1_rid.isna(), 'prev_field_size'] = np.nan
    tgt['prev_rank_norm'] = tgt['prev_rank'] / tgt['prev_field_size']

    win_times = (
        ent[pd.to_numeric(ent['kakutei_chakujun'], errors='coerce') == 1]
        .groupby('race_id')['race_time'].first()
    )
    tgt['_win_time'] = win_times.reindex(
        prev1_rid.fillna('__na__').values
    ).values
    tgt.loc[prev1_rid.isna(), '_win_time'] = np.nan
    tgt['prev_margin'] = (
        pd.to_numeric(tgt['prev1_race_time'], errors='coerce') -
        pd.to_numeric(tgt['_win_time'],       errors='coerce')
    )

    # ── opp_next テーブル（前走/前々走共用） ─────────────────────────────
    log.info("opp_next テーブル構築中...")
    opp_next = ent[ent['next_race_id'].notna()][
        ['race_id', 'blood_no', 'kakutei_chakujun', 'next_chaku', 'next_date']
    ].copy()
    opp_next.columns = ['prev_race_id', 'opp_bn', 'opp_prev_chaku',
                        'opp_next_chaku', 'opp_next_date']

    # ── 前々走ベースの集計（メイン） ─────────────────────────────────────
    log.info("prev2 opponent_next 計算中...")
    slim2 = tgt[['_uid', '_bn', '_cur_date']].copy()
    slim2['prev_race_id'] = tgt['prev2_race_id'].values
    agg_prev2 = _build_opp_agg(slim2, opp_next, 'prev2', include_top3_filter=True)
    tgt = tgt.merge(agg_prev2.reset_index(names='_uid'), on='_uid', how='left')

    # ── 前走ベースの集計（補助） ─────────────────────────────────────────
    log.info("prev1 opponent_next 計算中...")
    slim1 = tgt[['_uid', '_bn', '_cur_date']].copy()
    slim1['prev_race_id'] = tgt['prev1_race_id'].values
    agg_prev1 = _build_opp_agg(slim1, opp_next, 'prev1', include_top3_filter=False)
    tgt = tgt.merge(agg_prev1.reset_index(names='_uid'), on='_uid', how='left')

    # ── competitiveness_score ─────────────────────────────────────────────
    prev2_rate = pd.to_numeric(tgt.get('prev2_opp_top3_rate', np.nan), errors='coerce')
    margin     = pd.to_numeric(tgt['prev_margin'], errors='coerce').clip(lower=0)
    tgt['competitiveness_score'] = prev2_rate / (1.0 + margin)

    # ── 最終整形 ──────────────────────────────────────────────────────────
    for col in FEATURE_COLS:
        if col not in tgt.columns:
            tgt[col] = np.nan

    result = tgt[FEATURE_COLS].copy()
    log.info("特徴量計算完了: %d行 %d列", len(result), len(result.columns))
    return result
