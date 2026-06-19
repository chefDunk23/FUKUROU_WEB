"""
scripts/patch_grade_jvdata.py
==============================
rich_features Parquet に jyoken_cd_2..5 を keiba_v2.races から補填する。

M0-I.2 カットオーバー前処理:
  grade_code='E'（特別競走）の行は GRADE_VALUE_MAP で固定値を与えるのではなく
  jyoken_cd（条件コード）から正確なクラス数値を導出するため、
  jyoken_cd カラムが基底 Parquet に必要となる。

Usage:
    py -3.13 scripts/patch_grade_jvdata.py
    py -3.13 scripts/patch_grade_jvdata.py \\
        --in  outputs/rich_features_2022plus.parquet \\
        --out outputs/rich_features_jvdata_2022plus.parquet
"""
from __future__ import annotations

import argparse
import io
import logging
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv(_ROOT / ".env")

import pandas as pd
import psycopg2

from shared.config import DB_V2 as DB_KEIBA_V2

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

_DEFAULT_IN  = Path("outputs/rich_features_2022plus.parquet")
_DEFAULT_OUT = Path("outputs/rich_features_jvdata_2022plus.parquet")


def _fetch_jyoken(conn, race_ids: list[str]) -> pd.DataFrame:
    """keiba_v2.races から joken_code_2..5 を取得して DataFrame で返す。"""
    if not race_ids:
        return pd.DataFrame(columns=["race_id", "jyoken_cd_2", "jyoken_cd_3",
                                      "jyoken_cd_4", "jyoken_cd_5"])
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id            AS race_id,
               joken_code_2  AS jyoken_cd_2,
               joken_code_3  AS jyoken_cd_3,
               joken_code_4  AS jyoken_cd_4,
               joken_code_5  AS jyoken_cd_5
        FROM   races
        WHERE  id = ANY(%s)
        """,
        (list(race_ids),),
    )
    rows = cur.fetchall()
    cols = ["race_id", "jyoken_cd_2", "jyoken_cd_3", "jyoken_cd_4", "jyoken_cd_5"]
    return pd.DataFrame(rows, columns=cols)


def run(in_path: Path, out_path: Path) -> None:
    log.info("読み込み中: %s", in_path)
    df = pd.read_parquet(in_path, engine="pyarrow")
    n_orig = len(df)
    log.info("行数: %d, カラム数: %d", n_orig, len(df.columns))

    if all(c in df.columns for c in ("jyoken_cd_2", "jyoken_cd_3", "jyoken_cd_4", "jyoken_cd_5")):
        log.info("jyoken_cd カラムは既に存在します。スキップします。")
        df.to_parquet(out_path, engine="pyarrow", index=False)
        log.info("保存: %s", out_path)
        return

    race_ids = df["race_id"].unique().tolist()
    log.info("ユニーク race_id: %d件。keiba_v2 から joken_code を取得中...", len(race_ids))

    conn = psycopg2.connect(**DB_KEIBA_V2, connect_timeout=10)
    try:
        jy_df = _fetch_jyoken(conn, race_ids)
    finally:
        conn.close()

    log.info("jyoken マッチ件数: %d / %d", len(jy_df), len(race_ids))

    # LEFT JOIN: race_id が races に存在しない行は NaN になる
    df = df.merge(jy_df, on="race_id", how="left")

    # "000" を None に統一して compute_jv_class_score と挙動を合わせる
    for col in ("jyoken_cd_2", "jyoken_cd_3", "jyoken_cd_4", "jyoken_cd_5"):
        df[col] = df[col].where(df[col].ne("000") & df[col].notna(), other=None)

    assert len(df) == n_orig, f"行数が変化しました: {n_orig} → {len(df)}"

    # 統計
    e_mask = df["grade_code"].eq("E")
    resolved = e_mask & df[["jyoken_cd_2", "jyoken_cd_3", "jyoken_cd_4", "jyoken_cd_5"]].notna().any(axis=1)
    log.info(
        "grade_code='E' 行: %d / %d  うち jyoken_cd 解決済み: %d (%.1f%%)",
        e_mask.sum(), n_orig,
        resolved.sum(),
        resolved.sum() / max(e_mask.sum(), 1) * 100,
    )

    log.info("保存: %s", out_path)
    df.to_parquet(out_path, engine="pyarrow", index=False)
    log.info("完了。行数: %d, カラム数: %d", len(df), len(df.columns))


def main() -> None:
    parser = argparse.ArgumentParser(description="Parquet に jyoken_cd カラムを補填")
    parser.add_argument("--in",  dest="in_path",  default=str(_DEFAULT_IN))
    parser.add_argument("--out", dest="out_path", default=str(_DEFAULT_OUT))
    args = parser.parse_args()
    run(Path(args.in_path), Path(args.out_path))


if __name__ == "__main__":
    main()
