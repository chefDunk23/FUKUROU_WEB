"""
scripts/health_check.py
=======================
システム健全性スナップショット。

  py scripts/health_check.py          # stdout のみ
  py scripts/health_check.py --discord  # stdout + Discord 通知 (DISCORD_WEBHOOK_URL 要)
  py scripts/health_check.py --fail-on-critical  # 問題あり時に exit 1

Worker から呼ぶ例:
  from scripts.health_check import run_health_check
  run_health_check()
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv(_ROOT / ".env")

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf-8-sig"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except AttributeError:
        pass

import psycopg2
from shared.config import DB_JVDL, DB_V2

logger = logging.getLogger(__name__)

# ── 鮮度しきい値 (日数) ─────────────────────────────────────────────────────
_THRESHOLDS: dict[str, tuple[int, int]] = {
    "jockey_feature_store":   (1, 3),    # 日次更新想定
    "trainer_feature_store":  (1, 3),    # 日次更新想定
    "sire_feature_store":     (1, 3),    # 日次更新想定
    "horse_rating_store":     (2, 7),    # レース結果後に更新
    "training_feature_store": (7, 30),   # 週次更新想定（training_data 連動）
    "chokyo_scores":          (7, 30),   # レース前に更新
    "aptitude_scores":        (7, 30),   # レース前に更新
}


# ── データクラス ─────────────────────────────────────────────────────────────
@dataclass
class FeatureFreshness:
    name: str
    last_date: date | None
    lag_days: int | None
    status: str  # "ok" | "warn" | "critical" | "empty"


@dataclass
class JobSummary:
    period_days: int
    done: int
    failed: int
    running: int
    pending: int


@dataclass
class CacheCount:
    predictions_today: int
    predictions_total: int
    detail_cache_today: int
    detail_cache_total: int
    as_of: date


@dataclass
class HealthReport:
    generated_at: datetime
    feature_freshness: list[FeatureFreshness] = field(default_factory=list)
    jobs: JobSummary | None = None
    cache: CacheCount | None = None
    keiba_v2_last_race: date | None = None
    errors: list[str] = field(default_factory=list)


# ── クエリ ───────────────────────────────────────────────────────────────────
def _jvdl_conn() -> Any:
    return psycopg2.connect(**DB_JVDL)


def _v2_conn() -> Any:
    return psycopg2.connect(**DB_V2)


_FEATURE_STORE_QUERIES: dict[str, str] = {
    "jockey_feature_store":   "SELECT MAX(target_date) FROM jockey_feature_store",
    "trainer_feature_store":  "SELECT MAX(target_date) FROM trainer_feature_store",
    "sire_feature_store":     "SELECT MAX(target_date) FROM sire_feature_store",
    "horse_rating_store":     "SELECT MAX(race_date)   FROM horse_rating_store",
    "training_feature_store": "SELECT MAX(target_date) FROM training_feature_store",
    "chokyo_scores":          "SELECT MAX(computed_at::date) FROM chokyo_scores",
    "aptitude_scores":        "SELECT MAX(computed_at::date) FROM aptitude_scores",
}


def _collect_feature_freshness(today: date) -> list[FeatureFreshness]:
    results: list[FeatureFreshness] = []
    try:
        conn = _jvdl_conn()
        cur = conn.cursor()
        for name, sql in _FEATURE_STORE_QUERIES.items():
            try:
                cur.execute(sql)
                row = cur.fetchone()
                last_val = row[0] if row else None
                if last_val is None:
                    results.append(FeatureFreshness(name, None, None, "empty"))
                    continue
                last_date = last_val if isinstance(last_val, date) else last_val.date()
                lag = (today - last_date).days
                warn_days, crit_days = _THRESHOLDS.get(name, (1, 3))
                if lag >= crit_days:
                    status = "critical"
                elif lag >= warn_days:
                    status = "warn"
                else:
                    status = "ok"
                results.append(FeatureFreshness(name, last_date, lag, status))
            except Exception as e:
                results.append(FeatureFreshness(name, None, None, "critical"))
                logger.warning("[health_check] %s クエリ失敗: %s", name, e)
        conn.close()
    except Exception as e:
        logger.error("[health_check] fukurou_jvdl 接続失敗: %s", e)
    return results


def _collect_jobs(period_days: int = 7) -> JobSummary | None:
    try:
        conn = _jvdl_conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                COUNT(*) FILTER (WHERE status = 'done')    AS done,
                COUNT(*) FILTER (WHERE status = 'failed')  AS failed,
                COUNT(*) FILTER (WHERE status = 'running') AS running,
                COUNT(*) FILTER (WHERE status = 'pending') AS pending
            FROM jobs
            WHERE created_at >= NOW() - (%s || ' days')::INTERVAL
            """,
            (str(period_days),),
        )
        row = cur.fetchone()
        conn.close()
        if row:
            return JobSummary(period_days, int(row[0]), int(row[1]), int(row[2]), int(row[3]))
    except Exception as e:
        logger.error("[health_check] jobs クエリ失敗: %s", e)
    return None


def _collect_cache(today: date) -> CacheCount | None:
    try:
        conn = _jvdl_conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                COUNT(*) FILTER (WHERE predicted_at::date = CURRENT_DATE) AS today,
                COUNT(*) AS total
            FROM race_predictions
            """
        )
        pred = cur.fetchone()
        cur.execute(
            """
            SELECT
                COUNT(*) FILTER (WHERE computed_at::date = CURRENT_DATE) AS today,
                COUNT(*) AS total
            FROM race_detail_cache
            """
        )
        det = cur.fetchone()
        conn.close()
        return CacheCount(
            predictions_today=int(pred[0]),
            predictions_total=int(pred[1]),
            detail_cache_today=int(det[0]),
            detail_cache_total=int(det[1]),
            as_of=today,
        )
    except Exception as e:
        logger.error("[health_check] cache クエリ失敗: %s", e)
    return None


def _collect_keiba_v2_last() -> date | None:
    try:
        conn = _v2_conn()
        cur = conn.cursor()
        cur.execute("SELECT MAX(race_date) FROM races")
        row = cur.fetchone()
        conn.close()
        if row and row[0]:
            return row[0] if isinstance(row[0], date) else row[0].date()
    except Exception as e:
        logger.error("[health_check] keiba_v2.races クエリ失敗: %s", e)
    return None


# ── レポート生成 ─────────────────────────────────────────────────────────────
def run_health_check() -> HealthReport:
    now = datetime.now(tz=timezone.utc)
    today = now.date()
    report = HealthReport(generated_at=now)
    report.feature_freshness = _collect_feature_freshness(today)
    report.jobs = _collect_jobs()
    report.cache = _collect_cache(today)
    report.keiba_v2_last_race = _collect_keiba_v2_last()
    return report


def _status_icon(status: str) -> str:
    return {"ok": "OK   ", "warn": "WARN ", "critical": "CRIT ", "empty": "EMPTY"}.get(status, "?    ")


def format_report_text(report: HealthReport) -> str:
    sep = "=" * 64
    lines: list[str] = [
        sep,
        f"福郎 ヘルスチェック  {report.generated_at.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        sep,
        "",
        "[Feature Store 鮮度]",
    ]
    for fs in report.feature_freshness:
        lag_str = f"{fs.lag_days}日" if fs.lag_days is not None else "データなし"
        date_str = str(fs.last_date) if fs.last_date else "N/A"
        icon = _status_icon(fs.status)
        lines.append(f"  {icon}  {fs.name:<28}  {date_str}  ({lag_str})")

    lines.append("")
    lines.append(f"[ジョブ直近{report.jobs.period_days}日]" if report.jobs else "[ジョブ] N/A")
    if report.jobs:
        j = report.jobs
        lines.append(
            f"  完了: {j.done}  失敗: {j.failed}  実行中: {j.running}  待機: {j.pending}"
        )

    lines.append("")
    lines.append("[本日分キャッシュ]")
    if report.cache:
        c = report.cache
        lines.append(f"  race_predictions  : 本日{c.predictions_today}行 / 合計{c.predictions_total}行")
        lines.append(f"  race_detail_cache : 本日{c.detail_cache_today}行 / 合計{c.detail_cache_total}行")
    else:
        lines.append("  N/A")

    lines.append("")
    lines.append("[keiba_v2.races 最終日]")
    lines.append(f"  {report.keiba_v2_last_race or 'N/A'}")

    if report.errors:
        lines.append("")
        lines.append("[エラー]")
        for e in report.errors:
            lines.append(f"  {e}")

    lines.append(sep)
    return "\n".join(lines)


def _has_problem(report: HealthReport) -> bool:
    for fs in report.feature_freshness:
        if fs.status in ("critical", "empty"):
            return True
    if report.jobs and report.jobs.failed > 0:
        return True
    return False


def send_discord(report: HealthReport, webhook_url: str) -> None:
    text = format_report_text(report)
    problem = _has_problem(report)
    prefix = ":red_circle:" if problem else ":white_check_mark:"
    payload = json.dumps({"content": f"{prefix} **[福郎] ヘルスチェック**\n```\n{text}\n```"})
    req = urllib.request.Request(
        webhook_url,
        data=payload.encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=10)


# ── CLI ──────────────────────────────────────────────────────────────────────
def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="福郎システムヘルスチェック")
    parser.add_argument("--discord", action="store_true", help="Discord に結果を送信")
    parser.add_argument("--fail-on-critical", action="store_true", help="critical 項目があれば exit 1")
    args = parser.parse_args()

    report = run_health_check()
    text = format_report_text(report)
    print(text)

    if args.discord:
        webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "")
        if not webhook_url:
            print("警告: DISCORD_WEBHOOK_URL が設定されていないため Discord 通知をスキップ")
        else:
            try:
                send_discord(report, webhook_url)
                print("Discord 通知送信完了")
            except Exception as e:
                print(f"Discord 通知失敗: {e}")

    if args.fail_on_critical and _has_problem(report):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
