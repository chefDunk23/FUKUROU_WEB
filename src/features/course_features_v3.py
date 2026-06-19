"""
src/features/course_features_v3.py
====================================
course_v3: コース物理特性 × レース内容 × 期待値ギャップの3軸複合特徴量。

【Phase 1】 競馬場直接適性（既存）
    apt_venue_starts / apt_venue_win_rate_5 / apt_venue_avg_rank_5 / apt_venue_fukusho_rate_5

【Phase 2】 物理特性別 Expectation Gap + 上がり適性（新規）
    Expectation Gap (EG) = ninki - confirmed_rank（正 = 人気より好走、負 = 凡走）
    eg_flat_avg10  / eg_steep_avg10               …坂カテゴリ別 EG 直近10走平均
    eg_turn_L_avg10 / eg_turn_R_avg10             …回り方向別 EG 直近10走平均
    agari_flat_avg10 / agari_steep_avg10          …坂カテゴリ別 go3f 上がり順位直近10走平均
    eg_steep_minus_flat                           …急坂と平坦のEGギャップ（条件替わり指標）

【Phase 3】 ローテーション条件替わり（既存）
    rot_straight_delta / rot_turn_switch / rot_slope_shift
    rot_distance_delta / rot_is_new_venue

リーク防止方針（最重要）:
    - confirmed_rank・go3f_rank は全て shift(1) 済みの過去走のみ参照。
    - 当該レース（予想対象）の confirmed_rank は NaN としておくこと。

NaN 補完の優先順位:
    1. 同馬の全コース通算 EG／上がり実績
    2. ドメイン定数（EG=0.0 中立, 上がり=中間着順）

必須カラム（Phase 2 追加分）:
    ninki     (int: 人気順位、欠損は 0 で代替)
    go_3f_time (float: ラスト3Fタイム秒、欠損→ go3f_rank=NaN)
    last_straight_hill_flag (int: 0=坂なし/平坦, 1=急坂あり)
    elevation_diff (float: コースの高低差m)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# ── 回り方向マスタ ────────────────────────────────────────────────────────────
# 左回り: 東京(05) / 中京(07) / 新潟(04) のみ。他の全場は右回り。
TURN_DIRECTION: dict[str, str] = {
    "01": "R",  # 札幌
    "02": "R",  # 函館
    "03": "R",  # 福島
    "04": "L",  # 新潟
    "05": "L",  # 東京
    "06": "R",  # 中山
    "07": "L",  # 中京
    "08": "R",  # 京都
    "09": "R",  # 阪神
    "10": "R",  # 小倉
}

# ── コース物理マスタ ──────────────────────────────────────────────────────────
# (keibajo_code, track_code, distance) → (straight_dist, elevation_diff, hill_flag)
# JRA-VAN track_code: "10"=右内芝, "11"=左芝, "12"=左外芝,
#                     "17"=変則芝(京都外/阪神外), "18"=直線芝,
#                     "23"=左ダート, "24"=右ダート
_COURSE_MASTER: dict[tuple[str, str, int], tuple[float, float, int]] = {
    # ── 01 札幌 (右回り・平坦・洋芝) ─────────────────────────────────────────
    ("01","10",1000):(266.1,0.0,0), ("01","10",1200):(266.1,0.0,0),
    ("01","10",1500):(266.1,0.0,0), ("01","10",1800):(266.1,0.0,0),
    ("01","10",2000):(266.1,0.0,0), ("01","10",2600):(266.1,0.0,0),
    ("01","24",1000):(264.3,0.0,0), ("01","24",1700):(264.3,0.0,0),
    ("01","24",2400):(264.3,0.0,0),
    # ── 02 函館 (右回り・高低差3.5m・洋芝) ──────────────────────────────────
    ("02","10",1000):(262.1,3.5,0), ("02","10",1200):(262.1,3.5,0),
    ("02","10",1800):(262.1,3.5,0), ("02","10",2000):(262.1,3.5,0),
    ("02","10",2600):(262.1,3.5,0),
    ("02","24",1000):(260.3,3.5,0), ("02","24",1700):(260.3,3.5,0),
    ("02","24",2400):(260.3,3.5,0),
    # ── 03 福島 (右回り・急坂あり) ────────────────────────────────────────────
    ("03","10",1200):(292.0,1.9,1), ("03","10",1700):(292.0,1.9,1),
    ("03","10",1800):(292.0,1.9,1), ("03","10",2000):(292.0,1.9,1),
    ("03","10",2600):(292.0,1.9,1),
    ("03","24",1150):(295.7,2.1,1), ("03","24",1700):(295.7,2.1,1),
    ("03","24",2400):(295.7,2.1,1),
    # ── 04 新潟 (左回り・平坦 / 外回り日本最長直線) ──────────────────────────
    ("04","18",1000):(1000.0,0.0,0),
    ("04","10",1200):(358.7,0.4,0), ("04","10",1400):(358.7,0.4,0),
    ("04","10",2000):(358.7,0.4,0), ("04","10",2200):(358.7,0.4,0),
    ("04","10",2400):(358.7,0.4,0),
    ("04","12",1600):(658.7,0.0,0), ("04","12",1800):(658.7,0.0,0),
    ("04","12",2000):(658.7,0.0,0), ("04","12",2200):(658.7,0.0,0),
    ("04","24",1200):(353.9,0.6,0), ("04","24",1700):(353.9,0.6,0),
    ("04","24",1800):(353.9,0.6,0), ("04","24",2500):(353.9,0.6,0),
    # ── 05 東京 (左回り・急坂・長い直線) ─────────────────────────────────────
    ("05","11",1400):(525.9,2.7,1), ("05","11",1600):(525.9,2.7,1),
    ("05","11",1800):(525.9,2.7,1), ("05","11",2000):(525.9,2.7,1),
    ("05","11",2300):(525.9,2.7,1), ("05","11",2400):(525.9,2.7,1),
    ("05","11",2500):(525.9,2.7,1), ("05","11",3400):(525.9,2.7,1),
    ("05","23",1300):(501.6,2.4,1), ("05","23",1400):(501.6,2.4,1),
    ("05","23",1600):(501.6,2.4,1), ("05","23",2100):(501.6,2.4,1),
    # ── 06 中山 (右回り・急坂・小回り) ──────────────────────────────────────
    ("06","10",1200):(310.0,5.3,1), ("06","12",1600):(310.0,5.3,1),
    ("06","10",1800):(310.0,5.3,1), ("06","10",2000):(310.0,5.3,1),
    ("06","10",2200):(310.0,5.3,1), ("06","10",2500):(310.0,5.3,1),
    ("06","10",3600):(310.0,5.3,1),
    ("06","24",1200):(308.0,4.4,1), ("06","24",1800):(308.0,4.4,1),
    ("06","24",2400):(308.0,4.4,1),
    # ── 07 中京 (左回り・急坂・長い直線) ─────────────────────────────────────
    ("07","11",1200):(412.5,3.5,1), ("07","11",1400):(412.5,3.5,1),
    ("07","11",1600):(412.5,3.5,1), ("07","11",2000):(412.5,3.5,1),
    ("07","11",2200):(412.5,3.5,1),
    ("07","23",1200):(410.7,3.4,1), ("07","23",1400):(410.7,3.4,1),
    ("07","23",1800):(410.7,3.4,1), ("07","23",1900):(410.7,3.4,1),
    # ── 08 京都 (右回り・淀の坂・直線平坦) ──────────────────────────────────
    ("08","10",1200):(328.4,4.3,0), ("08","10",1400):(328.4,4.3,0),
    ("08","10",1600):(328.4,4.3,0), ("08","10",2000):(328.4,4.3,0),
    ("08","17",1400):(403.7,4.3,0), ("08","17",1600):(403.7,4.3,0),
    ("08","17",1800):(403.7,4.3,0), ("08","17",2200):(403.7,4.3,0),
    ("08","17",2400):(403.7,4.3,0), ("08","17",3000):(403.7,4.3,0),
    ("08","17",3200):(403.7,4.3,0),
    ("08","24",1200):(329.1,3.2,0), ("08","24",1400):(329.1,3.2,0),
    ("08","24",1800):(329.1,3.2,0), ("08","24",1900):(329.1,3.2,0),
    # ── 09 阪神 (右回り・急坂 / 内外で直線長が大きく異なる) ──────────────────
    ("09","10",1200):(356.5,1.9,1), ("09","10",1400):(356.5,1.9,1),
    ("09","10",1800):(356.5,1.9,1), ("09","10",2000):(356.5,1.9,1),
    ("09","10",2200):(356.5,1.9,1),
    ("09","17",1600):(473.6,2.4,1), ("09","17",1800):(473.6,2.4,1),
    ("09","17",2400):(473.6,2.4,1),
    ("09","24",1200):(352.7,1.6,1), ("09","24",1400):(352.7,1.6,1),
    ("09","24",1800):(352.7,1.6,1), ("09","24",2000):(352.7,1.6,1),
    # ── 10 小倉 (右回り・直線平坦) ──────────────────────────────────────────
    ("10","10",1200):(293.0,3.0,0), ("10","10",1700):(293.0,3.0,0),
    ("10","10",1800):(293.0,3.0,0), ("10","10",2000):(293.0,3.0,0),
    ("10","24",1000):(291.3,2.9,0), ("10","24",1700):(291.3,2.9,0),
    ("10","24",2400):(291.3,2.9,0),
}

# ── 競馬場レベルのデフォルト（未知コース時フォールバック）────────────────────
_VENUE_DEFAULTS: dict[str, tuple[float, float, int]] = {}
for (_kc, _tc, _d), _v in _COURSE_MASTER.items():
    if _kc not in _VENUE_DEFAULTS or _v[0] > _VENUE_DEFAULTS[_kc][0]:
        _VENUE_DEFAULTS[_kc] = _v

# ── 公開定数 ─────────────────────────────────────────────────────────────────
COURSE_V3_COLS: list[str] = [
    # Phase 1: 競馬場直接適性
    "apt_venue_starts",
    "apt_venue_win_rate_5",
    "apt_venue_avg_rank_5",
    "apt_venue_fukusho_rate_5",
    # Phase 2: コース物理特性 × Expectation Gap / 上がり適性
    "eg_flat_avg10",
    "eg_steep_avg10",
    "eg_turn_L_avg10",
    "eg_turn_R_avg10",
    "eg_steep_minus_flat",
    "agari_flat_avg10",
    "agari_steep_avg10",
    # Phase 3: ローテーション条件替わり
    "rot_straight_delta",
    "rot_turn_switch",
    "rot_slope_shift",
    "rot_distance_delta",
    "rot_is_new_venue",
]

_TMP_COLS = ["_cur_straight", "_cur_slope_cat", "_cur_turn",
             "_go3f_rank", "_expect_gap"]


# ─────────────────────────────────────────────────────────────────────────────
# ヘルパー
# ─────────────────────────────────────────────────────────────────────────────

def _slope_cat(elevation_diff: float, hill_flag: int) -> int:
    """坂カテゴリ: 0=平坦, 1=中坂, 2=急坂"""
    if hill_flag == 0:
        return 0
    return 2 if elevation_diff >= 3.0 else 1


def _lookup_course_props(
    keibajo: str, track_code: str, distance: int
) -> tuple[float, float, int]:
    """
    (straight_dist, elevation_diff, hill_flag) を返す。
    完全一致なければ同競馬場内で距離が最も近いコースにフォールバック。
    """
    kc   = str(keibajo).zfill(2)
    tc   = str(track_code).zfill(2)
    dist = int(distance)
    key  = (kc, tc, dist)

    if key in _COURSE_MASTER:
        return _COURSE_MASTER[key]

    candidates = {k: v for k, v in _COURSE_MASTER.items() if k[0] == kc}
    if candidates:
        nearest = min(candidates, key=lambda k: abs(k[2] - dist))
        return candidates[nearest]

    return _VENUE_DEFAULTS.get(kc, (300.0, 1.0, 0))


def _cond_rolling_mean(
    series: pd.Series,
    mask: pd.Series,
    window: int = 10,
    min_periods: int = 1,
) -> pd.Series:
    """
    条件付きローリング平均。mask==True の行だけを有効値として window 内で平均する。
    mask==False の行は NaN にマスクしてから rolling(window) を適用するため、
    「直近 window 走のうちその条件に当てはまる走の平均」を返す。
    """
    return series.where(mask).rolling(window, min_periods=min_periods).mean()


# ─────────────────────────────────────────────────────────────────────────────
# 公開 API
# ─────────────────────────────────────────────────────────────────────────────

def create_course_features_v3(df: pd.DataFrame) -> pd.DataFrame:
    """
    Phase 1 + Phase 2 + Phase 3 のコース適性特徴量を生成する。

    入力 df は「1馬1レース1行」形式。予想対象レースの行は
    confirmed_rank = NaN（リーク防止）、go_3f_time = NaN に設定しておくこと。

    ninki が欠損の場合は 0 として扱い EG 計算をスキップ（NaN となる）。
    """
    df = df.copy()
    df["horse_id"]     = df["horse_id"].astype(str)
    df["keibajo_code"] = df["keibajo_code"].astype(str).str.zfill(2)
    df["track_code"]   = df["track_code"].astype(str).str.zfill(2)
    df["distance"]     = pd.to_numeric(df["distance"], errors="coerce").fillna(0).astype(int)
    df["race_date"]    = pd.to_datetime(df["race_date"])

    if "confirmed_rank" not in df.columns:
        df["confirmed_rank"] = pd.to_numeric(
            df.get("kakutei_chakujun"), errors="coerce"
        )

    df = _attach_current_props(df)
    df = _attach_go3f_rank(df)
    df = _attach_expect_gap(df)

    df = _compute_venue_features(df)
    df = _compute_eg_features(df)
    df = _compute_rotation_features(df)

    df = df.drop(columns=_TMP_COLS, errors="ignore")
    return df


# ─────────────────────────────────────────────────────────────────────────────
# 内部処理
# ─────────────────────────────────────────────────────────────────────────────

def _attach_current_props(df: pd.DataFrame) -> pd.DataFrame:
    """当該レースのコース物理特性を一時カラムとして付与する（ベクトル化）。"""
    props = [
        _lookup_course_props(kc, tc, d)
        for kc, tc, d in zip(
            df["keibajo_code"].values,
            df["track_code"].values,
            df["distance"].values,
        )
    ]
    df = df.copy()
    df["_cur_straight"]  = [p[0] for p in props]
    df["_cur_slope_cat"] = [_slope_cat(p[1], p[2]) for p in props]
    df["_cur_turn"]      = df["keibajo_code"].map(TURN_DIRECTION).fillna("R")
    return df


def _attach_go3f_rank(df: pd.DataFrame) -> pd.DataFrame:
    """
    go_3f_time から レース内上がり順位（1=最速）を付与する。
    go_3f_time が欠損のレース（予想対象含む）は NaN のまま。
    """
    df = df.copy()
    if "go_3f_time" not in df.columns:
        df["_go3f_rank"] = np.nan
        return df

    # レース内で go_3f_time 昇順ランク (1=上がり最速)
    # NaN は na_option="bottom" で最下位扱いにする
    df["_go3f_rank"] = df.groupby("race_id")["go_3f_time"].rank(
        ascending=True, method="min", na_option="bottom"
    )
    # go_3f_time 自体が欠損の馬は NaN に戻す（予想対象行など）
    df.loc[df["go_3f_time"].isna(), "_go3f_rank"] = np.nan
    return df


def _attach_expect_gap(df: pd.DataFrame) -> pd.DataFrame:
    """
    Expectation Gap = ninki - confirmed_rank を付与する。
    正値 = 人気より好走（穴で来た）、負値 = 人気倒れ。
    ninki または confirmed_rank が欠損の場合は NaN。
    """
    df = df.copy()
    ninki_raw = df.get("ninki")
    # ninki 列が存在しない場合（_HIST_COLS 未収録の推論パス）は全 NaN Series にする
    if isinstance(ninki_raw, pd.Series):
        ninki = pd.to_numeric(ninki_raw, errors="coerce")
    else:
        ninki = pd.Series(np.nan, index=df.index)
    # 着順が 0 以下（取消・除外）は無効
    rank  = pd.to_numeric(df["confirmed_rank"], errors="coerce")
    rank  = rank.where(rank > 0)
    df["_expect_gap"] = (ninki - rank).where(ninki.notna() & rank.notna())
    return df


def _compute_venue_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Phase 1: (horse_id, keibajo_code) グループ内で
    shift(1) → rolling(5) を適用し競馬場固有の実績を計算する。
    groupby.rolling を使って lambda を排除し高速化する。
    """
    _W = 5
    df_v = df.sort_values(["horse_id", "keibajo_code", "race_date", "race_id"]).copy()
    gv   = df_v.groupby(["horse_id", "keibajo_code"], sort=False)

    df["apt_venue_starts"] = gv.cumcount()

    # 有効着順（0以下は無効）・shift(1) で過去走のみ参照
    rank_valid  = df_v["confirmed_rank"].where(df_v["confirmed_rank"] > 0)
    df_v["_rv"] = rank_valid
    df_v["_rv_s"]    = gv["_rv"].shift(1)                              # 過去走着順
    df_v["_rv_win"]  = df_v["_rv_s"].where(df_v["_rv_s"].notna()).eq(1).where(df_v["_rv_s"].notna()).astype(float)
    df_v["_rv_fku"]  = df_v["_rv_s"].le(3).where(df_v["_rv_s"].notna()).astype(float)

    def _groll_v(col: str, window: int) -> pd.Series:
        r = gv[col].rolling(window, min_periods=1).mean()
        return r.reset_index(level=[0, 1], drop=True).reindex(df_v.index)

    df["apt_venue_win_rate_5"]     = _groll_v("_rv_win", _W)
    df["apt_venue_avg_rank_5"]     = _groll_v("_rv_s",   _W)
    df["apt_venue_fukusho_rate_5"] = _groll_v("_rv_fku", _W)

    no_venue_hist = df["apt_venue_starts"] == 0
    for col in ("apt_venue_win_rate_5", "apt_venue_avg_rank_5", "apt_venue_fukusho_rate_5"):
        df.loc[no_venue_hist, col] = np.nan

    # ── Fallback 1: 同馬の全競馬場通算実績（expanding）───────────────────────
    df_h = df.sort_values(["horse_id", "race_date", "race_id"]).copy()
    gh   = df_h.groupby("horse_id", sort=False)

    rank_valid_h = df_h["confirmed_rank"].where(df_h["confirmed_rank"] > 0)
    df_h["_rv_h"]    = rank_valid_h
    df_h["_rv_hs"]   = gh["_rv_h"].shift(1)
    df_h["_rv_hw"]   = df_h["_rv_hs"].where(df_h["_rv_hs"].notna()).eq(1).where(df_h["_rv_hs"].notna()).astype(float)
    df_h["_rv_hf"]   = df_h["_rv_hs"].le(3).where(df_h["_rv_hs"].notna()).astype(float)

    def _gexp(col: str) -> pd.Series:
        r = (
            df_h.groupby("horse_id", sort=False)[col]
            .expanding(min_periods=1).mean()
        )
        return r.reset_index(level=0, drop=True).reindex(df_h.index)

    overall_win = _gexp("_rv_hw")
    overall_avg = _gexp("_rv_hs")
    overall_fku = _gexp("_rv_hf")

    df["apt_venue_win_rate_5"]     = df["apt_venue_win_rate_5"].fillna(overall_win)
    df["apt_venue_avg_rank_5"]     = df["apt_venue_avg_rank_5"].fillna(overall_avg)
    df["apt_venue_fukusho_rate_5"] = df["apt_venue_fukusho_rate_5"].fillna(overall_fku)

    # ── Fallback 2: JRA全体平均（デビュー戦）─────────────────────────────────
    df["apt_venue_win_rate_5"]     = df["apt_venue_win_rate_5"].fillna(0.08)
    df["apt_venue_avg_rank_5"]     = df["apt_venue_avg_rank_5"].fillna(7.0)
    df["apt_venue_fukusho_rate_5"] = df["apt_venue_fukusho_rate_5"].fillna(0.23)

    return df


def _compute_eg_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Phase 2: コース物理特性別 Expectation Gap + 上がり適性を計算する。

    groupby.rolling() を使って lambda を排除し高速化する。
    horse_id でソート済みの df_h 上で shift(1) + rolling(10) を適用。
    """
    _WINDOW = 10

    # horse_id, race_date でソートして連続した行を保証
    df_h = df.sort_values(["horse_id", "race_date", "race_id"]).copy()

    # ── shift(1) で「過去走」の値を取得 ──────────────────────────────────────
    # groupby.shift は高速な Cython 実装
    gh = df_h.groupby("horse_id", sort=False)
    df_h["_ps"] = gh["_cur_slope_cat"].shift(1).astype(float)   # prev slope_cat
    df_h["_pt"] = gh["_cur_turn"].shift(1)                       # prev turn
    df_h["_pe"] = gh["_expect_gap"].shift(1)                     # prev EG
    df_h["_pa"] = gh["_go3f_rank"].shift(1)                      # prev agari rank

    # ── 条件マスク適用（NaN化）──────────────────────────────────────────────
    df_h["_eg_flat_m"]  = df_h["_pe"].where(df_h["_ps"] == 0)
    df_h["_eg_steep_m"] = df_h["_pe"].where(df_h["_ps"] >= 1)
    df_h["_eg_L_m"]     = df_h["_pe"].where(df_h["_pt"] == "L")
    df_h["_eg_R_m"]     = df_h["_pe"].where(df_h["_pt"] == "R")
    df_h["_ag_flat_m"]  = df_h["_pa"].where(df_h["_ps"] == 0)
    df_h["_ag_steep_m"] = df_h["_pa"].where(df_h["_ps"] >= 1)

    # ── groupby.rolling (lambda 不使用・Cython 高速パス) ─────────────────────
    def _groll(col: str) -> pd.Series:
        """horse_id グループ内で rolling mean を計算し元のインデックスに戻す。"""
        r = (
            df_h.groupby("horse_id", sort=False)[col]
            .rolling(_WINDOW, min_periods=1)
            .mean()
        )
        # groupby.rolling はマルチインデックスを返すので level=1 を取る
        return r.reset_index(level=0, drop=True).reindex(df_h.index)

    df["eg_flat_avg10"]   = _groll("_eg_flat_m")
    df["eg_steep_avg10"]  = _groll("_eg_steep_m")
    df["eg_turn_L_avg10"] = _groll("_eg_L_m")
    df["eg_turn_R_avg10"] = _groll("_eg_R_m")
    df["agari_flat_avg10"]  = _groll("_ag_flat_m")
    df["agari_steep_avg10"] = _groll("_ag_steep_m")

    # ── 坂適性ギャップ（条件替わりの恩恵指標）─────────────────────────────────
    # 急坂 EG - 平坦 EG: 正=急坂で強い、負=平坦で強い（条件替わりのミスプライス検出）
    df["eg_steep_minus_flat"] = df["eg_steep_avg10"] - df["eg_flat_avg10"]

    # ── Fallback 1: 全コース通算 EG / 上がり平均 ─────────────────────────────
    overall_eg = gh["_expect_gap"].transform(
        lambda x: x.shift(1).rolling(_WINDOW, min_periods=1).mean()
    )
    overall_agari = gh["_go3f_rank"].transform(
        lambda x: x.shift(1).rolling(_WINDOW, min_periods=1).mean()
    )

    for col in ("eg_flat_avg10", "eg_steep_avg10",
                "eg_turn_L_avg10", "eg_turn_R_avg10"):
        df[col] = df[col].fillna(overall_eg)

    df["agari_flat_avg10"]  = df["agari_flat_avg10"].fillna(overall_agari)
    df["agari_steep_avg10"] = df["agari_steep_avg10"].fillna(overall_agari)

    # ── Fallback 2: ドメイン定数（デビュー戦・全未経験）───────────────────────
    # EG = 0.0: 人気通りの走り（中立）
    # 上がり順位 = 8.0: 16頭立て中間順位（JRA平均出走頭数 ≈ 13.7）
    for col in ("eg_flat_avg10", "eg_steep_avg10",
                "eg_turn_L_avg10", "eg_turn_R_avg10", "eg_steep_minus_flat"):
        df[col] = df[col].fillna(0.0)

    df["agari_flat_avg10"]  = df["agari_flat_avg10"].fillna(8.0)
    df["agari_steep_avg10"] = df["agari_steep_avg10"].fillna(8.0)

    return df


def _compute_rotation_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Phase 3: 直近 1 走との条件差分を計算する。
    前走がない場合（デビュー戦）はすべて 0（変化なし＝中立）で補完。
    """
    df_h = df.sort_values(["horse_id", "race_date", "race_id"])
    gh   = df_h.groupby("horse_id", sort=False)

    prev_straight  = gh["_cur_straight"].transform(lambda x: x.shift(1))
    prev_slope_cat = gh["_cur_slope_cat"].transform(lambda x: x.astype(float).shift(1))
    prev_distance  = gh["distance"].transform(lambda x: x.astype(float).shift(1))
    prev_turn      = gh["_cur_turn"].transform(lambda x: x.shift(1))

    df["rot_straight_delta"] = (df["_cur_straight"] - prev_straight).fillna(0.0)
    df["rot_slope_shift"]    = (
        df["_cur_slope_cat"].astype(float) - prev_slope_cat
    ).fillna(0.0)
    df["rot_distance_delta"] = (
        df["distance"].astype(float) - prev_distance
    ).fillna(0.0)

    # 回り方向変化フラグ（インデックスズレを避けるため df_h ベースで比較）
    turn_switched = (
        df_h["_cur_turn"] != prev_turn
    ).where(prev_turn.notna(), other=np.nan)
    df["rot_turn_switch"] = turn_switched.fillna(0.0).astype(float)

    df["rot_is_new_venue"] = (df["apt_venue_starts"] == 0).astype(float)

    return df
