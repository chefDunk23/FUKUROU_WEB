"""Parquetロード → X / y / groups 組み立て"""
from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import pandas as pd

from .config import (
    FEATURES_APTITUDE,
    FEATURES_AUX,
    FEATURES_CHOKYO,
    FEATURES_DM_GAP,
    FEATURES_JOCKEY,
    FEATURES_PAST_PERF,
    FEATURES_PHYSICAL,
    FEATURES_RATING,
    FEATURES_SUBMODEL,
    FEATURES_TRAINER,
    FEATURES_TRAINING,
    GRADE_CODE_MAP,
    GROUP_COL,
    NUMERIC_CODE_COLS,
    TARGET_COL,
)


class Dataset(NamedTuple):
    X: pd.DataFrame
    y: pd.Series          # lambdarank: relevance (0/1/2) | binary: 0/1
    groups: pd.Series     # race_id（groupby用。str型のまま保持）
    raw: pd.DataFrame     # 数値変換済み全カラム（evaluate.py で tan_odds 等を参照）


def _resolve_feature_cols(
    df: pd.DataFrame,
    feature_override: list[str] | None = None,
) -> list[str]:
    """利用可能な特徴量カラムを動的解決する。

    V2 スタック Parquet（v2_stacked_features.parquet）には score_* 列が存在するため、
    その場合はサブモデルスコア 6 列のみを特徴量として使う（stacking モード）。
    それ以外は従来の flat モードで動作する（下位互換）。

    feature_override が指定されている場合はそのリストを優先する（サブモデル選択など）。
    """
    # 明示的な上書きが指定されている場合（例: training_v2/pedigree_v1 除外）
    if feature_override is not None:
        return [c for c in feature_override if c in df.columns]

    # stacking モード: 6 サブモデルスコア列がすべて揃っている場合
    if all(c in df.columns for c in FEATURES_SUBMODEL):
        return list(FEATURES_SUBMODEL)

    # flat モード: 旧 Parquet 用（下位互換）
    mandatory = (
        FEATURES_PHYSICAL
        + FEATURES_PAST_PERF
        + FEATURES_AUX
        + NUMERIC_CODE_COLS
        + ["grade_code"]
    )
    optional_groups = (
        FEATURES_DM_GAP
        + FEATURES_RATING
        + FEATURES_CHOKYO
        + FEATURES_APTITUDE
        + FEATURES_JOCKEY
        + FEATURES_TRAINER
        + FEATURES_TRAINING
    )
    available_optional = [c for c in optional_groups if c in df.columns]
    return mandatory + available_optional


def _prepare_numerics(df: pd.DataFrame) -> pd.DataFrame:
    """全カラムを適切な数値型に変換する。

    変換ルール:
      - grade_code: 英字コード("E","C","A","L","G") → GRADE_CODE_MAP で固定整数
      - NUMERIC_CODE_COLS: "01","02"等の数値文字列 → pd.to_numeric
      - object/str型の数値列(basis_weight, tan_odds等) → pd.to_numeric
    """
    df = df.copy()

    # grade_code: 英字 → 固定整数マッピング（未知コードはNaN）
    if "grade_code" in df.columns:
        df["grade_code"] = df["grade_code"].map(GRADE_CODE_MAP)

    # 数値文字列コード列
    for col in NUMERIC_CODE_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # その他のobject/str型数値列（basis_weight, tan_odds は元データでobject）
    for col in ["basis_weight", "tan_odds", "zen_3f", "go_3f",
                "horse_sex", "horse_age", "pace_type"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


def _to_relevance(chakujun: pd.Series) -> pd.Series:
    """着順 → 3段階relevanceラベル（lambdarank用、非負整数）
    1着=2, 2-3着=1, 4着以下=0
    """
    rel = pd.Series(0, index=chakujun.index, dtype=int)
    rel[chakujun == 1] = 2
    rel[(chakujun >= 2) & (chakujun <= 3)] = 1
    return rel


def load(
    parquet_path: str | Path,
    mode: str = "rank",
    feature_override: list[str] | None = None,
) -> Dataset:
    """
    Args:
        parquet_path: generate_pace_features.py が出力したParquetのパス
        mode: "rank"（lambdarank）| "binary_win"（単勝2値）

    Returns:
        Dataset(X, y, groups, raw)
    """
    df = pd.read_parquet(parquet_path)

    # 着順が確定していない行を除外
    df = df[df[TARGET_COL].notna() & (df[TARGET_COL] > 0)].copy()
    df = df.sort_values([GROUP_COL, "umaban"]).reset_index(drop=True)

    # 数値変換（全必要カラムを一括処理）
    df = _prepare_numerics(df)

    feature_cols = _resolve_feature_cols(df, feature_override=feature_override)
    missing = [c for c in feature_cols if c not in df.columns]
    if missing:
        raise KeyError(f"Parquetに存在しない特徴量カラム: {missing}")

    X = df[feature_cols].copy()

    chakujun = df[TARGET_COL].astype(int)
    if mode == "rank":
        y = _to_relevance(chakujun)
    elif mode == "binary_win":
        y = (chakujun == 1).astype(int)
    else:
        raise ValueError(f"未対応のmode: {mode!r}（'rank' または 'binary_win'）")

    return Dataset(X=X, y=y, groups=df[GROUP_COL], raw=df)
