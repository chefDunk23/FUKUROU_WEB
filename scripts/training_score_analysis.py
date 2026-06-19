"""
scripts/training_score_analysis.py
====================================
調教スコアリング集計バッチ。

集計軸: 競馬場 × 馬場(芝/ダート) × 距離帯 × 美浦/栗東 × 施設(坂路/ウッド)
集計粒度: 4F総合タイム & 終い1Fラップ を 0.1秒単位でバケット化
出力指標: 勝率 / 連対率(2着以内) / 複勝率(3着以内) + サンプル数

Usage:
    py scripts/training_score_analysis.py
    py scripts/training_score_analysis.py --days-before 14 --min-bucket 15 --min-cond 50
    py scripts/training_score_analysis.py --facility hc
    py scripts/training_score_analysis.py --output-dir data/output/training_analysis
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv(_ROOT / ".env")

import math
import pandas as pd
import psycopg2
import psycopg2.extras
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from shared.config import DB_V2

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

# ── 定数 ─────────────────────────────────────────────────────────────────────

PLACE_NAMES: dict[str, str] = {
    "01": "札幌", "02": "函館", "03": "福島", "04": "新潟",
    "05": "東京", "06": "中山", "07": "中京", "08": "京都",
    "09": "阪神", "10": "小倉",
}

TRACK_NAMES: dict[str, str] = {
    "1": "芝", "2": "ダート",
}

def surface_from_track_code(tc: str) -> str | None:
    """track_code 先頭1桁で芝/ダートを判定。障害(5x)はNoneを返す。"""
    if tc and tc[0] == "1":
        return "1"
    if tc and tc[0] == "2":
        return "2"
    return None

CENTER_NAMES: dict[str, str] = {
    "0": "美浦", "1": "栗東",
}

FACILITY_NAMES: dict[str, str] = {
    "hc": "坂路", "wc": "ウッド(CW)",
}


def distance_band(distance: int) -> str:
    if distance <= 1400:
        return "短距離(~1400m)"
    if distance <= 1800:
        return "マイル(1401~1800m)"
    if distance <= 2200:
        return "中距離(1801~2200m)"
    return "長距離(2201m~)"


# ── データ取得 ────────────────────────────────────────────────────────────────

_SQL_HC = """
WITH ranked AS (
    SELECT
        re.horse_id,
        re.race_id,
        r.race_date,
        r.keibajo_code      AS place_cd,
        LEFT(r.track_code, 1) AS track_cd,
        r.distance,
        t.center_cd,
        'hc'::text          AS facility,
        t.chokyo_date,
        t.lap_total_4f      AS time_4f,
        t.lap_total_3f      AS time_3f,
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
      AND t.lap_total_4f > 0
      AND re.kakutei_chakujun IS NOT NULL
      AND re.kakutei_chakujun > 0
      AND LEFT(r.track_code, 1) IN ('1', '2')
)
SELECT
    horse_id, race_id, race_date, place_cd, track_cd, distance,
    center_cd, facility, chokyo_date, time_4f, time_3f, lap_1,
    kakutei_chakujun
FROM ranked
WHERE rn = 1
"""

_SQL_WC = """
WITH ranked AS (
    SELECT
        re.horse_id,
        re.race_id,
        r.race_date,
        r.keibajo_code      AS place_cd,
        LEFT(r.track_code, 1) AS track_cd,
        r.distance,
        t.center_cd,
        'wc'::text          AS facility,
        t.chokyo_date,
        t.lap_total_4f      AS time_4f,
        NULL::float         AS time_3f,
        t.lap_1,
        re.kakutei_chakujun,
        ROW_NUMBER() OVER (
            PARTITION BY re.race_id, re.horse_id
            ORDER BY t.chokyo_date DESC, t.chokyo_time DESC
        ) AS rn
    FROM training_data_wc t
    JOIN race_entries re ON re.horse_id = t.horse_id
    JOIN races r ON r.id = re.race_id
    WHERE t.chokyo_date >= r.race_date - INTERVAL '{days} days'
      AND t.chokyo_date <  r.race_date
      AND t.lap_total_4f IS NOT NULL
      AND t.lap_total_4f > 0
      AND re.kakutei_chakujun IS NOT NULL
      AND re.kakutei_chakujun > 0
      AND LEFT(r.track_code, 1) IN ('1', '2')
)
SELECT
    horse_id, race_id, race_date, place_cd, track_cd, distance,
    center_cd, facility, chokyo_date, time_4f, time_3f, lap_1,
    kakutei_chakujun
FROM ranked
WHERE rn = 1
"""


def _query_to_df(conn: psycopg2.extensions.connection, sql: str) -> pd.DataFrame:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    return pd.DataFrame([dict(r) for r in rows])


def fetch_data(conn: psycopg2.extensions.connection, days_before: int, facility: str) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    if facility in ("hc", "both"):
        log.info("HC(坂路) データ取得中...")
        df = _query_to_df(conn, _SQL_HC.format(days=days_before))
        frames.append(df)
        log.info(f"  HC: {len(df):,} 件")
    if facility in ("wc", "both"):
        log.info("WC(ウッド) データ取得中...")
        df = _query_to_df(conn, _SQL_WC.format(days=days_before))
        frames.append(df)
        log.info(f"  WC: {len(df):,} 件")
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


# ── 集計 ─────────────────────────────────────────────────────────────────────

def _bucket(val: float, step: float = 0.1) -> float:
    """0.1秒単位に切り捨てバケット化"""
    return math.floor(val / step) * step


def compute_stats(
    df: pd.DataFrame,
    time_col: str,
    min_bucket: int,
    min_cond: int,
) -> pd.DataFrame:
    """
    グループ別・タイム別に勝率/連対率/複勝率を集計する。

    Returns
    -------
    pd.DataFrame with columns:
        place_cd, track_cd, distance_band, center_cd, facility,
        time_bucket, n, win_rate, place2_rate, place3_rate
    """
    df = df.copy()
    df["distance_band"] = df["distance"].apply(distance_band)
    df["time_bucket"] = df[time_col].apply(lambda v: round(_bucket(v), 1) if pd.notna(v) else None)
    df = df.dropna(subset=["time_bucket"])

    group_cols = ["place_cd", "track_cd", "distance_band", "center_cd", "facility"]
    bucket_col = "time_bucket"

    records = []
    for keys, grp in df.groupby(group_cols):
        if len(grp) < min_cond:
            continue
        for t_val, tgrp in grp.groupby(bucket_col):
            n = len(tgrp)
            if n < min_bucket:
                continue
            win   = (tgrp["kakutei_chakujun"] == 1).sum()
            p2    = (tgrp["kakutei_chakujun"] <= 2).sum()
            p3    = (tgrp["kakutei_chakujun"] <= 3).sum()
            row = dict(zip(group_cols, keys))
            row.update({
                "time_col":    time_col,
                "time_bucket": round(float(t_val), 1),
                "n":           n,
                "win_rate":    round(win / n * 100, 1),
                "place2_rate": round(p2  / n * 100, 1),
                "place3_rate": round(p3  / n * 100, 1),
            })
            records.append(row)

    return pd.DataFrame(records)


# ── グラフ生成 ────────────────────────────────────────────────────────────────

def _cond_label(row: dict) -> str:
    place  = PLACE_NAMES.get(row["place_cd"], row["place_cd"])
    track  = TRACK_NAMES.get(row["track_cd"], row["track_cd"])
    dband  = row["distance_band"]
    center = CENTER_NAMES.get(str(row["center_cd"]), row["center_cd"])
    fac    = FACILITY_NAMES.get(row["facility"], row["facility"])
    return f"{place} {track} {dband} {center} {fac}"


def make_html_report(
    stats_4f: pd.DataFrame,
    stats_1f: pd.DataFrame,
    output_path: Path,
) -> None:
    """
    条件ごとに 4Fタイム / 終い1F ラップの2チャートを並べた
    インタラクティブHTML を生成する。
    """
    combined = pd.concat([
        stats_4f.assign(metric="4F総合"),
        stats_1f.assign(metric="終い1F"),
    ], ignore_index=True)

    if combined.empty:
        log.warning("集計結果が空のため HTML を生成しません")
        return

    group_cols = ["place_cd", "track_cd", "distance_band", "center_cd", "facility"]
    conditions = (
        combined[group_cols]
        .drop_duplicates()
        .sort_values(["place_cd", "track_cd", "distance_band", "center_cd", "facility"])
        .to_dict("records")
    )

    log.info(f"グラフ生成: {len(conditions)} 条件")

    # ── Plotly figure with dropdown ──────────────────────────────────────────
    fig = make_subplots(
        rows=1, cols=2,
        subplot_titles=["4F総合タイム", "終い1Fラップ"],
        horizontal_spacing=0.08,
    )

    # 全トレース（条件×metric の組み合わせ）を追加し visible で切り替え
    trace_groups: list[tuple[dict, str]] = []  # (cond_dict, metric)
    for cond in conditions:
        for metric in ["4F総合", "終い1F"]:
            trace_groups.append((cond, metric))

    n_cond = len(conditions)

    for i, (cond, metric) in enumerate(trace_groups):
        mask = combined["metric"] == metric
        for col in group_cols:
            mask &= combined[col] == cond[col]
        subset = combined[mask].sort_values("time_bucket")

        col_num = 1 if metric == "4F総合" else 2
        label = _cond_label(cond)

        # 条件インデックス
        cond_idx = conditions.index(cond)
        visible = cond_idx == 0  # 最初の条件だけ表示

        # 勝率 (bar)
        fig.add_trace(go.Bar(
            name="勝率",
            x=subset["time_bucket"],
            y=subset["win_rate"],
            text=subset["n"].apply(lambda n: f"n={n}"),
            textposition="outside",
            marker_color="rgba(55,128,191,0.8)",
            visible=visible,
            legendgroup="win",
            showlegend=(i == 0),
        ), row=1, col=col_num)

        # 連対率 (line)
        fig.add_trace(go.Scatter(
            name="連対率",
            x=subset["time_bucket"],
            y=subset["place2_rate"],
            mode="lines+markers",
            line=dict(color="orange", width=2),
            visible=visible,
            legendgroup="p2",
            showlegend=(i == 0),
        ), row=1, col=col_num)

        # 複勝率 (line)
        fig.add_trace(go.Scatter(
            name="複勝率",
            x=subset["time_bucket"],
            y=subset["place3_rate"],
            mode="lines+markers",
            line=dict(color="green", width=2, dash="dot"),
            visible=visible,
            legendgroup="p3",
            showlegend=(i == 0),
        ), row=1, col=col_num)

    # ── ドロップダウン ────────────────────────────────────────────────────────
    traces_per_cond = 6  # 4F×3本 + 1F×3本

    def _visibility(selected_idx: int) -> list[bool]:
        vis = []
        for i in range(n_cond):
            vis.extend([i == selected_idx] * traces_per_cond)
        return vis

    buttons = []
    for idx, cond in enumerate(conditions):
        buttons.append(dict(
            label=_cond_label(cond),
            method="update",
            args=[
                {"visible": _visibility(idx)},
                {"title": _cond_label(cond)},
            ],
        ))

    fig.update_layout(
        title=_cond_label(conditions[0]) if conditions else "",
        height=550,
        barmode="group",
        updatemenus=[dict(
            buttons=buttons,
            direction="down",
            showactive=True,
            x=0.0,
            xanchor="left",
            y=1.18,
            yanchor="top",
        )],
        legend=dict(orientation="h", y=-0.15),
        yaxis=dict(title="率 (%)", range=[0, 100]),
        yaxis2=dict(title="率 (%)", range=[0, 100]),
        xaxis=dict(title="タイム (秒)"),
        xaxis2=dict(title="タイム (秒)"),
        plot_bgcolor="white",
        paper_bgcolor="white",
    )

    fig.write_html(str(output_path), include_plotlyjs="cdn")
    log.info(f"HTML 出力: {output_path}")


# ── CSV 出力 ─────────────────────────────────────────────────────────────────

def export_csv(stats: pd.DataFrame, output_path: Path) -> None:
    stats = stats.copy()
    stats["place_name"]  = stats["place_cd"].map(PLACE_NAMES).fillna(stats["place_cd"])
    stats["track_name"]  = stats["track_cd"].map(TRACK_NAMES).fillna(stats["track_cd"])
    stats["center_name"] = stats["center_cd"].astype(str).map(CENTER_NAMES).fillna(stats["center_cd"].astype(str))
    stats["facility_name"] = stats["facility"].map(FACILITY_NAMES).fillna(stats["facility"])
    stats.to_csv(output_path, index=False, encoding="utf-8-sig")
    log.info(f"CSV 出力: {output_path}")


# ── テキストサマリー ──────────────────────────────────────────────────────────

def print_summary(stats: pd.DataFrame) -> None:
    if stats.empty:
        print("集計結果なし")
        return

    group_cols = ["place_cd", "track_cd", "distance_band", "center_cd", "facility"]
    print("\n" + "=" * 70)
    print("【調教スコアリング集計サマリー】")
    print("=" * 70)

    for keys, grp in stats.groupby(group_cols):
        cond = dict(zip(group_cols, keys))
        label = _cond_label(cond)
        total_n = grp["n"].sum()
        max_win = grp["win_rate"].max()
        best_row = grp.loc[grp["win_rate"].idxmax()]

        print(f"\n>> {label}  (総サンプル={total_n:,})")
        print(f"  最高勝率: {max_win:.1f}%  タイム={best_row['time_bucket']:.1f}秒  n={best_row['n']}")

        # 境界値候補: 勝率が前後バケットより 2pt 以上高いバケット
        grp_sorted = grp.sort_values("time_bucket")
        win_vals = grp_sorted["win_rate"].values
        times    = grp_sorted["time_bucket"].values
        thresholds = []
        for i in range(1, len(win_vals) - 1):
            if win_vals[i] >= win_vals[i-1] + 2.0 and win_vals[i] >= win_vals[i+1]:
                thresholds.append(f"{times[i]:.1f}秒(勝率{win_vals[i]:.1f}%)")
        if thresholds:
            print(f"  境界値候補: {', '.join(thresholds)}")

    print("\n" + "=" * 70)


# ── メイン ────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="調教スコアリング集計バッチ")
    p.add_argument("--days-before",  type=int, default=14,
                   help="レース前N日以内の調教を対象にする (default: 14)")
    p.add_argument("--min-bucket",   type=int, default=15,
                   help="1タイムバケットの最小サンプル数 (default: 15)")
    p.add_argument("--min-cond",     type=int, default=50,
                   help="1条件の最小サンプル数 (default: 50)")
    p.add_argument("--facility",     choices=["hc", "wc", "both"], default="both",
                   help="対象施設 hc=坂路 wc=ウッド both=両方 (default: both)")
    p.add_argument("--output-dir",   type=str, default="data/output/training_analysis",
                   help="出力先ディレクトリ (default: data/output/training_analysis)")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    output_dir = _ROOT / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    log.info(f"DB接続: {DB_V2['dbname']} @ {DB_V2['host']}")
    conn = psycopg2.connect(**DB_V2)
    try:
        df = fetch_data(conn, args.days_before, args.facility)
    finally:
        conn.close()

    if df.empty:
        log.warning("データが取得できませんでした。DB のデータ量を確認してください。")
        return

    log.info(f"総レコード数: {len(df):,}  ユニークレース: {df['race_id'].nunique():,}")

    # ── 4F総合タイム 集計 ───────────────────────────────────────────────────
    log.info("4F総合タイム 集計中...")
    stats_4f = compute_stats(df, "time_4f", args.min_bucket, args.min_cond)
    log.info(f"  有効バケット数: {len(stats_4f):,}")

    # ── 終い1Fラップ 集計 ───────────────────────────────────────────────────
    log.info("終い1Fラップ 集計中...")
    df_1f = df.dropna(subset=["lap_1"])
    stats_1f = compute_stats(df_1f, "lap_1", args.min_bucket, args.min_cond)
    # 時刻列を統一するため time_bucket はそのまま (lap_1 の値がバケットに入る)
    log.info(f"  有効バケット数: {len(stats_1f):,}")

    # ── CSV 出力 ─────────────────────────────────────────────────────────────
    all_stats = pd.concat([
        stats_4f.assign(time_col="time_4f"),
        stats_1f.assign(time_col="lap_1"),
    ], ignore_index=True)
    export_csv(all_stats, output_dir / "training_stats.csv")

    # ── HTML レポート ─────────────────────────────────────────────────────────
    make_html_report(stats_4f, stats_1f, output_dir / "training_report.html")

    # ── テキストサマリー ──────────────────────────────────────────────────────
    print_summary(stats_4f)

    log.info("完了")


if __name__ == "__main__":
    main()
