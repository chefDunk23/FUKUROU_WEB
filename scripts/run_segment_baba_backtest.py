"""
scripts/run_segment_baba_backtest.py
======================================
Phase 2 検証済みパターン（S-1 / B-2）のセグメント限定 × 馬場別バックテスト（BET-7）。

バックテストは JVDL DB (fukurou_jvdl) の races テーブルを使用する（ml.db = JVDL）。
セグメント・馬場コードも同じ races テーブルから取得することで race_id 形式が一致する。

実行:
  py -3 scripts/run_segment_baba_backtest.py
  py -3 scripts/run_segment_baba_backtest.py --strategy honmei_v7
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg2

from shared.config import DB_JVDL, EVAL_START_DATE
from tipster.backtest import run_backtest, _parse_period_days

# ── セグメント定義（Phase 2 確定値）────────────────────────────────────────
# S-1: ダート中距離(>1400m) + 坂あり競馬場
_HILL_VENUES = {"03", "05", "06", "07", "09"}  # 福島/東京/中山/中京/阪神

# ── 馬場コード（JVDL races.track_condition）────────────────────────────────
# '0'=不明, '1'=良, '2'=稍重, '3'=重, '4'=不良
_BABA_CODE_MAP = {"1": "良", "2": "稍重", "3": "重", "4": "不良"}
_BABA_LABELS   = ["良", "稍重", "重", "不良"]

_MIN_SAMPLE = 30  # 信頼できる最低サンプル数


# ─────────────────────────────────────────────────────────────────────────────
# DB 取得 (JVDL races テーブルのみ使用)
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_segment_baba_ids(from_date: str, to_date: str) -> dict:
    """JVDL races テーブルから S-1 / B-2 セグメントと馬場コードの race_id を取得する。

    JVDL races テーブルのカラム:
      id              — 12桁レースID（バックテストの filter_race_ids と一致）
      date            — 発走日
      place_code      — 競馬場コード '01'〜'10'（JRA）
      course_type     — 'ダート' / '芝' / '障害' / None
      distance        — 距離(m)
      track_condition — '0'(不明), '1'(良), '2'(稍重), '3'(重), '4'(不良)

    Returns:
      {
        "S1":   {race_id, ...},   # ダート中距離 + 坂あり
        "B2":   {race_id, ...},   # ダート中距離 全場
        "baba": {race_id: label}, # 馬場ラベルマップ
      }
    """
    conn = psycopg2.connect(**DB_JVDL)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, place_code, track_condition"
            " FROM races"
            " WHERE date BETWEEN %s AND %s"
            "   AND course_type = 'ダート'"
            "   AND distance > 1400"
            "   AND place_code <= '10'",  # JRA のみ（地方除外）
            (from_date, to_date),
        )
        s1_ids:  set[str]      = set()
        b2_ids:  set[str]      = set()
        baba_map: dict[str, str] = {}
        for race_id, place_code, track_cond in cur.fetchall():
            rid = str(race_id)
            pc  = str(place_code or "").strip().zfill(2)
            b2_ids.add(rid)
            if pc in _HILL_VENUES:
                s1_ids.add(rid)
            label = _BABA_CODE_MAP.get(str(track_cond or "").strip())
            if label:
                baba_map[rid] = label
        cur.close()
    finally:
        conn.close()
    return {"S1": s1_ids, "B2": b2_ids, "baba": baba_map}


# ─────────────────────────────────────────────────────────────────────────────
# バックテスト実行ラッパー
# ─────────────────────────────────────────────────────────────────────────────

def _run(strat: str, period: str, filter_ids: set[str]) -> dict | None:
    """filter_race_ids でフィルタしてバックテストを実行し、結果 dict を返す。"""
    if not filter_ids:
        return None
    results = run_backtest(strat, periods=[period], filter_race_ids=filter_ids)
    r = results[period]
    hr = r.honmei_results
    return {
        "races":       hr.race_count,
        "skipped":     r.skipped_races,
        "place_count": hr.place_count,
        "place_rate":  hr.place_rate,
        "win_rate":    hr.win_rate,
        "roi":         hr.tan_return_rate,
    }


def _verdict(r: dict | None, base_rate: float | None = None) -> str:
    if r is None:
        return "データなし"
    n = r["races"]
    if n < _MIN_SAMPLE:
        return f"サンプル不足({n}R<{_MIN_SAMPLE})"
    rate = r["place_rate"]
    warn = ""
    if base_rate is not None and (base_rate - rate) >= 0.10:
        warn = " [良より10pt以上低下]"
    if rate >= 0.55:
        return "優良(S昇格候補)" + warn
    if rate >= 0.45:
        return "標準" + warn
    if rate >= 0.35:
        return "やや低調" + warn
    return "低調" + warn


def _print_table(rows: list[tuple], headers: list[str]) -> None:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, c in enumerate(row):
            widths[i] = max(widths[i], len(str(c)))
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print(fmt.format(*[str(c) for c in row]))


# ─────────────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default="honmei_v6",
                    help="戦略ファイル名 (default: honmei_v6)")
    ap.add_argument("--period", default="1y",
                    help="集計期間 (default: 1y)")
    args = ap.parse_args()

    strat  = args.strategy
    period = args.period
    today  = date.today()
    days   = _parse_period_days(period)

    # 検証データ開始日 (EVAL_START_DATE) を下限にする（リーク防止）
    from_dt = max(date.fromisoformat(EVAL_START_DATE), today - timedelta(days=days))
    from_str = from_dt.isoformat()
    to_str   = today.isoformat()

    print(f"[seg_baba_bt] 戦略: {strat}  期間: {from_str} 〜 {to_str}")
    print(f"[seg_baba_bt] 検証データ開始: {EVAL_START_DATE}")
    print()

    # ── セグメント + 馬場コード 一括取得（JVDL races テーブル）────────────
    print("[seg_baba_bt] JVDL races テーブルからセグメント・馬場コード取得中...")
    fetched = _fetch_segment_baba_ids(from_str, to_str)
    seg_ids = {"S1": fetched["S1"], "B2": fetched["B2"]}
    baba_map = fetched["baba"]
    print(f"  S-1 (ダート中距離+坂あり): {len(seg_ids['S1'])}R")
    print(f"  B-2 (ダート中距離 全場):  {len(seg_ids['B2'])}R")
    print(f"  馬場コード取得: {len(baba_map)}R")
    baba_count: dict[str, int] = {lb: 0 for lb in _BABA_LABELS}
    for v in baba_map.values():
        if v in baba_count:
            baba_count[v] += 1
    for lb in _BABA_LABELS:
        print(f"  {lb}: {baba_count[lb]}R")
    print()

    # ── 各セグメント × 馬場 バックテスト ────────────────────────────────────
    segments = [
        ("S-1", "ダート中距離 + 坂あり（福島/東京/中山/中京/阪神）", seg_ids["S1"]),
        ("B-2", "ダート中距離 全場",                               seg_ids["B2"]),
    ]

    for seg_name, seg_desc, s_ids in segments:
        print("=" * 72)
        print(f"  {seg_name}: {seg_desc}")
        print(f"  戦略: {strat}  検証期間: {from_str} 〜 {to_str}")
        print("=" * 72)

        # 全体
        print(f"  [{seg_name}] 全馬場 ({len(s_ids)}R)...")
        overall = _run(strat, period, s_ids)

        # 馬場別
        baba_results: dict[str, dict | None] = {}
        for baba in _BABA_LABELS:
            baba_rids = {rid for rid, lbl in baba_map.items() if lbl == baba}
            filtered  = s_ids & baba_rids
            n_filtered = len(filtered)
            print(f"  [{seg_name}|{baba}] {n_filtered}R...")
            baba_results[baba] = _run(strat, period, filtered) if filtered else None

        # テーブル
        print()
        headers = ["馬場", "R数(filter)", "R数(成立)", "複勝数", "複勝率", "勝率", "ROI(単勝)", "判定"]
        rows: list[tuple] = []
        base_rate = overall["place_rate"] if overall else None

        if overall:
            rows.append(("全体", len(s_ids), overall["races"], overall["place_count"],
                         f"{overall['place_rate']:.1%}", f"{overall['win_rate']:.1%}",
                         f"{overall['roi']:.1%}", _verdict(overall)))
        else:
            rows.append(("全体", len(s_ids), 0, "-", "-", "-", "-", "データなし"))

        for baba in _BABA_LABELS:
            baba_rids = {rid for rid, lbl in baba_map.items() if lbl == baba}
            filtered  = s_ids & baba_rids
            r = baba_results.get(baba)
            if r is None:
                rows.append((baba, len(filtered), 0, "-", "-", "-", "-",
                             f"サンプル不足({len(filtered)}R)" if filtered else "データなし"))
            else:
                rows.append((baba, len(filtered), r["races"], r["place_count"],
                             f"{r['place_rate']:.1%}", f"{r['win_rate']:.1%}",
                             f"{r['roi']:.1%}", _verdict(r, base_rate)))

        _print_table(rows, headers)
        print()

        # 判定サマリー
        print(f">>> {seg_name} 馬場別判定:")
        if overall:
            print(f"  全体基準: 複勝{overall['place_rate']:.1%} / ROI {overall['roi']:.1%}")
        for baba in _BABA_LABELS:
            baba_rids = {rid for rid, lbl in baba_map.items() if lbl == baba}
            filtered  = s_ids & baba_rids
            r = baba_results.get(baba)
            if r is None or r["races"] < _MIN_SAMPLE:
                n = r["races"] if r else len(filtered)
                print(f"  {baba}: サンプル不足({n}R < {_MIN_SAMPLE}) → 判定保留")
                continue
            rate = r["place_rate"]
            roi  = r["roi"]
            diff = rate - (overall["place_rate"] if overall else 0.0)
            sign = "+" if diff >= 0 else ""
            warn = " [良より10pt以上低下]" if base_rate and (base_rate - rate) >= 0.10 else ""
            print(f"  {baba}: 複勝{rate:.1%}({sign}{diff:.1%}) / ROI {roi:.1%} → {_verdict(r, base_rate)}{warn}")
        print()

    print("[seg_baba_bt] 完了。")


if __name__ == "__main__":
    main()
