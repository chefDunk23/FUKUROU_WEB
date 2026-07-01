"""
shared/worker/job_runner.py
============================
ジョブキューワーカー（常駐プロセス）。

fukurou_jvdl.jobs テーブルを POLL_INTERVAL 秒ごとにポーリングし、
queued ジョブを 1 件ずつ取り出して逐次実行する。

多重起動防止: pg_try_advisory_lock で 1 ワーカーのみ動作保証。
              ジョブ単位の排他は SELECT ... FOR UPDATE SKIP LOCKED。

起動方法:
    python -m shared.worker.job_runner
    # または
    python shared/worker/job_runner.py

プロセス管理:
    pm2 start "python -m shared.worker.job_runner" --name jvdl-worker
"""
from __future__ import annotations

import logging
import os
import sys
import time
import traceback
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

_ROOT = Path(__file__).parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import psycopg2
import psycopg2.extras
from apscheduler.schedulers.background import BackgroundScheduler

from shared.config import DB_JVDL

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s [Worker] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── 定数 ─────────────────────────────────────────────────────────────────────

POLL_INTERVAL = 5          # 秒
LOG_MAX_LINES = 50         # log_tail に保持する最大行数
_HEALTH_CHECK_HOUR_JST = 9  # 毎日 09:00 JST にヘルスチェック
_ADVISORY_LOCK_KEY = 42002  # ワーカー起動唯一性保証（batch_predictor の 42001 と別）

# ── アイドル自動終了 ──────────────────────────────────────────────────────────
# 常駐させない運用方針: 起動時にキューを一括処理（ドレイン）した後、
# この秒数だけ新規ジョブが来なければ自動終了する。
# 0 を指定すると無効化（旧来通り常駐し続ける）。
_IDLE_EXIT_ENV = "WORKER_IDLE_EXIT_SECONDS"
_DEFAULT_IDLE_EXIT_SECONDS = 120

# スケジュール定義（hour/minute は JST）
_SCHEDULES = [
    # 毎日 09:00 — health_check（_scheduled_health_check を直接呼ぶ）
    {"kind": "direct",  "day_of_week": "*",    "hour": 9,  "minute": 0,  "fn": "_scheduled_health_check"},
    # 金曜 21:00 — 週末レース事前計算をジョブキューに投入
    {"kind": "enqueue", "day_of_week": "fri",  "hour": 21, "minute": 0,
     "job_type": "recompute_predictions", "params": {"mode": "weekend"}},
    # 土曜 08:30 — 当日レース再計算をジョブキューに投入
    {"kind": "enqueue", "day_of_week": "sat",  "hour": 8,  "minute": 30,
     "job_type": "recompute_predictions", "params": {"mode": "today"}},
    # 日曜 08:30 — 当日レース再計算をジョブキューに投入
    {"kind": "enqueue", "day_of_week": "sun",  "hour": 8,  "minute": 30,
     "job_type": "recompute_predictions", "params": {"mode": "today"}},
    # 月曜 07:00 — 先週末の実績を tipster_results に取り込む
    {"kind": "enqueue", "day_of_week": "mon",  "hour": 7,  "minute": 0,
     "job_type": "update_tipster_results", "params": {}},
]

# ── ジョブハンドラ登録 ─────────────────────────────────────────────────────────

_HANDLERS: dict[str, Callable[[dict, "JobContext"], None]] = {}


def register(job_type: str):
    """job_type ハンドラをデコレータで登録する。"""
    def _dec(fn: Callable):
        _HANDLERS[job_type] = fn
        return fn
    return _dec


# ── JobContext ────────────────────────────────────────────────────────────────

@dataclass
class JobContext:
    """ハンドラからジョブ状態を更新するためのコンテキスト。"""
    job_id: int
    _conn: psycopg2.extensions.connection
    _log_buf: deque = None  # type: ignore[assignment]
    _artifact_path: str | None = None

    def __post_init__(self):
        self._log_buf = deque(maxlen=LOG_MAX_LINES)

    def set_artifact(self, path: str) -> None:
        """ジョブ完了時に保存する成果物パスを設定する。"""
        self._artifact_path = path

    def report_progress(self, pct: int) -> None:
        pct = max(0, min(100, pct))
        with self._conn.cursor() as cur:
            cur.execute(
                "UPDATE jobs SET progress = %s WHERE id = %s",
                (pct, self.job_id),
            )
        self._conn.commit()

    def append_log(self, line: str) -> None:
        self._log_buf.append(line)
        tail = "\n".join(self._log_buf)
        with self._conn.cursor() as cur:
            cur.execute(
                "UPDATE jobs SET log_tail = %s WHERE id = %s",
                (tail, self.job_id),
            )
        self._conn.commit()


# ── ジョブハンドラ実装 ─────────────────────────────────────────────────────────

# ── フィーチャーストア名 → バッチ識別子マップ ─────────────────────────────────

_STORE_KEY_MAP: dict[str, str] = {
    "training":     "training",
    "condition":    "condition",
    "chokyo":       "chokyo",
    "jockey":       "jockey",
    "trainer":      "trainer",
    "sire":         "sire",
    "horse_rating": "horse_rating",
    "synergy":      "synergy",
    "course":       "course",
    "aptitude":     "aptitude",
}


@register("update_feature_stores")
def _handle_update_feature_stores(params: dict, ctx: JobContext) -> None:
    """全フィーチャーストアを更新する。

    params:
        stores: list[str] | None — 省略時は全ストア
                例: ["training", "jockey", "horse_rating"]
                キー: training / condition / jockey / trainer / sire /
                      horse_rating / synergy / course
        target_date: str | None — "YYYY-MM-DD"。省略時は今日
    """
    from datetime import date as _date

    from ml.db import engine as _engine
    from ml.batch.training_feature_batch import TrainingFeatureBatch
    from ml.batch.condition_match_batch import ConditionMatchBatch
    from ml.batch.chokyo_score_batch import run as _run_chokyo
    from ml.batch.aptitude_score_batch import run as _run_aptitude
    from ml.batch.external_factor_store import ExternalFactorStoreBatch
    from ml.batch.horse_rating_batch import HorseRatingBatch
    from ml.batch.synergy_store_batch import SynergyStoreBatch
    from ml.batch.course_profile_store import CourseProfileStoreBatch
    from shared.notification.discord import send_embed

    target_date_str: str | None = params.get("target_date")
    if target_date_str:
        try:
            target_date = _date.fromisoformat(target_date_str)
        except ValueError:
            target_date = _date.today()
    else:
        target_date = _date.today()

    requested: list[str] = params.get("stores") or list(_STORE_KEY_MAP.keys())
    requested_set = {_STORE_KEY_MAP.get(s, s) for s in requested}

    ctx.append_log(f"[update_feature_stores] target_date={target_date} stores={sorted(requested_set)}")
    ctx.report_progress(5)

    results: dict[str, str] = {}  # store_key → "ok" | "skip" | "ERROR: ..."

    # ── Step 1: training（condition より先に実行必須）────────────────────────
    _training_n: int = 0
    if "training" in requested_set:
        ctx.append_log("[1/8] training_feature_batch 開始")
        try:
            _training_n = TrainingFeatureBatch(engine=_engine).run(target_date=target_date)
            results["training"] = "ok"
            ctx.append_log(f"[1/8] training 完了: {_training_n} 行 UPSERT")
        except Exception as e:
            results["training"] = f"ERROR: {e}"
            ctx.append_log(f"[1/8] training 失敗: {e}")
    else:
        results["training"] = "skip"

    ctx.report_progress(15)

    # ── Step 2: condition（training 依存）───────────────────────────────────
    if "condition" in requested_set:
        if _training_n == 0:
            results["condition"] = "skip"
            ctx.append_log("[2/8] condition スキップ (training 0行 -> 対象データなし)")
        else:
            ctx.append_log("[2/8] condition_match_batch 開始")
            try:
                n = ConditionMatchBatch(engine=_engine).run(target_date=target_date)
                results["condition"] = "ok"
                ctx.append_log(f"[2/8] condition 完了: {n} 行 UPSERT")
            except Exception as e:
                results["condition"] = f"ERROR: {e}"
                ctx.append_log(f"[2/8] condition 失敗: {e}")
    else:
        results["condition"] = "skip"

    ctx.report_progress(25)

    # ── Step 3: chokyo（training 後、調教rawデータが必要）──────────────────
    if "chokyo" in requested_set:
        ctx.append_log("[3/8] chokyo_score_batch 開始")
        try:
            n = _run_chokyo(from_year=2015)
            results["chokyo"] = "ok"
            ctx.append_log(f"[3/8] chokyo 完了: {n} 行 UPSERT")
        except Exception as e:
            results["chokyo"] = f"ERROR: {e}"
            ctx.append_log(f"[3/8] chokyo 失敗: {e}")
    else:
        results["chokyo"] = "skip"

    ctx.report_progress(40)

    # ── Step 4-8: 独立バッチ（直列実行、並列化は将来課題）─────────────────

    # horse_rating は差分更新（全期間再計算を避ける）
    def _horse_rating_run() -> int:
        from sqlalchemy import text as _text
        with _engine.connect() as _conn:
            row = _conn.execute(_text("SELECT MAX(race_date) FROM horse_rating_store")).fetchone()
        max_stored = row[0] if row and row[0] else None
        from_date_hr = (max_stored + __import__("datetime").timedelta(days=1)) if max_stored else None
        ctx.append_log(f"  horse_rating from_date={from_date_hr}")
        return HorseRatingBatch(target_date=target_date, engine=_engine).run(from_date=from_date_hr)

    independent = [
        ("jockey/trainer/sire", "external",
         lambda: ExternalFactorStoreBatch(target_date=target_date).run()),
        ("horse_rating", "horse_rating", _horse_rating_run),
        ("synergy", "synergy",
         lambda: SynergyStoreBatch(engine=_engine).run(target_date=target_date)),
        ("course", "course",
         lambda: CourseProfileStoreBatch(target_date=target_date, engine=_engine).run()),
        ("aptitude", "aptitude",
         lambda: _run_aptitude(from_year=2015)),
    ]

    pct_step = 10
    for idx, (label, key, run_fn) in enumerate(independent, start=4):
        if key in requested_set or any(s in requested_set for s in ("jockey", "trainer", "sire") if key == "external"):
            ctx.append_log(f"[{idx}/8] {label} 開始")
            try:
                n = run_fn()
                results[key] = "ok"
                ctx.append_log(f"[{idx}/8] {label} 完了: {n} 行 UPSERT")
            except Exception as e:
                results[key] = f"ERROR: {e}"
                ctx.append_log(f"[{idx}/8] {label} 失敗: {e}")
        else:
            results[key] = "skip"
        ctx.report_progress(40 + idx * pct_step)

    # ── 集計 ─────────────────────────────────────────────────────────────────
    ok_count    = sum(1 for v in results.values() if v == "ok")
    err_count   = sum(1 for v in results.values() if v.startswith("ERROR"))
    skip_count  = sum(1 for v in results.values() if v == "skip")
    total_run   = ok_count + err_count

    summary = f"完了: {ok_count}/{total_run} 成功 / {skip_count} スキップ / {err_count} 失敗"
    ctx.append_log(f"[update_feature_stores] {summary}")
    ctx.report_progress(100)

    # ── Discord 通知 ──────────────────────────────────────────────────────────
    status_color = 0x00FF00 if err_count == 0 else 0xFF0000
    status_icon  = "✅" if err_count == 0 else "❌"
    fields = [
        {"name": k, "value": v, "inline": True}
        for k, v in results.items()
    ]
    send_embed(
        title=f"{status_icon} フィーチャーストア更新 {target_date}",
        description=summary,
        color=status_color,
        fields=fields,
    )

    if err_count > 0:
        failed_stores = [k for k, v in results.items() if v.startswith("ERROR")]
        raise RuntimeError(f"一部バッチ失敗: {failed_stores}")


@register("sync_races_from_jvdl")
def _handle_sync_races_from_jvdl(params: dict, ctx: JobContext) -> None:
    """DB_JVDL の races_v2 / race_entries_v2 を DB_V2 (fukurou_keiba_v2) に同期する。

    bulk_ingest_v2.py で投入した RA/SE レコードを予測 DB に反映することで、
    AI_FUKUROU_KEIBA_Ver2 パイプラインへの依存を解消する。

    params:
        from_date: str | None — "YYYY-MM-DD"。省略時は過去 90 日分のみ
                   "all" を指定すると全期間を同期（初回のみ推奨）
    """
    import datetime as _dt

    from shared.config import DB_V2

    from_date_str: str | None = params.get("from_date")
    if from_date_str == "all":
        from_yyyymmdd = "00000000"
    elif from_date_str:
        try:
            d = _dt.date.fromisoformat(from_date_str)
            from_yyyymmdd = d.strftime("%Y%m%d")
        except ValueError:
            from_yyyymmdd = (_dt.date.today() - _dt.timedelta(days=90)).strftime("%Y%m%d")
    else:
        from_yyyymmdd = (_dt.date.today() - _dt.timedelta(days=90)).strftime("%Y%m%d")

    ctx.append_log(f"[sync_races_from_jvdl] from={from_date_str!r} (yyyymmdd>={from_yyyymmdd})")
    ctx.report_progress(5)

    jvdl_conn = psycopg2.connect(**DB_JVDL)
    v2_conn   = psycopg2.connect(**DB_V2)

    races_upserted = 0
    entries_upserted = 0

    try:
        # ── Step 1: races_v2 を取得 ──────────────────────────────────────────
        with jvdl_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT race_id, kaisai_year, kaisai_monthday,
                       keibajo_code, kaisai_kai, kaisai_nichime, race_num,
                       race_name_hondai, race_name_short_10, race_name_short_6,
                       grade_code, kyoso_shubetsu, distance, track_code,
                       hassou_time, toroku_tosu, shusso_tosu,
                       tenko_code, shiba_baba_code, dirt_baba_code,
                       data_kubun
                FROM   races_v2
                WHERE  kaisai_year || kaisai_monthday >= %s
                ORDER  BY kaisai_year, kaisai_monthday
            """, (from_yyyymmdd,))
            race_rows = cur.fetchall()

        ctx.append_log(f"  races_v2 取得: {len(race_rows)} 行")
        ctx.report_progress(20)

        # ── Step 2: races → DB_V2 UPSERT ─────────────────────────────────────
        def _si(v):
            try:
                return int(v) if v is not None else None
            except (TypeError, ValueError):
                return None

        race_records = []
        valid_race_ids = []
        for r in race_rows:
            year     = (r["kaisai_year"] or "").strip()
            monthday = (r["kaisai_monthday"] or "").strip()
            if len(year) != 4 or len(monthday) != 4:
                continue
            race_date = f"{year}-{monthday[:2]}-{monthday[2:]}"
            race_records.append((
                r["race_id"],
                race_date,
                (r["keibajo_code"] or "").strip().zfill(2),
                _si(r["kaisai_kai"]),
                _si(r["kaisai_nichime"]),
                _si(r["race_num"]),
                r["race_name_hondai"],
                r["race_name_short_10"],
                r["race_name_short_6"],
                r["grade_code"],
                r["kyoso_shubetsu"],
                _si(r["distance"]),
                r["track_code"],
                r["hassou_time"],
                _si(r["toroku_tosu"]),
                _si(r["shusso_tosu"]),
                r["tenko_code"],
                r["shiba_baba_code"],
                r["dirt_baba_code"],
                r["data_kubun"],
            ))
            valid_race_ids.append(r["race_id"])

        if race_records:
            with v2_conn.cursor() as cur:
                psycopg2.extras.execute_values(cur, """
                    INSERT INTO races (
                        id, race_date, keibajo_code, kaiji, nichiji, race_num,
                        race_name_hondai, race_name_short_10, race_name_short_6,
                        grade_code, race_syubetsu_code, distance, track_code,
                        hassou_time, touroku_tosu, syusso_tosu,
                        tenko_code, shiba_baba_code, dirt_baba_code, data_kubun
                    ) VALUES %s
                    ON CONFLICT (id) DO UPDATE SET
                        race_date       = EXCLUDED.race_date,
                        syusso_tosu     = EXCLUDED.syusso_tosu,
                        tenko_code      = EXCLUDED.tenko_code,
                        shiba_baba_code = EXCLUDED.shiba_baba_code,
                        dirt_baba_code  = EXCLUDED.dirt_baba_code,
                        data_kubun      = EXCLUDED.data_kubun
                """, race_records, page_size=500)
            v2_conn.commit()
            races_upserted = len(race_records)
            ctx.append_log(f"  races UPSERT: {races_upserted} 行")

        ctx.report_progress(50)

        # ── Step 3: race_entries_v2 を取得 ────────────────────────────────────
        if not valid_race_ids:
            ctx.append_log("  同期対象レースなし。完了。")
            ctx.report_progress(100)
            return

        with jvdl_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT race_id, umaban, blood_no, horse_name, sex_cd, horse_age,
                       chokyosi_code, kinryo, blinker, kishu_code,
                       horse_weight, zogen_fugo, zogen_sa, ijyo_kubun,
                       nyusen_juni, kakutei_chakujun, race_time,
                       corner_1, corner_2, corner_3, corner_4,
                       tansho_odds, tansho_ninki, kohan_4f, kohan_3f,
                       data_kubun
                FROM   race_entries_v2
                WHERE  race_id = ANY(%s)
            """, (valid_race_ids,))
            entry_rows = cur.fetchall()

        ctx.append_log(f"  race_entries_v2 取得: {len(entry_rows)} 行")
        ctx.report_progress(70)

        # ── Step 4: race_entries → DB_V2 UPSERT ──────────────────────────────
        # race_entries_v2 は同一 (race_id, blood_no) でも data_kubun（速報→確定）ごとに
        # 別行として保持されており、umaban が確定前(0等)と確定後で異なることがある。
        # race_entries は horse_id ごとに1行のみのため、(race_id, blood_no) で最新
        # （umaban が確定済み=非0を優先し、同条件なら data_kubun が大きい方）の行だけ残す。
        best_by_horse: dict[tuple[str, str], dict] = {}
        for e in entry_rows:
            bn = e.get("blood_no")
            if not bn:
                continue
            key = (e["race_id"], bn)
            cur_best = best_by_horse.get(key)
            if cur_best is None:
                best_by_horse[key] = e
                continue
            cur_final = (cur_best["umaban"] or 0) != 0
            new_final = (e["umaban"] or 0) != 0
            if (new_final, str(e.get("data_kubun") or "")) > (
                cur_final, str(cur_best.get("data_kubun") or "")
            ):
                best_by_horse[key] = e
        entry_rows = [e for e in entry_rows if not e.get("blood_no")] + list(best_by_horse.values())

        entry_records = []
        for e in entry_rows:
            kinryo = e.get("kinryo")
            basis_weight = round(kinryo / 10.0, 1) if kinryo is not None else None
            entry_records.append((
                e["race_id"],
                e["umaban"],
                e.get("blood_no"),        # → horse_id
                e.get("horse_name"),
                e.get("sex_cd"),
                e.get("horse_age"),       # → age
                e.get("chokyosi_code"),   # → trainer_cd
                basis_weight,             # kinryo/10 → basis_weight (NUMERIC 4,1)
                e.get("blinker"),         # → blinker_cd
                e.get("kishu_code"),      # → jockey_cd
                e.get("horse_weight"),
                e.get("zogen_fugo"),      # → weight_sign
                e.get("zogen_sa"),        # → weight_diff
                e.get("ijyo_kubun"),      # → abnormal_cd
                e.get("nyusen_juni"),     # → nyuusen_order
                e.get("kakutei_chakujun"),
                e.get("race_time"),
                e.get("corner_1"),
                e.get("corner_2"),
                e.get("corner_3"),
                e.get("corner_4"),
                e.get("tansho_odds"),     # → tan_odds
                e.get("tansho_ninki"),    # → ninki
                e.get("kohan_4f"),        # → go_4f_time
                e.get("kohan_3f"),        # → go_3f_time
                e.get("data_kubun"),
            ))

        # race_entries には PK (race_id, umaban) のほかに、馬番変更（出走取消の再出走等）を
        # 想定した部分一意インデックス uq_re_race_horse (race_id, horse_id) も存在する。
        # ON CONFLICT は1つの制約しか対象にできないため、horse_id の umaban が前回同期時から
        # 変わったケースで uq_re_race_horse 違反になり UPSERT が失敗する。
        # jvdl側にエントリが存在するレースのみ削除してから挿入し直すことで両制約の衝突を避ける
        # （jvdl側が未バックフィルのレースまで削除してしまわないよう、対象を entry_rows に限定）。
        if entry_records:
            entry_race_ids = list({e["race_id"] for e in entry_rows})
            with v2_conn.cursor() as cur:
                cur.execute("DELETE FROM race_entries WHERE race_id = ANY(%s)", (entry_race_ids,))
            with v2_conn.cursor() as cur:
                psycopg2.extras.execute_values(cur, """
                    INSERT INTO race_entries (
                        race_id, umaban, horse_id, horse_name, sex_cd, age,
                        trainer_cd, basis_weight, blinker_cd, jockey_cd,
                        horse_weight, weight_sign, weight_diff, abnormal_cd,
                        nyuusen_order, kakutei_chakujun, race_time,
                        corner_1, corner_2, corner_3, corner_4,
                        tan_odds, ninki, go_4f_time, go_3f_time,
                        data_kubun
                    ) VALUES %s
                """, entry_records, page_size=1000)
            entries_upserted = len(entry_records)
            ctx.append_log(f"  race_entries 再投入: {entries_upserted} 行")
        v2_conn.commit()

        ctx.report_progress(100)
        ctx.append_log(
            f"[sync_races_from_jvdl] 完了: races={races_upserted} / entries={entries_upserted}"
        )

    finally:
        jvdl_conn.close()
        v2_conn.close()


@register("sync_jvdata")
def _handle_sync_jvdata(params: dict, ctx: JobContext) -> None:
    """JV-Link から差分取得し、DB 投入 → ストア更新 → 予測再計算を実行する。

    params:
        dataspecs:    list[str] | None — 省略時は ["RACE", "DIFF", "SLOP", "WOOD"]
        from_time:    str | None — "YYYYMMDDHHmmss"。省略時は sync_watermark から取得
        run_stores:   bool — デフォルト True
        run_recompute: bool — デフォルト False
        full_setup:   bool — True なら JVOpen option=4 で全量再取得
    """
    dataspecs: list[str] | None = params.get("dataspecs")
    from_time: str | None = params.get("from_time")
    run_stores: bool = bool(params.get("run_stores", True))
    run_recompute: bool = bool(params.get("run_recompute", False))
    full_setup: bool = bool(params.get("full_setup", False))
    weekly: bool = bool(params.get("weekly", False))

    ctx.append_log(
        f"[sync_jvdata] dataspecs={dataspecs or 'default'} "
        f"from_time={from_time!r} stores={run_stores} recompute={run_recompute} "
        f"full_setup={full_setup} weekly={weekly}"
    )
    ctx.report_progress(5)

    try:
        from jvdl_client.jvlink import ComImportError
        from jvdl_client.sync_jvdata import sync_from_jvlink
    except ComImportError as e:
        msg = f"JV-LinkはWindows環境でのみ実行可能: {e}"
        logger.error("[sync_jvdata] %s", msg)
        ctx.append_log(msg)
        raise RuntimeError(msg) from e

    results = sync_from_jvlink(
        dataspecs=dataspecs,
        from_time=from_time,
        run_ingest=True,
        run_stores=run_stores,
        run_recompute=run_recompute,
        full_setup=full_setup,
        weekly=weekly,
    )

    ctx.report_progress(90)

    ok_count  = sum(1 for v in results.values() if v == "ok")
    err_count = sum(1 for v in results.values() if v.startswith("ERROR"))
    summary   = f"完了: {ok_count}/{len(results)} 成功 / {err_count} 失敗"
    ctx.append_log(f"[sync_jvdata] {summary}")
    for ds, status in results.items():
        ctx.append_log(f"  {ds}: {status}")

    ctx.report_progress(100)
    if err_count > 0:
        failed = [ds for ds, v in results.items() if v.startswith("ERROR")]
        raise RuntimeError(f"一部 dataspec 失敗: {failed}")


@register("recompute_predictions")
def _handle_recompute_predictions(params: dict, ctx: JobContext) -> None:
    """今週末（または指定 race_ids）の予測キャッシュを再計算する。

    params:
        race_ids:  list[str] | None — 省略時は今週末の全レース
        mode:      "weekend" | "today" | "ids" — デフォルト "weekend"
    """
    mode = params.get("mode", "weekend")
    race_ids: list[str] | None = params.get("race_ids")

    ctx.append_log(f"[recompute_predictions] mode={mode} started")
    ctx.report_progress(5)

    # 意図的なレイヤー違反の例外（AD-1 で判断・記録）:
    # _run_batch 自体が api_v2.routers.prediction / api_v2.routers.races の
    # 計算ロジック（_compute_prediction, _compute_detail 等）に依存しているため、
    # shared/ に切り出しても api_v2 依存が shared 側に移るだけで解消しない。
    # 予測計算ロジックを api_v2 から動かす設計変更が必要になるため、ここでは
    # 遅延 import のまま残す。
    from api_v2.services.batch_predictor import (
        _run_batch,
        get_weekend_race_ids,
        get_today_race_ids,
    )

    if mode == "ids" and race_ids:
        ids = race_ids
    elif mode == "today":
        ids = get_today_race_ids()
    else:
        ids = get_weekend_race_ids()

    ctx.append_log(f"対象 race_ids: {len(ids)} 件")
    ctx.report_progress(10)

    if not ids:
        ctx.append_log("対象レースなし — 完了")
        return

    saved, failed_cnt, skipped = _run_batch(ids, batch_label=f"worker_{mode}")
    ctx.report_progress(100)

    # Redis 無効化
    redis_deleted = 0
    try:
        from shared.cache import RACE_DETAIL_CACHE_PFX, get_redis_client
        r = get_redis_client()
        if r:
            keys = [f"{RACE_DETAIL_CACHE_PFX}{rid}" for rid in ids]
            redis_deleted = r.delete(*keys)
    except Exception as e:
        ctx.append_log(f"Redis 無効化スキップ: {e}")

    ctx.append_log(
        f"完了: 計算対象={len(ids)} / 保存={saved} / スキップ(データなし)={skipped}"
        f" / 失敗={failed_cnt} / Redis削除={redis_deleted}"
    )


@register("run_tipster_evaluation")
def _handle_run_tipster_evaluation(params: dict, ctx: JobContext) -> None:
    """予想家(Tipster)戦略を評価し、HTMLレポートを生成する。

    params:
        race_ids:      list[str] | None — 省略時は今週末の全レース
        strategy:      str — 戦略名 (tipster/strategies/*.json、デフォルト "honmei_v1")
        output_format: "html" — 現状 html のみ対応
    """
    from datetime import date as _date

    from tipster.engine import evaluate_race
    from tipster.renderer import render_race_html, render_weekend_html

    strategy: str = params.get("strategy", "honmei_v1")
    race_ids: list[str] | None = params.get("race_ids")

    ctx.append_log(f"[run_tipster_evaluation] strategy={strategy} 開始")
    ctx.report_progress(5)

    if not race_ids:
        from api_v2.services.batch_predictor import get_weekend_race_ids
        race_ids = get_weekend_race_ids()

    ctx.append_log(f"対象 race_ids: {len(race_ids)} 件")
    if not race_ids:
        ctx.append_log("対象レースなし — 完了")
        return

    strategy_dir = Path("data/output/tipster") / strategy
    strategy_dir.mkdir(parents=True, exist_ok=True)

    evaluations = []
    failed = 0
    for i, rid in enumerate(race_ids):
        try:
            ev = evaluate_race(rid, strategy)
            render_race_html(ev, strategy_dir / f"{rid}.html")
            evaluations.append(ev)
        except Exception as e:
            failed += 1
            ctx.append_log(f"  race_id={rid} 失敗: {e}")
        ctx.report_progress(5 + int(90 * (i + 1) / len(race_ids)))

    summary_path = Path("data/output/tipster") / f"{strategy}_{_date.today().isoformat()}.html"
    render_weekend_html(evaluations, summary_path, link_prefix=f"{strategy}/")
    ctx.set_artifact(str(summary_path))
    ctx.report_progress(100)
    ctx.append_log(
        f"[run_tipster_evaluation] 完了: 成功={len(evaluations)} / 失敗={failed} / 出力={summary_path}"
    )


@register("update_tipster_results")
def _handle_update_tipster_results(params: dict, ctx: JobContext) -> None:
    """直近の確定レースに tipster 戦略を適用し、実績を tipster_results テーブルに UPSERT する。

    params:
        from_date:   str | None — "YYYY-MM-DD"。省略時は 14日前
        to_date:     str | None — "YYYY-MM-DD"。省略時は昨日
        strategy:    str | None — 戦略名（デフォルト "honmei_v6"）
    """
    import datetime as _dt
    import json as _json

    import psycopg2 as _pg2
    import psycopg2.extras as _extras
    from sqlalchemy import text as _text

    from tipster.engine import evaluate_race_context, fetch_race_context, load_strategy
    from shared.config import DB_V2

    strategy_name: str = params.get("strategy", "honmei_v6")
    anaba_name:    str = params.get("anaba_strategy", "anaba_v5")

    to_dt = _dt.date.fromisoformat(params["to_date"]) if params.get("to_date") else \
            _dt.date.today() - _dt.timedelta(days=1)
    from_dt = _dt.date.fromisoformat(params["from_date"]) if params.get("from_date") else \
              to_dt - _dt.timedelta(days=14)

    ctx.append_log(f"[update_tipster_results] strategy={strategy_name} {from_dt}〜{to_dt}")
    ctx.report_progress(5)

    # 確定済みレース（kakutei_chakujun が存在する）を取得
    # 注意: ml.db.engine (fukurou_jvdl) の races/race_entries は「旧・未使用」の
    # レガシーテーブルで、bulk_ingest_v2 が書き込まなくなって以降更新が止まっている
    # （2026-07 時点で 2026-06-14 で停止）。実際に予測パイプラインが使う
    # fukurou_keiba_v2.races / race_entries を参照する（DB_OPERATIONS_GUIDE.md 参照）。
    from sqlalchemy import create_engine as _create_engine

    _v2_cfg = DB_V2
    _v2_engine = _create_engine(
        f"postgresql+psycopg2://{_v2_cfg['user']}:{_v2_cfg['password']}"
        f"@{_v2_cfg['host']}:{_v2_cfg['port']}/{_v2_cfg['dbname']}"
    )

    with _v2_engine.connect() as _conn:
        rows = _conn.execute(_text("""
            SELECT DISTINCT r.id AS race_id, r.race_date AS race_date
            FROM   races r
            JOIN   race_entries e ON e.race_id = r.id
            WHERE  r.race_date BETWEEN :start AND :end
              AND  e.kakutei_chakujun IS NOT NULL AND e.kakutei_chakujun > 0
              AND  r.keibajo_code <= '10'
              AND  r.race_syubetsu_code IN ('11', '12')
            ORDER  BY r.race_date, r.id
        """), {"start": from_dt, "end": to_dt}).fetchall()

    race_ids = [(r[0], r[1]) for r in rows]
    ctx.append_log(f"  対象レース: {len(race_ids)} 件")

    _STRATEGIES_DIR = Path(__file__).parent.parent.parent / "tipster/strategies"

    try:
        honmei_strat = load_strategy(_STRATEGIES_DIR / f"{strategy_name}.json")
        anaba_strat  = load_strategy(_STRATEGIES_DIR / f"{anaba_name}.json")
    except Exception as e:
        raise RuntimeError(f"戦略ロード失敗: {e}") from e

    rank_labels = ["一押し", "二押し", "三押し"]
    upsert_rows: list[tuple] = []
    failed = 0

    for i, (race_id, race_date) in enumerate(race_ids):
        try:
            race_ctx    = fetch_race_context(race_id)
            honmei_eval = evaluate_race_context(race_ctx, honmei_strat)
            anaba_eval  = evaluate_race_context(race_ctx, anaba_strat)
        except Exception as e:
            ctx.append_log(f"  skip race_id={race_id}: {e}")
            failed += 1
            ctx.report_progress(5 + int(85 * (i + 1) / max(len(race_ids), 1)))
            continue

        honmei_ids: set[str] = set()
        picks: list[tuple[str, str]] = []  # (horse_id, rank_label)

        for j, cand in enumerate(honmei_eval.candidates[:3]):
            picks.append((cand.horse_id, rank_labels[j]))
            honmei_ids.add(cand.horse_id)

        for cand in anaba_eval.candidates:
            if cand.horse_id not in honmei_ids:
                picks.append((cand.horse_id, "穴推奨"))
                break

        # 実際の着順・オッズを取得（fukurou_keiba_v2.race_entries を参照）
        with _v2_engine.connect() as _conn:
            result_rows = _conn.execute(_text("""
                SELECT horse_id, kakutei_chakujun, tan_odds
                FROM   race_entries
                WHERE  race_id = :rid
            """), {"rid": race_id}).fetchall()
        result_map = {r[0]: (r[1], r[2]) for r in result_rows}

        for horse_id, label in picks:
            final_rank, tan_odds = result_map.get(horse_id, (None, None))
            is_win    = (final_rank == 1)    if final_rank is not None else None
            is_placed = (final_rank <= 3)    if final_rank is not None else None
            upsert_rows.append((
                race_id, horse_id, race_date, strategy_name, label,
                is_placed, is_win, final_rank, tan_odds,
            ))

        ctx.report_progress(5 + int(85 * (i + 1) / max(len(race_ids), 1)))

    # UPSERT
    if upsert_rows:
        db_conn = _pg2.connect(**DB_V2)
        try:
            _extras.execute_values(db_conn.cursor(), """
                INSERT INTO tipster_results
                    (race_id, horse_id, race_date, strategy, rank_label,
                     is_placed, is_win, final_rank, tan_odds)
                VALUES %s
                ON CONFLICT (race_id, horse_id, strategy) DO UPDATE SET
                    rank_label  = EXCLUDED.rank_label,
                    is_placed   = EXCLUDED.is_placed,
                    is_win      = EXCLUDED.is_win,
                    final_rank  = EXCLUDED.final_rank,
                    tan_odds    = EXCLUDED.tan_odds,
                    recorded_at = now()
            """, upsert_rows, page_size=200)
            db_conn.commit()
        finally:
            db_conn.close()

    ctx.report_progress(100)
    ctx.append_log(
        f"[update_tipster_results] 完了: UPSERT={len(upsert_rows)} 行 / 失敗レース={failed}"
    )


@register("update_ai_tipster_results")
def _handle_update_ai_tipster_results(params: dict, ctx: JobContext) -> None:
    """直近の確定レースにAI推奨(v1×opponent_v3アンサンブル)を適用し、
    実績を tipster_results テーブルに UPSERT する。

    honmei/anaba（JSON戦略ベース）と同じ tipster_results テーブルを共有し、
    strategy='ai_v1_opp' で区別する（既存の /cumulative-stats 等の集計
    エンドポイントをそのまま流用できるため）。計算パイプラインが完全に別
    （LightGBMアンサンブル）のため update_tipster_results とは別ハンドラにしている。

    params:
        from_date: str | None — "YYYY-MM-DD"。省略時は14日前
        to_date:   str | None — "YYYY-MM-DD"。省略時は昨日
    """
    import datetime as _dt

    import lightgbm as _lgb
    import psycopg2 as _pg2
    import psycopg2.extras as _extras
    from sqlalchemy import create_engine as _create_engine
    from sqlalchemy import text as _text

    from pace_bias_ai.opponent_model.features import load_all_race_history
    from scripts.generate_ai_picks import (
        _OPP_MODEL,
        _V1_MODEL,
        _get_race_entries,
        _get_race_meta_by_id,
        _jvdl_engine,
        compute_unified_rank,
        score_race_ai,
    )
    from shared.config import DB_V2

    to_dt = _dt.date.fromisoformat(params["to_date"]) if params.get("to_date") else \
            _dt.date.today() - _dt.timedelta(days=1)
    from_dt = _dt.date.fromisoformat(params["from_date"]) if params.get("from_date") else \
              to_dt - _dt.timedelta(days=14)

    ctx.append_log(f"[update_ai_tipster_results] {from_dt}〜{to_dt}")
    ctx.report_progress(5)

    _v2_cfg = DB_V2
    _v2_engine = _create_engine(
        f"postgresql+psycopg2://{_v2_cfg['user']}:{_v2_cfg['password']}"
        f"@{_v2_cfg['host']}:{_v2_cfg['port']}/{_v2_cfg['dbname']}"
    )

    with _v2_engine.connect() as _conn:
        rows = _conn.execute(_text("""
            SELECT DISTINCT r.id AS race_id
            FROM   races r
            JOIN   race_entries e ON e.race_id = r.id
            WHERE  r.race_date BETWEEN :start AND :end
              AND  e.kakutei_chakujun IS NOT NULL AND e.kakutei_chakujun > 0
              AND  r.keibajo_code <= '10'
              AND  r.race_syubetsu_code IN ('11', '12')
            ORDER  BY r.id
        """), {"start": from_dt, "end": to_dt}).fetchall()

    race_ids = [r[0] for r in rows]
    ctx.append_log(f"  対象レース: {len(race_ids)} 件")
    ctx.report_progress(10)

    ctx.append_log("  モデルロード: v1, opponent_v3")
    model_v1  = _lgb.Booster(model_file=str(_V1_MODEL))
    model_opp = _lgb.Booster(model_file=str(_OPP_MODEL))
    engine_jvdl = _jvdl_engine()
    df_ent_hist, df_races_hist = load_all_race_history(engine_jvdl)
    ctx.report_progress(15)

    upsert_rows: list[tuple] = []
    failed = 0

    for i, race_id in enumerate(race_ids):
        race_meta = _get_race_meta_by_id(race_id)
        entries = _get_race_entries(race_id) if race_meta else []

        result = None
        if race_meta is not None:
            try:
                result = score_race_ai(
                    race_meta, entries, model_v1, model_opp,
                    engine_jvdl, df_ent_hist, df_races_hist,
                )
            except Exception as e:
                ctx.append_log(f"  skip race_id={race_id}: {e}")
                failed += 1

        if result is not None:
            with _v2_engine.connect() as _conn:
                result_rows = _conn.execute(_text("""
                    SELECT horse_id, kakutei_chakujun, tan_odds
                    FROM   race_entries
                    WHERE  race_id = :rid
                """), {"rid": race_id}).fetchall()
            result_map = {r[0]: (r[1], r[2]) for r in result_rows}

            race_date = _dt.date.fromisoformat(result["race_date"])
            for pick in result["picks"]:
                label = compute_unified_rank(pick["rank"], pick["confidence_label"])
                if label is None:
                    continue
                horse_id = pick["horse_id"]
                final_rank, tan_odds = result_map.get(horse_id, (None, None))
                is_win    = (final_rank == 1) if final_rank is not None else None
                is_placed = (final_rank <= 3) if final_rank is not None else None
                upsert_rows.append((
                    race_id, horse_id, race_date, "ai_v1_opp", label,
                    is_placed, is_win, final_rank, tan_odds,
                ))

        ctx.report_progress(15 + int(80 * (i + 1) / max(len(race_ids), 1)))

    if upsert_rows:
        db_conn = _pg2.connect(**DB_V2)
        try:
            _extras.execute_values(db_conn.cursor(), """
                INSERT INTO tipster_results
                    (race_id, horse_id, race_date, strategy, rank_label,
                     is_placed, is_win, final_rank, tan_odds)
                VALUES %s
                ON CONFLICT (race_id, horse_id, strategy) DO UPDATE SET
                    rank_label  = EXCLUDED.rank_label,
                    is_placed   = EXCLUDED.is_placed,
                    is_win      = EXCLUDED.is_win,
                    final_rank  = EXCLUDED.final_rank,
                    tan_odds    = EXCLUDED.tan_odds,
                    recorded_at = now()
            """, upsert_rows, page_size=200)
            db_conn.commit()
        finally:
            db_conn.close()

    ctx.report_progress(100)
    ctx.append_log(
        f"[update_ai_tipster_results] 完了: UPSERT={len(upsert_rows)} 行 / 失敗レース={failed}"
    )


@register("run_tipster_backtest")
def _handle_run_tipster_backtest(params: dict, ctx: JobContext) -> None:
    """予想家(Tipster)戦略を過去レースに遡って適用し、回収率等を集計する。

    params:
        strategy:         str — 戦略名 (デフォルト "honmei_v1")
        reference_date:   str — 基準日 ("today" または "YYYY-MM-DD"、デフォルト "today")
        periods:          list[str] — 集計期間 (デフォルト ["3m", "6m", "1y"])
        grade_filter:      list[str] | None — グレードコード絞り込み (例: ["A","B","C"])
        distance_filter:   list[str] | None — 距離区分絞り込み (例: ["sprint","mile"])

    注意: race_detail_cache に依存しないDB直接クエリの軽量版を使うため _compute_detail は呼ばない。
    1年規模の集計 + 条件別有効性分析は数分〜十数分かかる想定。
    """
    from tipster.backtest import run_backtest
    from tipster.backtest_renderer import render_backtest_html

    strategy: str = params.get("strategy", "honmei_v1")
    reference_date: str = params.get("reference_date", "today")
    periods: list[str] = params.get("periods") or ["3m", "6m", "1y"]
    grade_filter = params.get("grade_filter")
    distance_filter = params.get("distance_filter")

    ctx.append_log(f"[run_tipster_backtest] strategy={strategy} reference_date={reference_date} periods={periods} 開始")
    ctx.report_progress(5)

    results = run_backtest(
        strategy, reference_date=reference_date, periods=periods,
        grade_filter=grade_filter, distance_filter=distance_filter,
    )
    ctx.report_progress(80)

    for p, r in results.items():
        hr = r.honmei_results
        ctx.append_log(
            f"  [{p}] {r.from_date}~{r.to_date}: 対象{r.total_races}レース(スキップ{r.skipped_races}) "
            f"勝率={hr.win_rate:.1%} 複勝率={hr.place_rate:.1%} "
            f"単勝回収率={hr.tan_return_rate:.1%} 複勝回収率={hr.fuku_return_rate:.1%}"
        )

    ref_str = reference_date if reference_date != "today" else __import__("datetime").date.today().isoformat()
    output_path = Path("data/output/tipster") / f"backtest_{strategy}_{ref_str}.html"
    render_backtest_html(results, output_path)
    ctx.set_artifact(str(output_path))
    ctx.report_progress(100)
    ctx.append_log(f"[run_tipster_backtest] 完了: 出力={output_path}")


# ── ワーカーループ ─────────────────────────────────────────────────────────────

_SQL_DEQUEUE = """
SELECT id, job_type, params
FROM   jobs
WHERE  status = 'queued'
ORDER  BY created_at
LIMIT  1
FOR UPDATE SKIP LOCKED
"""

_SQL_MARK_RUNNING = """
UPDATE jobs SET status = 'running', started_at = now(), progress = 0
WHERE id = %s
"""

_SQL_MARK_DONE = """
UPDATE jobs
SET    status = 'done', progress = 100,
       finished_at = now(),
       artifact_path = %s
WHERE  id = %s
"""

_SQL_MARK_FAILED = """
UPDATE jobs
SET    status = 'failed', finished_at = now(), log_tail = %s
WHERE  id = %s
"""


def _process_one(conn: psycopg2.extensions.connection) -> str | None:
    """キューから 1 件取り出して実行する。実行した場合はその job_type を、
    キューが空だった場合は None を返す（呼び出し側での集計・アイドル判定用）。"""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(_SQL_DEQUEUE)
        row = cur.fetchone()
        if row is None:
            conn.rollback()
            return None

        job_id = row["id"]
        job_type = row["job_type"]
        params = row["params"] or {}

        cur.execute(_SQL_MARK_RUNNING, (job_id,))
        conn.commit()

    logger.info("JOB START: id=%d type=%s params=%s", job_id, job_type, params)

    ctx = JobContext(job_id=job_id, _conn=conn)

    handler = _HANDLERS.get(job_type)
    if handler is None:
        msg = f"未知の job_type: {job_type!r}"
        logger.error(msg)
        with conn.cursor() as cur:
            cur.execute(_SQL_MARK_FAILED, (msg, job_id))
        conn.commit()
        return job_type

    try:
        handler(params, ctx)
        with conn.cursor() as cur:
            cur.execute(_SQL_MARK_DONE, (ctx._artifact_path, job_id))
        conn.commit()
        logger.info("JOB DONE:  id=%d type=%s", job_id, job_type)
    except Exception:
        tb = traceback.format_exc()
        logger.exception("JOB FAIL:  id=%d type=%s", job_id, job_type)
        ctx._log_buf.append(tb[-1000:])
        tail = "\n".join(ctx._log_buf)
        with conn.cursor() as cur:
            cur.execute(_SQL_MARK_FAILED, (tail, job_id))
        conn.commit()

    return job_type


_SQL_RESET_ORPHANS = """
UPDATE jobs
SET    status = 'failed',
       finished_at = now(),
       log_tail = COALESCE(log_tail || E'\n', '') || '[worker-restart] 起動時に孤児 running ジョブをリセット'
WHERE  status = 'running'
RETURNING id
"""


def _reset_orphan_jobs(conn: psycopg2.extensions.connection) -> None:
    """前回クラッシュで running のまま残ったジョブを failed に遷移させる。"""
    with conn.cursor() as cur:
        cur.execute(_SQL_RESET_ORPHANS)
        orphans = [row[0] for row in cur.fetchall()]
    conn.commit()
    if orphans:
        logger.warning("孤児 running ジョブをリセット: ids=%s", orphans)


def _enqueue_job(job_type: str, params: dict) -> None:
    """APScheduler スレッドからジョブキューにジョブを投入する。"""
    try:
        conn = psycopg2.connect(**DB_JVDL)
        with conn.cursor() as cur:
            import json as _json
            cur.execute(
                "INSERT INTO jobs (job_type, params, status) VALUES (%s, %s, 'queued')",
                (job_type, _json.dumps(params)),
            )
        conn.commit()
        conn.close()
        logger.info("[Scheduler] job enqueued: type=%s params=%s", job_type, params)
    except Exception:
        logger.exception("[Scheduler] ジョブ投入失敗: type=%s", job_type)


def _make_enqueue_fn(job_type: str, params: dict):
    """クロージャでジョブ種別・パラメータを束縛した投入関数を返す。"""
    def _fn() -> None:
        _enqueue_job(job_type, params)
    _fn.__name__ = f"enqueue_{job_type}"
    return _fn


def _scheduled_health_check() -> None:
    """APScheduler から毎日 09:00 JST に呼ばれるヘルスチェック。"""
    try:
        from scripts.health_check import (
            _has_problem,
            format_report_text,
            run_health_check,
        )
        from shared.notification.discord import send_embed

        report = run_health_check()
        text = format_report_text(report)
        logger.info("[HealthCheck/scheduled]\n%s", text)

        if _has_problem(report):
            send_embed(
                title="⚠️ ヘルスチェック異常",
                description=text[:4000],
                color=0xFF0000,
            )
            logger.info("[HealthCheck/scheduled] Discord 通知送信完了（問題あり）")
        else:
            # 毎日 OK 通知を送りたい場合は下行を有効化
            # send_embed(title="✅ ヘルスチェック正常", description=text[:4000], color=0x00FF00)
            pass
    except Exception:
        logger.exception("[HealthCheck/scheduled] 実行エラー（続行）")


def run_worker() -> None:
    """ワーカーのメインループ。

    常駐させない運用方針のため、起動したら:
      1. キューに溜まっている queued ジョブを全て順に処理する（ドレイン）
      2. 新規ジョブが来ないまま WORKER_IDLE_EXIT_SECONDS 秒経過したら自動終了する
         （0 を指定すると旧来通り常駐し続ける）
    Ctrl+C でもいつでも停止できる。
    """
    idle_exit_seconds = int(os.getenv(_IDLE_EXIT_ENV, str(_DEFAULT_IDLE_EXIT_SECONDS)))

    # ワーカー起動唯一性保証（advisory lock）
    lock_conn = psycopg2.connect(**DB_JVDL)
    lock_conn.autocommit = True
    with lock_conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (_ADVISORY_LOCK_KEY,))
        if not cur.fetchone()[0]:
            logger.error("別のワーカーが既に起動中です（advisory lock 取得失敗）。終了します。")
            lock_conn.close()
            sys.exit(1)

    logger.info(
        "ワーカー起動: POLL_INTERVAL=%ds  自動終了=%s",
        POLL_INTERVAL,
        f"新規ジョブなし{idle_exit_seconds}秒で終了" if idle_exit_seconds > 0 else "無効（常駐し続けます）",
    )

    # 未適用 DDL チェック（警告のみ、起動は続行）
    try:
        from scripts.check_migrations import check_migrations
        check_migrations(warn_only=True)
    except Exception as _e:
        logger.warning("check_migrations 失敗（続行）: %s", _e)

    import pytz
    _JST = pytz.timezone("Asia/Tokyo")
    scheduler = BackgroundScheduler(timezone=_JST)
    for sched in _SCHEDULES:
        dow = sched.get("day_of_week", "*")
        h, m = sched["hour"], sched["minute"]
        if sched["kind"] == "direct":
            scheduler.add_job(
                _scheduled_health_check, "cron",
                day_of_week=dow, hour=h, minute=m,
            )
            logger.info("APScheduler 登録: health_check  dow=%s %d:%02d JST", dow, h, m)
        else:
            fn = _make_enqueue_fn(sched["job_type"], sched["params"])
            scheduler.add_job(fn, "cron", day_of_week=dow, hour=h, minute=m)
            logger.info(
                "APScheduler 登録: enqueue %s %s  dow=%s %d:%02d JST",
                sched["job_type"], sched["params"], dow, h, m,
            )
    if idle_exit_seconds > 0:
        logger.warning(
            "常駐しない運用のため、上記の自動スケジュールはワーカー起動中の"
            "タイミングとしか一致しません。確実に実行したい処理は"
            "手動でジョブを投入してください。"
        )
    scheduler.start()

    work_conn = psycopg2.connect(**DB_JVDL)
    _reset_orphan_jobs(work_conn)

    processed_count = 0
    processed_types: dict[str, int] = {}
    last_activity = time.monotonic()

    logger.info("キューのドレインを開始します（溜まっているジョブを処理中）...")

    try:
        while True:
            try:
                processed_type = _process_one(work_conn)
                if processed_type is not None:
                    processed_count += 1
                    processed_types[processed_type] = processed_types.get(processed_type, 0) + 1
                    last_activity = time.monotonic()
                else:
                    idle_for = time.monotonic() - last_activity
                    if idle_exit_seconds > 0 and idle_for >= idle_exit_seconds:
                        logger.info(
                            "新規ジョブなしのまま%d秒経過したため終了します。", idle_exit_seconds
                        )
                        break
                    time.sleep(POLL_INTERVAL)
            except psycopg2.OperationalError:
                logger.warning("DB 接続断。再接続を試みます...")
                try:
                    work_conn.close()
                except Exception:
                    pass
                time.sleep(5)
                work_conn = psycopg2.connect(**DB_JVDL)
            except KeyboardInterrupt:
                logger.info("ワーカー停止（KeyboardInterrupt）")
                break
    finally:
        scheduler.shutdown(wait=False)
        with lock_conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (_ADVISORY_LOCK_KEY,))
        lock_conn.close()
        work_conn.close()
        if processed_count == 0:
            logger.info("処理対象のジョブはありませんでした。")
        else:
            detail = ", ".join(f"{t}×{n}" for t, n in processed_types.items())
            logger.info("完了: %d件のジョブを処理しました（%s）", processed_count, detail)


if __name__ == "__main__":
    run_worker()
