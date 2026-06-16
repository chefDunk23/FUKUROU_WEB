"""
web_service/batch/external_factor_store.py
============================================
外的要因（騎手・調教師・種牡馬）フィーチャーストアの日次バッチ集計。

処理フロー:
    1. races_v2 + race_entries_v2 + horses テーブルから
       target_date 未満のレース結果を一括 SELECT（リーク防止）
    2. 距離区分列 (dist_zone) を付与
    3. エンティティ（騎手/調教師/種牡馬）ごとに groupby 集計
       ・全体成績 (total_count / win_rate / top2_rate / top3_rate)
       ・馬場状態別 (firm/yaya/omo/furyo)
       ・距離区分別 (sprint/mile/middle/long)
       ・競馬場別   (venue_01 〜 venue_10)
    4. aptitude_shift = 条件別勝率 − 全体勝率 を計算
    5. PostgreSQL の INSERT ... ON CONFLICT DO UPDATE で UPSERT

使い方（プロジェクトルートから実行）:
    py web_service/batch/external_factor_store.py --date 2026-05-09

既存システムへの影響:
    - 読み取りのみ: races_v2 / race_entries_v2 / horses（変更なし）
    - 書き込み: *_feature_store テーブル（新規）のみ
"""
from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ml.db import DBConnector, SessionLocal
from ml.batch.models import (
    JockeyFeatureStore,
    TrainerFeatureStore,
    SireFeatureStore,
    FEATURE_STORE_MODELS,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# 定数定義
# ─────────────────────────────────────────────────────────────────────────────

# 距離区分の境界 [lo, hi) で定義
_DIST_ZONES: dict[str, tuple[int, int]] = {
    "sprint": (0,    1400),    # <1400m
    "mile":   (1400, 1801),    # 1400-1800m
    "middle": (1801, 2401),    # 1801-2400m
    "long":   (2401, 99999),   # >2400m
}

# 馬場コード → カラムサフィックス
_BABA_MAP: dict[str, str] = {
    "1": "firm",
    "2": "yaya",
    "3": "omo",
    "4": "furyo",
}

# 対象競馬場コード（0 埋め 2 桁）
_VENUE_CODES: list[str] = [f"{i:02d}" for i in range(1, 11)]  # '01'-'10'

# 馬場種別（芝/ダート）→ カラムサフィックス
_SURFACE_MAP: dict[str, str] = {
    "芝":   "turf",
    "ダート": "dirt",
}

# エンティティ定義: (entity_col, table_name, entity_id_col)
_ENTITY_CONFIGS: list[tuple[str, str, str]] = [
    ("kishu_code",     "jockey_feature_store",  "kishu_code"),
    ("chokyoshi_code", "trainer_feature_store",  "chokyoshi_code"),
    ("sire_id",        "sire_feature_store",     "sire_id"),
]

# 最小サンプル数（これ未満のエンティティはスキップ）
_MIN_SAMPLES = 3


# ─────────────────────────────────────────────────────────────────────────────
# データクラス
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class _Rates:
    """小集合から計算した基本成績指標。"""
    count:     int
    win_rate:  Optional[float]
    top2_rate: Optional[float]
    top3_rate: Optional[float]

    @classmethod
    def from_series(cls, chakujun: pd.Series) -> "_Rates":
        n = len(chakujun)
        if n == 0:
            return cls(count=0, win_rate=None, top2_rate=None, top3_rate=None)
        return cls(
            count=n,
            win_rate=float((chakujun == 1).mean()),
            top2_rate=float((chakujun <= 2).mean()),
            top3_rate=float((chakujun <= 3).mean()),
        )

    @property
    def is_reliable(self) -> bool:
        return self.count >= _MIN_SAMPLES


@dataclass
class _AggRow:
    """1エンティティ分の集計結果 (wide 形式 dict)。"""
    entity_id: str
    cols: dict = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# メインバッチクラス
# ─────────────────────────────────────────────────────────────────────────────

class ExternalFactorStoreBatch:
    """
    日次バッチ処理のエントリーポイント。

    Args:
        target_date: スナップショット日付（通常は実行日の前日）。
                     この日付「未満」のレース結果のみ集計する。
    """

    def __init__(self, target_date: date) -> None:
        self.target_date = target_date
        self.db          = DBConnector()
        self._raw: Optional[pd.DataFrame] = None   # DB から1度だけ取得

    # ──────────────────────────────────────────────────────────────────────
    # Public: バッチ実行
    # ──────────────────────────────────────────────────────────────────────

    def run(self) -> dict[str, int]:
        """
        バッチを実行し、UPSERT した件数を返す。

        Returns:
            {"jockey": N, "trainer": N, "sire": N}
        """
        logger.info(
            "[ExternalFactorStore] バッチ開始: target_date=%s", self.target_date
        )

        raw = self._fetch_race_results()
        if raw.empty:
            logger.warning(
                "[ExternalFactorStore] 集計対象データが 0 件 — スキップします"
            )
            return {"jockey": 0, "trainer": 0, "sire": 0}

        raw = self._enrich_distance_zone(raw)

        counts: dict[str, int] = {}
        for entity_col, table_name, id_col in _ENTITY_CONFIGS:
            entity_key = entity_col.split("_")[0]   # "kishu" / "chokyoshi" / "sire"
            if entity_col == "sire_id":
                rows = self._aggregate_sire(raw)
            else:
                rows = self._aggregate_entity(raw, entity_col)
            n    = self._upsert_rows(rows, table_name, id_col)
            counts[entity_key] = n
            logger.info(
                "[ExternalFactorStore] %s: %d 件 UPSERT 完了", entity_key, n
            )

        logger.info("[ExternalFactorStore] バッチ完了: %s", counts)
        return counts

    # ──────────────────────────────────────────────────────────────────────
    # Step 1: DB からレース結果を取得
    # ──────────────────────────────────────────────────────────────────────

    def _fetch_race_results(self) -> pd.DataFrame:
        """
        target_date【未満】のレース確定結果を一括取得する。

        ★ リーク防止の要: WHERE race_date < :cutoff でハードコード。
           このフィルタは絶対に緩めてはならない。
        """
        if self._raw is not None:
            return self._raw

        sql = f"""
        SELECT
            re.kishu_code      AS kishu_code,
            re.chokyosi_code   AS chokyoshi_code,
            h.sire_id,
            rv.distance,
            CASE
                WHEN rv.track_code::int BETWEEN 10 AND 22 THEN rv.shiba_baba_code
                WHEN rv.track_code::int BETWEEN 23 AND 29 THEN rv.dirt_baba_code
                WHEN rv.track_code::int BETWEEN 51 AND 59 THEN rv.shiba_baba_code
                ELSE NULL
            END                AS baba_code,
            rv.keibajo_code    AS venue_code,
            CASE
                WHEN rv.track_code::int BETWEEN 10 AND 22 THEN '芝'
                WHEN rv.track_code::int BETWEEN 23 AND 29 THEN 'ダート'
                WHEN rv.track_code::int BETWEEN 51 AND 59 THEN '障害'
                ELSE NULL
            END                AS course_type,
            re.kakutei_chakujun AS chakujun,
            h.sex              AS horse_sex,
            (EXTRACT(YEAR FROM to_date(rv.kaisai_year || rv.kaisai_monthday, 'YYYYMMDD'))::int
             - EXTRACT(YEAR FROM h.birthday)::int) AS age_at_race,
            re.horse_weight    AS body_weight,
            LAG(rv.distance) OVER (
                PARTITION BY re.blood_no
                ORDER BY to_date(rv.kaisai_year || rv.kaisai_monthday, 'YYYYMMDD'), re.race_id
            )                  AS prev_distance
        FROM   race_entries_v2 re
        JOIN   races_v2       rv ON re.race_id  = rv.race_id
        LEFT JOIN horses      h  ON re.blood_no = h.id
        WHERE  to_date(rv.kaisai_year || rv.kaisai_monthday, 'YYYYMMDD') < '{self.target_date}'
          AND  re.kakutei_chakujun IS NOT NULL
          AND  re.kakutei_chakujun  > 0
          AND  re.blood_no          <> '0000000000'
          AND  rv.distance          > 0
        """
        df = self.db.fetch_data(sql)
        df["chakujun"] = pd.to_numeric(df["chakujun"], errors="coerce")
        df["distance"] = pd.to_numeric(df["distance"],  errors="coerce").fillna(0).astype(int)
        df = df.dropna(subset=["chakujun"])

        logger.info(
            "[ExternalFactorStore] レース結果取得: %d 行 (cutoff=%s)",
            len(df), self.target_date,
        )
        self._raw = df
        return self._raw

    # ──────────────────────────────────────────────────────────────────────
    # Step 2: 距離区分を付与
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def _enrich_distance_zone(df: pd.DataFrame) -> pd.DataFrame:
        """distance 列から dist_zone ('sprint'/'mile'/'middle'/'long') を付与する。"""
        dist = df["distance"].astype(int)
        zone = pd.Series("other", index=df.index, dtype=str)
        for zone_name, (lo, hi) in _DIST_ZONES.items():
            zone[(dist >= lo) & (dist < hi)] = zone_name
        out = df.copy()
        out["dist_zone"] = zone
        return out

    # ──────────────────────────────────────────────────────────────────────
    # Step 3-4: エンティティ別集計 + aptitude_shift 計算
    # ──────────────────────────────────────────────────────────────────────

    def _aggregate_entity(
        self, df: pd.DataFrame, entity_col: str
    ) -> list[_AggRow]:
        """
        entity_col でグルーピングし、全条件の統計を計算する。

        信頼性フィルタ:
          - overall.count < _MIN_SAMPLES のエンティティは除外
          - 条件別サブセットが 0 件の場合は NULL (None) で格納
        """
        df_valid = df.dropna(subset=[entity_col])
        results: list[_AggRow] = []

        for entity_id, group in df_valid.groupby(entity_col):
            entity_id_str = str(entity_id)

            overall = _Rates.from_series(group["chakujun"])
            if not overall.is_reliable:
                continue   # 出走数が少なすぎるエンティティはスキップ

            cols: dict = {
                "total_count": overall.count,
                "win_rate":    overall.win_rate,
                "top2_rate":   overall.top2_rate,
                "top3_rate":   overall.top3_rate,
            }

            # 馬場別
            for baba_code, suffix in _BABA_MAP.items():
                subset = group[group["baba_code"].astype(str).str.strip() == baba_code]
                rates  = _Rates.from_series(subset["chakujun"])
                cols.update(
                    self._condition_cols(f"baba_{suffix}", rates, overall)
                )

            # 距離区分別
            for zone in _DIST_ZONES:
                subset = group[group["dist_zone"] == zone]
                rates  = _Rates.from_series(subset["chakujun"])
                cols.update(
                    self._condition_cols(f"dist_{zone}", rates, overall)
                )

            # 競馬場別
            for venue in _VENUE_CODES:
                venue_norm = venue.zfill(2)
                subset = group[
                    group["venue_code"].astype(str).str.strip().str.zfill(2) == venue_norm
                ]
                rates = _Rates.from_series(subset["chakujun"])
                cols.update(
                    self._condition_cols(f"venue_{venue_norm}", rates, overall)
                )

            # 芝/ダート別
            for raw_label, suffix in _SURFACE_MAP.items():
                subset = group[group["course_type"].astype(str).str.strip() == raw_label]
                rates  = _Rates.from_series(subset["chakujun"])
                cols.update(self._condition_cols(f"surface_{suffix}", rates, overall))

            results.append(_AggRow(entity_id=entity_id_str, cols=cols))

        return results

    # ──────────────────────────────────────────────────────────────────────
    # Step 5: PostgreSQL UPSERT
    # ──────────────────────────────────────────────────────────────────────

    def _upsert_rows(
        self,
        rows: list[_AggRow],
        table_name: str,
        entity_id_col: str,
    ) -> int:
        """
        _AggRow リストを対応テーブルへ UPSERT する。
        同一 (target_date, entity_id) が既存なら全統計列を上書き。
        """
        if not rows:
            return 0

        model_class = FEATURE_STORE_MODELS[table_name]

        with SessionLocal() as session:
            for row in rows:
                values = {
                    "target_date":  self.target_date,
                    entity_id_col:  row.entity_id,
                    **row.cols,
                    "updated_at": datetime.now(timezone.utc),
                }
                stmt = (
                    pg_insert(model_class)
                    .values(**values)
                    .on_conflict_do_update(
                        index_elements=["target_date", entity_id_col],
                        set_={k: v for k, v in values.items()
                              if k not in ("target_date", entity_id_col)},
                    )
                )
                session.execute(stmt)
            session.commit()

        return len(rows)

    # ──────────────────────────────────────────────────────────────────────
    # ヘルパー: 条件別カラム辞書を生成
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def _condition_cols(
        prefix: str,
        rates: _Rates,
        overall: _Rates,
    ) -> dict:
        """
        条件別のカラム辞書を生成する（aptitude_shift を含む）。

        shift の定義:
          win_shift  = 条件 win_rate  − overall win_rate
          top3_shift = 条件 top3_rate − overall top3_rate
          正 → 得意 / 負 → 苦手

        サンプルが 0 件の条件は全て None（欠損のまま保存）。
        """
        def _shift(cond_val: Optional[float], base_val: Optional[float]) -> Optional[float]:
            if cond_val is None or base_val is None:
                return None
            return round(cond_val - base_val, 5)

        return {
            f"{prefix}_count":      rates.count,
            f"{prefix}_win_rate":   rates.win_rate,
            f"{prefix}_top3_rate":  rates.top3_rate,
            f"{prefix}_win_shift":  _shift(rates.win_rate,  overall.win_rate),
            f"{prefix}_top3_shift": _shift(rates.top3_rate, overall.top3_rate),
        }

    # ──────────────────────────────────────────────────────────────────────
    # v1.2 拡張: 種牡馬専用集計（_SireExtendedMixin カラム生成）
    # ──────────────────────────────────────────────────────────────────────

    def _aggregate_sire(self, df: pd.DataFrame) -> list[_AggRow]:
        """
        種牡馬（sire_id）専用集計。
        _ConditionStatsMixin（馬場/距離/競馬場）+ _SireExtendedMixin
        （性別/年齢/体重/距離変動）の全カラムを生成する。
        """
        entity_col = "sire_id"
        df_valid   = df.dropna(subset=[entity_col])
        results: list[_AggRow] = []

        for entity_id, group in df_valid.groupby(entity_col):
            entity_id_str = str(entity_id)

            overall = _Rates.from_series(group["chakujun"])
            if not overall.is_reliable:
                continue

            cols: dict = {
                "total_count": overall.count,
                "win_rate":    overall.win_rate,
                "top2_rate":   overall.top2_rate,
                "top3_rate":   overall.top3_rate,
            }

            # 馬場別
            for baba_code, suffix in _BABA_MAP.items():
                subset = group[group["baba_code"].astype(str).str.strip() == baba_code]
                rates  = _Rates.from_series(subset["chakujun"])
                cols.update(self._condition_cols(f"baba_{suffix}", rates, overall))

            # 距離区分別
            for zone in _DIST_ZONES:
                subset = group[group["dist_zone"] == zone]
                rates  = _Rates.from_series(subset["chakujun"])
                cols.update(self._condition_cols(f"dist_{zone}", rates, overall))

            # 競馬場別
            for venue in _VENUE_CODES:
                venue_norm = venue.zfill(2)
                subset = group[
                    group["venue_code"].astype(str).str.strip().str.zfill(2) == venue_norm
                ]
                rates = _Rates.from_series(subset["chakujun"])
                cols.update(self._condition_cols(f"venue_{venue_norm}", rates, overall))

            # 芝/ダート別
            for raw_label, suffix in _SURFACE_MAP.items():
                subset = group[group["course_type"].astype(str).str.strip() == raw_label]
                rates  = _Rates.from_series(subset["chakujun"])
                cols.update(self._condition_cols(f"surface_{suffix}", rates, overall))

            # ▼ v1.2: _SireExtendedMixin カラム
            cols.update(ExternalFactorStoreBatch._calc_sex_cols(group, overall))
            cols.update(ExternalFactorStoreBatch._calc_age_cols(group, overall))
            # ▼ v1.3: 馬格ティア別（480kg 閾値）→ weight_light_* / weight_heavy_*
            cols.update(ExternalFactorStoreBatch._calc_weight_tier_cols(group, overall))
            cols.update(ExternalFactorStoreBatch._calc_weight_cols(group))
            cols.update(ExternalFactorStoreBatch._calc_dist_change_cols(group, overall))

            results.append(_AggRow(entity_id=entity_id_str, cols=cols))

        return results

    @staticmethod
    def _calc_sex_cols(group: pd.DataFrame, overall: _Rates) -> dict:
        """
        性別別（牡+セン / 牝）勝率シフトを計算する（v1.3: セン馬グループ修正）。

        sex='1' or '3' → 牡馬＋セン馬 (sex_male)
            セン馬は去勢後も雄体格のため male グループに統合するのが正しい
        sex='2'        → 牝馬 (sex_female)  フィリーサイアー判定の純粋な指標
        NaN（horses.sex が未入力）は両グループから除外される。
        """
        sex_col = group["horse_sex"].astype(str).str.strip()
        male    = group[sex_col.isin(["1", "3"])]   # 牡馬 + セン馬
        female  = group[sex_col == "2"]              # 牝馬のみ

        result: dict = {}
        result.update(
            ExternalFactorStoreBatch._condition_cols(
                "sex_male", _Rates.from_series(male["chakujun"]), overall
            )
        )
        result.update(
            ExternalFactorStoreBatch._condition_cols(
                "sex_female", _Rates.from_series(female["chakujun"]), overall
            )
        )
        return result

    @staticmethod
    def _calc_weight_tier_cols(group: pd.DataFrame, overall: _Rates) -> dict:
        """
        産駒の出走当日体重ティア別（軽量 / 大型）勝率シフトを計算する（v1.3 新規）。

        閾値 480kg:
          weight_light : body_weight <  480  （スピード型・牝馬的体格に多い）
          weight_heavy : body_weight >= 480  （パワー型・スタミナ型に多い）

        shift > 0 → その馬格帯で産駒が全体平均を上回る（馬格依存度の方向性）
        サンプルが _MIN_SAMPLES 未満の場合は rates.win_rate = None → shift も None。
        """
        weights = pd.to_numeric(group["body_weight"], errors="coerce")
        light   = group[weights <  480]
        heavy   = group[weights >= 480]

        result: dict = {}
        result.update(
            ExternalFactorStoreBatch._condition_cols(
                "weight_light", _Rates.from_series(light["chakujun"]), overall
            )
        )
        result.update(
            ExternalFactorStoreBatch._condition_cols(
                "weight_heavy", _Rates.from_series(heavy["chakujun"]), overall
            )
        )
        return result

    @staticmethod
    def _calc_age_cols(group: pd.DataFrame, overall: _Rates) -> dict:
        """
        年齢ティア別（2/3/4/5歳以上）勝率シフトを計算する。

        age_at_race はレース時点での実年齢（年）。
        NaN（horses.birthday 未入力）は全ティアから除外される。
        5歳・6歳・7歳以上は全て age5plus に集約する。
        """
        age = pd.to_numeric(group["age_at_race"], errors="coerce")

        tiers: dict[str, pd.Series] = {
            "age2":     age == 2,
            "age3":     age == 3,
            "age4":     age == 4,
            "age5plus": age >= 5,
        }
        result: dict = {}
        for prefix, mask in tiers.items():
            subset = group[mask.fillna(False)]
            rates  = _Rates.from_series(subset["chakujun"])
            result.update(
                ExternalFactorStoreBatch._condition_cols(prefix, rates, overall)
            )
        return result

    @staticmethod
    def _calc_weight_cols(group: pd.DataFrame) -> dict:
        """
        産駒の平均勝利馬体重・標準偏差・全体平均を計算する。

        avg_win_weight / std_win_weight は勝利時（chakujun=1）のサンプルを使用。
        フィジカル合致度の Z-score 計算: (current_weight - avg) / std
        _MIN_SAMPLES 未満の場合は None を返す（信頼性なし）。
        """
        weights     = pd.to_numeric(group["body_weight"], errors="coerce")
        win_weights = weights[group["chakujun"] == 1].dropna()
        all_weights = weights.dropna()

        return {
            "avg_win_weight": float(win_weights.mean()) if len(win_weights) >= _MIN_SAMPLES else None,
            "std_win_weight": float(win_weights.std())  if len(win_weights) >= _MIN_SAMPLES else None,
            "avg_all_weight": float(all_weights.mean()) if len(all_weights) >= _MIN_SAMPLES else None,
        }

    @staticmethod
    def _calc_dist_change_cols(group: pd.DataFrame, overall: _Rates) -> dict:
        """
        前走比の距離変動別（延長/短縮/同距離）勝率シフトを計算する。

        閾値 50m:
          diff >= +50  → dist_up   (距離延長: スタミナ値に対応)
          diff <= -50  → dist_down (距離短縮: 操縦性・気性に対応)
          -50 < diff < +50 → dist_same (同距離)

        prev_distance が NaN の行（初出走等）は全て除外する。
        """
        g    = group.dropna(subset=["prev_distance"])
        diff = g["distance"].astype(int) - g["prev_distance"].astype(int)

        up   = g[diff >= 50]
        down = g[diff <= -50]
        same = g[(diff > -50) & (diff < 50)]

        result: dict = {}
        for prefix, subset in [("dist_up", up), ("dist_down", down), ("dist_same", same)]:
            rates = _Rates.from_series(subset["chakujun"])
            result.update(
                ExternalFactorStoreBatch._condition_cols(prefix, rates, overall)
            )
        return result


# ─────────────────────────────────────────────────────────────────────────────
# CLI エントリーポイント
# ─────────────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="外的要因フィーチャーストアの日次バッチ集計"
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="スナップショット日付 (YYYY-MM-DD)。省略時は今日の前日を使用。",
    )
    parser.add_argument(
        "--loglevel",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="ログレベル",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    logging.basicConfig(
        level=getattr(logging, args.loglevel),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if args.date:
        target_date = date.fromisoformat(args.date)
    else:
        from datetime import timedelta
        target_date = date.today() - timedelta(days=1)

    logger.info("target_date = %s", target_date)
    batch = ExternalFactorStoreBatch(target_date=target_date)
    result = batch.run()

    print("\n--- 完了 ---")
    for entity, count in result.items():
        print(f"  {entity}: {count:,} 件 UPSERT")


if __name__ == "__main__":
    main()
