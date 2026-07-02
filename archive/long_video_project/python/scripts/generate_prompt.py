"""
scripts/generate_prompt.py
===========================
Phase 1 — LLM 向けプロンプトテキスト生成（API 呼び出しなし）。

予測スコア Parquet/CSV を読み込み、コーナー振り分け → 台本構成指示を結合した
テキストファイルを生成する。人間がこれを LLM チャットにコピペして台本 JSON を取得する。

Usage:
    py -3.13 scripts/generate_prompt.py --date 2026-05-17 --venue 08
    py -3.13 scripts/generate_prompt.py --date 2026-05-17 --venue 08 --parquet outputs/v2_stacked_features.parquet
    py -3.13 scripts/generate_prompt.py --date 2026-05-17  # 指定日の全会場を出力
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import pandas as pd

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.video_generator.corner_router import KEIBAJO_LABELS, route_session
from src.video_generator.prompt_builder import build_script_prompt

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

_DEFAULT_PARQUET = Path("outputs/v2_stacked_features.parquet")
_DEFAULT_OUTPUT  = Path("data/output/prompt_for_llm.txt")

# JRA 場コードから読みへの逆引き（ファイル名用・マルチバイト文字排除）
_KEIBAJO_ROMAJI: dict[str, str] = {
    "01": "sapporo",  "1": "sapporo",
    "02": "hakodate", "2": "hakodate",
    "03": "fukushima","3": "fukushima",
    "04": "niigata",  "4": "niigata",
    "05": "tokyo",    "5": "tokyo",
    "06": "nakayama", "6": "nakayama",
    "07": "chukyo",   "7": "chukyo",
    "08": "kyoto",    "8": "kyoto",
    "09": "hanshin",  "9": "hanshin",
    "10": "kokura",
}


def _venue_label(code: str) -> str:
    s = str(code).strip()
    return KEIBAJO_LABELS.get(s, f"会場{s}")


def _venue_romaji(code: str) -> str:
    s = str(code).strip()
    return _KEIBAJO_ROMAJI.get(s, f"venue{s}")


def _load_df(parquet_path: Path) -> pd.DataFrame:
    suffix = parquet_path.suffix.lower()
    if suffix in (".csv", ".tsv"):
        return pd.read_csv(parquet_path, low_memory=False)
    return pd.read_parquet(parquet_path)


def _filter_session(
    df: pd.DataFrame,
    date_str: str,
    keibajo_code: str | None,
) -> pd.DataFrame:
    """指定日付 & 会場でフィルタリングして返す。"""
    df = df.copy()
    df["_date_str"] = pd.to_datetime(df["race_date"]).dt.strftime("%Y-%m-%d")
    mask = df["_date_str"] == date_str
    if keibajo_code:
        mask &= df["keibajo_code"].astype(str).str.strip() == keibajo_code.strip()
    return df[mask].copy()


def generate_prompt_for_session(
    df: pd.DataFrame,
    date_str: str,
    keibajo_code: str,
    output_path: Path,
) -> Path:
    """
    1 セッション分のプロンプトテキストを output_path に保存する。

    Returns
    -------
    Path — 実際に書き込んだファイルパス
    """
    venue      = _venue_label(keibajo_code)
    venue_rom  = _venue_romaji(keibajo_code)
    session_label = f"{date_str} {venue}"
    log.info("セッション処理: %s  %dR", session_label, df["race_id"].nunique())

    result = route_session(df, session_label=session_label)
    sp     = build_script_prompt(result)

    # テンプレート情報サマリー
    summary_lines = [
        f"セッション     : {session_label}",
        f"テンプレート   : {sp.template}（{'重賞特化型' if sp.template == 'A' else '平場パック型'}）",
        f"鉄板枠         : {sp.n_teppan} 頭",
        f"スパイス枠     : {sp.n_spice} 頭",
        f"危険な人気馬   : {sp.n_danger} 頭",
        f"総レース数     : {result.total_races} R",
    ]

    separator = "=" * 72
    body = "\n".join([
        separator,
        "【SYSTEM PROMPT — キャラクター定義 & JSON スキーマ】",
        "（以下をシステムプロンプト欄にセットしてください）",
        separator,
        sp.system_prompt,
        "",
        separator,
        "【USER MESSAGE — セッションデータ & 台本構成指示】",
        "（以下をユーザーメッセージ欄にコピペしてください）",
        separator,
        sp.prompt,
        "",
        separator,
        "【生成メタ情報】",
        *[f"  {line}" for line in summary_lines],
        "",
        "出力された JSON を data/input/draft_video_data.json として保存してください。",
        separator,
    ])

    # ファイル名に日本語が混入しないよう日付+ローマ字で命名
    date_compact = date_str.replace("-", "")
    if output_path == _DEFAULT_OUTPUT:
        output_path = _DEFAULT_OUTPUT.parent / f"prompt_{date_compact}_{venue_rom}.txt"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(body, encoding="utf-8")
    log.info("プロンプト保存: %s", output_path)
    return output_path


def run(
    parquet_path: Path,
    date_str: str,
    keibajo_code: str | None,
    output_path: Path,
) -> None:
    if not parquet_path.exists():
        log.error(
            "スコアファイルが見つかりません: %s\n"
            "  先に make scores（または train_v2_submodels.py + merge_v2_submodel_scores.py）を実行してください。",
            parquet_path,
        )
        sys.exit(1)

    log.info("スコアファイル読み込み: %s", parquet_path)
    df_all = _load_df(parquet_path)
    log.info("  shape=%s", df_all.shape)

    df_filtered = _filter_session(df_all, date_str, keibajo_code)
    if df_filtered.empty:
        log.error(
            "指定条件のデータが見つかりません: date=%s  venue=%s",
            date_str, keibajo_code or "(全会場)",
        )
        sys.exit(1)

    # 会場ごとに分けて処理
    venues = (
        df_filtered["keibajo_code"]
        .astype(str)
        .str.strip()
        .unique()
        .tolist()
    )
    venues.sort()

    saved: list[Path] = []
    for code in venues:
        mask = df_filtered["keibajo_code"].astype(str).str.strip() == code
        sess_df = df_filtered[mask].copy()
        saved_path = generate_prompt_for_session(sess_df, date_str, code, output_path)
        saved.append(saved_path)

    print("\n" + "=" * 60)
    print("  Phase 1 完了 — LLM プロンプト生成")
    print("=" * 60)
    for p in saved:
        print(f"  -> {p}")
    print()
    print("  次のステップ:")
    print("    1. 上記ファイルの内容を ChatGPT / Claude Web チャットにコピペ")
    print("    2. 出力された JSON を data/input/draft_video_data.json として保存")
    print("    3. make render を実行して MP4 を書き出す")
    print("=" * 60 + "\n")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Phase 1: LLM プロンプトテキスト生成（API 呼び出しなし）"
    )
    p.add_argument(
        "--date", "-d", required=True, metavar="YYYY-MM-DD",
        help="対象日（例: 2026-05-17）",
    )
    p.add_argument(
        "--venue", "-v", default=None, metavar="CODE",
        help="JRA 場コード（例: 08=京都、09=阪神）。省略時は対象日の全会場を処理",
    )
    p.add_argument(
        "--parquet", "-p", type=Path, default=_DEFAULT_PARQUET,
        help=f"スコア Parquet/CSV のパス（デフォルト: {_DEFAULT_PARQUET}）",
    )
    p.add_argument(
        "--output", "-o", type=Path, default=_DEFAULT_OUTPUT,
        help=f"出力先テキストファイル（デフォルト: 日付+会場名で自動命名）",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run(
        parquet_path=args.parquet,
        date_str=args.date,
        keibajo_code=args.venue,
        output_path=args.output,
    )
