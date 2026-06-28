"""
pace_bias_ai/pipeline.py
==========================
展開 × バイアス エンジン: 第1層（数値化）パイプライン。

既存コードの流用（変更なし）:
    - src/features/pace_features_v4.py  → 脚質特徴量 (c4_norm等)
    - src/features/pace_simulation_v1.py → 隊列予想 (predicted_position_norm等)
    - course_profile_store              → 競馬場バイアス

新規実装（本モジュール）:
    - pace_bias_ai/features/layer1_horse.py → 自在タイプ / 隠れ末脚 / 開幕週等
    - pace_bias_ai/features/layer1_bias.py  → 当日/前日バイアス付与

Usage (Parquet一括処理):
    from pace_bias_ai.pipeline import build_layer1_features
    df_out = build_layer1_features(df)

Usage (DBあり: バイアス情報も付与):
    from pace_bias_ai.pipeline import build_layer1_features_with_db
    df_out = build_layer1_features_with_db(df, conn)
"""
from __future__ import annotations

import logging
from pathlib import Path
import sys

import pandas as pd

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.features.pace_features_v4 import PACE_V4_COLS, create_pace_features_v4
from src.features.pace_simulation_v1 import PACE_SIM_COLS, create_pace_simulation_features
from pace_bias_ai.features.layer1_horse import LAYER1_HORSE_COLS, create_layer1_horse_features
from pace_bias_ai.features.layer1_bias import (
    BIAS_FEATURE_COLS,
    compute_venue_bias_features,
    compute_day_bias_features,
    attach_prev_week_bias_to_df,
)

log = logging.getLogger(__name__)

# 第1層の全出力カラム（既存 + 新規）
LAYER1_ALL_COLS: list[str] = (
    PACE_V4_COLS           # 脚質特徴量 (20列)
    + PACE_SIM_COLS        # 隊列予想 (3列)
    + LAYER1_HORSE_COLS    # 馬単位新規 (7列)
    + BIAS_FEATURE_COLS    # バイアス特徴量 (7列)
)


def build_layer1_features(df: pd.DataFrame) -> pd.DataFrame:
    """DBなし版: Parquet 一括処理向け。

    バイアス情報（day_front_bias_pit 等）はデフォルト値で埋まる。
    pace_features_v4 + pace_simulation + layer1_horse のみ計算。

    Args:
        df: 1馬1レース1行。必須カラムは各サブモジュールの docstring 参照。

    Returns:
        LAYER1_ALL_COLS を追加した DataFrame
    """
    log.info("[Layer1] 第1層特徴量生成開始: %d行", len(df))

    # Step1: 脚質特徴量 (既存)
    log.info("[Layer1] Step1: pace_features_v4")
    df = create_pace_features_v4(df)

    # Step2: 隊列予想 (既存)
    log.info("[Layer1] Step2: pace_simulation_v1")
    if all(c in df.columns for c in ["avg_c1_norm_5", "umaban"]):
        df = create_pace_simulation_features(df)
    else:
        log.warning("[Layer1] avg_c1_norm_5 が未生成 — pace_simulation をスキップ")
        for col in PACE_SIM_COLS:
            if col not in df.columns:
                df[col] = 0.5

    # Step3: 馬単位新規特徴量 (新規)
    log.info("[Layer1] Step3: layer1_horse_features")
    df = create_layer1_horse_features(df)

    # Step4: バイアス特徴量 (DBなし → デフォルト値)
    log.info("[Layer1] Step4: bias_features (DB なし → デフォルト)")
    df = compute_venue_bias_features(df, conn=None)
    df = compute_day_bias_features(df, conn=None)
    df = attach_prev_week_bias_to_df(df, conn=None)

    log.info("[Layer1] 完了: %d列追加", len(LAYER1_ALL_COLS))
    return df


def build_layer1_features_with_db(df: pd.DataFrame, conn) -> pd.DataFrame:
    """DBあり版: 当日バイアス等をDBから実取得する。

    Args:
        df   : 1馬1レース1行
        conn : psycopg2 接続（race_entries_v2, races_v2, track_bias_pit へのアクセス）

    Returns:
        LAYER1_ALL_COLS を追加した DataFrame
    """
    log.info("[Layer1+DB] 第1層特徴量生成開始: %d行", len(df))

    # Step1〜3: DBなし版と同じ
    df = create_pace_features_v4(df)

    if all(c in df.columns for c in ["avg_c1_norm_5", "umaban"]):
        df = create_pace_simulation_features(df)
    else:
        for col in PACE_SIM_COLS:
            if col not in df.columns:
                df[col] = 0.5

    df = create_layer1_horse_features(df)

    # Step4: バイアス特徴量 (DBから実取得)
    log.info("[Layer1+DB] Step4: bias_features (DB あり)")
    df = compute_venue_bias_features(df, conn=conn)
    df = compute_day_bias_features(df, conn=conn)
    df = attach_prev_week_bias_to_df(df, conn=conn)

    log.info("[Layer1+DB] 完了")
    return df


def validate_layer1_output(df: pd.DataFrame) -> dict[str, float]:
    """第1層出力の NaN率を検証して返す（デバッグ・品質確認用）。"""
    result: dict[str, float] = {}
    for col in LAYER1_ALL_COLS:
        if col in df.columns:
            result[col] = float(df[col].isna().mean() * 100)
        else:
            result[col] = 100.0  # 列自体がない
    return result
