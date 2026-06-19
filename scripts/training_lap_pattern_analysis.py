"""
scripts/training_lap_pattern_analysis.py
==========================================
坂路(HC)ラップパターン分類による集計バッチ。

分類基準（A2のみユーザー指定で確定、他は対称的に類推 — 要すり合わせ）:

  A系列 = 全区間で加速ラップ（lap4 > lap3 > lap2 > lap1）
    A1 = 終い1F(lap1)が12秒台 (12.0~12.9)
    A2 = 終い2F(lap2・lap1)とも12秒台
    A3 = 終い1F(lap1)が11秒台 (11.0~11.9)

  B系列 = 途中まで加速 → 終いで失速
    B1 = lap2まで加速 → 最終1Fで失速(lap2 < lap1)。失速直前(lap2)が12秒台
    B2 = lap3まで加速 → 終い2F(lap2,lap1)とも失速(lap3 < lap2 かつ lap3 < lap1)。失速直前(lap3)が12秒台
    B3 = 同上、lap3が11秒台

  共通条件: 4F総合タイム(lap_total_4f) <= --time-max (default 59.9秒)

集計軸: 競馬場 × 馬場(芝/ダート) × 距離帯 × 美浦/栗東 × ラップパターン

Usage:
    py scripts/training_lap_pattern_analysis.py
    py scripts/training_lap_pattern_analysis.py --days-before 14 --time-max 59.9 --min-cell 10
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv(_ROOT / ".env")

import pandas as pd
import psycopg2
import psycopg2.extras
import plotly.graph_objects as go

from shared.config import DB_V2
from scripts.training_score_analysis import (
    PLACE_NAMES, TRACK_NAMES, CENTER_NAMES, distance_band,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

PATTERNS = ["A1", "A2", "A3", "B1", "B2", "B3"]

# ── SQL ──────────────────────────────────────────────────────────────────────

_SQL_HC_LAPS = """
WITH ranked AS (
    SELECT
        re.horse_id,
        re.race_id,
        r.race_date,
        r.keibajo_code        AS place_cd,
        LEFT(r.track_code, 1) AS track_cd,
        r.distance,
        t.center_cd,
        t.chokyo_date,
        t.lap_total_4f        AS total_time,
        t.lap_4,
        t.lap_3,
        t.lap_2,
        t.lap_1,
        re.kakutei_chakujun,
        ROW_NUMBER() OVER (
            PARTITION BY re.race_id, re.horse_id
            ORDER BY t.chokyo_date DESC, t.chokyo_time DESC
        ) AS rn
    FROM training_data_hc t
    JOIN race_entries re ON re.horse_id = t.horse_id
    JOIN races r ON r.id = re.race_id
    WHERE t.chokyo_date >= r.race_date - INTERVAL '{days} days'
      AND t.chokyo_date <  r.race_date
      AND t.lap_total_4f IS NOT NULL
      AND t.lap_4 IS NOT NULL AND t.lap_3 IS NOT NULL
      AND t.lap_2 IS NOT NULL AND t.lap_1 IS NOT NULL
      AND re.kakutei_chakujun IS NOT NULL
      AND re.kakutei_chakujun > 0
      AND LEFT(r.track_code, 1) IN ('1', '2')
)
SELECT
    horse_id, race_id, race_date, place_cd, track_cd, distance,
    center_cd, chokyo_date, total_time, lap_4, lap_3, lap_2, lap_1,
    kakutei_chakujun
FROM ranked
WHERE rn = 1
"""


def fetch_data(conn: psycopg2.extensions.connection, days_before: int) -> pd.DataFrame:
    log.info("HC(坂路) ラップデータ取得中...")
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(_SQL_HC_LAPS.format(days=days_before))
        rows = cur.fetchall()
    df = pd.DataFrame([dict(r) for r in rows])
    log.info(f"  取得: {len(df):,} 件")
    return df


# ── 分類ロジック ──────────────────────────────────────────────────────────────

def _tier(v: float) -> int:
    """12.6 -> 12 (秒台の整数部)"""
    return int(v // 1)


def classify_lap_pattern(lap4: float, lap3: float, lap2: float, lap1: float,
                          total_time: float, time_max: float) -> str | None:
    if total_time > time_max:
        return None

    accel_all = lap4 > lap3 > lap2 > lap1
    if accel_all:
        if _tier(lap1) == 12 and _tier(lap2) == 12:
            return "A2"
        if _tier(lap1) == 12:
            return "A1"
        if _tier(lap1) == 11:
            return "A3"
        return None

    decel_1f = (lap4 > lap3) and (lap3 > lap2) and (lap2 < lap1)
    if decel_1f and _tier(lap2) == 12:
        return "B1"

    decel_2f = (lap4 > lap3) and (lap3 < lap2) and (lap3 < lap1)
    if decel_2f:
        if _tier(lap3) == 12:
            return "B2"
        if _tier(lap3) == 11:
            return "B3"

    return None


def apply_classification(df: pd.DataFrame, time_max: float) -> pd.DataFrame:
    df = df.copy()
    df["pattern"] = df.apply(
        lambda row: classify_lap_pattern(
            row["lap_4"], row["lap_3"], row["lap_2"], row["lap_1"],
            row["total_time"], time_max,
        ),
        axis=1,
    )
    return df


# ── 集計 ─────────────────────────────────────────────────────────────────────

def compute_stats(df: pd.DataFrame, min_cell: int) -> pd.DataFrame:
    df = df.copy()
    df["distance_band"] = df["distance"].apply(distance_band)
    classified = df.dropna(subset=["pattern"])

    group_cols = ["place_cd", "track_cd", "distance_band", "center_cd"]
    records = []
    for keys, grp in classified.groupby(group_cols):
        for pattern, pgrp in grp.groupby("pattern"):
            n = len(pgrp)
            if n < min_cell:
                continue
            win = (pgrp["kakutei_chakujun"] == 1).sum()
            p2  = (pgrp["kakutei_chakujun"] <= 2).sum()
            p3  = (pgrp["kakutei_chakujun"] <= 3).sum()
            row = dict(zip(group_cols, keys))
            row.update({
                "pattern":     pattern,
                "n":           n,
                "win_rate":    round(win / n * 100, 1),
                "place2_rate": round(p2  / n * 100, 1),
                "place3_rate": round(p3  / n * 100, 1),
            })
            records.append(row)

    return pd.DataFrame(records)


def compute_baseline(df: pd.DataFrame, min_cell: int) -> pd.DataFrame:
    """パターンに当てはまらない(全坂路調教の)ベースライン勝率も条件別に算出"""
    df = df.copy()
    df["distance_band"] = df["distance"].apply(distance_band)
    group_cols = ["place_cd", "track_cd", "distance_band", "center_cd"]
    records = []
    for keys, grp in df.groupby(group_cols):
        n = len(grp)
        if n < min_cell:
            continue
        win = (grp["kakutei_chakujun"] == 1).sum()
        p2  = (grp["kakutei_chakujun"] <= 2).sum()
        p3  = (grp["kakutei_chakujun"] <= 3).sum()
        row = dict(zip(group_cols, keys))
        row.update({
            "pattern":     "ALL(基準)",
            "n":           n,
            "win_rate":    round(win / n * 100, 1),
            "place2_rate": round(p2  / n * 100, 1),
            "place3_rate": round(p3  / n * 100, 1),
        })
        records.append(row)
    return pd.DataFrame(records)


# ── 出力 ─────────────────────────────────────────────────────────────────────

def _cond_label(row: dict) -> str:
    place  = PLACE_NAMES.get(row["place_cd"], row["place_cd"])
    track  = TRACK_NAMES.get(row["track_cd"], row["track_cd"])
    dband  = row["distance_band"]
    center = CENTER_NAMES.get(str(row["center_cd"]), row["center_cd"])
    return f"{place} {track} {dband} {center}"


def export_csv(stats: pd.DataFrame, baseline: pd.DataFrame, output_path: Path) -> None:
    combined = pd.concat([baseline, stats], ignore_index=True)
    combined["place_name"]  = combined["place_cd"].map(PLACE_NAMES).fillna(combined["place_cd"])
    combined["track_name"]  = combined["track_cd"].map(TRACK_NAMES).fillna(combined["track_cd"])
    combined["center_name"] = combined["center_cd"].astype(str).map(CENTER_NAMES).fillna(combined["center_cd"].astype(str))
    combined = combined.sort_values(["place_cd", "track_cd", "distance_band", "center_cd", "pattern"])
    combined.to_csv(output_path, index=False, encoding="utf-8-sig")
    log.info(f"CSV 出力: {output_path}")


def make_html_report(stats: pd.DataFrame, baseline: pd.DataFrame, output_path: Path) -> None:
    if stats.empty:
        log.warning("集計結果が空のため HTML を生成しません")
        return

    group_cols = ["place_cd", "track_cd", "distance_band", "center_cd"]
    conditions = (
        stats[group_cols]
        .drop_duplicates()
        .sort_values(group_cols)
        .to_dict("records")
    )
    log.info(f"グラフ生成: {len(conditions)} 条件")

    fig = go.Figure()
    n_cond = len(conditions)

    for idx, cond in enumerate(conditions):
        mask = pd.Series(True, index=stats.index)
        for col in group_cols:
            mask &= stats[col] == cond[col]
        subset = stats[mask].set_index("pattern").reindex(PATTERNS).reset_index()

        bmask = pd.Series(True, index=baseline.index)
        for col in group_cols:
            bmask &= baseline[col] == cond[col]
        base_row = baseline[bmask]
        base_win = base_row["win_rate"].iloc[0] if len(base_row) else None

        visible = idx == 0

        fig.add_trace(go.Bar(
            name="勝率",
            x=subset["pattern"],
            y=subset["win_rate"],
            text=subset["n"].apply(lambda n: f"n={int(n)}" if pd.notna(n) else "n=0"),
            textposition="outside",
            marker_color="rgba(55,128,191,0.85)",
            visible=visible,
            legendgroup="win",
            showlegend=(idx == 0),
        ))
        fig.add_trace(go.Scatter(
            name="連対率",
            x=subset["pattern"],
            y=subset["place2_rate"],
            mode="lines+markers",
            line=dict(color="orange", width=2),
            visible=visible,
            legendgroup="p2",
            showlegend=(idx == 0),
        ))
        fig.add_trace(go.Scatter(
            name="複勝率",
            x=subset["pattern"],
            y=subset["place3_rate"],
            mode="lines+markers",
            line=dict(color="green", width=2, dash="dot"),
            visible=visible,
            legendgroup="p3",
            showlegend=(idx == 0),
        ))
        if base_win is not None:
            fig.add_trace(go.Scatter(
                name="基準勝率(全体)",
                x=PATTERNS,
                y=[base_win] * len(PATTERNS),
                mode="lines",
                line=dict(color="gray", width=1, dash="dash"),
                visible=visible,
                legendgroup="base",
                showlegend=(idx == 0),
            ))

    traces_per_cond = 4
    def _visibility(selected_idx: int) -> list[bool]:
        vis = []
        for i in range(n_cond):
            vis.extend([i == selected_idx] * traces_per_cond)
        return vis

    buttons = [
        dict(label=_cond_label({**cond, "distance_band": cond["distance_band"]}),
             method="update",
             args=[{"visible": _visibility(idx)}, {"title": _cond_label(cond)}])
        for idx, cond in enumerate(conditions)
    ]

    fig.update_layout(
        title=_cond_label(conditions[0]) if conditions else "",
        height=550,
        barmode="group",
        updatemenus=[dict(
            buttons=buttons, direction="down", showactive=True,
            x=0.0, xanchor="left", y=1.18, yanchor="top",
        )],
        legend=dict(orientation="h", y=-0.15),
        yaxis=dict(title="率 (%)", range=[0, 100]),
        xaxis=dict(title="ラップパターン"),
        plot_bgcolor="white",
        paper_bgcolor="white",
    )

    fig.write_html(str(output_path), include_plotlyjs="cdn")
    log.info(f"HTML 出力: {output_path}")


def print_summary(stats: pd.DataFrame, baseline: pd.DataFrame) -> None:
    if stats.empty:
        print("集計結果なし")
        return

    group_cols = ["place_cd", "track_cd", "distance_band", "center_cd"]
    print("\n" + "=" * 70)
    print("【坂路ラップパターン分類 集計サマリー】")
    print("=" * 70)

    for keys, grp in stats.groupby(group_cols):
        cond = dict(zip(group_cols, keys))
        label = _cond_label(cond)

        bmask = pd.Series(True, index=baseline.index)
        for col in group_cols:
            bmask &= baseline[col] == cond[col]
        base_row = baseline[bmask]
        base_win = base_row["win_rate"].iloc[0] if len(base_row) else None
        base_n   = base_row["n"].iloc[0] if len(base_row) else None

        print(f"\n>> {label}  (基準勝率={base_win}% n={base_n})")
        for _, r in grp.sort_values("pattern").iterrows():
            diff = f"(+{r['win_rate']-base_win:.1f}pt)" if base_win is not None else ""
            print(f"  {r['pattern']}: 勝率={r['win_rate']}% 連対率={r['place2_rate']}% "
                  f"複勝率={r['place3_rate']}% n={r['n']} {diff}")

    print("\n" + "=" * 70)


# ── メイン ────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="坂路ラップパターン分類集計バッチ")
    p.add_argument("--days-before", type=int, default=14,
                   help="レース前N日以内の調教を対象にする (default: 14)")
    p.add_argument("--time-max",    type=float, default=59.9,
                   help="4F総合タイムの上限。これを超える調教は分類対象外 (default: 59.9)")
    p.add_argument("--min-cell",    type=int, default=10,
                   help="1セル(条件×パターン)の最小サンプル数 (default: 10)")
    p.add_argument("--output-dir",  type=str, default="data/output/training_analysis",
                   help="出力先ディレクトリ")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    output_dir = _ROOT / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    log.info(f"DB接続: {DB_V2['dbname']} @ {DB_V2['host']}")
    conn = psycopg2.connect(**DB_V2)
    try:
        df = fetch_data(conn, args.days_before)
    finally:
        conn.close()

    if df.empty:
        log.warning("データが取得できませんでした")
        return

    log.info(f"分類実行中 (time_max={args.time_max}秒)...")
    df = apply_classification(df, args.time_max)
    classified_n = df["pattern"].notna().sum()
    log.info(f"  分類成立: {classified_n:,} / {len(df):,} 件 ({classified_n/len(df)*100:.1f}%)")
    for pat in PATTERNS:
        cnt = (df["pattern"] == pat).sum()
        log.info(f"    {pat}: {cnt:,} 件")

    stats = compute_stats(df, args.min_cell)
    baseline = compute_baseline(df, args.min_cell)

    export_csv(stats, baseline, output_dir / "lap_pattern_stats.csv")
    make_html_report(stats, baseline, output_dir / "lap_pattern_report.html")
    print_summary(stats, baseline)

    log.info("完了")


if __name__ == "__main__":
    main()
