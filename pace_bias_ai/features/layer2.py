"""
pace_bias_ai/features/layer2.py
================================
第2層（AI）: 特徴量エンジニアリング

設計方針:
    a. レース内相対化 — LightGBMが「このレース内での位置付け」を直接見られるように
    b. 条件カテゴリ変数 — 距離帯・芝ダート・競馬場コード（LightGBMに入力）
    c. 条件差特徴量 — 前走との変化量（距離変化は第1層で計算済み）
    d. ベイズスムージング付きTE — データ希薄時に全体平均に寄せる（リーク防止PIT化）

リーク対策（厳守）:
    - TEは「race_dateより前の実績」のみで計算（shift(1)+cumsum）
    - レース内相対化は「第1層の予測値（bias_position_harmony等）」を使用
      → 当レースの結果は一切参照しない
    - 当走の着順は特徴量に含めない
    - 初走（前走なし）の条件差特徴量はNaN → 中立値で補完

TEのベイズスムージング方式:
    te = (wins_before + α × global_rate) / (count_before + α)
    → 試行数が少ない（データ希薄）ほど全体平均（中立）に近づく
    → 試行数が多い（実績豊富）ほど実績値に近づく

    α値の根拠:
        jockey_te  (α=20): 騎手×距離帯×芝ダートで平均30〜100走。
                           α=20 → 30走時は実績60%/全体40%の混合。
        sire_te    (α=30): 種牡馬×距離帯×芝ダートで平均100〜500走だが
                           条件を絞ると薄くなるため。
        venue_horse_te (α=5): 馬×競馬場は平均3〜10走と非常に少ない。
                              α=5 → 5走時は実績50%/全体50%の混合で十分安全。
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# ── 出力カラム名 ──────────────────────────────────────────────────────────────
LAYER2_FEATURE_COLS: list[str] = [
    # a: レース内相対化（当レース内でのスコア順位・偏差）
    "harmony_rank_norm",        # harmony のレース内正規化順位 (0=最高, 1=最低)
    "pred_pos_rank_norm",       # 予測位置のレース内正規化順位 (0=最も前方)
    "hidden_late_rank_norm",    # 末脚のレース内正規化順位 (0=最強)
    "harmony_vs_mean",          # harmony - レース内平均（偏差）
    # d: ターゲットエンコーディング（PIT安全・ベイズスムージング）
    "jockey_te",                # 騎手×距離帯×芝ダートの複勝率TE
    "sire_te",                  # 種牡馬×距離帯×芝ダートの複勝率TE
    "venue_horse_te",           # 馬×競馬場の複勝率TE
    # c: 条件差特徴量（前走との変化、第1層の distance_change を補完）
    "venue_changed",            # 前走と今走で競馬場が異なる (0/1/NaN)
    "surface_changed",          # 芝⇔ダート変更 (0/1/NaN)
    "weight_change",            # 前走との斤量変化量（0.1kg単位）
    # 市場情報
    "popularity_norm",          # 人気を (人気-1)/(頭数-1) で正規化 (0=1番人気)
    "odds_log",                 # log1p(単勝オッズ)
    # 条件変数（LightGBMのカテゴリ変数として使用）
    "dist_cat",                 # 距離帯: 0=sprint, 1=mile, 2=middle, 3=long
    "surface_code",             # 走路: 0=芝, 1=ダート, 2=障害
    "field_size_norm",          # 出走頭数の正規化 (8頭=0, 18頭=1)
]

# ── TEのベイズスムージングα値 ─────────────────────────────────────────────────
_TE_ALPHA_JOCKEY = 20
_TE_ALPHA_SIRE   = 30
_TE_ALPHA_VENUE  = 5

# ── 距離帯区分（m）────────────────────────────────────────────────────────────
# 0=sprint(〜1400m), 1=mile(1401〜1800m), 2=middle(1801〜2200m), 3=long(>2200m)
_DIST_BINS = [0, 1400, 1800, 2200, 9999]

# ── NaN補完デフォルト値 ─────────────────────────────────────────────────────────
_FILL_TE            = 0.33   # JRA 全体の複勝率近似（3着以内/出走頭数=3/n, n≈10 → ~30%）
_FILL_VENUE_CHANGED = 0.5    # 初走は中立
_FILL_POP_NORM      = 0.5    # 人気不明は中立
_FILL_ODDS_LOG      = float(np.log1p(10.0))  # 10倍相当（中程度）


def _classify_surface(track_code: pd.Series) -> pd.Series:
    """track_code から走路コードを生成。0=芝, 1=ダート, 2=障害。"""
    tc_num     = pd.to_numeric(track_code, errors="coerce")
    first_dig  = (tc_num // 10).astype("Int64")
    result     = pd.Series(2.0, index=track_code.index, dtype=float)
    result[first_dig == 1] = 0.0
    result[first_dig == 2] = 1.0
    result[tc_num.isna()]  = np.nan
    return result


def _compute_pit_te(
    df: pd.DataFrame,
    group_cols: list[str],
    target_col: str,
    alpha: float,
) -> pd.Series:
    """PIT安全なベイズスムージングTE。

    各行の値 = 「そのrace_dateより前の同グループの目的変数実績」に
    ベイズスムージングを施したもの。

        te = (wins_before + α × global_rate) / (count_before + α)

    Args:
        df         : 入力DataFrame
        group_cols : グループキー（例: ['jockey_cd', 'dist_cat', 'surface_code']）
        target_col : ターゲット変数名（例: '_placed3'）。NaNは有効試行から除外。
        alpha      : スムージング強度（大きいほど全体平均に近づく）

    Returns:
        TE値のSeries（df.indexに対応）
    """
    required = {"race_date", target_col} | set(group_cols)
    missing  = required - set(df.columns)
    if missing:
        log.warning("[layer2 TE] カラム不足: %s → NaNで返す", sorted(missing))
        return pd.Series(np.nan, index=df.index)

    tgt_num     = pd.to_numeric(df[target_col], errors="coerce")
    global_rate = float(tgt_num.dropna().mean()) if tgt_num.notna().any() else 0.33

    # race_id も選択することで同日複数出走時のソート安定化に使用
    _sel = list(group_cols) + ["race_date", target_col]
    if "race_id" in df.columns:
        _sel.append("race_id")
    work = df[_sel].copy()
    work["_orig_idx"]  = np.arange(len(work))
    work["_tgt_val"]   = tgt_num.values
    work["_tgt_valid"] = tgt_num.notna().astype(float).values

    sort_cols = group_cols + ["race_date"] + (["race_id"] if "race_id" in work.columns else [])
    work = work.sort_values(sort_cols).reset_index(drop=True)
    grp  = work.groupby(group_cols, sort=False)

    # shift(1) で当走を除外した前走までの累積（リーク完全排除）
    work["_cum_wins"]  = grp["_tgt_val"].transform(
        lambda x: x.shift(1).fillna(0.0).cumsum()
    )
    work["_cum_count"] = grp["_tgt_valid"].transform(
        lambda x: x.shift(1).fillna(0.0).cumsum()
    )

    work["_te"] = (work["_cum_wins"] + alpha * global_rate) / (work["_cum_count"] + alpha)

    # 元の行順に復元
    work = work.sort_values("_orig_idx").reset_index(drop=True)
    return pd.Series(work["_te"].values, index=df.index)


def build_layer2_features(df: pd.DataFrame) -> pd.DataFrame:
    """第2層特徴量を生成して返す。

    入力dfは第1層（LAYER1_ALL_COLS）完了済みであること。
    LAYER2_FEATURE_COLS の列を追加した新しいDataFrameを返す。

    Args:
        df: 第1層完了済みのDataFrame（1馬1レース1行）

    Returns:
        LAYER2_FEATURE_COLS を追加した DataFrame（元dfを変更しない）
    """
    df = df.copy()
    log.info("[Layer2] 特徴量生成開始: %d行", len(df))

    # ── 出走頭数の確保 ────────────────────────────────────────────────────────
    if "field_size" in df.columns:
        fs    = pd.to_numeric(df["field_size"], errors="coerce").fillna(8.0).clip(lower=2.0)
    else:
        fs    = df.groupby("race_id")["umaban"].transform("max").fillna(8.0).clip(lower=2.0)
    denom = (fs - 1.0).clip(lower=1.0)

    # ── 1. 条件変数（距離帯・走路・頭数） ──────────────────────────────────────
    dist_num       = pd.to_numeric(df.get("distance"), errors="coerce")
    df["dist_cat"] = pd.cut(
        dist_num, bins=_DIST_BINS, labels=False, right=True,
    ).astype(float)

    df["surface_code"] = (
        _classify_surface(df["track_code"]) if "track_code" in df.columns
        else pd.Series(np.nan, index=df.index)
    )

    df["field_size_norm"] = ((fs - 8.0) / 10.0).clip(0.0, 1.0)

    # ── 2. TEの複勝フラグ（ターゲット変数）── 特徴量には含めない ──────────────
    if "kakutei_chakujun" in df.columns:
        rank_num      = pd.to_numeric(df["kakutei_chakujun"], errors="coerce")
        df["_placed3"] = (rank_num <= 3).where(rank_num.notna()).astype(float)
    else:
        df["_placed3"] = np.nan

    # ── 3. レース内相対化（a方針） ────────────────────────────────────────────

    # harmony_rank_norm: 0=そのレースで harmony が最高
    if "bias_position_harmony" in df.columns:
        h_rank = df.groupby("race_id")["bias_position_harmony"].rank(
            ascending=False, method="first", na_option="bottom",
        )
        df["harmony_rank_norm"] = ((h_rank - 1.0) / denom).clip(0.0, 1.0)
        h_mean                  = df.groupby("race_id")["bias_position_harmony"].transform("mean")
        df["harmony_vs_mean"]   = (df["bias_position_harmony"] - h_mean).fillna(0.0)
    else:
        df["harmony_rank_norm"] = 0.5
        df["harmony_vs_mean"]   = 0.0

    # pred_pos_rank_norm: 0=最も前方（小さい predicted_position_norm = 先頭グループ）
    if "predicted_position_norm" in df.columns:
        pp_rank = df.groupby("race_id")["predicted_position_norm"].rank(
            ascending=True, method="first", na_option="bottom",
        )
        df["pred_pos_rank_norm"] = ((pp_rank - 1.0) / denom).clip(0.0, 1.0)
    else:
        df["pred_pos_rank_norm"] = 0.5

    # hidden_late_rank_norm: 0=末脚が最も強い
    if "hidden_late_speed" in df.columns:
        hl_rank = df.groupby("race_id")["hidden_late_speed"].rank(
            ascending=False, method="first", na_option="bottom",
        )
        df["hidden_late_rank_norm"] = ((hl_rank - 1.0) / denom).clip(0.0, 1.0)
    else:
        df["hidden_late_rank_norm"] = 0.5

    # ── 4. TE（d方針: 騎手・種牡馬・馬×競馬場） ─────────────────────────────
    jockey_col = next(
        (c for c in ["jockey_cd", "jockey_id", "kishu_code"] if c in df.columns), None
    )
    sire_col = next(
        (c for c in ["father_blood_no", "sire_id", "sire_code"] if c in df.columns), None
    )

    if jockey_col and "dist_cat" in df.columns and "surface_code" in df.columns:
        df["jockey_te"] = _compute_pit_te(
            df, [jockey_col, "dist_cat", "surface_code"], "_placed3", _TE_ALPHA_JOCKEY,
        )
    else:
        df["jockey_te"] = np.nan
        if jockey_col is None:
            log.warning("[layer2] 騎手カラムなし → jockey_te=NaN")

    if sire_col and "dist_cat" in df.columns and "surface_code" in df.columns:
        df["sire_te"] = _compute_pit_te(
            df, [sire_col, "dist_cat", "surface_code"], "_placed3", _TE_ALPHA_SIRE,
        )
    else:
        df["sire_te"] = np.nan

    if "horse_id" in df.columns and "keibajo_code" in df.columns:
        df["venue_horse_te"] = _compute_pit_te(
            df, ["horse_id", "keibajo_code"], "_placed3", _TE_ALPHA_VENUE,
        )
    else:
        df["venue_horse_te"] = np.nan

    # ── 5. 条件差特徴量（c方針）──────────────────────────────────────────────
    # 時系列ソートして前走参照（shift(1)）
    _ORD = "__l2_orig_ord__"
    df[_ORD] = np.arange(len(df))
    df = df.sort_values(["horse_id", "race_date", "race_id"]).reset_index(drop=True)
    gh = df.groupby("horse_id", sort=False)

    if "keibajo_code" in df.columns:
        prev_venue         = gh["keibajo_code"].transform(lambda x: x.shift(1))
        venue_same         = df["keibajo_code"].astype(str) == prev_venue.fillna("__NA__").astype(str)
        df["venue_changed"] = (~venue_same).astype(float)
        df.loc[prev_venue.isna(), "venue_changed"] = np.nan
    else:
        df["venue_changed"] = np.nan

    if "surface_code" in df.columns:
        prev_surf              = gh["surface_code"].transform(lambda x: x.shift(1))
        df["surface_changed"]  = (df["surface_code"] != prev_surf).astype(float)
        df.loc[prev_surf.isna(), "surface_changed"] = np.nan
    else:
        df["surface_changed"] = np.nan

    if "kinryo" in df.columns:
        kinryo_num = pd.to_numeric(df["kinryo"], errors="coerce")
        prev_kg    = gh["kinryo"].transform(
            lambda x: pd.to_numeric(x, errors="coerce").shift(1)
        )
        df["weight_change"] = (kinryo_num - prev_kg)
        df.loc[prev_kg.isna(), "weight_change"] = np.nan
    else:
        df["weight_change"] = np.nan

    # 元の行順に戻す
    df = df.sort_values(_ORD).reset_index(drop=True)
    df = df.drop(columns=[_ORD], errors="ignore")

    # ── 6. 市場情報（人気・オッズ） ───────────────────────────────────────────
    if "popularity" in df.columns:
        pop                  = pd.to_numeric(df["popularity"], errors="coerce")
        df["popularity_norm"] = ((pop - 1.0) / denom).clip(0.0, 1.0)
    else:
        df["popularity_norm"] = np.nan

    if "win_odds" in df.columns:
        # win_odds は 0.1 倍単位（例: 23 → 2.3倍）
        odds_raw      = pd.to_numeric(df["win_odds"], errors="coerce") / 10.0
        df["odds_log"] = np.log1p(odds_raw.clip(lower=1.0))
    else:
        df["odds_log"] = np.nan

    # ── 7. NaN補完（中立値） ─────────────────────────────────────────────────
    _fill: dict[str, float] = {
        "jockey_te":          _FILL_TE,
        "sire_te":            _FILL_TE,
        "venue_horse_te":     _FILL_TE,
        "venue_changed":      _FILL_VENUE_CHANGED,
        "surface_changed":    0.0,
        "weight_change":      0.0,
        "popularity_norm":    _FILL_POP_NORM,
        "odds_log":           _FILL_ODDS_LOG,
        "dist_cat":           1.0,        # mile を中立とみなす
        "surface_code":       0.0,        # 芝をデフォルト
    }
    for col, val in _fill.items():
        if col in df.columns:
            df[col] = df[col].fillna(val)

    # ── 内部列削除 ─────────────────────────────────────────────────────────────
    internal = [c for c in df.columns if c.startswith("_")]
    df = df.drop(columns=internal, errors="ignore")

    log.info("[Layer2] 完了: %d列追加", len(LAYER2_FEATURE_COLS))
    return df
