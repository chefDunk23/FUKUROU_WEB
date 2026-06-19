"""
scripts/enrich_bloodline_v1.py
==============================
pedigree_features_v1_2022plus.parquet に bloodline_feature_store (P1-P5) の
Point-in-Time 血統特徴量を追加する。

処理フロー:
    1. 入力 Parquet を読み込む（horse_id / race_id が必要）
    2. fukurou_jvdl.bloodline_feature_store から全 P1-P5 特徴量を取得
    3. (horse_id, race_id) で LEFT JOIN
    4. 列を元 Parquet に追加して保存

生成される特徴量 (25列):
    ─── P1: 父 Point-in-Time 成績 ──────────────────────────────────────────
    sire_wr, sire_turf_wr, sire_dirt_wr
    sire_sprint_wr, sire_mile_wr, sire_middle_wr, sire_long_wr
    sire_heavy_wr, sire_growth_delta, sire_n_starts
    ─── P2: 母父 Point-in-Time 成績 ────────────────────────────────────────
    bms_wr, bms_turf_wr, bms_dirt_wr
    bms_sprint_wr, bms_mile_wr, bms_middle_wr, bms_long_wr
    bms_heavy_wr, bms_growth_delta, bms_n_starts
    ─── P3: 個体クロス ──────────────────────────────────────────────────────
    sire_sex_wr, p3_weight_gap
    ─── P4: 突然変異スコア ──────────────────────────────────────────────────
    p4_mutation_turf, p4_mutation_dirt, p4_n_ancestors
    ─── P5: 自己主張度（BMS分散） ───────────────────────────────────────────
    p5_dominance_score, p5_n_bms_groups

Usage:
    py -3.13 scripts/enrich_bloodline_v1.py
    py -3.13 scripts/enrich_bloodline_v1.py \\
        --in  outputs/pedigree_features_v1_2022plus.parquet \\
        --out outputs/bloodline_features_v1_2022plus.parquet
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.db.jvdl import get_conn as get_jvdl_conn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

_DEFAULT_IN  = Path("outputs/pedigree_features_v1_2022plus.parquet")
_DEFAULT_OUT = Path("outputs/bloodline_features_v1_2022plus.parquet")

BLOODLINE_COLS: list[str] = [
    # P1: 父 PIT
    "sire_wr", "sire_turf_wr", "sire_dirt_wr",
    "sire_sprint_wr", "sire_mile_wr", "sire_middle_wr", "sire_long_wr",
    "sire_heavy_wr", "sire_growth_delta", "sire_n_starts",
    # P2: 母父 PIT
    "bms_wr", "bms_turf_wr", "bms_dirt_wr",
    "bms_sprint_wr", "bms_mile_wr", "bms_middle_wr", "bms_long_wr",
    "bms_heavy_wr", "bms_growth_delta", "bms_n_starts",
    # P3
    "sire_sex_wr", "p3_weight_gap",
    # P4
    "p4_mutation_turf", "p4_mutation_dirt", "p4_n_ancestors",
    # P5
    "p5_dominance_score", "p5_n_bms_groups",
]


def _oof_to_jvlink_race_id(oof_id: str) -> str:
    """OOF race_id (16桁) → JV-Link race_id (12桁)。
    2022010506010101 → 202201050601
    """
    s = str(oof_id)
    return s[0:10] + s[14:16]


def _fetch_bloodline_store(horse_ids: list[str], jvlink_race_ids: list[str]) -> pd.DataFrame:
    """bloodline_feature_store から (horse_id, race_id) フィルタで取得する。
    race_idは JV-Link 形式 (12桁) を使用。
    """
    import psycopg2.extras
    query = """
        SELECT
            horse_id, race_id,
            sire_wr, sire_turf_wr, sire_dirt_wr,
            sire_sprint_wr, sire_mile_wr, sire_middle_wr, sire_long_wr,
            sire_heavy_wr, sire_growth_delta, sire_n_starts,
            bms_wr, bms_turf_wr, bms_dirt_wr,
            bms_sprint_wr, bms_mile_wr, bms_middle_wr, bms_long_wr,
            bms_heavy_wr, bms_growth_delta, bms_n_starts,
            sire_sex_wr, p3_weight_gap,
            p4_mutation_turf, p4_mutation_dirt, p4_n_ancestors,
            p5_dominance_score, p5_n_bms_groups
        FROM bloodline_feature_store
        WHERE horse_id = ANY(%s)
          AND race_id  = ANY(%s)
    """
    with get_jvdl_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, (horse_ids, jvlink_race_ids))
            rows = cur.fetchall()
    return pd.DataFrame(rows)


def enrich(in_path: Path, out_path: Path) -> None:
    log.info("入力Parquet読み込み: %s", in_path)
    df = pd.read_parquet(in_path)
    log.info("  %d行 / %dレース / %d列", len(df), df["race_id"].nunique(), len(df.columns))

    # OOF 16桁 → JV-Link 12桁 に変換してクエリ用カラムを作る
    df["_jvlink_race_id"] = df["race_id"].astype(str).map(_oof_to_jvlink_race_id)

    horse_ids       = df["horse_id"].astype(str).unique().tolist()
    jvlink_race_ids = df["_jvlink_race_id"].unique().tolist()

    log.info("bloodline_feature_store から取得 (%d頭 × %dレース)...", len(horse_ids), len(jvlink_race_ids))
    bfs = _fetch_bloodline_store(horse_ids, jvlink_race_ids)
    log.info("  取得: %d行", len(bfs))

    if bfs.empty:
        log.warning("bloodline_feature_store から0行取得。race_id変換を確認してください")
        bfs = pd.DataFrame(columns=["horse_id", "race_id"] + BLOODLINE_COLS)

    # bloodline_feature_store の race_id は JV-Link 形式 → _jvlink_race_id で JOIN
    bfs = bfs.rename(columns={"race_id": "_jvlink_race_id"})

    # 既存の bloodline 列は上書き
    drop_cols = [c for c in BLOODLINE_COLS if c in df.columns]
    if drop_cols:
        df = df.drop(columns=drop_cols)

    df = df.merge(
        bfs[["horse_id", "_jvlink_race_id"] + BLOODLINE_COLS],
        on=["horse_id", "_jvlink_race_id"],
        how="left",
    )
    df = df.drop(columns=["_jvlink_race_id"])

    for col in BLOODLINE_COLS:
        nan_pct = df[col].isna().mean() * 100
        if nan_pct < 99:
            log.info("  %-26s NaN=%.1f%%  mean=%s", col, nan_pct,
                     f"{df[col].mean():.4f}" if df[col].notna().any() else "N/A")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)
    log.info("保存完了: %s  shape=%s", out_path, df.shape)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="bloodline P1-P5 特徴量をParquetに追加")
    p.add_argument("--in",  dest="in_path",  type=Path, default=_DEFAULT_IN)
    p.add_argument("--out", dest="out_path", type=Path, default=_DEFAULT_OUT)
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    enrich(args.in_path, args.out_path)
