"""
anaba_ai/pipeline.py
=====================
特徴量生成・残差ターゲット計算・時系列分割を担うパイプライン。

入力: bloodline_features_v1_2022plus.parquet（既存 Parquet）
出力: (df_A, df_B, df_C) の 3 分割 DataFrame

残差ターゲット:
    p_raw(i)   = 1 / tan_odds(i)          単純暗黙確率
    p_market(i) = p_raw(i) / Σ p_raw(j)  レース内正規化（控除率補正）
    y_actual(i) = 1 if 1着 else 0
    residual(i) = y_actual(i) - p_market(i)

リーク対策:
    - go3f_rank_in_race はレース完了後の情報だが、
      サブモデル学習では shift(1)+rolling で過去走のみ参照する
    - tan_odds はターゲット計算のみ。サブモデル特徴量には含めない
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from .config import (
    DEFAULT_PARQUET,
    EXCLUDE_COLS,
    MARKET_PROXY_COLS,
    SPLIT_A_END,
    SPLIT_B_END,
    SPLIT_B_START,
    SPLIT_C_START,
    SUBMODEL_DEFS,
)

log = logging.getLogger(__name__)


def _compute_go3f_rank_in_race(df: pd.DataFrame) -> pd.Series:
    """go_3f_time のレース内順位（1=最速）を計算。0/NaN は除外。"""
    go3f = pd.to_numeric(df["go_3f_time"], errors="coerce")
    go3f = go3f.where(go3f > 0)
    return (
        go3f.groupby(df["race_id"])
        .rank(method="min", ascending=True, na_option="keep")
    )


def _compute_market_prob(df: pd.DataFrame) -> pd.Series:
    """tan_odds からレース内正規化暗黙確率を計算。"""
    odds = pd.to_numeric(df["tan_odds"], errors="coerce")
    p_raw = (1.0 / odds).where(odds > 0)
    p_sum = p_raw.groupby(df["race_id"]).transform("sum")
    return (p_raw / p_sum).fillna(0.0)


def _filter_valid(df: pd.DataFrame) -> pd.DataFrame:
    """着順・オッズが確定している行のみ残す。"""
    odds  = pd.to_numeric(df["tan_odds"], errors="coerce")
    ranks = pd.to_numeric(df["kakutei_chakujun"], errors="coerce")
    mask = ranks.notna() & (ranks > 0) & odds.notna() & (odds > 0)
    n_before = len(df)
    df = df[mask].copy()
    log.info("有効フィルタ: %d → %d 行 (除外 %d 行)", n_before, len(df), n_before - len(df))
    return df


def _numeric_code_cols(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """カテゴリコード列を数値に変換。"""
    for col in cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def load_and_prepare(parquet_path: str | Path | None = None) -> pd.DataFrame:
    """
    Parquet を読み込んで穴馬AI 用 DataFrame を返す。

    追加するカラム:
        go3f_rank_in_race : レース内上がり3F順位
        p_market          : 市場暗黙確率（正規化後）
        residual          : 残差ターゲット = y_actual - p_market
        y_actual          : 1着=1 / 非1着=0
    """
    path = Path(parquet_path or DEFAULT_PARQUET)
    if not path.exists():
        raise FileNotFoundError(f"Parquet ファイルが見つかりません: {path}")

    log.info("Parquet 読み込み: %s", path.name)
    df = pd.read_parquet(path)
    log.info("読み込み完了: %d行 × %d列", *df.shape)

    # 日付型に統一
    df["race_date"] = pd.to_datetime(df["race_date"])

    # 有効行のみ
    df = _filter_valid(df)

    # 数値コード変換（カテゴリ → 整数）
    code_cols = ["keibajo_code", "track_code", "tenko_code", "shiba_baba_code",
                 "dirt_baba_code", "horse_sex", "pace_type", "course_kubun"]
    df = _numeric_code_cols(df, code_cols)

    # ── 派生特徴量 ───────────────────────────────────────────────────────────
    df["go3f_rank_in_race"] = _compute_go3f_rank_in_race(df)

    # ── 残差ターゲット ────────────────────────────────────────────────────────
    df["p_market"] = _compute_market_prob(df)
    df["y_actual"] = (df["kakutei_chakujun"] == 1).astype(float)
    df["residual"] = df["y_actual"] - df["p_market"]

    # ── 各サブモデルの利用可能な特徴量を検証 ─────────────────────────────────
    for sdef in SUBMODEL_DEFS:
        available = [c for c in sdef["features"] if c in df.columns]
        missing   = [c for c in sdef["features"] if c not in df.columns]
        if missing:
            log.warning("[%s] 欠損特徴量 %d列: %s", sdef["name"], len(missing), missing)
        log.info("[%s] 利用可能特徴量: %d 列", sdef["name"], len(available))

    log.info("パイプライン完了: %d 行", len(df))
    return df


def split_periods(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    時系列3分割を返す。

    Returns:
        df_A: サブモデル学習期間（〜 SPLIT_A_END）
        df_B: メタモデル学習期間（SPLIT_B_START 〜 SPLIT_B_END）
        df_C: ホールドアウト検証期間（SPLIT_C_START 〜）
    """
    a_end   = pd.Timestamp(SPLIT_A_END)
    b_start = pd.Timestamp(SPLIT_B_START)
    b_end   = pd.Timestamp(SPLIT_B_END)
    c_start = pd.Timestamp(SPLIT_C_START)

    df_A = df[df["race_date"] <= a_end].copy()
    df_B = df[(df["race_date"] >= b_start) & (df["race_date"] <= b_end)].copy()
    df_C = df[df["race_date"] >= c_start].copy()

    log.info(
        "分割完了 — A: %d行(%d races) / B: %d行(%d races) / C: %d行(%d races)",
        len(df_A), df_A["race_id"].nunique(),
        len(df_B), df_B["race_id"].nunique(),
        len(df_C), df_C["race_id"].nunique(),
    )
    return df_A, df_B, df_C


def get_feature_cols(submodel_name: str, df: pd.DataFrame) -> list[str]:
    """指定サブモデルの有効特徴量カラムリストを返す。"""
    for sdef in SUBMODEL_DEFS:
        if sdef["name"] == submodel_name:
            return [c for c in sdef["features"] if c in df.columns]
    raise KeyError(f"未知のサブモデル名: {submodel_name!r}")
