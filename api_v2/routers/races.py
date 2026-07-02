"""
api_v2/routers/races.py
========================
GET /api/v2/races?date=YYYY-MM-DD — 指定日のレース一覧を返す。

fukurou_keiba_v2 にデータがない日（今週末の未来レース等）は
fukurou_jvdl にフォールバックして同等のレスポンスを返す。

クラスラベル計算ロジック（4段フォールバック）:
  Tier 1: grade_code が信頼できる（keiba_v2 実測値: A/B/C/L/E/H 等）→ 直接変換
  Tier 2: race_name が _RACE_GRADE_MAP に一致 → G1/G2/G3
  Tier 3: race_name の正規表現 → 新馬/未勝利/○勝クラス/オープン/障害
  Tier 4: grade_code == 'R'（jvdl 重賞タグ、格付け不明）→ "重賞"

  ※ jvdl の jyoken_cd_* / race_type_code はパーサー破損により信頼できないため使用しない。
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta

import psycopg2.extras
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from shared.db.jvdl import get_conn as get_jvdl_conn
from shared.db.jvdata import get_conn as get_v2_conn
from ._race_common import (
    _CLASS_REGEX,
    _GRADE_CLASS_SCORE,
    _GRADE_TO_LABEL,
    _JYOKEN_TO_CLASS,
    _KEIBAJO_NAME,
    _RACE_GRADE_MAP,
    _sf,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v2", tags=["v2-races"])

# ── ヘルパー関数 ──────────────────────────────────────────────────────────────

def _fmt_time(raw: str | None) -> str | None:
    """'HHMM' 形式の文字列を 'HH:MM' にフォーマットする。"""
    if not raw:
        return None
    s = str(raw).strip().zfill(4)
    if len(s) >= 4 and s not in ("0000", "    "):
        return f"{s[:2]}:{s[2:4]}"
    return None


_RELIABLE_GRADE_CODES: frozenset[str] = frozenset({
    "A", "B", "C", "L", "G", "F", "D", "A01", "A02", "A03", "A04",
})


def _compute_class_label(
    grade_code: str | None,
    race_type_code: str | None,
    jyoken_cd_2: str | None,
    jyoken_cd_3: str | None,
    jyoken_cd_4: str | None,
    jyoken_cd_5: str | None,
    race_name: str,
) -> str | None:
    """
    レースクラスラベルを 6 段フォールバックで計算する。

    Tier 1: grade_code が G1/G2/G3/Listed を明示するコード → _GRADE_TO_LABEL
    Tier 2: jyoken_cd_2-5 のいずれかが有効 → _JYOKEN_TO_CLASS
            (jvdl パーサーのバイト位置修正後に有効。v2 DB パスでは None のため skip)
    Tier 3: grade_code が E/H → "1勝/2勝クラス"
            (v2 DB パスの fallback。jyoken が取れた場合は Tier 2 で処理済み)
    Tier 4: race_name が _RACE_GRADE_MAP に一致 → G1/G2/G3
    Tier 5: race_name の正規表現 → 条件クラス
    Tier 6: grade_code == 'R' → "重賞"
    """
    g = grade_code.strip() if grade_code else ""

    # Tier 1: 重賞グレード・リステッド（jvdl/v2 DB 共通で信頼できるコード）
    if g and g in _RELIABLE_GRADE_CODES:
        label = _GRADE_TO_LABEL.get(g) or _GRADE_TO_LABEL.get(g.upper())
        if label:
            return label

    # Tier 2: jyoken_cd（specs.py のバイト位置修正後に有効）
    for jy_raw in (jyoken_cd_2, jyoken_cd_3, jyoken_cd_4, jyoken_cd_5):
        jy = (jy_raw or "").strip()
        if jy and jy != "000":
            label = _JYOKEN_TO_CLASS.get(jy)
            if label:
                return label

    # Tier 3: grade_code E/H（v2 DB の 1勝/2勝クラス fallback）
    if g in ("E", "H"):
        label = _GRADE_TO_LABEL.get(g)
        if label:
            return label

    # Tier 4: race_name 重賞ルックアップ（G1/G2/G3）
    name = (race_name or "").strip()
    if name:
        for fragment, grade_label in _RACE_GRADE_MAP:
            if fragment in name:
                return grade_label

    # Tier 5: 正規表現による条件クラス抽出
    if name:
        for pattern, class_label in _CLASS_REGEX:
            if pattern.search(name):
                return class_label

    # Tier 6: grade_code == 'R'（jvdl 重賞タグ、格付け不明）
    if g == "R":
        return "重賞"

    return None


# ── SQL ───────────────────────────────────────────────────────────────────────

_SQL_RACES_BY_DATE = """
SELECT
    id                  AS race_id,
    race_num,
    keibajo_code,
    distance,
    track_code,
    grade_code,
    race_name_hondai,
    race_name_short_10,
    syusso_tosu,
    hassou_time,
    tenko_code,
    shiba_baba_code,
    dirt_baba_code
FROM   races
WHERE  race_date = %s
ORDER  BY keibajo_code, race_num
"""

# 2026-07 修正: 従来 fukurou_jvdl.races/race_entries（JVDLフォーマット・旧スキーマ）を
# 参照していたが、このテーブルは bulk_ingest_v2 が書き込まなくなって以降更新が
# 止まっている「旧・未使用」テーブル（2026-06-14で停止）。実際に最新データが
# 入り続けている races_v2（列名が keiba_v2.races とほぼ同一のため _build_from_v2
# をそのまま再利用できる）を参照するよう修正した。
_SQL_JVDL_RACES_BY_DATE = """
SELECT
    race_id             AS race_id,
    race_num,
    keibajo_code,
    distance,
    track_code,
    grade_code,
    race_name_hondai,
    race_name_short_10,
    shusso_tosu         AS syusso_tosu,
    hassou_time,
    tenko_code,
    shiba_baba_code,
    dirt_baba_code
FROM   races_v2
WHERE  LEFT(race_id, 8) = %s
ORDER  BY keibajo_code, race_num
"""


# ── Pydantic モデル ───────────────────────────────────────────────────────────

class RaceSummary(BaseModel):
    race_id: str
    race_num: int
    keibajo_code: str
    keibajo_name: str
    distance: int
    track_code: str | None
    grade_code: str | None
    race_name: str
    syusso_tosu: int | None
    hassou_time: str | None = None      # "HH:MM" 形式
    class_label: str | None = None      # "G1", "3歳未勝利", "2歳新馬" 等
    is_special: bool = False            # JV-Data grade_code=='E'（特別競走）フラグ
    tenko_code: str | None = None
    shiba_baba_code: str | None = None
    dirt_baba_code: str | None = None


class RaceListResponse(BaseModel):
    date: str
    races: list[RaceSummary]


# ── ビルダー ──────────────────────────────────────────────────────────────────

def _build_from_v2(rows: list) -> list[RaceSummary]:
    summaries: list[RaceSummary] = []
    for row in rows:
        kc       = str(row["keibajo_code"]).strip().zfill(2)
        name_raw = (row.get("race_name_hondai") or row.get("race_name_short_10") or "").strip()
        grade    = str(row["grade_code"]).strip() if row["grade_code"] else None
        lbl      = _compute_class_label(grade, None, None, None, None, None, name_raw)
        # 名称がない場合はクラスラベルで補完
        display_name = name_raw or lbl or ""
        summaries.append(RaceSummary(
            race_id         = str(row["race_id"]),
            race_num        = int(row["race_num"]),
            keibajo_code    = kc,
            keibajo_name    = _KEIBAJO_NAME.get(kc, kc),
            distance        = int(row["distance"]),
            track_code      = str(row["track_code"]).strip() if row["track_code"] else None,
            grade_code      = grade,
            race_name       = display_name,
            syusso_tosu     = int(row["syusso_tosu"]) if row["syusso_tosu"] is not None else None,
            hassou_time     = _fmt_time(row.get("hassou_time")),
            class_label     = lbl,
            tenko_code      = str(row["tenko_code"]).strip() if row.get("tenko_code") else None,
            shiba_baba_code = str(row["shiba_baba_code"]).strip() if row.get("shiba_baba_code") else None,
            dirt_baba_code  = str(row["dirt_baba_code"]).strip() if row.get("dirt_baba_code") else None,
        ))
    return summaries


# ── エンドポイント ────────────────────────────────────────────────────────────

@router.get("/races", response_model=RaceListResponse)
def list_races(
    date: date = Query(..., description="対象日 (YYYY-MM-DD)"),
) -> RaceListResponse:
    # Step 1: fukurou_keiba_v2（ETL 済み過去データ）
    try:
        with get_v2_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(_SQL_RACES_BY_DATE, (date,))
                rows = cur.fetchall()
    except Exception as exc:
        logger.exception("[V2Races] keiba_v2 クエリ失敗: %s", exc)
        raise HTTPException(status_code=500, detail="データ取得エラーが発生しました")

    if rows:
        return RaceListResponse(date=str(date), races=_build_from_v2(rows))

    # Step 2: jvdl フォールバック（今週末など未来レース用。races_v2 参照）
    logger.info("[V2Races] keiba_v2 に %s のデータなし → jvdl(races_v2) フォールバック", date)
    try:
        with get_jvdl_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(_SQL_JVDL_RACES_BY_DATE, (date.strftime("%Y%m%d"),))
                jvdl_rows = cur.fetchall()
    except Exception as exc:
        logger.exception("[V2Races] jvdl フォールバック失敗: %s", exc)
        raise HTTPException(status_code=500, detail="データ取得エラーが発生しました")

    logger.info("[V2Races] jvdl(races_v2) から %d レース取得: %s", len(jvdl_rows), date)
    return RaceListResponse(date=str(date), races=_build_from_v2(jvdl_rows))


class WeekendRacesResponse(BaseModel):
    """今週末の開催日ごとレース一覧。日付不要でフロントが1リクエストで取得できる。"""
    available_dates: list[str]                    # データのある日付のみ（YYYY-MM-DD）
    races_by_date:   dict[str, list[RaceSummary]] # date → races


def _this_weekend() -> tuple[date, date]:
    """今週の土曜・日曜を返す。JST 固定（UTC 日曜 00:xx が JST 月曜と誤判定されるのを防ぐ）。
    土曜 → 今日が土曜
    日曜 → 昨日（土曜）＋今日（日曜） ← 日曜は +6 ではなく -1
    平日 → 次の土曜・日曜
    """
    import datetime as _dt_jst
    from zoneinfo import ZoneInfo
    today   = _dt_jst.datetime.now(ZoneInfo("Asia/Tokyo")).date()
    weekday = today.weekday()   # 0=月 … 5=土 6=日
    if weekday == 5:
        sat = today
    elif weekday == 6:
        sat = today - timedelta(days=1)  # 日曜は前日（土曜）が同一週末
    else:
        sat = today + timedelta(days=(5 - weekday))
    return sat, sat + timedelta(days=1)


def _fetch_races_for_date(d: date) -> list[RaceSummary]:
    """1日分のレースをkeiba_v2 → jvdlの優先順で取得する共通ヘルパー。"""
    try:
        with get_v2_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(_SQL_RACES_BY_DATE, (d,))
                rows = cur.fetchall()
        if rows:
            return _build_from_v2(rows)
    except Exception as exc:
        logger.warning("[WeekendRaces] keiba_v2 失敗 %s: %s", d, exc)

    try:
        with get_jvdl_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(_SQL_JVDL_RACES_BY_DATE, (d.strftime("%Y%m%d"),))
                jvdl_rows = cur.fetchall()
        return _build_from_v2(jvdl_rows)
    except Exception as exc:
        logger.warning("[WeekendRaces] jvdl 失敗 %s: %s", d, exc)
        return []


@router.get("/races/weekend", response_model=WeekendRacesResponse)
def get_weekend_races() -> WeekendRacesResponse:
    """今週末（土・日）のレース一覧を日付ごとにまとめて返す。date パラメータ不要。"""
    sat, sun = _this_weekend()
    result: dict[str, list[RaceSummary]] = {}

    for d in (sat, sun):
        races = _fetch_races_for_date(d)
        if races:
            result[str(d)] = races
            logger.info("[WeekendRaces] %s → %d レース", d, len(races))

    return WeekendRacesResponse(
        available_dates = sorted(result.keys()),
        races_by_date   = result,
    )


# ── レーススコア計算（race_level.py が利用する独立ロジック） ────────────────────
# 2026-07 注記: V2アンサンブル引退で races.py 本体の _compute_detail は削除したが、
# 以下のレース点数計算（対戦馬次走成績×同日タイム×クラス補正）はV2推論とは無関係の
# 独立したドメインロジックで、api_v2/routers/race_level.py（GET /api/v2/race-level/{id}、
# フロントの RaceLevelPanel/RaceLevelModal が使用）が引き続き依存しているため残す。

class OpponentResult(BaseModel):
    """対戦馬1頭分の次走情報（レースレベル判定用）。"""
    horse_id:      str
    this_rank:     int           # その過去走での着順
    this_margin:   float | None  # 勝ち馬からの秒差（winner=0.0、不明=None）
    next_race_rank: int | None   # 次走の確定着順（未出走=None）


class RaceScore(BaseModel):
    """過去走1レース分のレース点数（P1+P2 実装: 75点満点）。"""
    total_score:             float  # 0〜75（将来 adversity_bonus +25 で100点満点予定）
    time_score:              float  # 0〜30: 同日タイム指数（P2）
    member_level_score:      float  # 0〜30: 対戦馬次走好走率（P1）
    class_score:             float  # 0〜15: グレード補正（P1）
    track_condition_warning: bool   # True: 馬場差アラート（比較対象で馬場状態コードが混在）
    sample_count:            int    # 同日タイム比較サンプル数（信頼性確認用）
    label:                   str    # "S" ≥60 / "A" ≥49 / "B" ≥38 / "C" <38


class PastRaceRecord(BaseModel):
    """1走分の過去成績。"""
    race_id:         str | None = None           # 内部 race_id（フロント連携・将来リンク用）
    date:            str
    race_name:       str | None = None
    keibajo:         str | None = None
    distance:        int | None = None
    surface:         str | None = None           # "芝" | "ダ" | "障"
    track_condition: str | None = None           # "良" | "稍重" | "重" | "不良"
    rank:            int | None = None
    head_count:      int | None = None
    race_time:       float | None = None         # 秒（例: 70.4）
    agari_3f:        float | None = None         # 上がり3F秒（例: 35.1）
    opponents_next_races: list[OpponentResult] = []  # 同レース出走馬の次走成績
    race_score:      RaceScore | None = None     # レース点数（Pro馬柱用）


def _score_to_label(score: float) -> str:
    """75点満点スケールのラベル変換。"""
    if score >= 60.0:
        return "S"
    if score >= 49.0:
        return "A"
    if score >= 38.0:
        return "B"
    return "C"


def _compute_class_score(grade_code: str | None) -> float:
    """grade_code → 0〜15点のクラス補正スコア。"""
    if not grade_code:
        return 3.0
    return _GRADE_CLASS_SCORE.get(str(grade_code).strip().upper(), 3.0)


def _compute_member_level_score(opponents: list) -> float:
    """対戦馬（this_rank ≤ 5 のもの）の次走3着以内率 → 0〜30点。
    サンプル 3 件未満は統計的に不安定なため中間値 15.0 を返す。
    """
    eligible = [
        o for o in opponents
        if o.next_race_rank is not None and o.this_rank <= 5
    ]
    if len(eligible) < 3:
        return 15.0
    good_rate = sum(1 for o in eligible if o.next_race_rank <= 3) / len(eligible)
    return round(good_rate * 30.0, 2)


def _compute_time_score(
    horse_time:   float | None,
    daily_avg:    float | None,
    daily_std:    float | None,
    sample_count: int,
) -> float:
    """同日タイム指数 → 0〜30点。
    horse_time < daily_avg（速い）→ 高得点。
    std が None / ≤0（サンプル1件以下）は信頼性低として中間補正する。
    """
    if horse_time is None or daily_avg is None or daily_avg <= 0:
        return 15.0  # データなし → 中間値
    if daily_std is None or daily_std < 0.01:
        return 15.0  # std 未定義: 1サンプルのみ → 比較不能
    z = (daily_avg - horse_time) / daily_std  # 速い = 正値
    raw = 15.0 + z * 7.5                      # ±2σ → 0〜30 範囲
    score = max(0.0, min(30.0, raw))
    if sample_count < 2:
        # サンプル不足: 中間寄りに引き寄せる
        score = score * 0.5 + 7.5
    return round(score, 2)


def _build_race_score(
    pr:         "PastRaceRecord",
    time_stats: dict | None,
    grade_code: str | None,
) -> RaceScore:
    """PastRaceRecord + 時間統計 + grade_code から RaceScore を組み立てる。"""
    member_sc = _compute_member_level_score(pr.opponents_next_races)
    class_sc  = _compute_class_score(grade_code)

    if time_stats:
        t_score   = _compute_time_score(
            pr.race_time,
            time_stats.get("daily_avg_time"),
            time_stats.get("daily_std_time"),
            time_stats.get("sample_count", 0),
        )
        tc_warn   = bool(time_stats.get("track_condition_warning", False))
        sample_n  = int(time_stats.get("sample_count", 0))
    else:
        t_score  = 15.0
        tc_warn  = False
        sample_n = 0

    total = t_score + member_sc + class_sc
    return RaceScore(
        total_score             = round(total, 2),
        time_score              = t_score,
        member_level_score      = member_sc,
        class_score             = class_sc,
        track_condition_warning = tc_warn,
        sample_count            = sample_n,
        label                   = _score_to_label(total),
    )


_SQL_DAILY_TIME_STATS = """
SELECT
    keys.d                        AS race_date,
    keys.kj                       AS keibajo_code,
    keys.dist                     AS distance,
    keys.tc                       AS track_code,
    AVG(e.race_time)              AS daily_avg_time,
    STDDEV_SAMP(e.race_time)      AS daily_std_time,
    COUNT(*)                      AS sample_count,
    (
        COUNT(DISTINCT CASE
            WHEN r.shiba_baba_code IS NOT NULL
             AND r.shiba_baba_code::text NOT IN ('0', '')
            THEN r.shiba_baba_code END) > 1
        OR
        COUNT(DISTINCT CASE
            WHEN r.dirt_baba_code IS NOT NULL
             AND r.dirt_baba_code::text NOT IN ('0', '')
            THEN r.dirt_baba_code END) > 1
    )                             AS track_condition_warning
FROM unnest(%s::date[], %s::text[], %s::int[], %s::text[])
     AS keys(d, kj, dist, tc)
JOIN races r
  ON  r.race_date          = keys.d
  AND r.keibajo_code::text = keys.kj
  AND r.distance           = keys.dist
  AND r.track_code::text   = keys.tc
JOIN race_entries e
  ON  e.race_id          = r.id
  AND e.kakutei_chakujun = 1
  AND e.race_time        IS NOT NULL
  AND e.race_time         > 0
GROUP BY keys.d, keys.kj, keys.dist, keys.tc
"""


def _fetch_daily_time_stats(
    race_meta_map: dict[str, dict],
) -> dict[str, dict]:
    """過去走レース群の同日タイム統計を一括取得する（N+1回避: 1クエリ）。

    Args:
        race_meta_map: {race_id: {date, keibajo_code, distance, track_code, grade_code}}

    Returns:
        {race_id: {daily_avg_time, daily_std_time, sample_count, track_condition_warning}}
    """
    if not race_meta_map:
        return {}

    from collections import defaultdict

    # 一意な (date, keibajo, dist, tc) キーを収集し race_id へ逆引きできるようにする
    key_to_rids: dict[tuple, list[str]] = defaultdict(list)
    for rid, meta in race_meta_map.items():
        d    = meta.get("date")
        kj   = meta.get("keibajo_code")
        dist = meta.get("distance")
        tc   = meta.get("track_code")
        # 必須フィールドが揃っていない race_id はスキップ
        if d is None or not kj or dist is None or not tc:
            continue
        key = (str(d)[:10], str(kj), int(dist), str(tc))
        key_to_rids[key].append(rid)

    if not key_to_rids:
        return {}

    unique_keys = list(key_to_rids.keys())
    # unnest 用に各次元を別リストへ分解
    import datetime as _dt
    dates  = [_dt.date.fromisoformat(k[0]) for k in unique_keys]
    kjajos = [k[1] for k in unique_keys]
    dists  = [k[2] for k in unique_keys]
    tcs    = [k[3] for k in unique_keys]

    try:
        with get_v2_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(_SQL_DAILY_TIME_STATS, (dates, kjajos, dists, tcs))
                rows = cur.fetchall()
    except Exception as e:
        logger.warning("[RaceScore] 同日タイム統計取得失敗: %s", e)
        return {}

    result: dict[str, dict] = {}
    for row in rows:
        # DB から返ってくる値で逆引きキーを再構築
        key = (
            str(row["race_date"])[:10],
            str(row["keibajo_code"] or "").strip(),
            int(row["distance"]) if row["distance"] is not None else 0,
            str(row["track_code"] or "").strip(),
        )
        stats = {
            "daily_avg_time":          float(row["daily_avg_time"])  if row["daily_avg_time"]  is not None else None,
            "daily_std_time":          float(row["daily_std_time"])  if row["daily_std_time"]  is not None else None,
            "sample_count":            int(row["sample_count"]),
            "track_condition_warning": bool(row["track_condition_warning"]),
        }
        for rid in key_to_rids.get(key, []):
            result[rid] = stats

    return result


def _fetch_horse_name_map(horse_ids: list[str]) -> dict[str, str | None]:
    """horse_id → 馬名 (fukurou_jvdl.horses) — 父・母父名のルックアップ用"""
    if not horse_ids:
        return {}
    try:
        with get_jvdl_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, name FROM horses WHERE id = ANY(%s)",
                    (horse_ids,),
                )
                rows = cur.fetchall()
        return {str(r["id"]): r.get("name") for r in rows}
    except Exception as e:
        logger.warning("[RaceDetail] 父・母父名ルックアップ失敗: %s", e)
        return {}


# ── レース詳細 Pydantic モデル ────────────────────────────────────────────────
# フロントエンドの RawRaceDetail / RawHorse インターフェースに完全対応


class TrainingSession(BaseModel):
    training_date: str
    center:        str
    course:        str
    record_type:   str           # 'HC' (坂路) / 'WC' (ウッド)
    time_total:    float | None
    lap_1:         float | None  # ラスト1F
    lap_2:         float | None
    lap_3:         float | None
    lap_4:         float | None


class HorseTrainingSummary(BaseModel):
    horse_id: str
    sessions: list[TrainingSession]


class RaceTrainingResponse(BaseModel):
    race_id:   str
    race_date: str
    horses:    list[HorseTrainingSummary]


# ── エンドポイント ────────────────────────────────────────────────────────────

_SQL_TRAINING: str = """
SELECT DISTINCT
    t.horse_id,
    t.date::date   AS training_date,
    t.center,
    t.course,
    t.time_total,
    t.lap_1,
    t.lap_2,
    t.lap_3,
    t.lap_4
FROM  training_data t
WHERE t.horse_id = ANY(%s)
  AND t.date::date >= %s::date - INTERVAL '30 days'
  AND t.date::date <  %s::date
ORDER BY t.horse_id, t.date::date DESC
"""


@router.get("/races/{race_id}/training", response_model=RaceTrainingResponse)
def get_race_training(race_id: str) -> RaceTrainingResponse:
    """対象レースの全出走馬の直近30日調教データを返す（重複排除済み）。"""
    race_date_str = race_id[:8]
    if len(race_date_str) < 8 or not race_date_str.isdigit():
        raise HTTPException(status_code=400, detail=f"不正な race_id: {race_id}")

    with get_jvdl_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # 2026-07 修正: race_entries（旧・未使用テーブル）ではなく
            # race_entries_v2 を参照する（列名: blood_no → horse_id）。
            cur.execute(
                "SELECT blood_no AS horse_id FROM race_entries_v2 WHERE race_id = %s",
                (race_id,),
            )
            horse_rows = cur.fetchall()

    if not horse_rows:
        raise HTTPException(status_code=404, detail=f"レースが見つかりません: {race_id}")

    horse_ids = [str(r["horse_id"]) for r in horse_rows]

    with get_jvdl_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(_SQL_TRAINING, (horse_ids, race_date_str, race_date_str))
            rows = cur.fetchall()

    sessions_map: dict[str, list[TrainingSession]] = {}
    for r in rows:
        hid    = str(r["horse_id"])
        course = str(r["course"] or "")
        rtype  = "HC" if course == "坂路" else "WC"
        s = TrainingSession(
            training_date=str(r["training_date"]),
            center=str(r["center"] or ""),
            course=course,
            record_type=rtype,
            time_total=_sf(r["time_total"]),
            lap_1=_sf(r["lap_1"]),
            lap_2=_sf(r["lap_2"]),
            lap_3=_sf(r["lap_3"]),
            lap_4=_sf(r["lap_4"]),
        )
        sessions_map.setdefault(hid, []).append(s)

    horses_out = [
        HorseTrainingSummary(horse_id=hid, sessions=sessions)
        for hid, sessions in sessions_map.items()
    ]

    return RaceTrainingResponse(
        race_id=race_id,
        race_date=race_date_str,
        horses=horses_out,
    )


