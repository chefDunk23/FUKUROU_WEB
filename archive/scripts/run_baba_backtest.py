"""
scripts/run_baba_backtest.py
============================
S-1 / B-2 パターンの馬場別バックテスト（BET-7 Step2）。

検証データ (EVAL_START_DATE 以降) のみ対象。
JVDL races_v2 から確定馬場コードを取得し、馬場別に的中率・ROI を集計する。

実行:
  py -3 scripts/run_baba_backtest.py
  py -3 scripts/run_baba_backtest.py --strategy honmei_v6
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg2

from shared.config import DB_JVDL, EVAL_START_DATE
from tipster.backtest import run_backtest

_BABA_CODE_MAP = {"1": "良", "2": "稍重", "3": "重", "4": "不良"}
_BABA_LABELS   = ["良", "稍重", "重", "不良"]


def _fetch_race_baba_map(from_date: str, to_date: str) -> dict[str, str]:
    """races_v2 から 対象期間の race_id → 馬場ラベル マップを取得。

    12桁 race_id 形式（V2 DB と一致）で返す。
    ダートは dirt_baba_code 優先、芝は shiba_baba_code。
    """
    conn = psycopg2.connect(**DB_JVDL)
    try:
        cur = conn.cursor()
        # race_id 先頭8桁が YYYYMMDD 形式 → BETWEEN で範囲フィルタ
        from_compact = from_date.replace("-", "")
        to_compact   = to_date.replace("-", "")
        cur.execute(
            """
            SELECT
                SUBSTRING(race_id::text, 1, 10) || SUBSTRING(race_id::text, 15, 2) AS short_id,
                COALESCE(
                    NULLIF(TRIM(dirt_baba_code::text), ''),
                    NULLIF(TRIM(shiba_baba_code::text), '')
                ) AS baba_code
            FROM races_v2
            WHERE SUBSTRING(race_id::text, 1, 8) BETWEEN %s AND %s
              AND (
                (dirt_baba_code IS NOT NULL AND TRIM(dirt_baba_code::text) NOT IN ('', '0'))
                OR
                (shiba_baba_code IS NOT NULL AND TRIM(shiba_baba_code::text) NOT IN ('', '0'))
              )
            """,
            (from_compact, to_compact),
        )
        result: dict[str, str] = {}
        for short_id, baba_code in cur.fetchall():
            label = _BABA_CODE_MAP.get(str(baba_code or "").strip())
            if label and short_id:
                result[str(short_id)] = label
        cur.close()
    finally:
        conn.close()
    return result


def _print_table(rows: list[tuple], headers: list[str]) -> None:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print(fmt.format(*[str(c) for c in row]))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default="honmei_v6",
                    help="戦略ファイル名 (default: honmei_v6)")
    ap.add_argument("--period", default="1y",
                    help="集計期間 (default: 1y / 例: 6m, 3m)")
    args = ap.parse_args()

    strat_path = args.strategy
    period = args.period
    today_str = str(date.today())

    print(f"[baba_backtest] 戦略: {strat_path}  期間: {period}")
    print(f"[baba_backtest] 検証データ開始: {EVAL_START_DATE}")
    print()

    # ── 全体バックテスト ──────────────────────────────────────────────────
    print("[baba_backtest] 全体バックテスト実行中...")
    results_all = run_backtest(strat_path, periods=[period])
    overall = results_all[period]
    hr_o = overall.honmei_results
    print(f"  総レース数: {overall.total_races}  スキップ: {overall.skipped_races}")
    print(f"  本命 複勝率: {hr_o.place_count}/{hr_o.race_count} = {hr_o.place_rate:.1%}"
          f"  ROI(単勝): {hr_o.tan_return_rate:.1%}")
    print()

    # ── 馬場コードマップ取得 ───────────────────────────────────────────────
    # 期間を推定（run_backtest と同じ参照日ベース）
    from datetime import timedelta
    from tipster.backtest import _parse_period_days
    days = _parse_period_days(period)
    from_date_dt = date.today() - timedelta(days=days)
    from_date_str = from_date_dt.isoformat()

    print(f"[baba_backtest] JVDL から馬場コード取得中 ({from_date_str}〜{today_str})...")
    baba_map = _fetch_race_baba_map(from_date_str, today_str)
    print(f"[baba_backtest] 馬場コード取得: {len(baba_map)}レース")
    for label in _BABA_LABELS:
        cnt = sum(1 for v in baba_map.values() if v == label)
        print(f"  {label}: {cnt}レース")
    print()

    # ── 馬場別バックテスト ─────────────────────────────────────────────────
    print("[baba_backtest] 馬場別バックテスト実行中...")
    baba_results: dict[str, dict] = {}

    for baba_label in _BABA_LABELS:
        race_ids = {rid for rid, label in baba_map.items() if label == baba_label}
        if not race_ids:
            print(f"  {baba_label}: データなし")
            baba_results[baba_label] = None
            continue

        res = run_backtest(strat_path, periods=[period], filter_race_ids=race_ids)
        r = res[period]
        hr = r.honmei_results
        baba_results[baba_label] = {
            "races": hr.race_count,
            "placed": hr.place_count,
            "place_rate": hr.place_rate,
            "roi": hr.tan_return_rate,
            "win_rate": hr.win_rate,
            "skipped": r.skipped_races,
        }
        print(f"  {baba_label}: {hr.race_count}R / 複勝 {hr.place_count} ({hr.place_rate:.1%}) "
              f"/ 勝率 {hr.win_rate:.1%} / ROI {hr.tan_return_rate:.1%}"
              f"  (スキップ: {r.skipped_races})")

    # ── 結果テーブル ───────────────────────────────────────────────────────
    print()
    print("=" * 70)
    print(f"  馬場別バックテスト結果 [{strat_path}] (直近 {period})")
    print("=" * 70)
    headers = ["馬場", "R数", "複勝", "複勝率", "勝率", "ROI(単勝)", "スキップ"]
    rows = []
    rows.append(("全体",
                 hr_o.race_count,
                 hr_o.place_count,
                 f"{hr_o.place_rate:.1%}",
                 f"{hr_o.win_rate:.1%}",
                 f"{hr_o.tan_return_rate:.1%}",
                 overall.skipped_races))
    for label in _BABA_LABELS:
        r = baba_results.get(label)
        if r is None:
            rows.append((label, "-", "-", "-", "-", "-", "-"))
        else:
            rows.append((
                label,
                r["races"],
                r["placed"],
                f"{r['place_rate']:.1%}",
                f"{r['win_rate']:.1%}",
                f"{r['roi']:.1%}",
                r["skipped"],
            ))
    _print_table(rows, headers)

    # ── 判定 ─────────────────────────────────────────────────────────────
    print()
    print(">>> Step3 判定基準:")
    for label in _BABA_LABELS:
        r = baba_results.get(label)
        if r is None or r["races"] < 5:
            print(f"  {label}: サンプル不足（R数{r['races'] if r else 0} < 5）→ 判定保留")
            continue
        rate = r["place_rate"]
        roi = r["roi"]
        if rate >= 0.55:
            verdict = "優良（S昇格候補）"
        elif rate >= 0.45:
            verdict = "標準（現状維持）"
        elif rate >= 0.35:
            verdict = "やや低調（⚠️マーク候補）"
        else:
            verdict = "低調（B降格候補）"
        print(f"  {label}: 複勝{rate:.1%} ROI{roi:.1%} → {verdict}")

    print()
    print("[baba_backtest] 完了。")


if __name__ == "__main__":
    main()
