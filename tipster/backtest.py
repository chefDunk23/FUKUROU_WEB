"""
tipster/backtest.py
=====================
honmei_v1 等の戦略を過去レースに遡って適用し、本命馬の勝率・複勝率・回収率を集計する。

race_detail_cache は行数が極端に少なく(検証時点で1行)、_compute_detail を
全件に対して呼ぶと数時間かかるため使わない。代わりに races/race_entries 等を
直接バルクロードし、pandas のベクトル化操作で「前走/前々走の対戦相手の次走順位」
「前走斤量」「前走騎手」などを事前計算した軽量 RaceContext を構築する。

AIスコアはこの軽量版では取得しない（発走前計算であることを保証できないため）。
本命の選定は明文化されたルール（engine.select_honmei、本ファイルから共有利用）に従う:
条件クリア数 → 合計スコア → AIスコア(常時0=実質スキップ) → 馬番が若い方。
"""
from __future__ import annotations

import argparse
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text

from shared.config import EVAL_START_DATE, TRAIN_END_DATE
from .conditions import CONDITION_REGISTRY, _class_level_from_codes, classify_pace_prediction
from .engine import _SURFACE_JA_TO_EN, compute_confidence, load_strategy, select_honmei
from .models import (
    BacktestResult,
    ConditionEffectiveness,
    GradeStats,
    HorseContext,
    HorseEvaluation,
    PastRaceInfo,
    PastRaceOpponent,
    RaceContext,
    Strategy,
)

# ─────────────────────────────────────────────────────────────────────────
# 定数
# ─────────────────────────────────────────────────────────────────────────
# データ分割境界（shared.config で一元管理）:
#   学習データ: ~ TRAIN_END_DATE (2025-05-31)
#   検証データ: EVAL_START_DATE (2025-06-01) ~ 直近
# バックテストを検証期間のみで実施する場合は from_date に EVAL_START_DATE を使うこと。
# ランダムシャッフル分割は禁止。時系列順の分割のみ許可。

_PERIOD_DAYS = {"3m": 90, "6m": 180, "1y": 365}
_LOOKBACK_DAYS = 730        # 前走/前々走探索用にロード期間を遡る幅
_FUKU_ODDS_RATIO = 0.25     # 複勝オッズ概算: 1 + (単勝オッズ-1)*RATIO（複勝オッズ未保存のため近似）

_DISTANCE_BUCKETS = (
    (1400, "sprint"),
    (1800, "mile"),
    (2200, "middle"),
)


def get_train_end_date() -> date:
    """学習データ終了日（shared.config.TRAIN_END_DATE）を date オブジェクトで返す。"""
    return date.fromisoformat(TRAIN_END_DATE)


def get_eval_start_date() -> date:
    """検証データ開始日（shared.config.EVAL_START_DATE）を date オブジェクトで返す。"""
    return date.fromisoformat(EVAL_START_DATE)


def _parse_period_days(period: str) -> int:
    if period in _PERIOD_DAYS:
        return _PERIOD_DAYS[period]
    m = re.fullmatch(r"(\d+)([dmy])", period)
    if not m:
        raise ValueError(f"不明な期間指定: {period!r}")
    n, unit = int(m.group(1)), m.group(2)
    return {"d": 1, "m": 30, "y": 365}[unit] * n


def _to_str(v) -> str:
    """pandas が NaN(float) として読み込んだ可能性のある列を安全に文字列化する。"""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return str(v)


def _grade_bucket(grade_code, jyoken_cd_3) -> str:
    g = _to_str(grade_code).strip()
    if g == "A":
        return "G1"
    if g == "B":
        return "G2"
    if g == "C":
        return "G3"
    if g == "L":
        return "L"
    jy = _to_str(jyoken_cd_3).strip()
    if jy == "999":
        return "OP"
    if jy in ("701", "702", "703"):
        return "新馬・未勝利"
    return "条件戦"


def _distance_bucket(distance: int | None) -> str:
    if distance is None:
        return "unknown"
    for limit, label in _DISTANCE_BUCKETS:
        if distance <= limit:
            return label
    return "long"


def _approx_fuku_odds(tan_odds: float) -> float:
    """複勝オッズの概算値。実データ(複勝オッズ)は本DBに保存されていないための近似式。"""
    return max(1.0, 1.0 + (tan_odds - 1.0) * _FUKU_ODDS_RATIO)


def _aggregate_picks(picks: list[tuple[int | None, float | None]]) -> GradeStats:
    valid = [p for p in picks if p[0] is not None]
    n = len(valid)
    if n == 0:
        return GradeStats()
    wins = [p for p in valid if p[0] == 1]
    places = [p for p in valid if p[0] <= 3]
    win_count, place_count = len(wins), len(places)
    tan_return = sum((odds or 0.0) * 100 for _, odds in wins)
    fuku_return = sum(_approx_fuku_odds(odds or 1.0) * 100 for _, odds in places)
    avg_win_odds = (sum(odds or 0.0 for _, odds in wins) / win_count) if win_count else 0.0
    stake = n * 100
    return GradeStats(
        race_count=n,
        win_count=win_count,
        place_count=place_count,
        win_rate=round(win_count / n, 4),
        place_rate=round(place_count / n, 4),
        avg_win_odds=round(avg_win_odds, 2),
        tan_return_rate=round(tan_return / stake, 4) if stake else 0.0,
        fuku_return_rate=round(fuku_return / stake, 4) if stake else 0.0,
    )


# ─────────────────────────────────────────────────────────────────────────
# バルクロード + ベクトル化前処理
# ─────────────────────────────────────────────────────────────────────────

_BULK_SQL = text("""
    SELECT
      e.race_id, e.horse_id, e.jockey_id, e.trainer_id,
      e.bracket_number AS wakuban, e.horse_number AS umaban,
      e.weight AS burden_weight, e.win_odds AS tan_odds,
      e.confirmed_rank, e.time_seconds, e.corner_4,
      r.date, r.place_code, r.distance, r.course_type AS surface,
      r.grade_code, r.jyoken_cd_3, r.name AS race_name
    FROM race_entries e
    JOIN races r ON e.race_id = r.id
    WHERE r.date BETWEEN :start AND :end
      AND e.confirmed_rank IS NOT NULL AND e.confirmed_rank > 0
      AND r.course_type IN ('芝', 'ダート')
      AND r.place_code <= '10'
    ORDER BY e.horse_id, r.date
""")

# rest_interval の「海外/地方帰り」判定専用。メインのバルクロードはJRA限定で高速に保ち、
# 対象馬に限定した小規模な追加クエリで地方・海外レース歴だけ別途取得する。
_NON_JRA_SQL = text("""
    SELECT e.horse_id, r.date, r.place_code
    FROM race_entries e
    JOIN races r ON e.race_id = r.id
    WHERE e.horse_id = ANY(:hids)
      AND r.date BETWEEN :start AND :end
      AND r.place_code !~ '^(0[1-9]|10)$'
      AND e.confirmed_rank IS NOT NULL AND e.confirmed_rank > 0
    ORDER BY e.horse_id, r.date
""")

# course_fitness（同コース/類似コースでの過去成績）が「過去5走」を参照するため、
# shift(1)〜shift(5) で前走〜5走前を保持する。
_N_PAST_RACES = 5


def _load_bulk_data(load_start: date, load_end: date) -> pd.DataFrame:
    """対象期間 + 遡り期間の全JRAレース結果をロードし、前走/次走列を計算する。"""
    from ml.db import engine as _engine

    df = pd.read_sql(_BULK_SQL, _engine, params={"start": load_start, "end": load_end})
    df["date"] = pd.to_datetime(df["date"])
    for col in ("burden_weight", "tan_odds", "confirmed_rank", "time_seconds", "corner_4", "distance"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["is_jra"] = True

    df = df.sort_values(["horse_id", "date"]).reset_index(drop=True)
    g = df.groupby("horse_id", sort=False)
    for i in range(1, _N_PAST_RACES + 1):
        df[f"prev{i}_race_id"] = g["race_id"].shift(i)
    df["prev_race_date"] = g["date"].shift(1)
    df["prev_burden_weight"] = g["burden_weight"].shift(1)
    df["prev_jockey_id"] = g["jockey_id"].shift(1)
    # next_confirmed_rank はその馬の「次走」の結果だが、評価対象レースより後に
    # 実施された次走を使うとデータリークになる。next_race_date を併せて保持し、
    # 消費側 (_build_past_races) で evaluation_date より前のものだけに絞る。
    df["next_confirmed_rank"] = g["confirmed_rank"].shift(-1)
    df["next_race_date"] = g["date"].shift(-1)

    df["winner_time"] = df.groupby("race_id")["time_seconds"].transform("min")
    df["this_margin"] = df["time_seconds"] - df["winner_time"]
    df["field_size"] = df.groupby("race_id")["horse_id"].transform("count")

    df["corner_ratio"] = np.where(
        df["corner_4"].notna() & (df["corner_4"] > 0) & (df["field_size"] > 1),
        (df["corner_4"] - 1) / (df["field_size"] - 1),
        np.nan,
    )
    # position_tendency 代理指標: 直近(自身を含まない)最大3走の corner_ratio 平均
    df["position_tendency_proxy"] = df.groupby("horse_id")["corner_ratio"].transform(
        lambda s: s.shift(1).rolling(3, min_periods=1).mean()
    )
    return df


def _build_race_groups(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {rid: g.reset_index(drop=True) for rid, g in df.groupby("race_id")}


def _safe_str_or_none(v) -> str | None:
    s = _to_str(v).strip()
    return s or None


def _build_race_meta(race_groups: dict[str, pd.DataFrame]) -> dict[str, dict]:
    meta: dict[str, dict] = {}
    for rid, g in race_groups.items():
        first = g.iloc[0]
        dist = int(first["distance"]) if pd.notna(first["distance"]) else None
        grade_code = _safe_str_or_none(first["grade_code"])
        jyoken_cd_3 = _safe_str_or_none(first["jyoken_cd_3"])
        meta[rid] = {
            "date": first["date"],
            "place_code": _safe_str_or_none(first["place_code"]),
            "distance": dist,
            "surface": _safe_str_or_none(first["surface"]),
            "grade_code": grade_code,
            "jyoken_cd_3": jyoken_cd_3,
            "race_name": _safe_str_or_none(first.get("race_name")),
            "grade_bucket": _grade_bucket(grade_code, jyoken_cd_3),
            "distance_bucket": _distance_bucket(dist),
            "class_level": _class_level_from_codes(grade_code, jyoken_cd_3),
            "is_jra": bool(first["is_jra"]),
        }
    return meta


def _build_date_jockey_places(df: pd.DataFrame) -> dict[tuple, set]:
    sub = df.dropna(subset=["jockey_id"])[["date", "jockey_id", "place_code"]]
    out: dict[tuple, set] = defaultdict(set)
    for date_val, jid, pc in sub.itertuples(index=False):
        out[(date_val, jid)].add(pc)
    return out


def _collect_synergy_pairs(race_groups: dict[str, pd.DataFrame], target_race_ids: list[str]) -> set[tuple[str, str]]:
    """jockey_change（騎手乗り替わり）が発生する対象レースの (trainer_id, jockey_id) を集める。

    _SynergyCache.preload() に渡すことで、ペアごとの個別クエリ（高コスト）を避け、
    1回のバルククエリで済ませる。
    """
    pairs: set[tuple[str, str]] = set()
    for rid in target_race_ids:
        rows = race_groups[rid]
        changed = rows[rows["prev_jockey_id"].notna() & (rows["jockey_id"] != rows["prev_jockey_id"])]
        for tid, jid in zip(changed["trainer_id"], changed["jockey_id"]):
            if tid and jid:
                pairs.add((tid, jid))
    return pairs


def _fetch_non_jra_interim_races(horse_ids: set[str], load_start: date, load_end: date) -> dict[str, list[tuple]]:
    """対象馬の地方・海外レース歴を取得する（rest_interval の海外/地方帰り判定専用）。

    メインのバルクロードはJRA限定で高速だが、「前走が地方/海外だった」ことを検知するには
    JRA以外のレースも見る必要がある。対象馬に限定した小規模な追加クエリで済ませる。
    戻り値: {horse_id: [(date, place_code), ...]}（日付昇順）
    """
    horse_ids = {h for h in horse_ids if h}
    if not horse_ids:
        return {}
    from ml.db import engine as _engine

    with _engine.connect() as conn:
        rows = conn.execute(
            _NON_JRA_SQL, {"hids": list(horse_ids), "start": load_start, "end": load_end},
        ).fetchall()
    out: dict[str, list[tuple]] = defaultdict(list)
    for hid, d, pc in rows:
        out[hid].append((pd.Timestamp(d), pc))
    return out


def _fetch_jockey_stats(jockey_ids: set[str]) -> dict[str, tuple[int | None, int | None]]:
    if not jockey_ids:
        return {}
    from ml.db import engine as _engine

    with _engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, yr_wins, career_wins FROM jockeys WHERE id = ANY(:ids)"),
            {"ids": list(jockey_ids)},
        ).fetchall()
    return {r[0]: (r[1], r[2]) for r in rows}


def _fetch_bias_map(target_race_ids: list[str], race_meta: dict[str, dict]) -> dict[str, dict]:
    """track_bias_pit 優先、無ければ course_profile_store の脚質別勝率から推定する（バルク版）。"""
    if not target_race_ids:
        return {}
    from ml.db import engine as _engine

    out: dict[str, dict] = {}
    with _engine.connect() as conn:
        rows = conn.execute(
            text("SELECT race_id, front_bias_pit, inner_bias_pit FROM track_bias_pit WHERE race_id = ANY(:ids)"),
            {"ids": target_race_ids},
        ).fetchall()
        for rid, front, inner in rows:
            out[rid] = {"front_bias_pit": front, "inner_bias_pit": inner, "source": "track_bias_pit"}

        missing = [rid for rid in target_race_ids if rid not in out]
        if not missing:
            return out

        combos = {
            (race_meta[rid]["place_code"], race_meta[rid]["distance"], _SURFACE_JA_TO_EN.get(race_meta[rid]["surface"]))
            for rid in missing
        }
        for pc, dist, surf in combos:
            if not (pc and dist and surf):
                continue
            cp_rows = conn.execute(
                text(
                    "SELECT target_date, style_nige_win_rate, style_senko_win_rate, "
                    "style_sashi_win_rate, style_oikomi_win_rate FROM course_profile_store "
                    "WHERE place_code=:pc AND distance=:d AND surface=:s ORDER BY target_date"
                ),
                {"pc": pc, "d": dist, "s": surf},
            ).fetchall()
            if not cp_rows:
                continue
            cp_df = pd.DataFrame([dict(r._mapping) for r in cp_rows])
            cp_df["target_date"] = pd.to_datetime(cp_df["target_date"])
            for rid in missing:
                meta = race_meta[rid]
                key = (meta["place_code"], meta["distance"], _SURFACE_JA_TO_EN.get(meta["surface"]))
                if key != (pc, dist, surf):
                    continue
                rd = pd.Timestamp(meta["date"])
                cand = cp_df[cp_df["target_date"] <= rd]
                if cand.empty:
                    continue
                last = cand.iloc[-1]
                nige, senko, sashi, oikomi = (
                    float(last[c] or 0.0)
                    for c in ("style_nige_win_rate", "style_senko_win_rate", "style_sashi_win_rate", "style_oikomi_win_rate")
                )
                front_bias = ((nige + senko) / 2) - ((sashi + oikomi) / 2)
                out[rid] = {"front_bias_pit": front_bias, "inner_bias_pit": None, "source": "course_profile_store"}
    return out


class _SynergyCache:
    """trainer_id × jockey_id の synergy_store 時系列をペア単位でキャッシュする。

    synergy_store はテーブル全体で 6000万行あり全件ロードは不可能。さらに既存の
    インデックスは (target_date, trainer_id, jockey_id) の順で構成されており、
    target_date を絞らない (trainer_id, jockey_id) のみの検索には使えないため、
    ペアごとに個別クエリすると1件あたり数十msかかり、ペア数が数千〜1万を超えると
    全体で数百秒規模のボトルネックになる（実測: 13,330ペアで約856秒相当）。
    そのため preload() で必要なペアをまとめて1回のクエリで取得する。
    """

    def __init__(self) -> None:
        self._series: dict[tuple[str, str], pd.DataFrame] = {}

    def preload(self, pairs: set[tuple[str, str]], load_start=None, load_end=None) -> None:
        """必要な (trainer_id, jockey_id) ペアをまとめて1回のクエリで取得する。

        synergy_store は (trainer_id, jockey_id) ペア1件あたり平均1800行近い時系列を持つ
        （ほぼ日次更新）。target_date で [load_start, load_end] に絞らないと、評価に
        不要な古い履歴まで全件転送してしまい、ペア数が数百でも数十秒規模に膨らむ。
        """
        pairs = {p for p in pairs if p[0] and p[1]}
        if not pairs:
            return
        from ml.db import engine as _engine

        tids = [p[0] for p in pairs]
        jids = [p[1] for p in pairs]
        date_filter = ""
        params: dict = {"tids": tids, "jids": jids}
        if load_start is not None and load_end is not None:
            date_filter = "AND s.target_date BETWEEN :start AND :end "
            params["start"], params["end"] = load_start, load_end
        with _engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT s.trainer_id, s.jockey_id, s.target_date, s.combo_count, "
                    "s.combo_win_rate, s.combo_top3_rate "
                    "FROM synergy_store s "
                    "JOIN unnest(CAST(:tids AS text[]), CAST(:jids AS text[])) AS pr(trainer_id, jockey_id) "
                    "ON s.trainer_id = pr.trainer_id AND s.jockey_id = pr.jockey_id "
                    + date_filter +
                    "ORDER BY s.trainer_id, s.jockey_id, s.target_date"
                ),
                params,
            ).fetchall()

        grouped: dict[tuple[str, str], list] = defaultdict(list)
        for tid, jid, target_date, combo_count, combo_win_rate, combo_top3_rate in rows:
            grouped[(tid, jid)].append((target_date, combo_count, combo_win_rate, combo_top3_rate))

        for pair in pairs:
            recs = grouped.get(pair)
            if recs:
                df = pd.DataFrame(recs, columns=["target_date", "combo_count", "combo_win_rate", "combo_top3_rate"])
                df["target_date"] = pd.to_datetime(df["target_date"])
            else:
                df = pd.DataFrame()
            self._series[pair] = df

    def lookup(self, trainer_id: str | None, jockey_id: str | None, race_date) -> dict | None:
        if not trainer_id or not jockey_id:
            return None
        key = (trainer_id, jockey_id)
        if key not in self._series:
            self._series[key] = self._fetch(trainer_id, jockey_id)
        series = self._series[key]
        if series.empty:
            return None
        cand = series[series["target_date"] <= pd.Timestamp(race_date)]
        if cand.empty:
            return None
        last = cand.iloc[-1]
        return {
            "combo_count": int(last["combo_count"]),
            "combo_win_rate": float(last["combo_win_rate"]) if pd.notna(last["combo_win_rate"]) else None,
            "combo_top3_rate": float(last["combo_top3_rate"]) if pd.notna(last["combo_top3_rate"]) else None,
        }

    @staticmethod
    def _fetch(trainer_id: str, jockey_id: str) -> pd.DataFrame:
        from ml.db import engine as _engine

        with _engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT target_date, combo_count, combo_win_rate, combo_top3_rate FROM synergy_store "
                    "WHERE trainer_id=:t AND jockey_id=:j ORDER BY target_date"
                ),
                {"t": trainer_id, "j": jockey_id},
            ).fetchall()
        df = pd.DataFrame([dict(r._mapping) for r in rows])
        if not df.empty:
            df["target_date"] = pd.to_datetime(df["target_date"])
        return df


_JOCKEY_VENUE_COLS = [f"venue_{i:02d}_win_rate" for i in range(1, 11)]


class _JockeyVenueCache:
    """jockey_feature_store の (target_date, 競馬場別勝率, 全体勝率) 時系列をキャッシュする(jockey_intent用)。"""

    def __init__(self) -> None:
        self._series: dict[str, pd.DataFrame] = {}

    def preload(self, jockey_ids: set[str], load_start, load_end) -> None:
        jockey_ids = {j for j in jockey_ids if j}
        if not jockey_ids:
            return
        from ml.db import engine as _engine

        venue_cols_sql = ", ".join(_JOCKEY_VENUE_COLS)
        with _engine.connect() as conn:
            rows = conn.execute(
                text(
                    f"SELECT kishu_code, target_date, win_rate, {venue_cols_sql} "
                    "FROM jockey_feature_store WHERE kishu_code = ANY(:ids) "
                    "AND target_date BETWEEN :start AND :end ORDER BY kishu_code, target_date"
                ),
                {"ids": list(jockey_ids), "start": load_start, "end": load_end},
            ).fetchall()

        grouped: dict[str, list] = defaultdict(list)
        for row in rows:
            grouped[row[0]].append(row[1:])

        cols = ["target_date", "win_rate"] + _JOCKEY_VENUE_COLS
        for jid, recs in grouped.items():
            df = pd.DataFrame(recs, columns=cols)
            df["target_date"] = pd.to_datetime(df["target_date"])
            self._series[jid] = df

    def lookup(self, jockey_id: str | None, race_date, place_code: str | None) -> tuple[float | None, float | None]:
        """戻り値: (当該競馬場での勝率, 全体勝率)。データなしは (None, None)。"""
        if not jockey_id or jockey_id not in self._series:
            return None, None
        series = self._series[jockey_id]
        cand = series[series["target_date"] <= pd.Timestamp(race_date)]
        if cand.empty:
            return None, None
        last = cand.iloc[-1]
        overall = float(last["win_rate"]) if pd.notna(last["win_rate"]) else None
        venue_col = f"venue_{place_code}_win_rate" if place_code else None
        venue = (
            float(last[venue_col]) if venue_col in cand.columns and pd.notna(last[venue_col]) else None
        ) if venue_col else None
        return venue, overall


# ─────────────────────────────────────────────────────────────────────────
# 軽量 RaceContext 構築
# ─────────────────────────────────────────────────────────────────────────


def _get_past_race_info(
    prid: str, race_groups: dict[str, pd.DataFrame], past_race_cache: dict
) -> tuple[dict | None, list[tuple]]:
    """過去走1戦分の出走馬共通情報をメモ化して構築する（次走順位は未マスクの生データ）。

    戻り値: (race_meta_dict, [(horse_id, this_rank, this_margin, next_race_date, next_rank_raw), ...])

    next_race_date を一緒に保持し、評価対象レースの発走日より後に実施された次走は
    _build_past_races 側でマスクする（同じ過去走 prid が複数の評価対象レースから
    異なる日付で参照されるため、マスク前の生データをキャッシュする必要がある）。
    """
    if prid in past_race_cache:
        return past_race_cache[prid]

    rows = race_groups.get(prid)
    if rows is None or rows.empty:
        past_race_cache[prid] = (None, [])
        return past_race_cache[prid]

    raw_opponents: list[tuple] = []
    for _, orow in rows.iterrows():
        this_rank = int(orow["confirmed_rank"]) if pd.notna(orow["confirmed_rank"]) else None
        next_rank_raw = int(orow["next_confirmed_rank"]) if pd.notna(orow["next_confirmed_rank"]) else None
        next_date = orow["next_race_date"] if pd.notna(orow["next_race_date"]) else None
        this_margin = float(orow["this_margin"]) if pd.notna(orow["this_margin"]) else None
        raw_opponents.append((orow["horse_id"], this_rank, this_margin, next_date, next_rank_raw))

    first = rows.iloc[0]
    grade_code = _safe_str_or_none(first["grade_code"])
    jyoken_cd_3 = _safe_str_or_none(first["jyoken_cd_3"])
    meta = {
        "date": str(first["date"].date()),
        "distance": int(first["distance"]) if pd.notna(first["distance"]) else None,
        "surface": _safe_str_or_none(first["surface"]),
        "head_count": len(rows),
        "race_name": _safe_str_or_none(first.get("race_name")),
        "grade_code": grade_code,
        "place_code": _safe_str_or_none(first["place_code"]),
        "jyoken_cd_3": jyoken_cd_3,
        "class_level": _class_level_from_codes(grade_code, jyoken_cd_3),
    }
    past_race_cache[prid] = (meta, raw_opponents)
    return past_race_cache[prid]


def _build_past_races(
    row, race_groups: dict[str, pd.DataFrame], past_race_cache: dict, evaluation_date,
) -> list[PastRaceInfo]:
    """row（評価対象レースの1馬分）の過去 _N_PAST_RACES 走分の情報を構築する（新しい順）。

    データリーク防止: 対戦相手の「次走」順位は、next_race_date < evaluation_date
    （評価対象レースの発走日より前に確定したもの）のみ採用する。それ以外は
    「まだ次走を消化していない」のと同じ扱い（None = 判定保留）にする。
    """
    out: list[PastRaceInfo] = []
    hid = row["horse_id"]
    for i in range(1, _N_PAST_RACES + 1):
        prid = row.get(f"prev{i}_race_id")
        if prid is None or (isinstance(prid, float) and pd.isna(prid)):
            continue
        meta, raw_opponents = _get_past_race_info(prid, race_groups, past_race_cache)
        if meta is None:
            continue

        own_rank = None
        opponents: list[PastRaceOpponent] = []
        for ohid, this_rank, this_margin, next_date, next_rank_raw in raw_opponents:
            safe_next_rank = next_rank_raw if (next_date is not None and next_date < evaluation_date) else None
            opponents.append(PastRaceOpponent(
                horse_id=ohid, this_rank=this_rank, this_margin=this_margin, next_race_rank=safe_next_rank,
            ))
            if ohid == hid:
                own_rank = this_rank

        out.append(PastRaceInfo(
            race_id=prid,
            date=meta["date"],
            rank=own_rank,
            distance=meta["distance"],
            surface=meta["surface"],
            head_count=meta["head_count"],
            race_name=meta["race_name"],
            class_score=None,
            time_score=None,
            member_level_score=None,
            opponents_next_races=opponents,
            grade_code=meta["grade_code"],
            place_code=meta["place_code"],
            jyoken_cd_3=meta["jyoken_cd_3"],
            class_level=meta["class_level"],
        ))
    return out


def _build_lightweight_context(
    race_id: str,
    race_groups: dict[str, pd.DataFrame],
    race_meta: dict[str, dict],
    bias_map: dict[str, dict],
    synergy_cache: _SynergyCache,
    date_jockey_places: dict[tuple, set],
    jockey_stats: dict[str, tuple[int | None, int | None]],
    past_race_cache: dict,
    jockey_venue_cache: _JockeyVenueCache | None = None,
    non_jra_races: dict[str, list[tuple]] | None = None,
) -> RaceContext | None:
    """軽量版 RaceContext を構築する。

    AIスコアは意図的に取得しない（race_detail_cache はほぼ空であり、かつキャッシュが
    レース確定後に再計算されたものだと発走前情報のみという制約に違反するリスクがあるため）。
    候補馬のランキングは条件クリア数 + 合計スコアのみで決まる（engine.select_honmei 参照）。
    """
    rows = race_groups.get(race_id)
    if rows is None or rows.empty:
        return None
    meta = race_meta[race_id]
    bias = bias_map.get(race_id, {})

    horses: list[HorseContext] = []
    for _, row in rows.iterrows():
        hid = row["horse_id"]
        jockey_id = row["jockey_id"]
        trainer_id = row["trainer_id"]
        prev_jockey_id = row["prev_jockey_id"] if pd.notna(row["prev_jockey_id"]) else None

        step1 = False
        step2 = False
        affinity = None
        if prev_jockey_id and jockey_id and prev_jockey_id != jockey_id:
            step1 = bool(((rows["jockey_id"] == prev_jockey_id) & (rows["horse_id"] != hid)).any())
            if not step1:
                places = date_jockey_places.get((meta["date"], prev_jockey_id), set())
                step2 = bool(places - {meta["place_code"]})
            affinity = synergy_cache.lookup(trainer_id, jockey_id, meta["date"])

        jockey_yr_wins, jockey_career_wins = jockey_stats.get(jockey_id, (None, None))

        jockey_venue_rate, jockey_overall_rate = (
            jockey_venue_cache.lookup(jockey_id, meta["date"], meta["place_code"])
            if jockey_venue_cache is not None else (None, None)
        )

        prev_race_date = row["prev_race_date"] if pd.notna(row["prev_race_date"]) else None
        prev_race_days_ago = (row["date"] - prev_race_date).days if prev_race_date is not None else None

        overseas_place_code = None
        if non_jra_races is not None and prev_race_date is not None:
            interim = [
                pc for d, pc in non_jra_races.get(hid, [])
                if prev_race_date < d < row["date"]
            ]
            if interim:
                overseas_place_code = interim[-1]

        horses.append(HorseContext(
            horse_id=hid,
            horse_name=None,
            umaban=int(row["umaban"]) if pd.notna(row["umaban"]) else None,
            wakuban=int(row["wakuban"]) if pd.notna(row["wakuban"]) else None,
            jockey_id=jockey_id,
            jockey_name=None,
            trainer_id=trainer_id,
            trainer_name=None,
            burden_weight=float(row["burden_weight"]) if pd.notna(row["burden_weight"]) else None,
            horse_weight=None,
            ai_score=None,
            ai_rank=None,
            chokyo_score=None,
            position_tendency=(
                float(row["position_tendency_proxy"]) if pd.notna(row["position_tendency_proxy"]) else None
            ),
            prev_race_rank=None,
            prev_race_grade=None,
            prev_race_days_ago=prev_race_days_ago,
            past_races=_build_past_races(row, race_groups, past_race_cache, meta["date"]),
            tan_odds=float(row["tan_odds"]) if pd.notna(row["tan_odds"]) else None,
            prev_burden_weight=(
                float(row["prev_burden_weight"]) if pd.notna(row["prev_burden_weight"]) else None
            ),
            prev_jockey_id=prev_jockey_id,
            jockey_yr_wins=jockey_yr_wins,
            jockey_career_wins=jockey_career_wins,
            jockey_change_step1_same_race=step1,
            jockey_change_step2_other_venue=step2,
            jockey_change_affinity=affinity,
            jockey_venue_win_rate=jockey_venue_rate,
            jockey_overall_win_rate=jockey_overall_rate,
            overseas_interim_place_code=overseas_place_code,
        ))

    return RaceContext(
        race_id=race_id,
        race_name=meta.get("race_name"),
        race_date=str(meta["date"].date()),
        place_code=meta["place_code"],
        keibajo_name=None,
        distance=meta["distance"],
        surface=meta["surface"],
        class_label=None,
        grade_code=meta["grade_code"],
        jyoken_cd_3=meta["jyoken_cd_3"],
        class_level=meta["class_level"],
        pace_prediction=classify_pace_prediction(horses),
        horses=horses,
        front_bias_pit=bias.get("front_bias_pit"),
        inner_bias_pit=bias.get("inner_bias_pit"),
        bias_source=bias.get("source", "none"),
    )


def _apply_filters(
    race_ids: list[str], race_meta: dict[str, dict],
    grade_filter: list[str] | None, distance_filter: list[str] | None,
) -> list[str]:
    out = []
    for rid in race_ids:
        meta = race_meta.get(rid)
        if meta is None:
            continue
        if grade_filter and (meta["grade_code"] or "").strip() not in grade_filter:
            continue
        if distance_filter and meta["distance_bucket"] not in distance_filter:
            continue
        out.append(rid)
    return out


# ─────────────────────────────────────────────────────────────────────────
# バックテスト実行
# ─────────────────────────────────────────────────────────────────────────


def _evaluate_full(ctx: RaceContext, enabled_cfgs: list) -> dict[str, list[tuple]]:
    """各馬について有効な全条件を早期break無しで評価する（条件関数はここで1回だけ呼ぶ）。

    条件別有効性分析(Leave-One-Out)で条件を1つ除外して再集計する際、同じ条件関数を
    何度も呼び直さずに済むよう、レース単位でこの結果をキャッシュして再利用する。
    """
    full: dict[str, list[tuple]] = {}
    for horse in ctx.horses:
        results = []
        for cfg in enabled_cfgs:
            fn = CONDITION_REGISTRY.get(cfg.id)
            if fn is None:
                continue
            results.append((cfg, fn(horse, ctx, cfg.params)))
        full[horse.horse_id] = results
    return full


def _finalize_horse(horse_id: str, full_results: list[tuple], exclude_id: str | None) -> HorseEvaluation:
    """break-on-first-required-failure ロジックを（必要なら1条件を除外して）再生する。"""
    ev = HorseEvaluation(horse_id=horse_id, horse_name=None, ai_score=0.0)
    for cfg, result in full_results:
        if cfg.id == exclude_id:
            continue
        ev.conditions.append(result)
        if cfg.required and not result.passed:
            ev.eliminated = True
            ev.elimination_reason = f"{cfg.id}: {result.reason}"
            break
    return ev


def _evaluate_race_cached(
    ctx: RaceContext, enabled_cfgs: list, full_cache: dict, exclude_id: str | None = None,
) -> list:
    full = full_cache.get(ctx.race_id)
    if full is None:
        full = _evaluate_full(ctx, enabled_cfgs)
        full_cache[ctx.race_id] = full
    return [_finalize_horse(h.horse_id, full[h.horse_id], exclude_id) for h in ctx.horses]


def run_backtest_range(
    strategy: Strategy,
    race_ids: list[str],
    contexts: dict[str, RaceContext],
    race_groups: dict[str, pd.DataFrame],
    race_meta: dict[str, dict],
    full_cache: dict,
    period_label: str = "",
    from_date: str = "",
    to_date: str = "",
    exclude_condition_id: str | None = None,
    collect_stats: tuple[dict, dict] | None = None,
    min_total_score: float | None = None,
    max_candidates_for_honmei: int | None = None,
) -> BacktestResult:
    """与えられた race_ids（既に構築済みの contexts を使う）に対し、本命の成績を集計する。

    full_cache: レース単位の「全条件・早期break無し」評価結果のキャッシュ（race_id -> ...）。
        条件別有効性分析が同じレースを条件数+1回再評価する際、条件関数の呼び出し自体は
        レースごとに1回だけで済むようにするための共有キャッシュ（呼び出し元で使い回すこと）。
    exclude_condition_id: 指定時はこの条件だけを無効化したものとして集計する（Leave-One-Out用）。
    collect_stats: (applied, eliminated) の defaultdict(int) ペア。指定時、各条件の
        評価数/除外数をこの実行中に集計する（exclude_condition_id=None の基準実行で使う）。
    min_total_score / max_candidates_for_honmei: 未指定(None)なら strategy.ranking の値を使う。
        AY-1/AY-2 の閾値比較実験で、戦略JSONを書き換えずに値を変えて検証するための上書き口。
    """
    enabled_cfgs = [c for c in strategy.conditions if c.enabled]
    effective_min_score = (
        min_total_score if min_total_score is not None else strategy.ranking.min_total_score
    )
    effective_max_candidates = (
        max_candidates_for_honmei if max_candidates_for_honmei is not None else strategy.ranking.max_candidates_for_honmei
    )
    picks_all: list[tuple[int | None, float | None]] = []
    picks_by_grade: dict[str, list] = defaultdict(list)
    picks_by_distance: dict[str, list] = defaultdict(list)
    picks_by_surface: dict[str, list] = defaultdict(list)
    picks_by_confidence: dict[str, list] = defaultdict(list)
    skipped = 0

    for rid in race_ids:
        ctx = contexts.get(rid)
        if ctx is None or not ctx.horses:
            skipped += 1
            continue
        results = _evaluate_race_cached(ctx, enabled_cfgs, full_cache, exclude_condition_id)

        if collect_stats is not None and exclude_condition_id is None:
            applied, eliminated_counter = collect_stats
            for h_res in results:
                for cfg, result in zip(enabled_cfgs, h_res.conditions):
                    applied[cfg.id] += 1
                    if cfg.required and not result.passed:
                        eliminated_counter[cfg.id] += 1

        candidates = [r for r in results if not r.eliminated]
        eligible_count = len(candidates)
        if eligible_count == 0:
            skipped += 1
            continue
        umaban_map = {h.horse_id: h.umaban for h in ctx.horses}
        honmei = select_honmei(candidates, umaban_map, effective_min_score, effective_max_candidates)
        if honmei is None:
            skipped += 1
            continue
        confidence = compute_confidence(honmei, eligible_count)
        rows = race_groups.get(rid)
        hrow = rows[rows["horse_id"] == honmei.horse_id] if rows is not None else None
        if hrow is None or hrow.empty:
            skipped += 1
            continue
        hrow = hrow.iloc[0]
        # ここで参照する confirmed_rank / tan_odds(確定単勝オッズ) は「結果の評価」専用。
        # 条件判定(_build_lightweight_context 内)には一切渡していない。
        rank = int(hrow["confirmed_rank"]) if pd.notna(hrow["confirmed_rank"]) else None
        odds = float(hrow["tan_odds"]) if pd.notna(hrow["tan_odds"]) else None
        if rank is None:
            skipped += 1
            continue

        pick = (rank, odds)
        picks_all.append(pick)
        meta = race_meta[rid]
        picks_by_grade[meta["grade_bucket"]].append(pick)
        picks_by_distance[meta["distance_bucket"]].append(pick)
        picks_by_surface[meta["surface"] or "不明"].append(pick)
        picks_by_confidence[confidence].append(pick)

    return BacktestResult(
        strategy=strategy.name,
        strategy_version=strategy.version,
        from_date=from_date,
        to_date=to_date,
        period_label=period_label,
        total_races=len(race_ids),
        skipped_races=skipped,
        honmei_results=_aggregate_picks(picks_all),
        grade_breakdown={g: _aggregate_picks(p) for g, p in picks_by_grade.items()},
        surface_breakdown={s: _aggregate_picks(p) for s, p in picks_by_surface.items()},
        distance_breakdown={d: _aggregate_picks(p) for d, p in picks_by_distance.items()},
        confidence_breakdown={c: _aggregate_picks(p) for c, p in picks_by_confidence.items()},
        generated_at=datetime.now().isoformat(timespec="seconds"),
    )


def _compute_condition_analysis(
    strategy: Strategy,
    race_ids: list[str],
    contexts: dict[str, RaceContext],
    race_groups: dict[str, pd.DataFrame],
    race_meta: dict[str, dict],
    base_result: BacktestResult,
    full_cache: dict,
    applied: dict[str, int],
    eliminated: dict[str, int],
    min_total_score: float | None = None,
    max_candidates_for_honmei: int | None = None,
) -> dict[str, ConditionEffectiveness]:
    """各条件を1つずつ無効化して再集計し、ON時/OFF時の回収率を比較する(Leave-One-Out)。

    CONDITION_REGISTRY ではなく strategy.conditions（実際に有効な条件）を列挙する。
    新条件を @register_condition で追加し、戦略JSONに加えるだけで自動的に対象になる。

    full_cache を base_result の計算（run_backtest 側）と共有しているため、ここでの
    Leave-One-Out 再集計は条件関数を再呼び出しせず、キャッシュ済み結果の再生のみで済む。
    applied/eliminated は run_backtest_range の collect_stats で既に集計済みのものを受け取る。
    min_total_score/max_candidates_for_honmei は base_result と同じ条件で比較するため、
    run_backtest() から渡された有効値をそのまま引き継ぐ。
    """
    enabled_cfgs = [c for c in strategy.conditions if c.enabled]

    analysis: dict[str, ConditionEffectiveness] = {}
    for cfg in enabled_cfgs:
        without_result = run_backtest_range(
            strategy, race_ids, contexts, race_groups, race_meta, full_cache,
            exclude_condition_id=cfg.id,
            min_total_score=min_total_score, max_candidates_for_honmei=max_candidates_for_honmei,
        )

        with_stats = base_result.honmei_results
        without_stats = without_result.honmei_results
        with_rate = with_stats.tan_return_rate
        without_rate = without_stats.tan_return_rate
        if without_rate > 1e-9:
            lift = round(with_rate / without_rate, 4)
        else:
            lift = 999.0 if with_rate > 1e-9 else 1.0

        analysis[cfg.id] = ConditionEffectiveness(
            condition_id=cfg.id,
            applied_count=applied.get(cfg.id, 0),
            eliminated_count=eliminated.get(cfg.id, 0),
            with_condition=with_stats,
            without_condition=without_stats,
            lift=lift,
        )
    return analysis


def run_backtest(
    strategy_path: str,
    reference_date: str = "today",
    periods: list[str] | None = None,
    grade_filter: list[str] | None = None,
    distance_filter: list[str] | None = None,
    min_total_score: float | None = None,
    max_candidates_for_honmei: int | None = None,
) -> dict[str, BacktestResult]:
    """期間ごと(3m/6m/1y等)にバックテストを実行する。バルクロードは1回だけ行い期間間で共有する。

    min_total_score / max_candidates_for_honmei: 指定時は戦略JSONの ranking 設定を上書きする
    （AY-1/AY-2 の閾値比較実験用。JSONを書き換えずに複数パターンを検証できる）。
    """
    periods = periods or ["3m", "6m", "1y"]
    strategy = load_strategy(strategy_path)

    ref = date.today() if reference_date == "today" else date.fromisoformat(reference_date)
    period_ranges = {p: (ref - timedelta(days=_parse_period_days(p)), ref) for p in periods}
    earliest_start = min(start for start, _ in period_ranges.values())

    load_start = earliest_start - timedelta(days=_LOOKBACK_DAYS)
    bulk_df = _load_bulk_data(load_start, ref)
    race_groups = _build_race_groups(bulk_df)
    race_meta = _build_race_meta(race_groups)

    # バルクロード自体はJRA限定（高速化のため）。is_jra は常に True だが、
    # 将来の拡張に備えてフィルタとして残す。
    all_target_ids = [
        rid for rid, meta in race_meta.items()
        if meta["is_jra"] and earliest_start <= meta["date"].date() <= ref
    ]
    all_target_ids = _apply_filters(all_target_ids, race_meta, grade_filter, distance_filter)

    date_jockey_places = _build_date_jockey_places(bulk_df)
    horse_ids = {
        hid for rid in all_target_ids
        for hid in race_groups[rid]["horse_id"].dropna().tolist()
    }
    jockey_ids = {
        jid for rid in all_target_ids
        for jid in race_groups[rid]["jockey_id"].dropna().tolist()
    }
    jockey_stats = _fetch_jockey_stats(jockey_ids)
    bias_map = _fetch_bias_map(all_target_ids, race_meta)
    synergy_cache = _SynergyCache()
    synergy_cache.preload(_collect_synergy_pairs(race_groups, all_target_ids), load_start, ref)
    jockey_venue_cache = _JockeyVenueCache()
    jockey_venue_cache.preload(jockey_ids, load_start, ref)
    non_jra_races = _fetch_non_jra_interim_races(horse_ids, load_start, ref)
    past_race_cache: dict = {}

    contexts: dict[str, RaceContext] = {}
    for rid in all_target_ids:
        ctx = _build_lightweight_context(
            rid, race_groups, race_meta, bias_map, synergy_cache,
            date_jockey_places, jockey_stats, past_race_cache, jockey_venue_cache, non_jra_races,
        )
        if ctx is not None:
            contexts[rid] = ctx

    # 条件関数の評価結果はレース単位でキャッシュし、期間をまたいで（同じレースが
    # 3m/6m/1y 等の複数期間に含まれる場合も）条件関数の再呼び出しを避ける。
    full_cache: dict = {}

    results: dict[str, BacktestResult] = {}
    for p, (start, end) in period_ranges.items():
        period_ids = [rid for rid in contexts if start <= race_meta[rid]["date"].date() <= end]
        applied: dict[str, int] = defaultdict(int)
        eliminated: dict[str, int] = defaultdict(int)
        result = run_backtest_range(
            strategy, period_ids, contexts, race_groups, race_meta, full_cache,
            period_label=p, from_date=start.isoformat(), to_date=end.isoformat(),
            collect_stats=(applied, eliminated),
            min_total_score=min_total_score, max_candidates_for_honmei=max_candidates_for_honmei,
        )
        result.condition_analysis = _compute_condition_analysis(
            strategy, period_ids, contexts, race_groups, race_meta, result, full_cache, applied, eliminated,
            min_total_score=min_total_score, max_candidates_for_honmei=max_candidates_for_honmei,
        )
        results[p] = result

    return results


# ─────────────────────────────────────────────────────────────────────────
# CLI エントリポイント
# ─────────────────────────────────────────────────────────────────────────


def _cli() -> None:
    parser = argparse.ArgumentParser(description="Tipster バックテスト")
    parser.add_argument("--strategy", default="honmei_v1")
    parser.add_argument("--reference-date", default="today")
    parser.add_argument("--periods", default="3m,6m,1y")
    parser.add_argument("--grade-filter", default=None, help="例: A,B,C")
    parser.add_argument("--distance-filter", default=None, help="例: sprint,mile")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    periods = [p.strip() for p in args.periods.split(",") if p.strip()]
    grade_filter = [g.strip() for g in args.grade_filter.split(",")] if args.grade_filter else None
    distance_filter = [d.strip() for d in args.distance_filter.split(",")] if args.distance_filter else None

    results = run_backtest(
        args.strategy, reference_date=args.reference_date, periods=periods,
        grade_filter=grade_filter, distance_filter=distance_filter,
    )

    for p, r in results.items():
        hr = r.honmei_results
        print(
            f"[{p}] {r.from_date}~{r.to_date}: 対象{r.total_races}レース(スキップ{r.skipped_races}) "
            f"勝率={hr.win_rate:.1%} 複勝率={hr.place_rate:.1%} "
            f"単勝回収率={hr.tan_return_rate:.1%} 複勝回収率={hr.fuku_return_rate:.1%}"
        )

    from .backtest_renderer import render_backtest_html

    ref_str = args.reference_date if args.reference_date != "today" else date.today().isoformat()
    output_path = args.output or f"data/output/tipster/backtest_{args.strategy}_{ref_str}.html"
    path = render_backtest_html(results, output_path)
    print(f"レポート生成: {path}")


if __name__ == "__main__":
    _cli()
