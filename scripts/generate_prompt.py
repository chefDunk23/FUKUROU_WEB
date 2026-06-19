"""
scripts/generate_prompt.py
===========================
Phase 1 — 踏み台 JSON（raw_race_data.json）生成。

データソース（優先順）:
  1. Parquet + fukurou_jvdl（馬名補完）  — 過去モデルスコアが使える日付
  2. fukurou_jvdl のみ（expert_evaluations スコアを使用） — Parquet 範囲外の日付

LLM API は一切叩かない。このJSONをLLMに渡して台本を生成する。

Usage:
    py -3.13 scripts/generate_prompt.py --date 2026-05-31 --venue 08
    py -3.13 scripts/generate_prompt.py --date 2026-05-31            # 全 JRA 会場
    py -3.13 scripts/generate_prompt.py --date 2026-05-31 --no-db   # DB 接続なし（Parquet のみ）
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import pandas as pd

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.video_generator.corner_router import GRADE_LABELS, KEIBAJO_LABELS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

_DEFAULT_PARQUET = Path("outputs/v2_stacked_features.parquet")

# JRA 場コード → ローマ字（Remotion props 用）
_KEIBAJO_ROMAJI: dict[str, str] = {
    "01": "sapporo",  "1": "sapporo",
    "02": "hakodate", "2": "hakodate",
    "03": "fukushima","3": "fukushima",
    "04": "niigata",  "4": "niigata",
    "05": "tokyo",    "5": "tokyo",
    "06": "nakayama", "6": "nakayama",
    "07": "chukyo",   "7": "chukyo",
    "08": "kyoto",    "8": "kyoto",
    "09": "hanshin",  "9": "hanshin",
    "10": "kokura",
}

# jvdl.races.course_type → track_code（_surface() 互換）
_COURSE_TYPE_TO_TRACK_CODE: dict[str, str] = {
    "芝":   "10",
    "ダート": "20",
    "障害":  "51",
}

_SUB_COLS = [
    "score_ability_v2",
    "score_course_v2",
    "score_team_v2",
    "score_training_v2",
    "score_pace_v2",
    "score_pedigree_v1",
]

_MARKS = ["◎", "◯", "▲"]

_TARGET_RACE_NUMS = {9, 10, 11, 12}

# JRA 中央競馬の場コード（地方・海外除く）
_JRA_PLACE_CODES = {"01","02","03","04","05","06","07","08","09","10"}


# ── fukurou_jvdl から馬名を取得 ─────────────────────────────────────────────

_SQL_HORSE_NAMES = """
SELECT
    r.id                  AS race_id,
    r.race_number         AS race_num,
    r.name                AS race_name,
    r.place_code          AS keibajo_code,
    e.horse_number        AS umaban,
    h.name                AS horse_name,
    e.horse_id,
    e.win_odds            AS tan_odds,
    e.popularity          AS ninki
FROM   races r
JOIN   race_entries e  ON e.race_id = r.id
JOIN   horses h        ON h.id = e.horse_id
WHERE  r.date::date   = %s
  AND  r.place_code   = %s
  AND  r.race_number  BETWEEN 9 AND 12
ORDER  BY r.race_number, e.horse_number
"""


def _fetch_horse_names(date_str: str, venue_code: str) -> pd.DataFrame | None:
    """fukurou_jvdl から馬名を取得する。接続失敗時は None を返す。"""
    try:
        from shared.db.jvdl import query_df
        log.info("jvdl から馬名を取得中: %s %s 9R〜12R", date_str, venue_code)
        df = query_df(_SQL_HORSE_NAMES, (date_str, venue_code.zfill(2)))
        if df.empty:
            log.warning("jvdl に対象レースのデータがありません: %s %s", date_str, venue_code)
            return None
        df["race_id"]    = df["race_id"].astype(str)
        df["horse_name"] = df["horse_name"].fillna("").astype(str)
        df["umaban"]     = pd.to_numeric(df["umaban"], errors="coerce").fillna(0).astype(int)
        log.info("  jvdl 取得完了: %d頭", len(df))
        return df
    except Exception as e:
        log.warning("jvdl 接続失敗（馬番のみ表示）: %s", e)
        return None


# ── fukurou_jvdl ネイティブセッション（Parquet 範囲外用）────────────────────

_SQL_JVDL_SESSION = """
SELECT
    r.id                                                                 AS race_id,
    r.race_number                                                        AS race_num,
    COALESCE(NULLIF(TRIM(r.name), ''), '')                              AS race_name,
    r.place_code                                                         AS keibajo_code,
    r.course_type,
    r.distance,
    r.grade_code,
    e.horse_number                                                       AS umaban,
    h.name                                                               AS horse_name,
    e.horse_id,
    e.win_odds                                                           AS tan_odds,
    e.popularity                                                         AS ninki,
    COALESCE(SUM(ev.score) FILTER (WHERE ev.expert_type = 'pace'),      0) AS ev_pace,
    COALESCE(SUM(ev.score) FILTER (WHERE ev.expert_type = 'training'),  0) AS ev_training,
    COALESCE(SUM(ev.score) FILTER (WHERE ev.expert_type = 'breeding'),  0) AS ev_breeding,
    COALESCE(SUM(ev.score) FILTER (WHERE ev.expert_type = 'condition'), 0) AS ev_condition,
    COALESCE(SUM(ev.score) FILTER (WHERE ev.expert_type = 'reversal'),  0) AS ev_reversal,
    COALESCE(SUM(ev.score), 0)                                              AS ev_total
FROM   races r
JOIN   race_entries e  ON e.race_id = r.id
JOIN   horses h        ON h.id = e.horse_id
LEFT   JOIN expert_evaluations ev
       ON  ev.race_id  = r.id
       AND ev.horse_id = e.horse_id
WHERE  r.date::date  = %s
  AND  r.place_code  = %s
  AND  r.race_number BETWEEN 9 AND 12
GROUP  BY r.id, r.race_number, r.name, r.place_code,
          r.course_type, r.distance, r.grade_code,
          e.horse_number, h.name, e.horse_id, e.win_odds, e.popularity
ORDER  BY r.race_number, e.horse_number
"""

_SQL_JVDL_VENUES = """
SELECT DISTINCT place_code
FROM   races
WHERE  date::date = %s
  AND  place_code = ANY(%s)
ORDER  BY place_code
"""


def _get_venues_from_jvdl(date_str: str) -> list[str]:
    """jvdl からその日開催の JRA 会場コードを返す。失敗時は空リスト。"""
    try:
        from shared.db.jvdl import query_df
        codes = sorted(_JRA_PLACE_CODES)
        df = query_df(_SQL_JVDL_VENUES, (date_str, codes))
        return df["place_code"].tolist() if not df.empty else []
    except Exception as e:
        log.warning("jvdl 会場取得失敗: %s", e)
        return []


# ── ヘルパー ─────────────────────────────────────────────────────────────────

def _grade(code) -> str:
    if code is None:
        return "未勝利"
    s = str(code).strip()
    if not s or s.lower() == "nan":
        return "未勝利"
    try:
        if np.isnan(float(s)):
            return "未勝利"
    except ValueError:
        pass
    return GRADE_LABELS.get(s, s)


def _surface(track_code) -> str:
    try:
        return "ダート" if int(str(track_code)) >= 20 else "芝"
    except (TypeError, ValueError):
        return "芝"


def _race_label(row: pd.Series, race_name_override: str = "") -> str:
    venue    = KEIBAJO_LABELS.get(str(row.get("keibajo_code", "")).strip(), "?")
    race_num = int(row.get("race_num", 0) or 0)
    surface  = _surface(row.get("track_code"))
    distance = int(row.get("distance", 0) or 0)
    grade    = _grade(row.get("grade_code"))
    rname    = race_name_override or str(row.get("race_name", "") or "").strip()
    rname    = "" if rname in ("nan", "None") else rname
    if rname:
        return f"{venue}{race_num}R {rname}（{grade}）{surface}{distance}m"
    return f"{venue}{race_num}R {grade} {surface}{distance}m"


def _zscores(s: pd.Series) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce").fillna(0.0)
    mn, std = s.mean(), s.std()
    return (s - mn) / std if std > 1e-9 else pd.Series(0.0, index=s.index)


def _ai_score_0to100(z_ens: pd.Series) -> pd.Series:
    mn, mx = z_ens.min(), z_ens.max()
    return (z_ens - mn) / (mx - mn) * 100 if mx - mn > 1e-9 else pd.Series(50.0, index=z_ens.index)


def _horse_name_from(row: pd.Series, db_map: dict[tuple[str, int], str]) -> str:
    race_id = str(row.get("race_id", ""))
    umaban  = int(row.get("umaban", 0) or 0)
    name = db_map.get((race_id, umaban))
    if name:
        return name
    for col in ("horse_name", "uma_name"):
        v = row.get(col)
        if v and str(v) not in ("nan", "None", ""):
            return str(v)
    return f"{umaban}番"


def _date_label(date_str: str) -> str:
    import datetime
    try:
        dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        return f"{dt.year}/{dt.month}/{dt.day}({'月火水木金土日'[dt.weekday()]})"
    except Exception:
        return date_str


def _evaluation_keywords(zs_map: dict, idx: int, ai_rank: int) -> list[str]:
    def z(col: str) -> float:
        s = zs_map.get(col)
        return float(s.iloc[idx]) if s is not None and idx < len(s) else 0.0

    ability_z  = z("score_ability_v2")
    pace_z     = z("score_pace_v2")
    pedigree_z = z("score_pedigree_v1")
    course_z   = z("score_course_v2")
    is_underdog = ai_rank >= 4 and (pace_z >= 1.5 or pedigree_z >= 1.5)

    kw: list[str] = []
    if ability_z >= 1.8:  kw.append("AI指数突出")
    if pace_z    >= 1.5:  kw.append("展開有利")
    if pedigree_z >= 1.5: kw.append("血統適性")
    if course_z  >= 1.2:  kw.append("コース適性")
    if is_underdog:       kw.append("穴狙い")
    if not kw:            kw.append("総合能力上位")
    return kw[:3]


def _evaluation_keywords_from_jvdl(row: pd.Series, ai_rank: int) -> list[str]:
    """jvdl.expert_evaluations スコアから評価キーワードを生成する。"""
    pace_s     = float(row.get("ev_pace",      0) or 0)
    training_s = float(row.get("ev_training",  0) or 0)
    breeding_s = float(row.get("ev_breeding",  0) or 0)
    condition_s= float(row.get("ev_condition", 0) or 0)
    reversal_s = float(row.get("ev_reversal",  0) or 0)

    kw: list[str] = []
    if pace_s     >= 2: kw.append("展開有利")
    if breeding_s >= 2: kw.append("血統適性")
    if training_s >= 2: kw.append("調教良好")
    if condition_s >= 2: kw.append("コース適性")
    if reversal_s >= 2 and ai_rank >= 4: kw.append("穴狙い")
    if not kw:          kw.append("総合能力上位")
    return kw[:3]


# ── Parquet ベース セッション処理 ─────────────────────────────────────────────

def process_session(
    df_all:     pd.DataFrame,
    date_str:   str,
    venue_code: str,
    use_db:     bool = True,
) -> tuple[str, str, list[dict]]:
    """Parquet + jvdl（馬名補完）で races リストを返す。"""
    venue_name = KEIBAJO_LABELS.get(venue_code.strip(), f"会場{venue_code}")
    date_label = _date_label(date_str)

    df_all["_date_str"] = pd.to_datetime(df_all["race_date"]).dt.strftime("%Y-%m-%d")
    mask = (df_all["_date_str"] == date_str) & (
        df_all["keibajo_code"].astype(str).str.strip() == venue_code.strip()
    )
    sess_df = df_all[mask].copy()

    if sess_df.empty:
        log.warning("Parquet にデータがありません: date=%s venue=%s", date_str, venue_code)
        return venue_name, date_label, []

    log.info("Parquet: %s %s  %dR %d頭", date_str, venue_name,
             sess_df["race_id"].nunique(), len(sess_df))

    db_df  = _fetch_horse_names(date_str, venue_code) if use_db else None
    db_map: dict[tuple[str, int], str] = {}
    race_name_db_map: dict[str, str] = {}
    if db_df is not None:
        for _, r in db_df.iterrows():
            name = r["horse_name"]
            if name:
                db_map[(str(r["race_id"]), int(r["umaban"]))] = name
        if "race_name" in db_df.columns:
            for _, r in db_df[["race_id", "race_name"]].drop_duplicates("race_id").iterrows():
                rn = str(r.get("race_name", "") or "").strip()
                if rn and rn not in ("nan", "None"):
                    race_name_db_map[str(r["race_id"])] = rn

    sess_df["_rn"] = pd.to_numeric(sess_df.get("race_num", 0), errors="coerce").fillna(0)
    race_ids = (
        sess_df[sess_df["_rn"].isin(_TARGET_RACE_NUMS)][["race_id", "_rn"]]
        .drop_duplicates("race_id")
        .sort_values("_rn")["race_id"]
        .tolist()
    )
    if not race_ids:
        log.warning("9R〜12Rのデータがありません: %s %s", date_str, venue_code)
        return venue_name, date_label, []

    target_df = sess_df[sess_df["_rn"].isin(_TARGET_RACE_NUMS)].copy()
    av_global = [c for c in _SUB_COLS if c in target_df.columns]
    if av_global:
        global_z_ens = pd.concat(
            [_zscores(target_df[c]) for c in av_global], axis=1
        ).mean(axis=1)
    else:
        global_z_ens = pd.Series(0.0, index=target_df.index)

    race_intermediates: list[tuple] = []
    for rid in race_ids:
        rd_orig = sess_df[sess_df["race_id"] == rid].copy()
        if rd_orig.empty:
            continue
        av = [c for c in _SUB_COLS if c in rd_orig.columns]
        if not av:
            continue
        rd = rd_orig.reset_index(drop=True)
        per_zs_map = {c: _zscores(rd[c]) for c in av}
        per_z_ens  = pd.concat(list(per_zs_map.values()), axis=1).mean(axis=1)
        g_z = global_z_ens.reindex(rd_orig.index).fillna(0.0).values
        race_intermediates.append((rid, rd, per_zs_map, per_z_ens, g_z))

    all_g_z = np.concatenate([g_z for _, _, _, _, g_z in race_intermediates])
    g_min, g_max = float(all_g_z.min()), float(all_g_z.max())
    g_range = g_max - g_min if g_max - g_min > 1e-9 else 1.0

    def _global_score(val: float) -> float:
        return round((val - g_min) / g_range * 100, 1)

    races: list[dict] = []
    for rid, rd, per_zs_map, per_z_ens, g_z in race_intermediates:
        ab_rank = per_z_ens.rank(ascending=False, method="first").astype(int)
        top3    = per_z_ens.nlargest(min(3, len(rd))).index.tolist()
        r0    = rd.iloc[0]
        race_name_override = race_name_db_map.get(str(rid), "")
        label = _race_label(r0, race_name_override)

        picks: list[dict] = []
        for i, idx in enumerate(top3):
            row    = rd.loc[idx]
            emp_z  = float(per_zs_map.get("score_ability_v2", pd.Series([0.0])).iloc[idx]) \
                     if idx < len(per_zs_map.get("score_ability_v2", pd.Series())) else 0.0
            ai_r   = int(ab_rank.iloc[idx])
            umaban = int(row.get("umaban", idx + 1) or idx + 1)
            hname  = _horse_name_from(row, db_map)
            picks.append({
                "mark":                _MARKS[i],
                "umaban":              umaban,
                "horse_name":          hname,
                "ai_score":            _global_score(float(g_z[idx])),
                "emp_z":               f"{emp_z:+.2f}",
                "evaluation_keywords": _evaluation_keywords(per_zs_map, idx, ai_r),
                "evaluation_reason":   "",
                "concern":             "",
            })

        races.append({
            "race_id":           str(rid),
            "race_label":        label,
            "race_name":         race_name_override,
            "picks":             picks,
            "speech_lines":      [],
            "speech_text":       "",
            "telop":             "",
            "audio_url":         "",
            "audio_duration_ms": 0,
        })

    return venue_name, date_label, races


# ── jvdl ネイティブ セッション処理（Parquet 範囲外用）─────────────────────────

def process_session_from_jvdl(
    date_str:   str,
    venue_code: str,
) -> tuple[str, str, list[dict]]:
    """
    Parquet にデータがない日付に対して fukurou_jvdl のみで races を構築する。
    AI スコアは expert_evaluations の合計スコアを使用する。
    """
    venue_name = KEIBAJO_LABELS.get(venue_code.strip(), f"会場{venue_code}")
    date_label = _date_label(date_str)

    try:
        from shared.db.jvdl import query_df
    except ImportError as e:
        log.error("jvdl インポート失敗: %s", e)
        return venue_name, date_label, []

    try:
        df = query_df(_SQL_JVDL_SESSION, (date_str, venue_code.zfill(2)))
    except Exception as e:
        log.error("jvdl クエリ失敗: %s", e)
        return venue_name, date_label, []

    if df.empty:
        log.warning("jvdl にデータがありません: %s %s", date_str, venue_code)
        return venue_name, date_label, []

    log.info("jvdl ネイティブ: %s %s  %dR %d頭",
             date_str, venue_name, df["race_id"].nunique(), len(df))

    # track_code を course_type から導出
    df["track_code"] = df["course_type"].map(_COURSE_TYPE_TO_TRACK_CODE).fillna("10")

    # ev_total を float 化
    df["ev_total"] = pd.to_numeric(df["ev_total"], errors="coerce").fillna(0.0)

    race_ids = (
        df[df["race_num"].isin(_TARGET_RACE_NUMS)][["race_id", "race_num"]]
        .drop_duplicates("race_id")
        .sort_values("race_num")["race_id"]
        .tolist()
    )
    if not race_ids:
        log.warning("9R〜12R のデータがありません: %s %s", date_str, venue_code)
        return venue_name, date_label, []

    # セッション全体の ev_total を Min-Max 正規化してグローバル AI スコアに
    all_ev = df[df["race_id"].isin(race_ids)]["ev_total"].values
    ev_min, ev_max = float(all_ev.min()), float(all_ev.max())
    ev_range = ev_max - ev_min if ev_max - ev_min > 1e-9 else 1.0

    def _global_score(val: float) -> float:
        return round((val - ev_min) / ev_range * 100, 1)

    races: list[dict] = []
    for rid in race_ids:
        rd = df[df["race_id"] == rid].copy().reset_index(drop=True)
        if rd.empty:
            continue

        # per-race Z-score でランキング
        per_z = _zscores(rd["ev_total"])
        top3   = per_z.nlargest(min(3, len(rd))).index.tolist()
        ab_rank = per_z.rank(ascending=False, method="first").astype(int)

        r0    = rd.iloc[0]
        label = _race_label(r0, str(r0.get("race_name", "") or "").strip())

        picks: list[dict] = []
        for i, idx in enumerate(top3):
            row    = rd.loc[idx]
            emp_z  = float(per_z.iloc[idx])
            ai_r   = int(ab_rank.iloc[idx])
            umaban = int(row.get("umaban", idx + 1) or idx + 1)
            hname  = str(row.get("horse_name", "") or f"{umaban}番")
            ev_val = float(row.get("ev_total", 0) or 0)
            picks.append({
                "mark":                _MARKS[i],
                "umaban":              umaban,
                "horse_name":          hname,
                "ai_score":            _global_score(ev_val),
                "emp_z":               f"{emp_z:+.2f}",
                "evaluation_keywords": _evaluation_keywords_from_jvdl(row, ai_r),
                "evaluation_reason":   "",
                "concern":             "",
            })

        races.append({
            "race_id":           str(rid),
            "race_label":        label,
            "race_name":         str(r0.get("race_name", "") or "").strip(),
            "picks":             picks,
            "speech_lines":      [],
            "speech_text":       "",
            "telop":             "",
            "audio_url":         "",
            "audio_duration_ms": 0,
        })

    return venue_name, date_label, races


# ── LLM への指示文 ─────────────────────────────────────────────────────────────

_INSTRUCTIONS = """\
あなたはAI競馬動画の台本ライターです。
このJSONの各レースについて、以下の【記入必須フィールド】だけを埋めてください。
それ以外のフィールドは絶対に変更・削除しないでください。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
◆ 変更してはいけないフィールド（触らないこと）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  race_id / race_label / race_name / picks[].mark / picks[].umaban
  picks[].horse_name / picks[].ai_score / picks[].emp_z
  picks[].evaluation_keywords / speech_text / audio_url / audio_duration_ms
  _seq / _instructions

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
◆ 記入必須フィールド（1）speech_lines — 博士×助手の掛け合い
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  "speech_lines": [] の空配列に以下形式で追加してください。

  形式（3フィールド必須）:
    {"speaker": "博士", "text": "一文または二文。漢字交じり。テロップ表示に使用。", "reading": "いちぶんまたはにぶん。すべてひらがな。おんせいごうせいにしよう。"},
    {"speaker": "助手", "text": "明るい返答。", "reading": "あかるいへんとう。"},

  reading フィールドのルール:
  ・すべてひらがなで記入（カタカナ・漢字・アルファベット不可）
  ・数字は読み方通り（12番 → じゅうにばん、G1 → じーわん、AI → えーあい）
  ・馬名はカタカナ読みをひらがなに（ウインマスカレード → ういんますかれーど）
  ・レース名・競馬場名も正しい読みで（飛竜特別 → ひりゅうとくべつ、新潟 → にいがた）
  ・長音符「ー」はひらがなの「ー」そのまま使用可

  キャラクター:
  ・博士（フクロウ博士）: 落ち着いた解説者口調。picks の根拠・展開・血統を具体的に語る。
  ・助手（ひよこ）: 明るく元気。視聴者目線で質問・驚き・相槌を入れる。

  分量: 1レースにつき 10〜14 要素（5〜7往復）= 約40〜50秒

  _seq による動画の流れ:
  ・"index": 1（最初のレース）
      → 動画の冒頭には「目次画面」があり、そこで挨拶・開催地・日付は済んでいる。
      → 「皆様こんにちは」「本日は〇〇競馬場」などの前置きは絶対に入れないこと。
      → 直接レース解説から入る。例）「まず{race_label}じゃが、本命は…」
  ・"index" が中間
      → 助手が「続けて教えてください！」などで自然につなぐ
      → 毎回同じフレーズにしないこと
  ・"index" = "total"（最後のレース）
      → 博士が「以上、本日の注目レースでした。ぜひ参考にしてください。」などで締める

  注意:
  ・各 text の中に改行文字（\\n）を入れないこと
  ・speaker は必ず "博士" か "助手" のいずれか（他の文字列不可）
  ・race_label や horse_name は JSON から読み取って台本に織り込む

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
◆ 記入必須フィールド（2）picks[].evaluation_reason と concern
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ◎◯▲ の3頭すべてに記入（▲を忘れずに）。

  evaluation_reason: AIが高く評価している理由を15文字以内
    例）「前走上がり最速」「距離短縮プラス」「道悪巧者」

  concern: 不安材料・買いにくい点を15文字以内
    例）「折り合い難しい」「外枠がマイナス」「休み明け初戦」

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
◆ 記入必須フィールド（3）telop
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  本命◎馬名と一言評価を20文字以内で。
    例）「◎サンライズフラッグ 展開利す」
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""


# ── CLI ──────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Phase 1: 踏み台JSON生成")
    p.add_argument("--date",    "-d", required=True, metavar="YYYY-MM-DD")
    p.add_argument("--venue",   "-v", default=None,  metavar="CODE",
                   help="JRA場コード（例: 08=京都）。省略時は全会場を1動画にまとめる")
    p.add_argument("--parquet", "-p", type=Path, default=_DEFAULT_PARQUET)
    p.add_argument("--output",  "-o", type=Path, default=Path("data/output"),
                   help="出力ディレクトリ（デフォルト: data/output/）")
    p.add_argument("--no-db",   action="store_true",
                   help="DB接続をスキップする（Parquet のみ、jvdl フォールバックなし）")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()

    # ── Parquet の読み込みと対象日チェック ────────────────────────────────
    parquet_has_date = False
    df_all: pd.DataFrame | None = None

    if args.parquet.exists():
        log.info("Parquet 読み込み: %s", args.parquet)
        df_all = pd.read_parquet(args.parquet)
        df_all["_date_str"] = pd.to_datetime(df_all["race_date"]).dt.strftime("%Y-%m-%d")
        parquet_has_date = (df_all["_date_str"] == args.date).any()
        if parquet_has_date:
            log.info("Parquet に %s のデータあり → Parquetモード", args.date)
        else:
            log.info("Parquet に %s のデータなし → jvdl ネイティブモードで試行", args.date)
    else:
        log.info("Parquet が見つかりません → jvdl ネイティブモード: %s", args.parquet)

    # ── 会場コード決定 ────────────────────────────────────────────────────
    if args.venue:
        venue_codes = [args.venue]
    elif parquet_has_date and df_all is not None:
        venue_codes = (
            df_all[df_all["_date_str"] == args.date]["keibajo_code"]
            .astype(str).str.strip().unique().tolist()
        )
        venue_codes.sort()
    elif not args.no_db:
        venue_codes = _get_venues_from_jvdl(args.date)
        if not venue_codes:
            log.error("jvdl に %s の開催データがありません", args.date)
            sys.exit(1)
    else:
        log.error("Parquet にデータがなく --no-db が指定されているため処理できません")
        sys.exit(1)

    # ── セッション処理 ────────────────────────────────────────────────────
    all_races:   list[dict] = []
    venue_names: list[str]  = []
    date_label = _date_label(args.date)

    for code in venue_codes:
        if parquet_has_date and df_all is not None:
            vname, dlabel, races = process_session(
                df_all, args.date, code, use_db=not args.no_db
            )
            # Parquet に日付はあるがこの会場にデータがない場合は jvdl で補完
            if not races and not args.no_db:
                log.info("Parquet に %s %s のデータなし → jvdl で補完", args.date, code)
                vname, dlabel, races = process_session_from_jvdl(args.date, code)
        else:
            vname, dlabel, races = process_session_from_jvdl(args.date, code)

        if races:
            all_races.extend(races)
            venue_names.append(vname)
            date_label = dlabel

    if not all_races:
        log.error("出力できるレースがありません")
        sys.exit(1)

    # ── 連番付与・JSON 保存 ───────────────────────────────────────────────
    total = len(all_races)
    for i, race in enumerate(all_races):
        race["_seq"] = {"index": i + 1, "total": total}

    combined_venue = "・".join(venue_names)
    data = {
        "session": args.date,
        "date":    date_label,
        "venue":   combined_venue,
        "races":   all_races,
        "_instructions": _INSTRUCTIONS,
    }

    date_compact = args.date.replace("-", "")
    suffix = venue_codes[0] if len(venue_codes) == 1 else "all"
    out_path = args.output / f"raw_race_data_{date_compact}_{suffix}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("踏み台 JSON 保存: %s  (%d レース / %s)", out_path, total, combined_venue)

    print(f"\n{'='*60}")
    print(f"  Phase 1 完了 — 踏み台 JSON 生成 ({combined_venue} / {total}レース)")
    print(f"{'='*60}")
    print(f"  -> {out_path}")
    print(f"\n  次のステップ:")
    print(f"    1. 上記 JSON の内容を LLM チャットに渡す")
    print(f"    2. _instructions に従い speech_lines / evaluation_reason / concern / telop を記入")
    print(f"    3. 記入済みJSONをアップロードしてレンダリング開始")
    print(f"{'='*60}\n")
