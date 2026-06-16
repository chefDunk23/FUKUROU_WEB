"""
web_service/batch/training_feature_batch.py
==============================================
調教 T スコア・プロセスベクトル計算バッチ（v1.6）。

アルゴリズム:
  1. training_data から target_date 前 14 日分の調教記録を取得
  2. (date, center, course_type) グループ内で T スコア (T = 50 + 10 × z) を計算
     WC は各コースポジション (A~E) のタイム補正を施してから正規化
  3. 馬ごとに以下のプロセスベクトルを算出:
       session_count  : 14日内のセッション数
       slope_ratio    : 坂路セッション比率
       best_z_total   : T スコア最大値 (min/max 方向は小さいほど高速)
       latest_z_total : 最終セッションの T スコア
       z_trend_slope  : T スコアの線形トレンド係数（session_count <= 2 のとき NaN）
       avg_accel      : 平均加速度指標 (lap1_time − lap2_time の平均; 後半速い = 正値)
       latest_accel   : 最終セッションの加速度
  4. training_feature_store へ UPSERT

Usage:
    batch = TrainingFeatureBatch(engine=engine)
    n = batch.run(target_date=date.today())
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ml.batch.models import TrainingFeatureStore

_logger = logging.getLogger(__name__)

_WINDOW_DAYS: int = 14          # 直前何日分の調教を使うか
_MIN_SESSIONS_FOR_SLOPE: int = 3  # z_trend_slope を計算する最低セッション数（≤2 → NaN）

# WC コースポジション別タイム補正値（単位: 秒）
_WC_COURSE_CORRECTION: dict[str, float] = {
    "A": 0.0,
    "B": 0.3,
    "C": 0.6,
    "D": 0.9,
    "E": 1.2,
}


# ─────────────────────────────────────────────────────────────────────────────
# モジュールレベル純粋関数
# ─────────────────────────────────────────────────────────────────────────────


def normalize_course_type(
    course_type: Optional[str],
    time_total: float,
) -> float:
    """
    WC コースポジション補正を施したタイムを返す。

    '坂路'（坂路コース）は補正なし。
    WC（ウッドチップ）は A~E の位置によるインコース有利分を補正し、
    フラットな比較を可能にする。

    DB 実値（training_data.course）は以下のいずれか:
        '坂路' → 坂路コース (補正なし)
        'A', 'B', 'C', 'D', 'E' → WC コースポジション（補正あり）
    旧形式 'WC_A' 等は後方互換として残す。

    Args:
        course_type: DB の course 値（例: '坂路', 'C', 'D', 'WC_A'）
        time_total:  コースタイム（秒）

    Returns:
        補正後タイム（秒）。WC 以外または不明の場合は補正なし。
    """
    if not course_type:
        return time_total
    ct = str(course_type).strip().upper()
    # DB実値: 'A'〜'E' の単一文字がWCコースポジション
    if ct in _WC_COURSE_CORRECTION:
        return time_total + _WC_COURSE_CORRECTION[ct]
    # 旧形式 'WC_A'〜'WC_E' への後方互換
    if ct.startswith("WC_") and len(ct) == 4 and ct[-1] in _WC_COURSE_CORRECTION:
        return time_total + _WC_COURSE_CORRECTION[ct[-1]]
    return time_total


def add_t_scores_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    (training_date, center_id, course_type) グループ内で T スコアを計算して列追加する。

    T = 50 + 10 × (μ_group − time_corrected) / σ_group
    ※ 時計が速い（小さい）ほど T スコアが高い。

    Args:
        df: 必須列 ['training_date', 'center_id', 'course_type', 'time_corrected']

    Returns:
        't_score_total' 列を追加した新しい DataFrame（元 df は変更しない）。
        グループ内 σ == 0 の場合は T = 50.0 を返す。
    """
    result = df.copy()
    result["t_score_total"] = np.nan

    grp_cols = ["training_date", "center_id", "course_type"]
    for _, grp_idx in result.groupby(grp_cols).groups.items():
        times = result.loc[grp_idx, "time_corrected"]
        mu = times.mean()
        sigma = times.std(ddof=1)
        if pd.isna(sigma) or sigma == 0.0:
            result.loc[grp_idx, "t_score_total"] = 50.0
        else:
            result.loc[grp_idx, "t_score_total"] = 50.0 + 10.0 * (mu - times) / sigma

    return result


def compute_z_trend_slope(t_scores: pd.Series) -> Optional[float]:
    """
    T スコア時系列の線形トレンド係数を計算する。

    セッション番号を説明変数（0, 1, 2, ...）、T スコアを被説明変数として
    最小二乗回帰を行い、傾きを返す。

    Args:
        t_scores: 時系列順に並んだ T スコア Series

    Returns:
        傾き（正 = 調子上向き）。n <= 2 のときは None（NaN 相当）。
    """
    n = len(t_scores.dropna())
    if n <= _MIN_SESSIONS_FOR_SLOPE - 1:  # i.e., n <= 2
        return None

    x = np.arange(len(t_scores), dtype=float)
    y = t_scores.values.astype(float)
    valid = ~np.isnan(y)
    if valid.sum() < _MIN_SESSIONS_FOR_SLOPE:
        return None

    x_v = x[valid]
    y_v = y[valid]
    # 最小二乗法（np.polyfit degree=1）
    slope = float(np.polyfit(x_v, y_v, 1)[0])
    return slope


def compute_process_vector(
    horse_df: pd.DataFrame,
) -> dict[str, Optional[float]]:
    """
    1 頭分の調教 DataFrame からプロセスベクトルを計算する。

    Args:
        horse_df: 1 頭分の調教記録（training_date 昇順ソート済み）。
                  必須列: ['training_date', 'course_type', 't_score_total',
                           'lap1_time', 'lap2_time']

    Returns:
        プロセスベクトル dict（キーは TrainingFeatureStore のカラム名に対応）。
        値が算出不能な場合は None。
    """
    if horse_df.empty:
        return {
            "session_count":  0,
            "slope_ratio":    None,
            "best_z_total":   None,
            "latest_z_total": None,
            "z_trend_slope":  None,
            "avg_accel":      None,
            "latest_accel":   None,
        }

    df = horse_df.sort_values("training_date")

    session_count = len(df)

    # 坂路セッション比率（DB値: '坂路'）
    is_slope = df["course_type"].astype(str) == "坂路"
    slope_ratio = float(is_slope.mean()) if session_count > 0 else None

    t_scores = df["t_score_total"]
    best_z_total   = float(t_scores.max())   if t_scores.notna().any() else None
    latest_z_total = float(t_scores.iloc[-1]) if t_scores.notna().any() else None

    z_trend_slope = compute_z_trend_slope(t_scores)

    # 加速度指標: lap1 − lap2（後半が速い = 正値）
    # lap1 = 前半ラップ、lap2 = 後半ラップ
    has_lap = df["lap1_time"].notna() & df["lap2_time"].notna()
    if has_lap.any():
        accel_series = df.loc[has_lap, "lap1_time"] - df.loc[has_lap, "lap2_time"]
        avg_accel    = float(accel_series.mean())
        latest_valid = df.loc[has_lap, "training_date"].max()
        latest_accel = float(
            accel_series.loc[df.loc[has_lap, "training_date"] == latest_valid].iloc[-1]
        )
    else:
        avg_accel    = None
        latest_accel = None

    return {
        "session_count":  session_count,
        "slope_ratio":    slope_ratio,
        "best_z_total":   best_z_total,
        "latest_z_total": latest_z_total,
        "z_trend_slope":  z_trend_slope,
        "avg_accel":      avg_accel,
        "latest_accel":   latest_accel,
    }


# ─────────────────────────────────────────────────────────────────────────────
# バッチクラス
# ─────────────────────────────────────────────────────────────────────────────


class TrainingFeatureBatch:
    """
    target_date の直前 14 日間の調教記録から T スコア・プロセスベクトルを計算し、
    training_feature_store へ UPSERT するバッチ。

    Usage:
        batch = TrainingFeatureBatch(engine=engine)
        rows_updated = batch.run(target_date=date.today())
    """

    def __init__(self, engine) -> None:
        self.engine = engine

    def run(self, target_date: date) -> int:
        """
        Returns:
            UPSERT した行数。
        """
        _logger.info("[TrainingFeatureBatch] 開始: target_date=%s", target_date)

        window_start = target_date - timedelta(days=_WINDOW_DAYS)
        df_raw = self._fetch_training_data(window_start, target_date)

        if df_raw.empty:
            _logger.warning("[TrainingFeatureBatch] 調教データなし。スキップ。")
            return 0

        # タイム補正
        df_raw["time_corrected"] = df_raw.apply(
            lambda r: normalize_course_type(r["course_type"], r["time_total"]),
            axis=1,
        )

        # T スコア計算（全馬一括・グループ内正規化）
        df_scored = add_t_scores_df(df_raw)

        # 馬ごとにプロセスベクトルを算出
        rows: list[dict] = []
        for horse_id, horse_df in df_scored.groupby("horse_id"):
            vec = compute_process_vector(horse_df)
            rows.append({
                "horse_id":       str(horse_id),
                "target_date":    target_date,
                **vec,
            })

        if not rows:
            return 0

        self._upsert(rows)
        _logger.info("[TrainingFeatureBatch] 完了: %d 行 UPSERT", len(rows))
        return len(rows)

    # ── データ取得 ────────────────────────────────────────────────────────────

    def _fetch_training_data(
        self,
        window_start: date,
        target_date: date,
    ) -> pd.DataFrame:
        """
        training_data テーブルから調教記録を取得する。

        フィルタ条件:
            - training_date >= window_start（直前 14 日間）
            - training_date <  target_date（データリーク防止）
        """
        sql = text(f"""
            SELECT
                horse_id,
                date         AS training_date,
                center       AS center_id,
                course       AS course_type,
                time_total,
                lap_1        AS lap1_time,
                lap_2        AS lap2_time
            FROM   training_data
            WHERE  date        >= '{window_start}'
              AND  date        <  '{target_date}'
              AND  horse_id    IS NOT NULL
              AND  time_total  IS NOT NULL
            ORDER BY horse_id, date ASC
        """)
        with self.engine.connect() as conn:
            df = pd.read_sql(sql, conn)

        df["training_date"] = pd.to_datetime(df["training_date"]).dt.date
        df["time_total"]    = pd.to_numeric(df["time_total"],  errors="coerce")
        df["lap1_time"]     = pd.to_numeric(df["lap1_time"],   errors="coerce")
        df["lap2_time"]     = pd.to_numeric(df["lap2_time"],   errors="coerce")
        return df.dropna(subset=["time_total"])

    # ── UPSERT ───────────────────────────────────────────────────────────────

    def _upsert(self, rows: list[dict]) -> None:
        if not rows:
            return
        table = TrainingFeatureStore.__table__
        stmt = pg_insert(table).values(rows)
        update_cols = {
            col.name: stmt.excluded[col.name]
            for col in table.columns
            if col.name not in ("id", "horse_id", "target_date")
        }
        stmt = stmt.on_conflict_do_update(
            constraint="uq_training_fs_horse_date",
            set_=update_cols,
        )
        with self.engine.begin() as conn:
            conn.execute(stmt)
