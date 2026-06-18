"""
jvdl_client/sync_jvdata.py
============================
JV-Link 差分取得 → DB投入 → ストア更新 → 予測再計算の一連フロー。

月曜朝バッチ / 管理画面 / 手動実行のいずれからも呼べる。

CLI:
    python -m jvdl_client.sync_jvdata                         # 全 dataspec 差分同期
    python -m jvdl_client.sync_jvdata --dataspecs RACE,DIFF   # 指定のみ
    python -m jvdl_client.sync_jvdata --full-setup            # option=4 で全量再取得
    python -m jvdl_client.sync_jvdata --no-stores --no-recompute  # 取得+投入のみ
    python -m jvdl_client.sync_jvdata --from-time 20260609000000
"""
from __future__ import annotations

import argparse
import datetime
import io
import logging
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv(_ROOT / ".env")

import psycopg2

from shared.config import DB_JVDL
from shared.notification.discord import send_embed

logger = logging.getLogger(__name__)

# ── デフォルト設定 ──────────────────────────────────────────────────────────

_DEFAULT_DATASPECS = ["RACE", "SLOP", "WOOD"]  # DIFF は無効な dataspec (JVOpen rc=-1)
_DEFAULT_FROM_TIME = "20220101000000"  # 初回(ウォーターマークなし)の場合
_RAW_DIR = Path(os.getenv("RAW_DATA_DIR", str(_ROOT / "data" / "input")))
_ADMIN_API_BASE = os.getenv("ADMIN_API_BASE", "http://127.0.0.1:8003")
_ADMIN_API_KEY  = os.getenv("ADMIN_API_KEY", "")


# ── メイン関数 ────────────────────────────────────────────────────────────────

def sync_from_jvlink(
    dataspecs: list[str] | None = None,
    from_time: str | None = None,
    output_dir: str | None = None,
    run_ingest: bool = True,
    run_stores: bool = True,
    run_recompute: bool = False,
    full_setup: bool = False,
    weekly: bool = False,
    dry_run: bool = False,
) -> dict[str, str]:
    """JV-Link から差分取得し、DB 投入 → ストア更新 → 予測再計算を実行する。

    Args:
        dataspecs:    取得するデータ種別リスト。省略時は全デフォルト。
        from_time:    "YYYYMMDDHHmmss"。省略時は sync_watermark テーブルから取得。
        output_dir:   raw ファイル書き出し先。省略時は RAW_DATA_DIR 環境変数。
        run_ingest:   True なら bulk_ingest_v2 を呼んで DB 投入する。
        run_stores:   True なら update_feature_stores ジョブを投入する。
        run_recompute: True なら recompute_predictions ジョブを投入する。
        full_setup:   True なら JVOpen option=4 で全量再取得する。
        weekly:       True なら JVOpen option=2 で今週分のみ取得する（木/金出馬確定用）。
                      full_setup=True の場合は full_setup が優先。
        dry_run:      True なら JVOpen のみ実行して readcount/downloadcount を確認。
                      ファイル書き込み・DB 投入・ジョブ投入を一切行わない。

    Returns:
        {dataspec: "ok" | "skip" | "dry-run: ..." | "ERROR: ..."} の辞書。
    """
    from jvdl_client.jvlink import JVLinkClient, ComImportError, OPT_SETUP, OPT_STORED_DIFF, OPT_WEEKLY

    if dataspecs is None:
        dataspecs = list(_DEFAULT_DATASPECS)

    raw_dir = Path(output_dir) if output_dir else _RAW_DIR
    raw_dir.mkdir(parents=True, exist_ok=True)

    if full_setup:
        option = OPT_SETUP
    elif weekly:
        option = OPT_WEEKLY
    else:
        option = OPT_STORED_DIFF
    now_str = datetime.datetime.now().strftime("%Y%m%d%H%M%S")

    results: dict[str, str] = {}

    # ── dry-run: JVOpen のみ、ファイル書き込み・DB 投入なし ───────────────────
    if dry_run:
        conn = psycopg2.connect(**DB_JVDL, connect_timeout=10)
        try:
            try:
                jv = JVLinkClient()
            except (ComImportError, ValueError) as e:
                logger.error("[sync_jvdata] %s", e)
                for ds in dataspecs:
                    results[ds] = f"ERROR: {e}"
                return results

            print(f"[dry-run] option={option} ({'full-setup' if full_setup else 'diff'})")
            print(f"{'dataspec':<8}  {'from_time':<14}  {'ret':>4}  {'readcount':>10}  {'downloadcount':>13}  lastfile_ts")
            print("-" * 75)
            with jv:
                for ds in dataspecs:
                    wm = JVLinkClient.get_watermark(conn, ds, _DEFAULT_FROM_TIME)
                    effective_from = from_time or wm
                    try:
                        info = jv.dry_run(ds, effective_from, option)
                        print(
                            f"{ds:<8}  {effective_from:<14}  {info['ret']:>4}  "
                            f"{info['readcount']:>10}  {info['downloadcount']:>13}  {info['lastfile_ts']}"
                        )
                        results[ds] = (
                            f"dry-run: readcount={info['readcount']} "
                            f"downloadcount={info['downloadcount']}"
                        )
                    except Exception as e:
                        logger.exception("[sync_jvdata] dry_run %s 失敗: %s", ds, e)
                        results[ds] = f"ERROR: {e}"
        finally:
            conn.close()
        return results

    # ── Step 1: JV-Link から各 dataspec を取得 ────────────────────────────────
    conn = psycopg2.connect(**DB_JVDL, connect_timeout=10)
    try:
        try:
            jv = JVLinkClient()
        except (ComImportError, ValueError) as e:
            logger.error("[sync_jvdata] %s", e)
            for ds in dataspecs:
                results[ds] = f"ERROR: {e}"
            _notify_result(results, now_str)
            return results

        with jv:
            for ds in dataspecs:
                wm = JVLinkClient.get_watermark(conn, ds, _DEFAULT_FROM_TIME)
                effective_from = from_time or wm
                raw_path = raw_dir / f"raw_{ds}.txt"

                logger.info("[sync_jvdata] %s: from_time=%s → %s", ds, effective_from, raw_path)
                try:
                    byte_count = 0
                    with open(raw_path, "wb") as fout:
                        for record in jv.fetch_stored(ds, effective_from, option):
                            fout.write(record)
                            if not record.endswith(b"\n"):
                                fout.write(b"\n")
                            byte_count += len(record)

                    if byte_count == 0:
                        logger.info("[sync_jvdata] %s: 新規データなし (skip)", ds)
                        results[ds] = "skip"
                    else:
                        logger.info("[sync_jvdata] %s: %.1f KB 書き出し完了", ds, byte_count / 1024)
                        JVLinkClient.set_watermark(conn, ds, now_str)
                        results[ds] = "ok"
                except Exception as e:
                    logger.exception("[sync_jvdata] %s 取得失敗: %s", ds, e)
                    results[ds] = f"ERROR: {e}"

    finally:
        conn.close()

    ok_specs = [ds for ds, v in results.items() if v == "ok"]

    # ── Step 2: DB 投入 ───────────────────────────────────────────────────────
    if run_ingest and ok_specs:
        logger.info("[sync_jvdata] bulk_ingest_v2 開始: files=%s", ok_specs)
        try:
            from scripts.bulk_ingest_v2 import run_ingest as _bulk_ingest  # type: ignore[import]
            file_names = [f"raw_{ds}.txt" for ds in ok_specs]
            _bulk_ingest(files=file_names, dry_run=False, hook=False)
            logger.info("[sync_jvdata] bulk_ingest_v2 完了")
        except Exception as e:
            logger.exception("[sync_jvdata] bulk_ingest_v2 失敗: %s", e)
            results["_ingest"] = f"ERROR: {e}"

    # ── Step 3: ストア更新ジョブ投入 ─────────────────────────────────────────
    if run_stores:
        _submit_job("update_feature_stores", {})

    # ── Step 4: 予測再計算ジョブ投入 ─────────────────────────────────────────
    if run_recompute:
        _submit_job("recompute_predictions", {"mode": "weekend"})

    _notify_result(results, now_str)
    return results


def _submit_job(job_type: str, params: dict) -> None:
    """api_admin に POST /jobs を投入する。失敗してもログのみ(fail-open)。"""
    try:
        import requests
        resp = requests.post(
            f"{_ADMIN_API_BASE}/jobs",
            json={"job_type": job_type, "params": params},
            headers={"X-API-Key": _ADMIN_API_KEY},
            timeout=10,
        )
        if resp.ok:
            jid = resp.json().get("id", "?")
            logger.info("[sync_jvdata] ジョブ投入: %s → #%s", job_type, jid)
        else:
            logger.warning("[sync_jvdata] ジョブ投入失敗: %s status=%d", job_type, resp.status_code)
    except Exception as e:
        logger.warning("[sync_jvdata] ジョブ投入エラー (%s): %s", job_type, e)


def _notify_result(results: dict[str, str], ts: str) -> None:
    """Discord に同期結果を通知する。DISCORD_WEBHOOK_URL 未設定時はスキップ。"""
    ok_count  = sum(1 for v in results.values() if v == "ok")
    err_count = sum(1 for v in results.values() if v.startswith("ERROR"))
    icon = "✅" if err_count == 0 else "❌"
    fields = [{"name": k, "value": v, "inline": True} for k, v in results.items()]
    send_embed(
        title=f"{icon} JV-Data 同期 {ts}",
        description=f"完了: {ok_count}/{len(results)} 成功 / {err_count} 失敗",
        color=0x00FF00 if err_count == 0 else 0xFF0000,
        fields=fields,
    )


# ── CLI エントリポイント ────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m jvdl_client.sync_jvdata",
        description="JV-Link 差分取得 → DB 投入 → ストア更新",
    )
    p.add_argument(
        "--dataspecs",
        default=",".join(_DEFAULT_DATASPECS),
        help="カンマ区切りのデータ種別 (デフォルト: RACE,DIFF,SLOP,WOOD)",
    )
    p.add_argument(
        "--from-time",
        default=None,
        metavar="YYYYMMDDHHmmss",
        help="取得開始時刻。省略時は sync_watermark テーブルの値を使用",
    )
    p.add_argument(
        "--output-dir",
        default=None,
        help=f"raw ファイル書き出し先 (デフォルト: {_RAW_DIR})",
    )
    p.add_argument(
        "--full-setup",
        action="store_true",
        help="JVOpen option=4 で全量再取得 (初回セットアップ用)",
    )
    p.add_argument(
        "--weekly",
        action="store_true",
        help="JVOpen option=2 で今週分のみ取得 (木/金出馬確定用)",
    )
    p.add_argument(
        "--no-ingest",
        action="store_true",
        help="DB 投入をスキップ (raw ファイル生成のみ)",
    )
    p.add_argument(
        "--no-stores",
        action="store_true",
        help="フィーチャーストア更新をスキップ",
    )
    p.add_argument(
        "--recompute",
        action="store_true",
        help="ストア更新後に予測再計算ジョブを投入する",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="JVOpen のみ実行して readcount/downloadcount を確認。ファイル書き込み・DB 投入なし",
    )
    return p


def main() -> None:
    import io as _io
    sys.stdout = _io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    args = _build_parser().parse_args()
    dataspecs = [s.strip() for s in args.dataspecs.split(",") if s.strip()]

    results = sync_from_jvlink(
        dataspecs=dataspecs,
        from_time=args.from_time,
        output_dir=args.output_dir,
        run_ingest=not args.no_ingest and not args.dry_run,
        run_stores=not args.no_stores and not args.dry_run,
        run_recompute=args.recompute and not args.dry_run,
        full_setup=args.full_setup,
        weekly=args.weekly,
        dry_run=args.dry_run,
    )

    err_count = sum(1 for v in results.values() if v.startswith("ERROR"))
    sys.exit(1 if err_count > 0 else 0)


if __name__ == "__main__":
    main()
