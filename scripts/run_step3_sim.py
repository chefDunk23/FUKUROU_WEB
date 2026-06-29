"""
Step3: 実運用シミュレーション（直近10レース日 / ダート中距離|全体パターン）
py -3 scripts/run_step3_sim.py
"""
from __future__ import annotations
import json, sys
from datetime import date, timedelta
from pathlib import Path
import numpy as np
import pandas as pd
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).parent.parent))
from ml.db import engine as _engine
from tipster.backtest import _load_bulk_data, _LOOKBACK_DAYS
from tipster.combo_backtest import _combo_str, _fetch_payouts_bulk
from tipster.conditions import _class_level_from_codes

_RC_JSON = Path(__file__).parent.parent / "tipster" / "racecourse_features.json"
with open(_RC_JSON, encoding="utf-8") as _f:
    _RC_FEAT = {k: v for k, v in json.load(_f).items() if not k.startswith("_")}
_WESTERN_PC  = frozenset(pc for pc, f in _RC_FEAT.items() if f["turf_type"] == "western")
_HILL_PC     = frozenset(pc for pc, f in _RC_FEAT.items() if f["has_hill"])
_LONG_STR_PC = frozenset(pc for pc, f in _RC_FEAT.items() if f["straight_m"] >= 400)
_SMALL_PC    = frozenset(pc for pc, f in _RC_FEAT.items() if f["course_size"] == "small")
_VENUE_NAME  = {"01":"札幌","02":"函館","03":"福島","04":"新潟","05":"東京",
                "06":"中山","07":"中京","08":"京都","09":"阪神","10":"小倉"}

_SUPP_SQL = text("""
    SELECT e.race_id, e.horse_id, h.sire_id, e.f3_time, e.popularity, r.track_condition,
           b.sire_turf_wr, b.sire_dirt_wr,
           b.sire_sprint_wr, b.sire_mile_wr, b.sire_middle_wr, b.sire_long_wr, b.sire_heavy_wr
    FROM race_entries e JOIN races r ON e.race_id = r.id JOIN horses h ON h.id = e.horse_id
    LEFT JOIN bloodline_feature_store b ON b.horse_id = e.horse_id AND b.race_id = e.race_id
    WHERE r.date BETWEEN :start AND :end
      AND e.confirmed_rank IS NOT NULL AND e.confirmed_rank > 0
      AND r.course_type IN ('芝','ダート') AND r.place_code <= '10'
""")
_SIRE_SQL = text("""
    SELECT sire_id, target_date, top3_rate AS sire_top3_rate,
           venue_01_top3_rate,venue_01_count, venue_02_top3_rate,venue_02_count,
           venue_03_top3_rate,venue_03_count, venue_04_top3_rate,venue_04_count,
           venue_05_top3_rate,venue_05_count, venue_06_top3_rate,venue_06_count,
           venue_07_top3_rate,venue_07_count, venue_08_top3_rate,venue_08_count,
           venue_09_top3_rate,venue_09_count, venue_10_top3_rate,venue_10_count,
           surface_turf_top3_rate AS sire_sfs_turf_top3, surface_dirt_top3_rate AS sire_sfs_dirt_top3
    FROM sire_feature_store ORDER BY sire_id, target_date
""")

# DB上の最新日を取得
_latest = pd.read_sql(text("""
    SELECT MAX(r.date) AS d FROM races r
    JOIN race_entries e ON e.race_id = r.id
    WHERE r.course_type='ダート' AND r.distance >= 1401 AND r.place_code <= '10'
      AND e.confirmed_rank IS NOT NULL
"""), _engine, params={})
_td = _latest.iloc[0]["d"]
if hasattr(_td, "date"):
    TO_DATE = _td.date()
elif isinstance(_td, str):
    TO_DATE = date.fromisoformat(_td)
else:
    TO_DATE = _td

# 直近10レース日
_race_dates_q = pd.read_sql(text("""
    SELECT DISTINCT r.date FROM races r
    JOIN race_entries e ON e.race_id = r.id
    WHERE r.course_type='ダート' AND r.distance >= 1401 AND r.place_code <= '10'
      AND e.confirmed_rank IS NOT NULL AND e.confirmed_rank > 0
    ORDER BY r.date DESC LIMIT 10
"""), _engine)
RACE_DAYS = sorted(_race_dates_q["date"].tolist())
_rd0 = RACE_DAYS[0] if RACE_DAYS else TO_DATE - timedelta(days=30)
FROM_DATE = _rd0.date() if hasattr(_rd0, "date") else (date.fromisoformat(str(_rd0)) if isinstance(_rd0, str) else _rd0)

print(f"[sim] 直近10レース日: {RACE_DAYS[0]} ~ {RACE_DAYS[-1]}")
load_start = FROM_DATE - timedelta(days=_LOOKBACK_DAYS)
FULL_FROM  = date.fromisoformat("2025-06-27")

# ─── データ読み込み ────────────────────────────────────────────────────────
def _merge_sire_pit(df: pd.DataFrame, sire_df: pd.DataFrame) -> pd.DataFrame:
    """PIT (Point-In-Time) 種牡馬特性マージ: レース日より前の最新スナップショットを使用"""
    sire_df = sire_df.copy()
    sire_df["target_date"] = pd.to_datetime(sire_df["target_date"]).astype("datetime64[us]")
    sire_cols = [c for c in sire_df.columns if c not in ("sire_id", "target_date")]
    race_keys = df[["race_id", "sire_id", "date"]].drop_duplicates().copy()
    race_keys["date"] = race_keys["date"].astype("datetime64[us]")
    race_keys = race_keys.sort_values("date").reset_index(drop=True)
    sire_sorted = sire_df.sort_values("target_date").reset_index(drop=True)
    pit = pd.merge_asof(
        race_keys, sire_sorted,
        left_on="date", right_on="target_date",
        by="sire_id", direction="backward",
    )
    pit = pit[["race_id", "sire_id"] + sire_cols]
    return df.merge(pit, on=["race_id", "sire_id"], how="left")


print("[sim] ベースデータ読み込み...")
base = _load_bulk_data(load_start, TO_DATE)
supp = pd.read_sql(_SUPP_SQL, _engine, params={"start": load_start, "end": TO_DATE})
print("[sim] 種牡馬特性読み込み (PIT)...")
sire_df = pd.read_sql(_SIRE_SQL, _engine)
horse_names = pd.read_sql(text("SELECT id AS horse_id, name AS horse_name FROM horses"), _engine)
jockey_names = pd.read_sql(text("SELECT id AS jockey_id, name AS jockey_name FROM jockeys"), _engine)

df = base.merge(supp, on=["race_id","horse_id"], how="left")
df = _merge_sire_pit(df, sire_df)
df = df.merge(horse_names, on="horse_id", how="left")
df = df.merge(jockey_names, on="jockey_id", how="left")

def _cl(row):
    return _class_level_from_codes(
        str(row["grade_code"]) if pd.notna(row["grade_code"]) else None,
        str(row["jyoken_cd_3"]) if pd.notna(row["jyoken_cd_3"]) else None)
df["class_level"] = df.apply(_cl, axis=1)
df["f3_rank"] = df.groupby("race_id")["f3_time"].rank(ascending=True, na_option="keep")
df["f3_rank_pct"] = df["f3_rank"] / df["field_size"]

# ─── 特徴量計算 ────────────────────────────────────────────────────────────
print("[sim] 特徴量計算中...")
df = df.sort_values(["horse_id", "date"]).reset_index(drop=True)
g = df.groupby("horse_id", sort=False)
for i in range(1, 4):
    df[f"prev{i}_rank"]       = g["confirmed_rank"].shift(i)
    df[f"prev{i}_surface"]    = g["surface"].shift(i)
    df[f"prev{i}_margin"]     = g["this_margin"].shift(i)
    df[f"prev{i}_tc"]         = g["track_condition"].shift(i)
    df[f"prev{i}_class"]      = g["class_level"].shift(i)
    df[f"prev{i}_f3pct"]      = g["f3_rank_pct"].shift(i)
    df[f"prev{i}_place_code"] = g["place_code"].shift(i)
df["days_since_prev"] = (df["date"] - df["prev_race_date"]).dt.days

# class_ok
df["cond_class_ok"] = (df["class_level"] <= df["prev1_class"]).astype(object)
df.loc[df["prev1_class"].isna(), "cond_class_ok"] = None

# interval_ok
iv = df["days_since_prev"]
df["cond_interval_ok"] = ((iv >= 15) & (iv <= 28)).astype(object)
df.loc[iv.isna(), "cond_interval_ok"] = None

# surface_ok
surf_good = pd.Series(False, index=df.index)
surf_any  = pd.Series(False, index=df.index)
for i in range(1, 4):
    same = (df[f"prev{i}_surface"] == df["surface"]) & df[f"prev{i}_surface"].notna()
    good = same & (df[f"prev{i}_rank"] <= 3) & df[f"prev{i}_rank"].notna()
    surf_good |= good; surf_any |= same
df["cond_surface_ok"] = np.where(~surf_any, None, surf_good.astype(float))

# f3_top
df["cond_f3_top"] = (df["prev1_f3pct"] <= 0.33).astype(object)
df.loc[df["prev1_f3pct"].isna(), "cond_f3_top"] = None

# sire_venue
pc = df["place_code"]
sire_ven_rate  = pd.Series(np.nan, index=df.index)
sire_ven_count = pd.Series(0.0, index=df.index)
for code in [f"{i:02d}" for i in range(1, 11)]:
    mask = pc == code
    col_r = f"venue_{code}_top3_rate"; col_c = f"venue_{code}_count"
    if col_r in df.columns:
        sire_ven_rate[mask]  = df.loc[mask, col_r]
        sire_ven_count[mask] = df.loc[mask, col_c]
df["cond_sire_venue"] = (sire_ven_rate > df["sire_top3_rate"]).astype(object)
df.loc[df["sire_top3_rate"].isna() | (sire_ven_count < 10), "cond_sire_venue"] = None

CONDS = ["class_ok", "interval_ok", "surface_ok", "f3_top", "sire_venue"]
COND_LABELS = {
    "class_ok":    "クラス維持/降級",
    "interval_ok": "間隔15-28日",
    "surface_ok":  "同馬場好走歴",
    "f3_top":      "前走上がり上位33%",
    "sire_venue":  "種牡馬同会場適性",
}

# ─── マッチ判定 ────────────────────────────────────────────────────────────
sim_mask = (
    (df["surface"] == "ダート") & (df["distance"] >= 1401)
    & (df["date"].dt.date >= FROM_DATE) & (df["date"].dt.date <= TO_DATE)
)
sub = df[sim_mask]
match = pd.Series(True, index=sub.index)
for c in CONDS:
    match = match & (sub[f"cond_{c}"] == 1.0)
matched = sub[match].copy()
matched["date_only"] = matched["date"].dt.date
matched = matched.sort_values(["date_only", "race_id", "umaban"])

# payout
target_rids = matched["race_id"].unique().tolist()
payout_map = _fetch_payouts_bulk(target_rids)

total = len(matched)
place_cnt = int((matched["confirmed_rank"] <= 3).sum())
win_cnt   = int((matched["confirmed_rank"] == 1).sum())

print()
print("=" * 70)
print("Step3: 実運用シミュレーション（直近10レース日 / ダート中距離|全体）")
print("=" * 70)
print(f"  パターン: {' + '.join(CONDS)}")
print(f"  対象期間: {FROM_DATE} ~ {TO_DATE}")
print(f"  合計: {total}頭 / 複勝{place_cnt}頭({place_cnt/total:.1%}) / 単勝{win_cnt}頭({win_cnt/total:.1%})")

def _reason(row):
    parts = []
    for c in CONDS:
        v = row.get(f"cond_{c}")
        if v == 1.0:
            parts.append(f"[OK]{COND_LABELS.get(c, c)}")
    return " | ".join(parts)

prev_date = None
for _, row in matched.iterrows():
    d = row["date_only"]
    venue = _VENUE_NAME.get(str(row["place_code"]).zfill(2), row["place_code"])
    if d != prev_date:
        print(f"\n  >> {d} {venue}")
        prev_date = d
    rank = int(row["confirmed_rank"]) if pd.notna(row["confirmed_rank"]) else "?"
    if rank != "?" and rank == 1:
        hit = "[HIT:単複]"
    elif rank != "?" and rank <= 3:
        hit = "[HIT:複] "
    else:
        hit = "[MISS]   "
    horse  = row.get("horse_name", f"id:{row['horse_id']}")
    jockey = row.get("jockey_name", "?")
    ninki  = int(row["popularity"]) if pd.notna(row["popularity"]) else "?"
    umaban = int(row["umaban"]) if pd.notna(row["umaban"]) else "?"
    reason = _reason(row)
    rpm = payout_map.get(row["race_id"], {})
    combo = _combo_str(int(row["umaban"])) if pd.notna(row["umaban"]) else None
    place_pay = rpm.get("fukusho", {}).get(combo) if combo else None
    win_pay   = rpm.get("tansho",  {}).get(combo) if combo else None
    pay_str = ""
    if place_pay: pay_str += f" 複{place_pay}円"
    if win_pay:   pay_str += f" 単{win_pay}円"
    print(f"    {hit} {horse}({umaban}番) {ninki}人気 着:{rank} {jockey}{pay_str}")
    print(f"           {reason}")

# 日別サマリー
print()
print("  【日別サマリー】")
print(f"  {'日付':<12} {'会場':>6} {'該当':>6} {'複勝':>6} {'的中率':>8}")
for d_key, grp in matched.groupby("date_only"):
    n_d = len(grp)
    p_d = int((grp["confirmed_rank"] <= 3).sum())
    venue = _VENUE_NAME.get(str(grp.iloc[0]["place_code"]).zfill(2), "?")
    print(f"  {str(d_key):<12} {venue:>6} {n_d:>6}頭 {p_d:>6}複 {p_d/n_d:>7.1%}")

print()
print("=" * 70)
print("[sim] 完了")
