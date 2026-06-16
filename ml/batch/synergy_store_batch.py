"""
web_service/batch/synergy_store_batch.py
==========================================
調教師×騎手 シナジー（独立性モデル）バッチ（v1.6）。

アルゴリズム:
  独立性モデル: 調教師と騎手が独立に能力を発揮すると仮定したとき、
  コンビの期待勝率は P_trainer × P_jockey / P_overall で表される。
  実際の勝率との差分 (synergy_shift) がプラスなら相性良し・マイナスなら相性悪し。

  synergy_win_shift  = combo_win_rate  - (trainer_win_rate  × jockey_win_rate  / overall_win_rate)
  synergy_top3_shift = combo_top3_rate - (trainer_top3_rate × jockey_top3_rate / overall_top3_rate)

MIN_SAMPLES: コンビ出走回数が 20 未満の場合は shift=0.0 を返す（推定不安定防止）。

Usage:
    batch = SynergyStoreBatch(engine=engine)
    n = batch.run(target_date=date.today())
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ml.batch.models import SynergyStore

_logger = logging.getLogger(__name__)

_MIN_SAMPLES: int = 20  # コンビ推定の最低出走回数


# ─────────────────────────────────────────────────────────────────────────────
# モジュールレベル純粋関数
# ─────────────────────────────────────────────────────────────────────────────


def compute_synergy_shift(
    combo_count: int,
    combo_win_rate: float,
    combo_top3_rate: float,
    trainer_win_rate: float,
    trainer_top3_rate: float,
    jockey_win_rate: float,
    jockey_top3_rate: float,
    overall_win_rate: float,
    overall_top3_rate: float,
    min_samples: int = _MIN_SAMPLES,
) -> tuple[Optional[float], Optional[float]]:
    """
    独立性モデルによるシナジーシフトを計算する。

    Args:
        combo_count:       コンビの累積出走回数
        combo_win_rate:    コンビの実際の勝率
        combo_top3_rate:   コンビの実際の3着内率
        trainer_win_rate:  調教師単体の勝率
        trainer_top3_rate: 調教師単体の3着内率
        jockey_win_rate:   騎手単体の勝率
        jockey_top3_rate:  騎手単体の3着内率
        overall_win_rate:  全体勝率（標準化の分母）
        overall_top3_rate: 全体3着内率（標準化の分母）
        min_samples:       信頼度閾値（これ未満は 0.0 を返す）

    Returns:
        (synergy_win_shift, synergy_top3_shift)

    計算式:
        expected_win  = trainer_win_rate  × jockey_win_rate  / overall_win_rate
        synergy_shift = combo_win_rate - expected_win

    特性:
        - 独立なら shift ≈ 0.0
        - overall_win_rate = 0.0 のときは 0.0 を返す（ゼロ除算防止）
        - combo_count < min_samples のときは 0.0 を返す（推定不安定防止）
    """
    if combo_count < min_samples:
        # データ不足は「平均シナジー(0.0)」ではなく「不明(None)」で返す。
        # モデルは LEFT JOIN 後の NaN として「情報なし」で扱う。
        return None, None

    if overall_win_rate <= 0.0:
        win_shift = 0.0
    else:
        expected_win = trainer_win_rate * jockey_win_rate / overall_win_rate
        win_shift = combo_win_rate - expected_win

    if overall_top3_rate <= 0.0:
        top3_shift = 0.0
    else:
        expected_top3 = trainer_top3_rate * jockey_top3_rate / overall_top3_rate
        top3_shift = combo_top3_rate - expected_top3

    return float(win_shift), float(top3_shift)


# ─────────────────────────────────────────────────────────────────────────────
# バッチクラス
# ─────────────────────────────────────────────────────────────────────────────


class SynergyStoreBatch:
    """
    target_date 未満のレース結果を集計し、synergy_store へ UPSERT するバッチ。

    Usage:
        batch = SynergyStoreBatch(engine=engine)
        rows_updated = batch.run(target_date=date.today())
    """

    def __init__(self, engine) -> None:
        self.engine = engine

    def run(self, target_date: date) -> int:
        """
        target_date 未満のレース結果からシナジー指標を計算し UPSERT する。

        Returns:
            UPSERT した行数。
        """
        _logger.info("[SynergyBatch] 開始: target_date=%s", target_date)

        race_df = self._fetch_race_results(target_date)
        if race_df.empty:
            _logger.warning("[SynergyBatch] 対象レースなし。スキップ。")
            return 0

        rows = self._compute_rows(race_df, target_date)
        if not rows:
            _logger.warning("[SynergyBatch] 集計結果なし。スキップ。")
            return 0

        self._upsert(rows)
        _logger.info("[SynergyBatch] 完了: %d 行 UPSERT", len(rows))
        return len(rows)

    # ── データ取得 ────────────────────────────────────────────────────────────

    def _fetch_race_results(self, target_date: date) -> pd.DataFrame:
        """
        race_entries_v2 + races_v2 を JOIN し、着順確定済みエントリーを取得する。

        フィルタ条件:
            - race_date < target_date（データリーク防止）
            - trainer_id / jockey_id 両方が NULL でない
            - kakutei_chakujun IS NOT NULL AND > 0
        """
        sql = text(f"""
            SELECT
                re.chokyosi_code AS trainer_id,
                re.kishu_code    AS jockey_id,
                CASE WHEN re.kakutei_chakujun = 1 THEN 1 ELSE 0 END  AS is_win,
                CASE WHEN re.kakutei_chakujun <= 3 THEN 1 ELSE 0 END AS is_top3
            FROM   race_entries_v2 re
            JOIN   races_v2        rv ON re.race_id = rv.race_id
            WHERE  to_date(rv.kaisai_year || rv.kaisai_monthday, 'YYYYMMDD') < '{target_date}'
              AND  re.kakutei_chakujun  IS NOT NULL
              AND  re.kakutei_chakujun  >  0
              AND  re.chokyosi_code     IS NOT NULL
              AND  re.kishu_code        IS NOT NULL
        """)
        with self.engine.connect() as conn:
            df = pd.read_sql(sql, conn)

        df["is_win"]  = df["is_win"].astype(int)
        df["is_top3"] = df["is_top3"].astype(int)
        return df

    # ── 集計・シナジー計算 ────────────────────────────────────────────────────

    def _compute_rows(self, df: pd.DataFrame, target_date: date) -> list[dict]:
        overall_total = len(df)
        if overall_total == 0:
            return []

        overall_win_rate  = df["is_win"].mean()
        overall_top3_rate = df["is_top3"].mean()

        # 調教師単体集計
        trainer_stats = (
            df.groupby("trainer_id")
            .agg(
                trainer_win_rate=("is_win",  "mean"),
                trainer_top3_rate=("is_top3", "mean"),
            )
        )

        # 騎手単体集計
        jockey_stats = (
            df.groupby("jockey_id")
            .agg(
                jockey_win_rate=("is_win",  "mean"),
                jockey_top3_rate=("is_top3", "mean"),
            )
        )

        # コンビ集計
        combo_stats = (
            df.groupby(["trainer_id", "jockey_id"])
            .agg(
                combo_count=("is_win",  "count"),
                combo_win_rate=("is_win",  "mean"),
                combo_top3_rate=("is_top3", "mean"),
            )
            .reset_index()
        )

        # JOIN して計算
        combo_stats = combo_stats.join(trainer_stats, on="trainer_id")
        combo_stats = combo_stats.join(jockey_stats,  on="jockey_id")
        combo_stats.fillna(0.0, inplace=True)

        rows: list[dict] = []
        for _, row in combo_stats.iterrows():
            win_shift, top3_shift = compute_synergy_shift(
                combo_count=int(row["combo_count"]),
                combo_win_rate=float(row["combo_win_rate"]),
                combo_top3_rate=float(row["combo_top3_rate"]),
                trainer_win_rate=float(row["trainer_win_rate"]),
                trainer_top3_rate=float(row["trainer_top3_rate"]),
                jockey_win_rate=float(row["jockey_win_rate"]),
                jockey_top3_rate=float(row["jockey_top3_rate"]),
                overall_win_rate=overall_win_rate,
                overall_top3_rate=overall_top3_rate,
            )
            rows.append({
                "target_date":        target_date,
                "trainer_id":         str(row["trainer_id"]),
                "jockey_id":          str(row["jockey_id"]),
                "combo_count":        int(row["combo_count"]),
                "combo_win_rate":     float(row["combo_win_rate"]),
                "combo_top3_rate":    float(row["combo_top3_rate"]),
                "synergy_win_shift":  win_shift,
                "synergy_top3_shift": top3_shift,
            })

        return rows

    # ── UPSERT ───────────────────────────────────────────────────────────────

    def _upsert(self, rows: list[dict]) -> None:
        if not rows:
            return
        table = SynergyStore.__table__
        stmt = pg_insert(table).values(rows)
        update_cols = {
            col.name: stmt.excluded[col.name]
            for col in table.columns
            if col.name not in ("id", "target_date", "trainer_id", "jockey_id")
        }
        stmt = stmt.on_conflict_do_update(
            constraint="uq_synergy_date_trainer_jockey",
            set_=update_cols,
        )
        with self.engine.begin() as conn:
            conn.execute(stmt)
