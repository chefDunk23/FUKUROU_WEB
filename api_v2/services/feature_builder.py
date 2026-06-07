"""
api_v2/services/feature_builder.py
=====================================
血統系統特徴量エンジニアリング（lineage target encoding）。

import_bloodline_masters.py で構築した hanshoku_ma_master / lineage_info と
build_lineage_stats_store() で構築した lineage_stats_store を参照し、
以下の 28 列を生成する（sire_ / bms_ それぞれ 14 列）。

    ─── スピード/スタミナ ──────────────────────────────────────────────────
    {p}_line_code           系統コード（後段特徴量の JOIN キー）
    {p}_line_avg_win_dist   系統産駒の平均勝利距離
    {p}_line_sprint_wr      スプリント（≤1400m）勝率
    {p}_line_sprint_top3r   スプリント複勝率
    {p}_line_mile_wr        マイル（1401-1800m）勝率
    {p}_line_long_wr        長距離（>2200m）勝率
    ─── 早熟/晩成 ──────────────────────────────────────────────────────────
    {p}_line_age2_wr        2歳戦勝率
    {p}_line_age3_wr        3歳戦勝率
    {p}_line_age4plus_wr    4歳以上勝率
    {p}_line_maturity       晩成指数（age4plus_wr / age2_wr, >1=晩成）
    ─── 性別×ダート / 馬格乖離 ─────────────────────────────────────────────
    {p}_line_male_dirt_wr   牡馬ダート勝率
    {p}_line_female_dirt_wr 牝馬ダート勝率
    {p}_line_sex_dirt_bias  性別ダートバイアス（牡-牝 勝率差）
    {p}_line_weight_gap     系統産駒平均馬体重と現馬馬体重の差（+=軽い）

前提:
    import_bloodline_masters.py の実行完了
    build_lineage_stats_store() の事前実行（初回のみ、その後週次更新推奨）
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import psycopg2.extras

log = logging.getLogger(__name__)

# ── 生成カラム定義 ────────────────────────────────────────────────────────────

_STAT_SUFFIXES = (
    "line_avg_win_dist",
    "line_sprint_wr",
    "line_sprint_top3r",
    "line_mile_wr",
    "line_long_wr",
    "line_age2_wr",
    "line_age3_wr",
    "line_age4plus_wr",
    "line_maturity",
    "line_male_dirt_wr",
    "line_female_dirt_wr",
    "line_sex_dirt_bias",
    "line_weight_gap",
)

LINEAGE_COLS: list[str] = (
    ["sire_line_code", "bms_line_code"]
    + [f"sire_{s}" for s in _STAT_SUFFIXES]
    + [f"bms_{s}"  for s in _STAT_SUFFIXES]
)

# ── lineage_stats_store テーブル定義 ─────────────────────────────────────────

_DDL_STATS_STORE = """
CREATE TABLE IF NOT EXISTS lineage_stats_store (
    line_code            CHAR(4)  PRIMARY KEY,
    sample_count         INTEGER  NOT NULL DEFAULT 0,
    avg_win_dist         REAL,
    sprint_win_rate      REAL,
    sprint_top3_rate     REAL,
    mile_win_rate        REAL,
    long_win_rate        REAL,
    age2_win_rate        REAL,
    age3_win_rate        REAL,
    age4plus_win_rate    REAL,
    male_dirt_win_rate   REAL,
    female_dirt_win_rate REAL,
    avg_weight           REAL,
    updated_at           TIMESTAMP DEFAULT NOW()
);
"""

# ── 統計ビルド SQL ────────────────────────────────────────────────────────────
# fukurou_jvdl の race_entries / races / horses と
# import_bloodline_masters が作った lineage_info を結合して集計する。
# 馬年齢は racing 時の年 - 誕生年（JRA 基準の簡易計算）。
# ダート判定: races.course_type = 'ダート'
# 性別: horses.sex ('1'=牡, '2'=牝)

_SQL_BUILD_STATS = """
WITH lineage_map AS (
    SELECT DISTINCT ON (h.id)
        h.id         AS horse_id,
        h.sex,
        h.birthday,
        li.line_code
    FROM horses h
    JOIN lineage_info li ON li.breed_id = h.sire_id
    WHERE h.sire_id IS NOT NULL
    ORDER BY h.id, li.line_code
),
entries AS (
    SELECT
        lm.line_code,
        lm.sex,
        e.confirmed_rank                                      AS rank,
        CAST(r.distance AS INTEGER)                           AS distance,
        r.course_type,
        NULLIF(e.horse_weight, 0)                             AS horse_weight,
        CASE
            WHEN lm.birthday IS NOT NULL
            THEN DATE_PART('year', r.date::date)
                 - DATE_PART('year', lm.birthday::date)
            ELSE NULL
        END::INTEGER                                          AS age_years
    FROM race_entries e
    JOIN races r        ON r.id       = e.race_id
    JOIN lineage_map lm ON lm.horse_id = e.horse_id
    WHERE e.confirmed_rank IS NOT NULL
      AND e.confirmed_rank > 0
)
SELECT
    line_code,
    COUNT(*)                                                         AS sample_count,
    ROUND(AVG(distance) FILTER (WHERE rank = 1)::NUMERIC, 0)         AS avg_win_dist,
    ROUND(
        COUNT(*) FILTER (WHERE rank = 1 AND distance <= 1400)::NUMERIC
        / NULLIF(COUNT(*) FILTER (WHERE distance <= 1400), 0), 4)    AS sprint_win_rate,
    ROUND(
        COUNT(*) FILTER (WHERE rank <= 3 AND distance <= 1400)::NUMERIC
        / NULLIF(COUNT(*) FILTER (WHERE distance <= 1400), 0), 4)    AS sprint_top3_rate,
    ROUND(
        COUNT(*) FILTER (WHERE rank = 1 AND distance BETWEEN 1401 AND 1800)::NUMERIC
        / NULLIF(COUNT(*) FILTER (WHERE distance BETWEEN 1401 AND 1800), 0), 4) AS mile_win_rate,
    ROUND(
        COUNT(*) FILTER (WHERE rank = 1 AND distance > 2200)::NUMERIC
        / NULLIF(COUNT(*) FILTER (WHERE distance > 2200), 0), 4)     AS long_win_rate,
    ROUND(
        COUNT(*) FILTER (WHERE rank = 1 AND age_years = 2)::NUMERIC
        / NULLIF(COUNT(*) FILTER (WHERE age_years = 2), 0), 4)       AS age2_win_rate,
    ROUND(
        COUNT(*) FILTER (WHERE rank = 1 AND age_years = 3)::NUMERIC
        / NULLIF(COUNT(*) FILTER (WHERE age_years = 3), 0), 4)       AS age3_win_rate,
    ROUND(
        COUNT(*) FILTER (WHERE rank = 1 AND age_years >= 4)::NUMERIC
        / NULLIF(COUNT(*) FILTER (WHERE age_years >= 4), 0), 4)      AS age4plus_win_rate,
    ROUND(
        COUNT(*) FILTER (WHERE rank = 1 AND sex = '1' AND course_type = 'ダート')::NUMERIC
        / NULLIF(COUNT(*) FILTER (WHERE sex = '1' AND course_type = 'ダート'), 0), 4)
                                                                      AS male_dirt_win_rate,
    ROUND(
        COUNT(*) FILTER (WHERE rank = 1 AND sex = '2' AND course_type = 'ダート')::NUMERIC
        / NULLIF(COUNT(*) FILTER (WHERE sex = '2' AND course_type = 'ダート'), 0), 4)
                                                                      AS female_dirt_win_rate,
    ROUND(AVG(horse_weight) FILTER (WHERE horse_weight IS NOT NULL), 1) AS avg_weight
FROM entries
GROUP BY line_code
HAVING COUNT(*) >= 10
"""

# ── 統計ストアのビルド（オフライン実行） ──────────────────────────────────────

def build_lineage_stats_store(conn) -> int:
    """
    fukurou_jvdl の race_entries から系統別ターゲット統計を集計し
    lineage_stats_store へ UPSERT する。

    Returns:
        書き込んだ行数（系統コードのユニーク数）
    """
    with conn.cursor() as cur:
        cur.execute(_DDL_STATS_STORE)
        conn.commit()

        cur.execute(_SQL_BUILD_STATS)
        rows = cur.fetchall()

    if not rows:
        log.warning("[lineage_stats] 集計結果ゼロ — lineage_info が空の可能性があります")
        return 0

    cols = [desc[0] for desc in cur.description]
    records = [dict(zip(cols, r)) for r in rows]

    with conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO lineage_stats_store
                (line_code, sample_count, avg_win_dist,
                 sprint_win_rate, sprint_top3_rate, mile_win_rate, long_win_rate,
                 age2_win_rate, age3_win_rate, age4plus_win_rate,
                 male_dirt_win_rate, female_dirt_win_rate, avg_weight, updated_at)
            VALUES %s
            ON CONFLICT (line_code) DO UPDATE SET
                sample_count         = EXCLUDED.sample_count,
                avg_win_dist         = EXCLUDED.avg_win_dist,
                sprint_win_rate      = EXCLUDED.sprint_win_rate,
                sprint_top3_rate     = EXCLUDED.sprint_top3_rate,
                mile_win_rate        = EXCLUDED.mile_win_rate,
                long_win_rate        = EXCLUDED.long_win_rate,
                age2_win_rate        = EXCLUDED.age2_win_rate,
                age3_win_rate        = EXCLUDED.age3_win_rate,
                age4plus_win_rate    = EXCLUDED.age4plus_win_rate,
                male_dirt_win_rate   = EXCLUDED.male_dirt_win_rate,
                female_dirt_win_rate = EXCLUDED.female_dirt_win_rate,
                avg_weight           = EXCLUDED.avg_weight,
                updated_at           = NOW()
            """,
            [
                (r["line_code"], r["sample_count"], r["avg_win_dist"],
                 r["sprint_win_rate"], r["sprint_top3_rate"],
                 r["mile_win_rate"], r["long_win_rate"],
                 r["age2_win_rate"], r["age3_win_rate"], r["age4plus_win_rate"],
                 r["male_dirt_win_rate"], r["female_dirt_win_rate"],
                 r["avg_weight"], "NOW()")
                for r in records
            ],
        )
    conn.commit()

    log.info("[lineage_stats] %d 系統コードの統計を更新しました", len(records))
    return len(records)


# ── リアルタイム用ルックアップ ────────────────────────────────────────────────

def fetch_lineage_context(
    conn,
    sire_ids: list[str],
    bms_ids: list[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    hanshoku_ma_master / lineage_info / lineage_stats_store から
    父・母父に対応する系統コードと統計を取得する。

    Args:
        conn     : fukurou_jvdl psycopg2 接続
        sire_ids : 父の繁殖登録番号リスト
        bms_ids  : 母父の繁殖登録番号リスト

    Returns:
        line_code_df : columns=[breed_id, line_code]
        stats_df     : line_code をインデックスとした統計 DataFrame
    """
    all_ids = list({s for s in sire_ids + bms_ids if s and str(s).strip()})
    if not all_ids:
        return pd.DataFrame(columns=["breed_id", "line_code"]), pd.DataFrame()

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT breed_id, line_code
                FROM   lineage_info
                WHERE  breed_id = ANY(%s)
                """,
                (all_ids,),
            )
            lc_rows = cur.fetchall()

        if not lc_rows:
            return pd.DataFrame(columns=["breed_id", "line_code"]), pd.DataFrame()

        line_codes = list({r["line_code"] for r in lc_rows if r["line_code"]})

        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM lineage_stats_store WHERE line_code = ANY(%s)",
                (line_codes,),
            )
            stat_rows = cur.fetchall()

    except Exception as exc:
        log.warning("[lineage_ctx] DB lookup 失敗: %s", exc)
        return pd.DataFrame(columns=["breed_id", "line_code"]), pd.DataFrame()

    line_code_df = (
        pd.DataFrame(lc_rows)
        .drop_duplicates("breed_id")         # 1馬→複数系統がある場合は先頭を使用
    )
    stats_df = (
        pd.DataFrame(stat_rows).set_index("line_code")
        if stat_rows else pd.DataFrame()
    )
    return line_code_df, stats_df


# ── 特徴量エンジニアリング（純粋変換） ───────────────────────────────────────

def create_lineage_features(
    df: pd.DataFrame,
    line_code_df: pd.DataFrame,
    stats_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    系統コードと統計ストアから血統系統特徴量 LINEAGE_COLS を生成する。

    Args:
        df           : race_entries DataFrame（sire_id, bms_id, horse_weight 列必須）
        line_code_df : fetch_lineage_context() の第1戻り値
        stats_df     : fetch_lineage_context() の第2戻り値

    Returns:
        LINEAGE_COLS を追加した新しい DataFrame（元 df を変更しない）
    """
    df = df.copy()
    for col in LINEAGE_COLS:
        df[col] = np.nan

    if line_code_df.empty or stats_df.empty:
        return df

    # breed_id → line_code の逆引きマップ
    code_lookup: pd.Series = (
        line_code_df
        .dropna(subset=["line_code"])
        .set_index("breed_id")["line_code"]
    )

    df["sire_line_code"] = (
        df["sire_id"].astype(str).map(code_lookup)
        if "sire_id" in df.columns else np.nan
    )
    df["bms_line_code"] = (
        df["bms_id"].astype(str).map(code_lookup)
        if "bms_id" in df.columns else np.nan
    )

    horse_weight = pd.to_numeric(
        df.get("horse_weight", pd.Series(np.nan, index=df.index)),
        errors="coerce",
    )

    def _attach_stats(line_code_col: str, prefix: str) -> None:
        codes: pd.Series = df[line_code_col]

        stat_map: dict[str, str] = {
            f"{prefix}_line_avg_win_dist":    "avg_win_dist",
            f"{prefix}_line_sprint_wr":       "sprint_win_rate",
            f"{prefix}_line_sprint_top3r":    "sprint_top3_rate",
            f"{prefix}_line_mile_wr":         "mile_win_rate",
            f"{prefix}_line_long_wr":         "long_win_rate",
            f"{prefix}_line_age2_wr":         "age2_win_rate",
            f"{prefix}_line_age3_wr":         "age3_win_rate",
            f"{prefix}_line_age4plus_wr":     "age4plus_win_rate",
            f"{prefix}_line_male_dirt_wr":    "male_dirt_win_rate",
            f"{prefix}_line_female_dirt_wr":  "female_dirt_win_rate",
            f"{prefix}_line_avg_weight_raw":  "avg_weight",  # 中間列
        }
        for out_col, stat_col in stat_map.items():
            if stat_col in stats_df.columns:
                df[out_col] = codes.map(stats_df[stat_col])

        # 晩成指数: age4plus_wr / age2_wr（age2 が 0.01 未満なら NaN）
        age2   = pd.to_numeric(df.get(f"{prefix}_line_age2_wr"),     errors="coerce")
        age4p  = pd.to_numeric(df.get(f"{prefix}_line_age4plus_wr"), errors="coerce")
        df[f"{prefix}_line_maturity"] = (age4p / age2.clip(lower=0.01)).where(age2.notna())

        # 性別ダートバイアス: 牡勝率 - 牝勝率
        male_d   = pd.to_numeric(df.get(f"{prefix}_line_male_dirt_wr"),   errors="coerce")
        female_d = pd.to_numeric(df.get(f"{prefix}_line_female_dirt_wr"), errors="coerce")
        df[f"{prefix}_line_sex_dirt_bias"] = (male_d - female_d)

        # 馬格乖離: 系統産駒平均体重 - 現馬体重（正=現馬が軽い）
        avg_w_raw = f"{prefix}_line_avg_weight_raw"
        if avg_w_raw in df.columns:
            avg_w = pd.to_numeric(df[avg_w_raw], errors="coerce")
            df[f"{prefix}_line_weight_gap"] = avg_w - horse_weight
            df.drop(columns=[avg_w_raw], inplace=True)

    _attach_stats("sire_line_code", "sire")
    _attach_stats("bms_line_code",  "bms")

    return df
