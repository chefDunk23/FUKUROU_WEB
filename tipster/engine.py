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
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

from .conditions import CONDITION_REGISTRY
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
    最優先で読み、キャッシュが無ければ `api_v2.routers.races._compute_detail` を
    直接呼び出して計算する（既存ロジックの再利用）。
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
    from api_v2.routers.races import _compute_detail

    resp = _compute_detail(race_id)
    if resp is None:
        raise ValueError(f"race_id={race_id!r} のレースデータが見つかりません")
    return resp.model_dump(mode="json")


def _collect_past_race_ids(horses_raw: list[dict], limit: int = 2) -> set[str]:
    """race_level の前走/前々走判定に使う grade_code 取得対象の race_id（DB形式に変換済み）を集める。"""
    ids: set[str] = set()
    for h in horses_raw:
        extra = h.get("extra") or {}
        for pr in (extra.get("past_races") or [])[:limit]:
            rid = pr.get("race_id")
            if rid:
                ids.add(_to_db_race_id(rid))
    return ids


def _fetch_grade_codes(race_ids: set[str]) -> dict[str, str | None]:
    if not race_ids:
        return {}
    from ml.db import engine as _engine

    with _engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, grade_code FROM races WHERE id = ANY(:ids)"),
            {"ids": list(race_ids)},
        ).fetchall()
    return {row[0]: row[1] for row in rows}


def _parse_past_race(pr: dict, grade_map: dict[str, str | None]) -> PastRaceInfo:
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
    grade_code = grade_map.get(_to_db_race_id(raw_race_id)) if raw_race_id else None
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
    )


def _fetch_race_meta(race_id: str) -> dict:
    from ml.db import engine as _engine

    with _engine.connect() as conn:
        row = conn.execute(
            text("SELECT place_code, distance, course_type, date FROM races WHERE id = :rid"),
            {"rid": race_id},
        ).fetchone()
    if row is None:
        return {}
    return {"place_code": row[0], "distance": row[1], "course_type": row[2], "date": row[3]}


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


def _to_db_race_id(race_id: str) -> str:
    """JV-Data 16桁 race_id (日付8+場2+回2+日2+R番2) を races.id の12桁形式 (日付8+場2+R番2) に変換する。

    race_detail_cache の payload.extra.past_races[].race_id は JV-Data 生形式(16桁)で
    格納されているため、DB_JVDL の races/race_entries を直接引く際は変換が必要。
    """
    if len(race_id) == 16:
        return race_id[:10] + race_id[14:16]
    return race_id


def _fetch_supplementary(race_id: str, meta: dict, horses_raw: list[dict]) -> dict[str, dict]:
    """race_detail_cache の payload に含まれない補足情報（ID/前走斤量/騎手乗り替わり判定）を取得する。"""
    from ml.db import engine as _engine

    race_date = meta.get("date")
    place_code = meta.get("place_code")
    out: dict[str, dict] = {}

    with _engine.connect() as conn:
        for h in horses_raw:
            hid = h["horse_id"]
            entry: dict = {}

            row = conn.execute(
                text("SELECT jockey_id, trainer_id FROM race_entries WHERE race_id=:rid AND horse_id=:hid"),
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
                    text("SELECT weight, jockey_id FROM race_entries WHERE race_id=:rid AND horse_id=:hid"),
                    {"rid": prev_race_id, "hid": hid},
                ).fetchone()
                if prow:
                    entry["prev_burden_weight"] = prow[0]
                    prev_jockey_id = prow[1]
                    entry["prev_jockey_id"] = prev_jockey_id

            if prev_jockey_id and jockey_id and prev_jockey_id != jockey_id:
                step1 = conn.execute(
                    text("SELECT 1 FROM race_entries WHERE race_id=:rid AND jockey_id=:jid "
                         "AND horse_id != :hid LIMIT 1"),
                    {"rid": race_id, "jid": prev_jockey_id, "hid": hid},
                ).fetchone() is not None
                entry["step1"] = step1

                step2 = False
                if not step1 and race_date and place_code:
                    step2 = conn.execute(
                        text("SELECT 1 FROM race_entries se JOIN races r ON se.race_id = r.id "
                             "WHERE r.date = :rd AND se.jockey_id = :jid AND r.place_code != :pc LIMIT 1"),
                        {"rd": race_date, "jid": prev_jockey_id, "pc": place_code},
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

            out[hid] = entry
    return out


def _build_race_context(race_id: str, payload: dict) -> RaceContext:
    horses_raw = payload.get("horses") or []
    meta = _fetch_race_meta(race_id)
    bias = _fetch_track_bias(race_id, meta)
    supp = _fetch_supplementary(race_id, meta, horses_raw)
    grade_map = _fetch_grade_codes(_collect_past_race_ids(horses_raw))

    horses: list[HorseContext] = []
    for h in horses_raw:
        hid = h["horse_id"]
        extra = h.get("extra") or {}
        s = supp.get(hid, {})
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
            past_races=[_parse_past_race(pr, grade_map) for pr in (extra.get("past_races") or [])],
            tan_odds=h.get("tan_odds"),
            prev_burden_weight=s.get("prev_burden_weight"),
            prev_jockey_id=s.get("prev_jockey_id"),
            jockey_yr_wins=s.get("jockey_yr_wins"),
            jockey_career_wins=s.get("jockey_career_wins"),
            jockey_change_step1_same_race=s.get("step1", False),
            jockey_change_step2_other_venue=s.get("step2", False),
            jockey_change_affinity=s.get("affinity"),
        ))

    return RaceContext(
        race_id=race_id,
        race_name=payload.get("race_name"),
        race_date=payload.get("race_date"),
        place_code=meta.get("place_code"),
        keibajo_name=payload.get("keibajo_name"),
        distance=payload.get("distance"),
        surface=meta.get("course_type"),
        class_label=payload.get("class_label"),
        grade_code=payload.get("grade_code"),
        horses=horses,
        front_bias_pit=bias.get("front_bias_pit"),
        inner_bias_pit=bias.get("inner_bias_pit"),
        bias_source=bias.get("source", "none"),
    )


# ─────────────────────────────────────────────────────────────────────────
# 評価エンジン
# ─────────────────────────────────────────────────────────────────────────


def evaluate_race(race_id: str, strategy: str | Path | Strategy) -> RaceEvaluation:
    """1レースに戦略を適用し、候補馬ランキングを返す。"""
    strat = strategy if isinstance(strategy, Strategy) else load_strategy(strategy)
    race_ctx = fetch_race_context(race_id)

    results: list[HorseEvaluation] = []
    for horse in race_ctx.horses:
        ev = HorseEvaluation(horse_id=horse.horse_id, horse_name=horse.horse_name, ai_score=horse.ai_score or 0.0)
        for cond_cfg in strat.conditions:
            if not cond_cfg.enabled:
                continue
            fn = CONDITION_REGISTRY.get(cond_cfg.id)
            if fn is None:
                continue
            result = fn(horse, race_ctx, cond_cfg.params)
            ev.conditions.append(result)
            if cond_cfg.required and not result.passed:
                ev.eliminated = True
                ev.elimination_reason = f"{cond_cfg.id}: {result.reason}"
                break
        results.append(ev)

    candidates = [r for r in results if not r.eliminated]
    eliminated = [r for r in results if r.eliminated]

    def _metric(ev: HorseEvaluation, name: str) -> float:
        if name == "condition_clear_count":
            return float(ev.clear_count)
        if name == "ai_score":
            return ev.ai_score
        if name == "total_score":
            return ev.total_score
        return 0.0

    candidates.sort(
        key=lambda ev: (-_metric(ev, strat.ranking.primary), -_metric(ev, strat.ranking.secondary))
    )

    return RaceEvaluation(
        race_id=race_id,
        race_name=race_ctx.race_name,
        strategy=strat.name,
        strategy_version=strat.version,
        generated_at=datetime.now().isoformat(timespec="seconds"),
        candidates=candidates[: strat.ranking.max_selections],
        eliminated_horses=eliminated,
        eliminated_count=len(eliminated),
    )


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
