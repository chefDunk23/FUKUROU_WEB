"""
api_v2/services/batch_predictor.py
=====================================
週末レース予測の事前計算バッチサービス。

フロー:
  1. JST 今週末（土日）の race_id 一覧を fukurou_keiba_v2.races から取得
  2. 各 race_id に対して _compute_prediction() + _compute_detail() を呼び出す
  3. 結果を _save_prediction_cache() / _save_detail_cache() 経由で Redis へ書き込む

呼び出し元:
  - api_v2/main.py の APScheduler（金曜 21:00 / 土日 08:30 JST に自動実行）
  - shared/worker/job_runner.py の recompute_predictions ジョブ経由
  - scripts/ から手動実行も可能
"""
from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from zoneinfo import ZoneInfo

import psycopg2

from shared.config import DB_JVDL
from shared.db.jvdata import get_conn as get_v2_conn

logger = logging.getLogger(__name__)

_JST = ZoneInfo("Asia/Tokyo")
_BATCH_LOCK_KEY = 42001  # pg_try_advisory_lock キー（バッチ多重起動防止用）

_SQL_RACE_IDS_BY_DATE = """
SELECT id
FROM   races
WHERE  date >= %s AND date < %s + INTERVAL '1 day'
ORDER  BY id
"""


def _this_weekend() -> tuple[date, date]:
    """JST での今週末（土日）の日付を返す。金〜日は当週、月〜木は翌週。"""
    import datetime as _dt
    today = _dt.datetime.now(_JST).date()
    days_to_sat = (5 - today.weekday()) % 7
    sat = today + timedelta(days=days_to_sat)
    return sat, sat + timedelta(days=1)


def _today_jst() -> date:
    import datetime as _dt
    return _dt.datetime.now(_JST).date()


def _race_ids_for_dates(dates: list[date]) -> list[str]:
    """指定日の race_id 一覧を fukurou_keiba_v2 から返す。"""
    race_ids: list[str] = []
    for d in dates:
        try:
            with get_v2_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(_SQL_RACE_IDS_BY_DATE, (d, d))
                    ids = [row[0] for row in cur.fetchall()]
            race_ids.extend(ids)
            if ids:
                logger.info("[Batch] %s → %d races", d, len(ids))
        except Exception:
            logger.exception("[Batch] race_ids クエリ失敗: date=%s", d)
    return race_ids


def get_weekend_race_ids() -> list[str]:
    """今週末 (土日) の race_id 一覧を返す。"""
    sat, sun = _this_weekend()
    return _race_ids_for_dates([sat, sun])


def get_today_race_ids() -> list[str]:
    """JST 当日の race_id 一覧を返す。"""
    return _race_ids_for_dates([_today_jst()])


def _run_batch(race_ids: list[str], batch_label: str) -> tuple[int, int, int]:
    """race_ids に対して予測 + 詳細キャッシュを計算して保存する。

    戻り値: (saved, failed_count, skipped_count)
      - saved:  race_detail_cache への UPSERT 成功件数
      - failed: 例外で処理失敗した件数
      - skipped: _compute_detail が None を返したスキップ件数（データ未存在）

    J-3: pg_try_advisory_xact_lock で多重起動防止（プール外の専用接続で保持）。
    J-5: レース単位 try/except + 終了時に「成功n / 失敗m / 所要時間」をログ出力。
    """
    from api_v2.routers.prediction import _compute_prediction, _save_prediction_cache
    from api_v2.routers.races import _compute_detail, _save_detail_cache
    from shared.services.model_version import get_model_version

    # J-3: セッションレベル advisory lock（autocommit=True で接続し、トランザクション外で保持）。
    # pg_try_advisory_lock はセッション終了まで保持 → conn.close() で自動解放。
    # finally で pg_advisory_unlock + conn.close() を保証する。
    lock_conn: psycopg2.extensions.connection | None = None
    try:
        lock_conn = psycopg2.connect(**DB_JVDL)
        lock_conn.autocommit = True
        with lock_conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (_BATCH_LOCK_KEY,))
            acquired: bool = cur.fetchone()[0]
        if not acquired:
            logger.info("[Batch/%s] 別ワーカーが実行中のためスキップ", batch_label)
            lock_conn.close()
            return 0, 0, 0
    except Exception:
        logger.exception("[Batch/%s] advisory lock 取得失敗", batch_label)
        if lock_conn is not None:
            lock_conn.close()
        return 0, 0, 0

    t_start = time.perf_counter()
    logger.info("[Batch/%s] 事前計算開始: %d レース", batch_label, len(race_ids))
    model_ver = get_model_version()
    saved = 0
    failed: list[str] = []
    skipped = 0

    try:
        for race_id in race_ids:
            try:
                # 予測スコアキャッシュ（race_predictions）
                pred_resp = _compute_prediction(race_id, include_evidence=False)
                if pred_resp is not None:
                    _save_prediction_cache(pred_resp)

                # レース詳細キャッシュ（race_detail_cache）
                detail_resp = _compute_detail(race_id)
                if detail_resp is None:
                    logger.debug("[Batch/%s] race_id=%s: データ未存在、スキップ", batch_label, race_id)
                    skipped += 1
                    continue
                _save_detail_cache(detail_resp, model_ver)
                saved += 1

            except FileNotFoundError as e:
                logger.error("[Batch/%s] モデル未ロード、バッチ中断: %s", batch_label, e)
                failed.append(race_id)
                break
            except Exception:
                logger.exception("[Batch/%s] race_id=%s: 計算失敗、スキップ", batch_label, race_id)
                failed.append(race_id)

    finally:
        elapsed = time.perf_counter() - t_start
        # J-5: 終了サマリーログ
        logger.info(
            "[Batch/%s] 完了: 保存 %d / スキップ %d / 失敗 %d / 所要時間 %.1fs",
            batch_label, saved, skipped, len(failed), elapsed,
        )
        if failed:
            logger.warning("[Batch/%s] 失敗 race_id 一覧: %s", batch_label, failed)

        # セッションレベル advisory lock を明示解放してから接続を閉じる
        try:
            with lock_conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", (_BATCH_LOCK_KEY,))
        except Exception:
            pass
        lock_conn.close()

    return saved, len(failed), skipped


def precompute_weekend_races() -> int:
    """今週末の全レース予測 + 詳細を計算してキャッシュに保存する。保存件数を返す。"""
    race_ids = get_weekend_race_ids()
    if not race_ids:
        logger.warning("[Batch] 今週末のレースが見つかりません（出馬表未公開の可能性）")
        return 0
    saved, _failed, _skipped = _run_batch(race_ids, "weekend")
    return saved


def precompute_today_races() -> int:
    """当日レースを再計算して race_predictions / race_detail_cache を更新し、Redis を無効化する。

    J-2: 土日 08:30 JST に APScheduler から呼び出される。
    馬体重(SE) / 天候・馬場(RA) が ETL 済みの場合はその最新値が反映される。
    ※ ETL（RA/SE）が未実装のため、現時点ではフォールバック値のまま再計算される。
    """
    from api_v2.routers.races import _get_redis, _CACHE_PFX

    race_ids = get_today_race_ids()
    if not race_ids:
        logger.info("[Batch/today] 当日レースが見つかりません")
        return 0

    saved, _failed, _skipped = _run_batch(race_ids, "today")

    # DB キャッシュ更新後に Redis の古いキャッシュを削除（強制再読み込み）
    r = _get_redis()
    if r:
        keys = [f"{_CACHE_PFX}{rid}" for rid in race_ids]
        try:
            deleted = r.delete(*keys)
            logger.info("[Batch/today] Redis キー削除: %d / %d", deleted, len(keys))
        except Exception as e:
            logger.warning("[Batch/today] Redis キー削除失敗: %s", e)

    return saved
