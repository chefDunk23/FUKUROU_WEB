"""
pace_bias_ai/features/sire_aptitude.py
=======================================
血統×適性スコアを事実集計（複勝率差分）で計算するモジュール。

設計方針:
- 説明可能性最優先: 「この種牡馬の芝複勝率は○%（全体比+X%pt）」を直接返す
- PIT-safe: race_date 以前の最新スナップショットのみ参照
- サンプル不足は中立(NaN): N < N_MIN なら父父へフォールバック、父父もNG なら NaN
- AIモデルは変更しない: スコアは補正層として独立

## 出力特徴量（全て "種牡馬複勝率 - 種牡馬全体複勝率" の差分、単位 %pt）

| 列名              | 意味                              |
|-------------------|-----------------------------------|
| sire_course_fit   | コース（芝/ダート）適性差分       |
| sire_dist_fit     | 距離帯（短/マイル/中/長）適性差分 |
| sire_venue_fit    | 競馬場別適性差分                  |
| sire_age_fit      | 馬齢別適性差分                    |
| sire_sex_fit      | 性別別適性差分                    |
| sire_weight_gap   | 産駒平均体重 - 今回馬体重（kg）  |
| sire_top3_rate    | 種牡馬全体複勝率（参照値）        |
| sire_total_count  | 種牡馬産駒出走数（信頼度）        |
| fallback_to_sire_sire | 父父フォールバックを使用したか |
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd
import sqlalchemy
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# サンプル数閾値: これ未満なら父父フォールバックまたはNaN
N_MIN_SIRE = 30

# dist_cat → sire_feature_store の距離列プレフィックス
_DIST_CAT_MAP: dict[int, str] = {
    0: "dist_sprint",
    1: "dist_mile",
    2: "dist_middle",
    3: "dist_long",
}

# keibajo_code → venue列プレフィックス
_VENUE_MAP: dict[str, str] = {
    "01": "venue_01", "02": "venue_02", "03": "venue_03",
    "04": "venue_04", "05": "venue_05", "06": "venue_06",
    "07": "venue_07", "08": "venue_08", "09": "venue_09",
    "10": "venue_10",
}

# sex_cd → 性別列プレフィックス (1=牡/セン, 2=牝)
_SEX_MAP: dict[int, str] = {
    1: "sex_male",
    2: "sex_female",
}

# horse_age → 年齢列プレフィックス
def _age_prefix(age: int) -> str:
    if age <= 2:   return "age2"
    if age == 3:   return "age3"
    if age == 4:   return "age4"
    return "age5plus"


def _load_sire_stats_bulk(
    sire_ids: list[str],
    target_date: str,
    conn: sqlalchemy.engine.Connection,
) -> pd.DataFrame:
    """target_date 以前の最新スナップショットを sire_ids 全件一括取得。"""
    if not sire_ids:
        return pd.DataFrame()

    query = sqlalchemy.text("""
        SELECT DISTINCT ON (sire_id)
            sire_id, target_date, total_count,
            win_rate, top3_rate,
            surface_turf_count, surface_turf_top3_rate, surface_turf_top3_shift,
            surface_dirt_count, surface_dirt_top3_rate, surface_dirt_top3_shift,
            dist_sprint_count, dist_sprint_top3_rate, dist_sprint_top3_shift,
            dist_mile_count,   dist_mile_top3_rate,   dist_mile_top3_shift,
            dist_middle_count, dist_middle_top3_rate, dist_middle_top3_shift,
            dist_long_count,   dist_long_top3_rate,   dist_long_top3_shift,
            venue_01_count, venue_01_top3_rate, venue_01_top3_shift,
            venue_02_count, venue_02_top3_rate, venue_02_top3_shift,
            venue_03_count, venue_03_top3_rate, venue_03_top3_shift,
            venue_04_count, venue_04_top3_rate, venue_04_top3_shift,
            venue_05_count, venue_05_top3_rate, venue_05_top3_shift,
            venue_06_count, venue_06_top3_rate, venue_06_top3_shift,
            venue_07_count, venue_07_top3_rate, venue_07_top3_shift,
            venue_08_count, venue_08_top3_rate, venue_08_top3_shift,
            venue_09_count, venue_09_top3_rate, venue_09_top3_shift,
            venue_10_count, venue_10_top3_rate, venue_10_top3_shift,
            age2_count,     age2_top3_rate,     age2_top3_shift,
            age3_count,     age3_top3_rate,     age3_top3_shift,
            age4_count,     age4_top3_rate,     age4_top3_shift,
            age5plus_count, age5plus_top3_rate, age5plus_top3_shift,
            sex_male_count,   sex_male_top3_rate,   sex_male_top3_shift,
            sex_female_count, sex_female_top3_rate, sex_female_top3_shift,
            avg_all_weight
        FROM sire_feature_store
        WHERE sire_id = ANY(:ids)
          AND target_date <= :td
        ORDER BY sire_id, target_date DESC
    """)
    return pd.read_sql(query, conn, params={"ids": sire_ids, "td": target_date})


def _load_horses_bulk(
    horse_ids: list[str],
    conn: sqlalchemy.engine.Connection,
) -> pd.DataFrame:
    """horse_id → sire_id / sire_sire_id を一括取得（父父フォールバック用）。"""
    if not horse_ids:
        return pd.DataFrame()

    query = sqlalchemy.text("""
        SELECT h.id AS horse_id,
               h.sire_id,
               hs.sire_id AS sire_sire_id
        FROM horses h
        LEFT JOIN horses hs ON h.sire_id = hs.id
        WHERE h.id = ANY(:ids)
    """)
    return pd.read_sql(query, conn, params={"ids": horse_ids})


def _fetch_horse_meta_bulk(
    horse_ids: list[str],
    conn: sqlalchemy.engine.Connection,
) -> pd.DataFrame:
    """race_entries_v2 から sex_cd / horse_age を一括取得（horse_id = blood_no）。"""
    if not horse_ids:
        return pd.DataFrame()

    query = sqlalchemy.text("""
        SELECT DISTINCT ON (blood_no)
            blood_no AS horse_id,
            sex_cd,
            horse_age
        FROM race_entries_v2
        WHERE blood_no = ANY(:ids)
        ORDER BY blood_no, race_id DESC
    """)
    return pd.read_sql(query, conn, params={"ids": horse_ids})


def _pick_shift(
    row: pd.Series,
    count_col: str,
    shift_col: str,
    n_min: int = N_MIN_SIRE,
) -> Optional[float]:
    """カウントが n_min 以上なら top3_shift を返し、未満なら None。"""
    cnt = row.get(count_col)
    if pd.isna(cnt) or cnt < n_min:
        return None
    val = row.get(shift_col)
    if pd.isna(val):
        return None
    return float(val)


def _compute_row_scores(
    row: pd.Series,          # 1馬1レース行
    sire_stats: pd.Series,   # sire_feature_store の1行
) -> dict[str, float]:
    """1行分の血統適性スコア辞書を計算して返す。"""
    out: dict[str, float] = {
        "sire_top3_rate":   float(sire_stats.get("top3_rate", np.nan)),
        "sire_total_count": float(sire_stats.get("total_count", 0)),
        "sire_course_fit":  np.nan,
        "sire_dist_fit":    np.nan,
        "sire_venue_fit":   np.nan,
        "sire_age_fit":     np.nan,
        "sire_sex_fit":     np.nan,
        "sire_weight_gap":  np.nan,
    }

    # コース適性
    track = str(row.get("track_code", "")).zfill(2)
    if track.startswith("1"):
        v = _pick_shift(sire_stats, "surface_turf_count", "surface_turf_top3_shift")
    elif track.startswith("2"):
        v = _pick_shift(sire_stats, "surface_dirt_count", "surface_dirt_top3_shift")
    else:
        v = None
    if v is not None:
        out["sire_course_fit"] = v

    # 距離帯適性
    dist_cat = row.get("dist_cat")
    if dist_cat is not None and not pd.isna(dist_cat):
        pfx = _DIST_CAT_MAP.get(int(dist_cat))
        if pfx:
            v = _pick_shift(sire_stats, f"{pfx}_count", f"{pfx}_top3_shift")
            if v is not None:
                out["sire_dist_fit"] = v

    # 競馬場別適性
    venue_code = str(row.get("keibajo_code", "")).zfill(2)
    vpfx = _VENUE_MAP.get(venue_code)
    if vpfx:
        v = _pick_shift(sire_stats, f"{vpfx}_count", f"{vpfx}_top3_shift")
        if v is not None:
            out["sire_venue_fit"] = v

    # 年齢別適性
    horse_age = row.get("horse_age")
    if horse_age is not None and not pd.isna(horse_age):
        apfx = _age_prefix(int(horse_age))
        v = _pick_shift(sire_stats, f"{apfx}_count", f"{apfx}_top3_shift")
        if v is not None:
            out["sire_age_fit"] = v

    # 性別別適性
    sex_cd = row.get("sex_cd")
    if sex_cd is not None and not pd.isna(sex_cd):
        spfx = _SEX_MAP.get(int(sex_cd))
        if spfx:
            v = _pick_shift(sire_stats, f"{spfx}_count", f"{spfx}_top3_shift")
            if v is not None:
                out["sire_sex_fit"] = v

    # 馬体重ギャップ（産駒平均 - 今回）
    avg_w = sire_stats.get("avg_all_weight")
    horse_w = row.get("horse_weight")
    if not pd.isna(avg_w) and not pd.isna(horse_w) and horse_w > 0:
        out["sire_weight_gap"] = float(avg_w) - float(horse_w)

    return out


SIRE_APTITUDE_COLS = [
    "sire_course_fit",
    "sire_dist_fit",
    "sire_venue_fit",
    "sire_age_fit",
    "sire_sex_fit",
    "sire_weight_gap",
    "sire_top3_rate",
    "sire_total_count",
    "fallback_to_sire_sire",
]


def build_sire_aptitude(
    df: pd.DataFrame,
    engine: Engine,
    target_date: str,
) -> pd.DataFrame:
    """
    df に血統適性スコアを付与して返す。

    Parameters
    ----------
    df : race_entries DataFrame (horse_id, race_date, track_code,
         dist_cat, keibajo_code, horse_weight が必要)
    engine : SQLAlchemy Engine
    target_date : スナップショット参照日 (必須, "YYYY-MM-DD" 形式)
        PIT安全性のため呼び出し元が明示的に渡すこと。
        df に複数の race_date が混在する場合は、df 内の最古日の前日を渡すこと。
        例: target_date = str(df["race_date"].min().date() - timedelta(days=1))

    Returns
    -------
    df に SIRE_APTITUDE_COLS の列を追加した新 DataFrame
    """
    df = df.copy()
    for col in SIRE_APTITUDE_COLS:
        df[col] = np.nan
    df["fallback_to_sire_sire"] = 0

    horse_ids = df["horse_id"].dropna().astype(str).unique().tolist()
    logger.info("血統適性取得: %d 頭 / target_date=%s", len(horse_ids), target_date)

    with engine.connect() as conn:
        # horses → sire_id / sire_sire_id の取得
        horses_df = _load_horses_bulk(horse_ids, conn)
        if horses_df.empty:
            logger.warning("horses テーブルからデータが取得できませんでした")
            return df

        horses_df["horse_id"] = horses_df["horse_id"].astype(str)
        horses_df["sire_id"] = horses_df["sire_id"].astype(str)
        horses_df["sire_sire_id"] = horses_df["sire_sire_id"].fillna("").astype(str)

        # sex_cd / horse_age の取得
        meta_df = _fetch_horse_meta_bulk(horse_ids, conn)
        meta_df["horse_id"] = meta_df["horse_id"].astype(str)

        # sire_id リストで種牡馬スナップショット取得
        all_sire_ids = (
            horses_df["sire_id"].tolist()
            + horses_df["sire_sire_id"].dropna().tolist()
        )
        all_sire_ids = [s for s in set(all_sire_ids) if s and s != "None"]

        sire_stats_df = _load_sire_stats_bulk(all_sire_ids, target_date, conn)
        if sire_stats_df.empty:
            logger.warning("sire_feature_store からデータが取得できませんでした (target_date=%s)", target_date)
            return df
        sire_stats_df["sire_id"] = sire_stats_df["sire_id"].astype(str)
        sire_stats_index = sire_stats_df.set_index("sire_id")

    # horse_id → sire_id / sire_sire_id のマップ
    horses_map = horses_df.set_index("horse_id").to_dict("index")
    meta_map = meta_df.set_index("horse_id").to_dict("index") if not meta_df.empty else {}

    # 各行にスコアを付与
    result_rows = []
    for idx, row in df.iterrows():
        horse_id = str(row.get("horse_id", ""))
        hinfo = horses_map.get(horse_id, {})
        sire_id = hinfo.get("sire_id", "")
        sire_sire_id = hinfo.get("sire_sire_id", "")

        # sex_cd / horse_age (DBから取得、なければキャッシュ列)
        hmeta = meta_map.get(horse_id, {})
        row_with_meta = row.copy()
        if "sex_cd" not in row or pd.isna(row.get("sex_cd")):
            row_with_meta["sex_cd"] = hmeta.get("sex_cd")
        if "horse_age" not in row or pd.isna(row.get("horse_age")):
            row_with_meta["horse_age"] = hmeta.get("horse_age")

        used_fallback = 0
        scores: dict[str, float] = {}

        # ── 父のスナップショット参照 ──────────────────────────────────
        if sire_id and sire_id in sire_stats_index.index:
            stats = sire_stats_index.loc[sire_id]
            if isinstance(stats, pd.DataFrame):
                stats = stats.iloc[0]
            # total_count が N_MIN 以上なら使用
            if pd.notna(stats.get("total_count")) and stats["total_count"] >= N_MIN_SIRE:
                scores = _compute_row_scores(row_with_meta, stats)
            else:
                # 父のサンプルが少ない → 父父フォールバック
                used_fallback = 1

        else:
            # sire_id が取れない → 父父フォールバック
            used_fallback = 1

        # ── 父父フォールバック ────────────────────────────────────────
        if used_fallback and sire_sire_id and sire_sire_id in sire_stats_index.index:
            stats_ss = sire_stats_index.loc[sire_sire_id]
            if isinstance(stats_ss, pd.DataFrame):
                stats_ss = stats_ss.iloc[0]
            if pd.notna(stats_ss.get("total_count")) and stats_ss["total_count"] >= N_MIN_SIRE:
                scores = _compute_row_scores(row_with_meta, stats_ss)

        result_rows.append({**scores, "fallback_to_sire_sire": used_fallback})

    scores_df = pd.DataFrame(result_rows, index=df.index)
    for col in SIRE_APTITUDE_COLS:
        if col in scores_df.columns:
            df[col] = scores_df[col]

    coverage = df[["sire_course_fit", "sire_dist_fit", "sire_venue_fit"]].notna().mean()
    logger.info(
        "血統適性カバー率: course=%.1f%% dist=%.1f%% venue=%.1f%%",
        coverage["sire_course_fit"] * 100,
        coverage["sire_dist_fit"] * 100,
        coverage["sire_venue_fit"] * 100,
    )

    return df
