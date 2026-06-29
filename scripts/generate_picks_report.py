"""
scripts/generate_picks_report.py
=================================
週末レースの予想一覧HTMLレポートを生成する。

出力: data/output/tipster/picks_report.html

実行:
  py -3 scripts/generate_picks_report.py

構成（Phase 2 セグメント別パターン）:
  - 一押し (S): ダート中距離(>1400m) & 坂あり会場 のみ（S-1/S-2パターン相当）
  - 二押し (B): ダート中距離(>1400m) 非坂あり のみ（B-2パターン相当）
  - 三押し (暫定): 上記以外の全レース — honmei_v6条件クリア数上位
  - 穴推奨: 芝短距離(≤1400m) & 野芝会場 のみ（anaba確定パターン相当）
  - 推奨なし: 条件クリア馬がいないレース
  - 各推奨馬に「クリアした条件リスト + reason」を表示
  - 各条件に「なぜ効くか」の解説を付与（静的テキスト）
"""
from __future__ import annotations

import copy
import html
import json
import sys
from datetime import date as _date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg2

from api_v2.routers.races import get_weekend_races
from shared.config import DB_V2, DB_JVDL
import tipster.conditions_v2  # noqa: F401 — v2_* 条件を CONDITION_REGISTRY に登録するため先にimport
from tipster.engine import evaluate_race_context, fetch_race_context, load_strategy
from tipster.models import HorseEvaluation, RaceContext, RaceEvaluation

# ─── 定数 ────────────────────────────────────────────────────────────────────

_STRATEGY_HONMEI = Path(__file__).parent.parent / "tipster" / "strategies" / "honmei_v7.json"
_STRATEGY_ANABA  = Path(__file__).parent.parent / "tipster" / "strategies" / "anaba_v5.json"
_STRATEGY_S1     = Path(__file__).parent.parent / "tipster" / "strategies" / "s1_pattern.json"
_OUTPUT_PATH     = Path("data/output/tipster/picks_report.html")

_VENUE_NAME = {
    "01": "札幌", "02": "函館", "03": "福島", "04": "新潟", "05": "東京",
    "06": "中山", "07": "中京", "08": "京都", "09": "阪神", "10": "小倉",
}

# ─── Phase 2 セグメント分類 ───────────────────────────────────────────────────

# 坂あり会場: 福島(03)・東京(05)・中山(06)・中京(07)・阪神(09)
_HILL_VENUES = {"03", "05", "06", "07", "09"}
# 洋芝会場: 札幌(01)・函館(02) → 野芝 = 上記以外
_YOHI_VENUES = {"01", "02"}


_SYUBETSU_TO_CLASS_LEVEL: dict[str, int] = {
    # JRA race_syubetsu_code → class_level（新馬=1〜G1=10）
    # 推定マッピング（JV-Data Vol.4 Table K-8 参照）
    "11": 2,  # 未勝利
    "12": 3,  # 1勝クラス
    "13": 4,  # 2勝クラス
    "14": 5,  # 3勝クラス
    "15": 5,  # 3勝クラス（variant）
    "18": 6,  # オープン
    "19": 7,  # Listed
    "20": 8,  # G3
    "21": 9,  # G2
    "22": 10, # G1
}


def _fetch_race_meta_v2(race_ids: list[str]) -> dict[str, dict]:
    """v2 DBからレースの keibajo_code / track_code / distance / class_level を一括取得する。

    track_code 先頭: '1'=芝, '2'=ダート, '5'=障害
    keibajo_code: '01'-'10'（place_code と同義）
    """
    result: dict[str, dict] = {}
    if not race_ids:
        return result
    conn = psycopg2.connect(**DB_V2)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, keibajo_code, track_code, distance, race_syubetsu_code"
            " FROM races WHERE id = ANY(%s)",
            (race_ids,),
        )
        for row in cur.fetchall():
            rid, keibajo, track, dist, syubetsu = row
            surface = (
                "芝"    if (track or "")[:1] == "1" else
                "ダート" if (track or "")[:1] == "2" else
                ""
            )
            class_level = _SYUBETSU_TO_CLASS_LEVEL.get(str(syubetsu or ""))
            result[str(rid)] = {
                "keibajo":     str(keibajo or ""),
                "surface":     surface,
                "distance":    int(dist) if dist else 0,
                "class_level": class_level,
            }
        cur.close()
    finally:
        conn.close()
    return result


def _fetch_jockey_entries(race_ids: list[str]) -> dict[tuple, dict]:
    """V2 DBのrace_entriesから騎手コードを一括取得する。

    Returns: {(race_id, horse_id): {"jockey_id": str, "prev_jockey_id": str|None}}
    """
    result: dict[tuple, dict] = {}
    if not race_ids:
        return result
    conn = psycopg2.connect(**DB_V2)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT race_id, horse_id, jockey_cd, jockey_cd_before"
            " FROM race_entries WHERE race_id = ANY(%s)",
            (race_ids,),
        )
        for row in cur.fetchall():
            rid, hid, jcd, jcd_before = row
            result[(str(rid), str(hid))] = {
                "jockey_id": str(jcd) if jcd and str(jcd) not in ("", "00000") else None,
                "prev_jockey_id": str(jcd_before) if jcd_before and str(jcd_before) not in ("", "00000") else None,
            }
        cur.close()
    finally:
        conn.close()
    return result


def _fetch_jockey_yr_wins(kishu_codes: set[str]) -> dict[str, int]:
    """JVDLのrace_entries_v2から2026年の年間勝利数を集計する。

    Returns: {kishu_code: yr_wins}
    """
    if not kishu_codes:
        return {}
    conn = psycopg2.connect(**DB_JVDL)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT kishu_code, COUNT(*) AS yr_wins"
            " FROM race_entries_v2"
            " WHERE kakutei_chakujun = 1"
            "   AND LEFT(race_id, 4) = '2026'"
            "   AND kishu_code != '00000'"
            " GROUP BY kishu_code",
        )
        result = {row[0]: int(row[1]) for row in cur.fetchall()}
        cur.close()
    finally:
        conn.close()
    return result


def _fetch_past_class_levels(past_race_ids: set[str]) -> dict[str, int | None]:
    """JVDLのraces_v2から直近レースのクラスレベルを取得する。

    engine._fetch_past_race_extra はJVDL races（12桁ID, max 2026-06-14）しか参照しないため、
    直近4週のレース（races_v2にのみ存在する16桁ID）では class_level=None になる。
    本関数でその欠損を補完する。

    Returns: {race_id_16char: class_level}
    """
    if not past_race_ids:
        return {}
    conn = psycopg2.connect(**DB_JVDL)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT race_id, kyoso_shubetsu FROM races_v2 WHERE race_id = ANY(%s)",
            (list(past_race_ids),),
        )
        result: dict[str, int | None] = {}
        for race_id, kyoso_shubetsu in cur.fetchall():
            cl = _SYUBETSU_TO_CLASS_LEVEL.get(str(kyoso_shubetsu or "").strip())
            result[str(race_id)] = cl
        cur.close()
    finally:
        conn.close()
    return result


def _fetch_f3_rank_pct_for_past_races(
    past_race_ids: set[str],
) -> dict[str, dict[str, float]]:
    """過去走のf3_time_rank_pct (blood_no → rank_pct) をまとめて取得する。

    JVDL DB の race_entries_v2.kohan_3f（上がり3F）からレース内順位パーセンタイルを計算。
    blood_no はエンジン horse_id と同じ形式。race_id は16文字IDで直接照合。

    Returns: {race_id: {blood_no: f3_rank_pct}}
    """
    if not past_race_ids:
        return {}
    try:
        conn = psycopg2.connect(**DB_JVDL)
        cur = conn.cursor()
        cur.execute(
            "SELECT race_id, blood_no, kohan_3f"
            " FROM race_entries_v2"
            " WHERE race_id = ANY(%s) AND kohan_3f IS NOT NULL AND kohan_3f > 0",
            (list(past_race_ids),),
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()

        # レースごとにf3_rank_pctを計算
        races_f3: dict[str, dict[str, float]] = {}
        for race_id, blood_no, kohan_3f in rows:
            races_f3.setdefault(str(race_id), {})[str(blood_no)] = float(kohan_3f)

        result: dict[str, dict[str, float]] = {}
        for rid, horse_f3s in races_f3.items():
            n = len(horse_f3s)
            sorted_times = sorted(horse_f3s.values())
            rank_pct_map: dict[str, float] = {}
            for hid, t in horse_f3s.items():
                rank = sorted_times.index(t) + 1  # 1-based, ascending = 最速
                rank_pct_map[hid] = rank / n
            result[rid] = rank_pct_map

        return result
    except Exception:
        return {}


def _fetch_training_for_race(race_ctx: RaceContext) -> dict:
    """TR-1 調教評価: レース内全馬の調教ランキングを計算する。

    conditions_tr1._fetch_training_rows と同一クエリを使用。

    Returns: {blood_no: RankedHorse} ※ データ不足時は空 dict
    """
    try:
        from ml.db import engine as _ml_engine  # type: ignore
        from sqlalchemy import text as _text
        from tipster.training_ranker import SlopeRow, WoodRow  # noqa: F811
        from tipster.training_ranker import load_config as _load_tr_config
        from tipster.training_ranker import rank_horses_by_training

        race_date_raw = race_ctx.race_date  # "YYYY-MM-DD" or None
        if not race_date_raw:
            return {}
        race_date = race_date_raw.replace("-", "")
        blood_nos = [h.horse_id for h in race_ctx.horses]
        if not blood_nos:
            return {}

        d = _date(int(race_date[:4]), int(race_date[4:6]), int(race_date[6:8]))
        since = (d - timedelta(days=30)).strftime("%Y%m%d")

        slope_by: dict[str, list] = {bn: [] for bn in blood_nos}
        wood_by:  dict[str, list] = {bn: [] for bn in blood_nos}

        with _ml_engine.connect() as conn:
            for r in conn.execute(
                _text("SELECT blood_no, chokyo_date, chokyo_time, center_cd,"
                      " time_4f, lap_l4_l3, lap_l3_l2, lap_l2_l1, lap_l1"
                      " FROM training_slope"
                      " WHERE blood_no = ANY(:bns)"
                      "   AND chokyo_date >= :since AND chokyo_date <= :until"),
                {"bns": blood_nos, "since": since, "until": race_date},
            ).fetchall():
                slope_by.setdefault(r[0], []).append(
                    SlopeRow(blood_no=r[0], chokyo_date=r[1], chokyo_time=r[2],
                             center_cd=r[3], time_4f=r[4], lap_l4_l3=r[5],
                             lap_l3_l2=r[6], lap_l2_l1=r[7], lap_l1=r[8])
                )
            for r in conn.execute(
                _text("SELECT blood_no, chokyo_date, chokyo_time, time_5f, lap_l2_l1, lap_l1"
                      " FROM training_wood"
                      " WHERE blood_no = ANY(:bns)"
                      "   AND chokyo_date >= :since AND chokyo_date <= :until"),
                {"bns": blood_nos, "since": since, "until": race_date},
            ).fetchall():
                wood_by.setdefault(r[0], []).append(
                    WoodRow(blood_no=r[0], chokyo_date=r[1], chokyo_time=r[2],
                            time_5f=r[3], lap_l2_l1=r[4], lap_l1=r[5])
                )

        config = _load_tr_config()
        ranked = rank_horses_by_training(
            blood_nos=blood_nos,
            slope_rows_by_horse=slope_by,
            wood_rows_by_horse=wood_by,
            race_date=race_date,
            config=config,
        )
        return {r.blood_no: r for r in ranked}
    except Exception:
        return {}


def _fetch_baba_affinity(blood_nos: list[str], surface: str) -> dict[str, dict[str, tuple[int, int]]]:
    """JVDL から馬場状態別のアフィニティ（出走数・複勝圏内数）を取得する。

    surface は "芝" or "ダート"。

    Returns: {blood_no: {"良": (runs, placed), "稍重": (runs, placed), ...}}
    """
    if not blood_nos:
        return {}
    if surface == "芝":
        baba_col = "shiba_baba_code"
    elif surface == "ダート":
        baba_col = "dirt_baba_code"
    else:
        return {}

    conn = psycopg2.connect(**DB_JVDL)
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT e.blood_no, v.{baba_col},"
            "       COUNT(*) AS runs,"
            "       SUM(CASE WHEN e.kakutei_chakujun BETWEEN 1 AND 3 THEN 1 ELSE 0 END) AS placed"
            " FROM race_entries_v2 e"
            " JOIN races_v2 v ON e.race_id = v.race_id"
            " WHERE e.blood_no = ANY(%s)"
            f"   AND v.{baba_col} IN ('1','2','3','4')"
            "   AND e.kakutei_chakujun > 0"
            f" GROUP BY e.blood_no, v.{baba_col}",
            (blood_nos,),
        )
        result: dict[str, dict[str, tuple[int, int]]] = {}
        for blood_no, baba_code, runs, placed in cur.fetchall():
            label = _BABA_CODE_TO_LABEL.get(str(baba_code))
            if label:
                result.setdefault(str(blood_no), {})[label] = (int(runs), int(placed))
        cur.close()
    finally:
        conn.close()
    return result


def _baba_affinity_score(affinity: dict[str, tuple[int, int]], baba: str) -> float:
    """馬場状態アフィニティの強化スコアを返す。

    ボーナス/ペナルティの設計:
      ≥3走 かつ 複勝率 ≥50% → +3.5（明確に得意）
      ≥3走 かつ 複勝率 35-50% → +1.5（やや得意）
      ≥3走 かつ 複勝率 15-35% → -1.0（やや不得意）
      ≥3走 かつ 複勝率 <15%   → -3.5（明確に苦手）
      1-2走              → ±0.5（小さな影響のみ）
      データなし         → 0.0（中立）
    """
    entry = affinity.get(baba)
    if not entry:
        return 0.0
    runs, placed = entry
    if runs == 0:
        return 0.0
    rate = placed / runs
    if runs >= _BABA_AFFINITY_MIN_RUNS:
        if rate >= 0.50:
            return 3.5
        if rate >= 0.35:
            return 1.5
        if rate >= 0.15:
            return -1.0
        return -3.5
    # 1-2 runs: small effect
    return 0.5 if rate >= 0.50 else -0.5


def _rerank_by_baba(
    candidates: list[HorseEvaluation],
    affinity_map: dict[str, dict[str, tuple[int, int]]],
    baba: str,
) -> list[HorseEvaluation]:
    """候補馬を馬場状態アフィニティで再ランキングする。

    ソートキー: 単一の合成スコア = clear_count * 1.5 + total_score + baba_score
    clear_count をプライマリとしない（馬場アフィニティで逆転可能にする）。
    """
    def sort_key(h: HorseEvaluation) -> float:
        baba_sc = _baba_affinity_score(affinity_map.get(h.horse_id, {}), baba)
        return -(h.clear_count * 1.5 + h.total_score + baba_sc)

    return sorted(candidates, key=sort_key)


def _race_tier(race_meta: dict) -> str:
    """レースのPhase 2ティアを返す（v2 DBメタデータ使用）。
    S:     ダート中距離(>1400m) & 坂あり → 一押し
    B:     ダート中距離(>1400m) 非坂あり → 二押し
    anaba: 芝短距離(≤1400m) & 野芝      → 三押し + 穴推奨
    other: 上記以外                      → 三押し(暫定)
    """
    surface  = race_meta.get("surface", "")
    distance = race_meta.get("distance", 0)
    place    = race_meta.get("keibajo", "")

    is_dirt_mid   = surface == "ダート" and distance > 1400
    is_turf_short = surface == "芝" and distance <= 1400
    is_hill       = place in _HILL_VENUES
    is_nozhi      = place not in _YOHI_VENUES  # 野芝 = 洋芝以外

    if is_dirt_mid and is_hill:
        return "S"
    if is_dirt_mid:
        return "B"
    if is_turf_short and is_nozhi:
        return "anaba"
    return "other"


_TIER_LABEL: dict[str, str] = {
    "S":     "一押し",
    "B":     "二押し",
    "anaba": "三押し",
    "other": "三押し",
}
_TIER_COLOR: dict[str, str] = {
    "S":     "#e74c3c",
    "B":     "#e67e22",
    "anaba": "#f1c40f",
    "other": "#f1c40f",
}
_ANABA_COLOR = "#9b59b6"

# ─── 条件ラベル / なぜ効くか ──────────────────────────────────────────────────

_COND_LABEL: dict[str, str] = {
    "v2_past_margin":       "前走好走歴（着差≤1秒）",
    "v2_race_quality":      "前走レースレベル",
    "v2_class_change":      "クラス変化",
    "v2_jockey_positive":   "騎手（継続/有力）",
    "v2_weight_favor":      "斤量軽減",
    "v2_interval_optimal":  "適切間隔（2〜4週）",
    "v2_surface_history":   "同馬場好走歴",
    "v2_distance_match":    "距離適性",
    "v2_baba_track_record":  "馬場別過去成績",
    "v2_sire_baba_fit":      "種牡馬馬場適性",
    "v2_heavy_track_stamina": "道悪スタミナ",
    "cond_upset_score":     "穴候補スコア",
    "cond_low_popularity":  "人気薄",
    "cond_surface_ok":      "同馬場好走歴",
    "cond_f3_top":          "上がり上位33%",
    "cond_class_ok":        "クラス維持/降級",
    "cond_interval_ok":     "中2〜4週",
    "cond_sire_venue":      "種牡馬同会場適性",
    "cond_sire_surface":    "種牡馬馬場適性",
    "cond_margin":          "前走着差",
    "cond_hill_fit":        "坂あり適性",
    "cond_straight_fit":    "直線適性",
    "cond_weight_ok":       "斤量条件",
    # S-1パターン専用条件ラベル
    "v2_f3_top":            "前走上がり上位33%",
    "v2_hill_fit":          "坂あり競馬場適性",
    "v2_sire_venue":        "種牡馬会場適性",
}

_COND_WHY: dict[str, str] = {
    "v2_past_margin": (
        "前走で勝ち馬から1秒以内に入った馬は「能力的に惜しい負け」をしている。"
        "次走で展開が向けばそのまま勝ちに直結する最も信頼できる好走指標。"
    ),
    "v2_race_quality": (
        "前走の対戦相手が次走で複勝圏に入れるということは、そのレース自体がレベルの高い一戦だった証明。"
        "強い相手と戦った馬は着順以上の実力を持っていることが多い。"
    ),
    "v2_class_change": (
        "降級馬はクラス適正による実力差が生じやすい。"
        "前走のクラスで通用しなかった要素がリセットされ、本来の能力が発揮されやすい。"
    ),
    "v2_jockey_positive": (
        "継続騎乗は調教師・騎手の馬への理解度が高い状態。"
        "有力騎手への乗替りは陣営の積極的な勝ち意欲を示すサイン。"
    ),
    "v2_weight_favor": (
        "斤量は直接的な有利不利要因。"
        "0.5kg以上の軽減は特にマイル以下の短距離・マイル戦で効果が大きい。"
    ),
    "v2_interval_optimal": (
        "中2〜4週（15〜28日）は疲労が抜けつつも調子が維持されている黄金期間。"
        "長期休養明けや中1週の馬との比較で安定感が高い。"
    ),
    "v2_surface_history": (
        "芝/ダートの適性は過去成績が最も正直に示す。"
        "同馬場で複勝圏に入った実績がある馬は、馬場への適性が証明済み。"
    ),
    "v2_distance_match": (
        "距離適性も過去の好走距離が最も信頼できる指標。"
        "得意距離帯での好走歴は繰り返されやすい。"
    ),
    "cond_surface_ok":   "同馬場での好走歴あり。馬場適性を実績で証明している。",
    "cond_f3_top":       "前走の上がり上位33%は末脚の安定性を示す。次走も同様の脚が使えれば好走。",
    "cond_class_ok":     "クラス維持または降級。能力的な余裕がある状態。",
    "cond_interval_ok":  "中2〜4週。疲労回復と調子維持のバランスが最良の間隔。",
    "cond_sire_venue":   "父馬のこの会場での複勝率が全体平均より高い。コース相性の遺伝的要素。",
    "cond_sire_surface": "父馬のこの馬場（芝/ダート）での成績が優秀。馬場適性の遺伝的要素。",
    "cond_margin":       "前走勝ち馬差≤0.5秒。惜敗馬で次走で好走しやすい位置にある。",
    "cond_hill_fit":     "坂あり競馬場での好走歴あり。スタミナ・パワー型への適性。",
    "cond_weight_ok":    "斤量条件クリア。過去の斤量との比較で不利でない。",
    # S-1パターン専用条件解説
    "v2_f3_top": (
        "前走の上がり3Fがレース内上位33%以内。末脚の安定性を示す最も信頼できる好走指標。"
        "ダート中距離では上がり能力が着順に直結する。"
    ),
    "v2_hill_fit": (
        "坂あり競馬場（福島・東京・中山・中京・阪神）での過去3走以内に3着以内の好走歴。"
        "スタミナ・パワーが必要な坂コースへの適性を実績で証明している。"
    ),
    "v2_sire_venue": (
        "父馬の今回会場での複勝率が全体平均より高い（出走10頭以上で統計的に有意）。"
        "コース適性には遺伝的要素があり、同会場での好走歴は産駒に継承されやすい。"
    ),
}


# ─── 馬場状態定数 ────────────────────────────────────────────────────────────

_BABA_CODE_TO_LABEL: dict[str, str] = {"1": "良", "2": "稍重", "3": "重", "4": "不良"}
_BABA_CONDITIONS: list[str] = ["良", "稍重", "重", "不良"]
_BABA_AFFINITY_MIN_RUNS = 3    # 信頼できるアフィニティに必要な最低出走数
_BABA_AFFINITY_WEIGHT   = 1.5  # アフィニティボーナスの重み

# BET-7 馬場別バックテスト結果に基づくROI警告 (良: 74.4%, 稍重: 46.0%, 重: 33.3%, 不良: 49.7%)
_BABA_ROI_WARNING: dict[str, str] = {
    "良":  "",
    "稍重": "ROI注意（BT: 46.0%）",
    "重":  "ROI低調（BT: 33.3%）",
    "不良": "サンプル少（BT: 49.7%）",
}


def _make_baba_strategy(base_strat, baba: str):
    """baba条件のparams.babaを指定馬場に上書きしたStrategyのコピーを返す。"""
    strat = base_strat.model_copy(deep=True)
    for cond in strat.conditions:
        if cond.id in ("v2_baba_track_record", "v2_sire_baba_fit", "v2_heavy_track_stamina"):
            cond.params = {**cond.params, "baba": baba}
    return strat


# ─── ユーティリティ ───────────────────────────────────────────────────────────

def _cond_label(cond_id: str) -> str:
    return _COND_LABEL.get(cond_id, cond_id)


def _cond_why(cond_id: str) -> str:
    return _COND_WHY.get(cond_id, "")


def _esc(v) -> str:
    return html.escape(str(v)) if v is not None else ""


# ─── HTML 生成 ────────────────────────────────────────────────────────────────

def _horse_card(
    horse: HorseEvaluation,
    label: str,
    color: str,
    race_eval: RaceEvaluation,
    strategy=None,
    tr1_horse=None,
    baba_affinity: dict | None = None,
) -> str:
    """馬1頭分のカードHTMLを生成する。

    tr1_horse: RankedHorse | None — TR-1 調教評価結果
    baba_affinity: {"良": (runs, placed), ...} | None — 馬場アフィニティ（参考表示のみ。ランク判定に影響しない）
    """
    name = _esc(horse.horse_name or horse.horse_id)
    strat_cond_ids: list[str] = (
        [c.id for c in strategy.conditions if c.enabled] if strategy else []
    )

    lines = [
        f'<div class="horse-card" style="border-left: 4px solid {color}">',
        f'  <div class="horse-header">',
        f'    <span class="rank-badge" style="background:{color}">{_esc(label)}</span>',
        f'    <span class="horse-name">{name}</span>',
        f'    <span class="scores">',
        f'      クリア数: <strong>{horse.clear_count}</strong> / ',
        f'      総合スコア: <strong>{horse.total_score:.1f}</strong> / ',
        f'      AIスコア: <strong>{horse.ai_score:.1f}</strong>',
        f'    </span>',
        f'  </div>',
    ]

    if horse.conditions:
        lines.append('  <div class="cond-list">')
        for i, cond_result in enumerate(horse.conditions):
            if cond_result.passed is True:
                icon = "✅"
                cls = "cond-pass"
            elif cond_result.passed is False:
                icon = "❌"
                cls = "cond-fail"
            else:
                icon = "⚪"
                cls = "cond-none"

            cond_id   = strat_cond_ids[i] if i < len(strat_cond_ids) else ""
            cond_name = _esc(_cond_label(cond_id)) if cond_id else ""
            reason    = _esc(cond_result.reason) if cond_result.reason else ""
            why_text  = _esc(_cond_why(cond_id)) if cond_id else ""
            lines.append(f'    <div class="cond-row {cls}">')
            lines.append(f'      <span class="cond-icon">{icon}</span>')
            if cond_name:
                lines.append(f'      <span class="cond-label">{cond_name}</span>')
            lines.append(f'      <span class="cond-reason">{reason}</span>')
            if why_text and cond_result.passed is True:
                lines.append(f'      <span class="cond-why">{why_text}</span>')
            lines.append(f'    </div>')
        lines.append('  </div>')
    else:
        lines.append('  <div class="cond-list"><span class="no-data">条件データなし</span></div>')

    # ─── TR-1 調教評価セクション ───────────────────────────────────────────
    if tr1_horse is not None:
        cond_lbl = _esc(tr1_horse.condition_label)
        tb = f"{tr1_horse.tiebreak_time_sec:.1f}秒" if tr1_horse.tiebreak_time_sec is not None else "-"
        lines += [
            '  <div class="tr1-section">',
            f'    <span class="tr1-badge">調教 {cond_lbl}</span>',
            f'    <span class="tr1-rank">TR-1ランク {tr1_horse.rank}位</span>',
            f'    <span class="tr1-time">基準タイム {_esc(tb)}</span>',
            '  </div>',
        ]
    else:
        lines.append('  <div class="tr1-section tr1-none"><span>調教データなし</span></div>')

    # ─── 馬場別成績セクション（参考情報。ランク判定に影響しない）────────────
    baba_items = []
    for baba in _BABA_CONDITIONS:
        entry = (baba_affinity or {}).get(baba)
        if entry:
            runs, placed = entry
            pct = f"{placed/runs:.0%}" if runs > 0 else "-"
            rate = placed / runs if runs > 0 else 0.0
            if runs >= _BABA_AFFINITY_MIN_RUNS:
                item_cls = "affinity-good" if rate >= 0.35 else ("affinity-normal" if rate >= 0.15 else "affinity-bad")
            else:
                item_cls = "affinity-few"
            baba_items.append(
                f'<span class="affinity-item {item_cls}" data-baba="{_esc(baba)}">'
                f'{_esc(baba)}: {placed}/{runs} ({pct})</span>'
            )
        else:
            baba_items.append(
                f'<span class="affinity-item affinity-none" data-baba="{_esc(baba)}">'
                f'{_esc(baba)}: -</span>'
            )
    lines += [
        '  <div class="affinity-section">',
        '    <span class="affinity-label">参考: 馬場別複勝率</span>',
        *[f'    {item}' for item in baba_items],
        '  </div>',
    ]

    # ─── 馬場別成績コメント行（全4馬場分。JS で表示切り替え）──────────────
    lines.append('  <div class="baba-cond-display-wrap">')
    for baba in _BABA_CONDITIONS:
        entry = (baba_affinity or {}).get(baba)
        if entry and entry[0] >= _BABA_AFFINITY_MIN_RUNS:
            runs, placed = entry
            rate = placed / runs
            if rate >= 0.50:
                icon, row_cls = "✅", "cond-pass"
                reason = f"{baba}馬場: {placed}/{runs} ({rate:.0%}) 得意"
            elif rate >= 0.35:
                icon, row_cls = "✅", "cond-pass"
                reason = f"{baba}馬場: {placed}/{runs} ({rate:.0%}) やや得意"
            elif rate >= 0.15:
                icon, row_cls = "⚠️", "cond-none"
                reason = f"{baba}馬場: {placed}/{runs} ({rate:.0%}) やや不得意"
            else:
                icon, row_cls = "❌", "cond-fail"
                reason = f"{baba}馬場: {placed}/{runs} ({rate:.0%}) 苦手"
        elif entry and entry[0] > 0:
            runs, placed = entry
            icon, row_cls = "⚪", "cond-none"
            reason = f"{baba}馬場: {placed}/{runs} — サンプル少（参考）"
        else:
            icon, row_cls = "⚪", "cond-none"
            reason = f"{baba}馬場: 実績なし"
        # 最初の馬場（良）だけ初期表示、他は JS で切り替え
        display = "" if baba == "良" else " style=\"display:none\""
        lines.append(
            f'    <div class="cond-row {row_cls} baba-cond-row" data-baba="{_esc(baba)}"{display}>'
            f'<span class="cond-icon">{icon}</span>'
            f'<span class="cond-label">馬場適性参考</span>'
            f'<span class="cond-reason">{_esc(reason)}</span>'
            f'</div>'
        )
    lines.append('  </div>')

    lines.append('</div>')
    return "\n".join(lines)


def _tier_badge(tier: str) -> str:
    """レースティアのバッジHTML。"""
    labels = {
        "S":     ("S", "#e74c3c",   "ダート中距離|坂あり"),
        "B":     ("B", "#e67e22",   "ダート中距離"),
        "anaba": ("穴", "#9b59b6",  "芝短距離|野芝"),
        "other": ("暫", "#95a5a6",  "その他"),
    }
    key, color, desc = labels.get(tier, ("?", "#aaa", ""))
    return (
        f'<span class="tier-badge" style="background:{color}">{key}</span>'
        f'<span class="tier-desc">{_esc(desc)}</span>'
    )


def _build_race_section(
    race_id: str,
    race_name: str,
    tier: str,
    baba_picks_map: dict[str, list[tuple]],  # {baba: [(horse, label, color, race_eval, strat)]}
    tr1_map: dict | None = None,             # {blood_no: RankedHorse}
    affinity_map: dict | None = None,        # {blood_no: {baba: (runs, placed)}}
) -> str:
    """1レース分の HTML セクションを生成する（馬場別4セクション）。"""
    place_code = race_id[8:10] if len(race_id) >= 10 else "??"
    venue = _VENUE_NAME.get(place_code, place_code)
    race_num = int(race_id[14:16]) if len(race_id) >= 16 else 0

    lines = [
        f'<div class="race-card tier-{tier}" data-tier="{_esc(tier)}" data-race-id="{_esc(race_id)}">',
        f'  <div class="race-header">',
        f'    <span class="race-title">{_esc(venue)} R{race_num}</span>',
        f'    {_tier_badge(tier)}',
        f'    <span class="race-name">{_esc(race_name)}</span>',
        f'  </div>',
    ]

    for i, baba in enumerate(_BABA_CONDITIONS):
        picks = baba_picks_map.get(baba, [])
        active_cls = " active" if i == 0 else ""
        lines.append(f'  <div class="baba-section{active_cls}" data-baba="{_esc(baba)}">')

        warning = _BABA_ROI_WARNING.get(baba, "")
        if warning:
            lines.append(f'    <div class="baba-roi-warning">{_esc(warning)}</div>')

        lines.append('    <div class="picks">')
        if picks:
            for horse, label, color, race_eval, strat in picks:
                tr1_horse = (tr1_map or {}).get(horse.horse_id)
                baba_aff  = (affinity_map or {}).get(horse.horse_id)
                lines.append(_horse_card(horse, label, color, race_eval, strat,
                                         tr1_horse=tr1_horse, baba_affinity=baba_aff))
        else:
            lines.append('      <div class="no-pick">このレースには推奨馬がありません</div>')
        lines.append('    </div>')  # picks
        lines.append('  </div>')   # baba-section

    lines.append('</div>')
    return "\n".join(lines)


_SEGMENT_INFO: dict[str, dict] = {
    "S":     {"name": "ダート中距離・坂あり", "hint": "Phase2 S-1/S-2 複勝率 66.7%", "color": "#e74c3c"},
    "B":     {"name": "ダート中距離",         "hint": "Phase2 B-2 複勝率 58.1%",     "color": "#e67e22"},
    "anaba": {"name": "芝短距離・野芝",        "hint": "Phase2 穴パターン対象",        "color": "#9b59b6"},
    "other": {"name": "その他セグメント",       "hint": "暫定（検証中）",              "color": "#95a5a6"},
}


def _race_to_json_obj(
    rid: str,
    race_ctx,
    race_meta: dict,
    tier: str,
    pick_horse_id: str | None,
    pick_label: str | None,
    pick_color: str | None,
    anaba_horse_id: str | None,
    all_eval,           # RaceEvaluation (uncapped, all horses)
    anaba_eval,         # RaceEvaluation | None
    tr1_map: dict,
    affinity_map: dict,
    strat_cond_ids: list[str],
    baba_picks: dict | None = None,  # {baba: {"horse_id", "label", "color"} | None}
) -> dict:
    """レース1件分のJSONオブジェクトを生成する（HTML内埋め込み用）。"""
    place_code = rid[8:10] if len(rid) >= 10 else "??"
    venue_name = _VENUE_NAME.get(place_code, place_code)
    race_num = int(rid[14:16]) if len(rid) >= 16 else 0

    all_evaled = {ev.horse_id: ev for ev in (all_eval.candidates + all_eval.eliminated_horses)}
    anaba_evaled = {}
    if anaba_eval:
        anaba_evaled = {ev.horse_id: ev for ev in (anaba_eval.candidates + anaba_eval.eliminated_horses)}

    horses_json = []
    for horse in race_ctx.horses:
        hid = horse.horse_id
        ev = all_evaled.get(hid) or anaba_evaled.get(hid)
        is_honmei_pick = hid == pick_horse_id
        is_anaba_pick  = hid == anaba_horse_id

        aff = affinity_map.get(hid) or {}
        baba_aff_json = {}
        for baba in _BABA_CONDITIONS:
            entry = aff.get(baba)
            baba_aff_json[baba] = {"runs": entry[0], "placed": entry[1]} if entry else None

        tr1 = tr1_map.get(hid)
        conds = []
        if ev:
            for i, cr in enumerate(ev.conditions):
                cid = strat_cond_ids[i] if i < len(strat_cond_ids) else ""
                conds.append({
                    "id":     cid,
                    "label":  _cond_label(cid) if cid else "",
                    "passed": cr.passed,
                    "reason": cr.reason or "",
                    "why":    _cond_why(cid) if cid else "",
                })

        pick_label_h = None
        pick_color_h = None
        if is_honmei_pick:
            pick_label_h = pick_label
            pick_color_h = pick_color
        elif is_anaba_pick:
            pick_label_h = "穴推奨"
            pick_color_h = _ANABA_COLOR

        horses_json.append({
            "horse_id":          hid,
            "horse_name":        horse.horse_name or hid,
            "umaban":            horse.umaban,
            "clear_count":       ev.clear_count if ev else 0,
            "total_score":       ev.total_score if ev else 0.0,
            "ai_score":          ev.ai_score if ev else (horse.ai_score or 0.0),
            "is_pick":           is_honmei_pick or is_anaba_pick,
            "pick_label":        pick_label_h,
            "pick_color":        pick_color_h,
            "eliminated":        (ev.eliminated if ev else False),
            "elimination_reason": (ev.elimination_reason if ev else None),
            "tr1_rank":          tr1.rank if tr1 else None,
            "tr1_condition":     tr1.condition_label if tr1 else None,
            "tr1_time":          tr1.tiebreak_time_sec if tr1 else None,
            "conditions":        conds,
            "baba_affinity":     baba_aff_json,
        })

    # Sort by umaban for JSON (display sort is JS-controlled)
    horses_json.sort(key=lambda h: h["umaban"] or 99)

    return {
        "race_id":      rid,
        "race_name":    race_ctx.race_name or rid,
        "venue":        venue_name,
        "race_num":     race_num,
        "date":         rid[:8] if len(rid) >= 8 else "",
        "tier":         tier,
        "tier_label":   _TIER_LABEL[tier],
        "tier_color":   _TIER_COLOR.get(tier, "#aaa"),
        "segment_name": _SEGMENT_INFO[tier]["name"],
        "segment_hint": _SEGMENT_INFO[tier]["hint"],
        "surface":      race_meta.get("surface", ""),
        "distance":     race_meta.get("distance", 0),
        "horses":       horses_json,
        "baba_picks":   baba_picks or {},
    }


# ─── HTML テンプレート ────────────────────────────────────────────────────────

_STYLE = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, "Hiragino Sans", "Yu Gothic", sans-serif;
  background: #f5f6fa;
  color: #1a1a2e;
  padding: 24px;
  line-height: 1.5;
}
h1 {
  font-size: 22px;
  font-weight: 700;
  color: #1a1a2e;
  margin-bottom: 4px;
}
.subtitle { font-size: 13px; color: #666; margin-bottom: 16px; }
.legend {
  display: flex; gap: 12px; margin-bottom: 24px; flex-wrap: wrap;
  background: white; border-radius: 8px; padding: 10px 16px;
  border: 1px solid #e8e8f0;
}
.legend-item { display: flex; align-items: center; gap: 6px; font-size: 12px; color: #555; }
.legend-dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
.race-card {
  background: white;
  border-radius: 12px;
  box-shadow: 0 2px 8px rgba(0,0,0,0.08);
  margin-bottom: 24px;
  overflow: hidden;
}
.race-header {
  background: #1a1a2e;
  color: white;
  padding: 14px 20px;
  display: flex;
  align-items: center;
  gap: 12px;
}
.race-card.tier-S .race-header { background: #7b1528; }
.race-card.tier-B .race-header { background: #7a3a0a; }
.race-card.tier-anaba .race-header { background: #3a1a5e; }
.race-title { font-size: 16px; font-weight: 700; }
.race-name { font-size: 14px; color: #a0aec0; flex: 1; }
.tier-badge {
  font-size: 11px;
  font-weight: 700;
  padding: 2px 7px;
  border-radius: 10px;
  color: white;
  white-space: nowrap;
}
.tier-desc { font-size: 11px; color: #a0c0e0; white-space: nowrap; }
.picks { padding: 16px; display: flex; flex-direction: column; gap: 12px; }
.no-pick { color: #999; font-size: 14px; padding: 8px 0; }
.horse-card {
  background: #fafafa;
  border-radius: 8px;
  border: 1px solid #e8e8f0;
  padding: 12px 16px;
}
.horse-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}
.rank-badge {
  color: white;
  font-size: 12px;
  font-weight: 700;
  padding: 3px 10px;
  border-radius: 12px;
  white-space: nowrap;
}
.horse-name { font-size: 16px; font-weight: 700; flex: 1; }
.scores { font-size: 12px; color: #666; white-space: nowrap; }
.cond-list { display: flex; flex-direction: column; gap: 4px; }
.cond-row {
  font-size: 13px;
  padding: 4px 6px;
  border-radius: 4px;
  display: flex;
  align-items: flex-start;
  gap: 8px;
}
.cond-pass { background: #f0fdf4; }
.cond-fail { background: #fff5f5; opacity: 0.7; }
.cond-none { background: #f8f9fa; opacity: 0.7; }
.cond-icon { flex-shrink: 0; }
.cond-label { font-weight: 600; color: #333; white-space: nowrap; }
.cond-reason { color: #444; flex: 1; }
.cond-why { font-size: 11px; color: #777; border-left: 2px solid #d1fae5; padding-left: 6px; margin-top: 2px; display: block; }
.no-data { color: #aaa; font-size: 13px; }
/* ── コントロールバー ── */
.control-bar {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  padding: 10px 16px;
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
  margin-bottom: 12px;
  align-items: center;
}
.btn-group {
  display: flex;
  align-items: center;
  gap: 4px;
}
.btn-group-label {
  font-size: 12px;
  color: #64748b;
  white-space: nowrap;
  margin-right: 4px;
}
.mode-btn {
  padding: 5px 14px;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  background: white;
  color: #475569;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.12s;
}
.mode-btn:hover { background: #f1f5f9; }
.mode-btn.active { background: #1e293b; color: white; border-color: #1e293b; }
.sort-btn {
  padding: 3px 10px;
  border: 1px solid #d1d5db;
  border-radius: 4px;
  background: white;
  color: #6b7280;
  font-size: 12px;
  cursor: pointer;
}
.sort-btn.active { background: #374151; color: white; border-color: #374151; }
/* ── TR-1 調教セクション ── */
.tr1-section {
  margin-top: 8px;
  padding: 5px 8px;
  background: #eff6ff;
  border-radius: 5px;
  border-left: 3px solid #3b82f6;
  display: flex;
  gap: 10px;
  align-items: center;
  font-size: 12px;
  flex-wrap: wrap;
}
.tr1-none { background: #f1f5f9; border-left-color: #cbd5e1; color: #94a3b8; }
.tr1-badge { font-weight: 700; color: #1d4ed8; font-size: 14px; }
.tr1-rank { color: #374151; }
.tr1-time { color: #6b7280; }
/* ── 馬場アフィニティセクション ── */
.affinity-section {
  margin-top: 6px;
  display: flex;
  gap: 6px;
  align-items: center;
  flex-wrap: wrap;
  font-size: 11px;
}
.affinity-label { font-weight: 600; color: #555; white-space: nowrap; }
.affinity-item { padding: 2px 6px; border-radius: 4px; white-space: nowrap; font-size: 11px; }
.affinity-good    { background: #dcfce7; color: #15803d; font-weight: 600; }
.affinity-normal  { background: #f3f4f6; color: #4b5563; }
.affinity-bad     { background: #fee2e2; color: #b91c1c; font-weight: 600; }
.affinity-few     { background: #fef9c3; color: #854d0e; }
.affinity-none    { background: #f9fafb; color: #d1d5db; }
.affinity-current { outline: 2px solid #2563eb; outline-offset: 1px; }
.baba-cond-row { border-top: 1px solid #e5e7eb; margin-top: 4px; padding-top: 4px; }
/* ── 馬場タブ ── */
.baba-tab-bar {
  position: sticky;
  top: 0;
  z-index: 40;
  background: #1a1a2e;
  padding: 10px 20px;
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 20px;
  box-shadow: 0 3px 10px rgba(0,0,0,0.2);
}
.baba-tab-bar-label { font-size: 12px; color: #94a3b8; margin-right: 4px; }
.baba-tab {
  padding: 8px 22px;
  border: 2px solid #374151;
  border-radius: 24px;
  background: #374151;
  color: #9ca3af;
  font-size: 14px;
  font-weight: 700;
  cursor: pointer;
  transition: all 0.15s;
  letter-spacing: 0.03em;
}
.baba-tab:hover { background: #4b5563; color: #e5e7eb; }
.baba-tab.active {
  background: #10b981;
  color: white;
  border-color: #10b981;
  box-shadow: 0 0 12px rgba(16,185,129,0.5);
}
.baba-tab-hint { font-size: 11px; color: #6b7280; margin-left: 8px; }
/* 現在の馬場バナー */
.baba-current-banner {
  background: linear-gradient(90deg, #10b981 0%, #059669 100%);
  color: white;
  padding: 10px 20px;
  border-radius: 8px;
  margin-bottom: 16px;
  display: flex;
  align-items: center;
  gap: 16px;
  font-size: 14px;
}
.baba-banner-label { font-size: 18px; font-weight: 800; letter-spacing: 0.05em; }
.baba-banner-sub { font-size: 12px; opacity: 0.85; }
.baba-changed-badge {
  background: #f59e0b;
  color: white;
  font-size: 10px;
  font-weight: 700;
  padding: 2px 7px;
  border-radius: 10px;
  white-space: nowrap;
}
.baba-section { display: none; }
.baba-section.active { display: block; }
.baba-roi-warning {
  background: #fef3c7;
  border-left: 3px solid #f59e0b;
  color: #92400e;
  font-size: 12px;
  font-weight: 600;
  padding: 5px 14px;
  margin: 0;
}
.footer {
  text-align: center;
  color: #999;
  font-size: 12px;
  margin-top: 24px;
  padding-top: 16px;
  border-top: 1px solid #e8e8f0;
}
"""


def _render_html(
    sections: list[str],
    race_data_json: str,
    generated_at: str,
    stats: dict,
) -> str:
    """モード切り替え対応 HTML を生成する。

    ランク判定は Phase2 検証済みロジックのまま（baba_scoreはランクに影響しない）。
    馬場タブは参考情報（馬場別成績）の強調表示のみを切り替える。
    全出走馬モード / 条件表示モードは JS で制御する。
    """
    s_cnt  = stats.get("S", 0)
    b_cnt  = stats.get("B", 0)
    an_cnt = stats.get("anaba", 0)
    ot_cnt = stats.get("other", 0)
    body = "\n".join(sections) if sections else '<p style="color:#999">今週末のレースデータがありません。</p>'

    _JS = r"""
const RACE_DATA = __RACE_DATA__;

let viewMode = 'pickup';   // 'pickup' | 'all_horses'
let condMode = 'all';      // 'all' | 'training' | 'segment'
let currentBaba = '良';
let sortMode = 'clear';    // 'umaban' | 'clear'

function escH(s) {
  if (!s) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── ボタン状態更新 ─────────────────────────────────────────────────────────
function _activateBtn(group, val) {
  document.querySelectorAll('[data-grp="' + group + '"]').forEach(function(b) {
    b.classList.toggle('active', b.dataset.val === val);
  });
}

// ── バナー更新（共通）────────────────────────────────────────────────────
var _BABA_INFO = {
  '良':  ['良馬場', '標準コンディション', '#10b981'],
  '稍重': ['稍重馬場', 'やや湿潤', '#f59e0b'],
  '重':  ['重馬場',  '湿潤・パワー型有利', '#ef4444'],
  '不良': ['不良馬場', '泥状態・馬場巧者優先', '#7c3aed'],
};
function _updateBabaBanner(baba) {
  var i = _BABA_INFO[baba] || [baba, '', '#6b7280'];
  var banner = document.getElementById('baba-current-banner');
  if (banner) {
    banner.style.background = 'linear-gradient(90deg,' + i[2] + ' 0%,' + i[2] + 'cc 100%)';
    var lbl = banner.querySelector('.baba-banner-label');
    var sub = banner.querySelector('.baba-banner-sub');
    if (lbl) lbl.textContent = i[0];
    if (sub) sub.textContent = i[1] + ' — BET-7: 馬場別条件で推奨馬が変わる場合があります';
  }
}

// ── 馬場タブ ─────────────────────────────────────────────────────────────
function switchBaba(baba) {
  currentBaba = baba;
  _activateBtn('baba', baba);
  _updateBabaBanner(baba);

  if (viewMode === 'all_horses') {
    // 全出走馬モード: 再レンダリング（renderAllHorses内でcurrentBabaを参照してハイライト）
    renderAllHorses();
    return;
  }
  if (viewMode === 'segment') {
    // セグメント別モード: アフィニティのみ更新
    document.querySelectorAll('.affinity-item').forEach(function(el) {
      el.classList.toggle('affinity-current', el.dataset.baba === baba);
    });
    return;
  }

  // ピックアップモード（BET-7）: 馬場別セクションを切り替え（推奨馬が変わる）
  document.querySelectorAll('.baba-section').forEach(function(el) {
    el.classList.toggle('active', el.dataset.baba === baba);
  });
  // affinity ハイライト更新
  document.querySelectorAll('.affinity-item').forEach(function(el) {
    el.classList.toggle('affinity-current', el.dataset.baba === baba);
  });
  // 馬場別コメント行の表示切り替え
  document.querySelectorAll('.baba-cond-row').forEach(function(el) {
    el.style.display = (el.dataset.baba === baba) ? '' : 'none';
  });
}

// ── 表示範囲モード ─────────────────────────────────────────────────────────
// Bug Fix 4: all_horses モードでも馬場タブを有効にする（要件: 両モードでハイライト切り替え可能）
function setViewMode(mode) {
  viewMode = mode;
  _activateBtn('view', mode);
  // 馬場タブは全モードで有効（ピックアップ/全出走馬 どちらでも馬場別ハイライトが機能する）
  document.querySelectorAll('[data-grp="baba"]').forEach(function(b) {
    b.disabled = false;
    b.style.opacity = '';
  });
  document.getElementById('baba-hint').textContent = '参考: 馬場別成績の強調表示';

  if (mode === 'pickup') {
    document.getElementById('pickup-view').style.display = '';
    document.getElementById('dynamic-view').style.display = 'none';
    // ピックアップに戻ったとき現在の馬場状態を静的DOMに再適用
    switchBaba(currentBaba);
  } else {
    document.getElementById('pickup-view').style.display = 'none';
    document.getElementById('dynamic-view').style.display = '';
    renderAllHorses();
  }
}

// ── 条件表示モード ─────────────────────────────────────────────────────────
function setCondMode(mode) {
  condMode = mode;
  _activateBtn('cond', mode);
  if (viewMode !== 'pickup') { renderAllHorses(); return; }

  if (mode === 'training') {
    document.querySelectorAll('.cond-list, .affinity-section, .baba-cond-display-wrap').forEach(function(el) {
      el.style.display = 'none';
    });
  } else if (mode === 'all') {
    document.querySelectorAll('.cond-list, .affinity-section, .baba-cond-display-wrap').forEach(function(el) {
      el.style.display = '';
    });
    // 馬場別コメント行の表示を現在馬場だけにする + バナー再適用
    document.querySelectorAll('.baba-cond-row').forEach(function(el) {
      el.style.display = (el.dataset.baba === currentBaba) ? '' : 'none';
    });
    // affinity ハイライト再適用（training モードから戻ったとき）
    document.querySelectorAll('.affinity-item').forEach(function(el) {
      el.classList.toggle('affinity-current', el.dataset.baba === currentBaba);
    });
  } else if (mode === 'segment') {
    viewMode = 'segment';
    document.getElementById('pickup-view').style.display = 'none';
    document.getElementById('dynamic-view').style.display = '';
    renderSegment();
    return;
  }
  document.getElementById('pickup-view').style.display = '';
  document.getElementById('dynamic-view').style.display = 'none';
}

// ── ソートモード ──────────────────────────────────────────────────────────
function setSortMode(mode) {
  sortMode = mode;
  _activateBtn('sort', mode);
  if (viewMode === 'all_horses') renderAllHorses();
}

// ── 全出走馬レンダリング ──────────────────────────────────────────────────
function renderAllHorses() {
  var container = document.getElementById('dynamic-view');
  var html = '<h2 style="margin-bottom:16px;font-size:16px;color:#555">全出走馬一覧</h2>';
  html += '<div style="margin-bottom:12px">';
  html += '<span style="font-size:13px;font-weight:600;margin-right:8px">並び替え:</span>';
  html += ['umaban','clear'].map(function(m) {
    var lbl = m === 'umaban' ? '馬番順' : 'クリア数順';
    return '<button class="sort-btn' + (sortMode===m?' active':'') + '" data-grp="sort" data-val="' + m + '" onclick="setSortMode(\'' + m + '\')">' + lbl + '</button>';
  }).join('');
  html += '</div>';

  var tierOrder = ['S','B','anaba','other'];
  RACE_DATA.forEach(function(race) {
    var horses = race.horses.slice().sort(function(a,b) {
      if (sortMode === 'umaban') return (a.umaban||99)-(b.umaban||99);
      return b.clear_count - a.clear_count;
    });

    html += '<div class="race-card tier-' + escH(race.tier) + '" style="margin-bottom:20px">';
    html += '<div class="race-header">';
    html += '<span class="race-title">' + escH(race.venue) + ' R' + race.race_num + '</span>';
    html += '<span style="background:' + escH(race.tier_color) + '" class="tier-badge">' + escH(race.tier.charAt(0).toUpperCase()) + '</span>';
    html += '<span class="race-name">' + escH(race.race_name) + '</span>';
    html += '</div>';
    html += '<div style="padding:12px">';

    // BET-7: currentBabaのピック情報を参照
    var babaPick = race.baba_picks ? race.baba_picks[currentBaba] : null;
    horses.forEach(function(h) {
      var isBabaPick = babaPick && h.horse_id === babaPick.horse_id;
      var pickColor = isBabaPick ? babaPick.color : (h.pick_color || '#10b981');
      var pickLabel = isBabaPick ? babaPick.label : h.pick_label;
      var bg = isBabaPick ? 'background:#f0fdf4;border-left:4px solid ' + escH(pickColor) : (h.eliminated ? 'background:#fff5f5;opacity:0.7' : '');
      html += '<div style="margin-bottom:10px;padding:10px;border-radius:8px;border:1px solid #e5e7eb;' + bg + '">';
      html += '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:6px">';
      if (isBabaPick) html += '<span style="background:' + escH(pickColor) + ';color:white;font-size:11px;font-weight:700;padding:2px 8px;border-radius:12px">' + escH(pickLabel) + '</span>';
      html += '<span style="font-size:13px;color:#666;min-width:24px">' + (h.umaban||'-') + '番</span>';
      html += '<span style="font-size:15px;font-weight:700">' + escH(h.horse_name) + '</span>';
      html += '<span style="font-size:12px;color:#666">クリア: <strong>' + h.clear_count + '</strong> / スコア: ' + h.total_score.toFixed(1) + '</span>';
      if (h.eliminated) html += '<span style="background:#fee2e2;color:#b91c1c;font-size:11px;padding:2px 6px;border-radius:4px">除外: ' + escH(h.elimination_reason) + '</span>';
      html += '</div>';

      // 条件表示 (condModeに応じて切り替え)
      if (condMode !== 'training' && h.conditions && h.conditions.length) {
        html += '<div style="display:flex;flex-direction:column;gap:3px">';
        h.conditions.forEach(function(c) {
          var icon = c.passed === true ? '✅' : (c.passed === false ? '❌' : '⚪');
          var bg2 = c.passed === true ? '#f0fdf4' : (c.passed === false ? '#fff5f5' : '#f8f9fa');
          html += '<div style="font-size:12px;padding:3px 6px;border-radius:4px;background:' + bg2 + ';display:flex;gap:6px">';
          html += '<span>' + icon + '</span>';
          if (c.label) html += '<strong>' + escH(c.label) + '</strong>';
          html += '<span style="color:#555">' + escH(c.reason) + '</span>';
          html += '</div>';
        });
        html += '</div>';
      }

      // TR-1
      if (h.tr1_rank) {
        html += '<div class="tr1-section" style="margin-top:6px">';
        html += '<span class="tr1-badge">調教 ' + escH(h.tr1_condition) + '</span>';
        html += '<span class="tr1-rank">TR-1ランク ' + h.tr1_rank + '位</span>';
        if (h.tr1_time) html += '<span class="tr1-time">' + h.tr1_time.toFixed(1) + '秒</span>';
        html += '</div>';
      }

      // 馬場アフィニティ
      // Bug Fix 5: data-baba属性を追加 + currentBabaでaffinity-currentクラスを付与
      html += '<div class="affinity-section" style="margin-top:6px">';
      html += '<span class="affinity-label">参考: 馬場別複勝率</span>';
      ['良','稍重','重','不良'].forEach(function(baba) {
        var aff = h.baba_affinity[baba];
        var cls, txt;
        if (aff) {
          var rate = aff.runs > 0 ? aff.placed/aff.runs : 0;
          cls = aff.runs >= 3 ? (rate >= 0.35 ? 'affinity-good' : (rate >= 0.15 ? 'affinity-normal' : 'affinity-bad')) : 'affinity-few';
          txt = baba + ': ' + aff.placed + '/' + aff.runs + ' (' + Math.round(rate*100) + '%)';
        } else { cls = 'affinity-none'; txt = baba + ': -'; }
        var activeCls = (baba === currentBaba) ? ' affinity-current' : '';
        html += '<span class="affinity-item ' + cls + activeCls + '" data-baba="' + escH(baba) + '">' + txt + '</span>';
      });
      html += '</div>';

      html += '</div>';  // horse row
    });

    html += '</div>';  // padding
    html += '</div>';  // race-card
  });

  container.innerHTML = html;
}

// ── セグメント別レンダリング ──────────────────────────────────────────────
function renderSegment() {
  var container = document.getElementById('dynamic-view');
  var segments = [
    {tier:'S', name:'ダート中距離・坂あり', hint:'Phase2 S-1/S-2 複勝率 66.7%', color:'#e74c3c'},
    {tier:'B', name:'ダート中距離', hint:'Phase2 B-2 複勝率 58.1%', color:'#e67e22'},
    {tier:'anaba', name:'芝短距離・野芝', hint:'Phase2 穴パターン対象', color:'#9b59b6'},
    {tier:'other', name:'その他セグメント', hint:'暫定（検証中）', color:'#95a5a6'},
  ];
  var html = '<h2 style="margin-bottom:16px;font-size:16px;color:#555">セグメント別表示</h2>';

  segments.forEach(function(seg) {
    var races = RACE_DATA.filter(function(r) { return r.tier === seg.tier || (seg.tier === 'anaba' && (r.tier === 'anaba' || r.tier === 'anaba_pick')); });
    if (!races.length) return;

    html += '<div style="margin-bottom:32px">';
    html += '<div style="background:' + seg.color + ';color:white;padding:12px 20px;border-radius:10px 10px 0 0">';
    html += '<span style="font-size:18px;font-weight:800">' + escH(seg.name) + '</span> &nbsp;';
    html += '<span style="font-size:12px;opacity:0.9">' + escH(seg.hint) + '</span>';
    html += '<span style="float:right;font-size:13px">' + races.length + 'レース</span>';
    html += '</div>';

    races.forEach(function(race) {
      var pick = race.horses.find(function(h) { return h.is_pick && h.pick_label !== '穴推奨'; });
      if (!pick) return;
      html += '<div style="padding:10px 16px;border:1px solid #e5e7eb;border-top:none;background:white">';
      html += '<span style="font-size:13px;font-weight:600;color:#333">' + escH(race.venue) + ' R' + race.race_num + '</span>';
      html += '<span style="margin-left:12px;font-size:13px">';
      html += '<strong>' + escH(pick.horse_name) + '</strong>';
      html += ' クリア:' + pick.clear_count + ' スコア:' + pick.total_score.toFixed(1);
      html += '</span>';
      html += '</div>';
    });
    html += '</div>';
  });

  container.innerHTML = html;
}

// ── 初期化 ────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', function() {
  // 良馬場をデフォルトでハイライト（バナー + affinity + baba-cond-row）
  switchBaba('良');
  _updateBabaBanner('良');
});
""".replace("__RACE_DATA__", race_data_json)

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width">
<title>フクロウ AI — 週末予想レポート</title>
<style>{_STYLE}</style>
</head>
<body>
<h1>フクロウ AI — 週末予想レポート</h1>
<p class="subtitle">生成日時: {_esc(generated_at)} &nbsp;|&nbsp; 対象: {stats.get('total', 0)}R
&nbsp;(一押し {s_cnt}R / 二押し {b_cnt}R / 穴推奨対象 {an_cnt}R / 三押し暫定 {ot_cnt}R)</p>

<!-- ── コントロールバー ── -->
<div class="control-bar">
  <div class="btn-group">
    <span class="btn-group-label">表示範囲:</span>
    <button class="mode-btn active" data-grp="view" data-val="pickup" onclick="setViewMode('pickup')">ピックアップ</button>
    <button class="mode-btn" data-grp="view" data-val="all_horses" onclick="setViewMode('all_horses')">全出走馬</button>
  </div>
  <div class="btn-group">
    <span class="btn-group-label">条件表示:</span>
    <button class="mode-btn active" data-grp="cond" data-val="all" onclick="setCondMode('all')">全条件</button>
    <button class="mode-btn" data-grp="cond" data-val="training" onclick="setCondMode('training')">調教のみ</button>
    <button class="mode-btn" data-grp="cond" data-val="segment" onclick="setCondMode('segment')">セグメント別</button>
  </div>
</div>

<!-- ── 馬場タブ（BET-7: 馬場別条件反映。推奨馬が変わる場合あり）── -->
<div class="baba-tab-bar">
  <span class="baba-tab-bar-label">馬場:</span>
  <button class="baba-tab active" data-grp="baba" data-val="良" onclick="switchBaba('良')">良馬場</button>
  <button class="baba-tab" data-grp="baba" data-val="稍重" onclick="switchBaba('稍重')">稍重</button>
  <button class="baba-tab" data-grp="baba" data-val="重" onclick="switchBaba('重')">重馬場</button>
  <button class="baba-tab" data-grp="baba" data-val="不良" onclick="switchBaba('不良')">不良</button>
  <span id="baba-hint" class="baba-tab-hint">参考: 馬場別成績の強調表示</span>
</div>
<!-- ── 馬場バナー（Bug Fix 6: 専用DIV要素を追加。CSSのbaba-current-bannerを正しく適用）── -->
<div id="baba-current-banner" class="baba-current-banner">
  <span class="baba-banner-label">良馬場</span>
  <span class="baba-banner-sub">標準コンディション — BET-7: 馬場別条件で推奨馬が変わる場合があります</span>
</div>

<div class="legend">
  <div class="legend-item"><span class="legend-dot" style="background:#e74c3c"></span>一押し (S) — Phase2 66.7%実績</div>
  <div class="legend-item"><span class="legend-dot" style="background:#e67e22"></span>二押し (B) — Phase2 58.1%実績</div>
  <div class="legend-item"><span class="legend-dot" style="background:#9b59b6"></span>穴推奨 — 芝短距離|野芝</div>
  <div class="legend-item"><span class="legend-dot" style="background:#f1c40f"></span>三押し — 暫定</div>
  <div class="legend-item" style="color:#888;font-size:11px">※BET-7: 馬場タブで馬場別条件(v2_baba_track_record等)を反映した推奨馬を表示。良: ROI74.4% / 稍重: 46.0% / 重: 33.3%</div>
</div>

<!-- ── ピックアップ表示 (デフォルト・静的HTML) ── -->
<div id="pickup-view">
{body}
</div>

<!-- ── 動的表示 (全出走馬 / セグメント別 / JS制御) ── -->
<div id="dynamic-view" style="display:none"></div>

<div class="footer">
  フクロウ AI — 競馬予測は参考情報です<br>
  S-1（一押し）: s1_pattern（5条件ALL-True 複勝65.0%）/ B/other: honmei_v7 / 穴: anaba_v5 / TR-1: training_ranker
</div>
<script>
{_JS}
</script>
</body>
</html>"""


# ─── エントリポイント ──────────────────────────────────────────────────────────

# ─── DecimalEncoder（モジュールレベル）──────────────────────────────────────────

class _DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        return super().default(o)


# ─── コアデータ生成（Phase 1-4）─────────────────────────────────────────────────

def _run_picks_core() -> tuple[list[str], list[dict], list[dict], dict, str, int, int]:
    """Phase 1-4 を実行。
    Returns: (sections, race_data_list, picks_data, tier_counts, generated_at, ok, ng)
    """
    print("[picks] 戦略ロード中...")
    try:
        honmei_strat = load_strategy(_STRATEGY_HONMEI)
        anaba_strat  = load_strategy(_STRATEGY_ANABA)
        s1_strat     = load_strategy(_STRATEGY_S1)
    except FileNotFoundError as e:
        raise RuntimeError(f"戦略ファイルが見つかりません: {e}") from e

    # BET-7: 馬場別戦略（params.babaを各馬場に設定したコピー）
    baba_strats = {baba: _make_baba_strategy(honmei_strat, baba) for baba in _BABA_CONDITIONS}
    s1_strat_cond_ids = [c.id for c in s1_strat.conditions if c.enabled]
    _S1_N_CONDS = len(s1_strat_cond_ids)  # 5条件

    print("[picks] 今週末のレース取得中...")
    weekend = get_weekend_races()
    race_ids = [
        race.race_id
        for races in weekend.races_by_date.values()
        for race in races
    ]
    print(f"[picks] 対象レース数: {len(race_ids)}")

    print("[picks] v2 DBからレースメタデータ取得中...")
    meta_map = _fetch_race_meta_v2(race_ids)

    print("[picks] 騎手データ取得中（v2 DB + JVDL）...")
    jockey_entries = _fetch_jockey_entries(race_ids)
    all_kishu_codes = {v["jockey_id"] for v in jockey_entries.values() if v.get("jockey_id")}
    jockey_yr_wins = _fetch_jockey_yr_wins(all_kishu_codes)
    print(f"[picks] 騎手コード数: {len(all_kishu_codes)} / 年間勝利数DB: {len(jockey_yr_wins)}件")

    # ── フェーズ1: 全レースコンテキスト取得 + 基本補完 ──────────────────────────
    print("[picks] レースコンテキスト取得中...")
    ctx_list: list[tuple[str, object, dict, str]] = []  # (rid, race_ctx, race_meta, tier)
    past_race_ids_need_class: set[str] = set()
    ng_fetch = 0

    for rid in race_ids:
        try:
            race_ctx  = fetch_race_context(rid)
            race_meta = meta_map.get(rid, {})
            tier      = _race_tier(race_meta)

            # v2 DBメタでNoneフィールドを補完
            if race_ctx.surface is None and race_meta.get("surface"):
                race_ctx.surface = race_meta["surface"]
            if race_ctx.place_code is None and race_meta.get("keibajo"):
                race_ctx.place_code = race_meta["keibajo"]
            if race_ctx.class_level is None and race_meta.get("class_level") is not None:
                race_ctx.class_level = race_meta["class_level"]

            # 騎手データ補完
            for horse in race_ctx.horses:
                key = (rid, horse.horse_id)
                j = jockey_entries.get(key)
                if j:
                    if horse.jockey_id is None and j.get("jockey_id"):
                        horse.jockey_id = j["jockey_id"]
                    if horse.prev_jockey_id is None and j.get("prev_jockey_id"):
                        horse.prev_jockey_id = j["prev_jockey_id"]
                if horse.jockey_id and horse.jockey_yr_wins is None:
                    horse.jockey_yr_wins = jockey_yr_wins.get(horse.jockey_id, 0)

                # 過去走クラスが欠損している16桁race_idを収集（races_v2補完用）
                if horse.past_races and horse.past_races[0].race_id and horse.past_races[0].class_level is None:
                    past_race_ids_need_class.add(horse.past_races[0].race_id)

            ctx_list.append((rid, race_ctx, race_meta, tier))
        except Exception as e:
            print(f"  [picks] race_id={rid} コンテキスト取得失敗: {e}")
            ng_fetch += 1

    # ── フェーズ2: 過去走クラスレベル補完（races_v2からの一括取得）────────────
    if past_race_ids_need_class:
        print(f"[picks] 過去走クラスレベル補完中（{len(past_race_ids_need_class)}件 → races_v2参照）...")
        extra_class = _fetch_past_class_levels(past_race_ids_need_class)
        for _rid, race_ctx, _meta, _tier in ctx_list:
            for horse in race_ctx.horses:
                if horse.past_races and horse.past_races[0].class_level is None:
                    pr0 = horse.past_races[0]
                    if pr0.race_id and pr0.race_id in extra_class:
                        pr0.class_level = extra_class[pr0.race_id]

    # ── フェーズ2b: S-1用 f3_time_rank_pct補完（ml.db race_entries から一括取得）──
    # v2_f3_top条件がNoneにならないよう、S-tier全馬の過去走f3ランクを補完する
    s_tier_past_race_ids: set[str] = set()
    for rid, race_ctx, _meta, tier in ctx_list:
        if tier != "S":
            continue
        for horse in race_ctx.horses:
            for pr in horse.past_races:
                if pr.race_id and pr.f3_time_rank_pct is None:
                    s_tier_past_race_ids.add(pr.race_id)

    if s_tier_past_race_ids:
        print(f"[picks] S-1 f3_rank_pct補完中（過去走{len(s_tier_past_race_ids)}件）...")
        f3_rank_map = _fetch_f3_rank_pct_for_past_races(s_tier_past_race_ids)
        for rid, race_ctx, _meta, tier in ctx_list:
            if tier != "S":
                continue
            for horse in race_ctx.horses:
                for pr in horse.past_races:
                    if pr.race_id and pr.f3_time_rank_pct is None:
                        race_f3 = f3_rank_map.get(pr.race_id, {})
                        if horse.horse_id in race_f3:
                            pr.f3_time_rank_pct = race_f3[horse.horse_id]

    # ── フェーズ3: TR-1 + 馬場アフィニティ（全レース一括）─────────────────
    print("[picks] TR-1 調教評価取得中...")
    tr1_by_race: dict[str, dict] = {}
    tr1_ok = tr1_ng = 0
    for rid, race_ctx, _meta, _tier in ctx_list:
        ranked = _fetch_training_for_race(race_ctx)
        tr1_by_race[rid] = ranked
        if ranked: tr1_ok += 1
        else: tr1_ng += 1
    print(f"[picks] TR-1: 取得{tr1_ok}レース / データなし{tr1_ng}レース")

    print("[picks] 馬場アフィニティ取得中...")
    blood_nos_dirt: list[str] = []
    blood_nos_turf: list[str] = []
    for rid, race_ctx, race_meta, _tier in ctx_list:
        surface = race_meta.get("surface", "")
        bns = [h.horse_id for h in race_ctx.horses]
        if surface == "ダート": blood_nos_dirt.extend(bns)
        elif surface == "芝":   blood_nos_turf.extend(bns)
    affinity_dirt = _fetch_baba_affinity(list(set(blood_nos_dirt)), "ダート")
    affinity_turf = _fetch_baba_affinity(list(set(blood_nos_turf)), "芝")
    print(f"[picks] アフィニティ: ダート{len(affinity_dirt)}頭 / 芝{len(affinity_turf)}頭")

    # ── フェーズ4: 評価 + HTML生成 ─────────────────────────────────────────
    # ランク判定: Phase 2 検証済みロジック（honmei_eval.candidates[0] = engine出力そのまま）
    # 馬場アフィニティはランク判定に影響しない（参考表示のみ）

    sections: list[str] = []
    tier_counts: dict[str, int] = {"S": 0, "B": 0, "anaba": 0, "other": 0}
    ok, ng = 0, ng_fetch
    picks_data: list[dict] = []  # 結果追跡（シンプル形式）
    race_data_list: list[dict] = []  # 全出走馬 JSON 埋め込み用

    strat_cond_ids = [c.id for c in honmei_strat.conditions if c.enabled]
    anaba_cond_ids = [c.id for c in anaba_strat.conditions if c.enabled]

    for rid, race_ctx, race_meta, tier in ctx_list:
        try:
            tier_counts[tier] += 1
            surface = race_meta.get("surface", "")
            affinity_map = (affinity_dirt if surface == "ダート"
                            else (affinity_turf if surface == "芝" else {}))
            tr1_map = tr1_by_race.get(rid, {})

            # baba_picks_map: {baba: [(horse, label, color, race_eval, strat)]}
            baba_picks_map: dict[str, list[tuple]] = {}
            baba_picks_json: dict[str, dict | None] = {}
            all_eval_for_json = None
            eval_strat_cond_ids: list[str] = strat_cond_ids  # honmei_v7がデフォルト

            if tier == "S":
                # S-1パターン: 5条件ALL-True（clear_count==5）必須、最人気（tan_odds最小）選択
                s1_eval = evaluate_race_context(
                    race_ctx, s1_strat,
                    max_selections=len(race_ctx.horses)
                )
                horse_odds = {h.horse_id: (h.tan_odds or 999.0) for h in race_ctx.horses}
                # 5条件すべて passed=True の馬のみ候補（passed=None はclear_count加算なし）
                s1_cleared = [h for h in s1_eval.candidates if h.clear_count == _S1_N_CONDS]
                s1_top = (
                    min(s1_cleared, key=lambda h: horse_odds.get(h.horse_id, 999.0))
                    if s1_cleared else None
                )
                # 全baba共通で同一S-1推奨（S-1条件はbaba非依存）
                for baba in _BABA_CONDITIONS:
                    b_picks_s1: list[tuple] = []
                    if s1_top is not None:
                        b_picks_s1.append((s1_top, _TIER_LABEL[tier], _TIER_COLOR[tier], s1_eval, s1_strat))
                    baba_picks_map[baba] = b_picks_s1
                    baba_picks_json[baba] = (
                        {"horse_id": s1_top.horse_id, "label": _TIER_LABEL[tier], "color": _TIER_COLOR.get(tier, "#aaa")}
                        if s1_top else None
                    )
                all_eval_for_json = s1_eval
                top = s1_top
                eval_strat_cond_ids = s1_strat_cond_ids
            else:
                # BET-7: 馬場別に4回評価する（各baba戦略でv2_baba_*条件が異なるparams.babaを参照）
                for baba in _BABA_CONDITIONS:
                    baba_strat = baba_strats[baba]
                    b_eval = evaluate_race_context(
                        race_ctx, baba_strat,
                        max_selections=len(race_ctx.horses)
                    )
                    b_top = b_eval.candidates[0] if b_eval.candidates else None

                    b_picks: list[tuple] = []
                    if b_top is not None:
                        b_picks.append((b_top, _TIER_LABEL[tier], _TIER_COLOR[tier], b_eval, baba_strat))

                    # anaba追加（良馬場評価のときのみ穴推奨を共通で追加）
                    if tier == "anaba" and baba == "良":
                        anaba_eval = evaluate_race_context(
                            race_ctx, anaba_strat,
                            max_selections=len(race_ctx.horses)
                        )
                        honmei_ids = {b_top.horse_id} if b_top else set()
                        anaba_best = next(
                            (c for c in anaba_eval.candidates if c.horse_id not in honmei_ids),
                            None,
                        )
                        if anaba_best is not None:
                            b_picks.append((anaba_best, "穴推奨", _ANABA_COLOR, anaba_eval, anaba_strat))

                    baba_picks_map[baba] = b_picks
                    baba_picks_json[baba] = (
                        {"horse_id": b_top.horse_id, "label": _TIER_LABEL[tier], "color": _TIER_COLOR.get(tier, "#aaa")}
                        if b_top else None
                    )
                    if baba == "良":
                        all_eval_for_json = b_eval

                # 良馬場評価をJSON用（全出走馬モードの条件表示）に使う
                if all_eval_for_json is None:
                    all_eval_for_json = evaluate_race_context(
                        race_ctx, honmei_strat, max_selections=len(race_ctx.horses)
                    )
                top = all_eval_for_json.candidates[0] if all_eval_for_json.candidates else None

            race_name = race_ctx.race_name or rid
            place_code = rid[8:10] if len(rid) >= 10 else "??"
            race_num = int(rid[14:16]) if len(rid) >= 16 else 0
            date_str = rid[:8] if len(rid) >= 8 else ""
            venue_name = _VENUE_NAME.get(place_code, place_code)

            # 静的 HTML セクション（ピックアップ表示用: 馬場別4セクション）
            sections.append(
                _build_race_section(rid, race_name, tier, baba_picks_map,
                                    tr1_map=tr1_map, affinity_map=affinity_map)
            )

            # 穴推奨情報（良馬場判定）
            good_picks = baba_picks_map.get("良", [])
            anaba_best_h = next((h for h, lbl, *_ in good_picks if lbl == "穴推奨"), None)

            # 全出走馬 JSON オブジェクト
            race_data_list.append(
                _race_to_json_obj(
                    rid, race_ctx, race_meta, tier,
                    pick_horse_id  = top.horse_id if top else None,
                    pick_label     = _TIER_LABEL[tier] if top else None,
                    pick_color     = _TIER_COLOR.get(tier, "#aaa") if top else None,
                    anaba_horse_id = anaba_best_h.horse_id if anaba_best_h else None,
                    all_eval       = all_eval_for_json,
                    anaba_eval     = None,
                    tr1_map        = tr1_map,
                    affinity_map   = affinity_map,
                    strat_cond_ids = eval_strat_cond_ids,
                    baba_picks     = baba_picks_json,
                )
            )

            # 結果追跡 JSON（良馬場ピック = 基準）
            if top is not None:
                picks_data.append({
                    "race_id": rid, "date": date_str,
                    "venue": venue_name, "race_num": race_num, "race_name": race_name,
                    "tier": tier, "label": _TIER_LABEL[tier],
                    "horse_id": top.horse_id, "horse_name": top.horse_name or top.horse_id,
                    "clear_count": top.clear_count, "ai_score": top.ai_score,
                    "actual_rank": None, "placed": None,
                })
            if anaba_best_h is not None:
                picks_data.append({
                    "race_id": rid, "date": date_str,
                    "venue": venue_name, "race_num": race_num, "race_name": race_name,
                    "tier": "anaba_pick", "label": "穴推奨",
                    "horse_id": anaba_best_h.horse_id, "horse_name": anaba_best_h.horse_name or anaba_best_h.horse_id,
                    "clear_count": anaba_best_h.clear_count, "ai_score": anaba_best_h.ai_score,
                    "actual_rank": None, "placed": None,
                })
            ok += 1

        except Exception as e:
            print(f"  [picks] race_id={rid} 失敗: {e}")
            ng += 1

    print(f"[picks] 一押し (S): {tier_counts['S']}レース")
    print(f"[picks] 二押し (B): {tier_counts['B']}レース")
    print(f"[picks] 穴推奨対象: {tier_counts['anaba']}レース")
    print(f"[picks] 三押し暫定: {tier_counts['other']}レース")

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return sections, race_data_list, picks_data, tier_counts, generated_at, ok, ng


# ─── 共通データ生成（CLI / API 共用）────────────────────────────────────────────

def build_picks_json() -> tuple[list[dict], dict, str]:
    """CLIとAPIの共通データ生成関数。(race_data_list, stats, generated_at) を返す。
    副作用: data/output/tipster/picks_race_data.json を書き出す。"""
    _sections, race_data_list, picks_data, tier_counts, generated_at, ok, _ng = _run_picks_core()
    stats = {**tier_counts, "total": ok}
    cache_path = _OUTPUT_PATH.parent / "picks_race_data.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(
            {"generated_at": generated_at, "race_data": race_data_list, "stats": stats},
            ensure_ascii=False,
            cls=_DecimalEncoder,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[picks] キャッシュ保存: {cache_path}")
    return race_data_list, stats, generated_at


# ─── エントリーポイント ────────────────────────────────────────────────────────

def main() -> None:
    sections, race_data_list, picks_data, tier_counts, generated_at, ok, ng = _run_picks_core()
    stats = {**tier_counts, "total": ok}
    race_data_json = json.dumps(race_data_list, ensure_ascii=False, cls=_DecimalEncoder)
    html_content = _render_html(sections, race_data_json, generated_at, stats)

    picks_json_path = _OUTPUT_PATH.parent / "picks_this_week.json"
    picks_json_path.write_text(
        json.dumps({"generated_at": generated_at, "picks": picks_data}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[picks] ピック保存: {picks_json_path} ({len(picks_data)}件)")

    cache_path = _OUTPUT_PATH.parent / "picks_race_data.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(
            {"generated_at": generated_at, "race_data": race_data_list, "stats": stats},
            ensure_ascii=False,
            cls=_DecimalEncoder,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[picks] キャッシュ保存: {cache_path}")

    _OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _OUTPUT_PATH.write_text(html_content, encoding="utf-8")
    print(f"[picks] 出力: {_OUTPUT_PATH} / 成功 {ok} / 失敗 {ng}")


if __name__ == "__main__":
    main()
