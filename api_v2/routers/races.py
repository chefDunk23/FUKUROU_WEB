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
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import psycopg2.extras
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel

from api_v2.deps import rate_limit_predict
from shared.cache import RACE_DETAIL_CACHE_PFX, get_redis_client
from shared.db.jvdl import get_conn as get_jvdl_conn
from shared.db.jvdata import get_conn as get_v2_conn
from shared.services.model_version import get_model_version
from ._race_common import (
    JV_GRADE_TO_LABEL,
    _BABA_LABEL,
    _CLASS_REGEX,
    _GRADE_CLASS_SCORE,
    _GRADE_TO_LABEL,
    _JYOKEN_TO_CLASS,
    _KEIBAJO_NAME,
    _RACE_GRADE_MAP,
    _TENKO_LABEL,
    _baba_str,
    _clean_name,
    _is_valid_code,
    _sf,
    _si,
    _surface_str,
    _weather_str,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v2", tags=["v2-races"])

# ── 定数 ──────────────────────────────────────────────────────────────────────

_COURSE_TYPE_TO_TRACK_CODE: dict[str, str] = {
    "芝":    "10",
    "ダート": "23",   # JV-Data ダート下限; 10-22=芝, 23-29=ダ, 51-59=障
    "障害":   "51",
}


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

_SQL_JVDL_RACES_BY_DATE = """
SELECT
    r.id              AS race_id,
    r.race_number     AS race_num,
    r.place_code      AS keibajo_code,
    COALESCE(NULLIF(TRIM(r.name), ''), '') AS race_name,
    r.distance,
    r.course_type,
    r.grade_code,
    r.start_time,
    r.race_type_code,
    r.jyoken_cd_2,
    r.jyoken_cd_3,
    r.jyoken_cd_4,
    r.jyoken_cd_5,
    COUNT(e.horse_id) AS syusso_tosu
FROM   races r
LEFT   JOIN race_entries e ON e.race_id = r.id
WHERE  r.date >= %s AND r.date < %s + INTERVAL '1 day'
GROUP  BY r.id, r.race_number, r.place_code, r.name, r.date,
          r.distance, r.course_type, r.grade_code, r.start_time,
          r.race_type_code, r.jyoken_cd_2, r.jyoken_cd_3,
          r.jyoken_cd_4, r.jyoken_cd_5
ORDER  BY r.place_code, r.race_number
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


def _build_from_jvdl(rows: list) -> list[RaceSummary]:
    summaries: list[RaceSummary] = []
    for row in rows:
        kc          = str(row["keibajo_code"]).strip().zfill(2)
        course_type = str(row["course_type"] or "").strip()
        track_code  = _COURSE_TYPE_TO_TRACK_CODE.get(course_type, "10")
        grade       = str(row["grade_code"]).strip() if row["grade_code"] else None
        name_raw    = str(row["race_name"]).strip()
        lbl = _compute_class_label(
            grade,
            str(row["race_type_code"] or "").strip() or None,
            str(row["jyoken_cd_2"] or "").strip() or None,
            str(row["jyoken_cd_3"] or "").strip() or None,
            str(row["jyoken_cd_4"] or "").strip() or None,
            str(row["jyoken_cd_5"] or "").strip() or None,
            name_raw,
        )
        # 名称が空のときはクラスラベルで補完（例: "3歳未勝利"）、それもなければ "NR"
        display_name = name_raw or lbl or f"{row['race_num']}R"
        summaries.append(RaceSummary(
            race_id      = str(row["race_id"]),
            race_num     = int(row["race_num"]),
            keibajo_code = kc,
            keibajo_name = _KEIBAJO_NAME.get(kc, kc),
            distance     = int(row["distance"]) if row["distance"] else 0,
            track_code   = track_code,
            grade_code   = grade,
            race_name    = display_name,
            syusso_tosu  = int(row["syusso_tosu"]) if row["syusso_tosu"] else None,
            hassou_time  = _fmt_time(row.get("start_time")),
            class_label  = lbl,
            is_special   = (grade == "E"),
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

    # Step 2: jvdl フォールバック（今週末など未来レース用）
    logger.info("[V2Races] keiba_v2 に %s のデータなし → jvdl フォールバック", date)
    try:
        with get_jvdl_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(_SQL_JVDL_RACES_BY_DATE, (date, date))
                jvdl_rows = cur.fetchall()
    except Exception as exc:
        logger.exception("[V2Races] jvdl フォールバック失敗: %s", exc)
        raise HTTPException(status_code=500, detail="データ取得エラーが発生しました")

    logger.info("[V2Races] jvdl から %d レース取得: %s", len(jvdl_rows), date)
    return RaceListResponse(date=str(date), races=_build_from_jvdl(jvdl_rows))


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
                cur.execute(_SQL_JVDL_RACES_BY_DATE, (d, d))
                jvdl_rows = cur.fetchall()
        return _build_from_jvdl(jvdl_rows)
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


# ── レース詳細 Pydantic モデル ────────────────────────────────────────────────
# フロントエンドの RawRaceDetail / RawHorse インターフェースに完全対応

class SubmodelScores(BaseModel):
    score_ability_v2:  float
    score_course_v2:   float
    score_team_v2:     float
    score_training_v2: float
    score_pace_v2:     float
    score_pedigree_v1: float


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


class HorseExtra(BaseModel):
    sire_name:            str | None = None
    dam_sire_name:        str | None = None
    prev_race_grade:      str | None = None
    prev_race_rank:       int | None = None
    prev_race_days_ago:   int | None = None
    chokyo_score:         float | None = None
    past_races:           list[PastRaceRecord] = []
    ten_index:            float | None = None   # 0-100: テン速度指数、高いほど前付け
    agari_index:          float | None = None   # 0-100: 上がり速度指数、高いほど速い
    position_tendency:    float | None = None   # 0=逃げ〜1=追込: 予測ポジション
    predicted_field_pace: float | None = None   # 0〜1: レース全体ペース強度
    pace_harmony:         float | None = None   # ペース適合スコア


class RaceDetailHorse(BaseModel):
    umaban:          int
    wakuban:         int | None = None   # 未確定時 null（フロントは「—」表示）
    horse_id:        str
    horse_name:      str | None = None
    jockey_name:     str | None = None
    trainer_name:    str | None = None
    horse_weight:    int | None = None
    weight_diff:     int | None = None
    burden_weight:   float
    tan_odds:        float | None = None
    ninki:           int | None = None
    ai_score:        float             # 0.0–1.0
    ai_rank:         int
    submodel_scores: SubmodelScores
    extra:           HorseExtra


class PositioningMap(BaseModel):
    """馬番を脚質グループ別に分類した AI 隊列予想。"""
    nige:   list[int] = []   # 逃げ: predicted_position_norm < 0.15
    senko:  list[int] = []   # 先行: 0.15 〜 0.45
    sashi:  list[int] = []   # 差し: 0.45 〜 0.75
    oikomi: list[int] = []   # 追込: 0.75 〜


class TrackBiasInfo(BaseModel):
    """直近同コースレース結果から推定したトラックバイアス情報。"""
    bias_type:       str | None = None  # "内枠前有利" / "差し有利" / "均等" 等
    note:            str = ""
    sample_races:    int = 0
    is_opening_week: bool = False
    fallback_used:   bool = False


class RaceInfo(BaseModel):
    pace_prediction: str                      # 'slow' | 'medium' | 'fast' | 'unknown'
    bias_note:       str
    positioning_map: PositioningMap | None = None  # AI 隊列予想（データ不足時は null）
    track_bias:      TrackBiasInfo | None = None   # 直近同コースバイアス


class RaceDetailResponse(BaseModel):
    race_id:         str
    race_date:       str
    keibajo_name:    str
    race_num:        int
    race_name:       str
    distance:        int
    track_code:      str
    grade_code:      str | None = None
    class_label:     str | None = None   # "G1", "3歳未勝利", "2歳新馬" 等
    is_special:      bool = False        # JV-Data grade_code=='E'（特別競走）フラグ
    syusso_tosu:     int
    weather:         str
    track_condition: str
    race_info:       RaceInfo
    horses:          list[RaceDetailHorse]
    computed_at:     str | None = None   # ISO8601 UTC — キャッシュ生成タイムスタンプ


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


# ── prediction.py の推論パイプラインを借用 ────────────────────────────────────
# 循環なし: prediction.py は races.py を import していない
from api_v2.routers.prediction import (  # noqa: E402
    _DIRT_SUBMODEL_SCORES,
    _TURF_SUBMODEL_SCORES,
    _build_features,
    _detect_surface,
    _fetch_horse_history,
    _get_dual_engine,
)

# ── Redis キャッシュ（fail-open: 未起動でもエンドポイントは正常動作）────────────
# 接続ロジック本体は shared/cache.py に共通化済み（get_redis_client）。

_CACHE_TTL = 300           # 5 分: リアルタイムオッズ・馬体重の変化を反映
_CACHE_PFX = RACE_DETAIL_CACHE_PFX


# ── DB 永続キャッシュ（race_detail_cache テーブル）────────────────────────────

_SQL_DETAIL_SELECT = """
    SELECT payload FROM race_detail_cache
    WHERE race_id = %s AND model_version = %s
"""
_SQL_DETAIL_UPSERT = """
    INSERT INTO race_detail_cache (race_id, model_version, computed_at, payload)
    VALUES (%s, %s, now(), %s)
    ON CONFLICT (race_id) DO UPDATE
      SET model_version = EXCLUDED.model_version,
          computed_at   = EXCLUDED.computed_at,
          payload       = EXCLUDED.payload
"""


def _get_cached_detail(race_id: str, model_version: str) -> "RaceDetailResponse | None":
    """race_detail_cache からモデルバージョン一致のキャッシュを取得する。障害時は None を返す。"""
    try:
        with get_jvdl_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(_SQL_DETAIL_SELECT, (race_id, model_version))
                row = cur.fetchone()
        if row is None:
            return None
        return RaceDetailResponse.model_validate(row["payload"])
    except Exception:
        logger.exception("[Cache/DB] detail cache 読み取り失敗 race_id=%s", race_id)
        return None


def _save_detail_cache(resp: "RaceDetailResponse", model_version: str) -> None:
    """race_detail_cache テーブルに詳細データを UPSERT する。障害時はログのみ。"""
    try:
        with get_jvdl_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    _SQL_DETAIL_UPSERT,
                    (resp.race_id, model_version, psycopg2.extras.Json(resp.model_dump(mode="json"))),
                )
            conn.commit()
    except Exception:
        logger.exception("[Cache/DB] detail cache 保存失敗 race_id=%s", resp.race_id)


# ── 展開予想（pace simulation）────────────────────────────────────────────────

_PACE_LABEL_MAP = {
    "fast":    "ハイペース予想",
    "medium":  "平均ペース予想",
    "slow":    "スロー予想",
    "unknown": "ペース不明",
}
_PACE_BIAS_NOTE = {
    "fast":    "ハイペースが想定されます。差し・追い込み馬が台頭しやすい展開です。",
    "medium":  "平均的なペースが想定されます。展開の有利不利は少ない見通しです。",
    "slow":    "スローペースが想定されます。先行馬が有利になりやすい展開です。",
    "unknown": "",
}


def _compute_pace_prediction(
    raw_df: pd.DataFrame,
) -> tuple[str, str, "PositioningMap | None", "dict[int, dict] | None"]:
    """pace_simulation_v1 で展開予想ラベル・bias_note・隊列マップ・馬別シム値を算出する。

    Returns
    -------
    (pace_label, bias_note, positioning_map, sim_per_horse)
      sim_per_horse: {umaban: {position_tendency, predicted_field_pace, pace_harmony}} | None
    """
    try:
        from src.features.pace_simulation_v1 import create_pace_simulation_features

        req_cols = ["race_id", "umaban", "avg_first_corner_norm_5"]
        missing  = [c for c in req_cols if c not in raw_df.columns]
        if missing:
            logger.debug("[PaceSim] 必須カラム不足 %s → unknown", missing)
            return "unknown", "", None, None

        sim_df     = create_pace_simulation_features(raw_df.copy())
        field_pace = float(sim_df["predicted_field_pace"].iloc[0])

        if field_pace >= 0.55:
            label = "fast"
        elif field_pace >= 0.35:
            label = "medium"
        else:
            label = "slow"

        # 馬別シム値を常に抽出（品質チェックとは独立して返す）
        sim_per_horse: dict[int, dict] = {}
        for _, srow in sim_df.iterrows():
            ub = int(srow["umaban"])
            sim_per_horse[ub] = {
                "position_tendency":    _sf(srow.get("predicted_position_norm")),
                "predicted_field_pace": _sf(srow.get("predicted_field_pace")),
                "pace_harmony":         _sf(srow.get("pace_harmony_pre")),
            }

        # ── avg_first_corner_norm_5 の実データ品質チェック ──────────────────
        # first_corner = c1→c2→c3→c4 の優先順で最初に記録されたコーナー順位。
        # スプリント(≤1400m)ではc3が該当し、まくり馬の誤認を防ぐ。
        # 0.5 はデフォルト補完値（新潟直線など全コーナー未記録の場合）。
        # 実データが半数未満 or std < 0.10 の場合は信頼性不足として非表示。
        fc_col  = raw_df["avg_first_corner_norm_5"] if "avg_first_corner_norm_5" in raw_df.columns else pd.Series(dtype=float)
        n_total = len(raw_df)
        real_fc = fc_col.dropna()
        real_fc = real_fc[(real_fc < 0.49) | (real_fc > 0.51)]
        n_valid_fc = int(len(real_fc))
        fc_std     = float(real_fc.std(ddof=0)) if n_valid_fc > 1 else 0.0

        has_sufficient_real_data = n_valid_fc >= (n_total / 2)
        has_meaningful_variance  = fc_std >= 0.10

        if not (has_sufficient_real_data and has_meaningful_variance):
            logger.info(
                "[PaceSim] race_id=%s avg_first_corner_norm_5 実データ=%d/%d頭 std=%.4f"
                " → positioning_map=None（実データ不足または個性差なし）",
                raw_df["race_id"].iloc[0], n_valid_fc, n_total, fc_std,
            )
            return label, _PACE_BIAS_NOTE[label], None, sim_per_horse

        # 馬ごとの predicted_position_norm で脚質分類
        # 0=最前（逃げ）… 1=最後方（追込）
        groups: dict[str, list[int]] = {"nige": [], "senko": [], "sashi": [], "oikomi": []}
        for _, row in sim_df.sort_values("predicted_position_norm").iterrows():
            pos    = float(row["predicted_position_norm"])
            umaban = int(row["umaban"])
            if pos < 0.15:
                groups["nige"].append(umaban)
            elif pos < 0.45:
                groups["senko"].append(umaban)
            elif pos < 0.75:
                groups["sashi"].append(umaban)
            else:
                groups["oikomi"].append(umaban)

        pmap = PositioningMap(**groups)
        logger.info(
            "[PaceSim] race_id=%s field_pace=%.3f → %s  nige=%s senko=%s  (first_corner有効=%d/%d頭)",
            raw_df["race_id"].iloc[0], field_pace, label,
            pmap.nige, pmap.senko, n_valid_fc, n_total,
        )
        return label, _PACE_BIAS_NOTE[label], pmap, sim_per_horse

    except Exception as e:
        logger.warning("[PaceSim] 計算失敗: %s", e)
        return "unknown", "", None, None


# ── トラックバイアス推定（直近同コース結果ベース）────────────────────────────

def _compute_track_bias(
    keibajo_code: str,
    track_code: str,
    race_date,
    race_id: str,
) -> "TrackBiasInfo":
    """直近の同コース（競馬場 + 芝/ダート）レース結果からトラックバイアスを推定。

    シグナル1 (枠番バイアス):
      勝ち馬の枠番パーセンタイル = (wakuban-1) / (max_gate-1) の平均
      0=最内, 1=最外。平均 ≤ 0.35 → 内枠有利、≥ 0.65 → 外枠有利

    シグナル2 (決着スタイル):
      勝ち馬の上がり3Fランク（フィールド昇順）を正規化した平均
      0=最速クローザー(差し型), 1=最遅(前残り型)
      平均 ≤ 0.35 → 差し有利、≥ 0.65 → 前有利

    フォールバック:
      直近14日で3レース未満 → 28日に拡張 (fallback_used=True)
      それでも3レース未満   → 開幕週扱い。芝はデフォルト内枠前有利。
    """
    import datetime as _dt
    from collections import defaultdict

    _surface         = _surface_str(track_code) or "芝"
    surf_lo, surf_hi = {"芝": ("10", "22"), "ダ": ("23", "29"), "障": ("51", "59")}.get(_surface, ("10", "22"))
    race_dt          = pd.Timestamp(race_date).date()
    kc_str           = str(keibajo_code).strip().zfill(2)

    def _fetch(days_back: int) -> list:
        cutoff = race_dt - _dt.timedelta(days=days_back)
        try:
            with get_v2_conn() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(_SQL_RECENT_COURSE_RESULTS, (
                        kc_str, surf_lo, surf_hi, cutoff, race_dt, race_id,
                    ))
                    return cur.fetchall()
        except Exception as e:
            logger.warning("[TrackBias] クエリ失敗: %s", e)
            return []

    rows          = _fetch(14)
    fallback_used = False
    if len({str(r["race_id"]) for r in rows}) < 3:
        ext = _fetch(28)
        if len({str(r["race_id"]) for r in ext}) > len({str(r["race_id"]) for r in rows}):
            rows, fallback_used = ext, True

    races_map: dict[str, list] = defaultdict(list)
    for row in rows:
        races_map[str(row["race_id"])].append(row)

    sample_count    = len(races_map)
    is_opening_week = sample_count < 3
    is_turf         = _surface == "芝"

    if sample_count == 0:
        if is_turf:
            return TrackBiasInfo(
                bias_type="内枠前有利（推定）",
                note="直近の同コースデータなし。芝の開幕週は内枠・前有利になりやすいです。",
                sample_races=0, is_opening_week=True, fallback_used=False,
            )
        return TrackBiasInfo(
            bias_type=None,
            note="直近の同コースデータがありません。",
            sample_races=0, is_opening_week=True, fallback_used=False,
        )

    gate_pcts:  list[float] = []
    agari_pcts: list[float] = []

    for _rid, entries in races_map.items():
        winners = [e for e in entries if _si(e.get("finish_rank")) == 1]
        if not winners:
            continue
        winner = winners[0]

        # 枠番バイアス: 勝ち馬の枠番パーセンタイル
        w_gate   = _si(winner.get("wakuban"))
        all_gates = [_si(e.get("wakuban")) for e in entries
                     if _si(e.get("wakuban")) is not None and _si(e.get("wakuban")) > 0]
        if w_gate and w_gate > 0 and len(all_gates) >= 3:
            max_gate = max(all_gates)
            if max_gate > 1:
                gate_pcts.append((w_gate - 1) / (max_gate - 1))

        # 決着スタイル: 勝ち馬の上がり3Fランク正規化
        w_agari = _sf(winner.get("go_3f_time"))
        if w_agari and w_agari > 0:
            valid_times = sorted([
                _sf(e.get("go_3f_time"))  # type: ignore[arg-type]
                for e in entries
                if _sf(e.get("go_3f_time")) is not None and (_sf(e.get("go_3f_time")) or 0) > 0
            ])
            if len(valid_times) >= 3:
                rank = sum(1 for t in valid_times if t < w_agari - 0.001)
                agari_pcts.append(rank / max(len(valid_times) - 1, 1))

    if not gate_pcts and not agari_pcts:
        return TrackBiasInfo(
            bias_type=None,
            note=f"直近{sample_count}レースのデータから傾向を判定できませんでした。",
            sample_races=sample_count, is_opening_week=is_opening_week, fallback_used=fallback_used,
        )

    avg_gate  = sum(gate_pcts)  / len(gate_pcts)  if gate_pcts  else 0.5
    avg_agari = sum(agari_pcts) / len(agari_pcts) if agari_pcts else 0.5

    n_gate  = len(gate_pcts)
    n_agari = len(agari_pcts)
    inner_bias  = n_gate  >= 3 and avg_gate  <= 0.35
    outer_bias  = n_gate  >= 3 and avg_gate  >= 0.65
    front_bias  = n_agari >= 3 and avg_agari >= 0.65
    closer_bias = n_agari >= 3 and avg_agari <= 0.35

    if   inner_bias and front_bias:   bias_type = "内枠前有利"
    elif inner_bias and closer_bias:  bias_type = "内枠差し有利"
    elif outer_bias and front_bias:   bias_type = "外枠前有利"
    elif outer_bias and closer_bias:  bias_type = "外枠差し有利"
    elif inner_bias:                  bias_type = "内枠有利"
    elif outer_bias:                  bias_type = "外枠有利"
    elif front_bias:                  bias_type = "前有利（先行有利）"
    elif closer_bias:                 bias_type = "差し・追込有利"
    else:                             bias_type = "均等（バイアスなし）"

    note_parts: list[str] = []
    if gate_pcts:
        note_parts.append(f"内枠勝率 {round((1 - avg_gate) * 100)}%（{n_gate}R）")
    if agari_pcts:
        style = "前残り傾向" if front_bias else ("差し傾向" if closer_bias else "中間")
        note_parts.append(style)

    if fallback_used:
        pfx = f"先週以前{sample_count}レース: "
    elif is_opening_week:
        pfx = f"開幕週（{sample_count}レース）: "
    else:
        pfx = f"直近{sample_count}レース: "

    note = pfx + "、".join(note_parts) if note_parts else pfx + "バイアス判定中"
    return TrackBiasInfo(
        bias_type=bias_type, note=note,
        sample_races=sample_count, is_opening_week=is_opening_week, fallback_used=fallback_used,
    )



# ── 前走データ取得ヘルパー ────────────────────────────────────────────────────

def _fetch_prev_race(
    horse_ids: list[str],
    race_date,
) -> dict[str, dict]:
    """各馬の直前レース結果を返す。{horse_id: {prev_race_grade, prev_race_rank, prev_race_days_ago}}
    keiba_v2 の race_history を参照。jvdl フォールバック馬（未登録）は空 dict になる。
    """
    try:
        hist = _fetch_horse_history(horse_ids, race_date)
    except Exception as e:
        logger.warning("[RaceDetail] 前走履歴取得失敗: %s", e)
        return {}

    if hist.empty:
        return {}

    hist = hist.copy()
    hist["horse_id"]  = hist["horse_id"].astype(str)
    hist["_race_date"] = pd.to_datetime(hist["race_date"], errors="coerce")
    target_dt = pd.Timestamp(race_date)

    result: dict[str, dict] = {}
    for hid, grp in hist.groupby("horse_id"):
        latest    = grp.sort_values("_race_date").iloc[-1]
        days_ago  = (target_dt - latest["_race_date"]).days
        grade_raw = latest.get("grade_code")
        # keiba_v2 grade_code ("A","B","C"...) → 表示ラベル ("G1","G2"...)
        grade_lbl = _GRADE_TO_LABEL.get(str(grade_raw).strip().upper(), str(grade_raw).strip()) \
                    if grade_raw and str(grade_raw).strip() not in ("None", "") else None
        result[str(hid)] = {
            "prev_race_grade":    grade_lbl,
            "prev_race_rank":     _si(latest.get("kakutei_chakujun")),
            "prev_race_days_ago": int(days_ago) if pd.notna(days_ago) and days_ago >= 0 else None,
        }
    return result


_SQL_PAST_5_RACES = """
SELECT
    e.horse_id,
    r.id          AS race_id,
    r.race_date,
    r.race_name_hondai,
    r.keibajo_code,
    r.distance,
    r.track_code,
    r.tenko_code,
    r.shiba_baba_code,
    r.dirt_baba_code,
    r.syusso_tosu,
    r.grade_code,
    e.kakutei_chakujun,
    e.race_time,
    e.go_3f_time
FROM   race_entries e
JOIN   races r ON e.race_id = r.id
WHERE  e.horse_id = ANY(%s)
  AND  r.race_date < %s
  AND  e.kakutei_chakujun IS NOT NULL
  AND  e.kakutei_chakujun > 0
ORDER  BY e.horse_id, r.race_date DESC, r.id DESC
"""

# ── 直近同コース結果取得（トラックバイアス判定用）────────────────────────────
_SQL_RECENT_COURSE_RESULTS = """
SELECT
    r.id    AS race_id,
    r.race_date,
    e.umaban,
    e.wakuban,
    e.kakutei_chakujun  AS finish_rank,
    e.go_3f_time
FROM race_entries e
JOIN races r ON e.race_id = r.id
WHERE r.keibajo_code = %s
  AND r.track_code BETWEEN %s AND %s
  AND r.race_date >= %s
  AND r.race_date <  %s
  AND r.id != %s
  AND e.kakutei_chakujun IS NOT NULL
  AND e.kakutei_chakujun > 0
ORDER BY r.race_date DESC
LIMIT 300
"""

# ── 同日タイム統計一括取得 ─────────────────────────────────────────────────────
# 最大90レース分の (race_date, keibajo_code, distance, track_code) を
# unnest で一括JOINし、1クエリで全統計を取得する（N+1回避）。
# track_condition_warning: 同一グループ内で馬場コードが混在しているか
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

# ── レース点数スコア計算ヘルパー ────────────────────────────────────────────────


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


# ── 対戦馬の次走取得: 2クエリ + Python マッチング方式 ─────────────────────────
# LATERAL JOIN は race_date を含むインデックスがないため遅い。
# ① 対象レースの出走馬を一括取得（idx_re_race 使用: O(ms)）
# ② 出走馬の次走候補を一括取得（最古の対象レース日以降を全件: O(ms)）
# ③ Python で (horse_id, past_race_date) → 最初の次走 をマッチング
_SQL_OPPONENTS_IN_RACES = """
SELECT
    e.race_id,
    r.race_date,
    e.horse_id,
    e.kakutei_chakujun AS this_rank,
    CASE
        WHEN TRIM(e.time_diff) ~ '^[+-][0-9]+$'
        THEN GREATEST(0.0, TRIM(e.time_diff)::integer / 10.0)
        ELSE NULL
    END AS this_margin
FROM race_entries e
JOIN races r ON r.id = e.race_id
WHERE e.race_id = ANY(%s)
  AND e.kakutei_chakujun IS NOT NULL
  AND e.kakutei_chakujun > 0
ORDER BY e.race_id, e.kakutei_chakujun
"""

_SQL_NEXT_RACES_BULK = """
SELECT
    e.horse_id,
    r.race_date,
    e.kakutei_chakujun AS next_rank
FROM race_entries e
JOIN races r ON r.id = e.race_id
WHERE e.horse_id = ANY(%s)
  AND r.race_date >= %s
  AND e.kakutei_chakujun IS NOT NULL
  AND e.kakutei_chakujun > 0
ORDER BY e.horse_id, r.race_date ASC, r.id ASC
"""


def _fetch_past_5_races(
    horse_ids: list[str],
    race_date,
) -> tuple[dict[str, list[PastRaceRecord]], list[str], dict[str, dict]]:
    """各馬の直近5走を返す。
    Returns:
        (past5_map, race_ids_used, race_meta_map)
        past5_map    : {horse_id: [PastRaceRecord, ...]}
        race_ids_used: 使用した race_id のリスト（opponents 取得に渡す）
        race_meta_map: {race_id: {date, keibajo_code, distance, track_code, grade_code}}
                       （_fetch_daily_time_stats の入力に使う）
    データ不足・エラー時は ({}, [], {}) を返す（フォールバック安全）。
    """
    if not horse_ids:
        return {}, [], {}
    try:
        date_val = pd.Timestamp(race_date).date()
        with get_v2_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(_SQL_PAST_5_RACES, (horse_ids, date_val))
                rows = cur.fetchall()
    except Exception as e:
        logger.warning("[RaceDetail] 過去5走取得失敗: %s", e)
        return {}, [], {}

    result: dict[str, list[PastRaceRecord]] = {}
    race_meta_map: dict[str, dict] = {}
    all_race_ids: set[str] = set()
    from itertools import groupby
    rows_sorted = sorted(rows, key=lambda r: (str(r["horse_id"]), str(r["race_date"])), reverse=False)

    for hid_raw, grp in groupby(rows_sorted, key=lambda r: str(r["horse_id"])):
        entries = list(grp)
        recent5 = sorted(entries, key=lambda r: str(r["race_date"]), reverse=True)[:5]
        records: list[PastRaceRecord] = []
        for r in recent5:
            rid = str(r["race_id"])
            all_race_ids.add(rid)
            kc = str(r["keibajo_code"] or "").strip().zfill(2)
            tc = str(r["track_code"] or "").strip()
            surface = _surface_str(tc)
            shiba   = r.get("shiba_baba_code")
            dirt    = r.get("dirt_baba_code")
            baba_code  = shiba if _is_valid_code(shiba) else (dirt if _is_valid_code(dirt) else None)
            track_cond = _baba_str(baba_code) if baba_code else None
            race_time_raw = r.get("race_time")
            go3f_raw      = r.get("go_3f_time")

            # タイム統計・クラス算出に使うメタ情報（race_idごとに1回だけ記録）
            if rid not in race_meta_map:
                raw_tc  = str(r["track_code"] or "").strip()
                raw_kj  = str(r["keibajo_code"] or "").strip()
                raw_dist = _si(r.get("distance"))
                raw_gc  = str(r["grade_code"] or "").strip() if r.get("grade_code") else None
                race_meta_map[rid] = {
                    "date":         r["race_date"],
                    "keibajo_code": raw_kj,
                    "distance":     raw_dist,
                    "track_code":   raw_tc if raw_tc else None,
                    "grade_code":   raw_gc if raw_gc else None,
                }

            records.append(PastRaceRecord(
                race_id         = rid,
                date            = str(r["race_date"]),
                race_name       = (str(r["race_name_hondai"] or "").strip() or None),
                keibajo         = _KEIBAJO_NAME.get(kc, kc or None),
                distance        = _si(r.get("distance")),
                surface         = surface,
                track_condition = track_cond,
                rank            = _si(r.get("kakutei_chakujun")),
                head_count      = _si(r.get("syusso_tosu")),
                race_time       = float(race_time_raw) if race_time_raw and float(race_time_raw) > 0 else None,
                agari_3f        = float(go3f_raw)      if go3f_raw      and float(go3f_raw)      > 0 else None,
            ))
        result[hid_raw] = records

    return result, list(all_race_ids), race_meta_map


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


def _fetch_opponents_next_races(
    race_ids: list[str],
) -> dict[str, list[OpponentResult]]:
    """対象レースに出走した全馬の次走成績を 2クエリ + Python マッチングで取得。
    LATERAL JOIN を避けることで O(ms) を実現（idx_re_race / idx_re_horse 利用）。
    Returns: {race_id: [OpponentResult, ...]} 着順昇順
    """
    if not race_ids:
        return {}
    try:
        with get_v2_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # ① 対象レースの出走馬一覧
                cur.execute(_SQL_OPPONENTS_IN_RACES, (race_ids,))
                opp_rows = cur.fetchall()

                if not opp_rows:
                    return {}

                # ② 出走馬の次走候補を最古の対象日付以降で一括取得
                opp_horse_ids = list({str(r["horse_id"]) for r in opp_rows})
                min_date = min(r["race_date"] for r in opp_rows)
                cur.execute(_SQL_NEXT_RACES_BULK, (opp_horse_ids, min_date))
                next_rows = cur.fetchall()

    except Exception as e:
        logger.warning("[RaceDetail] 対戦馬次走取得失敗: %s", e)
        return {}

    # Python でマッチング: horse_id → [(race_date, next_rank), ...] sorted by date
    from collections import defaultdict
    future_by_horse: dict[str, list[tuple]] = defaultdict(list)
    for nr in next_rows:
        future_by_horse[str(nr["horse_id"])].append(
            (nr["race_date"], int(nr["next_rank"]))
        )
    # すでに race_date 昇順なので bisect でも可だが len が小さいので線形で十分

    result: dict[str, list[OpponentResult]] = {}
    for row in opp_rows:
        rid      = str(row["race_id"])
        hid      = str(row["horse_id"])
        past_dt  = row["race_date"]
        margin_raw = row.get("this_margin")

        # horse の次走 = past_dt より後で最初のもの
        next_rank: int | None = None
        for (future_dt, rank) in future_by_horse.get(hid, []):
            if future_dt > past_dt:
                next_rank = rank
                break

        result.setdefault(rid, []).append(OpponentResult(
            horse_id       = hid,
            this_rank      = int(row["this_rank"]),
            this_margin    = float(margin_raw) if margin_raw is not None else None,
            next_race_rank = next_rank,
        ))
    return result



def _compute_ten_index(avg_first_corner: float | None) -> float | None:
    """avg_first_corner_norm_5 (0=逃げ, 1=追込) → テン指数 (0-100, 高いほど前付け)。"""
    if avg_first_corner is None:
        return None
    val = max(0.0, min(1.0, float(avg_first_corner)))
    return round((1.0 - val) * 100, 1)


def _compute_agari_index(avg_go3f_rank: float | None, max_field: int = 16) -> float | None:
    """avg_go3f_rank_5 (1=最速, max_field=最遅) → 上がり指数 (0-100, 高いほど速い)。"""
    if avg_go3f_rank is None:
        return None
    rank = max(1.0, min(float(max_field), float(avg_go3f_rank)))
    return round((1.0 - (rank - 1.0) / max(max_field - 1, 1)) * 100, 1)


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


def _fetch_detail_supplements(race_id: str) -> dict[int, dict]:
    """umaban → {wakuban, jockey_name, trainer_name} を返す。
    keiba_v2（race_entries に wakuban / jockey_name_short / trainer_name_short あり）
    → jvdl（bracket_number / jockeys JOIN / trainers JOIN）の順で試みる。
    """
    # keiba_v2 path (ETL済み過去レース)
    try:
        with get_v2_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT
                        e.umaban,
                        e.wakuban,
                        TRIM(COALESCE(e.jockey_name_short,  '')) AS jockey_name,
                        TRIM(COALESCE(e.trainer_name_short, '')) AS trainer_name
                    FROM race_entries e
                    WHERE e.race_id = %s
                    ORDER BY e.umaban
                    """,
                    (race_id,),
                )
                rows = cur.fetchall()
        if rows:
            return {
                int(r["umaban"]): {
                    "wakuban":      int(r["wakuban"]) if r.get("wakuban") else None,
                    "jockey_name":  _clean_name(r.get("jockey_name")),
                    "trainer_name": _clean_name(r.get("trainer_name")),
                }
                for r in rows
            }
    except Exception as e:
        logger.warning("[RaceDetail] keiba_v2 supplement取得失敗: %s", e)

    # jvdl path (今週末の未来レース)
    try:
        with get_jvdl_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT
                        COALESCE(NULLIF(e.horse_number::integer, 0),
                                 ROW_NUMBER() OVER (ORDER BY e.horse_id)::integer
                        ) AS umaban,
                        e.bracket_number  AS wakuban,
                        TRIM(COALESCE(j.name, '')) AS jockey_name,
                        TRIM(COALESCE(t.name, '')) AS trainer_name
                    FROM race_entries e
                    LEFT JOIN jockeys  j ON j.id = e.jockey_id
                    LEFT JOIN trainers t ON t.id = e.trainer_id
                    WHERE e.race_id = %s
                    ORDER BY e.horse_number
                    """,
                    (race_id,),
                )
                rows = cur.fetchall()
        return {
            int(r["umaban"]): {
                "wakuban":      int(r["wakuban"]) if r.get("wakuban") else None,
                "jockey_name":  _clean_name(r.get("jockey_name")),
                "trainer_name": _clean_name(r.get("trainer_name")),
            }
            for r in rows
            if r.get("umaban")
        }
    except Exception as e:
        logger.warning("[RaceDetail] jvdl supplement取得失敗: %s", e)

    return {}


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
            cur.execute(
                "SELECT horse_id FROM race_entries WHERE race_id = %s",
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


def _compute_detail(race_id: str) -> "RaceDetailResponse | None":
    """DB + LightGBM 推論による完全計算。

    戻り値:
        RaceDetailResponse — 計算成功（computed_at に UTC ISO8601 を付与）
        None               — レース未発見（raw_df.empty）
    送出:
        FileNotFoundError  — モデルファイル未ロード
        Exception          — その他エラー（呼び出し元でハンドリング）
    """
    # Step2: _build_features（DB取得 + 特徴量計算 + 6サブモデル推論）
    t_s2 = time.perf_counter()
    raw_df, X, *_ = _build_features(race_id)
    elapsed_s2 = time.perf_counter() - t_s2

    if raw_df.empty:
        return None

    # Step3: メインアンサンブル推論（LightGBM）
    t_s3 = time.perf_counter()
    dual_engine = _get_dual_engine()
    tc      = str(raw_df["track_code"].iloc[0]).strip()
    surface = _detect_surface(tc)
    engine  = dual_engine.dirt if surface == "dirt" else dual_engine.turf
    active  = _DIRT_SUBMODEL_SCORES if surface == "dirt" else _TURF_SUBMODEL_SCORES

    X_in = X[active].copy()
    for feat in engine.feature_names:
        if feat not in X_in.columns:
            X_in[feat] = np.nan
    scores = engine.predict(X_in[engine.feature_names])
    elapsed_s3 = time.perf_counter() - t_s3

    # ai_rank は生スコアの順序で確定（T-score 変換前）
    ai_ranks: list[int] = (
        pd.Series(scores)
        .rank(ascending=False, method="min")
        .astype(int)
        .tolist()
    )

    # Step4: T-score変換 + 補完クエリ + Pydanticマッピング
    t_s4 = time.perf_counter()

    # EMP T-score 変換（係数25: z≈+1.5σ の馬が87付近 → Sランク）
    _raw = np.array(scores, dtype=float)
    _mean, _std = float(_raw.mean()), float(_raw.std())
    if _std > 1e-9 and len(_raw) > 1:
        emp_scores: np.ndarray = (_raw - _mean) / _std * 25.0 + 50.0
        emp_scores = np.clip(emp_scores, 5.0, 99.0)
    else:
        emp_scores = np.full(len(_raw), 50.0)

    # 展開予想 + 隊列マップ + 馬別シムデータ
    pace_label, bias_note, positioning_map, sim_per_horse = _compute_pace_prediction(raw_df)

    # トラックバイアス推定（直近同コースデータベース）
    horse_ids = raw_df["horse_id"].astype(str).tolist()
    race_date = raw_df["race_date"].iloc[0]
    _raw_kc = str(raw_df["keibajo_code"].iloc[0]).strip().zfill(2)
    _raw_tc = str(raw_df["track_code"].iloc[0]).strip()
    track_bias_result = _compute_track_bias(_raw_kc, _raw_tc, race_date, race_id)

    # 前走・父母父名・枠番/騎手/調教師・過去5走の補完クエリ
    prev_map                              = _fetch_prev_race(horse_ids, race_date)
    past5_map, past_race_ids, race_meta_map = _fetch_past_5_races(horse_ids, race_date)

    t_opp = time.perf_counter()
    opponents_map = _fetch_opponents_next_races(past_race_ids)
    logger.info("[RaceDetail] opponents_next_races: %d races, %.3fs",
                len(past_race_ids), time.perf_counter() - t_opp)

    # 同日タイム統計を一括取得（1クエリ、N+1回避）
    t_rsc = time.perf_counter()
    time_stats_map = _fetch_daily_time_stats(race_meta_map)
    logger.info("[RaceScore] 同日タイム統計: %d unique keys → %d hits, %.3fs",
                len(race_meta_map), len(time_stats_map), time.perf_counter() - t_rsc)

    # opponents をマージ + RaceScore を各 PastRaceRecord に付与
    for _records in past5_map.values():
        for _pr in _records:
            if _pr.race_id and _pr.race_id in opponents_map:
                _pr.opponents_next_races = opponents_map[_pr.race_id]
            if _pr.race_id:
                _meta   = race_meta_map.get(_pr.race_id)
                _grade  = _meta.get("grade_code") if _meta else None
                _pr.race_score = _build_race_score(
                    _pr, time_stats_map.get(_pr.race_id), _grade
                )

    # 上がり指数に使うフィールドの馬場種別（_surface_str で整数範囲判定）
    _agari_col = (
        "avg_go3f_rank_5_dirt"
        if _surface_str(str(raw_df["track_code"].iloc[0])) != "芝"
        else "avg_go3f_rank_5_turf"
    )
    _field_size   = int(raw_df["umaban"].max()) or 16

    has_sire = "sire_id" in raw_df.columns
    has_bms  = "bms_id"  in raw_df.columns
    sire_ids = raw_df["sire_id"].dropna().astype(str).tolist() if has_sire else []
    bms_ids  = raw_df["bms_id"].dropna().astype(str).tolist()  if has_bms  else []
    name_map = _fetch_horse_name_map(list(set(sire_ids + bms_ids)))

    supps = _fetch_detail_supplements(race_id)

    # 5. レースヘッダー組み立て
    first     = raw_df.iloc[0]
    kc        = str(first.get("keibajo_code", "")).strip().zfill(2)
    race_name = str(first.get("race_name_hondai", "") or "").strip()
    if not race_name:
        race_name = f"{_si(first.get('race_num')) or '?'}R"

    grade_raw = first.get("grade_code")
    grade_str = str(grade_raw).strip() \
                if grade_raw and str(grade_raw).strip() not in ("None", "") else None

    # class_label: _compute_class_label で確定（jyoken_cd は raw_df にない場合が多いが
    # race_name フォールバックで "未勝利"等を補完できる）
    class_label_str = _compute_class_label(
        grade_str,
        str(first.get("race_type_code") or "").strip() or None,
        str(first.get("jyoken_cd_2") or "").strip() or None,
        str(first.get("jyoken_cd_3") or "").strip() or None,
        str(first.get("jyoken_cd_4") or "").strip() or None,
        str(first.get("jyoken_cd_5") or "").strip() or None,
        race_name,
    )

    # 天候・馬場（3経路）:
    #   keiba_v2 芝  : tenko_code=天候(1〜4), shiba_baba_code=馬場(1〜4)
    #   keiba_v2 ダート: tenko_code=天候(1〜4), dirt_baba_code=馬場(1〜4)
    #   jvdl フォールバック: shiba/dirt は NULL
    #     jvdl_track_condition = r.track_condition（馬場コード）→ _baba_str
    #     jvdl_weather         = r.weather（天候コード）→ _weather_str
    #     ※ keiba_v2 の tenko_code(天候) とは別カラム — SQL エイリアスで明示
    shiba = first.get("shiba_baba_code")
    dirt  = first.get("dirt_baba_code")
    if _is_valid_code(shiba):
        # keiba_v2 芝レース
        weather         = _weather_str(first.get("tenko_code"))
        track_condition = _baba_str(shiba)
    elif _is_valid_code(dirt):
        # keiba_v2 ダートレース（tenko_code=天候コード、shiba_baba_code は空/0）
        weather         = _weather_str(first.get("tenko_code"))
        track_condition = _baba_str(dirt)
    else:
        # jvdl フォールバック: shiba/dirt は NULL
        jvdl_wx = first.get("jvdl_weather")
        weather         = _weather_str(jvdl_wx) if _is_valid_code(jvdl_wx) else "—"
        track_condition = _baba_str(first.get("jvdl_track_condition"))

    # 6. 出走馬リスト組み立て（numpy 型を Python 型へ明示キャスト）
    horses_out: list[RaceDetailHorse] = []
    for i, row in raw_df.reset_index(drop=True).iterrows():
        hid    = str(row["horse_id"])
        umaban = int(row["umaban"])
        supp   = supps.get(umaban, {})
        prev   = prev_map.get(hid, {})

        # 枠番: DBの値を優先、0/null は None（フロントが「—」表示）
        wb_raw = supp.get("wakuban")
        wakuban: int | None = int(wb_raw) if (wb_raw and int(wb_raw) > 0) else None

        sire_id = str(row["sire_id"]) if has_sire and pd.notna(row.get("sire_id")) else None
        bms_id  = str(row["bms_id"])  if has_bms  and pd.notna(row.get("bms_id"))  else None

        sub = SubmodelScores(
            score_ability_v2  = float(row.get("score_ability_v2",  0.0) or 0.0),
            score_course_v2   = float(row.get("score_course_v2",   0.0) or 0.0),
            score_team_v2     = float(row.get("score_team_v2",     0.0) or 0.0),
            score_training_v2 = float(row.get("score_training_v2", 0.0) or 0.0),
            score_pace_v2     = float(row.get("score_pace_v2",     0.0) or 0.0),
            score_pedigree_v1 = float(row.get("score_pedigree_v1", 0.0) or 0.0),
        )

        # テン・上がり指数: raw_df の特徴量から計算
        ten_raw   = _sf(row.get("avg_first_corner_norm_5"))
        agari_raw = _sf(row.get(_agari_col))

        # 馬別ペースシム値（_compute_pace_prediction が成功した場合のみ）
        sim_h = sim_per_horse.get(umaban) if sim_per_horse else None

        extra = HorseExtra(
            sire_name             = name_map.get(sire_id) if sire_id else None,
            dam_sire_name         = name_map.get(bms_id)  if bms_id  else None,
            prev_race_grade       = prev.get("prev_race_grade"),
            prev_race_rank        = prev.get("prev_race_rank"),
            prev_race_days_ago    = prev.get("prev_race_days_ago"),
            chokyo_score          = _sf(row.get("chokyo_master_score")),
            past_races            = past5_map.get(hid, []),
            ten_index             = _compute_ten_index(ten_raw),
            agari_index           = _compute_agari_index(agari_raw, _field_size),
            position_tendency     = sim_h.get("position_tendency")    if sim_h else None,
            predicted_field_pace  = sim_h.get("predicted_field_pace") if sim_h else None,
            pace_harmony          = sim_h.get("pace_harmony")         if sim_h else None,
        )

        # EMP T-score を 0-1 範囲に正規化してから格納する。
        # フロントエンドの adapter が `Math.round(ai_score * 100)` で 0-100 に変換するため、
        # バックエンドは必ず 0-1 の値を返す（86.5 → 0.865 → adapter 出力 87）。
        emp_01 = round(float(emp_scores[i]) / 100.0, 4)

        horses_out.append(RaceDetailHorse(
            umaban        = umaban,
            wakuban       = wakuban,
            horse_id      = hid,
            horse_name    = row.get("horse_name") or None,
            jockey_name   = supp.get("jockey_name"),
            trainer_name  = supp.get("trainer_name"),
            horse_weight  = _si(row.get("horse_weight")),
            weight_diff   = _si(row.get("weight_diff")),
            burden_weight = float(row.get("basis_weight") or 55.0),
            tan_odds      = _sf(row.get("tan_odds")),
            ninki         = _si(row.get("ninki")),
            ai_score      = emp_01,
            ai_rank       = int(ai_ranks[i]),
            submodel_scores = sub,
            extra           = extra,
        ))

    horses_out.sort(key=lambda h: h.ai_rank)

    elapsed_s4 = time.perf_counter() - t_s4
    logger.info(
        "[Timing/compute_detail] race_id=%s horses=%d S2=%.3fs S3=%.3fs S4=%.3fs",
        race_id, len(horses_out), elapsed_s2, elapsed_s3, elapsed_s4,
    )

    # is_special: jyoken_cd の有無で JV-Data スキーマ(E=特別競走) か
    # 旧 keiba_v2 スキーマ(E=1勝クラス) かを区別する。
    _has_jyoken = any(
        (str(first.get(f) or "").strip() not in ("", "000"))
        for f in ("jyoken_cd_2", "jyoken_cd_3", "jyoken_cd_4", "jyoken_cd_5")
    )
    is_special_flag = grade_str == "E" and _has_jyoken

    return RaceDetailResponse(
        race_id         = race_id,
        race_date       = str(pd.Timestamp(race_date).date()),
        keibajo_name    = _KEIBAJO_NAME.get(kc, kc),
        race_num        = int(first["race_num"]),
        race_name       = race_name,
        distance        = int(first["distance"]),
        track_code      = str(first.get("track_code", "10")).strip().zfill(2),
        grade_code      = grade_str,
        class_label     = class_label_str,
        is_special      = is_special_flag,
        syusso_tosu     = len(horses_out),
        weather         = weather,
        track_condition = track_condition,
        race_info       = RaceInfo(
            pace_prediction = pace_label,
            bias_note       = bias_note,
            positioning_map = positioning_map,
            track_bias      = track_bias_result,
        ),
        horses      = horses_out,
        computed_at = datetime.now(timezone.utc).isoformat(),
    )


@router.get("/races/{race_id}", response_model=RaceDetailResponse)
def get_race_detail(
    race_id: str,
    _: None = Depends(rate_limit_predict),
) -> Response | RaceDetailResponse:
    """
    レース詳細を返す。
    Redis（TTL 5分）→ DB 永続キャッシュ（model_version 一致）→ live計算 の3段で処理する。
    live計算後は DB + Redis 両方に保存する。
    """
    t_total_start = time.perf_counter()
    logger.info("[V2RaceDetail] race_id=%s", race_id)
    model_ver = get_model_version()

    # ── Step1: Redis キャッシュ ──────────────────────────────────────────────
    t_s1 = time.perf_counter()
    cache_key    = f"{_CACHE_PFX}{race_id}"
    r            = get_redis_client()
    redis_status = "Offline" if r is None else "MISS"

    if r:
        try:
            cached = r.get(cache_key)
            if cached:
                elapsed_s1 = time.perf_counter() - t_s1
                logger.info(
                    "[Timing] race_id=%s Redis HIT %.3fs / TOTAL %.3fs",
                    race_id, elapsed_s1, time.perf_counter() - t_total_start,
                )
                return Response(content=cached, media_type="application/json",
                                headers={"X-Cache": "HIT"})
        except Exception as e:
            logger.warning("[Cache] Redis get失敗（処理継続）: %s", e)
            redis_status = "Error"

    elapsed_s1 = time.perf_counter() - t_s1

    # ── Step2: DB 永続キャッシュ ─────────────────────────────────────────────
    t_s2 = time.perf_counter()
    cached_detail = _get_cached_detail(race_id, model_ver)
    elapsed_s2 = time.perf_counter() - t_s2

    if cached_detail is not None:
        json_str = cached_detail.model_dump_json()
        if r:
            try:
                r.setex(cache_key, _CACHE_TTL, json_str)
            except Exception:
                pass
        logger.info(
            "[Timing] race_id=%s Redis=%s DB HIT %.3fs / TOTAL %.3fs",
            race_id, redis_status, elapsed_s2, time.perf_counter() - t_total_start,
        )
        return Response(content=json_str, media_type="application/json",
                        headers={"X-Cache": "DB-HIT"})

    # ── Step3: live 計算 ──────────────────────────────────────────────────────
    try:
        response = _compute_detail(race_id)
    except FileNotFoundError as e:
        logger.error("[V2RaceDetail] AIモデルファイル未検出: %s", e)
        raise HTTPException(status_code=503, detail="AIモデルが未ロードです。管理者に連絡してください。")
    except Exception as e:
        logger.exception("[V2RaceDetail] 計算エラー: %s", e)
        raise HTTPException(status_code=500, detail="予測処理でエラーが発生しました")

    if response is None:
        raise HTTPException(status_code=404, detail=f"レースが見つかりません: {race_id}")

    # ── キャッシュ保存（DB + Redis）──────────────────────────────────────────
    _save_detail_cache(response, model_ver)
    json_str = response.model_dump_json()
    if r:
        try:
            r.setex(cache_key, _CACHE_TTL, json_str)
            logger.info("[Cache] Redis SET race_id=%s TTL=%ds", race_id, _CACHE_TTL)
        except Exception as e:
            logger.warning("[Cache] Redis set失敗（処理継続）: %s", e)

    logger.info(
        "[Timing] race_id=%s Redis=%s DB MISS → live TOTAL %.3fs",
        race_id, redis_status, time.perf_counter() - t_total_start,
    )
    return response
