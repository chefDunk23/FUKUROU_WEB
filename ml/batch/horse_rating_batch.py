"""
web_service/batch/horse_rating_batch.py
========================================
馬レーティング（Elo）の逐次計算バッチ（v1.5）。

アルゴリズム: Bradley-Terry 型 N 頭ペアワイズ Elo 更新。
  全着順ペア (i, j) のスコアを比較し、K/(n-1) の正規化係数でレーティングを更新する。
  これにより「1着 vs 2着」だけでなく「5着 vs 10着」もレーティング情報として活用できる。

設計方針:
  - 全期間のレースを race_date ASC で逐次処理（並列化不可）
  - turf / dirt のサーフェス別に独立したレーティングを管理
  - _ratings dict でインメモリキャッシュ（DB 再クエリなし）
  - UPSERT によるべき等性保証（再実行しても同一結果）
  - モジュールレベル純粋関数（grade_to_k, apply_elo_pairwise）はテスト容易

Usage:
    batch = HorseRatingBatch(target_date=date.today(), engine=engine)
    n = batch.run()              # 全期間再計算（初回約30分）
    n = batch.run(from_date=...)  # 差分更新（日次運用）
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ml.batch.models import HorseRatingStore

_logger = logging.getLogger(__name__)

_INITIAL_RATING: float = 1500.0
_ELO_SCALE: float = 400.0  # Elo 式の分母定数 D

# JRA-VAN grade_code → K 係数（グレード補正）
_GRADE_K: dict[str, float] = {
    "11": 32.0,  # G1
    "12": 28.0,  # G2
    "13": 24.0,  # G3
    "14": 22.0,  # Listed / 重賞（G外）
    "15": 20.0,  # オープン特別
    "16": 18.0,  # 3勝クラス
    "17": 16.0,  # 2勝クラス
    "18": 16.0,  # 1勝クラス
    "19": 16.0,  # 未勝利
    "20": 16.0,  # 新馬
}
_DEFAULT_K: float = 16.0


# ─────────────────────────────────────────────────────────────────────────────
# モジュールレベル純粋関数（テスト容易・副作用なし）
# ─────────────────────────────────────────────────────────────────────────────


def grade_to_k(grade_code: Optional[str]) -> float:
    """
    JRA-VAN grade_code を K 係数に変換する。

    Args:
        grade_code: レースのグレードコード（'11'=G1, '12'=G2, ..., '20'=新馬）。
                    None または未知コードの場合はデフォルト K を返す。

    Returns:
        K 係数 (float)。
    """
    if grade_code is None:
        return _DEFAULT_K
    return _GRADE_K.get(str(grade_code).strip(), _DEFAULT_K)


def apply_elo_pairwise(
    ratings: dict[str, float],
    positions: dict[str, int],
    k_factor: float,
) -> dict[str, float]:
    """
    Bradley-Terry 型 N 頭ペアワイズ Elo 更新。

    各馬 i について全対戦相手 j とのペアワイズスコアを集計し、
    K/(n-1) の正規化係数でレーティングを更新する。

    Args:
        ratings:   {horse_id: pre_race_rating}
        positions: {horse_id: finishing_position}（1始まり、小さい = 上位）
        k_factor:  K 係数（グレード補正済み）

    Returns:
        {horse_id: post_race_rating}

    計算式:
        E_{i,j} = 1 / (1 + 10^((R_j − R_i) / 400))
        S_{i,j} = 1.0 if pos_i < pos_j  (i が j に勝ち)
                = 0.5 if pos_i == pos_j  (同着)
                = 0.0 if pos_i > pos_j  (i が j に負け)
        ΔR_i = (K / (n-1)) × Σ_{j≠i} (S_{i,j} − E_{i,j})

    特性:
        - 零和: Σ_i ΔR_i = 0（レーティング総量は保存される）
        - n=1: 変化なし（対戦相手なし）
        - 同着全員: ΔR_i = 0（スコア均衡）
    """
    horse_ids = list(ratings.keys())
    n = len(horse_ids)
    if n <= 1:
        return dict(ratings)

    k_adj = k_factor / (n - 1)
    deltas: dict[str, float] = {hid: 0.0 for hid in horse_ids}

    for h_i in horse_ids:
        r_i = ratings[h_i]
        pos_i = positions[h_i]
        for h_j in horse_ids:
            if h_i == h_j:
                continue
            r_j = ratings[h_j]
            pos_j = positions[h_j]

            # 実際のペアワイズスコア
            if pos_i < pos_j:
                s_ij = 1.0
            elif pos_i == pos_j:
                s_ij = 0.5
            else:
                s_ij = 0.0

            # 期待ペアワイズスコア（Elo 式）
            e_ij = 1.0 / (1.0 + 10.0 ** ((r_j - r_i) / _ELO_SCALE))

            deltas[h_i] += k_adj * (s_ij - e_ij)

    return {hid: ratings[hid] + deltas[hid] for hid in horse_ids}


# ─────────────────────────────────────────────────────────────────────────────
# バッチクラス
# ─────────────────────────────────────────────────────────────────────────────


class HorseRatingBatch:
    """
    全期間のレースを日付昇順で逐次処理し、馬レーティングを計算・保存するバッチ。

    Usage:
        batch = HorseRatingBatch(target_date=date.today(), engine=engine)
        rows_updated = batch.run()
    """

    def __init__(self, target_date: date, engine) -> None:
        self.target_date = target_date
        self.engine = engine
        # インメモリ rating キャッシュ: horse_id → {surface: rating}
        # surface は 'turf' または 'dirt'
        self._ratings: dict[str, dict[str, float]] = {}

    def run(self, from_date: Optional[date] = None) -> int:
        """
        レースを race_date 昇順で逐次処理し、horse_rating_store へ UPSERT する。

        Args:
            from_date: 差分更新の開始日（レース日 >= from_date を対象）。
                       None の場合は全期間再計算。
                       ウォームスタートにより DB 既存レーティングを引き継ぐ。

        Returns:
            UPSERT した行数（馬×レース単位）。
        """
        _logger.info(
            "[HorseRating] 開始: target_date=%s from_date=%s",
            self.target_date, from_date,
        )

        self._load_warm_start_ratings()

        races_df = self._fetch_races_with_results(from_date)
        if races_df.empty:
            _logger.warning("[HorseRating] 処理対象レースなし。スキップ。")
            return 0

        total_rows = 0
        # (race_date, race_id) 昇順でグループ化 ← 逐次順序を厳密に保証
        for (_, race_id), group in races_df.groupby(
            ["race_date", "race_id"], sort=True
        ):
            rows = self._process_race(group)
            if not rows:
                continue

            self._upsert(rows)
            total_rows += len(rows)

            # インメモリキャッシュを post_race_rating で更新
            for row in rows:
                hid  = row["horse_id"]
                surf = row["surface"]
                if hid not in self._ratings:
                    self._ratings[hid] = {}
                self._ratings[hid][surf] = row["post_race_rating"]

        _logger.info("[HorseRating] 完了: %d 行 UPSERT", total_rows)
        return total_rows

    # ── データ取得 ────────────────────────────────────────────────────────────

    def _load_warm_start_ratings(self) -> None:
        """
        horse_rating_store から各馬の最新 post_race_rating をロードし、
        インメモリキャッシュ self._ratings を初期化する。

        差分更新（from_date 指定）時のウォームスタートに使用。
        テーブルが空またはエラーの場合は空 dict のまま（全馬が初期値 1500 からスタート）。
        """
        sql = text("""
            SELECT DISTINCT ON (horse_id, surface)
                horse_id,
                surface,
                post_race_rating
            FROM horse_rating_store
            ORDER BY horse_id, surface, race_date DESC
        """)
        with self.engine.connect() as conn:
            try:
                rows = conn.execute(sql).fetchall()
            except Exception:
                _logger.debug("[HorseRating] ウォームスタート: テーブル未作成またはエラー。初期値で開始。")
                rows = []

        for hid, surf, rating in rows:
            if hid not in self._ratings:
                self._ratings[hid] = {}
            self._ratings[hid][surf] = float(rating)

        _logger.debug(
            "[HorseRating] ウォームスタート完了: %d 馬分のレーティングをロード",
            len(self._ratings),
        )

    def _fetch_races_with_results(self, from_date: Optional[date]) -> pd.DataFrame:
        """
        race_entries_v2 + races_v2 を JOIN し、確定着順付きのエントリーを取得する。

        フィルタ条件:
            - race_date < target_date（データリーク防止）
            - race_date >= from_date（差分更新; None なら全期間）
            - kakutei_chakujun IS NOT NULL AND > 0（確定着順のみ）
            - blood_no が '0000000000'（血統未登録）の馬は対象外
        """
        from_clause = (
            f"AND to_date(rv.kaisai_year || rv.kaisai_monthday, 'YYYYMMDD') >= '{from_date}'"
            if from_date else ""
        )
        sql = text(f"""
            SELECT
                re.race_id,
                to_date(rv.kaisai_year || rv.kaisai_monthday, 'YYYYMMDD') AS race_date,
                CASE
                    WHEN rv.track_code::int BETWEEN 10 AND 22 THEN 'turf'
                    WHEN rv.track_code::int BETWEEN 51 AND 59 THEN 'turf'
                    ELSE 'dirt'
                END                                                AS surface,
                re.blood_no                                        AS horse_id,
                re.kakutei_chakujun                                AS confirmed_rank
            FROM   race_entries_v2 re
            JOIN   races_v2        rv ON re.race_id = rv.race_id
            WHERE  to_date(rv.kaisai_year || rv.kaisai_monthday, 'YYYYMMDD') < '{self.target_date}'
              {from_clause}
              AND  re.kakutei_chakujun IS NOT NULL
              AND  re.kakutei_chakujun >  0
              AND  re.kakutei_chakujun <= 30
              AND  re.blood_no         IS NOT NULL
              AND  re.blood_no         <> '0000000000'
            ORDER BY race_date ASC, re.race_id ASC
        """)
        with self.engine.connect() as conn:
            df = pd.read_sql(sql, conn)

        df["race_date"]      = pd.to_datetime(df["race_date"]).dt.date
        df["confirmed_rank"] = pd.to_numeric(df["confirmed_rank"], errors="coerce")
        return df.dropna(subset=["confirmed_rank"])

    # ── レース処理 ────────────────────────────────────────────────────────────

    def _process_race(self, race_df: pd.DataFrame) -> list[dict]:
        """
        1 レース分の Elo 更新を行い、UPSERT 用の dict リストを返す。

        2 頭未満（単走・除外等）は空リストを返してスキップする。
        """
        valid = race_df.dropna(subset=["confirmed_rank"]).copy()
        valid["confirmed_rank"] = valid["confirmed_rank"].astype(int)
        if len(valid) < 2:
            return []

        race_id   = str(valid["race_id"].iloc[0])
        race_date = valid["race_date"].iloc[0]
        surface   = str(valid["surface"].iloc[0])

        k_factor = _DEFAULT_K
        n        = len(valid)

        # 出走前レーティング（キャッシュから取得、未登録は初期値）
        pre_ratings: dict[str, float] = {
            str(row["horse_id"]): self._get_rating(str(row["horse_id"]), surface)
            for _, row in valid.iterrows()
        }

        # 着順 dict
        positions: dict[str, int] = {
            str(row["horse_id"]): int(row["confirmed_rank"])
            for _, row in valid.iterrows()
        }

        # レースレベル統計（出走前レーティングから算出）
        pre_values = np.array(list(pre_ratings.values()), dtype=float)
        race_avg_rating    = float(pre_values.mean())
        race_top_rating    = float(pre_values.max())
        race_rating_spread = float(pre_values.std(ddof=1)) if n >= 2 else 0.0

        # Elo ペアワイズ更新
        post_ratings = apply_elo_pairwise(pre_ratings, positions, k_factor)

        rows: list[dict] = []
        for hid, pre_r in pre_ratings.items():
            post_r = post_ratings[hid]
            rows.append({
                "horse_id":            hid,
                "race_id":             race_id,
                "race_date":           race_date,
                "surface":             surface,
                "pre_race_rating":     pre_r,
                "race_avg_rating":     race_avg_rating,
                "race_top_rating":     race_top_rating,
                "race_rating_spread":  race_rating_spread,
                "post_race_rating":    post_r,
                "delta_rating":        post_r - pre_r,
                "finishing_position":  positions[hid],
                "field_size":          n,
                "k_factor":            k_factor,
            })

        return rows

    def _get_rating(self, horse_id: str, surface: str) -> float:
        """インメモリキャッシュから現在のレーティングを取得。未登録は初期値 1500.0。"""
        return self._ratings.get(horse_id, {}).get(surface, _INITIAL_RATING)

    # ── UPSERT ───────────────────────────────────────────────────────────────

    def _upsert(self, rows: list[dict]) -> None:
        if not rows:
            return
        table = HorseRatingStore.__table__
        stmt = pg_insert(table).values(rows)
        update_cols = {
            col.name: stmt.excluded[col.name]
            for col in table.columns
            if col.name not in ("id", "horse_id", "race_id")
        }
        stmt = stmt.on_conflict_do_update(
            constraint="uq_horse_rating_horse_race",
            set_=update_cols,
        )
        with self.engine.begin() as conn:
            conn.execute(stmt)
