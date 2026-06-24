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
    """過去走の grade_code / place_code / jyoken_cd_3 をまとめて取得する。"""
    if not race_ids:
        return {}
    from ml.db import engine as _engine

    with _engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, grade_code, place_code, jyoken_cd_3 FROM races WHERE id = ANY(:ids)"),
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
    from ml.db import engine as _engine

    with _engine.connect() as conn:
        row = conn.execute(
            text("SELECT place_code, distance, course_type, date, jyoken_cd_3 FROM races WHERE id = :rid"),
            {"rid": race_id},
        ).fetchone()
    if row is None:
        return {}
    return {
        "place_code": row[0], "distance": row[1], "course_type": row[2], "date": row[3],
        "jyoken_cd_3": row[4],
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
            if cond_cfg.required and not result.passed:
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
