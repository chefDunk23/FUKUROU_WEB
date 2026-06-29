"""
tipster/conditions_tr1.py
===========================
BET-5 実験用: TR-1（調教AIフィルタリング、tipster/training_ranker.py）の
優先度ランキング上位馬を、本命選定の追加条件として組み込むための新規条件
"training_rank_top"。

設計方針（PLAN.md §3 BET-5 / ユーザー指示「既存ロジック本体は変更しない」に従う）:
  - tipster/conditions.py・tipster/training_ranker.py・tipster/engine.py は
    一切変更しない。本ファイルは register_condition() で CONDITION_REGISTRY に
    新規エントリを追加登録するだけの追加モジュール
    （tipster/backtest.py のコメント「新条件を@register_conditionで追加し、
    戦略JSONに加えるだけで自動的に対象になる」という既存の拡張方式に従う）。
  - training_ranker.rank_horses_by_training() をそのまま呼び出すだけで、
    優先度①〜⑦の判定ロジック自体には一切手を加えない。
  - 本条件を有効化するには、戦略評価より前に本モジュールを import しておく
    必要がある（register_condition の副作用で CONDITION_REGISTRY に登録される
    ため）。scripts/run_strategy_experiment.py が起動時に import している。

HorseContext.horse_id は race_entries.horse_id であり、
shared/worker/job_runner.py の同期処理で blood_no がそのまま格納されているため
（race_entries_v2.blood_no → race_entries.horse_id）、そのまま
training_slope/training_wood の blood_no として使用できる。
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import text

from .conditions import register_condition
from .models import ConditionResult, HorseContext, RaceContext
from .training_ranker import SlopeRow, WoodRow, load_config, rank_horses_by_training

# weekend_filter_data.py と同じ遡り日数（条件⑤=前週6-8日前の坂路データをカバー）
_LOOKBACK_DAYS = 30

# race_id -> {blood_no: rank} のプロセス内キャッシュ（同一レースの馬ごとに
# rank_horses_by_training() を毎回呼び直さないようにするためだけのもの。
# training_ranker.py 自体のロジックには影響しない）。
_rank_cache: dict[str, dict[str, int]] = {}


def _race_date_yyyymmdd(race_ctx: RaceContext) -> str | None:
    """RaceContext.race_date（"YYYY-MM-DD"）を training_ranker が期待する
    "YYYYMMDD" 形式に変換する。"""
    if not race_ctx.race_date:
        return None
    return race_ctx.race_date.replace("-", "")


def _fetch_training_rows(
    blood_nos: list[str], race_date: str
) -> tuple[dict[str, list[SlopeRow]], dict[str, list[WoodRow]]]:
    """training_slope / training_wood から対象馬の直近データを取得する。

    tipster/weekend_filter_data.py の _fetch_training_rows_by_blood() と
    同一のクエリパターン（ml.db.engine 経由）を用いる。
    """
    from ml.db import engine as _engine

    since = (
        date(int(race_date[:4]), int(race_date[4:6]), int(race_date[6:8]))
        - timedelta(days=_LOOKBACK_DAYS)
    ).strftime("%Y%m%d")

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


def _ranks_for_race(race_ctx: RaceContext) -> dict[str, int]:
    """race_ctx.race_id に対する {blood_no: rank} を計算してキャッシュする。"""
    cached = _rank_cache.get(race_ctx.race_id)
    if cached is not None:
        return cached

    race_date = _race_date_yyyymmdd(race_ctx)
    blood_nos = [h.horse_id for h in race_ctx.horses]
    if not race_date or not blood_nos:
        _rank_cache[race_ctx.race_id] = {}
        return {}

    slope_by, wood_by = _fetch_training_rows(blood_nos, race_date)
    config = load_config()
    ranked = rank_horses_by_training(
        blood_nos=blood_nos,
        slope_rows_by_horse=slope_by,
        wood_rows_by_horse=wood_by,
        race_date=race_date,
        config=config,
    )
    rank_map = {r.blood_no: r.rank for r in ranked}
    _rank_cache[race_ctx.race_id] = rank_map
    return rank_map


@register_condition("training_rank_top")
def check_training_rank_top(horse: HorseContext, race_ctx: RaceContext, params: dict) -> ConditionResult:
    """TR-1（training_ranker.rank_horses_by_training）の優先度順位が
    上位 top_n 以内かどうかを判定する。

    既存条件のような「足切り（required:true）」ではなく、デフォルトでは
    スコア加点のみを想定する（params で required:true にする場合の挙動は
    呼び出し元の戦略JSON側の設定に委ねる）。
    """
    top_n = params.get("top_n", 3)
    bonus_score = params.get("bonus_score", 1.5)
    penalty_score = params.get("penalty_score", 0.0)

    rank_map = _ranks_for_race(race_ctx)
    if not rank_map:
        return ConditionResult(passed=True, score=0.0, reason="調教データ不足(判定保留)")

    rank = rank_map.get(horse.horse_id)
    if rank is not None and rank <= top_n:
        return ConditionResult(
            passed=True, score=bonus_score,
            reason=f"TR-1調教ランキング{rank}位(上位{top_n}以内)",
            detail={"training_rank": rank},
        )
    if rank is not None:
        return ConditionResult(
            passed=False, score=penalty_score,
            reason=f"TR-1調教ランキング{rank}位(上位{top_n}外)",
            detail={"training_rank": rank},
        )
    return ConditionResult(passed=False, score=penalty_score, reason="TR-1優先度①〜⑦のいずれにも該当なし")
