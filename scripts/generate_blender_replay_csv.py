"""
scripts/generate_blender_replay_csv.py
========================================
Blender 3Dリプレイ動画用のキーフレームCSVを生成する。

JRA-VAN DB から指定レースの結果データを取得し、各馬の
「スタート → 各コーナー通過 → ゴール」のキーフレームを計算して CSV で出力する。

出力フォーマット (4カラム):
    frame       : フレーム数 (30fps デフォルト)
    horse_name  : 馬名または "馬番X"
    progress    : コース進捗度 (スタート=0.0, ゴール=1.0)
    x_offset    : 横位置 [m] (最内=0.0 付近, 外回りほど大きい)

出力先:
    outputs/blender_csv/{race_id}_replay.csv

Usage:
    # 直近の安田記念 (仮 race_id)
    py -3.13 scripts/generate_blender_replay_csv.py --race_id 202506010811

    # fps 指定
    py -3.13 scripts/generate_blender_replay_csv.py --race_id 202506010811 --fps 24

    # 出力ディレクトリ変更
    py -3.13 scripts/generate_blender_replay_csv.py --race_id 202506010811 --out data/output/blender_csv

    # 直近 G1 レースを自動検索して生成
    py -3.13 scripts/generate_blender_replay_csv.py --latest_g1

Blender での使い方:
    生成した CSV を Blender Python スクリプトで読み込む:
        import csv
        with open('202506010811_replay.csv') as f:
            reader = csv.DictReader(f)
            for row in reader:
                frame    = int(row['frame'])
                name     = row['horse_name']
                progress = float(row['progress'])
                x_offset = float(row['x_offset'])
                # → 各馬オブジェクトにキーフレームを挿入
"""
from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
from pathlib import Path

import psycopg2.extras

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.db.jvdata import get_conn as get_v2_conn
from shared.db.jvdl import get_conn as get_jvdl_conn
from src.features.blender_replay import HorseEntry, KeyFrame, build_keyframes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

_DEFAULT_OUT_DIR = Path("outputs/blender_csv")
_DEFAULT_FPS = 30

# ── SQL ───────────────────────────────────────────────────────────────────────

_SQL_RACE_INFO = """
SELECT
    r.id                AS race_id,
    r.race_date::text   AS race_date,
    r.race_name_hondai  AS race_name,
    r.race_num,
    r.keibajo_code,
    r.distance,
    r.track_code,
    r.grade_code,
    r.syusso_tosu
FROM races r
WHERE r.id = %s
"""

_SQL_ENTRIES = """
SELECT
    e.horse_id,
    e.umaban,
    e.wakuban,
    e.kakutei_chakujun      AS final_rank,
    e.race_time,
    e.go_3f_time,
    e.time_diff,
    COALESCE(e.corner_1, 0) AS corner_1,
    COALESCE(e.corner_2, 0) AS corner_2,
    COALESCE(e.corner_3, 0) AS corner_3,
    COALESCE(e.corner_4, 0) AS corner_4
FROM race_entries e
WHERE e.race_id = %s
  AND e.kakutei_chakujun IS NOT NULL
  AND e.kakutei_chakujun > 0
ORDER BY e.kakutei_chakujun ASC
"""

_SQL_HORSE_NAMES = """
SELECT id, name FROM horses WHERE id = ANY(%s)
"""

# 直近 G1 レース検索
_SQL_LATEST_G1 = """
SELECT r.id AS race_id, r.race_date::text, r.race_name_hondai, r.distance
FROM races r
JOIN race_entries e ON e.race_id = r.id
WHERE r.grade_code IN ('A', 'G', 'A01')
  AND e.kakutei_chakujun IS NOT NULL
  AND e.kakutei_chakujun > 0
  AND r.race_date <= CURRENT_DATE
GROUP BY r.id, r.race_date, r.race_name_hondai, r.distance
HAVING COUNT(e.horse_id) >= 6
ORDER BY r.race_date DESC, r.id DESC
LIMIT 5
"""


# ── パース・変換ユーティリティ ────────────────────────────────────────────────

def _parse_time_diff(raw) -> float:
    """
    time_diff 文字列 → 着差 [秒]。
    '+15' → 1.5s, '0' / NULL / 解析不能 → 0.0s (勝ち馬扱い)
    """
    if raw is None:
        return 0.0
    s = str(raw).strip()
    m = re.match(r'^[+-]?(\d+)$', s)
    if not m:
        return 0.0
    return max(0.0, int(m.group(1)) / 10.0)


def _safe_float(v) -> float | None:
    """None / NaN / 0 → None, それ以外は float。"""
    if v is None:
        return None
    try:
        f = float(v)
        return f if f > 0 and f == f else None  # NaN check: f == f is False for NaN
    except (TypeError, ValueError):
        return None


def _safe_int(v, default: int = 0) -> int:
    try:
        return int(v) if v is not None else default
    except (TypeError, ValueError):
        return default


# ── DB アクセス ───────────────────────────────────────────────────────────────

def _fetch_race_info(race_id: str) -> dict | None:
    with get_v2_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(_SQL_RACE_INFO, (race_id,))
            row = cur.fetchone()
    return dict(row) if row else None


def _fetch_entries(race_id: str) -> list[dict]:
    with get_v2_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(_SQL_ENTRIES, (race_id,))
            rows = cur.fetchall()
    return [dict(r) for r in rows]


def _fetch_horse_names(horse_ids: list[str]) -> dict[str, str]:
    """horse_id → 馬名 のマップを返す。jvdl DB の horses テーブルを参照。"""
    if not horse_ids:
        return {}
    try:
        with get_jvdl_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(_SQL_HORSE_NAMES, (horse_ids,))
                rows = cur.fetchall()
        return {str(r["id"]): str(r["name"]) for r in rows if r.get("name")}
    except Exception as exc:
        log.warning("馬名取得失敗 (フォールバック: 馬番表示): %s", exc)
        return {}


def _fetch_latest_g1_race_ids() -> list[dict]:
    with get_v2_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(_SQL_LATEST_G1)
            rows = cur.fetchall()
    return [dict(r) for r in rows]


# ── メイン生成ロジック ────────────────────────────────────────────────────────

def generate_replay_csv(
    race_id: str,
    out_dir: Path = _DEFAULT_OUT_DIR,
    fps: int = _DEFAULT_FPS,
) -> Path:
    """
    指定 race_id の Blender 用キーフレーム CSV を生成して保存する。

    Returns
    -------
    Path : 出力ファイルパス
    """
    log.info("=== Blender リプレイ CSV 生成開始: %s ===", race_id)

    # ── レース情報取得 ────────────────────────────────────────────────────
    race_info = _fetch_race_info(race_id)
    if not race_info:
        raise ValueError(f"レース情報が見つかりません: {race_id}")

    distance = _safe_int(race_info.get("distance"), 2000)
    race_name = str(race_info.get("race_name") or "").strip() or race_id
    race_date = str(race_info.get("race_date") or "")
    log.info(
        "レース: %s %s  距離: %dm  %s",
        race_date, race_name, distance, race_info.get("keibajo_code", ""),
    )

    # ── 出走馬データ取得 ──────────────────────────────────────────────────
    raw_entries = _fetch_entries(race_id)
    if not raw_entries:
        raise ValueError(f"出走馬データが見つかりません（レース未確定の可能性）: {race_id}")

    log.info("出走馬 %d 頭のデータ取得完了", len(raw_entries))

    # ── 馬名取得 ─────────────────────────────────────────────────────────
    horse_ids = [str(r["horse_id"]) for r in raw_entries if r.get("horse_id")]
    name_map = _fetch_horse_names(horse_ids)
    log.info("馬名取得: %d / %d 頭", len(name_map), len(horse_ids))

    # ── HorseEntry リスト構築 ─────────────────────────────────────────────
    entries: list[HorseEntry] = []
    for row in raw_entries:
        hid    = str(row.get("horse_id") or "")
        umaban = _safe_int(row.get("umaban"), 0)
        wakuban = _safe_int(row.get("wakuban"), 0) or None

        # 馬名: horses テーブル → "馬番X" フォールバック
        name = name_map.get(hid) or f"馬番{umaban}"

        corners: dict[int, int] = {}
        for cn in (1, 2, 3, 4):
            rank = _safe_int(row.get(f"corner_{cn}"), 0)
            if rank > 0:
                corners[cn] = rank

        entries.append(HorseEntry(
            horse_id       = hid,
            horse_name     = name,
            umaban         = umaban,
            wakuban        = wakuban,
            final_rank     = _safe_int(row.get("final_rank"), 99),
            race_time      = _safe_float(row.get("race_time")),
            go_3f_time     = _safe_float(row.get("go_3f_time")),
            time_diff_secs = _parse_time_diff(row.get("time_diff")),
            corners        = corners,
        ))

    # ── キーフレーム計算 ──────────────────────────────────────────────────
    keyframes = build_keyframes(entries, distance, fps=fps)
    log.info("キーフレーム数: %d (全馬合計)", len(keyframes))

    # ── 統計サマリー出力 ──────────────────────────────────────────────────
    winner_entry = min(entries, key=lambda e: e.final_rank)
    winner_time = winner_entry.race_time or 0.0
    total_frames = round(winner_time * fps)
    log.info("勝ち馬: %s  走破タイム: %.1fs  総フレーム数: %d (%.1fs @ %dfps)",
             winner_entry.horse_name, winner_time, total_frames, total_frames / fps, fps)

    # ── CSV 書き出し ──────────────────────────────────────────────────────
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{race_id}_replay.csv"

    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["frame", "horse_name", "progress", "x_offset"])
        for kf in keyframes:
            writer.writerow([kf.frame, kf.horse_name, kf.progress, kf.x_offset])

    row_count = len(keyframes)
    log.info("CSV 出力完了: %s  (%d 行)", out_path, row_count)

    # ── 確認用サマリー表示 ─────────────────────────────────────────────────
    _print_summary(keyframes, entries, winner_time, fps)

    return out_path


def _print_summary(
    keyframes: list[KeyFrame],
    entries: list[HorseEntry],
    winner_time: float,
    fps: int,
) -> None:
    """コンソールにキーフレームサマリーを表示する（デバッグ用）。"""
    print()
    print("=== キーフレームサマリー (先着5頭) ===")
    top5 = sorted(entries, key=lambda e: e.final_rank)[:5]
    top5_names = {e.horse_name for e in top5}

    label_order = {"start": 0, "c1": 1, "c2": 2, "c3": 3, "c4": 4, "finish": 5}
    for name in [e.horse_name for e in top5]:
        horse_kfs = sorted(
            [kf for kf in keyframes if kf.horse_name == name],
            key=lambda k: label_order.get(k.label, 9),
        )
        print(f"\n  【{name}】")
        print(f"  {'label':<8}  {'frame':>6}  {'time':>6}  {'progress':>9}  {'x_offset':>9}")
        for kf in horse_kfs:
            t = kf.frame / fps
            print(f"  {kf.label:<8}  {kf.frame:>6}  {t:>5.1f}s  {kf.progress:>9.4f}  {kf.x_offset:>9.2f}m")
    print()


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Blender 3Dリプレイ動画用キーフレーム CSV 生成",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用例:
  py -3.13 scripts/generate_blender_replay_csv.py --race_id 202506010811
  py -3.13 scripts/generate_blender_replay_csv.py --latest_g1
  py -3.13 scripts/generate_blender_replay_csv.py --race_id 202506010811 --fps 24
        """,
    )
    grp = p.add_mutually_exclusive_group(required=True)
    grp.add_argument("--race_id", type=str, help="レースID (例: 202506010811)")
    grp.add_argument(
        "--latest_g1",
        action="store_true",
        help="直近のG1レースを自動検索して生成 (候補を表示して選択)",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=_DEFAULT_OUT_DIR,
        help=f"出力ディレクトリ (デフォルト: {_DEFAULT_OUT_DIR})",
    )
    p.add_argument(
        "--fps",
        type=int,
        default=_DEFAULT_FPS,
        help=f"フレームレート (デフォルト: {_DEFAULT_FPS})",
    )
    return p.parse_args()


def _select_latest_g1() -> str | None:
    """直近G1候補を表示してユーザーに選択させる。"""
    candidates = _fetch_latest_g1_race_ids()
    if not candidates:
        log.error("G1レースデータが見つかりません")
        return None

    print("\n直近のG1レース候補:")
    for i, r in enumerate(candidates, 1):
        print(f"  {i}. [{r['race_id']}]  {r['race_date']}  {r['race_name_hondai']}  {r['distance']}m")

    try:
        choice = input("\n番号を選択してください (Enter でキャンセル): ").strip()
        if not choice:
            return None
        idx = int(choice) - 1
        if 0 <= idx < len(candidates):
            return str(candidates[idx]["race_id"])
    except (ValueError, KeyboardInterrupt):
        pass
    return None


def main() -> None:
    args = _parse_args()

    if args.latest_g1:
        race_id = _select_latest_g1()
        if not race_id:
            log.info("キャンセルされました")
            return
    else:
        race_id = args.race_id

    try:
        out_path = generate_replay_csv(
            race_id=race_id,
            out_dir=args.out,
            fps=args.fps,
        )
        print(f"\n出力: {out_path}")
    except ValueError as e:
        log.error("%s", e)
        sys.exit(1)
    except Exception as e:
        log.exception("予期しないエラー: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
