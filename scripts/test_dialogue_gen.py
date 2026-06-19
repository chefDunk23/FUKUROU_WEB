"""
scripts/test_dialogue_gen.py
==============================
掛け合い台本生成テスト。2026-05-17 京都のデータで Claude API を呼び出し、
生成された dialogue JSON を出力する。

Usage:
    py -3.13 scripts/test_dialogue_gen.py
    py -3.13 scripts/test_dialogue_gen.py --date 2026-05-17 --keibajo 08
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import sys
from pathlib import Path

# Windows UTF-8 出力
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd

from src.video_generator.corner_router import route_session
from src.video_generator.prompt_builder import build_script_prompt
from src.video_generator.script_generator import generate_dialogue

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

_DEFAULT_PARQUET  = Path("outputs/v2_stacked_features.parquet")
_DEFAULT_DATE     = "2026-05-17"
_DEFAULT_KEIBAJO  = "08"   # 京都


def run(parquet_path: Path, date: str, keibajo: str) -> None:
    if not parquet_path.exists():
        log.error("Parquet が見つかりません: %s", parquet_path)
        sys.exit(1)

    log.info("Parquet 読み込み: %s", parquet_path)
    df = pd.read_parquet(parquet_path)
    df["_date_str"] = pd.to_datetime(df["race_date"]).dt.strftime("%Y-%m-%d")
    df["race_id"]   = df["race_id"].astype(str)
    df["horse_id"]  = df["horse_id"].astype(str)

    mask = (df["_date_str"] == date) & (
        df["keibajo_code"].astype(str).str.strip() == keibajo.strip()
    )
    sess_df = df[mask].copy()

    if sess_df.empty:
        log.error("データが見つかりません: date=%s keibajo=%s", date, keibajo)
        sys.exit(1)

    from src.video_generator.corner_router import KEIBAJO_LABELS
    venue = KEIBAJO_LABELS.get(keibajo, f"会場{keibajo}")
    session_label = f"{date} {venue}"
    log.info("セッション: %s  %dR", session_label, sess_df["race_id"].nunique())

    # ── コーナー振り分け ────────────────────────────────────────────────────
    result = route_session(sess_df, session_label=session_label)

    log.info(
        "振り分け結果: 鉄板=%d  スパイス=%d  危険=%d  サクサク=%d",
        len(result.teppan), len(result.spice), len(result.danger),
        len(result.sakusaku_labels),
    )

    # ── プロンプト構築 ──────────────────────────────────────────────────────
    sp = build_script_prompt(result)

    print("\n" + "=" * 60)
    print(f"  テンプレート: {sp.template}")
    print(f"  ユーザーメッセージ（先頭500文字）:")
    print("  " + sp.prompt[:500].replace("\n", "\n  "))
    print("=" * 60 + "\n")

    # ── Claude API 呼び出し ─────────────────────────────────────────────────
    log.info("Claude API 呼び出し中...")
    data = generate_dialogue(sp)

    # ── 結果出力 ────────────────────────────────────────────────────────────
    dialogue = data.get("dialogue", [])
    print("\n" + "=" * 60)
    print(f"  掛け合い台本 — {session_label}  ({sp.template}型)")
    print(f"  生成ターン数: {len(dialogue)}")
    print("=" * 60 + "\n")

    for i, turn in enumerate(dialogue, 1):
        speaker = turn.get("speaker", "?")
        text    = turn.get("text", "")
        indent  = "  【博士】" if speaker == "フクロウ博士" else "  〔助手〕"
        print(f"{i:>3}  {indent}  {text}")

    print("\n" + "=" * 60)
    print("  JSON 全体出力:")
    print("=" * 60)
    print(json.dumps(data, ensure_ascii=False, indent=2))

    # ── ファイル保存 ────────────────────────────────────────────────────────
    out_dir  = _ROOT / "data" / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    fname    = f"dialogue_{date}_{venue}.json"
    out_path = out_dir / fname
    out_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log.info("保存: %s", out_path)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="掛け合い台本生成テスト")
    p.add_argument("--parquet",  type=Path,  default=_DEFAULT_PARQUET)
    p.add_argument("--date",     type=str,   default=_DEFAULT_DATE)
    p.add_argument("--keibajo",  type=str,   default=_DEFAULT_KEIBAJO)
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run(args.parquet, args.date, args.keibajo)
