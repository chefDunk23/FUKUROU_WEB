"""
scripts/refresh_training_features_in_parquet.py
================================================
backfill 済みの training_feature_store から training_v2 特徴量を
Parquet に再ジョインする（上書き保存）。

backfill 前は avg_accel 等が 64% null だったが、518K 行 UPSERT 後に
2024-01-15〜2026-06-01 のデータが揃った。このスクリプトでその恩恵を Parquet に反映する。

特徴量の join ロジック:
    training_feature_store は (horse_id, target_date) を持つ週次データ。
    Parquet の各行は race_date を持つため、
    「各レース日以前で最も新しい target_date」の行を asof-merge で選択する。

Usage:
    py scripts/refresh_training_features_in_parquet.py
    py scripts/refresh_training_features_in_parquet.py --parquet outputs/bloodline_features_v1_2022plus.parquet
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

from dotenv import load_dotenv
load_dotenv(_ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

_DEFAULT_PARQUET = Path("outputs/bloodline_features_v1_2022plus.parquet")

_TFS_COLS = [
    "best_z_total",
    "z_trend_slope",
    "avg_accel",
    "session_count",
    "slope_ratio",
]


def _load_training_feature_store() -> pd.DataFrame:
    import psycopg2
    from shared.config import DB_JVDL
    conn = psycopg2.connect(**DB_JVDL)
    sql = """
        SELECT horse_id, target_date, best_z_total, z_trend_slope,
               avg_accel, session_count, slope_ratio
        FROM training_feature_store
        ORDER BY horse_id, target_date
    """
    log.info("training_feature_store を読み込み中...")
    df = pd.read_sql(sql, conn)
    conn.close()
    df["target_date"] = pd.to_datetime(df["target_date"])
    log.info("  %d 行 / %d 馬", len(df), df["horse_id"].nunique())
    return df


def _sql_asof_join(df: pd.DataFrame) -> pd.DataFrame:
    """PostgreSQL の LATERAL join を使って asof merge を行う。

    各 (horse_id, race_date) に対し、race_date 以前で最も新しい
    training_feature_store レコードを結合する。
    """
    import psycopg2
    import psycopg2.extras
    from shared.config import DB_JVDL

    conn = psycopg2.connect(**DB_JVDL)
    cur = conn.cursor()

    # パーケットの (race_id, horse_id, race_date) を一時テーブルに INSERT
    log.info("  一時テーブル作成...")
    cur.execute("""
        CREATE TEMP TABLE _tmp_parquet_keys (
            race_id TEXT,
            horse_id TEXT,
            race_date DATE
        )
    """)

    keys = df[["race_id", "horse_id", "race_date"]].copy()
    keys["race_date"] = keys["race_date"].dt.date
    rows = [tuple(r) for r in keys.itertuples(index=False)]

    psycopg2.extras.execute_values(
        cur,
        "INSERT INTO _tmp_parquet_keys VALUES %s",
        rows,
        page_size=5000,
    )
    conn.commit()
    log.info("  %d 行挿入完了", len(rows))

    # LATERAL join で asof 結合
    log.info("  LATERAL asof join 実行中...")
    sql = """
        SELECT
            p.race_id,
            p.horse_id,
            t.best_z_total,
            t.z_trend_slope,
            t.avg_accel,
            t.session_count,
            t.slope_ratio
        FROM _tmp_parquet_keys p
        LEFT JOIN LATERAL (
            SELECT best_z_total, z_trend_slope, avg_accel, session_count, slope_ratio
            FROM training_feature_store
            WHERE horse_id = p.horse_id
              AND target_date <= p.race_date
            ORDER BY target_date DESC
            LIMIT 1
        ) t ON TRUE
    """
    cur.execute(sql)
    rows_out = cur.fetchall()
    conn.close()

    join_df = pd.DataFrame(
        rows_out,
        columns=["race_id", "horse_id"] + _TFS_COLS,
    )
    log.info("  join 結果: %d 行", len(join_df))

    # 元 df に結合
    result = df.merge(join_df, on=["race_id", "horse_id"], how="left")
    return result


def refresh(parquet_path: Path) -> None:
    log.info("Parquet 読み込み: %s", parquet_path)
    df = pd.read_parquet(parquet_path)
    orig_shape = df.shape
    log.info("  shape=%s", orig_shape)

    # race_date を datetime に正規化
    df["race_date"] = pd.to_datetime(df["race_date"])

    # 既存の training 特徴量列をいったん削除（再ジョイン後に上書き）
    existing = [c for c in _TFS_COLS if c in df.columns]
    if existing:
        log.info("  既存 training 特徴量 %s を削除して再ジョイン", existing)
        df = df.drop(columns=existing)

    # datetime dtype を統一
    df["race_date"] = pd.to_datetime(df["race_date"])

    # SQL LATERAL join で asof 結合
    log.info("SQL asof join で training features を結合中...")
    merged = _sql_asof_join(df)

    # target_date 列は不要なので削除
    if "target_date" in merged.columns:
        merged = merged.drop(columns=["target_date"])

    # 元のソート順（race_id + umaban）に戻す
    if "umaban" in merged.columns:
        merged = merged.sort_values(["race_id", "umaban"]).reset_index(drop=True)

    log.info("  shape: %s → %s", orig_shape, merged.shape)
    for col in _TFS_COLS:
        if col in merged.columns:
            null_pct = merged[col].isna().mean() * 100
            n_filled = merged[col].notna().sum()
            log.info("  %s: null=%.1f%%  filled=%d 行", col, null_pct, n_filled)

    backup = parquet_path.with_suffix(".parquet.bak")
    if not backup.exists():
        import shutil
        shutil.copy2(parquet_path, backup)
        log.info("バックアップ作成: %s", backup)

    merged.to_parquet(parquet_path, index=False)
    log.info("保存完了: %s", parquet_path)
    log.info("")
    log.info("次のステップ:")
    log.info("  py scripts/train_v2_submodels.py --submodel training_v2 --parquet %s", parquet_path)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="training_feature_store を Parquet に再ジョインする")
    p.add_argument("--parquet", type=Path, default=_DEFAULT_PARQUET)
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    if not args.parquet.exists():
        log.error("Parquet が見つかりません: %s", args.parquet)
        sys.exit(1)
    refresh(args.parquet)
