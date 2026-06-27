"""
api_v2/routers/tipster.py
==========================
予想家(Tipster)系エンドポイント群。

エンドポイント一覧:
  P1-B  GET  /api/v2/tipster/recent-results      直近の予測実績一覧
  P1-B  GET  /api/v2/tipster/cumulative-stats     ランク別累計複勝率
  P1-C  GET  /api/v2/tipster/weekly-overview      今週のレース全体像 + 推奨馬マーク
  P2-A  POST /api/v2/tipster/log                  SNS出力用ログ記録
  P2-A  GET  /api/v2/tipster/log                  SNS出力用ログ一覧（日付指定）

tipster/ 配下のロジックは変更しない。
このルーターは薄いラッパーとして呼び出すだけ。
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extras
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from shared.config import DB_V2
from shared.db.jvdata import get_conn as get_v2_conn

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v2/tipster", tags=["tipster"])

# ── パス定数 ─────────────────────────────────────────────────────────────────

_ROOT = Path(__file__).parent.parent.parent
_STRATEGY_HONMEI = _ROOT / "tipster/strategies/honmei_v6.json"
_STRATEGY_ANABA  = _ROOT / "tipster/strategies/anaba_v5.json"
_SNS_LOG_DIR     = _ROOT / "data/output/tipster/sns_log"

# ── 共通ユーティリティ ────────────────────────────────────────────────────────


def _load_strategies():
    from tipster.engine import load_strategy
    honmei_strat = load_strategy(_STRATEGY_HONMEI)
    anaba_strat  = load_strategy(_STRATEGY_ANABA)
    return honmei_strat, anaba_strat


def _get_picks_for_race(race_id: str) -> list[dict]:
    """race_id の推奨馬リストを返す。エラー時は空リスト。"""
    try:
        from tipster.engine import evaluate_race_context, fetch_race_context
        honmei_strat, anaba_strat = _load_strategies()
        ctx           = fetch_race_context(race_id)
        honmei_eval   = evaluate_race_context(ctx, honmei_strat)
        anaba_eval    = evaluate_race_context(ctx, anaba_strat)
    except Exception as e:
        logger.warning("picks 取得失敗 race_id=%s: %s", race_id, e)
        return []

    picks: list[dict] = []
    rank_labels = ["一押し", "二押し", "三押し"]
    honmei_ids: set[str] = set()

    for i, cand in enumerate(honmei_eval.candidates[:3]):
        label = rank_labels[i]
        honmei_ids.add(cand.horse_id)
        picks.append({
            "horse_id":   cand.horse_id,
            "horse_name": cand.horse_name,
            "rank_label": label,
        })

    for cand in anaba_eval.candidates:
        if cand.horse_id not in honmei_ids:
            picks.append({
                "horse_id":   cand.horse_id,
                "horse_name": cand.horse_name,
                "rank_label": "穴推奨",
            })
            break

    return picks


# ── Pydantic モデル ────────────────────────────────────────────────────────────

class RecentResult(BaseModel):
    race_id:    str
    race_date:  date
    horse_id:   str | None
    horse_name: str | None = None
    rank_label: str
    is_placed:  bool | None
    is_win:     bool | None
    final_rank: int | None
    tan_odds:   float | None
    strategy:   str


class CumulativeStat(BaseModel):
    rank_label:  str
    race_count:  int
    win_count:   int
    place_count: int
    win_rate:    float | None
    place_rate:  float | None
    strategy:    str


class WeeklyRace(BaseModel):
    race_id:       str
    race_date:     date
    race_num:      int | None
    keibajo_name:  str
    distance:      int | None
    surface:       str | None
    race_name:     str | None
    has_picks:     bool
    pick_labels:   list[str]
    volatility:    str    # "荒れそう" / "やや荒れ" / "堅め" / "不明"
    head_count:    int | None


class WeeklyOverviewResponse(BaseModel):
    week_start: date
    week_end:   date
    races:      list[WeeklyRace]


class SnsLogEntry(BaseModel):
    race_id:        str
    race_date:      str
    venue:          str | None = None
    distance:       int | None = None
    surface:        str | None = None
    honmei:         dict | None = None
    taiko:          list[dict] | None = None
    aite:           list[dict] | None = None
    ana:            dict | None = None
    conditions_used: str | None = None


# ── P1-B: 実績一覧 ─────────────────────────────────────────────────────────────

@router.get("/recent-results", response_model=list[RecentResult])
def get_recent_results(
    limit: int  = Query(50, ge=1, le=200),
    strategy: str = Query("honmei_v6"),
):
    """直近の予測実績を返す。tipster_results テーブルから取得。"""
    try:
        conn = psycopg2.connect(**DB_V2)
    except Exception as e:
        raise HTTPException(503, f"DB接続失敗: {e}")

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT tr.race_id, tr.race_date, tr.horse_id, tr.rank_label,
                       tr.is_placed, tr.is_win, tr.final_rank, tr.tan_odds, tr.strategy,
                       re.horse_name
                FROM   tipster_results tr
                LEFT JOIN race_entries re
                       ON re.race_id = tr.race_id AND re.horse_id = tr.horse_id
                WHERE  tr.strategy = %s
                ORDER  BY tr.race_date DESC, tr.race_id, tr.rank_label
                LIMIT  %s
            """, (strategy, limit))
            rows = cur.fetchall()
    except Exception as e:
        conn.close()
        raise HTTPException(500, f"クエリ失敗: {e}")
    finally:
        conn.close()

    return [RecentResult(**dict(r)) for r in rows]


# ── P1-B: 累計統計 ─────────────────────────────────────────────────────────────

@router.get("/cumulative-stats", response_model=list[CumulativeStat])
def get_cumulative_stats(strategy: str = Query("honmei_v6")):
    """ランク別（一押し/二押し/三押し/穴推奨）の累計勝率・複勝率を返す。"""
    try:
        conn = psycopg2.connect(**DB_V2)
    except Exception as e:
        raise HTTPException(503, f"DB接続失敗: {e}")

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT rank_label,
                       COUNT(*)                                           AS race_count,
                       COUNT(*) FILTER (WHERE is_win   = true)           AS win_count,
                       COUNT(*) FILTER (WHERE is_placed = true)          AS place_count,
                       strategy
                FROM   tipster_results
                WHERE  strategy = %s AND is_placed IS NOT NULL
                GROUP  BY rank_label, strategy
                ORDER  BY CASE rank_label
                            WHEN '一押し' THEN 1 WHEN '二押し' THEN 2
                            WHEN '三押し' THEN 3 WHEN '穴推奨' THEN 4
                            ELSE 9 END
            """, (strategy,))
            rows = cur.fetchall()
    except Exception as e:
        conn.close()
        raise HTTPException(500, f"クエリ失敗: {e}")
    finally:
        conn.close()

    stats = []
    for r in rows:
        rc = int(r["race_count"])
        wc = int(r["win_count"])
        pc = int(r["place_count"])
        stats.append(CumulativeStat(
            rank_label  = r["rank_label"],
            race_count  = rc,
            win_count   = wc,
            place_count = pc,
            win_rate    = round(wc / rc, 4) if rc > 0 else None,
            place_rate  = round(pc / rc, 4) if rc > 0 else None,
            strategy    = r["strategy"],
        ))
    return stats


# ── P1-C: 週次レース全体像 ────────────────────────────────────────────────────

_KEIBAJO_NAME: dict[str, str] = {
    "01": "札幌", "02": "函館", "03": "福島", "04": "新潟",
    "05": "東京", "06": "中山", "07": "中京", "08": "京都",
    "09": "阪神", "10": "小倉",
}


def _volatility(head_count: int | None, has_picks: bool) -> str:
    """簡易ルールで荒れやすさを判定。"""
    if head_count is None:
        return "不明"
    if head_count >= 16:
        return "荒れそう"
    if head_count >= 12:
        return "やや荒れ"
    if has_picks:
        return "やや荒れ"
    return "堅め"


@router.get("/weekly-overview", response_model=WeeklyOverviewResponse)
def get_weekly_overview(target_date: str | None = Query(None)):
    """今週（月〜日）の全レース + 推奨馬マーク + 荒れ指数を返す。"""
    if target_date:
        try:
            base = date.fromisoformat(target_date)
        except ValueError:
            raise HTTPException(400, "target_date は YYYY-MM-DD 形式で指定してください")
    else:
        base = date.today()

    # 今週の月〜日曜
    week_start = base - timedelta(days=base.weekday())
    week_end   = week_start + timedelta(days=6)

    try:
        conn = psycopg2.connect(**DB_V2)
    except Exception as e:
        raise HTTPException(503, f"DB接続失敗: {e}")

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT r.id AS race_id, r.date AS race_date, r.race_num,
                       r.place_code, r.distance, r.course_type AS surface,
                       COALESCE(r.name, r.name_short_10) AS race_name,
                       r.head_count
                FROM   races r
                WHERE  r.date BETWEEN %s AND %s
                  AND  r.place_code <= '10'
                ORDER  BY r.date, r.place_code, r.race_num
            """, (week_start, week_end))
            race_rows = cur.fetchall()
    except Exception as e:
        conn.close()
        raise HTTPException(500, f"クエリ失敗: {e}")
    finally:
        conn.close()

    # 各レースの picks をまとめてチェック（tipster_results から引く。未確定レースは engine 呼び出し）
    race_ids = [r["race_id"] for r in race_rows]

    # tipster_results から既存のピックを一括取得
    picks_by_race: dict[str, list[str]] = {}
    if race_ids:
        try:
            conn2 = psycopg2.connect(**DB_V2)
            with conn2.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT race_id, rank_label FROM tipster_results WHERE race_id = ANY(%s) ORDER BY rank_label",
                    (race_ids,)
                )
                for row in cur.fetchall():
                    picks_by_race.setdefault(row["race_id"], []).append(row["rank_label"])
            conn2.close()
        except Exception:
            pass

    weekly_races: list[WeeklyRace] = []
    for r in race_rows:
        rid = r["race_id"]
        labels = picks_by_race.get(rid, [])
        hc = r.get("head_count")
        has_p = len(labels) > 0
        keibajo = _KEIBAJO_NAME.get((r.get("place_code") or "").strip().zfill(2), r.get("place_code") or "")
        weekly_races.append(WeeklyRace(
            race_id      = rid,
            race_date    = r["race_date"],
            race_num     = r.get("race_num"),
            keibajo_name = keibajo,
            distance     = r.get("distance"),
            surface      = r.get("surface"),
            race_name    = r.get("race_name"),
            has_picks    = has_p,
            pick_labels  = labels,
            volatility   = _volatility(hc, has_p),
            head_count   = hc,
        ))

    return WeeklyOverviewResponse(
        week_start = week_start,
        week_end   = week_end,
        races      = weekly_races,
    )


# ── P2-A: SNS ログ ─────────────────────────────────────────────────────────────

@router.post("/log", status_code=201)
def post_sns_log(entry: SnsLogEntry):
    """SNS出力用のレース予想記録を JSON ファイルに書き出す。"""
    _SNS_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_date = entry.race_date[:10] if entry.race_date else date.today().isoformat()
    log_file = _SNS_LOG_DIR / f"{log_date}.json"

    existing: list[dict] = []
    if log_file.exists():
        try:
            existing = json.loads(log_file.read_text(encoding="utf-8"))
        except Exception:
            existing = []

    new_entry = entry.model_dump()
    new_entry["logged_at"] = datetime.now().isoformat()

    # 同一 race_id があれば上書き、なければ追記
    updated = [e for e in existing if e.get("race_id") != entry.race_id]
    updated.append(new_entry)

    log_file.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "ok", "file": str(log_file.relative_to(_ROOT))}


@router.get("/log")
def get_sns_log(log_date: str | None = Query(None)):
    """SNS出力用ログを返す（デフォルト: 今日分）。"""
    target = log_date or date.today().isoformat()
    try:
        target_date = date.fromisoformat(target[:10])
    except ValueError:
        raise HTTPException(400, "log_date は YYYY-MM-DD 形式で指定してください")

    log_file = _SNS_LOG_DIR / f"{target_date.isoformat()}.json"
    if not log_file.exists():
        return {"date": target_date.isoformat(), "entries": []}

    try:
        entries = json.loads(log_file.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(500, f"ログ読み込み失敗: {e}")

    return {"date": target_date.isoformat(), "entries": entries}
