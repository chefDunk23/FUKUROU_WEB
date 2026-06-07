"""
src/features/pedigree_features_v1.py
=====================================
pedigree_v1 サブモデル向け血統特徴量エンジニアリング。

父（sire）と母父（BMS）の成績統計（sire_feature_store）から
現在のレース条件・個体差に合わせたコンテキスト特徴量を 22 列生成する。

必須カラム（呼び出し前に enrich_pedigree_v1.py でプリジョイン済みであること）:
    horse_id, race_date, distance, track_code, keibajo_code
    horse_weight                             (個体馬体重)
    horse_age                                (レース時の年齢、float)
    horse_sex                                (性別コード: '1'=牡 '2'=牝 '3'=騸)
    shiba_baba_code / dirt_baba_code         (馬場状態: 1=良 2=稍重 3=重 4=不良)
    sire_win_rate, sire_top3_rate, sire_total_count
    sire_surface_turf_win_rate, sire_surface_turf_top3_rate
    sire_surface_dirt_win_rate, sire_surface_dirt_top3_rate
    sire_dist_sprint_win_rate ... sire_dist_long_win_rate
    sire_venue_01_win_rate ... sire_venue_10_win_rate
    sire_baba_firm_win_rate, sire_baba_yaya_win_rate
    sire_baba_omo_win_rate, sire_baba_furyo_win_rate
    sire_age2_win_rate, sire_age3_win_rate
    sire_age4_win_rate, sire_age5plus_win_rate
    sire_sex_male_win_rate, sire_sex_female_win_rate
    sire_avg_all_weight
    bms_* (同上)

生成特徴量 (24列):
    ─── 基本 ───────────────────────────────────────────────────────────
    sire_total_win_rate    父 総合勝率
    sire_total_top3_rate   父 総合複勝率
    sire_count             父 産駒出走数（サンプルサイズ）
    bms_total_win_rate     母父 総合勝率
    bms_total_top3_rate    母父 総合複勝率
    bms_count              母父 産駒出走数
    ─── 適性判定（コンテキスト適応）────────────────────────────────────
    sire_surface_win_rate  父 馬場面別勝率（芝/ダート）
    sire_surface_top3_rate 父 馬場面別複勝率
    sire_dist_win_rate     父 距離区分別勝率
    sire_venue_win_rate    父 競馬場別勝率
    bms_surface_win_rate   母父 馬場面別勝率
    bms_surface_top3_rate  母父 馬場面別複勝率
    bms_dist_win_rate      母父 距離区分別勝率
    bms_venue_win_rate     母父 競馬場別勝率
    ─── 道悪適性 ────────────────────────────────────────────────────────
    sire_heavy_win_rate    父 重・不良馬場での勝率
    bms_heavy_win_rate     母父 重・不良馬場での勝率
    ─── 成長曲線 ────────────────────────────────────────────────────────
    sire_age_win_rate      父 産駒の現年齢帯での勝率
    bms_age_win_rate       母父 産駒の現年齢帯での勝率
    sire_growth_factor     父 晩成指数（高いほど晩成傾向）
    bms_growth_factor      母父 晩成指数
    ─── 性別・馬体重クロス ──────────────────────────────────────────────
    sire_sex_win_rate      父 産駒の現馬の性別別勝率
    bms_sex_win_rate       母父 産駒の現馬の性別別勝率
    sire_weight_gap        産駒平均馬体重と現馬の馬体重の差（+= 現馬が軽い）
    bms_weight_gap         同母父版
"""
from __future__ import annotations

import numpy as np
import pandas as pd

PEDIGREE_V1_COLS: list[str] = [
    # 基本
    "sire_total_win_rate",
    "sire_total_top3_rate",
    "sire_count",
    "bms_total_win_rate",
    "bms_total_top3_rate",
    "bms_count",
    # 適性判定（コンテキスト適応）
    "sire_surface_win_rate",
    "sire_surface_top3_rate",
    "sire_dist_win_rate",
    "sire_venue_win_rate",
    "bms_surface_win_rate",
    "bms_surface_top3_rate",
    "bms_dist_win_rate",
    "bms_venue_win_rate",
    # 道悪適性
    "sire_heavy_win_rate",
    "bms_heavy_win_rate",
    # 成長曲線
    "sire_age_win_rate",
    "bms_age_win_rate",
    "sire_growth_factor",
    "bms_growth_factor",
    # 性別・馬体重クロス
    "sire_sex_win_rate",
    "bms_sex_win_rate",
    "sire_weight_gap",
    "bms_weight_gap",
]

# 距離区分境界 (sire_feature_store の dist_<cat>_win_rate と一致)
_DIST_BOUNDARIES = [
    ("sprint", 0,    1400),
    ("mile",   1401, 1800),
    ("middle", 1801, 2200),
    ("long",   2201, 99999),
]


def _surface_key(track_code: str) -> str:
    """JV-Data track_code から血統フィーチャーキーを返す。
    芝: 10-22 → "turf" / ダート: 23-29 + 障害: 51-59 → "dirt"
    """
    try:
        t = int(float(str(track_code).strip()))
    except (TypeError, ValueError):
        return "turf"
    return "dirt" if (23 <= t <= 29 or 51 <= t <= 59) else "turf"


def _dist_cat(distance: int) -> str:
    for cat, lo, hi in _DIST_BOUNDARIES:
        if lo <= distance <= hi:
            return cat
    return "long"


def _age_cat(age: float) -> str:
    if age < 2.5:
        return "age2"
    if age < 3.5:
        return "age3"
    if age < 4.5:
        return "age4"
    return "age5plus"


# ── 安全カラム取得ヘルパー ─────────────────────────────────────────────────────

def _col_or_default(df: pd.DataFrame, col: str, default) -> pd.Series:
    """列が存在しない / スカラーの場合でも必ず Series を返す。"""
    val = df.get(col)
    if val is None or np.isscalar(val):
        return pd.Series(default, index=df.index)
    return pd.Series(val, index=df.index)


def _safe_col_vals(df: pd.DataFrame, col: str) -> np.ndarray:
    """列が存在しない場合は NaN 配列を返す。df.get() が None/スカラーでも安全。"""
    val = df.get(col)
    if val is None or np.isscalar(val):
        return np.full(len(df), np.nan)
    return pd.to_numeric(val, errors="coerce").values


def _safe_col_series(df: pd.DataFrame, col: str) -> pd.Series:
    """列が存在しない場合は NaN Series を返す。"""
    val = df.get(col)
    if val is None or np.isscalar(val):
        return pd.Series(np.nan, index=df.index)
    return pd.to_numeric(val, errors="coerce")


# ── コンテキスト特徴量ピッカー ────────────────────────────────────────────────

def _pick_surface(
    df: pd.DataFrame,
    prefix: str,
    surface_series: pd.Series,
    stat: str,  # "win_rate" or "top3_rate"
) -> pd.Series:
    """芝/ダートに応じた surface_<stat> を選択。"""
    turf_vals = _safe_col_vals(df, f"{prefix}_surface_turf_{stat}")
    dirt_vals = _safe_col_vals(df, f"{prefix}_surface_dirt_{stat}")
    result = np.where(surface_series.values == "dirt", dirt_vals, turf_vals)
    return pd.Series(result, index=df.index)


def _pick_dist(df: pd.DataFrame, prefix: str, dist_series: pd.Series) -> pd.Series:
    n = len(df)
    dist_wr = np.full(n, np.nan)
    for cat, *_ in _DIST_BOUNDARIES:
        mask = dist_series.values == cat
        if mask.any():
            vals = _safe_col_vals(df, f"{prefix}_dist_{cat}_win_rate")
            dist_wr[mask] = vals[mask]
    return pd.Series(dist_wr, index=df.index)


def _pick_venue(df: pd.DataFrame, prefix: str, venue_series: pd.Series) -> pd.Series:
    n = len(df)
    venue_wr = np.full(n, np.nan)
    for v in [f"{i:02d}" for i in range(1, 11)]:
        mask = venue_series.values == v
        if mask.any():
            vals = _safe_col_vals(df, f"{prefix}_venue_{v}_win_rate")
            venue_wr[mask] = vals[mask]
    return pd.Series(venue_wr, index=df.index)


def _pick_heavy(
    df: pd.DataFrame,
    prefix: str,
    track_code_series: pd.Series,
    shiba_baba_series: pd.Series,
    dirt_baba_series: pd.Series,
) -> pd.Series:
    """
    道悪適性: 馬場状態コード (3=重, 4=不良) の時に対応する win_rate を返す。
    それ以外は baba_firm or baba_yaya の win_rate。
    """
    is_dirt = track_code_series.map(_surface_key) == "dirt"
    sb_vals = pd.to_numeric(shiba_baba_series, errors="coerce").fillna(1).values
    db_vals = pd.to_numeric(dirt_baba_series,  errors="coerce").fillna(1).values
    baba_code = np.where(is_dirt, db_vals, sb_vals)
    baba_code = pd.to_numeric(
        pd.Series(baba_code, index=df.index), errors="coerce"
    ).fillna(1).astype(int)

    firm_wr  = _safe_col_vals(df, f"{prefix}_baba_firm_win_rate")
    yaya_wr  = _safe_col_vals(df, f"{prefix}_baba_yaya_win_rate")
    omo_wr   = _safe_col_vals(df, f"{prefix}_baba_omo_win_rate")
    furyo_wr = _safe_col_vals(df, f"{prefix}_baba_furyo_win_rate")

    bc = baba_code.values
    result = np.select(
        [bc == 1, bc == 2, bc == 3, bc == 4],
        [firm_wr,  yaya_wr,  omo_wr,   furyo_wr],
        default=np.nan,
    )
    return pd.Series(result, index=df.index)


def _pick_age_win_rate(df: pd.DataFrame, prefix: str, age_series: pd.Series) -> pd.Series:
    """現馬の年齢帯に応じた産駒勝率を返す。"""
    n = len(df)
    result = np.full(n, np.nan)
    for cat in ("age2", "age3", "age4", "age5plus"):
        mask = age_series.values == cat
        if mask.any():
            vals = _safe_col_vals(df, f"{prefix}_{cat}_win_rate")
            result[mask] = vals[mask]
    return pd.Series(result, index=df.index)


def _growth_factor(df: pd.DataFrame, prefix: str) -> pd.Series:
    """
    晩成指数 = age4plus勝率 / age2勝率
    > 1 → 成長型（晩成）, < 1 → 早熟型
    """
    age2_wr     = _safe_col_series(df, f"{prefix}_age2_win_rate")
    age4_wr     = _safe_col_series(df, f"{prefix}_age4_win_rate")
    age5plus_wr = _safe_col_series(df, f"{prefix}_age5plus_win_rate")
    peak_wr = (age4_wr.fillna(0) + age5plus_wr.fillna(0)) / 2
    denom = age2_wr.clip(lower=0.01)
    return (peak_wr / denom).where(age2_wr.notna())


def _pick_sex_win_rate(df: pd.DataFrame, prefix: str, sex_series: pd.Series) -> pd.Series:
    """
    現馬の性別に応じた産駒勝率。
    sex: '1'=牡馬 → sex_male_win_rate
         '2'=牝馬 → sex_female_win_rate
         '3'=騸馬 → sex_male_win_rate（去勢前は牡として扱う）
    """
    male_wr   = _safe_col_vals(df, f"{prefix}_sex_male_win_rate")
    female_wr = _safe_col_vals(df, f"{prefix}_sex_female_win_rate")
    is_female = sex_series.astype(str).values == "2"
    result = np.where(is_female, female_wr, male_wr)
    return pd.Series(result, index=df.index)


def _weight_gap(df: pd.DataFrame, prefix: str) -> pd.Series:
    """
    馬格の遺伝度合い = 産駒の平均馬体重 - 現馬の馬体重
    正 → 現馬は産駒平均より軽い（線が細い）
    負 → 現馬は産駒平均より重い（馬格あり）
    """
    avg_w = _safe_col_series(df, f"{prefix}_avg_all_weight")
    cur_w = _safe_col_series(df, "horse_weight")
    return avg_w - cur_w


def create_pedigree_features_v1(df: pd.DataFrame) -> pd.DataFrame:
    """
    父・母父の血統特徴量 24 列を生成する。

    df には enrich_pedigree_v1.py によって sire_*/bms_* の中間列と
    horse_age / horse_sex が付与されていること。
    列が欠損している場合はデフォルト値で補完する。
    """
    df = df.copy()
    distance   = pd.to_numeric(df["distance"], errors="coerce").fillna(0).astype(int)
    track_code = df["track_code"].astype(str).str.strip()
    keibajo    = df["keibajo_code"].astype(str).str.zfill(2)
    shiba_baba = _col_or_default(df, "shiba_baba_code", 1)
    dirt_baba  = _col_or_default(df, "dirt_baba_code",  1)
    horse_age  = pd.to_numeric(_col_or_default(df, "horse_age", 3.0), errors="coerce").fillna(3.0)
    horse_sex  = _col_or_default(df, "horse_sex", "1").astype(str)

    surface_ser = track_code.map(_surface_key)
    dist_ser    = distance.map(_dist_cat)
    age_ser     = horse_age.map(_age_cat)

    for prefix in ("sire", "bms"):
        p = prefix

        # ── 基本 ──────────────────────────────────────────────────────────────
        df[f"{p}_total_win_rate"]  = _safe_col_series(df, f"{p}_win_rate")
        df[f"{p}_total_top3_rate"] = _safe_col_series(df, f"{p}_top3_rate")
        df[f"{p}_count"]           = _safe_col_series(df, f"{p}_total_count")

        # ── 適性判定 ──────────────────────────────────────────────────────────
        df[f"{p}_surface_win_rate"]  = _pick_surface(df, p, surface_ser, "win_rate")
        df[f"{p}_surface_top3_rate"] = _pick_surface(df, p, surface_ser, "top3_rate")
        df[f"{p}_dist_win_rate"]     = _pick_dist(df, p, dist_ser)
        df[f"{p}_venue_win_rate"]    = _pick_venue(df, p, keibajo)

        # ── 道悪適性 ──────────────────────────────────────────────────────────
        df[f"{p}_heavy_win_rate"] = _pick_heavy(
            df, p, track_code, shiba_baba, dirt_baba
        )

        # ── 成長曲線 ──────────────────────────────────────────────────────────
        df[f"{p}_age_win_rate"]   = _pick_age_win_rate(df, p, age_ser)
        df[f"{p}_growth_factor"]  = _growth_factor(df, p)

        # ── 性別・馬体重クロス ────────────────────────────────────────────────
        df[f"{p}_sex_win_rate"] = _pick_sex_win_rate(df, p, horse_sex)
        df[f"{p}_weight_gap"]   = _weight_gap(df, p)

    return df
