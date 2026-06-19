"""
api_v1/services/review_builder.py
===================================
月曜振り返り動画 (portrait / landscape) 用 timeline JSON を生成する。
AI_FUKUROU_KEIBA_Ver2/pipeline/step6_review_results.py の fukurou_v2_app 移植版。

入力:
  data/predictions/weekend_predictions_{YYYYMMDD}.csv
    (batch_predict() が実行時に自動保存するファイル)

出力:
  owl_video/public/dynamic_data/short_review/
    review_landscape_timeline_{YYYYMMDD}.json  ← RaceReviewPortrait / RaceReviewLandscape 共用

DB:
  fukurou_keiba_v2 (shared.db.jvdata) - race_entries, horses テーブルで確定結果取得

使用例 (API 経由):
  POST /api/v1/pipeline/review  {"race_date": "20260427", "with_tts": true}
"""
from __future__ import annotations

import glob
import logging
import math
import re
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import psycopg2.extras

from shared.db.jvdata import get_conn
from shared.config import DB_V2

logger = logging.getLogger(__name__)

# ── パス定数 ─────────────────────────────────────────────────────────────────

_APP_ROOT       = Path(__file__).parent.parent.parent           # fukurou_v2_app/
_PRED_DIR       = _APP_ROOT / "data" / "predictions"           # weekend_predictions_*.csv
_OWL_PUBLIC     = _APP_ROOT / "owl_video" / "public"
_REVIEW_DATA    = _OWL_PUBLIC / "dynamic_data" / "short_review"
_AUDIO_DIR      = _REVIEW_DATA / "audio"

AUDIO_BUFFER_SEC = 1.0

# ── 対象レース・マーク定数 ─────────────────────────────────────────────────────

TARGET_RACE_NUMS     = {9, 10, 11, 12}
AI_MARKS             = {1, 2, 3}
PLACE_RANK_MAX       = 3
HIGH_ODDS_1          = 2000     # 20倍超（単勝払戻円換算）
HIGH_ODDS_2          = 5000     # 50倍超
ANA_HIGH_ODDS_THRESHOLD  = 1000  # 穴馬的中 HIGH_DIVIDEND_WIN 判定下限
HIGHLIGHT_ODDS_THRESHOLD = 1500  # 特大ハイライト: 15倍以上
HIGHLIGHT_NINKI_THRESHOLD = 7    # 特大ハイライト: 7番人気以下
MAX_HIGHLIGHTS       = 5
MIN_HIGHLIGHTS       = 3

_MARK_LABEL: dict[int, str] = {1: "◎", 2: "〇", 3: "★"}
_WIN_BONUS:  dict[int, float] = {1: 3.0, 2: 2.0, 3: 1.5}
_TTS_MARK:   dict[str, str]  = {"◎": "本命", "〇": "対抗", "★": "単穴"}

# ── 演出フラグ ────────────────────────────────────────────────────────────────

_EFFECT_PERFECT_HIT          = "PERFECT_HIT"
_EFFECT_HONMEI_HIGH_DIVIDEND = "HONMEI_HIGH_DIVIDEND"
_EFFECT_HONMEI_WIN           = "HONMEI_WIN"
_EFFECT_HIGH_DIVIDEND_WIN    = "HIGH_DIVIDEND_WIN"
_EFFECT_HOLE_PLACE           = "HOLE_PLACE"
_EFFECT_NORMAL_HIT           = "NORMAL_HIT"
_EFFECT_MISS                 = "MISS"


# ── ユーティリティ ─────────────────────────────────────────────────────────────

def _extract_race_num(race_id: str) -> int:
    m = re.search(r'R(\d+)$', str(race_id))
    return int(m.group(1)) if m else 0


def _extract_date(race_id: str) -> str:
    s = str(race_id)
    return s[:8] if len(s) >= 8 else ""


_VENUE_MAP = {
    "01": "札幌", "02": "函館", "03": "福島", "04": "新潟",
    "05": "東京", "06": "中山", "07": "中京", "08": "京都",
    "09": "阪神", "10": "小倉",
}


def _extract_venue(race_id: str, race_info: str = "") -> str:
    if race_info:
        m = re.search(r'\d+回([^\d\s]+?)\d+日目', race_info)
        if m:
            return m.group(1).strip()
    code = str(race_id)[8:10] if len(str(race_id)) >= 10 else ""
    return _VENUE_MAP.get(code, f"会場{code}")


def _tansho_to_yen(raw) -> int:
    """win_odds（÷10済み float, 例: 23.4倍）→ 払戻円（例: 2340円）。"""
    try:
        return round(float(raw) * 100) if float(raw) > 0 else 0
    except (ValueError, TypeError):
        return 0


def _to_r_format(race_id: str) -> str:
    """DB形式（12桁・Rなし）→ CSV形式（13桁・R付き）。"""
    s = str(race_id)
    if len(s) == 12 and "R" not in s:
        return s[:10] + "R" + s[10:]
    return s


def _datestr_to_iso(date_str: str) -> str:
    return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"


# ── Step 1: 予測CSV 読み込み ────────────────────────────────────────────────────

def load_predictions(date_str: str) -> pd.DataFrame:
    """指定日付（YYYYMMDD）の weekend_predictions_*.csv を読み込む。"""
    _PRED_DIR.mkdir(parents=True, exist_ok=True)
    exact = _PRED_DIR / f"weekend_predictions_{date_str}.csv"
    if not exact.exists():
        files = sorted(glob.glob(str(_PRED_DIR / "weekend_predictions_*.csv")))
        candidates = [
            p for p in files
            if (m := re.search(r'(\d{8})', p)) and m.group(1) <= date_str
        ]
        if not candidates:
            raise FileNotFoundError(f"weekend_predictions_*.csv が見つかりません: {_PRED_DIR}")
        path = Path(candidates[-1])
        logger.warning("[ReviewBuilder] %s のCSVなし → 直近: %s", date_str, path.name)
    else:
        path = exact

    logger.info("[ReviewBuilder] 予測CSV: %s", path)
    df = pd.read_csv(path, encoding="utf-8-sig")
    df["umaban"] = pd.to_numeric(df["umaban"], errors="coerce").astype("Int64")
    # race_id を R-format に正規化
    df["race_id"] = df["race_id"].apply(_to_r_format)
    df = df[df["race_id"].apply(_extract_race_num).isin(TARGET_RACE_NUMS)].copy()
    logger.info("[ReviewBuilder] 対象レース行数: %d", len(df))
    return df


# ── Step 2: fukurou_keiba_v2 から確定結果取得 ────────────────────────────────

def fetch_race_results(race_ids: list[str]) -> pd.DataFrame:
    """
    fukurou_keiba_v2.race_entries + horses から確定着順・払戻を取得する。
    race_ids は R-format（例: "2026041903R10"）で渡す。DB クエリ前に R を除去する。
    """
    if not race_ids:
        return pd.DataFrame()

    db_race_ids = [rid.replace("R", "") for rid in race_ids]
    logger.debug("[ReviewBuilder] DBクエリ race_id 例: %s", db_race_ids[:3])

    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT
                        re.race_id,
                        h.name          AS horse_name,
                        re.horse_number AS umaban,
                        re.confirmed_rank AS kakutei_chakujun,
                        re.win_odds     AS tansho_odds,
                        re.popularity   AS tansho_ninki
                    FROM race_entries re
                    JOIN horses h ON h.id = re.horse_id
                    WHERE re.race_id = ANY(%s)
                    ORDER BY re.race_id, re.horse_number
                    """,
                    (db_race_ids,),
                )
                rows = cur.fetchall()
    except Exception as exc:
        logger.error("[ReviewBuilder] DB クエリ失敗: %s", exc)
        return pd.DataFrame()

    if not rows:
        logger.warning("[ReviewBuilder] 結果0件 — 実績未登録か race_id 不一致の可能性")
        return pd.DataFrame()

    df = pd.DataFrame([dict(r) for r in rows])
    # DB形式（Rなし）→ R-format に戻す（CSVとマージ可能にする）
    df["race_id"] = df["race_id"].apply(_to_r_format)
    df["umaban"] = pd.to_numeric(df["umaban"], errors="coerce").astype("Int64")
    df["kakutei_chakujun"] = pd.to_numeric(df["kakutei_chakujun"], errors="coerce")
    df["tansho_odds"] = pd.to_numeric(df["tansho_odds"], errors="coerce")
    # 取消・除外（confirmed_rank=0）を NaN にして誤判定を防ぐ
    df.loc[df["kakutei_chakujun"] == 0, "kakutei_chakujun"] = float("nan")
    logger.info("[ReviewBuilder] DB取得: %d行", len(df))
    return df


# ── Step 3: 結合・的中判定 ────────────────────────────────────────────────────

def _row_mvp_score(row: pd.Series) -> float:
    if not row["is_win_hit"]:
        return 0.0
    yen   = max(int(row["tansho_yen"]), 100)
    bonus = _WIN_BONUS.get(int(row["ai_rank"]), 0.0)
    h_bonus = 2.0 if yen >= HIGH_ODDS_2 else 1.0 if yen >= HIGH_ODDS_1 else 0.0
    return bonus + h_bonus + math.log10(yen / 100)


def merge_and_judge(pred_df: pd.DataFrame, result_df: pd.DataFrame) -> pd.DataFrame:
    if result_df.empty:
        logger.warning("[ReviewBuilder] 実績データなし → 的中判定スキップ")
        pred_df = pred_df.copy()
        for col in ("kakutei_chakujun", "tansho_odds_raw"):
            pred_df[col] = float("nan")
        pred_df["tansho_yen"]   = 0
        pred_df["is_win_hit"]   = False
        pred_df["is_place_hit"] = False
        pred_df["mvp_score"]    = 0.0
        return pred_df

    _slim = ["race_id", "umaban", "horse_name", "kakutei_chakujun", "tansho_odds"]
    if "tansho_ninki" in result_df.columns:
        _slim.append("tansho_ninki")
    result_slim = result_df[_slim].rename(columns={"tansho_odds": "tansho_odds_raw"})

    merged = pred_df.merge(result_slim, on=["race_id", "umaban"], how="left")
    if "horse_name_x" in merged.columns:
        merged["horse_name"] = merged["horse_name_x"].fillna(merged.get("horse_name_y", ""))
        merged.drop(columns=["horse_name_x", "horse_name_y"], errors="ignore", inplace=True)

    merged["tansho_yen"]   = merged["tansho_odds_raw"].apply(_tansho_to_yen)
    merged["is_win_hit"]   = (
        merged["ai_rank"].isin(AI_MARKS) &
        merged["kakutei_chakujun"].notna() &
        (merged["kakutei_chakujun"] == 1)
    )
    merged["is_place_hit"] = (
        merged["ai_rank"].isin(AI_MARKS) &
        merged["kakutei_chakujun"].notna() &
        (merged["kakutei_chakujun"] <= PLACE_RANK_MAX)
    )
    merged["mvp_score"] = merged.apply(_row_mvp_score, axis=1)

    # ワンツー決着ボーナス
    ai_top2 = merged[
        merged["ai_rank"].isin(AI_MARKS) &
        merged["kakutei_chakujun"].notna() &
        (merged["kakutei_chakujun"] <= 2)
    ]
    wantsu = set(ai_top2.groupby("race_id").size()[lambda s: s >= 2].index)
    merged["mvp_score"] += merged["race_id"].map(lambda r: 1.5 if r in wantsu else 0.0)
    return merged


# ── 演出フラグ判定 ────────────────────────────────────────────────────────────

def compute_race_effect_type(race_rows: pd.DataFrame) -> str:
    judged = race_rows.dropna(subset=["kakutei_chakujun"])
    if judged.empty:
        return _EFFECT_MISS

    honmei = judged[judged["ai_rank"] == 1]
    taikou = judged[judged["ai_rank"] == 2]
    ana    = judged[judged["ai_rank"] == 3]
    any_ai = judged[judged["ai_rank"].isin(AI_MARKS)]

    if (len(any_ai) == 3
            and not honmei[honmei["kakutei_chakujun"] <= PLACE_RANK_MAX].empty
            and not taikou[taikou["kakutei_chakujun"] <= PLACE_RANK_MAX].empty
            and not ana[ana["kakutei_chakujun"] <= PLACE_RANK_MAX].empty):
        return _EFFECT_PERFECT_HIT

    honmei_win = honmei[honmei["kakutei_chakujun"] == 1]
    if not honmei_win.empty:
        yen = int(honmei_win.iloc[0].get("tansho_yen", 0))
        return _EFFECT_HONMEI_HIGH_DIVIDEND if yen >= HIGH_ODDS_1 else _EFFECT_HONMEI_WIN

    ana_win = ana[ana["kakutei_chakujun"] == 1]
    if not ana_win.empty:
        ana_yen = int(ana_win.iloc[0].get("tansho_yen", 0))
        return _EFFECT_HIGH_DIVIDEND_WIN if ana_yen >= ANA_HIGH_ODDS_THRESHOLD else _EFFECT_NORMAL_HIT

    if not ana[ana["kakutei_chakujun"] <= PLACE_RANK_MAX].empty:
        return _EFFECT_HOLE_PLACE
    if not any_ai[any_ai["kakutei_chakujun"] <= PLACE_RANK_MAX].empty:
        return _EFFECT_NORMAL_HIT
    return _EFFECT_MISS


# ── 統計・サマリー ────────────────────────────────────────────────────────────

def build_race_summary(merged: pd.DataFrame) -> list[dict]:
    summary = []
    for race_id, grp in merged.groupby("race_id"):
        first     = grp.iloc[0]
        race_info = str(first.get("race_info", "")).strip()
        venue     = _extract_venue(str(race_id), race_info)
        judged    = grp.dropna(subset=["kakutei_chakujun"])
        winner_rows = judged[judged["kakutei_chakujun"] == 1] if not judged.empty else pd.DataFrame()

        if winner_rows.empty:
            winner_umaban = winner_name = winner_tansho_yen = None
        else:
            w = winner_rows.iloc[0]
            winner_umaban     = int(w["umaban"]) if pd.notna(w.get("umaban")) else None
            winner_name       = str(w["horse_name"]).strip() if pd.notna(w.get("horse_name")) else None
            winner_tansho_yen = int(w["tansho_yen"]) if pd.notna(w.get("tansho_yen")) else None

        honmei_judged = judged[judged["ai_rank"] == 1]
        summary.append({
            "race_id":               str(race_id),
            "venue":                 venue,
            "race_info":             race_info,
            "winner_umaban":         winner_umaban,
            "winner_name":           winner_name,
            "winner_tansho_yen":     winner_tansho_yen,
            "honmei_is_winner":      not honmei_judged[honmei_judged["kakutei_chakujun"] == 1].empty,
            "honmei_place_hit":      not honmei_judged[honmei_judged["kakutei_chakujun"] <= PLACE_RANK_MAX].empty,
            "any_recommended_place": bool(grp[(grp["ai_rank"].isin(AI_MARKS)) & (grp["is_place_hit"])].shape[0] > 0),
            "effect_type":           compute_race_effect_type(grp),
        })
    return summary


def build_daily_stats(merged: pd.DataFrame) -> dict:
    judged    = merged[merged["kakutei_chakujun"].notna()]
    total_r   = merged["race_id"].nunique()
    judged_r  = judged["race_id"].nunique()
    honmei_wins = (
        merged[(merged["ai_rank"] == 1) & (merged["kakutei_chakujun"] == 1)]
        ["race_id"].nunique()
    )
    place_races = merged[merged["is_place_hit"]]["race_id"].nunique()
    win_rows    = merged[merged["is_win_hit"]]
    max_payout  = int(win_rows["tansho_yen"].max()) if not win_rows.empty else 0
    honmei_judged_r = merged[
        (merged["ai_rank"] == 1) & merged["kakutei_chakujun"].notna()
    ]["race_id"].nunique()
    win_rate   = honmei_wins / honmei_judged_r if honmei_judged_r else 0.0
    place_rate = (
        merged[(merged["ai_rank"] == 1) & (merged["kakutei_chakujun"] <= PLACE_RANK_MAX)]
        ["race_id"].nunique() / honmei_judged_r if honmei_judged_r else 0.0
    )
    recommend_place_rate = place_races / judged_r if judged_r else 0.0
    return {
        "total_races":           total_r,
        "judged_races":          judged_r,
        "honmei_wins":           honmei_wins,
        "recommend_place_races": place_races,
        "max_payout_yen":        max_payout,
        "honmei_win_rate":       round(win_rate,            3),
        "honmei_place_rate":     round(place_rate,          3),
        "recommend_place_rate":  round(recommend_place_rate, 3),
        "comment": (
            f"AI推奨馬（◎〇★）が{judged_r}レース中{place_races}レースで3着以内"
            f"（馬券内率{recommend_place_rate*100:.0f}%）。"
            f"◎本命1着{honmei_wins}回、単勝的中率{win_rate*100:.0f}%。"
        ),
    }


def build_daily_highlight(merged: pd.DataFrame) -> dict | None:
    """当日の特大ハイライト（15倍以上 or 7番人気以下穴馬的中）を1件選ぶ。"""
    place_hits = merged[
        merged["ai_rank"].isin(AI_MARKS) &
        merged["kakutei_chakujun"].notna() &
        (merged["kakutei_chakujun"] <= PLACE_RANK_MAX)
    ].copy()
    if place_hits.empty:
        return None

    best_idx = place_hits["tansho_yen"].idxmax()
    row      = place_hits.loc[best_idx]
    yen      = int(row["tansho_yen"])
    ninki_raw = row.get("tansho_ninki")
    ninki    = int(ninki_raw) if pd.notna(ninki_raw) else 0

    if not (yen >= HIGHLIGHT_ODDS_THRESHOLD or (ninki >= HIGHLIGHT_NINKI_THRESHOLD and ninki > 0)):
        return None

    race_id   = str(row["race_id"])
    race_info = str(row.get("race_info", "")).strip() or race_id
    venue     = _extract_venue(race_id, race_info)
    ai_rank   = int(row["ai_rank"]) if pd.notna(row.get("ai_rank")) else 0
    mark      = _MARK_LABEL.get(ai_rank, "")
    return {
        "race_id":    race_id,
        "race_info":  race_info,
        "venue":      venue,
        "horse_name": str(row["horse_name"]).strip(),
        "mark_label": mark,
        "tts_mark":   _TTS_MARK.get(mark, ""),
        "chakujun":   int(row["kakutei_chakujun"]),
        "tansho_yen": yen,
        "odds_x":     round(yen / 100, 1),
        "ninki":      ninki,
    }


# ── ハイライトレース選出 ──────────────────────────────────────────────────────

_GRADED_KEYWORDS: frozenset[str] = frozenset({
    "フェブラリーステークス", "高松宮記念", "桜花賞", "皐月賞",
    "天皇賞", "NHKマイルカップ", "ヴィクトリアマイル",
    "優駿牝馬", "東京優駿", "日本ダービー", "安田記念", "宝塚記念",
    "スプリンターズステークス", "秋華賞", "菊花賞",
    "エリザベス女王杯", "マイルチャンピオンシップ",
    "ジャパンカップ", "チャンピオンズカップ",
    "阪神ジュベナイルフィリーズ", "朝日杯フューチュリティステークス",
    "ホープフルステークス", "有馬記念",
    "京都記念", "フローラステークス", "読売マイラーズカップ",
    "目黒記念", "エプソムカップ", "ラジオNIKKEI賞", "函館記念",
    "関屋記念", "クイーンステークス", "小倉記念", "新潟記念",
    "セントライト記念", "ローズステークス", "神戸新聞杯",
    "毎日王冠", "府中牝馬ステークス", "スワンステークス",
    "アルゼンチン共和国杯", "武蔵野ステークス",
    "チューリップ賞", "阪急杯", "マーメイドステークス",
})


def _is_graded(race_info: str) -> bool:
    if re.search(r'G[123]|重賞|ＧⅠ|ＧⅡ|ＧⅢ', race_info):
        return True
    return any(k in race_info for k in _GRADED_KEYWORDS)


def _highlight_score(race_rows: pd.DataFrame) -> float:
    ai_rows   = race_rows[race_rows["ai_rank"].isin(AI_MARKS) & race_rows["kakutei_chakujun"].notna()]
    place_rows = ai_rows[ai_rows["kakutei_chakujun"] <= PLACE_RANK_MAX]
    if place_rows.empty:
        return -1.0
    hit_count = len(place_rows)
    max_yen   = max(int(place_rows["tansho_yen"].max()), 100)
    return hit_count * 100.0 + min(max_yen / 100, 99.0)


def select_highlights(merged: pd.DataFrame) -> list[str]:
    scores: dict[str, float] = {}
    graded: list[str] = []
    for race_id, grp in merged.groupby("race_id"):
        race_id_str = str(race_id)
        race_info   = str(grp.iloc[0].get("race_info", ""))
        if _is_graded(race_info):
            graded.append(race_id_str)
        scores[race_id_str] = _highlight_score(grp)

    scored_hits = [r for r, s in sorted(scores.items(), key=lambda x: x[1], reverse=True)
                   if s > 0 and r not in graded]
    result = graded + scored_hits[:max(0, MAX_HIGHLIGHTS - len(graded))]
    return result[:MAX_HIGHLIGHTS]


# ── シーン構築 ─────────────────────────────────────────────────────────────────

def _build_race_result_scene(race_id: str, merged: pd.DataFrame) -> dict:
    """portrait/landscape 用の race_result シーンを組み立てる。"""
    race_rows = merged[merged["race_id"] == race_id]
    first     = race_rows.iloc[0]
    race_info = str(first.get("race_info", "")).strip()
    venue     = _extract_venue(race_id, race_info)
    effect    = compute_race_effect_type(race_rows)

    ai_rows = (
        race_rows[race_rows["ai_rank"].isin(AI_MARKS) & race_rows["kakutei_chakujun"].notna()]
        .sort_values("ai_rank")
    )
    recommended: list[dict] = []
    for _, row in ai_rows.iterrows():
        ai_rank    = int(row["ai_rank"]) if pd.notna(row.get("ai_rank")) else 0
        chakujun   = int(row["kakutei_chakujun"]) if pd.notna(row.get("kakutei_chakujun")) else 0
        tansho_yen = int(row.get("tansho_yen", 0))
        recommended.append({
            "horse_name": str(row["horse_name"]).strip(),
            "mark_label": _MARK_LABEL.get(ai_rank, ""),
            "chakujun":   chakujun,
            "tansho_yen": tansho_yen,
            "odds_x":     round(tansho_yen / 100, 1),
            "ai_rank":    ai_rank,
        })

    result_rows = (
        race_rows.dropna(subset=["kakutei_chakujun"])
        .sort_values("kakutei_chakujun").head(8)
    )
    race_result: list[dict] = []
    for _, r in result_rows.iterrows():
        ai_rank_v = int(r["ai_rank"]) if pd.notna(r.get("ai_rank")) else 0
        race_result.append({
            "chakujun":   int(r["kakutei_chakujun"]),
            "horse_name": str(r["horse_name"]).strip(),
            "mark_label": _MARK_LABEL.get(ai_rank_v, "") if ai_rank_v in _MARK_LABEL else "",
            "tansho_yen": int(r["tansho_yen"]) if pd.notna(r.get("tansho_yen")) else 0,
        })

    if effect == _EFFECT_PERFECT_HIT and recommended:
        speech = _perfect_hit_speech(recommended, race_info, venue, race_id)
    else:
        hits_compat = [{**h, "is_main": i == 0} for i, h in enumerate(recommended)]
        speech = _highlight_speech(
            recommended[0] if recommended else None,
            hits_compat, race_info, venue, race_id,
        )

    return {
        "type":               "race_result",
        "race_id":            race_id,
        "race_info":          race_info,
        "venue":              venue,
        "effect_type":        effect,
        "recommended_horses": recommended,
        "race_result":        race_result,
        "speech_text":        speech,
        "display_text":       race_info,
        "audio_path":         "",
        "duration_seconds":   0.0,
    }


# ── スピーチ生成 ──────────────────────────────────────────────────────────────

def _highlight_speech(
    main: dict | None,
    hits: list[dict],
    race_info: str,
    venue: str = "",
    race_id: str = "",
) -> str:
    race_num = _extract_race_num(race_id) if race_id else 0
    if race_num and venue:
        tts_label = f"{venue}{race_num}レース"
    elif venue and venue not in race_info:
        tts_label = f"{venue}、{race_info}"
    else:
        tts_label = race_info

    if not main:
        return f"{tts_label}、惜しくも推奨馬は全て着外じゃったホー。"

    mark      = main["mark_label"]
    horse     = main["horse_name"]
    chak      = main["chakujun"]
    odds      = main["odds_x"]
    yen       = main["tansho_yen"]
    tts_mark  = _TTS_MARK.get(mark, "")
    mark_pfx  = f"{tts_mark}の" if tts_mark else ""
    yen_str   = f"単勝{yen:,}円" if yen > 0 else ""

    sub = next((h for h in hits if not h.get("is_main")), None)
    sub_str = ""
    if sub:
        sm = _TTS_MARK.get(sub["mark_label"], "")
        sm_pfx = f"{sm}の" if sm else ""
        sub_str = f"さらに{sm_pfx}{sub['horse_name']}も{sub['chakujun']}着に入ったホー！"

    if chak == 1 and odds >= 50:
        base = f"{tts_label}！大波乱じゃ！AI推奨の{horse}が{odds:.0f}倍の大穴を制したぞ！{yen_str}的中だホー！"
    elif chak == 1 and odds >= 20:
        base = f"{tts_label}！穴ヒットだホー！{mark_pfx}{horse}が{odds:.1f}倍の中穴、{yen_str}で的中じゃ！"
    elif chak == 1:
        base = f"{tts_label}！的中だぞ！{mark_pfx}{horse}が{odds:.1f}倍、{yen_str}で1着じゃ！"
    elif chak == 2:
        base = f"{tts_label}。惜しい！{mark_pfx}{horse}が2着に入ったホー！もう一歩だったぞ！"
    else:
        base = f"{tts_label}。{mark_pfx}{horse}が{chak}着！馬券内を確保したホー！"

    return base + sub_str


def _perfect_hit_speech(
    horses: list[dict],
    race_info: str,
    venue: str = "",
    race_id: str = "",
) -> str:
    race_num = _extract_race_num(race_id) if race_id else 0
    tts_label = f"{venue}{race_num}レース" if (race_num and venue) else race_info
    descs = []
    for h in horses[:3]:
        tts_mark = _TTS_MARK.get(h["mark_label"], "")
        descs.append(f"{tts_mark}の{h['horse_name']}が{h['chakujun']}着" if tts_mark else f"{h['horse_name']}が{h['chakujun']}着")
    return (
        f"なんと！{tts_label}で完璧な予想が的中じゃ！！"
        f"{'、'.join(descs)}！"
        "推奨馬3頭がすべて馬券内に来る完璧的中！！AIフクロウ博士の本領発揮だホー！！"
    )


def _intro_speech(day_label: str, stats: dict) -> str:
    j   = stats["judged_races"]
    r   = stats["recommend_place_races"]
    pct = stats["recommend_place_rate"] * 100
    return (
        f"{day_label}の的中ハイライトをお届けだホー！"
        f"今日は{j}レース中、推奨馬が{r}レースで馬券内を確保！"
        f"馬券内率{pct:.0f}パーセントじゃ！さっそく見ていくぞ！"
    )


def _stats_speech(day_label: str, stats: dict) -> str:
    j    = stats["judged_races"]
    r    = stats["recommend_place_races"]
    hw   = stats["honmei_wins"]
    pct  = stats["recommend_place_rate"] * 100
    wpct = stats["honmei_win_rate"] * 100
    mp   = stats["max_payout_yen"]
    mp_str = f"最高配当は{mp/100:.1f}倍（{mp:,}円）じゃ！" if mp > 0 else ""
    return (
        f"本日{day_label}の成績まとめだホー！"
        f"対象{j}レース中、推奨馬が{r}レースで馬券内！馬券内率{pct:.0f}パーセントだぞ！"
        f"本命の1着は{hw}回、単勝的中率{wpct:.0f}パーセント。"
        f"{mp_str}また来週も全力予想するからよろしく頼むぞ！"
    )


def _summary_speech(
    day_label: str,
    stats: dict,
    race_summary: list[dict],
    daily_highlight: dict | None = None,
) -> str:
    j         = stats["judged_races"]
    place_cnt = sum(1 for rs in race_summary if rs.get("honmei_place_hit"))
    pct       = round(place_cnt / j * 100) if j else 0
    mp        = stats["max_payout_yen"]
    mp_str    = f"最高払戻は{mp/100:.1f}倍（{mp:,}円）！" if mp > 0 else ""

    if daily_highlight:
        h        = daily_highlight
        tts_mark = h.get("tts_mark", "") or ""
        mark_str = f"{tts_mark}の" if tts_mark else ""
        chak_str = "1着" if h["chakujun"] == 1 else f"{h['chakujun']}着"
        ninki_str = f"{h['ninki']}番人気の" if h.get("ninki") else ""
        return (
            f"{day_label}の全レースまとめだホー！"
            f"本日の最大の見どころは{h['race_info']}、"
            f"{ninki_str}{mark_str}{h['horse_name']}が{h['odds_x']:.1f}倍で{chak_str}じゃ！"
            f"AIの眼力、見せつけたぞ！また来週もよろしく頼むぞ！"
        )
    return (
        f"{day_label}の全レースまとめだホー！"
        f"対象{j}レース中、本命◎が{place_cnt}レースで3着以内——馬券内率{pct}パーセントじゃ！"
        f"{mp_str}また来週もよろしく頼むぞ！"
    )


def _outro_speech(daily_highlight: dict | None = None) -> str:
    if daily_highlight:
        h        = daily_highlight
        tts_mark = h.get("tts_mark", "") or ""
        mark_str = f"{tts_mark}の" if tts_mark else ""
        chak_str = "1着" if h["chakujun"] == 1 else f"{h['chakujun']}着"
        ninki_str = f"{h['ninki']}番人気の" if h.get("ninki") else ""
        return (
            f"今週の振り返りはここまでだホー！"
            f"それにしても{h['race_info']}の{ninki_str}{mark_str}{h['horse_name']}、"
            f"{h['odds_x']:.1f}倍で{chak_str}は見事じゃったぞ！"
            f"AIの神髄を見たか！"
            f"来週の予想もお楽しみに！チャンネル登録と高評価もよろしく頼むぞ！"
        )
    return (
        "今週の振り返りはここまでだホー！"
        "来週の予想もお楽しみに！チャンネル登録と高評価もよろしく頼むぞ！"
    )


# ── TTS（VOICEVOX）音声付与 ──────────────────────────────────────────────────

def _attach_audio(scenes: list[dict], date_str: str) -> None:
    """各シーンに VOICEVOX 音声を付与して audio_path / duration_seconds を更新する。"""
    from api_v1.services.voicevox_client import check_connection, generate_audio

    if not check_connection():
        logger.warning("[ReviewBuilder] VOICEVOX 未起動 — 音声生成スキップ")
        return

    _AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    for i, scene in enumerate(scenes):
        text = scene.get("speech_text", "").strip()
        text = text.replace("振り返り", "ふりかえり")
        if not text:
            continue
        scene_type = scene.get("type", f"scene{i}")
        wav_path   = _AUDIO_DIR / f"{date_str}_s{i:02d}_{scene_type}.wav"
        duration, ok = generate_audio(text, wav_path)
        if ok:
            try:
                rel = str(wav_path.relative_to(_OWL_PUBLIC)).replace("\\", "/")
            except ValueError:
                rel = f"dynamic_data/short_review/audio/{wav_path.name}"
            scene["audio_path"]       = rel
            scene["duration_seconds"] = round(duration + AUDIO_BUFFER_SEC, 2)
        else:
            logger.warning("[ReviewBuilder] 音声生成失敗: %s", wav_path.name)


# ── landscape/portrait 共用 timeline 生成 ────────────────────────────────────

def build_landscape_timeline(
    merged:    pd.DataFrame,
    date_str:  str,
    day_label: str,
    use_tts:   bool = False,
) -> Path:
    """
    review_landscape_timeline_{date_str}.json を生成して _REVIEW_DATA に保存する。
    RaceReviewPortrait / RaceReviewLandscape 両 Composition で読み込む共通フォーマット。
    """
    highlight_ids   = select_highlights(merged)
    logger.info("[ReviewBuilder] %s ハイライト %d本: %s", day_label, len(highlight_ids), highlight_ids)

    stats           = build_daily_stats(merged)
    race_summary    = build_race_summary(merged)
    daily_highlight = build_daily_highlight(merged)
    if daily_highlight:
        logger.info("[ReviewBuilder] 特大ハイライト: %s %s %.1fx",
                    daily_highlight["race_info"], daily_highlight["horse_name"], daily_highlight["odds_x"])

    scenes: list[dict] = [
        {
            "type":             "review_intro",
            "speech_text":      _intro_speech(day_label, stats),
            "display_text":     f"{day_label} 的中ハイライト",
            "day_label":        day_label,
            "audio_path":       "",
            "duration_seconds": 0.0,
        }
    ]
    for race_id in highlight_ids:
        scenes.append(_build_race_result_scene(race_id, merged))

    scenes += [
        {
            "type":             "daily_stats",
            "speech_text":      _stats_speech(day_label, stats),
            "display_text":     f"{day_label} 成績",
            "stats":            stats,
            "audio_path":       "",
            "duration_seconds": 0.0,
        },
        {
            "type":             "summary",
            "speech_text":      _summary_speech(day_label, stats, race_summary, daily_highlight),
            "display_text":     "",
            "audio_path":       "",
            "duration_seconds": 0.0,
        },
        {
            "type":             "outro",
            "speech_text":      _outro_speech(daily_highlight),
            "display_text":     "次回の予想もお楽しみに！",
            "audio_path":       "",
            "duration_seconds": 0.0,
        },
    ]

    if use_tts:
        logger.info("[ReviewBuilder] VOICEVOX 音声生成開始 (%d シーン)", len(scenes))
        _attach_audio(scenes, f"{date_str}_ls")

    timeline = {
        "video_type":      "landscape_review",
        "date":            _datestr_to_iso(date_str),
        "day_label":       day_label,
        "generated_at":    datetime.now().isoformat(timespec="seconds"),
        "daily_stats":     stats,
        "race_summary":    race_summary,
        "daily_highlight": daily_highlight,
        "scenes":          scenes,
    }

    _REVIEW_DATA.mkdir(parents=True, exist_ok=True)
    out_path = _REVIEW_DATA / f"review_landscape_timeline_{date_str}.json"
    import json
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(timeline, f, ensure_ascii=False, indent=2)
    logger.info("[ReviewBuilder] 出力: %s", out_path)
    return out_path


# ── エントリーポイント ──────────────────────────────────────────────────────────

def run_one_day(
    date_str:  str,
    day_label: str,
    use_tts:   bool = False,
) -> Path | None:
    """1日分の振り返り timeline JSON を生成して出力パスを返す。"""
    try:
        pred_df = load_predictions(date_str)
    except FileNotFoundError as exc:
        logger.error("[ReviewBuilder] %s: %s", day_label, exc)
        return None

    pred_df = pred_df[pred_df["race_id"].apply(_extract_date) == date_str].copy()
    if pred_df.empty:
        logger.error("[ReviewBuilder] %s: %s のレースデータなし", day_label, date_str)
        return None

    race_ids  = pred_df["race_id"].dropna().unique().tolist()
    result_df = fetch_race_results(race_ids)
    merged    = merge_and_judge(pred_df, result_df)
    return build_landscape_timeline(merged, date_str, day_label, use_tts=use_tts)


def run(
    race_date: str,
    day:       str = "both",
    use_tts:   bool = False,
) -> list[Path]:
    """
    土日どちらか（または両方）の振り返り timeline JSON を生成する。

    Args:
        race_date: 日曜日の日付 YYYYMMDD
        day:       "sat" | "sun" | "both"
        use_tts:   VOICEVOX 音声生成フラグ

    Returns:
        生成した JSON パスのリスト
    """
    sun_dt = datetime.strptime(race_date, "%Y%m%d")
    sat_dt = sun_dt - timedelta(days=1)
    sun_str = sun_dt.strftime("%Y%m%d")
    sat_str = sat_dt.strftime("%Y%m%d")

    results: list[Path] = []
    if day in ("sat", "both"):
        p = run_one_day(sat_str, "土曜日", use_tts=use_tts)
        if p:
            results.append(p)
    if day in ("sun", "both"):
        p = run_one_day(sun_str, "日曜日", use_tts=use_tts)
        if p:
            results.append(p)
    return results
