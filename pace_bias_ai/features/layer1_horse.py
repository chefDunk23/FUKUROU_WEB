"""
pace_bias_ai/features/layer1_horse.py
=======================================
第1層（数値化）: 馬単位の特徴量

新規実装:
    versatile_type          自在タイプ判定 (先行勝ち + 差し勝ち両方あり → 1.0)
    versatile_score         自在スコア (0〜1, キャリア浅い馬は NaN)
    hidden_late_speed       隠れた末脚スコア (上がり4〜5番手でも実質上位)
    weight_reduction_flag   減量騎手フラグ推算 (jockey_career_wins < 100 → 1.0)
    opening_week_flag       開幕週フラグ (kaisai_nichime が 1〜2 日目 → 1.0)
    distance_change         前走から今走の距離変化量 (m)
    distance_extended       距離延長フラグ (+200m 以上 → 1.0)

リーク防止方針:
    - 過去走の脚質・着順はすべて shift(1)+rolling → 当走を含まない
    - versatile_type は先行勝ち/差し勝ちの判定に当走着順を使わない
    - hidden_late_speed は「過去走の」上がりレース内順位を参照

必須カラム（入力 DataFrame）:
    horse_id           馬ID
    race_id            レースID
    race_date          レース日
    corner_4           第4コーナー通過順位
    kakutei_chakujun   確定着順 (0/NULL=取消・中止)
    go_3f_time         上がり3Fタイム (秒, 0/NULL=計測なし)
    umaban             馬番 (field_size 算出の代用)
    distance           レース距離 (m)

任意カラム:
    field_size         出走頭数（あれば優先使用）
    kaisai_nichime     開催日次 (races_v2 テーブル由来)
    jockey_career_wins 騎手通算勝利数 (jockeys テーブル由来)
    kinryo             負担重量 0.1kg 単位 (race_entries_v2 由来)
    basis_weight       斤量 kg (race_entries.basis_weight 由来)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# ── 定数 ──────────────────────────────────────────────────────────────────────

# 自在タイプ: 先行判定の c4 正規化閾値 (0=逃げ〜1=追込)
_FRONT_THRESH = 0.35   # 0.35 以下 → 先行とみなす
_CLOSER_THRESH = 0.65  # 0.65 以上 → 差しとみなす

# 自在スコアに最低限必要なキャリア（先行走or差し走の実績数）
_MIN_CAREER_FOR_VERSATILE = 4

# 開幕週判定: kaisai_nichime が何日目以内か
_OPENING_NICHIME_MAX = 2  # 1〜2日目 = 開幕週扱い

# 減量騎手の通算勝利数閾値（JRA基準: 100勝未満が減量対象）
_APPRENTICE_WIN_THRESHOLD = 100

# 距離延長閾値 (m)
_EXTENSION_MIN_M = 200

# NaN 補完
_FILL_VERSATILE = 0.5    # 判定不能（キャリア浅い）→ 中立
_FILL_HIDDEN_LS  = 0.5   # 隠れ末脚スコア未計算 → 中立

# 公開カラム名
LAYER1_HORSE_COLS: list[str] = [
    "versatile_type",
    "versatile_score",
    "hidden_late_speed",
    "weight_reduction_flag",
    "opening_week_flag",
    "distance_change",
    "distance_extended",
]

_REQUIRED_COLS = frozenset({
    "horse_id", "race_id", "race_date",
    "corner_4", "kakutei_chakujun", "go_3f_time",
    "umaban", "distance",
})


def create_layer1_horse_features(df: pd.DataFrame) -> pd.DataFrame:
    """馬単位の第1層特徴量を生成して返す。

    入力 df のコピーに LAYER1_HORSE_COLS の列を追加したものを返す。
    入力は「1馬1レース1行」形式。

    Returns:
        LAYER1_HORSE_COLS を追加した新しい DataFrame（元 df を変更しない）
    """
    missing = _REQUIRED_COLS - set(df.columns)
    if missing:
        raise ValueError(f"必須カラムが不足: {sorted(missing)}")

    df = df.copy()

    # ── 元の行順を記憶 ────────────────────────────────────────────────────────
    _ORD = "__orig_order__"
    df[_ORD] = np.arange(len(df), dtype=np.int64)

    # ── 出走頭数 ─────────────────────────────────────────────────────────────
    if "field_size" in df.columns:
        field_size = df["field_size"].fillna(1).clip(lower=1).astype(float)
    else:
        field_size = (
            df.groupby("race_id")["umaban"].transform("max")
            .fillna(1).clip(lower=1).astype(float)
        )
    denom = (field_size - 1.0).clip(lower=1.0)

    # ── 前処理: 無効値を NaN に ───────────────────────────────────────────────
    c4_raw = df["corner_4"].where(df["corner_4"].notna() & (df["corner_4"] > 0))
    rank_valid = df["kakutei_chakujun"].where(
        df["kakutei_chakujun"].notna() & (df["kakutei_chakujun"] > 0)
    )
    go3f_valid = df["go_3f_time"].where(df["go_3f_time"].notna() & (df["go_3f_time"] > 0))

    # ── c4 正規化 (0=先頭, 1=最後尾) ─────────────────────────────────────────
    c4_norm = (c4_raw - 1.0) / denom
    df["_c4_norm"] = c4_norm

    # ── 上がり3Fレース内順位 ─────────────────────────────────────────────────
    df["_go3f_rank"] = (
        df.groupby("race_id")["go_3f_time"]
        .transform(lambda s: (
            s.where(s.notna() & (s > 0))
            .rank(method="min", ascending=True, na_option="keep")
        ))
    )

    # ── 勝利フラグ ────────────────────────────────────────────────────────────
    df["_won"] = (rank_valid == 1).astype(float)
    # 先行勝ち: c4 正規化 ≤ 前付け閾値 かつ 1着
    df["_front_win"] = (c4_norm <= _FRONT_THRESH) & (df["_won"] == 1)
    df["_front_win"] = df["_front_win"].astype(float)
    # 差し勝ち: c4 正規化 ≥ 差し閾値 かつ 1着
    df["_closer_win"] = (c4_norm >= _CLOSER_THRESH) & (df["_won"] == 1)
    df["_closer_win"] = df["_closer_win"].astype(float)
    # NaN がある行はフラグも NaN に
    df.loc[c4_norm.isna() | rank_valid.isna(), ["_front_win", "_closer_win"]] = np.nan

    # ── 時系列ソート → horse_id グループ ────────────────────────────────────
    df = df.sort_values(["horse_id", "race_date", "race_id"]).reset_index(drop=True)
    gh = df.groupby("horse_id", sort=False)

    # ── 自在タイプ ────────────────────────────────────────────────────────────
    # shift(1) で当走を除外してから cumsum/cumcount で「これまでの実績」を算出
    cumfrontwin  = gh["_front_win"].transform(lambda x: x.shift(1).fillna(0).cumsum())
    cumcloserwin = gh["_closer_win"].transform(lambda x: x.shift(1).fillna(0).cumsum())
    cum_c4_valid = gh["_c4_norm"].transform(lambda x: x.shift(1).notna().cumsum())

    # 先行走と差し走の両方に実績があり、かつキャリア（c4有効走数）が十分ある場合に判定
    has_front  = cumfrontwin  > 0
    has_closer = cumcloserwin > 0
    career_ok  = cum_c4_valid >= _MIN_CAREER_FOR_VERSATILE

    df["versatile_type"] = np.where(
        career_ok,
        (has_front & has_closer).astype(float),
        np.nan,
    )

    # 自在スコア: 先行勝ち数と差し勝ち数の調和平均的スコア (0〜1)
    # min(front_wins, closer_wins) / max(front_wins, closer_wins + 1) を近似
    total_fw = cumfrontwin.clip(lower=0)
    total_cw = cumcloserwin.clip(lower=0)
    min_vc = np.minimum(total_fw, total_cw)
    max_vc = np.maximum(total_fw, total_cw).clip(lower=1)
    df["versatile_score"] = np.where(
        career_ok,
        (min_vc / max_vc).clip(0.0, 1.0),
        np.nan,
    )

    # ── 隠れた末脚スコア ─────────────────────────────────────────────────────
    # 「上がりがレース内で4〜5番手でも実質上位」を数値化する。
    # レース内上がり順位を頭数で正規化（0=最速, 1=最遅）し、
    # 直近5走の過去走平均をとったものを 1.0 から引いて「速さスコア」に変換。
    # 値が高い（上がり平均が速い）＝隠れた末脚あり。
    # go3f_rank_norm: 0=最速クローザー, 1=最遅（pace_simulation と同じ方向感）
    go3f_rank_norm = (df["_go3f_rank"] - 1.0) / denom.clip(lower=1.0)
    df["_go3f_rank_norm"] = go3f_rank_norm

    # 過去5走の上がり順位正規化平均（shift(1) でリーク防止）
    avg_go3f_norm_5 = gh["_go3f_rank_norm"].transform(
        lambda x: x.shift(1).rolling(5, min_periods=1).mean()
    )
    # 末脚スコア: 1 - 正規化上がり順位平均 → 0=末脚なし, 1=上がり最速
    df["hidden_late_speed"] = (1.0 - avg_go3f_norm_5).clip(0.0, 1.0)

    # ── 距離変化 ─────────────────────────────────────────────────────────────
    prev_dist = gh["distance"].transform(lambda x: x.shift(1))
    df["distance_change"]   = (df["distance"].astype(float) - prev_dist).fillna(0.0)
    df["distance_extended"] = (df["distance_change"] >= _EXTENSION_MIN_M).astype(float)

    # ── 開幕週フラグ ─────────────────────────────────────────────────────────
    if "kaisai_nichime" in df.columns:
        nichime = pd.to_numeric(df["kaisai_nichime"], errors="coerce")
        df["opening_week_flag"] = (nichime <= _OPENING_NICHIME_MAX).astype(float)
    else:
        df["opening_week_flag"] = np.nan  # データなし → 後でフォールバック

    # ── 減量騎手フラグ ────────────────────────────────────────────────────────
    # jockey_career_wins が利用可能な場合: 100勝未満 → 1.0
    # 利用不可の場合: kinryo（実斤量 ×10）と basis_weight を比較して推算
    if "jockey_career_wins" in df.columns:
        career_wins = pd.to_numeric(df["jockey_career_wins"], errors="coerce")
        df["weight_reduction_flag"] = (career_wins < _APPRENTICE_WIN_THRESHOLD).astype(float)
        df.loc[career_wins.isna(), "weight_reduction_flag"] = 0.0  # 不明 → なしと仮定
    elif "kinryo" in df.columns and "basis_weight" in df.columns:
        # kinryo は 0.1kg 単位整数 → /10 でkg換算
        kinryo_kg = pd.to_numeric(df["kinryo"], errors="coerce") / 10.0
        basis_kg  = pd.to_numeric(df["basis_weight"], errors="coerce")
        # 実斤量が標準より 1kg 以上軽い → 減量騎手の可能性が高い
        df["weight_reduction_flag"] = ((basis_kg - kinryo_kg) >= 1.0).astype(float)
        df.loc[kinryo_kg.isna() | basis_kg.isna(), "weight_reduction_flag"] = 0.0
    else:
        df["weight_reduction_flag"] = 0.0  # データなし → なしと仮定

    # ── 元の行順に復元 ────────────────────────────────────────────────────────
    internal = [c for c in df.columns if c.startswith("_") and c != _ORD]
    df = df.drop(columns=internal, errors="ignore")
    df = df.sort_values(_ORD).drop(columns=[_ORD]).reset_index(drop=True)

    # ── NaN 補完 ─────────────────────────────────────────────────────────────
    df["versatile_type"]     = df["versatile_type"].fillna(_FILL_VERSATILE)
    df["versatile_score"]    = df["versatile_score"].fillna(_FILL_VERSATILE)
    df["hidden_late_speed"]  = df["hidden_late_speed"].fillna(_FILL_HIDDEN_LS)
    df["opening_week_flag"]  = df["opening_week_flag"].fillna(0.0)   # kaisai_nichime なし → 非開幕週扱い
    df["weight_reduction_flag"] = df["weight_reduction_flag"].fillna(0.0)

    return df
