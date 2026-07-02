"""
scripts/generate_ai_picks.py
==============================
v1 × opponent_v3 アンサンブル (α=0.5) で週末AI推奨を生成する。

出力: data/output/tipster/ai_picks.json

設計方針:
  - 既存の generate_picks_report.py / conditions_v2.py は変更しない
  - 当日バイアスなし (day_front_bias_pit=0) で動作
  - PACE_V4_COLS は静的parquetではなく JVDL DB から対象馬の全確定済み過去走を
    都度ロードして計算する（opponent 特徴量と同じ設計。parquet陳腐化を構造的に防止）
  - opponent 特徴量は JVDL DB から都度ロード
"""
from __future__ import annotations

import json
import logging
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import lightgbm as lgb
import numpy as np
import pandas as pd
import psycopg2
import sqlalchemy
from sqlalchemy import bindparam, create_engine

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.config import DB_V2, DB_JVDL
from pace_bias_ai.pipeline import (
    build_layer1_features,
    LAYER1_ALL_COLS,
)
from pace_bias_ai.features.layer2 import (
    build_layer2_features,
    LAYER2_FEATURE_COLS,
    _compute_pit_te,
    _classify_surface,
    _DIST_BINS,
    _TE_ALPHA_JOCKEY,
    _TE_ALPHA_SIRE,
)
from pace_bias_ai.opponent_model.features import (
    load_all_race_history,
    build_opponent_features,
    FEATURE_COLS as OPP_FEATURE_COLS,
)
from pace_bias_ai.features.rotation_flag import build_rotation_flags, ROTATION_COLS
from pace_bias_ai.features.graded_confidence import (
    is_graded_race,
    classify_class_transition,
    class_transition_is_positive,
    is_excuse_margin_eligible,
    is_age_veteran,
)
from pace_bias_ai.features.condition_mapper import (
    ConditionMapper,
    HorseExplanation,
    FeatureExplanation,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
)
log = logging.getLogger(__name__)

# ── パス定数 ─────────────────────────────────────────────────────────────────
_V1_MODEL = _ROOT / "pace_bias_ai" / "models" / "v1_fullmodel_20250530.lgb"
_OPP_MODEL = _ROOT / "pace_bias_ai" / "models" / "opponent_v3_fullmodel_20250530.lgb"
_OUTPUT = _ROOT / "data" / "output" / "tipster" / "ai_picks.json"

_V1_FEATURES = [
    "avg_c1_norm_5", "avg_c4_norm_5", "avg_pos_advance_norm_5",
    "running_style_std_norm_5", "avg_first_corner_norm_5",
    "avg_c1_norm_5_sprint", "avg_c4_norm_5_sprint", "avg_pos_advance_norm_5_sprint",
    "avg_c1_norm_5_mile", "avg_c4_norm_5_mile", "avg_pos_advance_norm_5_mile",
    "avg_c1_norm_5_mid", "avg_c4_norm_5_mid", "avg_pos_advance_norm_5_mid",
    "avg_c1_norm_5_long", "avg_c4_norm_5_long", "avg_pos_advance_norm_5_long",
    "avg_go3f_rank_5_turf", "go3f_rank_std_5_turf",
    "avg_go3f_rank_5_dirt", "go3f_rank_std_5_dirt",
    "predicted_position_norm", "predicted_field_pace", "pace_harmony_pre",
    "versatile_type", "versatile_score", "hidden_late_speed",
    "weight_reduction_flag", "opening_week_flag",
    "distance_change", "distance_extended", "distance_shortened",
    "jockey_continuity_flag", "jockey_leading_flag",
    "venue_front_bias", "venue_inner_bias", "venue_agari_top2_rate",
    "day_front_bias_pit", "day_inner_bias_pit",
    "opening_week_prior", "prev_week_front_bias",
    "bias_position_harmony",
    "harmony_rank_norm", "pred_pos_rank_norm", "hidden_late_rank_norm",
    "harmony_vs_mean",
    "jockey_te", "sire_te", "venue_horse_te",
    "venue_changed", "surface_changed", "weight_change",
    "dist_cat", "surface_code", "field_size_norm",
]

_JST = ZoneInfo("Asia/Tokyo")
_ALPHA = 0.5


# ── DB接続 ─────────────────────────────────────────────────────────────────────

def _v2_conn():
    return psycopg2.connect(**DB_V2)


def _jvdl_conn():
    return psycopg2.connect(**DB_JVDL)


def _jvdl_engine() -> sqlalchemy.engine.Engine:
    cfg = DB_JVDL
    url = f"postgresql+psycopg2://{cfg['user']}:{cfg['password']}@{cfg['host']}:{cfg['port']}/{cfg['dbname']}"
    return create_engine(url, pool_pre_ping=True)


def _v2_engine() -> sqlalchemy.engine.Engine:
    cfg = DB_V2
    url = f"postgresql+psycopg2://{cfg['user']}:{cfg['password']}@{cfg['host']}:{cfg['port']}/{cfg['dbname']}"
    return create_engine(url, pool_pre_ping=True)


# ── 週末レース取得 ────────────────────────────────────────────────────────────

def _this_weekend() -> tuple[date, date]:
    today = pd.Timestamp.now(tz=_JST).date()
    days_to_sat = (5 - today.weekday()) % 7
    sat = today + timedelta(days=days_to_sat)
    return sat, sat + timedelta(days=1)


def _resolve_target_dates(target_dates: list[date]) -> list[date]:
    """
    指定された日付でレースが存在しない場合、直近の開催日にフォールバックする。
    v2 DB に対象日のレースがなければ最新 2 開催日を使用する。
    """
    with _v2_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM races WHERE race_date = ANY(%s)",
                ([d for d in target_dates],),
            )
            count = int(cur.fetchone()[0])

    if count > 0:
        log.info("対象日にレースあり: %s", [str(d) for d in target_dates])
        return target_dates

    log.warning(
        "対象日 %s にレースなし → 直近の開催日にフォールバック",
        [str(d) for d in target_dates],
    )
    with _v2_conn() as conn:
        with conn.cursor() as cur:
            # 今週末以前の直近 2 開催日
            cutoff = max(target_dates)
            cur.execute(
                """
                SELECT DISTINCT race_date
                FROM races
                WHERE race_date <= %s
                ORDER BY race_date DESC
                LIMIT 2
                """,
                (cutoff,),
            )
            rows = cur.fetchall()

    if not rows:
        log.error("フォールバック先の開催日も見つかりません")
        return target_dates

    fallback = sorted([r[0] for r in rows])
    log.info("フォールバック先: %s", [str(d) for d in fallback])
    return fallback


_RACE_META_COLS = (
    "id, race_date, keibajo_code, kaiji, nichiji, race_num, "
    "race_name_hondai, distance, track_code, grade_code, "
    "joken_code_youngest, syusso_tosu, data_kubun"
)


def _row_to_race_meta(row) -> dict:
    return {
        "race_id":          row[0],
        "race_date":        str(row[1]),
        "keibajo_code":     str(row[2]).zfill(2),
        "kaiji":            row[3],
        "nichiji":          row[4],
        "race_num":         row[5],
        "race_name":        row[6] or "",
        "distance":         int(row[7]) if row[7] else 1800,
        "track_code":       str(row[8]) if row[8] else "10",
        "grade_code":       str(row[9]) if row[9] else "",
        "joken_cd_youngest": str(row[10]) if row[10] else "",
        "field_size":       int(row[11]) if row[11] else 0,
        "data_kubun":       str(row[12]) if row[12] else None,
    }


def _get_upcoming_races(days: list[date]) -> list[dict]:
    """指定日のレース一覧を v2 DB から取得する。"""
    races: list[dict] = []
    with _v2_conn() as conn:
        with conn.cursor() as cur:
            for d in days:
                cur.execute(f"""
                    SELECT {_RACE_META_COLS}
                    FROM races
                    WHERE race_date = %s
                    ORDER BY keibajo_code, race_num
                """, (d,))
                races.extend(_row_to_race_meta(row) for row in cur.fetchall())
    log.info("週末レース: %d件", len(races))
    return races


def _get_race_meta_by_id(race_id: str) -> dict | None:
    """race_id を指定して1レース分のメタ情報を v2 DB から取得する（過去レースの再スコアリング向け）。"""
    with _v2_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {_RACE_META_COLS} FROM races WHERE id = %s", (race_id,))
            row = cur.fetchone()
    return _row_to_race_meta(row) if row else None


def _get_race_entries(race_id: str) -> list[dict]:
    """1レース分の出走馬を v2 DB から取得する。

    sire_id は fukurou_keiba_v2.horses ではなく JVDL 側から取得する
    （2026-07-02 発見: fukurou_keiba_v2.horses.sire_id は旧スクレイパー由来の
    文字化けデータで実質的に使い物にならない。例 "Cic" 等の断片文字列。
    JVDL 側の horses.sire_id はクリーンな値のため sire_te 等の TE 計算に
    こちらを使う）。
    """
    with _v2_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT e.race_id, e.umaban, e.horse_id, e.horse_name,
                       e.basis_weight, e.jockey_cd, e.age AS horse_age,
                       e.horse_weight, e.trainer_cd
                FROM race_entries e
                WHERE e.race_id = %s
                ORDER BY e.umaban
            """, (race_id,))
            rows = cur.fetchall()

    horse_ids = [str(r[2]) for r in rows if r[2]]
    sire_map: dict[str, str] = {}
    if horse_ids:
        with _jvdl_conn() as jconn:
            with jconn.cursor() as jcur:
                jcur.execute(
                    "SELECT id, sire_id FROM horses WHERE id = ANY(%s)",
                    (horse_ids,),
                )
                sire_map = {r[0]: r[1] for r in jcur.fetchall() if r[1]}

    entries = []
    for r in rows:
        horse_id_str = str(r[2]) if r[2] else ""
        entries.append({
            "race_id":    r[0],
            "umaban":     int(r[1]) if r[1] else 0,
            "horse_id":   horse_id_str,
            "horse_name": str(r[3]) if r[3] else "",
            "basis_weight": float(r[4]) if r[4] else 55.0,
            "jockey_cd":  str(r[5]) if r[5] else "",
            "horse_age":  int(r[6]) if r[6] else 3,
            "horse_weight": float(r[7]) if r[7] else 0.0,
            "trainer_cd": str(r[8]) if r[8] else "",
            "sire_id":    str(sire_map.get(horse_id_str, "")),
        })
    return entries


# ── 履歴データ ─────────────────────────────────────────────────────────────────

# PACE_V4 / layer1_horse の計算に必要な最小列（両モジュールの必須カラムの和集合）
_PACE_HIST_COLS: list[str] = [
    "horse_id", "race_id", "race_date", "umaban",
    "corner_1", "corner_4", "kakutei_chakujun", "go_3f_time",
    "distance", "track_code", "jockey_cd", "field_size",
]


def _empty_pace_hist() -> pd.DataFrame:
    df = pd.DataFrame(columns=_PACE_HIST_COLS)
    for col in ["umaban", "corner_1", "corner_4", "kakutei_chakujun", "go_3f_time", "distance", "field_size"]:
        df[col] = df[col].astype(float)
    df["race_date"] = pd.to_datetime(df["race_date"])
    return df


def _load_pace_v4_history(
    engine: sqlalchemy.engine.Engine,
    horse_ids: list[str],
    before_date: str,
) -> pd.DataFrame:
    """対象馬の全確定済み過去走を JVDL DB から都度ロードする（parquet非依存）。

    PACE_V4_COLS（脚質特徴量）・layer1_horse 特徴量の計算に必要な列のみを取得する。
    静的parquetのように再生成を忘れると陳腐化する問題が構造的に起きない
    （opponent_model.features.load_all_race_history と同じ設計思想）。

    Args:
        engine:      JVDL DB (fukurou_jvdl) の SQLAlchemy engine
        horse_ids:   対象レースの出走馬ID (blood_no) リスト
        before_date: この日付 (YYYYMMDD文字列) より前の確定済みレースのみ取得
                     （当日・未来のレースを混入させないための PIT ガード）

    Note (2026-07-02 発見・修正):
        r.shusso_tosu（真の出走頭数）を同梱する。対象馬「自身」の過去走しか
        読まないため、過去走の同一レースに対象馬グループの他の馬が
        たまたま同時出走していない限り、_compute_v1_features() 側の
        `groupby('race_id')['umaban'].transform('max')` による field_size
        推定が「その過去レースに実際に登場する対象馬の中の最大umaban」
        （= 真の出走頭数よりずっと小さい）になってしまい、avg_c4_norm_5 等
        の正規化値が 1.0 を超える異常値になるバグがあった
        （例: 真の16頭立てが umaban=1 の馬しか combined 内に無いために
        field_size=1 相当になり、c4_norm=(8-1)/(1-1).clip(1)=7 に破綻）。
    """
    if not horse_ids:
        return _empty_pace_hist()

    sql = sqlalchemy.text("""
        SELECT e.blood_no AS horse_id, e.race_id, e.umaban,
               e.corner_1, e.corner_4, e.kakutei_chakujun,
               e.kohan_3f AS go_3f_time, e.kishu_code AS jockey_cd,
               r.distance, r.track_code, r.shusso_tosu AS field_size
        FROM race_entries_v2 e
        JOIN races_v2 r ON r.race_id = e.race_id
        WHERE e.blood_no IN :horse_ids
          AND LEFT(e.race_id, 8) < :before_date
          AND e.kakutei_chakujun IS NOT NULL
        ORDER BY e.blood_no, e.race_id
    """).bindparams(bindparam("horse_ids", expanding=True))

    with engine.connect() as conn:
        df = pd.read_sql(
            sql, conn,
            params={"horse_ids": list(horse_ids), "before_date": before_date},
        )

    # 該当馬が全頭デビュー前（新馬戦等）で 0 行になるケースを含め、
    # pandas が空の read_sql 結果を object dtype で返すことがある。
    # 型を明示的に固定しないと pd.concat 時に非空側まで object 化してしまうため矯正する。
    df["horse_id"]  = df["horse_id"].astype(str)
    df["race_id"]   = df["race_id"].astype(str)
    df["jockey_cd"] = df["jockey_cd"].astype(str)
    df["track_code"] = df["track_code"].astype(str)
    for col in ["umaban", "corner_1", "corner_4", "kakutei_chakujun", "go_3f_time", "distance", "field_size"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["race_date"] = pd.to_datetime(df["race_id"].str[:8], format="%Y%m%d", errors="coerce")
    return df


# ── 重賞用confidence判定 補助データ取得 ────────────────────────────────────────
# pace_bias_ai/features/graded_confidence.py の分岐が grade_code in (A/B/C/L/E)
# のレースでのみ必要とする追加データ。通常レースでは呼び出されない
# （score_race_ai 内で is_graded の場合のみ取得する）。

_TIME_DIFF_RE = re.compile(r"^[+-]\d+$")


def _fetch_prev_race_excuse_info(
    horse_ids: list[str],
    before_date: str,
) -> dict[str, dict]:
    """対象馬の直前確定レースの grade_code と着差(秒)を取得する（度外視判定用）。

    fukurou_keiba_v2 (races/race_entries) を参照する。time_diff は
    符号付き3桁整数文字列(0.1秒単位、例 "+024"=2.4秒, "-000"=0.0秒)。

    Returns: {horse_id: {"grade_code": str|None, "margin_sec": float|None}}
    """
    if not horse_ids:
        return {}
    sql = sqlalchemy.text("""
        SELECT DISTINCT ON (e.horse_id)
            e.horse_id, r.grade_code, e.time_diff
        FROM race_entries e
        JOIN races r ON r.id = e.race_id
        WHERE e.horse_id IN :horse_ids
          AND r.race_date < :before_date
          AND e.kakutei_chakujun IS NOT NULL AND e.kakutei_chakujun > 0
        ORDER BY e.horse_id, r.race_date DESC, r.id DESC
    """).bindparams(bindparam("horse_ids", expanding=True))

    with _v2_engine().connect() as conn:
        rows = conn.execute(sql, {"horse_ids": list(horse_ids), "before_date": before_date}).fetchall()

    result: dict[str, dict] = {}
    for hid, grade_code, time_diff in rows:
        margin_sec = None
        td = (time_diff or "").strip()
        if _TIME_DIFF_RE.match(td):
            margin_sec = abs(int(td)) / 10.0
        result[str(hid)] = {"grade_code": grade_code, "margin_sec": margin_sec}
    return result


def _fetch_training_condition1_flags(
    engine: sqlalchemy.engine.Engine,
    blood_nos: list[str],
    race_date: str,
) -> dict[str, bool]:
    """対象馬が tipster/training_ranker.py の条件①（坂路ラスト1F≤11.9秒かつ
    全区間加速ラップ）に該当するかを判定する（調教①該当フラグ、重賞用confidence用）。

    generate_picks_report.py::_fetch_training_for_race と同一パターン
    （training_slope/training_wood を都度クエリしrank_horses_by_trainingへ）。

    Returns: {blood_no: bool}
    """
    from tipster.training_ranker import SlopeRow, WoodRow, load_config as _load_tr_config, rank_horses_by_training

    if not blood_nos:
        return {}
    d = pd.Timestamp(race_date[:4] + "-" + race_date[4:6] + "-" + race_date[6:8])
    since = (d - pd.Timedelta(days=30)).strftime("%Y%m%d")

    slope_by: dict[str, list] = {bn: [] for bn in blood_nos}
    wood_by: dict[str, list] = {bn: [] for bn in blood_nos}

    with engine.connect() as conn:
        for r in conn.execute(
            sqlalchemy.text(
                "SELECT blood_no, chokyo_date, chokyo_time, center_cd,"
                " time_4f, lap_l4_l3, lap_l3_l2, lap_l2_l1, lap_l1"
                " FROM training_slope"
                " WHERE blood_no IN :bns AND chokyo_date >= :since AND chokyo_date <= :until"
            ).bindparams(bindparam("bns", expanding=True)),
            {"bns": list(blood_nos), "since": since, "until": race_date},
        ).fetchall():
            slope_by.setdefault(r[0], []).append(
                SlopeRow(blood_no=r[0], chokyo_date=r[1], chokyo_time=r[2],
                         center_cd=r[3], time_4f=r[4], lap_l4_l3=r[5],
                         lap_l3_l2=r[6], lap_l2_l1=r[7], lap_l1=r[8])
            )
        for r in conn.execute(
            sqlalchemy.text(
                "SELECT blood_no, chokyo_date, chokyo_time, time_5f, lap_l2_l1, lap_l1"
                " FROM training_wood"
                " WHERE blood_no IN :bns AND chokyo_date >= :since AND chokyo_date <= :until"
            ).bindparams(bindparam("bns", expanding=True)),
            {"bns": list(blood_nos), "since": since, "until": race_date},
        ).fetchall():
            wood_by.setdefault(r[0], []).append(
                WoodRow(blood_no=r[0], chokyo_date=r[1], chokyo_time=r[2],
                        time_5f=r[3], lap_l2_l1=r[4], lap_l1=r[5])
            )

    try:
        config = _load_tr_config()
        ranked = rank_horses_by_training(
            blood_nos=blood_nos,
            slope_rows_by_horse=slope_by,
            wood_rows_by_horse=wood_by,
            race_date=race_date,
            config=config,
        )
    except Exception:
        log.exception("調教①判定失敗（重賞用confidence）")
        return {}

    return {r.blood_no: (r.condition_label == "①") for r in ranked}


def _load_jockey_recent_wins(
    engine: sqlalchemy.engine.Engine,
    jockey_codes: list[str],
    before_race_id: str,
) -> dict[str, tuple[float, float]]:
    """騎手の（年間勝利数, 通算勝利数）を PIT-safe に DB から集計する。

    layer1_horse.py の create_layer1_horse_features() は
    jockey_yr_wins/jockey_career_wins 列が無い場合、_compute_jockey_pit_wins()
    で「渡された DataFrame 内の過去走のみ」から代替集計する。だが
    _load_pace_v4_history() は「対象レースの出走馬自身の過去走」しか読まないため、
    その騎手が別の馬に騎乗した分の勝利が一切カウントされず、
    jockey_leading_flag（年間50勝以上）がほぼ常に 0 になるバグがあった
    （2026-07-02 メインレース検証で発見）。本関数で騎手コード単位に正しく
    集計し、_build_pred_row() で明示的に渡すことで代替集計を回避する。

    Args:
        jockey_codes:    対象レースの騎手コードリスト
        before_race_id:  この race_id より前の確定済みレースのみ集計（PITガード）
    Returns:
        {jockey_cd: (yr_wins, career_wins)}
        yr_wins:     今走と同じ暦年内の、今走より前の勝利数
        career_wins: 通算（全期間）の、今走より前の勝利数
    """
    if not jockey_codes:
        return {}
    cur_year = before_race_id[:4]

    sql = sqlalchemy.text("""
        SELECT kishu_code,
               COUNT(*) FILTER (
                   WHERE kakutei_chakujun = 1 AND LEFT(race_id, 4) = :cur_year
               ) AS yr_wins,
               COUNT(*) FILTER (WHERE kakutei_chakujun = 1) AS career_wins
        FROM race_entries_v2
        WHERE kishu_code IN :jockey_codes
          AND race_id < :before_race_id
          AND kakutei_chakujun IS NOT NULL
        GROUP BY kishu_code
    """).bindparams(bindparam("jockey_codes", expanding=True))

    with engine.connect() as conn:
        rows = conn.execute(
            sql,
            {"jockey_codes": list(set(jockey_codes)), "before_race_id": before_race_id, "cur_year": cur_year},
        ).fetchall()

    return {r.kishu_code: (float(r.yr_wins), float(r.career_wins)) for r in rows}


# ── jockey_te / sire_te（Layer2 target encoding）の母集団補完 ─────────────────
# _compute_pit_te() の cumsum 母集団に含める過去走の下限（メインレース検証
# バッチ（validate_main_races.py 相当）と同じ 2018-01-01 を採用し、
# global_rate（ベイズスムージングの中立値）の再現性を揃える）。
_TE_HIST_START = "20180101"
_TE_JRA_CODES  = [f"{i:02d}" for i in range(1, 11)]


def _load_te_population_history(engine: sqlalchemy.engine.Engine) -> pd.DataFrame:
    """jockey_te / sire_te の PIT-safe target encoding に必要な母集団データを
    DB から一括ロードする（generate_ai_picks() / update_ai_tipster_results の
    実行あたり1回のみ。opponent_model.features.load_all_race_history() と同じ
    「1回ロードして全レースで使い回す」設計）。

    build_layer2_features() 内の _compute_pit_te() は「渡された DataFrame 内」
    でしか cumsum 集計できない。だが _load_pace_v4_history() は対象レースの
    出走馬「自身」の過去走しか読まないため、score_race_ai() をそのまま呼ぶと
    その騎手/種牡馬が他の馬に騎乗・産駒として関わった実績が一切拾えず、
    jockey_te/sire_te が極端に薄いサンプルから計算されてしまう
    （2026-07-02 メインレース検証で発見。例: 通算2,204騎乗のベテラン騎手が
    対象馬自身の過去走ではわずか数走分しか登場しない）。

    本関数は JRA・2018年以降の全確定レースを母集団としてロードする
    （メインレース検証バッチと同一範囲）。_compute_te_for_pred_rows() で
    _compute_pit_te() に正しい母集団を渡せるようにする
    （検証スクリプトと同一関数 _compute_pit_te を再利用）。
    """
    sql = sqlalchemy.text("""
        SELECT e.blood_no AS horse_id, e.race_id, e.kishu_code AS jockey_cd,
               e.kakutei_chakujun, r.distance, r.track_code, h.sire_id
        FROM race_entries_v2 e
        JOIN races_v2 r ON r.race_id = e.race_id
        LEFT JOIN horses h ON h.id = e.blood_no
        WHERE LEFT(e.race_id, 8) >= :hist_start
          AND e.kakutei_chakujun IS NOT NULL
          AND r.keibajo_code = ANY(:jra_codes)
    """)

    with engine.connect() as conn:
        df = pd.read_sql(
            sql, conn,
            params={"hist_start": _TE_HIST_START, "jra_codes": _TE_JRA_CODES},
        )

    df["horse_id"]  = df["horse_id"].astype(str)
    df["race_id"]   = df["race_id"].astype(str)
    df["jockey_cd"] = df["jockey_cd"].astype(str)
    df["sire_id"]   = df["sire_id"].astype(str)
    df["distance"]   = pd.to_numeric(df["distance"], errors="coerce")
    df["kakutei_chakujun"] = pd.to_numeric(df["kakutei_chakujun"], errors="coerce")
    df["race_date"] = pd.to_datetime(df["race_id"].str[:8], format="%Y%m%d", errors="coerce")
    df["dist_cat"] = pd.cut(
        df["distance"], bins=_DIST_BINS, labels=False, right=True
    ).astype(float)
    df["surface_code"] = _classify_surface(df["track_code"])
    rank_num = df["kakutei_chakujun"]
    df["_placed3"] = (rank_num <= 3).where(rank_num.notna()).astype(float)
    return df


def _compute_te_for_pred_rows(
    pred_rows: list[dict],
    te_population: pd.DataFrame,
) -> pd.DataFrame:
    """jockey_te / sire_te を、検証バッチと同一の _compute_pit_te() を用いて
    正しい母集団（_load_te_population_history() の結果）付きで計算する。

    build_layer2_features() が内部で計算する jockey_te/sire_te（narrow-history）
    は上書きする前提。venue_horse_te は対象馬自身の過去走で完結するため対象外
    （_load_pace_v4_history() の hist_df で従来通り正しく計算される）。

    Returns:
        pred_rows と同じ順序の DataFrame（horse_id, jockey_te, sire_te 列）
    """
    te_context = te_population
    df_pred = pd.DataFrame(pred_rows)
    n_pred = len(df_pred)
    if n_pred == 0:
        return pd.DataFrame(columns=["horse_id", "jockey_te", "sire_te"])

    pred_slim = pd.DataFrame({
        "horse_id":     df_pred["horse_id"].astype(str),
        "race_id":      df_pred["race_id"].astype(str),
        "race_date":    pd.to_datetime(df_pred["race_date"]),
        "jockey_cd":    df_pred["jockey_cd"].astype(str),
        "sire_id":      df_pred["sire_id"].astype(str),
        "dist_cat":     pd.cut(
            pd.to_numeric(df_pred["distance"], errors="coerce"),
            bins=_DIST_BINS, labels=False, right=True,
        ).astype(float),
        "surface_code": _classify_surface(df_pred["track_code"]),
        "_placed3":     np.nan,  # 今走の結果は未確定（PITガード、shift(1)には影響しない）
    })

    combined = pd.concat([te_context, pred_slim], ignore_index=True)
    pred_index = combined.index[-n_pred:]

    jockey_te = _compute_pit_te(combined, ["jockey_cd", "dist_cat", "surface_code"], "_placed3", _TE_ALPHA_JOCKEY)
    sire_te   = _compute_pit_te(combined, ["sire_id", "dist_cat", "surface_code"], "_placed3", _TE_ALPHA_SIRE)

    return pd.DataFrame({
        "horse_id":  pred_slim["horse_id"].values,
        "jockey_te": jockey_te.loc[pred_index].values,
        "sire_te":   sire_te.loc[pred_index].values,
    })


# ── 予測行の構築 ──────────────────────────────────────────────────────────────

def _build_pred_row(
    entry: dict,
    race_meta: dict,
    jockey_wins: dict[str, tuple[float, float]] | None = None,
) -> dict:
    """1馬の予測行を構築する。

    PACE_V4_COLS / layer1_horse 系特徴量は、この予測行と _load_pace_v4_history()
    で取得した過去走を結合した後に create_pace_features_v4() 側で shift(1)+rolling
    により都度再計算されるため、ここでは持たせない（結果列は NaN のまま渡す）。
    """
    horse_id = entry["horse_id"]

    base: dict[str, Any] = {}

    # レース固有フィールド
    race_date_str = race_meta["race_date"]
    base.update({
        "race_id":       race_meta["race_id"],
        "race_date":     pd.Timestamp(race_date_str),
        "keibajo_code":  race_meta["keibajo_code"],
        "distance":      race_meta["distance"],
        "track_code":    race_meta["track_code"],
        "grade_code":    race_meta["grade_code"],
        "course_kubun":  "",
        "umaban":        entry["umaban"],
        "horse_id":      horse_id,
        "horse_name":    entry["horse_name"],
        "basis_weight":  entry["basis_weight"],
        "kinryo":        entry["basis_weight"] * 10,
        "jockey_cd":     entry["jockey_cd"],
        "horse_age":     entry["horse_age"],
        "horse_weight":  entry["horse_weight"],
        "trainer_cd":    entry["trainer_cd"],
        "sire_id":       entry["sire_id"],
        # 出走頭数（races.syusso_tosu）。umaban(枠番)確定前でも出馬投票時点で
        # 既に確定しているため、umaban=0 のフォールバックより優先して使う
        # （_compute_v1_features 側で field_size 列に反映）。
        "field_size_meta": race_meta.get("field_size") or np.nan,
        # 騎手の年間/通算勝利数（PIT-safe、_load_jockey_recent_wins() で別途集計）。
        # 明示的に渡すことで layer1_horse.py 側の PIT フォールバック集計
        # （＝この馬自身の過去走だけからの過小集計）を回避する。
        "jockey_yr_wins":     (jockey_wins or {}).get(entry["jockey_cd"], (np.nan, np.nan))[0],
        "jockey_career_wins": (jockey_wins or {}).get(entry["jockey_cd"], (np.nan, np.nan))[1],
        # 開催日次（layer1_horse.py の opening_week_flag 用）。
        # _load_pace_v4_history() 側は過去走側で使わないため取得していないが、
        # 予測行（今走）は race_meta（_row_to_race_meta の "nichiji"）から取れる。
        # 未設定だと opening_week_flag が常に0になる（2026-07-02 発見）。
        "kaisai_nichime": race_meta.get("nichiji"),
        # 結果列は NULL (未来のレース)
        "kakutei_chakujun": np.nan,
        "corner_1":         np.nan,
        "corner_2":         np.nan,
        "corner_3":         np.nan,
        "corner_4":         np.nan,
        "go_3f_time":       np.nan,
        "go_4f_time":       np.nan,
        "race_time":        np.nan,
    })
    return base


# ── v1 モデル特徴量の計算 ─────────────────────────────────────────────────────

def _compute_v1_features(
    pred_rows: list[dict],
    hist_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    pred_rows と hist_df を結合して v1 全特徴量を計算する。

    Returns:
        pred_rows のインデックスに対応した v1 特徴量 DataFrame
    """
    df_pred = pd.DataFrame(pred_rows)
    if df_pred.empty:
        return pd.DataFrame()

    # 結合: 履歴行 + 予測行
    combined = pd.concat([hist_df, df_pred], ignore_index=True)
    # race_date が datetime.date / Timestamp 混在で sort できないため統一する
    combined["race_date"] = pd.to_datetime(combined["race_date"])
    combined = combined.sort_values(["horse_id", "race_id", "umaban"]).reset_index(drop=True)

    # field_size:
    #   過去走 (hist_df 由来の行)     → races.shusso_tosu（"field_size" 列）を必ず使う。
    #   予測対象レース (df_pred 由来の行) → umaban(確定済み)の最大値を最優先、
    #                                  未確定(0)の場合のみ races.syusso_tosu
    #                                  （"field_size_meta" 列）で補完。
    #
    # 2026-07-02 発見・修正（過去走側）: 以前は umaban(確定済み)の「combined内
    # での」最大値から算出していたが、hist_df は対象馬「自身」の過去走しか
    # 含まないため、その過去レースに対象馬グループの他の馬がたまたま同時
    # 出走していない限り「その過去レースに実際に登場する対象馬の中の最大
    # umaban」（真の出走頭数よりずっと小さい）になってしまい、avg_c4_norm_5
    # 等の正規化値が 1.0 を超える異常値になるバグがあった（例: 真の16頭立て
    # で対象馬の umaban=1 しか combined 内に無いため field_size=1 相当になり、
    # c4_norm=(8-1)/(1-1).clip(1)=7 に破綻）。
    #
    # 既存修正（予測対象レース側）: umaban(枠番)が未確定だと 0 になり出走頭数
    # を著しく過小評価する（16頭立てが2頭立て相当に crush）問題への対応
    # （field_size_meta フォールバック）。ここでは umaban 確定済みなら
    # 実測値を優先する既存の挙動を維持する。
    if "field_size" in combined.columns:
        hist_field_size = pd.to_numeric(combined["field_size"], errors="coerce")
    else:
        hist_field_size = pd.Series(np.nan, index=combined.index)

    umaban_max = pd.to_numeric(
        combined.groupby("race_id")["umaban"].transform("max"), errors="coerce"
    )
    pred_field_size = umaban_max.copy()
    if "field_size_meta" in combined.columns:
        meta_fs = pd.to_numeric(combined["field_size_meta"], errors="coerce")
        needs_meta = pred_field_size.fillna(0) <= 0
        pred_field_size = pred_field_size.where(~needs_meta, meta_fs)

    # hist_df 由来の行（"field_size" 実測値あり）はそちらを優先し、
    # 無い行（= df_pred 由来、または shusso_tosu 欠損の古いデータ）は
    # pred_field_size（umaban最大値 or メタ補完）にフォールバックする。
    combined["field_size"] = hist_field_size.fillna(pred_field_size)

    # avg_rank_3: 直近3走の平均確定着順（shift(1)+rolling(3)、PIT-safe）。
    # _compute_confidence()「2走前3着以内」近似条件（+1点）で参照する。
    # 旧 archive/v2_ensemble/src/features/ability_features_v3.py の avg_rank_3
    # と同一定義（parquet除去 [2026-07-01] 後に本カラムが未生成となり、
    # 当該加点条件が常にNaNで無効化されていたための復元）。
    # combined は既に (horse_id, race_id) でソート済みかつ予測行の
    # kakutei_chakujun=NaN のため、shift(1) が当走を含むことはない。
    rank_valid = combined["kakutei_chakujun"].where(
        combined["kakutei_chakujun"].notna() & (combined["kakutei_chakujun"] > 0)
    )
    combined["avg_rank_3"] = (
        rank_valid.groupby(combined["horse_id"])
        .transform(lambda x: x.shift(1).rolling(3, min_periods=1).mean())
    )

    # LAYER1 + LAYER2 を実行
    # hist_df は生の過去走データのみ (PACE_V4_COLS 等は未計算)。
    # build_layer1_features 内の create_pace_features_v4 が horse_id 単位で
    # shift(1)+rolling(5) により都度算出する。予測行は kakutei_chakujun=NaN のため
    # 当走が rolling window に混入することはない。
    try:
        combined = build_layer1_features(combined)
    except Exception:
        log.exception("build_layer1_features 失敗")
        for col in LAYER1_ALL_COLS:
            if col not in combined.columns:
                combined[col] = np.nan

    try:
        combined = build_layer2_features(combined)
    except Exception:
        log.exception("build_layer2_features 失敗")
        for col in LAYER2_FEATURE_COLS:
            if col not in combined.columns:
                combined[col] = np.nan

    # 予測行のみ抽出（race_id で特定）
    pred_race_ids = {row["race_id"] for row in pred_rows}
    df_out = combined[combined["race_id"].isin(pred_race_ids)].copy()
    return df_out


# ── v1 予測 ────────────────────────────────────────────────────────────────────

def _predict_v1(df: pd.DataFrame, model: lgb.Booster) -> pd.Series:
    """v1 モデルで予測して Series を返す（index=df.index）。"""
    X = df.reindex(columns=_V1_FEATURES)
    X = X.fillna(X.median())
    scores = model.predict(X)
    return pd.Series(scores, index=df.index)


# ── opponent 特徴量・予測 ─────────────────────────────────────────────────────

def _build_opponent_target(race_meta: dict, entries: list[dict]) -> pd.DataFrame:
    """opponent_v3 用の df_target を構築する。"""
    rows = []
    for e in entries:
        rows.append({
            "horse_id":      e["horse_id"],
            "race_id":       race_meta["race_id"],
            "kinryo":        e["basis_weight"] * 10,
            "horse_age":     e["horse_age"],
            "distance":      race_meta["distance"],
            "track_code":    race_meta["track_code"],
            "keibajo_code":  race_meta["keibajo_code"],
            "grade_code":    race_meta["grade_code"],
            "joken_cd_youngest": race_meta["joken_cd_youngest"],
        })
    return pd.DataFrame(rows)


def _augment_entries_for_opponent(
    df_entries: pd.DataFrame,
    df_target: pd.DataFrame,
) -> pd.DataFrame:
    """
    upcoming race の行を df_entries に追加する。
    既に df_entries にある (blood_no, race_id) はスキップ（フォールバック時の重複防止）。
    """
    existing_pairs = set(zip(
        df_entries["blood_no"].astype(str),
        df_entries["race_id"].astype(str),
    ))
    aug_rows = []
    for _, row in df_target.iterrows():
        key = (str(row["horse_id"]), str(row["race_id"]))
        if key in existing_pairs:
            continue
        aug_rows.append({
            "blood_no":           str(row["horse_id"]),
            "race_id":            str(row["race_id"]),
            "kakutei_chakujun":   None,
            "race_time":          None,
            "kinryo":             row.get("kinryo"),
            "horse_age":          row.get("horse_age"),
            "horse_weight":       None,
            "umaban":             None,
        })
    if not aug_rows:
        return df_entries
    df_aug = pd.DataFrame(aug_rows)
    return pd.concat([df_entries, df_aug], ignore_index=True)


def _augment_races_for_opponent(
    df_races: pd.DataFrame,
    race_meta: dict,
) -> pd.DataFrame:
    """
    upcoming race のメタデータを df_races に追加する。
    既に df_races にある race_id はスキップ（フォールバック時の重複防止）。
    """
    if str(race_meta["race_id"]) in df_races["race_id"].astype(str).values:
        return df_races
    from pace_bias_ai.opponent_model.features import _vec_class_rank
    new_row = pd.DataFrame([{
        "race_id":            race_meta["race_id"],
        "grade_code":         race_meta["grade_code"],
        "jyoken_cd_youngest": race_meta["joken_cd_youngest"],
        "distance":           race_meta["distance"],
        "track_code":         race_meta["track_code"],
        "keibajo_code":       race_meta["keibajo_code"],
    }])
    new_row["class_rank"] = _vec_class_rank(
        new_row["grade_code"].fillna(""),
        new_row["jyoken_cd_youngest"].fillna(""),
    )
    return pd.concat([df_races, new_row], ignore_index=True)


def _predict_opponent(
    df_target: pd.DataFrame,
    df_entries: pd.DataFrame,
    df_races: pd.DataFrame,
    model: lgb.Booster,
) -> pd.Series:
    """opponent_v3 モデルで予測して Series を返す。"""
    df_feat = build_opponent_features(df_target, df_entries, df_races)
    X = df_feat.reindex(columns=OPP_FEATURE_COLS)
    # object 型カラムを数値に強制変換してから NaN 補完する
    X = X.apply(pd.to_numeric, errors="coerce")
    X = X.fillna(X.median())
    scores = model.predict(X)
    return pd.Series(scores, index=df_target.index)


# ── アンサンブル ──────────────────────────────────────────────────────────────

def _minmax_within_race(scores: pd.Series) -> pd.Series:
    """レース内 min-max 正規化（0-1）。全馬同スコアの場合は 0.5 を返す。"""
    lo, hi = scores.min(), scores.max()
    if hi > lo:
        return (scores - lo) / (hi - lo)
    return pd.Series(0.5, index=scores.index)


def _blend_normalized(
    v1_scores: pd.Series,
    opp_scores: pd.Series,
    alpha: float = _ALPHA,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    検証と同じアンサンブル: per-race min-max 正規化 → α×v1_norm + (1-α)×opp_norm。

    Returns: (v1_norm, opp_norm, blend)  ← すべて 0-1 範囲
    """
    v1_filled  = v1_scores.fillna(v1_scores.median())
    opp_filled = opp_scores.fillna(opp_scores.median())
    v1_norm    = _minmax_within_race(v1_filled)
    opp_norm   = _minmax_within_race(opp_filled)
    blend      = alpha * v1_norm + (1 - alpha) * opp_norm
    return v1_norm, opp_norm, blend


def _to_deviation(raw: pd.Series) -> pd.Series:
    """レース内偏差値（平均50、標準偏差10）を計算する。"""
    mu = raw.mean()
    sigma = raw.std(ddof=0)
    if sigma > 0:
        return ((raw - mu) / sigma * 10 + 50).round(1)
    return pd.Series(50.0, index=raw.index)


# ── 自信度計算 ────────────────────────────────────────────────────────────────

def _compute_confidence(
    flags: dict,
    race_meta: dict,
    v1_row: pd.Series,
    is_graded: bool = False,
    graded_extra: dict | None = None,
) -> tuple[int, str]:
    """
    自信度スコアを計算する。

    grade_code が A/B/C/L/E（重賞・OP・L）のレースは is_graded=True で
    呼ばれ、_compute_confidence_graded に分岐する（重賞用confidence判定、
    docs/validation/GRADED_CONFIDENCE_ANALYSIS.md で検証済み）。
    通常レース（is_graded=False）の判定ロジックは以下の通り変更なし。

    加点条件:
      +1 得意セグメント（中距離1600m超 or 長距離 or 東京/函館）
      +1 本気ローテ（is_genuine == 1）
      +1 近走好成績（avg_rank_3 ≤ 3.5 を "2走前3着以内" の近似として使用）
      +1 ネガ条件ゼロ（is_step / won_and_classup / transport_flag 全て非該当）

    減点条件:
      -1 ネガ条件ごと（is_step / won_and_classup / transport_flag）

    ラベル: A(3以上) / B(1〜2) / C(0以下)
    """
    if is_graded:
        return _compute_confidence_graded(flags, race_meta, v1_row, graded_extra or {})

    score = 0

    # 得意セグメント判定（レース条件ベース）
    dist  = int(race_meta.get("distance", 0))
    venue = str(race_meta.get("keibajo_code", ""))
    if dist > 1600 or venue in ("05", "02"):
        score += 1

    # 本気ローテ
    if flags.get("is_genuine") == 1:
        score += 1

    # 近走好成績（avg_rank_3 ≤ 3.5 を2走前3着以内の近似として使用）
    avg_r = v1_row.get("avg_rank_3", np.nan)
    if not pd.isna(avg_r) and float(avg_r) <= 3.5:
        score += 1

    # ネガ条件
    neg_flags = ("is_step", "won_and_classup", "transport_flag")
    neg_hits = sum(1 for f in neg_flags if flags.get(f) == 1)
    if neg_hits == 0:
        score += 1
    else:
        score -= neg_hits

    label = "A" if score >= 3 else "B" if score >= 1 else "C"
    return score, label


def _compute_confidence_graded(
    flags: dict,
    race_meta: dict,
    v1_row: pd.Series,
    graded_extra: dict,
) -> tuple[int, str]:
    """重賞（grade_code in A/B/C/L/E）専用の自信度計算。

    通常レース用 _compute_confidence との差分（docs/validation/
    GRADED_CONFIDENCE_ANALYSIS.md で検証済み、学習期間2022-01〜2025-05・
    グレード+OP/L N=4,092）:

    無効化する条件（重賞では効かないと実証済み）:
      is_step（重賞の計画的休養を誤検知するため）
      is_genuine
      long_rest（休み明け3ヶ月以上は重賞では26.3%と最高、ネガ扱い禁止。
                 is_stepの判定条件の一部のためis_step無効化で自動的に無効化）
      transport_flag（再計測で-2〜3ptのみ、無効化）

    据え置き（通常レースと同じ）:
      +1 得意セグメント
      +1 近走好成績
      won_and_classup によるネガ判定・ネガ条件ゼロ加点

    追加する条件（重賞専用）:
      クラス移動: 格下げ/同格ローテ=+1（29.9%/26.5%）、
                  格上挑戦・条件戦からの挑戦=-1（18.7%/15.9%）
      +1 調教①該当（+5.4pt）
      +1 度外視（前走G1/G2 かつ 着差0.5秒以内、32.4%）
      -1 高齢（7歳以上、重賞でも有効）

    ラベル: A(3以上) / B(1〜2) / C(0以下)（通常レースと同一閾値）
    """
    graded_extra = graded_extra or {}
    score = 0

    # 得意セグメント（据え置き）
    dist  = int(race_meta.get("distance", 0))
    venue = str(race_meta.get("keibajo_code", ""))
    if dist > 1600 or venue in ("05", "02"):
        score += 1

    # 近走好成績（据え置き）
    avg_r = v1_row.get("avg_rank_3", np.nan)
    if not pd.isna(avg_r) and float(avg_r) <= 3.5:
        score += 1

    # ネガ条件: won_and_classup のみ判定（is_step/transport_flag は重賞で無効化）
    if flags.get("won_and_classup") == 1:
        score -= 1
    else:
        score += 1

    # クラス移動
    transition = classify_class_transition(
        flags.get("class_vs_best"), flags.get("best_class_rank")
    )
    transition_positive = class_transition_is_positive(transition)
    if transition_positive is True:
        score += 1
    elif transition_positive is False:
        score -= 1

    # 調教①該当
    if graded_extra.get("training_condition1"):
        score += 1

    # 度外視（前走G1/G2 かつ 着差0.5秒以内）
    if graded_extra.get("excuse_margin"):
        score += 1

    # 高齢（7歳以上）
    if graded_extra.get("age_veteran"):
        score -= 1

    label = "A" if score >= 3 else "B" if score >= 1 else "C"
    return score, label


# ── 全馬スコアリング ──────────────────────────────────────────────────────────

def _score_all_horses(
    norm_blend: pd.Series,
    deviation: pd.Series,
    v1_norm: pd.Series,
    opp_norm: pd.Series,
    flags_df: pd.DataFrame,
    entries: list[dict],
    v1_df: pd.DataFrame,
    opp_df: pd.DataFrame,
    race_meta: dict,
    is_graded: bool = False,
    graded_extra_by_horse: dict[str, dict] | None = None,
) -> list[dict]:
    """全馬を偏差値降順に並べて返す（上位制限なし）。

    is_graded=True の場合、grade_code in (A/B/C/L/E) のレース用confidence判定
    （_compute_confidence_graded）を使う。graded_extra_by_horse は
    {horse_id: {"training_condition1": bool, "excuse_margin": bool,
    "age_veteran": bool}} を渡す（is_graded=False の場合は無視される）。
    """
    sorted_idx = deviation.sort_values(ascending=False).index
    picks = []
    graded_extra_by_horse = graded_extra_by_horse or {}

    for rank, idx in enumerate(sorted_idx):
        entry = next((e for e in entries if e["horse_id"] == v1_df.loc[idx, "horse_id"]), {})
        horse_id    = str(v1_df.loc[idx, "horse_id"])
        umaban      = int(v1_df.loc[idx, "umaban"])
        dev_score   = float(deviation.loc[idx])
        raw_score   = float(norm_blend.loc[idx])

        flags  = flags_df.loc[idx].to_dict() if idx in flags_df.index else {}
        v1_row = v1_df.loc[idx] if idx in v1_df.index else pd.Series(dtype=float)

        # 自信度
        conf_score, conf_label = _compute_confidence(
            flags, race_meta, v1_row,
            is_graded=is_graded,
            graded_extra=graded_extra_by_horse.get(horse_id),
        )

        # 説明生成（上位3頭のみ）
        explanation_text = ""
        if rank < 3:
            opp_row = opp_df.loc[idx] if idx in opp_df.index else None
            expl = HorseExplanation(
                race_id=str(v1_row.get("race_id", "")),
                umaban=umaban,
                ai_score=dev_score,
                top_explanations=_make_feature_explanations(v1_row),
                summary=_make_summary(v1_row, flags),
            )
            explanation_text = expl.to_full_report(
                horse_name=entry.get("horse_name", ""),
                opp_row=opp_row,
                flags=flags,
            )

        picks.append({
            "horse_id":         horse_id,
            "horse_name":       entry.get("horse_name", ""),
            "umaban":           umaban,
            "ai_v1_score":      round(float(v1_norm.loc[idx]), 4),
            "ai_opp_score":     round(float(opp_norm.loc[idx]), 4),
            "ai_raw":           round(raw_score, 4),
            "ai_deviation":     round(dev_score, 1),
            "rank":             rank + 1,
            "confidence_score": conf_score,
            "confidence_label": conf_label,
            "flags":            {k: (None if pd.isna(v) else v) for k, v in flags.items()},
            "explanation":      explanation_text,
        })

    return picks


# frontend/src/views/PicksView.tsx の computeUnifiedRank() と同一ロジック
# （実績集計側でも同じ基準でラベルを再現する必要があるため Python 側にも用意する）
def compute_unified_rank(rank: int, confidence_label: str) -> str | None:
    """rank(1始まり) と confidence_label(A/B/C) から統合推奨ラベルを返す。"""
    if rank == 1 and confidence_label == "A":
        return "一押し"
    if (rank == 1 and confidence_label == "B") or (rank == 2 and confidence_label == "A"):
        return "二押し"
    if rank <= 5 and confidence_label == "C":
        return "見送り"
    if 2 <= rank <= 5:
        return "三押し"
    return None


def _make_feature_explanations(v1_row: pd.Series) -> list[FeatureExplanation]:
    """v1 行から主要特徴量の説明リストを生成する（SHAP不使用）。"""
    explanations = []
    mapper = ConditionMapper()

    key_features = [
        ("avg_c4_norm_5", True),
        ("harmony_rank_norm", False),  # 低いほど有利
        ("bias_position_harmony", True),
        ("hidden_late_speed", True),
        ("jockey_te", True),
    ]
    for col, high_is_good in key_features:
        val = v1_row.get(col, np.nan)
        if pd.isna(val):
            continue
        val_f = float(val)
        # 有利方向の SHAP を近似: 高い方が有利なら val が高いほど正
        sv = val_f - 0.5 if high_is_good else 0.5 - val_f
        desc = mapper._describe(col, val_f, sv, v1_row)
        if desc:
            explanations.append(FeatureExplanation(
                feature_name=col,
                shap_value=sv,
                feature_value=val_f,
                description=desc,
                positive=sv >= 0,
            ))
    return explanations


def _make_summary(v1_row: pd.Series, flags: dict) -> str:
    """1文サマリーを生成する。"""
    parts = []
    c4 = v1_row.get("avg_c4_norm_5", np.nan)
    if not pd.isna(c4):
        c4_f = float(c4)
        if c4_f < 0.35:
            parts.append("逃げ・先行タイプ")
        elif c4_f < 0.6:
            parts.append("先行〜中団タイプ")
        else:
            parts.append("差し・追い込みタイプ")

    harm = v1_row.get("bias_position_harmony", np.nan)
    if not pd.isna(harm) and float(harm) > 0.55:
        parts.append("バイアス×展開の整合度高い")

    if flags.get("is_genuine") == 1:
        parts.append("本気ローテ")

    if not parts:
        return "AIスコア上位馬"
    return "、".join(parts) + "のため高評価"


# ── 1レース単位のスコアリング（バックフィル等での再利用向けに切り出し） ──────

def score_race_ai(
    race_meta: dict,
    entries: list[dict],
    model_v1: lgb.Booster,
    model_opp: lgb.Booster,
    engine_jvdl: sqlalchemy.engine.Engine,
    df_ent_hist: pd.DataFrame,
    df_races_hist: pd.DataFrame,
    df_te_hist: pd.DataFrame | None = None,
) -> dict | None:
    """1レース分のAI推奨（v1×opponent_v3アンサンブル）を計算する。

    generate_ai_picks() のメインループ本体を関数化したもの。過去レースの
    実績記録（update_ai_tipster_results）からも同じロジックで再利用できるよう
    に切り出している。出走馬が2頭未満、またはv1特徴量計算に失敗した場合は
    None を返す（呼び出し側でスキップ判定に使う）。

    Args:
        df_te_hist: _load_te_population_history() の結果。呼び出し側で1回だけ
            ロードして使い回す（None の場合は jockey_te/sire_te の母集団補完を
            スキップし、build_layer2_features() の narrow-history 版のまま）。
    """
    race_id = race_meta["race_id"]

    if len(entries) < 2:
        log.warning("出走馬不足 (%d頭) → スキップ: %s", len(entries), race_id)
        return None

    horse_ids = [e["horse_id"] for e in entries]

    # ── v1 特徴量計算 ──────────────────────────────────────────────────
    # 対象馬の全確定済み過去走を都度DBロード（parquet陳腐化を回避、PIT-safe）
    hist_df = _load_pace_v4_history(engine_jvdl, horse_ids, race_id[:8])
    jockey_wins = _load_jockey_recent_wins(
        engine_jvdl, [e["jockey_cd"] for e in entries], race_id
    )
    pred_rows = [_build_pred_row(e, race_meta, jockey_wins) for e in entries]

    v1_df = _compute_v1_features(pred_rows, hist_df)
    if v1_df.empty:
        log.warning("v1 特徴量計算失敗 → スキップ: %s", race_id)
        return None
    v1_df = v1_df.reset_index(drop=True)

    # ── jockey_te / sire_te の母集団補完（narrow-history バグの修正） ──────
    # _compute_v1_features 内の build_layer2_features() は hist_df（対象馬
    # 自身の過去走のみ）だけから jockey_te/sire_te を計算してしまうため、
    # 正しい母集団（df_te_hist、全馬の過去走）で上書きする。
    if df_te_hist is not None:
        try:
            te_fix = _compute_te_for_pred_rows(pred_rows, df_te_hist)
            te_fix = te_fix.set_index("horse_id")
            v1_df["jockey_te"] = v1_df["horse_id"].astype(str).map(te_fix["jockey_te"])
            v1_df["sire_te"]   = v1_df["horse_id"].astype(str).map(te_fix["sire_te"])
        except Exception:
            log.exception("jockey_te/sire_te 母集団補完に失敗 → narrow-history版を使用: %s", race_id)

    v1_scores_raw = _predict_v1(v1_df, model_v1)
    v1_df["_v1_score"] = v1_scores_raw

    # ── opponent 特徴量計算 ────────────────────────────────────────────
    df_target_opp = _build_opponent_target(race_meta, entries)
    df_ent_aug    = _augment_entries_for_opponent(df_ent_hist, df_target_opp)
    df_races_aug  = _augment_races_for_opponent(df_races_hist, race_meta)

    try:
        opp_scores_raw = _predict_opponent(
            df_target_opp, df_ent_aug, df_races_aug, model_opp
        )
    except Exception:
        log.exception("opponent 予測失敗: %s → v1 スコアのみ使用", race_id)
        opp_scores_raw = v1_scores_raw.copy()

    opp_feat_df = build_opponent_features(df_target_opp, df_ent_aug, df_races_aug)
    opp_feat_df["_opp_score"] = opp_scores_raw.values

    # ── アンサンブル（検証と同じ: per-race min-max → ブレンド） ────────
    # v1_scores_raw と opp_scores_raw はインデックスが別々なのでリセット
    v1_s   = pd.Series(v1_scores_raw.values,  index=range(len(entries)))
    opp_s  = pd.Series(opp_scores_raw.values, index=range(len(entries)))
    v1_norm, opp_norm, norm_blend = _blend_normalized(v1_s, opp_s)
    deviation = _to_deviation(norm_blend)

    # ── ローテーションフラグ ────────────────────────────────────────────
    df_rot_target = v1_df[["horse_id", "race_id", "race_date", "keibajo_code",
                            "grade_code"]].copy()
    df_rot_target = df_rot_target.rename(columns={"grade_code": "cur_grade_code"})
    # race_interval (休養日数) を追加
    race_dt = pd.Timestamp(race_meta["race_date"])
    intervals = []
    for hid in df_rot_target["horse_id"].astype(str):
        h = hist_df[hist_df["horse_id"] == hid]
        if not h.empty:
            last_dt = pd.Timestamp(h.iloc[-1]["race_date"])
            intervals.append((race_dt - last_dt).days)
        else:
            intervals.append(np.nan)
    df_rot_target["race_interval"] = intervals

    try:
        flags_df = build_rotation_flags(df_rot_target, engine_jvdl)
    except Exception:
        log.exception("rotation_flags 失敗: %s", race_id)
        flags_df = pd.DataFrame(
            [{c: np.nan for c in ROTATION_COLS} for _ in range(len(entries))],
            index=df_rot_target.index,
        )

    # ── 重賞用confidence判定 補助データ（grade_code in A/B/C/L/E のレースのみ）──
    is_graded = is_graded_race(race_meta.get("grade_code"))
    graded_extra_by_horse: dict[str, dict] = {}
    if is_graded:
        try:
            excuse_info = _fetch_prev_race_excuse_info(horse_ids, race_meta["race_date"])
        except Exception:
            log.exception("度外視判定用データ取得失敗（重賞用confidence）: %s", race_id)
            excuse_info = {}
        try:
            training1_flags = _fetch_training_condition1_flags(
                engine_jvdl, horse_ids, race_id[:8]
            )
        except Exception:
            log.exception("調教①判定失敗（重賞用confidence）: %s", race_id)
            training1_flags = {}

        for e in entries:
            hid = e["horse_id"]
            excuse = excuse_info.get(hid, {})
            graded_extra_by_horse[hid] = {
                "training_condition1": bool(training1_flags.get(hid, False)),
                "excuse_margin": is_excuse_margin_eligible(
                    excuse.get("grade_code"), excuse.get("margin_sec")
                ),
                "age_veteran": is_age_veteran(e.get("horse_age")),
            }

    # ── 全馬スコアリング ────────────────────────────────────────────────
    opp_feat_reindexed = opp_feat_df.reset_index(drop=True)
    picks = _score_all_horses(
        norm_blend, deviation, v1_norm, opp_norm,
        flags_df.reset_index(drop=True),
        entries, v1_df, opp_feat_reindexed, race_meta,
        is_graded=is_graded,
        graded_extra_by_horse=graded_extra_by_horse,
    )

    # レース内 top pick の自信度をレース単位に持つ
    top_confidence = picks[0]["confidence_label"] if picks else "C"

    return {
        "race_id":        race_id,
        "race_name":      race_meta["race_name"],
        "race_date":      race_meta["race_date"],
        "keibajo_code":   race_meta["keibajo_code"],
        "race_num":       race_meta["race_num"],
        "distance":       race_meta["distance"],
        "surface":        "芝" if str(race_meta["track_code"]).startswith("1") else "ダート",
        "grade_code":     race_meta["grade_code"],
        "field_size":     len(entries),
        "top_confidence": top_confidence,
        "data_kubun":     race_meta.get("data_kubun"),
        "rank_mode":      "graded" if is_graded else "standard",
        "picks":          picks,
    }


# ── メイン処理 ─────────────────────────────────────────────────────────────────

def generate_ai_picks(target_dates: list[date] | None = None) -> dict:
    """
    指定日（デフォルト: 今週末）のAI推奨を生成して辞書で返す。
    """
    if target_dates is None:
        sat, sun = _this_weekend()
        target_dates = [sat, sun]

    # DBにデータがなければ直近の開催日にフォールバック
    target_dates = _resolve_target_dates(target_dates)

    log.info("AI推奨生成開始: %s", [str(d) for d in target_dates])

    # モデルロード
    log.info("モデルロード: v1, opponent_v3")
    model_v1  = lgb.Booster(model_file=str(_V1_MODEL))
    model_opp = lgb.Booster(model_file=str(_OPP_MODEL))

    # JVDL DB エンジン（opponent / rotation / pace_v4 履歴 共通）
    engine_jvdl = _jvdl_engine()

    # opponent 用の全履歴をロード（一括、レース単位で再利用）
    log.info("JVDL 全履歴ロード中...")
    df_ent_hist, df_races_hist = load_all_race_history(engine_jvdl)

    # jockey_te/sire_te 用の母集団を一括ロード（narrow-history バグの修正）
    log.info("jockey_te/sire_te 母集団ロード中...")
    df_te_hist = _load_te_population_history(engine_jvdl)

    # 週末レース取得
    races = _get_upcoming_races(target_dates)
    if not races:
        log.warning("対象レースなし")
        result = {"generated_at": pd.Timestamp.now().isoformat(), "race_data": []}
        _OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        _OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result

    race_results = []
    for race_meta in races:
        log.info("処理中: %s %s %s %dm", race_meta["race_id"], race_meta["race_name"],
                 race_meta["keibajo_code"], race_meta["distance"])

        entries = _get_race_entries(race_meta["race_id"])
        race_result = score_race_ai(
            race_meta, entries, model_v1, model_opp,
            engine_jvdl, df_ent_hist, df_races_hist, df_te_hist,
        )
        if race_result is not None:
            race_results.append(race_result)

    sat, sun = _this_weekend()
    is_fallback = target_dates != [sat, sun] and target_dates != [sat] and target_dates != [sun]
    result = {
        "generated_at": pd.Timestamp.now().isoformat(),
        "target_dates": [str(d) for d in target_dates],
        "is_fallback": is_fallback,
        "race_data": race_results,
    }
    _OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    _OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("出力完了: %s (%d レース)", _OUTPUT, len(race_results))
    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="AI picks 生成スクリプト")
    parser.add_argument("--dates", nargs="*", help="対象日 (YYYY-MM-DD形式)。省略時は今週末")
    args = parser.parse_args()

    if args.dates:
        target = [date.fromisoformat(d) for d in args.dates]
    else:
        target = None

    generate_ai_picks(target)
