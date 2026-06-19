"""
scripts/dry_run_corner_router.py
=================================
コーナー振り分けロジックの裏取り検証（ドライラン）スクリプト。

直近2週末のデータ（2026-05-09/10, 2026-05-16/17）を使い、
corner_router.py の振り分けロジックを全レースに適用して検証レポートを出力する。

Usage:
    py -3.13 scripts/dry_run_corner_router.py
    py -3.13 scripts/dry_run_corner_router.py --dates 2026-05-17
    py -3.13 scripts/dry_run_corner_router.py --parquet outputs/v2_stacked_features.parquet
"""
from __future__ import annotations

import argparse
import io
import logging
import sys
from pathlib import Path

# Windows コンソールの CP932 制約を回避して UTF-8 出力
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import pandas as pd

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.video_generator.corner_router import (
    Corner,
    CornerPick,
    SessionResult,
    build_report,
    route_session,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

_DEFAULT_PARQUET = Path("outputs/v2_stacked_features.parquet")

# 直近2週末（デフォルト対象日）
_DEFAULT_DATES = [
    "2026-05-09",
    "2026-05-10",
    "2026-05-16",
    "2026-05-17",
]

KEIBAJO_LABELS: dict[str, str] = {
    "01": "札幌", "1": "札幌",
    "02": "函館", "2": "函館",
    "03": "福島", "3": "福島",
    "04": "新潟", "4": "新潟",
    "05": "東京", "5": "東京",
    "06": "中山", "6": "中山",
    "07": "中京", "7": "中京",
    "08": "京都", "8": "京都",
    "09": "阪神", "9": "阪神",
    "10": "小倉",
}


def _venue_label(code) -> str:
    s = str(code).strip() if code is not None else ""
    return KEIBAJO_LABELS.get(s, f"会場{s}")


def _attach_results(
    picks: list[CornerPick],
    result_map: dict[tuple[str, str], int],
) -> None:
    """各 CornerPick に実際の着順（actual_rank）と的中フラグ（hit）を付与する。"""
    for p in picks:
        rank = result_map.get((p.race_id, p.horse_id))
        p.actual_rank = rank
        p.hit = (rank == 1) if rank is not None else None


def run_dry_run(parquet_path: Path, dates: list[str]) -> None:
    if not parquet_path.exists():
        log.error("Parquet が見つかりません: %s", parquet_path)
        sys.exit(1)

    log.info("Parquet 読み込み: %s", parquet_path)
    df = pd.read_parquet(parquet_path)
    log.info("  shape=%s", df.shape)

    # race_date を date 文字列に統一
    df["_date_str"] = pd.to_datetime(df["race_date"]).dt.strftime("%Y-%m-%d")

    # フィルタ
    df_filtered = df[df["_date_str"].isin(dates)].copy()
    if df_filtered.empty:
        log.error("指定日付のデータが見つかりません: %s", dates)
        sys.exit(1)

    log.info(
        "  対象日: %s  レース数: %d  馬数: %d",
        ", ".join(sorted(dates)),
        df_filtered["race_id"].nunique(),
        len(df_filtered),
    )

    # 結果マップ: (race_id, horse_id) → kakutei_chakujun
    df_filtered["race_id"]  = df_filtered["race_id"].astype(str)
    df_filtered["horse_id"] = df_filtered["horse_id"].astype(str)
    result_map: dict[tuple[str, str], int] = {}
    for _, row in df_filtered[["race_id", "horse_id", "kakutei_chakujun"]].iterrows():
        rank = row["kakutei_chakujun"]
        if pd.notna(rank):
            result_map[(str(row["race_id"]), str(row["horse_id"]))] = int(rank)

    # ── セッション単位で処理 ──────────────────────────────────────────────────
    # セッション = (race_date, keibajo_code) の組み合わせ
    session_keys = (
        df_filtered[["_date_str", "keibajo_code"]]
        .drop_duplicates()
        .sort_values(["_date_str", "keibajo_code"])
        .itertuples(index=False, name=None)
    )

    # 週末ごとに日付でグループ化するための中間変数
    weekend_map: dict[str, list[str]] = {}  # "2026-05-09/10" → ["2026-05-09", "2026-05-10"]
    sorted_dates = sorted(set(dates))
    # 2日ずつペアリング（土曜/日曜）
    for i in range(0, len(sorted_dates), 2):
        pair = sorted_dates[i : i + 2]
        label = "/".join(d[5:] for d in pair)  # "05-09/10"
        for d in pair:
            weekend_map[d] = label

    all_sessions: list[SessionResult] = []

    for date_str, keibajo_code in session_keys:
        venue = _venue_label(keibajo_code)
        session_label = f"{date_str} {venue}"

        # セッション内データ
        mask = (df_filtered["_date_str"] == date_str) & (
            df_filtered["keibajo_code"].astype(str).str.strip() == str(keibajo_code).strip()
        )
        sess_df = df_filtered[mask].copy()

        if sess_df["race_id"].nunique() < 1:
            continue

        log.info("セッション処理: %s  %dR", session_label, sess_df["race_id"].nunique())

        result = route_session(sess_df, session_label=session_label)

        # 実結果を付与
        _attach_results(result.teppan,  result_map)
        _attach_results(result.spice,   result_map)
        _attach_results(result.danger,  result_map)

        all_sessions.append(result)
        print(build_report(result, include_sakusaku_detail=True))

    # ── 集計レポート ─────────────────────────────────────────────────────────
    _print_aggregate(all_sessions)


def _print_aggregate(sessions: list[SessionResult]) -> None:
    sep = "=" * 60

    total_teppan   = sum(len(s.teppan)  for s in sessions)
    total_spice    = sum(len(s.spice)   for s in sessions)
    total_danger   = sum(len(s.danger)  for s in sessions)
    total_sakusaku = sum(len(s.sakusaku_labels) for s in sessions)
    total_races    = sum(s.total_races  for s in sessions)

    # 的中集計（actual_rank が付いているもののみ）
    def _hits(picks: list[CornerPick]) -> tuple[int, int]:
        evaluated = [p for p in picks if p.actual_rank is not None]
        hits       = sum(1 for p in evaluated if p.hit)
        return hits, len(evaluated)

    # 危険な人気馬は「着外（2着以下）」が正解 → miss_rate で評価
    def _danger_miss(picks: list[CornerPick]) -> tuple[int, int]:
        evaluated = [p for p in picks if p.actual_rank is not None]
        misses    = sum(1 for p in evaluated if not p.hit)  # 1着以外 = 警告成立
        return misses, len(evaluated)

    t_hit,  t_eval  = _hits([p for s in sessions for p in s.teppan])
    sp_hit, sp_eval = _hits([p for s in sessions for p in s.spice])
    d_miss, d_eval  = _danger_miss([p for s in sessions for p in s.danger])

    def pct(n: int, d: int) -> str:
        return f"{n/d*100:.1f}%" if d else "—"

    print(sep)
    print("  ドライラン集計レポート")
    print(sep)
    print(f"  対象セッション数: {len(sessions)}")
    print(f"  総レース数      : {total_races}")
    print()
    print(f"  ■ 鉄板枠      選出={total_teppan:<3}  "
          f"的中={t_hit}/{t_eval}  勝率={pct(t_hit, t_eval)}")
    print(f"  ■ スパイス枠  選出={total_spice:<3}  "
          f"的中={sp_hit}/{sp_eval}  勝率={pct(sp_hit, sp_eval)}")
    print(f"  ■ 危険な人気馬 検出={total_danger:<3}  "
          f"警告成立(着外)={d_miss}/{d_eval}  危険率={pct(d_miss, d_eval)}")
    print(f"  ■ サクサク枠  {total_sakusaku}レース")
    print()

    # 鉄板の詳細リスト
    teppan_all = [p for s in sessions for p in s.teppan]
    if teppan_all:
        print("  鉄板枠 詳細:")
        print(f"  {'レース':<30} {'馬番':>4} {'着順':>4} {'ability_z':>9} {'gap':>6}")
        print("  " + "-" * 56)
        for p in teppan_all:
            rank_str = str(p.actual_rank) if p.actual_rank is not None else "?"
            hit_mark = " ✓" if p.hit else "  "
            surf = "ダ" if p.is_dirt else "芝"
            print(
                f"  {hit_mark} {p.race_label[:28]:<28} "
                f"{p.umaban:>4} {rank_str:>4}  "
                f"{surf} abilZ={p.ability_z:+.2f}  gap={p.ability_gap:.2f}"
            )
        print()

    # スパイスの詳細リスト
    spice_all = [p for s in sessions for p in s.spice]
    if spice_all:
        print("  スパイス枠 詳細:")
        print(f"  {'レース':<30} {'馬番':>4} {'ab2r':>4} {'着順':>4} {'pace_z':>7} {'ped_z':>7}")
        print("  " + "-" * 60)
        for p in spice_all:
            rank_str = str(p.actual_rank) if p.actual_rank is not None else "?"
            hit_mark = " ✓" if p.hit else "  "
            ab2r = p.ability_v2_rank if p.ability_v2_rank > 0 else "-"
            print(
                f"  {hit_mark} {p.race_label[:28]:<28} "
                f"{p.umaban:>4} {str(ab2r):>4} {rank_str:>4}  "
                f"paceZ={p.pace_z:+.2f}  pedZ={p.pedigree_z:+.2f}"
            )
        print()

    # 危険な人気馬の詳細リスト
    danger_all = [p for s in sessions for p in s.danger]
    if danger_all:
        print("  危険な人気馬 詳細: (着外=警告成立)")
        print(f"  {'レース':<30} {'馬番':>4} {'着順':>4} {'abilZ':>7} {'courseZ':>8} {'paceZ':>7}")
        print("  " + "-" * 64)
        for p in danger_all:
            rank_str = str(p.actual_rank) if p.actual_rank is not None else "?"
            warn_mark = " !" if (p.actual_rank is not None and not p.hit) else ("  " if p.hit is None else " 1")
            print(
                f"  {warn_mark} {p.race_label[:28]:<28} "
                f"{p.umaban:>4} {rank_str:>4}  "
                f"abilZ={p.ability_z:+.2f}  crsZ={p.course_z:+.2f}  paceZ={p.pace_z:+.2f}"
            )
        print()

    print(sep)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="コーナー振り分けロジックのドライラン検証"
    )
    p.add_argument(
        "--parquet", type=Path, default=_DEFAULT_PARQUET,
        help=f"対象 Parquet（デフォルト: {_DEFAULT_PARQUET}）",
    )
    p.add_argument(
        "--dates", nargs="+", default=_DEFAULT_DATES,
        metavar="YYYY-MM-DD",
        help="対象日（スペース区切りで複数指定可）",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run_dry_run(args.parquet, args.dates)
