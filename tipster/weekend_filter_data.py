"""
tipster/weekend_filter_data.py
================================
今週末レースに対する3条件ロジック（本命/相手/調教のみ）の適用結果を
データとして組み立てるアセンブリ層。

設計方針:
  - 条件ロジックの呼び出し（本ファイル）と HTML生成（weekend_filter_renderer.py）を分離する。
    将来「見たい条件を選ぶ」UIに発展する際、本ファイルはそのまま再利用できる
    （renderer 側だけ作り直せばよい）。
  - 本ファイルは DB アクセスと既存ロジック（select_honmei/select_aite/
    rank_horses_by_training）の呼び出しのみを行う。買い目構築は行わない。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import text

from .engine import evaluate_race_context, fetch_race_context, load_strategy, select_aite
from .training_ranker import SlopeRow, WoodRow, load_config, rank_horses_by_training

# 調教データの取得ウィンドウ（レース当日からの遡り日数）。
# 条件⑤（前週6-8日前の坂路データ）をカバーできる十分な余裕を持たせる。
_TRAINING_LOOKBACK_DAYS = 30


@dataclass(frozen=True)
class HonmeiRow:
    umaban: str | None
    horse_name: str
    is_honmei: bool
    clear_count: int
    total_score: float
    ai_score: float


@dataclass(frozen=True)
class AiteRow:
    umaban: str | None
    horse_name: str
    total_score: float
    ai_score: float


@dataclass(frozen=True)
class TrainingRow:
    umaban: str | None
    horse_name: str
    priority: int
    condition_label: str
    rank: int
    tiebreak_time_sec: float | None


@dataclass(frozen=True)
class RaceFilterResult:
    race_id: str
    race_name: str
    honmei_rows: list[HonmeiRow] = field(default_factory=list)
    aite_rows: list[AiteRow] = field(default_factory=list)
    training_rows: list[TrainingRow] = field(default_factory=list)
    training_error: str | None = None


def _fetch_blood_no_map(race_id: str) -> dict[str, tuple[str, str]]:
    """race_entries_v2 から umaban -> (blood_no, horse_name) のマップを返す。"""
    from ml.db import engine as _engine

    with _engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT umaban, blood_no, horse_name FROM race_entries_v2 "
                "WHERE race_id = :rid AND blood_no IS NOT NULL"
            ),
            {"rid": race_id},
        ).fetchall()
    return {str(r[0]): (r[1], r[2] or "") for r in rows}


def _fetch_training_rows_by_blood(
    blood_nos: list[str], race_date: str
) -> tuple[dict[str, list[SlopeRow]], dict[str, list[WoodRow]]]:
    """training_slope / training_wood から対象馬の直近データを取得する。"""
    from ml.db import engine as _engine

    since = (date(int(race_date[:4]), int(race_date[4:6]), int(race_date[6:8]))
             - timedelta(days=_TRAINING_LOOKBACK_DAYS)).strftime("%Y%m%d")

    slope_by: dict[str, list[SlopeRow]] = {bn: [] for bn in blood_nos}
    wood_by: dict[str, list[WoodRow]] = {bn: [] for bn in blood_nos}

    with _engine.connect() as conn:
        slope_rows = conn.execute(
            text(
                "SELECT blood_no, chokyo_date, chokyo_time, center_cd, "
                "       time_4f, lap_l4_l3, lap_l3_l2, lap_l2_l1, lap_l1 "
                "FROM training_slope "
                "WHERE blood_no = ANY(:bns) AND chokyo_date >= :since AND chokyo_date <= :until"
            ),
            {"bns": blood_nos, "since": since, "until": race_date},
        ).fetchall()
        wood_rows = conn.execute(
            text(
                "SELECT blood_no, chokyo_date, chokyo_time, "
                "       time_5f, lap_l2_l1, lap_l1 "
                "FROM training_wood "
                "WHERE blood_no = ANY(:bns) AND chokyo_date >= :since AND chokyo_date <= :until"
            ),
            {"bns": blood_nos, "since": since, "until": race_date},
        ).fetchall()

    for r in slope_rows:
        slope_by.setdefault(r[0], []).append(
            SlopeRow(
                blood_no=r[0], chokyo_date=r[1], chokyo_time=r[2], center_cd=r[3],
                time_4f=r[4], lap_l4_l3=r[5], lap_l3_l2=r[6], lap_l2_l1=r[7], lap_l1=r[8],
            )
        )
    for r in wood_rows:
        wood_by.setdefault(r[0], []).append(
            WoodRow(blood_no=r[0], chokyo_date=r[1], chokyo_time=r[2],
                    time_5f=r[3], lap_l2_l1=r[4], lap_l1=r[5])
        )
    return slope_by, wood_by


def _collect_honmei(race_ctx, strategy_name: str) -> tuple[list[HonmeiRow], str | None]:
    """本命候補の一覧と、選定された本命の horse_id（無ければ None）を返す。"""
    strat = load_strategy(strategy_name)
    ev = evaluate_race_context(race_ctx, strat)
    umaban_map = {h.horse_id: h.umaban for h in race_ctx.horses}
    honmei_id = ev.honmei.horse_id if ev.honmei else None
    rows = [
        HonmeiRow(
            umaban=_fmt_umaban(umaban_map.get(c.horse_id)),
            horse_name=c.horse_name,
            is_honmei=(c.horse_id == honmei_id),
            clear_count=c.clear_count,
            total_score=c.total_score,
            ai_score=c.ai_score,
        )
        for c in ev.candidates
    ]
    return rows, honmei_id


def _collect_aite(race_ctx, strategy_name: str, honmei_horse_id: str | None) -> list[AiteRow]:
    strat = load_strategy(strategy_name)
    ev = evaluate_race_context(race_ctx, strat)
    umaban_map = {h.horse_id: h.umaban for h in race_ctx.horses}
    aite = select_aite(ev.candidates, honmei_horse_id=honmei_horse_id)
    return [
        AiteRow(
            umaban=_fmt_umaban(umaban_map.get(c.horse_id)),
            horse_name=c.horse_name,
            total_score=c.total_score,
            ai_score=c.ai_score,
        )
        for c in aite
    ]


def _fmt_umaban(umaban: int | None) -> str | None:
    return str(umaban) if umaban is not None else None


def _collect_training(race_id: str, race_date: str) -> tuple[list[TrainingRow], str | None]:
    blood_map = _fetch_blood_no_map(race_id)
    if not blood_map:
        return [], "race_entries_v2 に blood_no が見つかりません（対象外）"

    umaban_by_blood_no = {bn: umaban for umaban, (bn, _name) in blood_map.items()}
    name_by_blood_no = {bn: name for _umaban, (bn, name) in blood_map.items()}
    blood_nos = list(umaban_by_blood_no.keys())

    slope_by, wood_by = _fetch_training_rows_by_blood(blood_nos, race_date)
    config = load_config()
    ranked = rank_horses_by_training(
        blood_nos=blood_nos,
        slope_rows_by_horse=slope_by,
        wood_rows_by_horse=wood_by,
        race_date=race_date,
        config=config,
        umaban_by_blood_no=umaban_by_blood_no,
    )
    rows = [
        TrainingRow(
            umaban=r.umaban,
            horse_name=name_by_blood_no.get(r.blood_no, ""),
            priority=r.priority,
            condition_label=r.condition_label,
            rank=r.rank,
            tiebreak_time_sec=r.tiebreak_time_sec,
        )
        for r in ranked
    ]
    return rows, None


def collect_race_filters(
    race_id: str,
    honmei_strategy: str = "honmei_v1",
    aite_strategy: str = "anaba_v1",
) -> RaceFilterResult:
    """1レース分の3条件（本命/相手/調教のみ）の適用結果を組み立てる。"""
    race_ctx = fetch_race_context(race_id)
    honmei_rows, honmei_horse_id = _collect_honmei(race_ctx, honmei_strategy)
    aite_rows = _collect_aite(race_ctx, aite_strategy, honmei_horse_id)
    training_rows, training_error = _collect_training(race_id, race_id[:8])
    return RaceFilterResult(
        race_id=race_id,
        race_name=race_ctx.race_name or race_id,
        honmei_rows=honmei_rows,
        aite_rows=aite_rows,
        training_rows=training_rows,
        training_error=training_error,
    )
