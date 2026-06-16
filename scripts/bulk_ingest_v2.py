"""
scripts/bulk_ingest_v2.py
==========================
JV-Data raw_*.txt ファイルを新パーサー経由で _v2 テーブルに一括投入する。

入力フォーマット:
  data/01_raw/raw_*.txt - 1行 = 1レコード (LF 区切り, cp932 raw bytes)
  各行の先頭2バイト = レコード種別 (RA / SE / HC / WC など)

ターゲットファイル (デフォルト):
  raw_DIFN.txt   - 差分蓄積データ (RA + SE + 各種速報)
  raw_SLOP.txt   - HC 坂路調教
  raw_WOOD.txt   - WC ウッドチップ調教

使い方:
    python scripts/bulk_ingest_v2.py
    python scripts/bulk_ingest_v2.py --files raw_DIFN.txt,raw_SLOP.txt,raw_WOOD.txt
    python scripts/bulk_ingest_v2.py --dry-run

M0-A Phase 2 実体化 (§7 Phase 2):
  1. 行単位 LF 分割 (raw_*.txt は loader.py が LF 終端で書き出す)
  2. parse_record でパース (鉄則1-2)
  3. BulkSink で UPSERT (BATCH=5000, 鮮度ガード付き)
  4. 失敗レコードは parse_dlq へ (鉄則3)
"""
from __future__ import annotations

import argparse
import io
import logging
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import psycopg2
import psycopg2.extras
from shared.config import DB_JVDL
from jvdl_parser.parser import (
    parse_record, parse_wh_entries, parse_o1_entries, stats,
)
from jvdl_parser.sink import BulkSink, _build_race_id
from jvdl_parser.processor import _write_dlq
from jvdl_parser.hook import post_recompute

# WH/O1 に加えて hook を発火させる追加種別
_HOOK_AFFECTING = frozenset(["WH", "WE", "O1", "RA", "SE", "AV", "JC", "TC", "CC"])

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

_RAW_DIR = Path(os.getenv("RAW_DATA_DIR", str(Path(__file__).resolve().parent.parent / "data" / "input")))
_DEFAULT_FILES = ["raw_DIFN.txt", "raw_SLOP.txt", "raw_WOOD.txt"]


def _ingest_file(
    fpath: Path,
    sink: BulkSink | None,
    conn,
    dry_run: bool,
) -> tuple[int, int, dict[str, int], set[str]]:
    """1 ファイルを行単位でパース → BulkSink へ投入。(ok, dlq, type_counts, affected_race_ids) を返す。"""
    ok = dlq = 0
    type_counts: dict[str, int] = defaultdict(int)
    affected: set[str] = set()
    dataspec = fpath.stem  # raw_DIFN -> DIFN
    log_interval = 500_000

    with open(fpath, "rb") as f:
        for line_no, line in enumerate(f, 1):
            # raw_*.txt フォーマット: loader.py が JVLink SafeArray(CRLF 含む) + \n を書き出す
            # Python の行イテレーターは \n で分割するため、1 行 = JVLink レコード(CRLF含む) そのもの。
            # rstrip(\r) してしまうと CRLF が剥がれてレコード長が 2 バイト短くなるため NG。
            raw = line  # CRLF は RECORD_DEFS の期待長に含まれる。stripping 不要。
            if len(raw) < 4:  # 超短行 (余分な \n 単体など) はスキップ
                continue
            try:
                result = parse_record(raw)
                if result is None:
                    ok += 1  # 未知種別スキップ (鉄則6)
                    continue

                rtype, row = result

                if not dry_run and sink is not None:
                    if rtype == "WH":
                        entries = parse_wh_entries(raw, row)
                        for entry in entries:
                            sink.feed("WH_ENTRY", entry)
                    elif rtype == "O1":
                        expanded = parse_o1_entries(raw, row)
                        for entry in expanded["win"]:
                            sink.feed("O1_WIN", entry)
                        for entry in expanded["place"]:
                            sink.feed("O1_PLACE", entry)
                    else:
                        sink.feed(rtype, row)

                # hook 用: レース影響範囲を収集
                if rtype in _HOOK_AFFECTING:
                    rid = _build_race_id(row)
                    if rid.strip("0"):
                        affected.add(rid)

                type_counts[rtype] += 1
                ok += 1

            except Exception as exc:
                if not dry_run:
                    # UPSERT 例外後はトランザクションが aborted 状態。
                    # ロールバックしてから DLQ に書くことで InFailedSqlTransaction を避ける。
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    _write_dlq(conn, raw, dataspec, exc)
                    try:
                        conn.commit()
                    except Exception:
                        pass
                dlq += 1

            if line_no % log_interval == 0:
                logger.info("  %s: %d 行処理済み (ok=%d dlq=%d)",
                            fpath.name, line_no, ok, dlq)

    if not dry_run and sink is not None:
        sink.flush()
        conn.commit()

    return ok, dlq, dict(type_counts), affected


def run_ingest(files: list[str], dry_run: bool = False, hook: bool = False) -> None:
    target_files: list[Path] = []
    for fname in files:
        p = _RAW_DIR / fname
        if not p.exists():
            logger.warning("ファイル未存在: %s (スキップ)", p)
            continue
        target_files.append(p)

    if not target_files:
        logger.error("有効なファイルが 1 件もありません。終了します。")
        sys.exit(2)

    total_size_mb = sum(f.stat().st_size for f in target_files) / (1024 * 1024)
    logger.info("対象ファイル: %d 件, 合計 %.1f MB (dry_run=%s)",
                len(target_files), total_size_mb, dry_run)

    total_ok = total_dlq = 0
    all_type_counts: dict[str, int] = defaultdict(int)
    all_affected: set[str] = set()
    t0 = time.monotonic()

    conn = psycopg2.connect(**DB_JVDL, connect_timeout=10)
    try:
        for i, fpath in enumerate(target_files, 1):
            sink = BulkSink(conn) if not dry_run else None
            logger.info("[%d/%d] 処理中: %s (%.1f MB)",
                        i, len(target_files), fpath.name,
                        fpath.stat().st_size / (1024 * 1024))
            ok, dlq, type_counts, affected = _ingest_file(fpath, sink, conn, dry_run)
            total_ok += ok
            total_dlq += dlq
            for k, v in type_counts.items():
                all_type_counts[k] += v
            all_affected |= affected
            elapsed = time.monotonic() - t0
            dlq_pct = dlq / max(ok + dlq, 1) * 100
            logger.info("  ok=%d dlq=%d dlq率=%.4f%% elapsed=%.0fs",
                        ok, dlq, dlq_pct, elapsed)
    finally:
        conn.close()

    elapsed = time.monotonic() - t0
    total = total_ok + total_dlq
    dlq_rate = total_dlq / max(total, 1) * 100

    print()
    print("=" * 62)
    print("  M0-A 投入レポート")
    print("=" * 62)
    print(f"  ファイル数            : {len(target_files)}")
    print(f"  ok レコード数         : {total_ok:>12,}")
    print(f"  DLQ レコード数        : {total_dlq:>12,}")
    status = "OK" if dlq_rate < 0.1 else "NG"
    print(f"  DLQ 率                : {dlq_rate:>11.4f}%  [{status} 受け入れ基準 < 0.1%]")
    print(f"  所要時間              : {elapsed:.1f}s")
    print()
    print("  レコード種別ごとの出現数:")
    for k, v in sorted(all_type_counts.items()):
        print(f"    {k:<22}: {v:>12,}")

    if not dry_run and total_dlq > 0:
        conn2 = psycopg2.connect(**DB_JVDL, connect_timeout=10)
        cur2 = conn2.cursor()
        cur2.execute(
            "SELECT error_class, COUNT(*) n FROM parse_dlq"
            " GROUP BY error_class ORDER BY n DESC LIMIT 10"
        )
        print()
        print("  DLQ エラークラス内訳 (上位10件):")
        for ec, cnt in cur2.fetchall():
            print(f"    {ec:<32}: {cnt:>8,}")

        if dlq_rate >= 0.1:
            cur2.execute(
                "SELECT record_type, dataspec, raw_record, error_detail"
                " FROM parse_dlq WHERE error_class = 'RecordLengthError'"
                " ORDER BY occurred_at DESC LIMIT 3"
            )
            rows = cur2.fetchall()
            if rows:
                print()
                print("  [RecordLengthError hex dump サンプル]")
                for rtype, dspec, raw_b, detail in rows:
                    raw_bytes = bytes(raw_b)
                    print(f"    type={rtype} spec={dspec} len={len(raw_bytes)} err={detail}")
                    print(f"    hex: {raw_bytes[:32].hex(' ')}")
        conn2.close()

    print("=" * 62)

    # ── hook: 影響レースの再計算ジョブを api_admin に投入 ──────────────────────
    if hook and not dry_run and all_affected:
        import os
        admin_url = os.environ.get("ADMIN_BASE_URL", "http://127.0.0.1:8003")
        api_key   = os.environ.get("ADMIN_API_KEY") or os.environ.get("API_KEY", "")
        logger.info("[Hook] 影響 race_ids=%d 件 → recompute_predictions ジョブ投入",
                    len(all_affected))
        try:
            result = post_recompute(all_affected, admin_base_url=admin_url, api_key=api_key)
            logger.info("[Hook] ジョブ投入完了: %s", result)
        except Exception as exc:
            logger.error("[Hook] ジョブ投入失敗 (続行): %s", exc)

    sys.exit(0 if dlq_rate < 0.1 else 1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="JV-Data raw_*.txt -> _v2 テーブル一括投入"
    )
    parser.add_argument(
        "--files",
        default=",".join(_DEFAULT_FILES),
        help="対象ファイル名(カンマ区切り)。デフォルト: raw_DIFN.txt,raw_SLOP.txt,raw_WOOD.txt",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="DB 書き込みなしでパースのみ"
    )
    parser.add_argument(
        "--hook", action="store_true",
        help="処理後に api_admin/jobs へ recompute_predictions ジョブを投入する",
    )
    args = parser.parse_args()

    run_ingest(
        files=[f.strip() for f in args.files.split(",") if f.strip()],
        dry_run=args.dry_run,
        hook=args.hook,
    )


if __name__ == "__main__":
    main()
