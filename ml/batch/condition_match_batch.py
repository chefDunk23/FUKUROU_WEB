"""
web_service/batch/condition_match_batch.py
============================================
コンディション合致スコア（WPP マッチング）計算バッチ（v1.6）。

アルゴリズム:
  WPP (Winning Pattern Profile) = 過去勝利時のプロセスベクトルの重心。
  重み付き L1 距離を使って、現在のプロセスベクトルと WPP の距離を計算し、
  condition_match_score = 1 / (1 + L1_dist) に変換する（0~1、高いほど合致）。

  プロセスベクトル（6 次元）:
    - session_count  (w=0.15)
    - slope_ratio    (w=0.10)
    - best_z_total   (w=0.25)
    - latest_z_total (w=0.20)
    - z_trend_slope  (w=0.15)
    - avg_accel      (w=0.15)

  is_reliable = win_pattern_count >= 3（WPP の信頼性フラグ）

計算フロー:
  1. 対象馬全馬の過去全勝利レース日を取得
  2. 各勝利日の直前 14 日の調教データを一括取得し T スコアを計算
  3. 各勝利時のプロセスベクトルを算出 → WPP（重心）を計算
  4. 現在（target_date 直前 14 日）のプロセスベクトルを計算
  5. L1 距離計算 → match_score → UPSERT

Usage:
    batch = ConditionMatchBatch(engine=engine)
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

from ml.batch.training_feature_batch import (
    add_t_scores_df,
    compute_process_vector,
    normalize_course_type,
)
from ml.batch.models import ConditionMatchStore

_logger = logging.getLogger(__name__)

_WINDOW_DAYS: int = 14
_MIN_RELIABLE_WINS: int = 3  # is_reliable = True とする最低勝利数

# プロセスベクトル次元名と重み（合計 1.0）
_VECTOR_WEIGHTS: dict[str, float] = {
    "best_z_total":   0.25,
    "latest_z_total": 0.20,
    "z_trend_slope":  0.15,
    "avg_accel":      0.15,
    "session_count":  0.15,
    "slope_ratio":    0.10,
}

_VECTOR_DIMS = list(_VECTOR_WEIGHTS.keys())


# ─────────────────────────────────────────────────────────────────────────────
# モジュールレベル純粋関数
# ─────────────────────────────────────────────────────────────────────────────


def aggregate_wpp(
    win_vectors: list[dict[str, Optional[float]]],
) -> Optional[dict[str, float]]:
    """
    過去勝利時のプロセスベクトルリストから WPP（重心）を計算する。

    Args:
        win_vectors: 勝利時プロセスベクトルのリスト

    Returns:
        各次元の平均値 dict。リストが空または全次元 None の場合は None。
    """
    if not win_vectors:
        return None

    wpp: dict[str, float] = {}
    for dim in _VECTOR_DIMS:
        vals = [
            v[dim]
            for v in win_vectors
            if v.get(dim) is not None and not (
                isinstance(v[dim], float) and np.isnan(v[dim])
            )
        ]
        if vals:
            wpp[dim] = float(np.mean(vals))
        else:
            wpp[dim] = np.nan

    if all(np.isnan(v) for v in wpp.values()):
        return None

    return wpp


def compute_match_score(
    current_vector: dict[str, Optional[float]],
    wpp: dict[str, float],
    weights: dict[str, float] = _VECTOR_WEIGHTS,
) -> float:
    """
    現在のプロセスベクトルと WPP の重み付き L1 距離から match_score を計算する。

    match_score = 1 / (1 + L1_weighted_dist)

    Args:
        current_vector: 現在のプロセスベクトル
        wpp:            WPP（勝利時重心）
        weights:        各次元の重み（合計 1.0 推奨）

    Returns:
        match_score (0.0 ~ 1.0)。
        完全一致なら 1.0。両者の次元が全て NaN なら 0.0 を返す。
    """
    total_dist = 0.0
    total_weight = 0.0

    for dim, w in weights.items():
        cur_val = current_vector.get(dim)
        wpp_val = wpp.get(dim)

        # どちらかが None / NaN なら、その次元をスキップ
        if cur_val is None or wpp_val is None:
            continue
        if isinstance(cur_val, float) and np.isnan(cur_val):
            continue
        if isinstance(wpp_val, float) and np.isnan(wpp_val):
            continue

        total_dist   += w * abs(float(cur_val) - float(wpp_val))
        total_weight += w

    if total_weight == 0.0:
        return 0.0

    # 有効次元のみで正規化した重み付き L1 距離
    normalized_dist = total_dist / total_weight
    return float(1.0 / (1.0 + normalized_dist))


# ─────────────────────────────────────────────────────────────────────────────
# バッチクラス
# ─────────────────────────────────────────────────────────────────────────────


class ConditionMatchBatch:
    """
    target_date における各馬の WPP マッチングスコアを計算し、
    condition_match_store へ UPSERT するバッチ。

    Usage:
        batch = ConditionMatchBatch(engine=engine)
        rows_updated = batch.run(target_date=date.today())
    """

    def __init__(self, engine) -> None:
        self.engine = engine

    def run(
        self,
        target_date: date,
        horse_ids_filter: list[str] | None = None,
    ) -> int:
        """
        Args:
            target_date:      予測対象日。
            horse_ids_filter: 処理対象馬 ID リスト。None のとき全勝利馬を対象にする。
                              レース当日の出走馬に絞ることで実行時間を大幅短縮できる。

        Returns:
            UPSERT した行数。
        """
        _logger.info(
            "[ConditionMatchBatch] 開始: target_date=%s, filter=%d頭",
            target_date,
            len(horse_ids_filter) if horse_ids_filter else -1,
        )

        # 対象馬と過去勝利日を取得
        win_dates_df = self._fetch_win_dates(target_date, horse_ids_filter)
        if win_dates_df.empty:
            _logger.warning("[ConditionMatchBatch] 対象勝利履歴なし。スキップ。")
            return 0

        horse_ids = win_dates_df["horse_id"].unique().tolist()

        # 現在のプロセスベクトル（target_date 直前 14 日）を計算
        current_vectors = self._compute_current_vectors(horse_ids, target_date)

        # WPP を計算
        rows = self._compute_wpp_rows(win_dates_df, current_vectors, target_date)
        if not rows:
            _logger.warning("[ConditionMatchBatch] 集計結果なし。スキップ。")
            return 0

        self._upsert(rows)
        _logger.info("[ConditionMatchBatch] 完了: %d 行 UPSERT", len(rows))
        return len(rows)

    # ── データ取得 ────────────────────────────────────────────────────────────

    def _fetch_win_dates(
        self,
        target_date: date,
        horse_ids_filter: list[str] | None = None,
    ) -> pd.DataFrame:
        """
        target_date 未満に勝利実績のある馬と勝利レース日を取得する。

        リーク防止: race_date < target_date のみ対象。
        horse_ids_filter が指定された場合、その馬のみを対象にする（高速化）。
        """
        filter_clause = ""
        if horse_ids_filter:
            ids_literal = ",".join(f"'{hid}'" for hid in horse_ids_filter)
            filter_clause = f"AND  re.blood_no IN ({ids_literal})"

        sql = text(f"""
            SELECT
                re.blood_no AS horse_id,
                to_date(rv.kaisai_year || rv.kaisai_monthday, 'YYYYMMDD') AS race_date
            FROM   race_entries_v2 re
            JOIN   races_v2        rv ON re.race_id = rv.race_id
            WHERE  to_date(rv.kaisai_year || rv.kaisai_monthday, 'YYYYMMDD') < '{target_date}'
              AND  re.kakutei_chakujun =  1
              AND  re.blood_no         IS NOT NULL
              AND  re.blood_no         <> '0000000000'
              {filter_clause}
            ORDER BY re.blood_no, race_date ASC
        """)
        with self.engine.connect() as conn:
            df = pd.read_sql(sql, conn)

        df["race_date"] = pd.to_datetime(df["race_date"]).dt.date
        return df

    def _fetch_training_window(
        self,
        horse_ids: list[str],
        window_start: date,
        window_end: date,
    ) -> pd.DataFrame:
        """
        指定期間・指定馬の調教データを一括取得する（T スコア計算用）。
        """
        if not horse_ids:
            return pd.DataFrame()

        ids_literal = ",".join(f"'{hid}'" for hid in horse_ids)
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
              AND  date        <  '{window_end}'
              AND  horse_id    IN ({ids_literal})
              AND  time_total  IS NOT NULL
            ORDER BY horse_id, date ASC
        """)
        with self.engine.connect() as conn:
            df = pd.read_sql(sql, conn)

        if df.empty:
            return df

        df["training_date"] = pd.to_datetime(df["training_date"]).dt.date
        df["time_total"]    = pd.to_numeric(df["time_total"],  errors="coerce")
        df["lap1_time"]     = pd.to_numeric(df["lap1_time"],   errors="coerce")
        df["lap2_time"]     = pd.to_numeric(df["lap2_time"],   errors="coerce")
        df["time_corrected"] = df.apply(
            lambda r: normalize_course_type(r["course_type"], r["time_total"]),
            axis=1,
        )
        return df.dropna(subset=["time_total"])

    # ── プロセスベクトル計算 ──────────────────────────────────────────────────

    def _compute_current_vectors(
        self,
        horse_ids: list[str],
        target_date: date,
    ) -> dict[str, dict]:
        """現在（target_date 直前 14 日）のプロセスベクトルを馬ごとに計算する。"""
        window_start = target_date - timedelta(days=_WINDOW_DAYS)
        df_raw = self._fetch_training_window(horse_ids, window_start, target_date)

        if df_raw.empty:
            return {hid: compute_process_vector(pd.DataFrame()) for hid in horse_ids}

        df_scored = add_t_scores_df(df_raw)

        vectors: dict[str, dict] = {}
        for hid in horse_ids:
            horse_df = df_scored[df_scored["horse_id"] == hid]
            vectors[hid] = compute_process_vector(horse_df)

        return vectors

    def _compute_win_vector_for_date(
        self,
        horse_id: str,
        win_date: date,
        df_all: pd.DataFrame,
    ) -> dict[str, Optional[float]]:
        """
        特定の勝利日における調教プロセスベクトルを返す。

        df_all は全期間の調教データ（スコア済み）。
        window_start = win_date - 14 days の期間でフィルタして計算。
        """
        window_start = win_date - timedelta(days=_WINDOW_DAYS)
        horse_df = df_all[
            (df_all["horse_id"] == horse_id)
            & (df_all["training_date"] >= window_start)
            & (df_all["training_date"] < win_date)
        ]
        return compute_process_vector(horse_df)

    def _compute_wpp_rows(
        self,
        win_dates_df: pd.DataFrame,
        current_vectors: dict[str, dict],
        target_date: date,
    ) -> list[dict]:
        """
        各馬の WPP を計算し、match_score を算出して dict リストを返す。
        """
        # 全過去勝利日に跨る調教データを一括取得
        horse_ids = win_dates_df["horse_id"].unique().tolist()
        min_win_date = win_dates_df["race_date"].min()
        hist_start = min_win_date - timedelta(days=_WINDOW_DAYS)

        df_hist_raw = self._fetch_training_window(horse_ids, hist_start, target_date)
        if not df_hist_raw.empty:
            df_hist = add_t_scores_df(df_hist_raw)
        else:
            df_hist = pd.DataFrame()

        rows: list[dict] = []
        for horse_id, horse_wins in win_dates_df.groupby("horse_id"):
            hid = str(horse_id)
            win_dates = horse_wins["race_date"].tolist()

            # 勝利時プロセスベクトルを収集
            win_vectors: list[dict] = []
            for wd in win_dates:
                if df_hist.empty:
                    vec = compute_process_vector(pd.DataFrame())
                else:
                    vec = self._compute_win_vector_for_date(hid, wd, df_hist)
                win_vectors.append(vec)

            wpp = aggregate_wpp(win_vectors)
            current_vec = current_vectors.get(hid, compute_process_vector(pd.DataFrame()))

            if wpp is None:
                match_score = 0.0
            else:
                match_score = compute_match_score(current_vec, wpp)

            win_pattern_count = len(win_dates)
            is_reliable = win_pattern_count >= _MIN_RELIABLE_WINS

            rows.append({
                "horse_id":            hid,
                "target_date":         target_date,
                "condition_match_score": match_score,
                "win_pattern_count":   win_pattern_count,
                "is_reliable":         is_reliable,
            })

        return rows

    # ── UPSERT ───────────────────────────────────────────────────────────────

    def _upsert(self, rows: list[dict]) -> None:
        if not rows:
            return
        table = ConditionMatchStore.__table__
        stmt = pg_insert(table).values(rows)
        update_cols = {
            col.name: stmt.excluded[col.name]
            for col in table.columns
            if col.name not in ("id", "horse_id", "target_date")
        }
        stmt = stmt.on_conflict_do_update(
            constraint="uq_condition_match_horse_date",
            set_=update_cols,
        )
        with self.engine.begin() as conn:
            conn.execute(stmt)
