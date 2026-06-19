"""
src/features/pace_features_v4.py
=================================
pace_v2 サブモデル向け特徴量エンジニアリング v4。

v3 からの改善:
    1. 頭数正規化 (0.0-1.0): コーナー通過順位を出走頭数で正規化
       0.0 = 先頭、1.0 = 最後尾。頭数の異なるレース間での脚質比較が可能に。
    2. 距離区分別脚質特徴量: スプリント/マイル/中距離/長距離 ごとに脚質履歴を分離
       該当区分に出走歴がない場合は中団相当 (0.5) で補完。
    3. 馬場別上がり適性: 芝/ダートに分けて上がり3F順位履歴を算出
       未経験の馬場は中位相当 (8.0) で補完。

v3 特徴量 (avg_c1_pos_5 / avg_c4_pos_5 / avg_pos_advance_5 /
           running_style_std_5 / avg_go3f_rank_5 / go3f_rank_std_5) は本モジュールに含まない。

リーク防止方針:
    - shift(1) + rolling(): 当走を除く直近N走の統計
    - 距離区分/馬場マスクはレース確定後の属性（予測時点で既知）であり当走情報ではない
    - go_3f_rank はレース内順位（レース単位で完結）を horse-level rolling の shift(1) で除外

使い方:
    df_out = create_pace_features_v4(df)

必須カラム:
    horse_id         馬ID
    race_id          レースID
    race_date        レース日付
    corner_1         第1コーナー通過順位 (int, 0/NULL=計測なし)
    corner_4         第4コーナー通過順位
    kakutei_chakujun 確定着順 (int, 0/NULL=取消・競走中止)
    go_3f_time       上がり3Fタイム (秒, 0/NULL=計測なし)
    umaban           馬番 (field_size の代用: race_id 内の最大値)
    distance         レース距離 (m)
    track_code       コード文字列 ('10' 系=芝, '20' 系=ダート)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# ── 距離区分定義 (スプリント/マイル/中距離/長距離) ─────────────────────────────
_DIST_BUCKETS: dict[str, tuple[int, int]] = {
    "sprint": (0,    1400),
    "mile":   (1500, 1800),
    "mid":    (1900, 2200),
    "long":   (2300, 99999),
}
_BUCKET_NAMES: list[str] = ["sprint", "mile", "mid", "long"]

# ── 馬場コード (JV-Data track_code の先頭文字で識別) ───────────────────────────
# '10'=芝, '11'=芝右, '12'=芝左, '17'=障害芝 等 → 先頭1文字 '1' が芝系
# '20'=ダート, '21'=ダート(右) 等 → 先頭1文字 '2' がダート系
_TURF_PREFIX = "1"
_DIRT_PREFIX = "2"

# ── NaN 補完値 ─────────────────────────────────────────────────────────────────
_FILL_NORM_POS:  float = 0.5   # 正規化コーナー位置: 中団相当
_FILL_NORM_ADV:  float = 0.0   # 正規化順位変化: 動きなし相当
_FILL_NORM_STD:  float = 0.15  # 正規化位置標準偏差: 典型的なブレ幅相当
_FILL_RANK_GO3F: float = 8.0   # 上がり3F順位 (生順位): 中位相当
_FILL_STD_GO3F:  float = 3.0   # 上がり3F順位標準偏差

_REQUIRED_COLS = frozenset({
    "horse_id", "race_id", "race_date",
    "corner_1", "corner_4", "kakutei_chakujun",
    "go_3f_time", "umaban", "distance", "track_code",
})

# 生成される特徴量カラム名 (外部参照用)
PACE_V4_COLS: list[str] = [
    # 頭数正規化ベース (4列)
    "avg_c1_norm_5",
    "avg_c4_norm_5",
    "avg_pos_advance_norm_5",
    "running_style_std_norm_5",
    # 最初通過コーナー正規化 (展開シミュレーション向け / 全距離対応)
    "avg_first_corner_norm_5",
    # 距離区分別脚質 (12列: 4区分 × 3特徴)
    "avg_c1_norm_5_sprint",          "avg_c4_norm_5_sprint",          "avg_pos_advance_norm_5_sprint",
    "avg_c1_norm_5_mile",            "avg_c4_norm_5_mile",            "avg_pos_advance_norm_5_mile",
    "avg_c1_norm_5_mid",             "avg_c4_norm_5_mid",             "avg_pos_advance_norm_5_mid",
    "avg_c1_norm_5_long",            "avg_c4_norm_5_long",            "avg_pos_advance_norm_5_long",
    # 馬場別上がり適性 (4列)
    "avg_go3f_rank_5_turf", "go3f_rank_std_5_turf",
    "avg_go3f_rank_5_dirt", "go3f_rank_std_5_dirt",
]


def create_pace_features_v4(df: pd.DataFrame) -> pd.DataFrame:
    """
    pace_v2 向け脚質・上がり特徴量 v4 を生成して返す。

    入力 df のコピーに PACE_V4_COLS の 20 列を追加したものを返す。
    入力に既存の v3 特徴量が含まれていても干渉しない。

    NaN 補完ルール:
        正規化コーナー位置          → 0.5  (中団相当)
        正規化順位変化              → 0.0  (動きなし)
        正規化位置標準偏差          → 0.15 (典型的なブレ幅)
        距離区分別 (出走歴なし)     → 上記と同値
        上がり3F順位 (馬場未経験)  → 8.0  (中位相当)
        上がり3F順位 std           → 3.0
    """
    missing = _REQUIRED_COLS - set(df.columns)
    if missing:
        raise ValueError(f"必須カラムが不足しています: {sorted(missing)}")

    df = df.copy()
    _ORD = "__orig_order__"
    df[_ORD] = np.arange(len(df), dtype=np.int64)

    # ── 前処理: 無効値を NaN に統一 ──────────────────────────────────────────
    c1_raw = df["corner_1"].where(df["corner_1"].notna() & (df["corner_1"] > 0))
    c4_raw = df["corner_4"].where(df["corner_4"].notna() & (df["corner_4"] > 0))
    rank_valid = df["kakutei_chakujun"].where(
        df["kakutei_chakujun"].notna() & (df["kakutei_chakujun"] > 0)
    )
    go3f_valid = df["go_3f_time"].where(df["go_3f_time"].notna() & (df["go_3f_time"] > 0))

    # ── First Corner Rank: c1 → c2 → c3 → c4 の優先順位で最初の記録コーナーを取得 ──
    # 1400m 以下は c1/c2 が未記録のため c3 が最初のコーナーになる（スプリント戦対応）。
    # 新潟1000m 直線など全コーナー未記録の場合は NaN（後で 0.5 補完）。
    def _valid(col: str) -> "pd.Series":
        if col not in df.columns:
            return pd.Series(np.nan, index=df.index)
        return df[col].where(df[col].notna() & (df[col] > 0))

    first_corner_raw = (
        _valid("corner_1")
        .combine_first(_valid("corner_2"))
        .combine_first(_valid("corner_3"))
        .combine_first(_valid("corner_4"))
    )

    # ── 出走頭数の導出 ────────────────────────────────────────────────────────
    # field_size 列が渡された場合はそれを優先（推論時: 1馬1行の履歴から正確な頭数を渡す）。
    # ない場合は race_id 内の umaban 最大値を代理値として使う（Parquet 一括処理向け）。
    if "field_size" in df.columns:
        field_size = df["field_size"].fillna(1).clip(lower=1).astype(float)
    else:
        field_size = (
            df.groupby("race_id")["umaban"]
            .transform("max")
            .fillna(1)
            .clip(lower=1)
            .astype(float)
        )
    denominator = (field_size - 1.0).clip(lower=1.0)  # 1頭立てのゼロ除算回避

    # ── 頭数正規化: (順位 - 1) / (頭数 - 1) → 0.0(先頭) 〜 1.0(最後尾) ──────
    c1_norm              = (c1_raw          - 1.0) / denominator
    c4_norm              = (c4_raw          - 1.0) / denominator
    first_corner_norm    = (first_corner_raw - 1.0) / denominator
    rank_norm            = (rank_valid       - 1.0) / denominator
    pos_advance_norm     = c4_norm - rank_norm   # 正値=追い込み、負値=失速

    df["_c1_norm"]            = c1_norm
    df["_c4_norm"]            = c4_norm
    df["_first_corner_norm"]  = first_corner_norm
    df["_pos_advance_norm"]   = pos_advance_norm

    # ── 上がり3F レース内順位 (生順位で保持) ─────────────────────────────────
    df["_go3f_valid"] = go3f_valid
    df["_go3f_rank"] = (
        df.groupby("race_id")["_go3f_valid"]
        .rank(method="min", ascending=True, na_option="keep")
    )

    # ── 距離区分マスク ─────────────────────────────────────────────────────────
    dist = df["distance"]
    for bucket, (lo, hi) in _DIST_BUCKETS.items():
        in_bucket = (dist >= lo) & (dist <= hi)
        df[f"_c1n_{bucket}"]  = c1_norm.where(in_bucket)
        df[f"_c4n_{bucket}"]  = c4_norm.where(in_bucket)
        df[f"_pad_{bucket}"]  = pos_advance_norm.where(in_bucket)

    # ── 馬場別マスク ──────────────────────────────────────────────────────────
    tc_str = df["track_code"].astype(str)
    df["_go3f_turf"] = df["_go3f_rank"].where(tc_str.str.startswith(_TURF_PREFIX))
    df["_go3f_dirt"] = df["_go3f_rank"].where(tc_str.str.startswith(_DIRT_PREFIX))

    # ── ソート: horse × date × race_id でリーク防止の基盤を確立 ─────────────
    df = df.sort_values(["horse_id", "race_date", "race_id"]).reset_index(drop=True)
    horse_grp = df.groupby("horse_id", sort=False)

    # ── Phase 1: 頭数正規化ベース特徴量 ───────────────────────────────────────
    df["avg_c1_norm_5"] = horse_grp["_c1_norm"].transform(
        lambda x: x.shift(1).rolling(5, min_periods=1).mean()
    )
    df["avg_c4_norm_5"] = horse_grp["_c4_norm"].transform(
        lambda x: x.shift(1).rolling(5, min_periods=1).mean()
    )
    df["avg_pos_advance_norm_5"] = horse_grp["_pos_advance_norm"].transform(
        lambda x: x.shift(1).rolling(5, min_periods=1).mean()
    )
    df["running_style_std_norm_5"] = horse_grp["_c1_norm"].transform(
        lambda x: x.shift(1).rolling(5, min_periods=2).std()
    )
    # 最初通過コーナー (c1→c2→c3→c4 優先): スプリントも含む全距離で有効
    df["avg_first_corner_norm_5"] = horse_grp["_first_corner_norm"].transform(
        lambda x: x.shift(1).rolling(5, min_periods=1).mean()
    )

    # ── Phase 2: 距離区分別脚質特徴量 ─────────────────────────────────────────
    for bucket in _BUCKET_NAMES:
        df[f"avg_c1_norm_5_{bucket}"] = horse_grp[f"_c1n_{bucket}"].transform(
            lambda x: x.shift(1).rolling(5, min_periods=1).mean()
        )
        df[f"avg_c4_norm_5_{bucket}"] = horse_grp[f"_c4n_{bucket}"].transform(
            lambda x: x.shift(1).rolling(5, min_periods=1).mean()
        )
        df[f"avg_pos_advance_norm_5_{bucket}"] = horse_grp[f"_pad_{bucket}"].transform(
            lambda x: x.shift(1).rolling(5, min_periods=1).mean()
        )

    # ── Phase 3: 馬場別上がり適性 ─────────────────────────────────────────────
    df["avg_go3f_rank_5_turf"] = horse_grp["_go3f_turf"].transform(
        lambda x: x.shift(1).rolling(5, min_periods=1).mean()
    )
    df["go3f_rank_std_5_turf"] = horse_grp["_go3f_turf"].transform(
        lambda x: x.shift(1).rolling(5, min_periods=2).std()
    )
    df["avg_go3f_rank_5_dirt"] = horse_grp["_go3f_dirt"].transform(
        lambda x: x.shift(1).rolling(5, min_periods=1).mean()
    )
    df["go3f_rank_std_5_dirt"] = horse_grp["_go3f_dirt"].transform(
        lambda x: x.shift(1).rolling(5, min_periods=2).std()
    )

    # ── 内部列削除・元の行順に復元 ────────────────────────────────────────────
    internal = [c for c in df.columns if c.startswith("_") and c != _ORD]
    df = df.drop(columns=internal)
    df = (
        df.sort_values(_ORD)
        .drop(columns=[_ORD])
        .reset_index(drop=True)
    )

    # ── ドメイン知識に基づく欠損値補完 ───────────────────────────────────────
    # 正規化コーナー位置: 0.5 (中団相当)
    for col in ["avg_c1_norm_5", "avg_c4_norm_5", "avg_first_corner_norm_5"]:
        df[col] = df[col].fillna(_FILL_NORM_POS)
    for bucket in _BUCKET_NAMES:
        df[f"avg_c1_norm_5_{bucket}"]  = df[f"avg_c1_norm_5_{bucket}"].fillna(_FILL_NORM_POS)
        df[f"avg_c4_norm_5_{bucket}"]  = df[f"avg_c4_norm_5_{bucket}"].fillna(_FILL_NORM_POS)

    # 正規化順位変化: 0.0 (動きなし)
    df["avg_pos_advance_norm_5"] = df["avg_pos_advance_norm_5"].fillna(_FILL_NORM_ADV)
    for bucket in _BUCKET_NAMES:
        df[f"avg_pos_advance_norm_5_{bucket}"] = (
            df[f"avg_pos_advance_norm_5_{bucket}"].fillna(_FILL_NORM_ADV)
        )

    # 正規化位置標準偏差: 0.15 (典型的なブレ幅)
    df["running_style_std_norm_5"] = df["running_style_std_norm_5"].fillna(_FILL_NORM_STD)

    # 上がり3F順位 (生順位): 8.0 (中位相当)
    df["avg_go3f_rank_5_turf"] = df["avg_go3f_rank_5_turf"].fillna(_FILL_RANK_GO3F)
    df["avg_go3f_rank_5_dirt"] = df["avg_go3f_rank_5_dirt"].fillna(_FILL_RANK_GO3F)
    df["go3f_rank_std_5_turf"] = df["go3f_rank_std_5_turf"].fillna(_FILL_STD_GO3F)
    df["go3f_rank_std_5_dirt"] = df["go3f_rank_std_5_dirt"].fillna(_FILL_STD_GO3F)

    return df


def validate_no_leakage(df_out: pd.DataFrame, horse_id: str) -> None:
    """特定馬の出力を時系列表示してリーク防止を目視確認する (開発用)。"""
    cols = [
        "race_date", "kakutei_chakujun", "distance", "track_code",
        "avg_c1_norm_5", "avg_c4_norm_5", "avg_pos_advance_norm_5",
        "avg_go3f_rank_5_turf", "avg_go3f_rank_5_dirt",
    ]
    available = [c for c in cols if c in df_out.columns]
    rows = df_out[df_out["horse_id"] == horse_id].sort_values("race_date")
    print(f"\n=== {horse_id} ({len(rows)} 走) ===")
    print(rows[available].to_string(index=False))
