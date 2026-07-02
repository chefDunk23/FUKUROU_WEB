"""
tipster/engine.py
==================
予想家フレームワークの共通フィルター実行エンジン。

evaluate_race(race_id, strategy_path) が:
  1. 戦略 JSON をロード (load_strategy)
  2. レースコンテキストを DB から取得 (fetch_race_context)
  3. 各馬に戦略の条件を順に適用 (必須条件を1つでも落とせば候補外)
  4. ランキングして上位 N 頭を RaceEvaluation として返す
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

from .conditions import (
    CONDITION_REGISTRY,
    _class_level_from_codes,
    _class_level_from_label,
    classify_pace_prediction,
)
from .models import (
    HorseContext,
    HorseEvaluation,
    PastRaceInfo,
    PastRaceOpponent,
    RaceContext,
    RaceEvaluation,
    Strategy,
)

_STRATEGIES_DIR = Path(__file__).parent / "strategies"


# ─────────────────────────────────────────────────────────────────────────
# 戦略ロード
# ─────────────────────────────────────────────────────────────────────────


def load_strategy(strategy_path: str | Path) -> Strategy:
    """戦略 JSON をロードする。

    `strategy_path` がファイルとして存在しない場合は、
    `tipster/strategies/{strategy_path}.json` を試す（"honmei_v1" のような短縮名を許容）。
    """
    p = Path(strategy_path)
    if not p.exists():
        candidate = _STRATEGIES_DIR / (p.name if p.suffix else f"{p.name}.json")
        if candidate.exists():
            p = candidate
        else:
            raise FileNotFoundError(f"戦略ファイルが見つかりません: {strategy_path!r}")
    data = json.loads(p.read_text(encoding="utf-8"))
    return Strategy.model_validate(data)


# ─────────────────────────────────────────────────────────────────────────
# レースコンテキスト取得
# ─────────────────────────────────────────────────────────────────────────


def fetch_race_context(race_id: str) -> RaceContext:
    """race_id のレースコンテキストを取得する。

    既存 API の `race_detail_cache`（AI予測・過去走・次走成績を含む既算出データ）を
    最優先で読み、キャッシュが無ければ `_compute_payload_live` で
    fukurou_keiba_v2 から直接計算する。
    """
    payload = _load_cached_payload(race_id)
    if payload is None:
        payload = _compute_payload_live(race_id)
    return _build_race_context(race_id, payload)


def _load_cached_payload(race_id: str) -> dict | None:
    from ml.db import engine as _engine

    with _engine.connect() as conn:
        row = conn.execute(
            text("SELECT payload FROM race_detail_cache WHERE race_id = :rid "
                 "ORDER BY computed_at DESC LIMIT 1"),
            {"rid": race_id},
        ).fetchone()
    return row[0] if row else None


def _compute_payload_live(race_id: str) -> dict:
    """race_detail_cache が無い場合のフォールバック（V2アンサンブル非依存の軽量版）。

    2026-07-03 修正: 以前は api_v2.routers.races._compute_detail（V2アンサンブルの
    LightGBM推論を含む）を直接呼んでいたが、V2アンサンブル引退でその関数自体が
    削除されており ImportError になっていた（未来レースへのpicks生成が全滅する形で
    2026-07-03 の実データ検証で発覚）。tipster が実際に使うのは DB 直接クエリで
    取得可能な情報のみ（ai_score/ai_rank は元々 None でも動作する設計）のため、
    fukurou_keiba_v2 (races/race_entries) から直接構築する。
    """
    import psycopg2.extras

    from api_v2.routers._race_common import _KEIBAJO_NAME
    from api_v2.routers.races import (
        _build_race_score,
        _compute_class_label,
        _fetch_daily_time_stats,
        fetch_detail_supplements,
        fetch_opponents_next_races,
        fetch_past_5_races,
        fetch_prev_race,
    )
    from shared.db.jvdata import get_conn as get_v2_conn

    with get_v2_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT r.id AS race_id, r.race_date, r.race_num, r.keibajo_code,
                       r.distance, r.track_code, r.grade_code,
                       COALESCE(NULLIF(TRIM(r.race_name_hondai), ''), r.race_name_short_10) AS race_name,
                       e.umaban, e.wakuban, e.horse_id, e.horse_name,
                       e.basis_weight, e.horse_weight, e.tan_odds
                FROM   races r JOIN race_entries e ON e.race_id = r.id
                WHERE  r.id = %s
                ORDER  BY e.umaban
                """,
                (race_id,),
            )
            rows = cur.fetchall()

    if not rows:
        raise ValueError(f"race_id={race_id!r} のレースデータが見つかりません")

    first = rows[0]
    race_date = first["race_date"]
    horse_ids = [str(r["horse_id"]) for r in rows]

    prev_map = fetch_prev_race(horse_ids, race_date)
    past5_map, past_race_ids, race_meta_map = fetch_past_5_races(horse_ids, race_date)
    opponents_map = fetch_opponents_next_races(past_race_ids, as_of_date=race_date)
    time_stats_map = _fetch_daily_time_stats(race_meta_map)
    supp_map = fetch_detail_supplements(race_id)

    for records in past5_map.values():
        for pr in records:
            if pr.race_id and pr.race_id in opponents_map:
                pr.opponents_next_races = opponents_map[pr.race_id]
            if pr.race_id:
                meta = race_meta_map.get(pr.race_id)
                grade = meta.get("grade_code") if meta else None
                pr.race_score = _build_race_score(pr, time_stats_map.get(pr.race_id), grade)

    class_label = _compute_class_label(
        str(first.get("grade_code") or "").strip() or None,
        None, None, None, None, None,
        str(first.get("race_name") or ""),
    )

    horses: list[dict] = []
    for r in rows:
        hid = str(r["horse_id"])
        prev = prev_map.get(hid, {})
        past5 = past5_map.get(hid, [])
        supp = supp_map.get(int(r["umaban"])) if r.get("umaban") else None
        horses.append({
            "horse_id":      hid,
            "horse_name":    r.get("horse_name"),
            "umaban":        r.get("umaban"),
            "wakuban":       r.get("wakuban") or (supp or {}).get("wakuban"),
            "jockey_name":   (supp or {}).get("jockey_name"),
            "trainer_name":  (supp or {}).get("trainer_name"),
            "burden_weight": float(r["basis_weight"]) if r.get("basis_weight") is not None else None,
            "horse_weight":  r.get("horse_weight"),
            "ai_score":      None,
            "ai_rank":       None,
            "tan_odds":      float(r["tan_odds"]) if r.get("tan_odds") is not None else None,
            "extra": {
                "prev_race_grade":    prev.get("prev_race_grade"),
                "prev_race_rank":     prev.get("prev_race_rank"),
                "prev_race_days_ago": prev.get("prev_race_days_ago"),
                "chokyo_score":       None,
                "past_races":         [pr.model_dump(mode="json") for pr in past5],
            },
        })

    kc = str(first.get("keibajo_code") or "").strip().zfill(2)
    return {
        "race_name":     first.get("race_name"),
        "race_date":     str(race_date) if race_date else None,
        "distance":      first.get("distance"),
        "keibajo_name":  _KEIBAJO_NAME.get(kc, kc),
        "grade_code":    first.get("grade_code"),
        "class_label":   class_label,
        "horses":        horses,
    }


def _collect_past_race_ids(horses_raw: list[dict], limit: int = 5) -> set[str]:
    """course_fitness（過去5走）/ race_level（過去2走）が使う補足情報の取得対象 race_id を集める。"""
    ids: set[str] = set()
    for h in horses_raw:
        extra = h.get("extra") or {}
        for pr in (extra.get("past_races") or [])[:limit]:
            rid = pr.get("race_id")
            if rid:
                ids.add(_to_db_race_id(rid))
    return ids


def _fetch_past_race_extra(race_ids: set[str]) -> dict[str, dict]:
    """過去走の grade_code / place_code / jyoken_cd_3 をまとめて取得する。

    2026-07 修正: 従来 ml.db.engine (fukurou_jvdl) の races テーブル
    （JVDLフォーマット・旧スキーマ）を参照していたが、このテーブルは
    bulk_ingest_v2 が書き込まなくなって以降更新が止まっている
    「旧・未使用」テーブル（2026-06-14で停止）。実際に最新データが
    入り続けている races_v2 を参照するよう修正した。
    """
    if not race_ids:
        return {}
    from ml.db import engine as _engine

    with _engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT race_id, grade_code, keibajo_code AS place_code, jyoken_cd_3
                FROM   races_v2
                WHERE  race_id = ANY(:ids)
            """),
            {"ids": list(race_ids)},
        ).fetchall()
    return {row[0]: {"grade_code": row[1], "place_code": row[2], "jyoken_cd_3": row[3]} for row in rows}


def _parse_past_race(pr: dict, extra_map: dict[str, dict]) -> PastRaceInfo:
    rs = pr.get("race_score") or {}
    opponents = [
        PastRaceOpponent(
            horse_id=o.get("horse_id"),
            this_rank=o.get("this_rank"),
            this_margin=o.get("this_margin"),
            next_race_rank=o.get("next_race_rank"),
        )
        for o in (pr.get("opponents_next_races") or [])
    ]
    raw_race_id = pr.get("race_id")
    db_extra = extra_map.get(_to_db_race_id(raw_race_id), {}) if raw_race_id else {}
    grade_code = db_extra.get("grade_code")
    jyoken_cd_3 = db_extra.get("jyoken_cd_3")
    return PastRaceInfo(
        race_id=raw_race_id,
        date=pr.get("date"),
        rank=pr.get("rank"),
        distance=pr.get("distance"),
        surface=pr.get("surface"),
        head_count=pr.get("head_count"),
        race_name=pr.get("race_name"),
        class_score=rs.get("class_score"),
        time_score=rs.get("time_score"),
        member_level_score=rs.get("member_level_score"),
        opponents_next_races=opponents,
        grade_code=grade_code,
        place_code=db_extra.get("place_code"),
        jyoken_cd_3=jyoken_cd_3,
        class_level=_class_level_from_codes(grade_code, jyoken_cd_3),
    )


def _fetch_race_meta(race_id: str) -> dict:
    """2026-07 修正: races（旧・未使用テーブル）ではなく races_v2 を参照する。
    races_v2 には course_type(日本語表記)・date(DATE型)の列が無いため、
    track_code から変換し、race_id の先頭8桁から日付を復元する。
    """
    from ml.db import engine as _engine
    from api_v2.routers._race_common import _surface_str

    with _engine.connect() as conn:
        row = conn.execute(
            text("""
                SELECT keibajo_code, distance, track_code, jyoken_cd_3
                FROM   races_v2
                WHERE  race_id = :rid
            """),
            {"rid": race_id},
        ).fetchone()
    if row is None:
        return {}
    race_date = None
    rid_str = str(race_id)
    if len(rid_str) >= 8:
        try:
            race_date = datetime.strptime(rid_str[:8], "%Y%m%d").date()
        except ValueError:
            race_date = None
    return {
        "place_code": row[0], "distance": row[1], "course_type": _surface_str(row[2]), "date": race_date,
        "jyoken_cd_3": row[3],
    }


# course_profile_store.surface は英語表記("turf"/"dirt")。races.course_type は日本語("芝"/"ダート")。
_SURFACE_JA_TO_EN = {"芝": "turf", "ダート": "dirt"}


def _fetch_track_bias(race_id: str, meta: dict) -> dict:
    """track_bias_pit（実測 PiT）優先、無ければ course_profile_store の脚質別勝率から推定する。"""
    from ml.db import engine as _engine

    with _engine.connect() as conn:
        row = conn.execute(
            text("SELECT front_bias_pit, inner_bias_pit FROM track_bias_pit WHERE race_id = :rid"),
            {"rid": race_id},
        ).fetchone()
        if row is not None:
            return {"front_bias_pit": row[0], "inner_bias_pit": row[1], "source": "track_bias_pit"}

        place_code, distance, race_date = meta.get("place_code"), meta.get("distance"), meta.get("date")
        surface = _SURFACE_JA_TO_EN.get(meta.get("course_type"))
        if not (place_code and distance and surface and race_date):
            return {"source": "none"}

        row2 = conn.execute(
            text(
                "SELECT style_nige_win_rate, style_senko_win_rate, "
                "style_sashi_win_rate, style_oikomi_win_rate "
                "FROM course_profile_store "
                "WHERE place_code = :pc AND distance = :dist AND surface = :surf "
                "AND target_date <= :rd ORDER BY target_date DESC LIMIT 1"
            ),
            {"pc": place_code, "dist": distance, "surf": surface, "rd": race_date},
        ).fetchone()
        if row2 is None:
            return {"source": "none"}

        nige, senko, sashi, oikomi = (v or 0.0 for v in row2)
        front_bias = ((nige + senko) / 2) - ((sashi + oikomi) / 2)
        return {"front_bias_pit": front_bias, "inner_bias_pit": None, "source": "course_profile_store"}


_BABA_CODE_MAP: dict[str, str] = {"1": "良", "2": "稍重", "3": "重", "4": "不良"}


def _fetch_baba_supplement_batch(horse_ids: list[str], race_date: str) -> dict[str, dict]:
    """JVDL から馬場別実績 + 種牡馬馬場適性を一括取得する（BET-7 馬場条件用）。

    PIT ルール: race_date より前のデータのみ参照（当該レース結果は含まない）。

    Returns: {horse_id: {
        "baba_record": {"良": (runs, placed), ...},
        "sire_id": str | None,
        "sire_baba_top3": {"良": top3_rate_shift, ...},  # 全体平均との差
    }}
    """
    import psycopg2
    from shared.config import DB_JVDL

    if not horse_ids:
        return {}

    _BABA_LABELS = ["良", "稍重", "重", "不良"]
    _CODE_TO_LABEL = {"1": "良", "2": "稍重", "3": "重", "4": "不良"}

    # ── 1. 馬場別過去成績（PIT: race_date より前のみ）────────────────────────
    baba_records: dict[str, dict[str, list[int, int]]] = {hid: {} for hid in horse_ids}
    try:
        conn = psycopg2.connect(**DB_JVDL)
        cur = conn.cursor()
        # ダート/芝の両馬場コードを OR で取る（どちらか一方が設定）
        # race_date (YYYY-MM-DD) → '20260627' 形式に変換して race_id 先頭8桁と比較 (PIT)
        race_date_compact = race_date.replace("-", "") if race_date else ""
        cur.execute(
            """
            SELECT
                re.blood_no,
                COALESCE(NULLIF(TRIM(r.dirt_baba_code::text), ''), NULLIF(TRIM(r.shiba_baba_code::text), '')) AS baba_code,
                COUNT(*) AS runs,
                SUM(CASE WHEN re.kakutei_chakujun BETWEEN 1 AND 3 THEN 1 ELSE 0 END) AS placed
            FROM race_entries_v2 re
            JOIN races_v2 r ON re.race_id = r.race_id
            WHERE re.blood_no = ANY(%s)
              AND re.kakutei_chakujun > 0
              AND SUBSTRING(r.race_id::text, 1, 8) < %s
              AND (
                  (r.dirt_baba_code IS NOT NULL AND TRIM(r.dirt_baba_code::text) NOT IN ('', '0'))
                  OR
                  (r.shiba_baba_code IS NOT NULL AND TRIM(r.shiba_baba_code::text) NOT IN ('', '0'))
              )
            GROUP BY re.blood_no, baba_code
            """,
            (horse_ids, race_date_compact),
        )
        for blood_no, baba_code, runs, placed in cur.fetchall():
            label = _CODE_TO_LABEL.get(str(baba_code or "").strip())
            if label and blood_no in baba_records:
                baba_records[blood_no][label] = [int(runs), int(placed)]

        # ── 2. 種牡馬 ID 取得（horse_blood_tree）────────────────────────────
        cur.execute(
            "SELECT horse_id, sire_id FROM horse_blood_tree WHERE horse_id = ANY(%s)",
            (horse_ids,),
        )
        sire_map: dict[str, str | None] = {hid: None for hid in horse_ids}
        for horse_id, sire_id in cur.fetchall():
            sire_map[horse_id] = sire_id

        # ── 3. 種牡馬馬場適性 + 会場適性（sire_feature_store PIT）──────────
        sire_ids = [sid for sid in sire_map.values() if sid]
        sire_baba: dict[str, dict[str, float]] = {}
        sire_venue: dict[str, dict[str, float]] = {}
        if sire_ids:
            # PIT スナップショット（レース日以前の最新）を取得
            cur.execute(
                """
                SELECT DISTINCT ON (sire_id)
                    sire_id,
                    baba_firm_top3_rate, baba_firm_top3_shift,
                    baba_yaya_top3_rate, baba_yaya_top3_shift,
                    baba_omo_top3_rate, baba_omo_top3_shift,
                    baba_furyo_top3_rate, baba_furyo_top3_shift,
                    top3_rate,
                    venue_01_top3_rate, venue_01_count,
                    venue_02_top3_rate, venue_02_count,
                    venue_03_top3_rate, venue_03_count,
                    venue_04_top3_rate, venue_04_count,
                    venue_05_top3_rate, venue_05_count,
                    venue_06_top3_rate, venue_06_count,
                    venue_07_top3_rate, venue_07_count,
                    venue_08_top3_rate, venue_08_count,
                    venue_09_top3_rate, venue_09_count,
                    venue_10_top3_rate, venue_10_count
                FROM sire_feature_store
                WHERE sire_id = ANY(%s)
                  AND target_date <= %s
                ORDER BY sire_id, target_date DESC
                """,
                (sire_ids, race_date),
            )
            for row in cur.fetchall():
                sid = row[0]
                sire_baba[sid] = {
                    "良":  float(row[2] or 0),   # baba_firm_top3_shift
                    "稍重": float(row[4] or 0),   # baba_yaya_top3_shift
                    "重":  float(row[6] or 0),    # baba_omo_top3_shift
                    "不良": float(row[8] or 0),   # baba_furyo_top3_shift
                }
                overall = float(row[9] or 0)
                ven: dict[str, float] = {"overall": overall}
                for i, pc in enumerate([f"{n:02d}" for n in range(1, 11)]):
                    rate_idx = 10 + i * 2       # row[10], row[12], ... row[28]
                    cnt_idx  = 10 + i * 2 + 1   # row[11], row[13], ... row[29]
                    rate = row[rate_idx]
                    cnt  = row[cnt_idx]
                    if rate is not None and cnt is not None and int(cnt) >= 10:
                        ven[pc] = float(rate)
                sire_venue[sid] = ven

        cur.close()
        conn.close()
    except Exception:
        # DB接続失敗時はフォールバック（条件は passed=None になる）
        return {hid: {"baba_record": {}, "sire_id": None, "sire_baba_top3": {}, "sire_venue_top3": {}} for hid in horse_ids}

    result = {}
    for hid in horse_ids:
        sid = sire_map.get(hid)
        result[hid] = {
            "baba_record": {k: tuple(v) for k, v in baba_records[hid].items()},
            "sire_id": sid,
            "sire_baba_top3": sire_baba.get(sid, {}) if sid else {},
            "sire_venue_top3": sire_venue.get(sid, {}) if sid else {},
        }
    return result


def _to_db_race_id(race_id: str) -> str:
    """race_detail_cache の payload.extra.past_races[].race_id (JV-Data 生形式・16桁) を返す。

    2026-07 修正: 以前は races.id の12桁形式（日付8+場2+R番2、旧・未使用テーブル用）に
    変換していたが、参照先を races_v2/race_entries_v2（16桁ネイティブ）に統一したため
    変換は不要になった。呼び出し元の互換のため関数自体は残し、恒等関数にしている。
    """
    return race_id


def _fetch_supplementary(race_id: str, meta: dict, horses_raw: list[dict]) -> dict[str, dict]:
    """race_detail_cache の payload に含まれない補足情報（ID/前走斤量/騎手乗り替わり判定）を取得する。"""
    from ml.db import engine as _engine

    race_date = meta.get("date")
    place_code = meta.get("place_code")
    out: dict[str, dict] = {}

    # 2026-07 修正: race_entries/races（旧・未使用テーブル）ではなく
    # race_entries_v2/races_v2 を参照する。列名対応: horse_id→blood_no,
    # jockey_id→kishu_code, trainer_id→chokyosi_code, weight→kinryo(0.1kg単位のためkg換算),
    # races.date→races_v2.race_id 先頭8桁, races.place_code→races_v2.keibajo_code。
    race_date_compact = race_date.strftime("%Y%m%d") if race_date else None

    with _engine.connect() as conn:
        for h in horses_raw:
            hid = h["horse_id"]
            entry: dict = {}

            row = conn.execute(
                text("SELECT kishu_code, chokyosi_code FROM race_entries_v2 WHERE race_id=:rid AND blood_no=:hid"),
                {"rid": race_id, "hid": hid},
            ).fetchone()
            jockey_id, trainer_id = (row[0], row[1]) if row else (None, None)
            entry["jockey_id"], entry["trainer_id"] = jockey_id, trainer_id

            extra = h.get("extra") or {}
            past_races = extra.get("past_races") or []
            prev_race_id = _to_db_race_id(past_races[0]["race_id"]) if past_races else None

            prev_jockey_id = None
            if prev_race_id:
                prow = conn.execute(
                    text("SELECT kinryo, kishu_code FROM race_entries_v2 WHERE race_id=:rid AND blood_no=:hid"),
                    {"rid": prev_race_id, "hid": hid},
                ).fetchone()
                if prow:
                    entry["prev_burden_weight"] = float(prow[0]) / 10.0 if prow[0] is not None else None
                    prev_jockey_id = prow[1]
                    entry["prev_jockey_id"] = prev_jockey_id

            if prev_jockey_id and jockey_id and prev_jockey_id != jockey_id:
                step1 = conn.execute(
                    text("SELECT 1 FROM race_entries_v2 WHERE race_id=:rid AND kishu_code=:jid "
                         "AND blood_no != :hid LIMIT 1"),
                    {"rid": race_id, "jid": prev_jockey_id, "hid": hid},
                ).fetchone() is not None
                entry["step1"] = step1

                step2 = False
                if not step1 and race_date_compact and place_code:
                    step2 = conn.execute(
                        text("SELECT 1 FROM race_entries_v2 se JOIN races_v2 r ON se.race_id = r.race_id "
                             "WHERE LEFT(r.race_id, 8) = :rd AND se.kishu_code = :jid "
                             "AND r.keibajo_code != :pc LIMIT 1"),
                        {"rd": race_date_compact, "jid": prev_jockey_id, "pc": place_code},
                    ).fetchone() is not None
                entry["step2"] = step2

                if trainer_id and jockey_id and race_date:
                    arow = conn.execute(
                        text("SELECT combo_count, combo_win_rate, combo_top3_rate FROM synergy_store "
                             "WHERE trainer_id=:tid AND jockey_id=:jid AND target_date <= :rd "
                             "ORDER BY target_date DESC LIMIT 1"),
                        {"tid": trainer_id, "jid": jockey_id, "rd": race_date},
                    ).fetchone()
                    if arow:
                        entry["affinity"] = {
                            "combo_count": arow[0], "combo_win_rate": arow[1], "combo_top3_rate": arow[2],
                        }

            if jockey_id:
                jrow = conn.execute(
                    text("SELECT yr_wins, career_wins FROM jockeys WHERE id=:jid"),
                    {"jid": jockey_id},
                ).fetchone()
                if jrow:
                    entry["jockey_yr_wins"], entry["jockey_career_wins"] = jrow[0], jrow[1]

            if jockey_id and place_code and re.fullmatch(r"(0[1-9]|10)", str(place_code)):
                venue_col = f"venue_{place_code}_win_rate"  # place_code は上の正規表現で検証済み
                vrow = conn.execute(
                    text(
                        f"SELECT win_rate, {venue_col} FROM jockey_feature_store "
                        "WHERE kishu_code=:jid AND target_date <= :rd "
                        "ORDER BY target_date DESC LIMIT 1"
                    ),
                    {"jid": jockey_id, "rd": race_date},
                ).fetchone()
                if vrow:
                    entry["jockey_overall_win_rate"], entry["jockey_venue_win_rate"] = vrow[0], vrow[1]

            out[hid] = entry
    return out


def _build_race_context(race_id: str, payload: dict) -> RaceContext:
    horses_raw = payload.get("horses") or []
    meta = _fetch_race_meta(race_id)
    bias = _fetch_track_bias(race_id, meta)
    supp = _fetch_supplementary(race_id, meta, horses_raw)
    extra_map = _fetch_past_race_extra(_collect_past_race_ids(horses_raw))
    # BET-7: 馬場別実績 + 種牡馬馬場適性（PIT-safe）
    race_date = meta.get("date") or (race_id[:8] if len(race_id) >= 8 else "")
    horse_ids_for_baba = [h["horse_id"] for h in horses_raw]
    baba_supp = _fetch_baba_supplement_batch(horse_ids_for_baba, race_date)

    horses: list[HorseContext] = []
    for h in horses_raw:
        hid = h["horse_id"]
        extra = h.get("extra") or {}
        s = supp.get(hid, {})
        bs = baba_supp.get(hid, {})
        horses.append(HorseContext(
            horse_id=hid,
            horse_name=h.get("horse_name"),
            umaban=h.get("umaban"),
            wakuban=h.get("wakuban"),
            jockey_id=s.get("jockey_id"),
            jockey_name=h.get("jockey_name"),
            trainer_id=s.get("trainer_id"),
            trainer_name=h.get("trainer_name"),
            burden_weight=h.get("burden_weight"),
            horse_weight=h.get("horse_weight"),
            ai_score=h.get("ai_score"),
            ai_rank=h.get("ai_rank"),
            chokyo_score=extra.get("chokyo_score"),
            position_tendency=extra.get("position_tendency"),
            prev_race_rank=extra.get("prev_race_rank"),
            prev_race_grade=extra.get("prev_race_grade"),
            prev_race_days_ago=extra.get("prev_race_days_ago"),
            past_races=[_parse_past_race(pr, extra_map) for pr in (extra.get("past_races") or [])],
            tan_odds=h.get("tan_odds"),
            prev_burden_weight=s.get("prev_burden_weight"),
            prev_jockey_id=s.get("prev_jockey_id"),
            jockey_yr_wins=s.get("jockey_yr_wins"),
            jockey_career_wins=s.get("jockey_career_wins"),
            jockey_change_step1_same_race=s.get("step1", False),
            jockey_change_step2_other_venue=s.get("step2", False),
            jockey_change_affinity=s.get("affinity"),
            jockey_venue_win_rate=s.get("jockey_venue_win_rate"),
            jockey_overall_win_rate=s.get("jockey_overall_win_rate"),
            # 海外/地方帰り判定はバックテスト(軽量パス)限定の実装。ライブパスでは
            # past_races[0].place_code（DB補完済み）による簡易判定に委ねる。
            overseas_interim_place_code=None,
            # BET-7: 馬場別実績 + 種牡馬馬場適性
            baba_record=bs.get("baba_record") or None,
            sire_id=bs.get("sire_id"),
            sire_baba_top3=bs.get("sire_baba_top3") or None,
            # Phase 2 S-1: 種牡馬会場適性（v2_sire_venue 条件用）
            sire_venue_top3=bs.get("sire_venue_top3") or None,
        ))

    race_grade_code = payload.get("grade_code")
    race_jyoken_cd_3 = meta.get("jyoken_cd_3")
    race_class_label = payload.get("class_label")
    # payload.grade_code は races.grade_code(A/B/C/L) とは別の数値エンコーディングのため
    # class_level 判定には使えない。class_label（"G1"等の文字列、生成済みで信頼できる）を優先する。
    race_class_level = _class_level_from_label(race_class_label)
    if race_class_level is None:
        race_class_level = _class_level_from_codes(race_grade_code, race_jyoken_cd_3)

    return RaceContext(
        race_id=race_id,
        race_name=payload.get("race_name"),
        race_date=payload.get("race_date"),
        place_code=meta.get("place_code"),
        keibajo_name=payload.get("keibajo_name"),
        distance=payload.get("distance"),
        surface=meta.get("course_type"),
        class_label=race_class_label,
        grade_code=race_grade_code,
        jyoken_cd_3=race_jyoken_cd_3,
        class_level=race_class_level,
        pace_prediction=classify_pace_prediction(horses),
        horses=horses,
        front_bias_pit=bias.get("front_bias_pit"),
        inner_bias_pit=bias.get("inner_bias_pit"),
        bias_source=bias.get("source", "none"),
    )


# ─────────────────────────────────────────────────────────────────────────
# 評価エンジン
# ─────────────────────────────────────────────────────────────────────────


def _ranking_metric(ev: HorseEvaluation, name: str) -> float:
    if name == "condition_clear_count":
        return float(ev.clear_count)
    if name == "ai_score":
        return ev.ai_score
    if name == "total_score":
        return ev.total_score
    return 0.0


def select_honmei(
    candidates: list[HorseEvaluation],
    umaban_map: dict[str, int | None],
    min_total_score: float | None = None,
    max_candidates_for_honmei: int | None = None,
) -> HorseEvaluation | None:
    """本命選定ルール: 条件クリア数 → 合計スコア → AIスコア → 馬番(若い方)、の順で決定的に1頭選ぶ。

    candidates は除外されていない馬の全件（max_selections による上位カット前）を渡すこと。
    - max_candidates_for_honmei 指定時: 候補数がこれを超えるレースは「足切りが効いていない
      = 自信度が低い」とみなし、本命なし(None)を返す。
    - min_total_score 指定時: 合計スコアがこれ未満の馬は本命候補から除外する。
      全馬が閾値未満なら本命なし(None)。
    """
    if max_candidates_for_honmei is not None and len(candidates) > max_candidates_for_honmei:
        return None
    pool = candidates
    if min_total_score is not None:
        pool = [c for c in candidates if c.total_score >= min_total_score]
    if not pool:
        return None
    return min(
        pool,
        key=lambda c: (
            -c.clear_count,
            -c.total_score,
            -c.ai_score,
            umaban_map.get(c.horse_id) if umaban_map.get(c.horse_id) is not None else 9999,
        ),
    )


def select_aite(
    candidates: list[HorseEvaluation],
    honmei_horse_id: str | None = None,
    max_aite: int | None = None,
) -> list[HorseEvaluation]:
    """相手選定: 候補馬リストから本命を除いた上位N頭を返す（BET-2）。

    candidates は相手選定戦略（anaba系）で evaluate_race_context() した結果の
    candidates（除外されていない馬）をそのまま渡すこと。戦略側のランキング順が維持される。

    - honmei_horse_id: 本命馬の horse_id（相手候補から除外する）。None なら除外なし。
    - max_aite: 最大相手頭数。None なら候補を全員返す。
      戦略 JSON の max_selections が既に上位カット済みの場合はこれを渡さなくてよい。
    """
    pool = [c for c in candidates if c.horse_id != honmei_horse_id]
    return pool[:max_aite] if max_aite is not None else pool


def compute_confidence(honmei: HorseEvaluation | None, eligible_count: int) -> str:
    """本命の自信度を S/A/B/C でラベル化する（AY-3）。

    honmei が None（本命なし）の場合は常に "C"（様子見推奨）。
    min_total_score/max_candidates_for_honmei によるゲートを適用した本命に対して計算すると、
    ゲート自体がB/C相当のケースを既に除外しているため実質 S/A/C にしかならない
    （バックテストでの閾値検証目的にはゲート無しの本命で計算すること）。
    """
    if honmei is None:
        return "C"
    score = honmei.total_score
    if score >= 5.0 and eligible_count <= 5:
        return "S"
    if score >= 3.0 and eligible_count <= 8:
        return "A"
    if score >= 2.0:
        return "B"
    return "C"


def evaluate_race_context(
    race_ctx: RaceContext, strategy: Strategy, max_selections: int | None = None
) -> RaceEvaluation:
    """既に構築済みの RaceContext に戦略を適用する（DB アクセスなし・純粋関数）。

    tipster/backtest.py の軽量コンテキスト（DB直接クエリ由来、_compute_detail不使用）にも
    そのまま使えるよう、DB取得処理 (fetch_race_context) とは独立させている。

    max_selections: 指定時は strategy.ranking.max_selections を上書きする。
        バックテストで「除外されていない馬を全件取得し、本命選定ルールを別途適用したい」
        ケース向け（例: len(race_ctx.horses) を渡して全件取得）。
    """
    results: list[HorseEvaluation] = []
    for horse in race_ctx.horses:
        ev = HorseEvaluation(horse_id=horse.horse_id, horse_name=horse.horse_name, ai_score=horse.ai_score or 0.0)
        for cond_cfg in strategy.conditions:
            if not cond_cfg.enabled:
                continue
            fn = CONDITION_REGISTRY.get(cond_cfg.id)
            if fn is None:
                continue
            result = fn(horse, race_ctx, cond_cfg.params)
            ev.conditions.append(result)
            # passed=None（判定不能・保留）は失格させない（BET-6）。明確に False の場合のみ失格。
            if cond_cfg.required and result.passed is False:
                ev.eliminated = True
                ev.elimination_reason = f"{cond_cfg.id}: {result.reason}"
                break
        results.append(ev)

    candidates = [r for r in results if not r.eliminated]
    eliminated = [r for r in results if r.eliminated]

    candidates.sort(
        key=lambda ev: (
            -_ranking_metric(ev, strategy.ranking.primary),
            -_ranking_metric(ev, strategy.ranking.secondary),
        )
    )

    eligible_count = len(candidates)
    umaban_map = {h.horse_id: h.umaban for h in race_ctx.horses}
    honmei = select_honmei(
        candidates, umaban_map,
        min_total_score=strategy.ranking.min_total_score,
        max_candidates_for_honmei=strategy.ranking.max_candidates_for_honmei,
    )
    confidence = compute_confidence(honmei, eligible_count)

    cap = max_selections if max_selections is not None else strategy.ranking.max_selections
    return RaceEvaluation(
        race_id=race_ctx.race_id,
        race_name=race_ctx.race_name,
        strategy=strategy.name,
        strategy_version=strategy.version,
        generated_at=datetime.now().isoformat(timespec="seconds"),
        candidates=candidates[:cap],
        eliminated_horses=eliminated,
        eliminated_count=len(eliminated),
        honmei=honmei,
        eligible_count=eligible_count,
        confidence=confidence,
    )


def evaluate_race(race_id: str, strategy: str | Path | Strategy) -> RaceEvaluation:
    """1レースに戦略を適用し、候補馬ランキングを返す（DB取得 + 評価）。"""
    strat = strategy if isinstance(strategy, Strategy) else load_strategy(strategy)
    race_ctx = fetch_race_context(race_id)
    return evaluate_race_context(race_ctx, strat)


# ─────────────────────────────────────────────────────────────────────────
# CLI エントリポイント
# ─────────────────────────────────────────────────────────────────────────


def _cli() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="予想家(Tipster)評価エンジン")
    parser.add_argument("--race-id", required=True, help="評価対象の race_id")
    parser.add_argument("--strategy", default="honmei_v1", help="戦略名 (tipster/strategies/*.json)")
    parser.add_argument("--output", default=None, help="出力HTMLパス（省略時は data/output/tipster/ 配下）")
    args = parser.parse_args()

    evaluation = evaluate_race(args.race_id, args.strategy)

    from .renderer import render_race_html

    output_path = args.output or f"data/output/tipster/{args.strategy}_{args.race_id}.html"
    path = render_race_html(evaluation, output_path)
    print(
        f"生成完了: {path} "
        f"(候補{len(evaluation.candidates)}頭 / 除外{evaluation.eliminated_count}頭)"
    )


if __name__ == "__main__":
    _cli()
