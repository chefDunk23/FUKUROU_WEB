"""
scripts/enrich_pedigree_v1.py
==============================
course_features_v3_2022plus.parquet に pedigree_features_v1 の特徴量
（父・母父の血統成績統計）を追加する。

処理フロー:
    1. 入力 Parquet から全 horse_id を収集
    2. fukurou_jvdl.horses から sire_id / bms_id を取得
    3. fukurou_jvdl.sire_feature_store から父・母父の統計を取得
    4. pandas.merge_asof で race_date <= target_date の最新スナップを JOIN
    5. sire_/bms_ プレフィックスで中間列を付与 → create_pedigree_features_v1 で集約
    6. 最終 12 特徴量列のみ元 Parquet に追加して保存

生成される特徴量 (12列):
    sire_total_win_rate   sire_total_top3_rate   sire_surface_win_rate
    sire_dist_win_rate    sire_venue_win_rate     sire_count
    bms_total_win_rate    bms_total_top3_rate    bms_surface_win_rate
    bms_dist_win_rate     bms_venue_win_rate      bms_count

Usage:
    py -3.13 scripts/enrich_pedigree_v1.py
    py -3.13 scripts/enrich_pedigree_v1.py \\
        --in  outputs/course_features_v3_2022plus.parquet \\
        --out outputs/pedigree_features_v1_2022plus.parquet
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd
import psycopg2
import psycopg2.extras

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.db.jvdl import get_conn as get_jvdl_conn
from src.features.pedigree_features_v1 import PEDIGREE_V1_COLS, create_pedigree_features_v1

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

_DEFAULT_IN  = Path("outputs/course_features_v3_2022plus.parquet")
_DEFAULT_OUT = Path("outputs/pedigree_features_v1_2022plus.parquet")

# sire_feature_store から取得する列
_SIRE_STORE_COLS = [
    "sire_id", "target_date",
    # 基本
    "total_count", "win_rate", "top3_rate",
    # 馬場面別（勝率 + 複勝率）
    "surface_turf_win_rate", "surface_turf_top3_rate",
    "surface_dirt_win_rate", "surface_dirt_top3_rate",
    # 距離区分別
    "dist_sprint_win_rate", "dist_mile_win_rate",
    "dist_middle_win_rate", "dist_long_win_rate",
    # 競馬場別
    "venue_01_win_rate", "venue_02_win_rate", "venue_03_win_rate",
    "venue_04_win_rate", "venue_05_win_rate", "venue_06_win_rate",
    "venue_07_win_rate", "venue_08_win_rate", "venue_09_win_rate",
    "venue_10_win_rate",
    # 道悪適性（馬場状態別勝率）
    "baba_firm_win_rate", "baba_yaya_win_rate",
    "baba_omo_win_rate",  "baba_furyo_win_rate",
    # 成長曲線（年齢帯別勝率）
    "age2_win_rate", "age3_win_rate",
    "age4_win_rate", "age5plus_win_rate",
    # 性別別勝率
    "sex_male_win_rate", "sex_female_win_rate",
    # 産駒平均馬体重（馬体重クロス用）
    "avg_all_weight",
]


def _fetch_horse_lineage(horse_ids: list[str]) -> pd.DataFrame:
    """fukurou_jvdl.horses から horse_id → sire_id, bms_id, sex, birthday を一括取得。"""
    if not horse_ids:
        return pd.DataFrame(columns=["horse_id", "sire_id", "bms_id", "sex", "birthday"])

    with get_jvdl_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id AS horse_id, sire_id, bms_id, sex, birthday "
                "FROM horses WHERE id = ANY(%s)",
                (horse_ids,),
            )
            rows = cur.fetchall()

    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["horse_id", "sire_id", "bms_id", "sex", "birthday"])

    df["horse_id"] = df["horse_id"].astype(str)
    df["sire_id"]  = df["sire_id"].where(df["sire_id"].notna(), None)
    df["bms_id"]   = df["bms_id"].where(df["bms_id"].notna(), None)
    df["sex"]      = df["sex"].astype(str).fillna("1")
    df["birthday"] = pd.to_datetime(df["birthday"], errors="coerce")
    return df


def _fetch_sire_store(sire_ids: list[str]) -> pd.DataFrame:
    """sire_feature_store から指定 sire_id の全スナップショットを取得。"""
    valid = [s for s in sire_ids if s is not None and str(s).strip()]
    if not valid:
        return pd.DataFrame(columns=_SIRE_STORE_COLS)

    with get_jvdl_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"SELECT {', '.join(_SIRE_STORE_COLS)} "
                "FROM sire_feature_store "
                "WHERE sire_id = ANY(%s) "
                "ORDER BY sire_id, target_date",
                (valid,),
            )
            rows = cur.fetchall()

    if not rows:
        return pd.DataFrame(columns=_SIRE_STORE_COLS)

    df = pd.DataFrame(rows)
    df["sire_id"]     = df["sire_id"].astype(str)
    df["target_date"] = pd.to_datetime(df["target_date"])
    return df


def _merge_sire_asof(
    df: pd.DataFrame,
    id_col: str,       # "sire_id" or "bms_id"
    prefix: str,       # "sire" or "bms"
    lineage: pd.DataFrame,
    store: pd.DataFrame,
) -> pd.DataFrame:
    """
    df に lineage.id_col を JOIN し、
    race_date <= target_date の最新スナップを merge_asof で付与する。
    結果列は prefix_<store_col> に改名する。
    """
    if store.empty:
        for col in _SIRE_STORE_COLS[2:]:  # sire_id, target_date 以外
            df[f"{prefix}_{col}"] = float("nan")
        return df

    # lineage から該当 id_col を取得 (horse_id → sire_id or bms_id)
    id_map = lineage[["horse_id", id_col]].drop_duplicates("horse_id")
    df = df.merge(id_map, on="horse_id", how="left", suffixes=("", "_new"))
    # 既に同名カラムがある場合は上書き防止
    if f"{id_col}_new" in df.columns:
        df[id_col] = df[f"{id_col}_new"].combine_first(df.get(id_col))
        df = df.drop(columns=[f"{id_col}_new"])

    # race_date 型揃え
    df["race_date"] = pd.to_datetime(df["race_date"])

    # merge_asof: (id_col, race_date) × (sire_id, target_date)
    # by=id_col で各種 sire/bms ごとに最新 target_date ≤ race_date を選択
    df_sorted   = df[["horse_id", id_col, "race_date"]].copy().sort_values("race_date")
    store_sorted = store.sort_values("target_date")

    merged = pd.merge_asof(
        df_sorted.rename(columns={id_col: "_join_id"}),
        store_sorted.rename(columns={"sire_id": "_join_id"}),
        left_on="race_date",
        right_on="target_date",
        by="_join_id",
        direction="backward",
    )
    merged = merged.drop(columns=["target_date"], errors="ignore")

    # 元 df に付与 (horse_id, race_date をキーに left join)
    stat_cols = [c for c in store_sorted.columns if c not in ("sire_id", "target_date")]
    rename_map = {c: f"{prefix}_{c}" for c in stat_cols}
    merged = merged.rename(columns=rename_map)

    for col in rename_map.values():
        df[col] = merged[col].values

    return df


def enrich(in_path: Path, out_path: Path) -> None:
    log.info("入力Parquet読み込み: %s", in_path)
    df = pd.read_parquet(in_path)
    log.info("  %d行 / %dレース / %d列", len(df), df["race_id"].nunique(), len(df.columns))

    # ── 1. horse lineage 取得 ─────────────────────────────────────────────────
    horse_ids = df["horse_id"].astype(str).unique().tolist()
    log.info("horse_id 数: %d → lineage 取得中...", len(horse_ids))
    lineage = _fetch_horse_lineage(horse_ids)
    df["horse_id"] = df["horse_id"].astype(str)

    # horse_age (レース時の年齢) と horse_sex をデータフレームに付与
    age_sex = lineage[["horse_id", "sex", "birthday"]].copy()
    df = df.merge(age_sex, on="horse_id", how="left")
    df["race_date"] = pd.to_datetime(df["race_date"], errors="coerce")
    df["horse_age"] = (df["race_date"] - df["birthday"]).dt.days / 365.25
    df["horse_sex"] = df["sex"].fillna("1")
    df = df.drop(columns=["sex", "birthday"], errors="ignore")
    log.info(
        "  horse_age NaN率=%.1f%%  horse_sex NaN率=%.1f%%",
        df["horse_age"].isna().mean() * 100,
        df["horse_sex"].isna().mean() * 100,
    )

    sire_ids_from_lineage = lineage["sire_id"].dropna().unique().tolist()
    bms_ids_from_lineage  = lineage["bms_id"].dropna().unique().tolist()
    all_sire_ids = list(set(sire_ids_from_lineage + bms_ids_from_lineage))
    log.info(
        "  sire_id=%d  bms_id=%d  ユニーク種牡馬=%d",
        len(sire_ids_from_lineage), len(bms_ids_from_lineage), len(all_sire_ids),
    )

    # ── 2. sire_feature_store 取得 ────────────────────────────────────────────
    log.info("sire_feature_store 取得中（%d 種牡馬）...", len(all_sire_ids))
    store = _fetch_sire_store(all_sire_ids)
    log.info("  スナップショット行数: %d", len(store))

    # ── 3. 父統計 JOIN ────────────────────────────────────────────────────────
    log.info("父（sire）統計を merge_asof で JOIN 中...")
    df = _merge_sire_asof(df, "sire_id", "sire", lineage, store)

    # ── 4. 母父統計 JOIN ──────────────────────────────────────────────────────
    log.info("母父（bms）統計を merge_asof で JOIN 中...")
    df = _merge_sire_asof(df, "bms_id", "bms", lineage, store)

    # ── 5. pedigree 特徴量生成 ────────────────────────────────────────────────
    log.info("pedigree_features_v1 生成中...")
    df_enriched = create_pedigree_features_v1(df)

    existing = [c for c in PEDIGREE_V1_COLS if c in df.columns]
    if existing:
        log.warning("既存列を上書きします: %s", existing)
    for col in PEDIGREE_V1_COLS:
        df[col] = df_enriched[col].to_numpy()

    # ── 中間列をクリーンアップ（sire_/bms_ プレフィックスの raw 列を除去）─────
    _KEEP = set(PEDIGREE_V1_COLS) | {"sire_id", "bms_id", "horse_age", "horse_sex"}
    raw_cols = [
        c for c in df.columns
        if (c.startswith("sire_") or c.startswith("bms_"))
        and c not in _KEEP
    ]
    df = df.drop(columns=raw_cols)

    log.info("=== pedigree_v1 特徴量 NaN率 ===")
    for col in PEDIGREE_V1_COLS:
        nan_pct = df[col].isna().mean() * 100
        log.info("  %-35s %5.1f%%", col, nan_pct)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)
    log.info(
        "出力: %s  (%d行 / %d列)",
        out_path, len(df), len(df.columns),
    )
    log.info("次のステップ: py -3.13 scripts/train_v2_submodels.py --parquet %s", out_path)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="course_v3 Parquetに pedigree_v1 特徴量を追加する"
    )
    p.add_argument("--in",  dest="in_path",  type=Path, default=_DEFAULT_IN)
    p.add_argument("--out", dest="out_path", type=Path, default=_DEFAULT_OUT)
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    if not args.in_path.exists():
        log.error("入力Parquetが見つかりません: %s", args.in_path)
        sys.exit(1)
    enrich(args.in_path, args.out_path)
