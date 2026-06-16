"""
web_service/batch/course_profile_store.py
==========================================
コース特性プロファイル（v1.4）の日次バッチ集計。

設計方針:
  - 競馬場(place_code) × 距離(distance) × 馬場種別(surface) の 3 次元キーで集計
  - race_entries + races を JOIN し、「target_date の前日以前」のみを参照（データリーク防止）
  - 枠番バイアス（1〜8枠）と脚質バイアス（逃/先/差/追）を勝率シフトとして保存
  - _MIN_SAMPLES 未満のセルは shift = None（モデルが NaN として扱う）
  - UPSERT によるべき等性: 同日に再実行しても結果が変わらない
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import NamedTuple, Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ml.batch.models import CourseProfileStore

_logger = logging.getLogger(__name__)

_MIN_SAMPLES = 30  # シフト値算出に必要な最低サンプル数


# ─────────────────────────────────────────────────────────────────────────────
# 内部データクラス
# ─────────────────────────────────────────────────────────────────────────────
class _Rates(NamedTuple):
    count: int
    win_rate: Optional[float]
    top3_rate: Optional[float]

    @classmethod
    def from_series(cls, rank_series: pd.Series) -> "_Rates":
        valid = pd.to_numeric(rank_series, errors="coerce").dropna()
        count = len(valid)
        if count == 0:
            return cls(count=0, win_rate=None, top3_rate=None)
        return cls(
            count=count,
            win_rate=float((valid == 1).sum() / count),
            top3_rate=float((valid <= 3).sum() / count),
        )


# ─────────────────────────────────────────────────────────────────────────────
# バッチクラス
# ─────────────────────────────────────────────────────────────────────────────
class CourseProfileStoreBatch:
    """
    コース特性プロファイルを日次で全量再計算し UPSERT するバッチ。

    Usage:
        batch = CourseProfileStoreBatch(target_date=date.today(), engine=engine)
        batch.run()
    """

    def __init__(self, target_date: date, engine) -> None:
        self.target_date = target_date
        self.engine = engine

    def run(self) -> int:
        """集計・UPSERT を実行し、挿入/更新件数を返す。"""
        _logger.info("[CourseProfile] 集計開始: target_date=%s", self.target_date)
        raw = self._fetch_race_results()
        if raw.empty:
            _logger.warning("[CourseProfile] 対象データなし。スキップ。")
            return 0
        rows = self._aggregate_course(raw)
        count = self._upsert(rows)
        _logger.info("[CourseProfile] 完了: %d件 UPSERT", count)
        return count

    # ── データ取得 ────────────────────────────────────────────────────────────

    def _fetch_race_results(self) -> pd.DataFrame:
        """
        race_entries_v2 + races_v2 を JOIN し、target_date 前の確定成績を取得する。

        track_code        : 10-22→'turf', 51-59→'turf', それ以外→'dirt'
        bracket_number    : 枠番 1〜8（race_entries_v2.wakuban、未取込分は NULL）
        running_style     : corner_4 を Proxy として動的推定
                            1 → '1'(逃), 2-3 → '2'(先行), 4-7 → '3'(差し), 8+ → '4'(追込)
        """
        sql = text(f"""
            WITH base AS (
                SELECT
                    rv.keibajo_code                               AS place_code,
                    rv.distance,
                    CASE
                        WHEN rv.track_code::int BETWEEN 10 AND 22 THEN 'turf'
                        WHEN rv.track_code::int BETWEEN 51 AND 59 THEN 'turf'
                        ELSE 'dirt'
                    END                                            AS surface,
                    re.wakuban                                    AS bracket_number,
                    re.kakutei_chakujun                           AS confirmed_rank,
                    re.corner_4,
                    COUNT(*) OVER (PARTITION BY re.race_id)       AS field_size
                FROM   race_entries_v2 re
                JOIN   races_v2        rv ON re.race_id = rv.race_id
                WHERE  to_date(rv.kaisai_year || rv.kaisai_monthday, 'YYYYMMDD') < '{self.target_date}'
                  AND  re.kakutei_chakujun IS NOT NULL
                  AND  re.kakutei_chakujun >  0
                  AND  re.kakutei_chakujun <= 30
                  AND  re.wakuban          BETWEEN 1 AND 8
                  AND  rv.keibajo_code     IS NOT NULL
                  AND  rv.distance         IS NOT NULL
            )
            SELECT
                place_code,
                distance,
                surface,
                bracket_number,
                confirmed_rank,
                CASE
                    WHEN corner_4 IS NULL OR field_size IS NULL OR field_size = 0 THEN NULL
                    WHEN corner_4::float / field_size <= 0.25 THEN '1'
                    WHEN corner_4::float / field_size <= 0.50 THEN '2'
                    WHEN corner_4::float / field_size <= 0.75 THEN '3'
                    ELSE '4'
                END AS running_style
            FROM base
        """)
        with self.engine.connect() as conn:
            return pd.read_sql(sql, conn)

    # ── 集計 ─────────────────────────────────────────────────────────────────

    def _aggregate_course(self, df: pd.DataFrame) -> list[dict]:
        rows: list[dict] = []
        df["confirmed_rank"] = pd.to_numeric(df["confirmed_rank"], errors="coerce")
        df["bracket_number"]   = pd.to_numeric(df["bracket_number"],   errors="coerce")

        for (place_code, distance, surface), group in df.groupby(
            ["place_code", "distance", "surface"]
        ):
            overall = _Rates.from_series(group["confirmed_rank"])
            if overall.count < _MIN_SAMPLES:
                continue  # 信頼性が低いコース組合せは除外

            row: dict = {
                "target_date": self.target_date,
                "place_code":  str(place_code),
                "distance":    int(distance),
                "surface":     str(surface),
                "updated_at":  datetime.now(timezone.utc),
                "total_count": overall.count,
                "win_rate":    overall.win_rate,
                "top3_rate":   overall.top3_rate,
            }
            row.update(self._calc_gate_cols(group, overall))
            row.update(self._calc_style_cols(group, overall))
            rows.append(row)

        return rows

    @staticmethod
    def _shift(cond_rate: Optional[float], overall_rate: Optional[float]) -> Optional[float]:
        if cond_rate is None or overall_rate is None:
            return None
        return cond_rate - overall_rate

    @staticmethod
    def _calc_gate_cols(group: pd.DataFrame, overall: _Rates) -> dict:
        result: dict = {}
        for gate in range(1, 9):
            prefix = f"gate{gate}"
            subset = group[group["bracket_number"] == gate]
            rates  = _Rates.from_series(subset["confirmed_rank"])
            result[f"{prefix}_count"]      = rates.count
            result[f"{prefix}_win_rate"]   = rates.win_rate
            result[f"{prefix}_top3_rate"]  = rates.top3_rate
            if rates.count >= _MIN_SAMPLES:
                result[f"{prefix}_win_shift"]  = CourseProfileStoreBatch._shift(rates.win_rate,  overall.win_rate)
                result[f"{prefix}_top3_shift"] = CourseProfileStoreBatch._shift(rates.top3_rate, overall.top3_rate)
            else:
                result[f"{prefix}_win_shift"]  = None
                result[f"{prefix}_top3_shift"] = None
        return result

    @staticmethod
    def _calc_style_cols(group: pd.DataFrame, overall: _Rates) -> dict:
        # running_style は corner_4 から動的推定された Proxy 値（'1'〜'4'）
        style_map = {"nige": "1", "senko": "2", "sashi": "3", "oikomi": "4"}
        result: dict = {}
        style_col = group["running_style"].astype(str).str.strip()
        for name, code in style_map.items():
            prefix = f"style_{name}"
            subset = group[style_col == code]
            rates  = _Rates.from_series(subset["confirmed_rank"])
            result[f"{prefix}_count"]      = rates.count
            result[f"{prefix}_win_rate"]   = rates.win_rate
            result[f"{prefix}_top3_rate"]  = rates.top3_rate
            if rates.count >= _MIN_SAMPLES:
                result[f"{prefix}_win_shift"]  = CourseProfileStoreBatch._shift(rates.win_rate,  overall.win_rate)
                result[f"{prefix}_top3_shift"] = CourseProfileStoreBatch._shift(rates.top3_rate, overall.top3_rate)
            else:
                result[f"{prefix}_win_shift"]  = None
                result[f"{prefix}_top3_shift"] = None
        return result

    # ── UPSERT ───────────────────────────────────────────────────────────────

    def _upsert(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        table = CourseProfileStore.__table__
        stmt = pg_insert(table).values(rows)
        update_cols = {
            col.name: stmt.excluded[col.name]
            for col in table.columns
            if col.name not in ("id", "target_date", "place_code", "distance", "surface")
        }
        stmt = stmt.on_conflict_do_update(
            constraint="uq_course_profile_key",
            set_=update_cols,
        )
        with self.engine.begin() as conn:
            result = conn.execute(stmt)
        return result.rowcount
