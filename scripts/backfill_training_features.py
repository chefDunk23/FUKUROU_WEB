"""
scripts/backfill_training_features.py
=======================================
training_feature_store の 2024-01-01〜現在ギャップを週次で埋める。

Usage:
    py scripts/backfill_training_features.py
    py scripts/backfill_training_features.py --from 2024-01-15 --to 2026-06-03 --step 7

仕様:
  - start_date から step 日ごとに target_date を進め TrainingFeatureBatch を実行
  - UPSERT なので再実行してもべき等
  - 進捗は stdout に出力（既存 target_date はスキップしない — 上書き更新）
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

_ROOT = Path(__file__).parent.parent

from dotenv import load_dotenv
load_dotenv(_ROOT / ".env")

if sys.stdout is not None and getattr(sys.stdout, "encoding", None) and \
        sys.stdout.encoding.lower() not in ("utf-8", "utf-8-sig"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except AttributeError:
        pass

import os
from sqlalchemy import create_engine

_pw   = os.getenv("DB_JVDL_PASSWORD", "")
_host = os.getenv("DB_JVDL_HOST", "localhost")
_port = os.getenv("DB_JVDL_PORT", "5432")
_db   = os.getenv("DB_JVDL_NAME", "fukurou_jvdl")
_user = os.getenv("DB_JVDL_USER", "postgres")
_DATABASE_URL = f"postgresql+psycopg2://{_user}:{_pw}@{_host}:{_port}/{_db}"


def main() -> int:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="start", default="2024-01-15",
                        help="開始 target_date (YYYY-MM-DD)")
    parser.add_argument("--to", dest="end", default=str(date.today()),
                        help="終了 target_date (YYYY-MM-DD)")
    parser.add_argument("--step", type=int, default=7,
                        help="ステップ日数 (デフォルト: 7)")
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end   = date.fromisoformat(args.end)
    step  = timedelta(days=args.step)

    from ml.batch.training_feature_batch import TrainingFeatureBatch
    engine = create_engine(_DATABASE_URL)
    batch  = TrainingFeatureBatch(engine=engine)

    dates: list[date] = []
    d = start
    while d <= end:
        dates.append(d)
        d += step

    total = len(dates)
    print(f"バックフィル開始: {start} → {end} (ステップ{args.step}日, 計{total}回)", flush=True)
    print("-" * 60, flush=True)

    total_rows = 0
    t_start = time.time()

    for i, target in enumerate(dates, 1):
        t0 = time.time()
        try:
            n = batch.run(target_date=target)
        except Exception as e:
            print(f"[{i:4d}/{total}] {target}  ERROR: {e}")
            continue
        elapsed = time.time() - t0
        total_rows += n
        eta_s = (time.time() - t_start) / i * (total - i)
        print(
            f"[{i:4d}/{total}] {target}  {n:6d}行  {elapsed:.1f}s"
            f"  累計{total_rows:,}行  残り{eta_s/60:.0f}分",
            flush=True,
        )

    wall = time.time() - t_start
    print("-" * 60)
    print(f"完了: 合計 {total_rows:,} 行 UPSERT / {wall/60:.1f}分")
    return 0


if __name__ == "__main__":
    sys.exit(main())
