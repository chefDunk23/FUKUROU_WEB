"""
scripts/generate_ai_picks.py
==============================
v1 × opponent_v3 アンサンブル (α=0.5) で週末AI推奨を生成する。

出力: data/output/tipster/ai_picks.json

設計方針:
  - 既存の generate_picks_report.py / conditions_v2.py は変更しない
  - 当日バイアスなし (day_front_bias_pit=0) で動作
  - parquet から PACE_V4_COLS を引き継ぎ, 残特徴量はパイプラインで計算
  - opponent 特徴量は JVDL DB から都度ロード
"""
from __future__ import annotations

import json
import logging
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
from sqlalchemy import create_engine

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.config import DB_V2, DB_JVDL
from pace_bias_ai.pipeline import (
    build_layer1_features,
    LAYER1_ALL_COLS,
)
from pace_bias_ai.features.layer2 import build_layer2_features, LAYER2_FEATURE_COLS
from pace_bias_ai.opponent_model.features import (
    load_all_race_history,
    build_opponent_features,
    FEATURE_COLS as OPP_FEATURE_COLS,
)
from pace_bias_ai.features.rotation_flag import build_rotation_flags, ROTATION_COLS
from pace_bias_ai.features.condition_mapper import (
    ConditionMapper,
    HorseExplanation,
    FeatureExplanation,
)
from src.features.pace_features_v4 import PACE_V4_COLS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
)
log = logging.getLogger(__name__)

# ── パス定数 ─────────────────────────────────────────────────────────────────
_PARQUET = _ROOT / "outputs" / "pace_features_v4_jvdata_2022plus.parquet"
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
_HIST_ROWS_PER_HORSE = 15  # 履歴として保持する最大行数（rolling 5走の安全マージン）


# ── DB接続 ─────────────────────────────────────────────────────────────────────

def _v2_conn():
    return psycopg2.connect(**DB_V2)


def _jvdl_engine() -> sqlalchemy.engine.Engine:
    cfg = DB_JVDL
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


def _get_upcoming_races(days: list[date]) -> list[dict]:
    """指定日のレース一覧を v2 DB から取得する。"""
    races: list[dict] = []
    with _v2_conn() as conn:
        with conn.cursor() as cur:
            for d in days:
                cur.execute("""
                    SELECT id, race_date, keibajo_code, kaiji, nichiji, race_num,
                           race_name_hondai, distance, track_code, grade_code,
                           joken_code_youngest, syusso_tosu
                    FROM races
                    WHERE race_date = %s
                    ORDER BY keibajo_code, race_num
                """, (d,))
                for row in cur.fetchall():
                    races.append({
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
                    })
    log.info("週末レース: %d件", len(races))
    return races


def _get_race_entries(race_id: str) -> list[dict]:
    """1レース分の出走馬を v2 DB から取得する。"""
    with _v2_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT e.race_id, e.umaban, e.horse_id, e.horse_name,
                       e.basis_weight, e.jockey_cd, e.age AS horse_age,
                       e.horse_weight, e.trainer_cd, h.sire_id
                FROM race_entries e
                LEFT JOIN horses h ON h.horse_id = e.horse_id
                WHERE e.race_id = %s
                ORDER BY e.umaban
            """, (race_id,))
            rows = cur.fetchall()
    entries = []
    for r in rows:
        entries.append({
            "race_id":    r[0],
            "umaban":     int(r[1]) if r[1] else 0,
            "horse_id":   str(r[2]) if r[2] else "",
            "horse_name": str(r[3]) if r[3] else "",
            "basis_weight": float(r[4]) if r[4] else 55.0,
            "jockey_cd":  str(r[5]) if r[5] else "",
            "horse_age":  int(r[6]) if r[6] else 3,
            "horse_weight": float(r[7]) if r[7] else 0.0,
            "trainer_cd": str(r[8]) if r[8] else "",
            "sire_id":    str(r[9]) if r[9] else "",
        })
    return entries


# ── 履歴データ ─────────────────────────────────────────────────────────────────

def _load_parquet() -> pd.DataFrame:
    """pace_features_v4 parquet をロードする。"""
    log.info("parquet ロード: %s", _PARQUET)
    df = pd.read_parquet(_PARQUET, engine="pyarrow")
    df["horse_id"] = df["horse_id"].astype(str)
    df["race_id"]  = df["race_id"].astype(str)
    # kinryo (0.1kg 単位) を basis_weight から生成
    if "kinryo" not in df.columns and "basis_weight" in df.columns:
        df["kinryo"] = pd.to_numeric(df["basis_weight"], errors="coerce") * 10
    log.info("parquet: %d行 (最新 %s)", len(df), df["race_id"].str[:8].max())
    return df


def _last_n_rows_per_horse(df: pd.DataFrame, horse_ids: list[str], n: int) -> pd.DataFrame:
    """horse_ids に対応する直近 n 行を parquet から取得する。"""
    sub = df[df["horse_id"].isin(horse_ids)].copy()
    sub = sub.sort_values(["horse_id", "race_id"])
    sub = sub.groupby("horse_id", group_keys=False).tail(n)
    return sub.reset_index(drop=True)


# ── 予測行の構築 ──────────────────────────────────────────────────────────────

def _build_pred_row(
    entry: dict,
    race_meta: dict,
    hist: pd.DataFrame,
) -> dict:
    """1馬の予測行を構築する（PACE_V4_COLS は履歴の最新行から引き継ぐ）。"""
    horse_id = entry["horse_id"]
    h = hist[hist["horse_id"] == horse_id]

    base: dict[str, Any] = {}

    # PACE_V4_COLS: 履歴の最新行から引き継ぐ（rolling stats は "現在の馬の状態"）
    if not h.empty:
        last_row = h.iloc[-1]
        for col in PACE_V4_COLS:
            base[col] = last_row.get(col, np.nan)
        # prev_rank や past_stats も引き継ぐ
        for col in ["prev1_rank", "avg_rank_3", "avg_rank_5", "recent_win_rate_5",
                    "recent_fukusho_rate_5", "jockey_win_rate", "jockey_turf_win_rate",
                    "jockey_dirt_win_rate", "trainer_win_rate"]:
            base[col] = last_row.get(col, np.nan)
    else:
        for col in PACE_V4_COLS:
            base[col] = np.nan

    # レース固有フィールドを上書き
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

    # LAYER1 + LAYER2 を実行
    # build_layer1_features は pace_v4 を再計算するが, PACE_V4_COLS が既に存在するため
    # create_pace_features_v4 は「上書き」する。予測行は kakutei_chakujun=NaN で
    # shift(1) が履歴を参照するため正しく動作する。
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

def _ensemble_and_normalize(
    v1_scores: pd.Series,
    opp_scores: pd.Series,
    alpha: float = _ALPHA,
) -> pd.Series:
    """α×v1 + (1-α)×opp をレース内 min-max 正規化して返す。"""
    v1 = v1_scores.fillna(v1_scores.median())
    op = opp_scores.fillna(opp_scores.median())
    raw = alpha * v1 + (1 - alpha) * op

    mn, mx = raw.min(), raw.max()
    if mx > mn:
        return (raw - mn) / (mx - mn)
    return pd.Series(0.5, index=raw.index)


# ── ランク・ラベル付与 ────────────────────────────────────────────────────────

def _score_all_horses(
    ensemble: pd.Series,
    flags_df: pd.DataFrame,
    entries: list[dict],
    v1_df: pd.DataFrame,
    opp_df: pd.DataFrame,
) -> list[dict]:
    """全馬をアンサンブルスコア順に並べて返す（上位制限なし）。"""
    sorted_idx = ensemble.sort_values(ascending=False).index
    picks = []

    for rank, idx in enumerate(sorted_idx):
        entry = next((e for e in entries if e["horse_id"] == v1_df.loc[idx, "horse_id"]), {})
        horse_id = str(v1_df.loc[idx, "horse_id"])
        umaban   = int(v1_df.loc[idx, "umaban"])
        score    = float(ensemble.loc[idx])

        flags = flags_df.loc[idx].to_dict() if idx in flags_df.index else {}

        # 説明生成（上位3頭のみ）
        explanation_text = ""
        if rank < 3:
            v1_row  = v1_df.loc[idx] if idx in v1_df.index else pd.Series(dtype=float)
            opp_row = opp_df.loc[idx] if idx in opp_df.index else None
            expl = HorseExplanation(
                race_id=str(v1_row.get("race_id", "")),
                umaban=umaban,
                ai_score=score,
                top_explanations=_make_feature_explanations(v1_row),
                summary=_make_summary(v1_row, flags),
            )
            explanation_text = expl.to_full_report(
                horse_name=entry.get("horse_name", ""),
                opp_row=opp_row,
                flags=flags,
            )

        picks.append({
            "horse_id":     horse_id,
            "horse_name":   entry.get("horse_name", ""),
            "umaban":       umaban,
            "ai_v1_score":  float(v1_df.loc[idx, "_v1_score"] if "_v1_score" in v1_df.columns else 0),
            "ai_opp_score": float(opp_df.loc[idx, "_opp_score"] if "_opp_score" in opp_df.columns else 0),
            "ai_ensemble":  round(score, 4),
            "rank":         rank + 1,
            "flags":        {k: (None if pd.isna(v) else v) for k, v in flags.items()},
            "explanation":  explanation_text,
        })

    return picks


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

    # parquet ロード
    df_parquet = _load_parquet()

    # JVDL DB エンジン（opponent / rotation 共通）
    engine_jvdl = _jvdl_engine()

    # opponent 用の全履歴をロード（一括、レース単位で再利用）
    log.info("JVDL 全履歴ロード中...")
    df_ent_hist, df_races_hist = load_all_race_history(engine_jvdl)

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
        race_id = race_meta["race_id"]
        log.info("処理中: %s %s %s %dm", race_id, race_meta["race_name"],
                 race_meta["keibajo_code"], race_meta["distance"])

        entries = _get_race_entries(race_id)
        if len(entries) < 2:
            log.warning("出走馬不足 (%d頭) → スキップ: %s", len(entries), race_id)
            continue

        horse_ids = [e["horse_id"] for e in entries]

        # ── v1 特徴量計算 ──────────────────────────────────────────────────
        hist_df = _last_n_rows_per_horse(df_parquet, horse_ids, _HIST_ROWS_PER_HORSE)
        pred_rows = [_build_pred_row(e, race_meta, hist_df) for e in entries]

        v1_df = _compute_v1_features(pred_rows, hist_df)
        if v1_df.empty:
            log.warning("v1 特徴量計算失敗 → スキップ: %s", race_id)
            continue
        v1_df = v1_df.reset_index(drop=True)

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

        # ── アンサンブル ────────────────────────────────────────────────────
        # v1_scores_raw と opp_scores_raw はインデックスが別々なのでリセット
        v1_s  = pd.Series(v1_scores_raw.values,  index=range(len(entries)))
        opp_s = pd.Series(opp_scores_raw.values, index=range(len(entries)))
        ensemble = _ensemble_and_normalize(v1_s, opp_s)

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

        # ── 全馬スコアリング ────────────────────────────────────────────────
        opp_feat_reindexed = opp_feat_df.reset_index(drop=True)
        picks = _score_all_horses(
            ensemble, flags_df.reset_index(drop=True),
            entries, v1_df, opp_feat_reindexed,
        )

        race_results.append({
            "race_id":       race_id,
            "race_name":     race_meta["race_name"],
            "race_date":     race_meta["race_date"],
            "keibajo_code":  race_meta["keibajo_code"],
            "race_num":      race_meta["race_num"],
            "distance":      race_meta["distance"],
            "surface":       "芝" if str(race_meta["track_code"]).startswith("1") else "ダート",
            "grade_code":    race_meta["grade_code"],
            "field_size":    len(entries),
            "picks":         picks,
        })

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
