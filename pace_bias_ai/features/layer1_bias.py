"""
pace_bias_ai/features/layer1_bias.py
======================================
第1層（数値化）: トラックバイアス特徴量

競馬場固有特徴（1階）と当日/前日バイアス（2階）を数値化する。

既存実装との関係:
    - `course_profile_store` → 競馬場×コース×距離の枠/脚質バイアス（既存）
    - `track_bias_pit`       → 直近同コース実測バイアス（既存）
    - `_compute_track_bias`  → races.py の推定関数（既存）
    → 本モジュールはこれらを「展開×バイアス エンジン」用に整理・拡張する

新規実装:
    compute_venue_bias_features()   競馬場固有特徴（course_profile_store から）
    compute_day_bias_features()     当日バイアス（track_bias_pit / 当日レース結果から）
    compute_prev_week_bias()        前日（前週同曜日）バイアス推定

出力特徴量:
    venue_front_bias        競馬場×コース固有の前残り傾向 (+= 前残り有利)
    venue_inner_bias        競馬場×コース固有の内枠有利度 (+= 内枠有利)
    venue_agari_top2_rate   競馬場×コース: 上がり1〜2番手が勝つ割合
    day_front_bias_pit      当日（直近レース）の前残り度 (track_bias_pit)
    day_inner_bias_pit      当日の内枠バイアス
    opening_week_prior      開幕週先行・内枠有利の事前確率 (0/1 フラグ)
    prev_week_front_bias    前週同曜日の前残り傾向（前日バイアス推定用）
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# ── 出力カラム名 ──────────────────────────────────────────────────────────────
BIAS_FEATURE_COLS: list[str] = [
    "venue_front_bias",
    "venue_inner_bias",
    "venue_agari_top2_rate",
    "day_front_bias_pit",
    "day_inner_bias_pit",
    "opening_week_prior",
    "prev_week_front_bias",
]

# ── デフォルト値（データなし時のフォールバック）────────────────────────────────
_DEFAULT_FRONT_BIAS = 0.0   # 中立
_DEFAULT_INNER_BIAS = 0.0   # 中立
_DEFAULT_AGARI_TOP2 = 0.4   # JRA 平均的な割合
_DEFAULT_PREV_WEEK  = 0.0   # 不明 → 中立

# ── 競馬場・コースから「先行有利傾向」を推定するマスタ ──────────────────────────
# JRA の標準直線距離をもとにした先行有利スコア (−1=差し有利, +1=前残り有利)
# ソース: course_features_v3.py の _COURSE_MASTER に準拠
_VENUE_FRONT_PRIOR: dict[str, float] = {
    "01": +0.3,   # 札幌: 小回り → やや前有利
    "02": +0.3,   # 函館: 小回り → やや前有利
    "03": +0.2,   # 福島: 小回り・急坂
    "04":  0.0,   # 新潟: 長い直線（外回り）→ 中立
    "05": -0.1,   # 東京: 最長直線 → やや差し有利
    "06": +0.2,   # 中山: 小回り・急坂
    "07":  0.0,   # 中京: 長い直線 → 中立
    "08": +0.1,   # 京都: やや前有利（淀の坂）
    "09":  0.0,   # 阪神: 内外で違うが中立と仮定
    "10": +0.2,   # 小倉: 小回り → やや前有利
}


# ─────────────────────────────────────────────────────────────────────────────
# 公開 API
# ─────────────────────────────────────────────────────────────────────────────

def compute_venue_bias_features(
    df: pd.DataFrame,
    conn=None,
) -> pd.DataFrame:
    """競馬場固有特徴（1階）を付与する。

    course_profile_store が利用可能な場合はDBから取得。
    利用不可の場合はマスタ値でフォールバック。

    Args:
        df   : race行（keibajo_code / track_code / distance 必須）
        conn : psycopg2 接続（None の場合はマスタ値フォールバック）

    Returns:
        venue_front_bias / venue_inner_bias / venue_agari_top2_rate を追加した df
    """
    df = df.copy()

    # まずマスタ値でデフォルトを設定
    kc = df["keibajo_code"].astype(str).str.zfill(2) if "keibajo_code" in df.columns else pd.Series("05", index=df.index)
    df["venue_front_bias"]    = kc.map(_VENUE_FRONT_PRIOR).fillna(_DEFAULT_FRONT_BIAS)
    df["venue_inner_bias"]    = _DEFAULT_INNER_BIAS
    df["venue_agari_top2_rate"] = _DEFAULT_AGARI_TOP2

    if conn is None:
        return df

    # course_profile_store から枠バイアス・脚質バイアスを取得
    try:
        df = _enrich_from_course_profile(df, conn)
    except Exception as exc:
        log.warning("[VenueBias] course_profile_store 取得失敗: %s", exc)

    return df


def _enrich_from_course_profile(df: pd.DataFrame, conn) -> pd.DataFrame:
    """course_profile_store から枠/脚質バイアスを取得してマージする。"""
    import psycopg2.extras

    # 対象コースのユニークキーを収集
    if "keibajo_code" not in df.columns or "distance" not in df.columns:
        return df

    surface_map = {"10": "turf", "11": "turf", "12": "turf", "17": "turf",
                   "18": "turf", "23": "dirt", "24": "dirt"}
    if "track_code" in df.columns:
        tc_str = df["track_code"].astype(str).str.zfill(2)
        surfaces = tc_str.map(lambda t: "turf" if t.startswith("1") else "dirt")
    else:
        surfaces = pd.Series("turf", index=df.index)

    df = df.copy()
    df["_surface_tmp"] = surfaces
    df["_kc_tmp"] = df["keibajo_code"].astype(str).str.zfill(2)

    keys = df[["_kc_tmp", "distance", "_surface_tmp"]].drop_duplicates()

    rows_fetched: list[dict] = []
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        for _, row in keys.iterrows():
            cur.execute(
                """
                SELECT place_code, distance, surface,
                       style_nige_win_rate, style_senko_win_rate,
                       style_sashi_win_rate, style_oikomi_win_rate,
                       inner_bracket_top3_shift, outer_bracket_top3_shift,
                       agari_1st_win_rate, agari_2nd_win_rate
                FROM   course_profile_store
                WHERE  place_code = %s AND distance = %s AND surface = %s
                ORDER  BY target_date DESC
                LIMIT  1
                """,
                (row["_kc_tmp"], int(row["distance"]), row["_surface_tmp"]),
            )
            r = cur.fetchone()
            if r:
                rows_fetched.append(dict(r))

    if not rows_fetched:
        df = df.drop(columns=["_surface_tmp", "_kc_tmp"], errors="ignore")
        return df

    profile_df = pd.DataFrame(rows_fetched)
    profile_df["_kc_tmp"] = profile_df["place_code"].astype(str).str.zfill(2)

    # 前残り傾向: 逃げ・先行勝率 - 差し・追込勝率
    nige   = pd.to_numeric(profile_df.get("style_nige_win_rate"),   errors="coerce").fillna(0)
    senko  = pd.to_numeric(profile_df.get("style_senko_win_rate"),  errors="coerce").fillna(0)
    sashi  = pd.to_numeric(profile_df.get("style_sashi_win_rate"),  errors="coerce").fillna(0)
    oikomi = pd.to_numeric(profile_df.get("style_oikomi_win_rate"), errors="coerce").fillna(0)
    profile_df["_front_bias"] = (nige + senko) - (sashi + oikomi)

    # 内枠バイアス: 内枠top3シフト - 外枠top3シフト
    inner_shift = pd.to_numeric(profile_df.get("inner_bracket_top3_shift"), errors="coerce").fillna(0)
    outer_shift = pd.to_numeric(profile_df.get("outer_bracket_top3_shift"), errors="coerce").fillna(0)
    profile_df["_inner_bias"] = inner_shift - outer_shift

    # 上がり1〜2番手勝率
    agari1 = pd.to_numeric(profile_df.get("agari_1st_win_rate"), errors="coerce").fillna(0)
    agari2 = pd.to_numeric(profile_df.get("agari_2nd_win_rate"), errors="coerce").fillna(0)
    profile_df["_agari_top2"] = agari1 + agari2

    # df にマージ
    merge_keys = df[["_kc_tmp", "distance", "_surface_tmp"]].reset_index()
    merged = merge_keys.merge(
        profile_df[["_kc_tmp", "distance", "surface", "_front_bias", "_inner_bias", "_agari_top2"]],
        left_on=["_kc_tmp", "distance", "_surface_tmp"],
        right_on=["_kc_tmp", "distance", "surface"],
        how="left",
    ).set_index("index")

    df["venue_front_bias"]      = merged["_front_bias"].reindex(df.index).fillna(df["venue_front_bias"])
    df["venue_inner_bias"]      = merged["_inner_bias"].reindex(df.index).fillna(_DEFAULT_INNER_BIAS)
    df["venue_agari_top2_rate"] = merged["_agari_top2"].reindex(df.index).fillna(_DEFAULT_AGARI_TOP2)

    df = df.drop(columns=["_surface_tmp", "_kc_tmp"], errors="ignore")
    return df


def compute_day_bias_features(
    df: pd.DataFrame,
    conn=None,
    target_date: "date | None" = None,
) -> pd.DataFrame:
    """当日バイアス（2階）を付与する。

    track_bias_pit テーブルが利用可能な場合は実測値を使用。
    利用不可の場合はデフォルト（中立）を設定。

    Args:
        df          : race行（race_id 必須、keibajo_code / track_code あれば絞り込み）
        conn        : psycopg2 接続
        target_date : 対象日（同日の直前レースのみ参照するため）

    Returns:
        day_front_bias_pit / day_inner_bias_pit / opening_week_prior を追加した df
    """
    df = df.copy()
    df["day_front_bias_pit"] = _DEFAULT_FRONT_BIAS
    df["day_inner_bias_pit"] = _DEFAULT_INNER_BIAS
    df["opening_week_prior"] = 0.0  # デフォルト: 非開幕週

    # 開幕週フラグ（layer1_horse.py の opening_week_flag が既にある場合は流用）
    if "opening_week_flag" in df.columns:
        df["opening_week_prior"] = df["opening_week_flag"].fillna(0.0)

    if conn is None or "race_id" not in df.columns:
        return df

    try:
        df = _enrich_from_track_bias_pit(df, conn)
    except Exception as exc:
        log.warning("[DayBias] track_bias_pit 取得失敗: %s", exc)

    return df


def _enrich_from_track_bias_pit(df: pd.DataFrame, conn) -> pd.DataFrame:
    """track_bias_pit から front_bias_pit / inner_bias_pit を取得してマージ。"""
    import psycopg2.extras

    race_ids = df["race_id"].astype(str).unique().tolist()
    if not race_ids:
        return df

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT race_id, front_bias_pit, inner_bias_pit FROM track_bias_pit WHERE race_id = ANY(%s)",
            (race_ids,),
        )
        rows = cur.fetchall()

    if not rows:
        return df

    bias_df = pd.DataFrame(rows)
    bias_df["race_id"] = bias_df["race_id"].astype(str)

    df = df.copy()
    df["race_id_str"] = df["race_id"].astype(str)
    merged = df.merge(
        bias_df.rename(columns={
            "front_bias_pit": "_fbp",
            "inner_bias_pit": "_ibp",
        }),
        left_on="race_id_str", right_on="race_id",
        how="left", suffixes=("", "_bias"),
    )

    df["day_front_bias_pit"] = merged["_fbp"].fillna(_DEFAULT_FRONT_BIAS).values
    df["day_inner_bias_pit"] = merged["_ibp"].fillna(_DEFAULT_INNER_BIAS).values
    df = df.drop(columns=["race_id_str"], errors="ignore")
    return df


def compute_prev_week_bias(
    keibajo_code: str,
    track_code: str,
    race_date: "date",
    conn,
) -> dict[str, float]:
    """前週同曜日の同コースレース結果から前残り/内外バイアスを推定する。

    Args:
        keibajo_code : 競馬場コード
        track_code   : コースコード
        race_date    : 今週の日付
        conn         : psycopg2 接続

    Returns:
        {"prev_week_front_bias": float, "prev_week_inner_bias": float}
    """
    import psycopg2.extras

    prev_week = race_date - timedelta(days=7)
    prev_week_start = prev_week - timedelta(days=1)
    prev_week_end   = prev_week + timedelta(days=1)

    kc = str(keibajo_code).strip().zfill(2)
    # track_code から surface 判定
    tc = str(track_code).strip().zfill(2)
    surf_cond = "10" if tc.startswith("1") else "20"

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    re.corner_4,
                    re.kakutei_chakujun,
                    re.wakuban,
                    re.kohan_3f,
                    COUNT(*) OVER (PARTITION BY re.race_id) AS field_size
                FROM   race_entries_v2 re
                JOIN   races_v2        rv ON re.race_id = rv.race_id
                WHERE  rv.keibajo_code = %s
                  AND  rv.track_code   LIKE %s
                  AND  to_date(rv.kaisai_year || rv.kaisai_monthday, 'YYYYMMDD')
                       BETWEEN %s AND %s
                  AND  re.kakutei_chakujun > 0
                  AND  re.kakutei_chakujun IS NOT NULL
                """,
                (kc, surf_cond + "%", prev_week_start, prev_week_end),
            )
            rows = cur.fetchall()
    except Exception as exc:
        log.warning("[PrevWeekBias] クエリ失敗: %s", exc)
        return {"prev_week_front_bias": _DEFAULT_PREV_WEEK, "prev_week_inner_bias": _DEFAULT_INNER_BIAS}

    if not rows:
        return {"prev_week_front_bias": _DEFAULT_PREV_WEEK, "prev_week_inner_bias": _DEFAULT_INNER_BIAS}

    sub = pd.DataFrame(rows)
    sub["field_size"] = pd.to_numeric(sub["field_size"], errors="coerce").fillna(1).clip(lower=1)
    sub["c4_norm"]    = (pd.to_numeric(sub["corner_4"], errors="coerce") - 1) / (sub["field_size"] - 1).clip(lower=1)
    sub["rank"]       = pd.to_numeric(sub["kakutei_chakujun"], errors="coerce")
    sub["wakuban"]    = pd.to_numeric(sub["wakuban"], errors="coerce")

    winners = sub[sub["rank"] == 1].copy()
    if winners.empty:
        return {"prev_week_front_bias": _DEFAULT_PREV_WEEK, "prev_week_inner_bias": _DEFAULT_INNER_BIAS}

    # 前残り度: 勝ち馬の c4_norm の平均（小 = 前残り型）
    avg_c4_win = winners["c4_norm"].mean()
    front_bias = -1.0 * (avg_c4_win - 0.5) * 2  # -1〜+1 スケール。0.5=中立

    # 内外バイアス: 勝ち馬の枠番パーセンタイル
    wb_pct = (winners["wakuban"] - 1.0) / (8.0 - 1.0)
    avg_wb_pct = wb_pct.mean()
    inner_bias = -1.0 * (avg_wb_pct - 0.5) * 2  # 内枠勝ちが多いほど+

    return {
        "prev_week_front_bias": float(np.clip(front_bias, -1.0, 1.0)),
        "prev_week_inner_bias": float(np.clip(inner_bias, -1.0, 1.0)),
    }


def attach_prev_week_bias_to_df(
    df: pd.DataFrame,
    conn=None,
) -> pd.DataFrame:
    """DataFrame の各行に前週同曜日バイアスを付与する。

    keibajo_code / track_code / race_date が必要。
    conn が None の場合はデフォルト値で埋める。
    """
    df = df.copy()
    df["prev_week_front_bias"] = _DEFAULT_PREV_WEEK

    if conn is None or not all(c in df.columns for c in ["keibajo_code", "track_code", "race_date"]):
        return df

    # レース単位でユニーク化して1回だけ計算（同じレースIDの全馬に同じ値を付与）
    race_keys = df[["race_id", "keibajo_code", "track_code", "race_date"]].drop_duplicates("race_id")

    bias_cache: dict[str, float] = {}
    for _, row in race_keys.iterrows():
        try:
            rd = pd.Timestamp(row["race_date"]).date()
        except Exception:
            continue
        result = compute_prev_week_bias(
            keibajo_code=str(row["keibajo_code"]),
            track_code=str(row["track_code"]),
            race_date=rd,
            conn=conn,
        )
        bias_cache[str(row["race_id"])] = result["prev_week_front_bias"]

    df["race_id_str"] = df["race_id"].astype(str)
    df["prev_week_front_bias"] = df["race_id_str"].map(bias_cache).fillna(_DEFAULT_PREV_WEEK)
    df = df.drop(columns=["race_id_str"], errors="ignore")
    return df
