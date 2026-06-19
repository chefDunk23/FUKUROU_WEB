"""
scripts/import_bloodline_masters.py
=====================================
HN（繁殖馬マスタ）と BT（系統情報）を JVDL RAW ファイルから読み取り、
PostgreSQL（fukurou_jvdl）に以下のテーブルを構築して格納する。

    hanshoku_ma_master : breed_id(PK), sire_breed_id, dam_breed_id,
                         uma_mei, uma_mei_kana, birth_year, sex_cd
    lineage_info       : (breed_id, line_code) PK, line_name

BT レコードのバイトレイアウト（実 JRA-VAN 仕様に準拠）:
    既存 specs.py の "dam_sire_sys:(26,4)" は誤記で、実際には
    sire_sys_name (36 bytes) が bytes 26-61 を占める。
    dam_sire_sys の正しい開始位置は byte 62。

Usage:
    py -3.13 scripts/import_bloodline_masters.py --data-dir C:/path/to/jvdl_raw
    py -3.13 scripts/import_bloodline_masters.py --data-dir C:/path/to/jvdl_raw --truncate
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import psycopg2
import psycopg2.extras

from shared.db.jvdl import get_conn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

_BATCH_SIZE = 5_000

# ── DDL ──────────────────────────────────────────────────────────────────────

_DDL_HANSHOKU = """
CREATE TABLE IF NOT EXISTS hanshoku_ma_master (
    breed_id      CHAR(10)  PRIMARY KEY,
    sire_breed_id CHAR(10),
    dam_breed_id  CHAR(10),
    uma_mei       TEXT,
    uma_mei_kana  TEXT,
    birth_year    SMALLINT,
    sex_cd        CHAR(1)
);
"""

_DDL_LINEAGE = """
CREATE TABLE IF NOT EXISTS lineage_info (
    breed_id   CHAR(10) NOT NULL,
    line_code  CHAR(4)  NOT NULL,
    line_name  TEXT,
    PRIMARY KEY (breed_id, line_code)
);
CREATE INDEX IF NOT EXISTS idx_lineage_line_code ON lineage_info (line_code);
"""

# ── バイトフィールド定義（1-based） ───────────────────────────────────────────

# HN (繁殖馬マスタ) — 全フィールドは CP932 固定長バイト配列
_HN_FIELDS: dict[str, tuple[int, int]] = {
    "blood_no":   (12, 10),   # 繁殖登録番号
    "name":       (22, 36),   # 馬名（全角18文字）
    "name_kana":  (58, 36),   # 馬名カナ（全角18文字）
    "sex_cd":     (154,  1),  # 性別コード
    "birth_year": (155,  4),  # 生年 YYYY
    "sire_code":  (183, 10),  # 父繁殖登録番号
    "dam_code":   (230, 10),  # 母繁殖登録番号
}

# BT (系統情報) — code と name が隣接するペア構造
# 注: specs.py の dam_sire_sys:(26,4) は byte 26 から始まる sire_sys_name の先頭 4 bytes
#     を誤って読んでいた。正しい dam_sire_sys 開始位置は byte 62。
_BT_FIELDS: dict[str, tuple[int, int]] = {
    "blood_no":           (12, 10),  # 繁殖登録番号
    "sire_sys":           (22,  4),  # 父系統コード
    "sire_sys_name":      (26, 36),  # 父系統名（全角18文字）
    "dam_sire_sys":       (62,  4),  # 母父系統コード
    "dam_sire_sys_name":  (66, 36),  # 母父系統名（全角18文字）
}

_NULL_ID = "0000000000"
_NULL_SYS = "0000"


# ── パーサー ─────────────────────────────────────────────────────────────────

def _extract(raw: bytes, start_1: int, length: int) -> str:
    """1-based バイト位置から CP932 フィールドを抽出し空白・NUL を除去する。"""
    chunk = raw[start_1 - 1: start_1 - 1 + length]
    try:
        decoded = chunk.decode("cp932", errors="replace")
    except Exception:
        decoded = ""
    return decoded.replace("\x00", "").strip(" 　")


def _nonempty(v: str) -> str | None:
    return v if v and v not in (_NULL_ID, _NULL_SYS, "0" * len(v)) else None


def _parse_hn(line: bytes) -> dict | None:
    if line[:2] != b"HN":
        return None
    breed_id = _extract(line, *_HN_FIELDS["blood_no"])
    if not breed_id or breed_id == _NULL_ID:
        return None
    birth_raw = _extract(line, *_HN_FIELDS["birth_year"])
    return {
        "breed_id":      breed_id,
        "sire_breed_id": _nonempty(_extract(line, *_HN_FIELDS["sire_code"])),
        "dam_breed_id":  _nonempty(_extract(line, *_HN_FIELDS["dam_code"])),
        "uma_mei":       _nonempty(_extract(line, *_HN_FIELDS["name"])),
        "uma_mei_kana":  _nonempty(_extract(line, *_HN_FIELDS["name_kana"])),
        "birth_year":    int(birth_raw) if birth_raw.isdigit() else None,
        "sex_cd":        _nonempty(_extract(line, *_HN_FIELDS["sex_cd"])),
    }


def _parse_bt(line: bytes) -> list[dict]:
    """BT レコード 1 行から lineage_info 用の dict を最大 2 件返す。"""
    if line[:2] != b"BT":
        return []
    breed_id = _extract(line, *_BT_FIELDS["blood_no"])
    if not breed_id or breed_id == _NULL_ID:
        return []

    rows: list[dict] = []
    for code_key, name_key in (
        ("sire_sys",     "sire_sys_name"),
        ("dam_sire_sys", "dam_sire_sys_name"),
    ):
        code = _extract(line, *_BT_FIELDS[code_key])
        if not code or code == _NULL_SYS:
            continue
        name_raw = _extract(line, *_BT_FIELDS[name_key])
        rows.append({
            "breed_id":  breed_id,
            "line_code": code,
            "line_name": name_raw or None,
        })
    return rows


# ── DB 操作 ──────────────────────────────────────────────────────────────────

def _ensure_tables(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(_DDL_HANSHOKU)
        cur.execute(_DDL_LINEAGE)
    conn.commit()
    log.info("テーブル確認/作成完了")


def _flush_hanshoku(cur, batch: list[dict]) -> None:
    if not batch:
        return
    # Deduplicate within the batch — keep last occurrence per breed_id
    deduped: dict[str, dict] = {}
    for r in batch:
        deduped[r["breed_id"]] = r
    psycopg2.extras.execute_values(
        cur,
        """
        INSERT INTO hanshoku_ma_master
            (breed_id, sire_breed_id, dam_breed_id,
             uma_mei, uma_mei_kana, birth_year, sex_cd)
        VALUES %s
        ON CONFLICT (breed_id) DO UPDATE SET
            sire_breed_id = COALESCE(EXCLUDED.sire_breed_id, hanshoku_ma_master.sire_breed_id),
            dam_breed_id  = COALESCE(EXCLUDED.dam_breed_id,  hanshoku_ma_master.dam_breed_id),
            uma_mei       = COALESCE(EXCLUDED.uma_mei,       hanshoku_ma_master.uma_mei),
            uma_mei_kana  = COALESCE(EXCLUDED.uma_mei_kana,  hanshoku_ma_master.uma_mei_kana),
            birth_year    = COALESCE(EXCLUDED.birth_year,    hanshoku_ma_master.birth_year),
            sex_cd        = COALESCE(EXCLUDED.sex_cd,        hanshoku_ma_master.sex_cd)
        """,
        [
            (r["breed_id"], r["sire_breed_id"], r["dam_breed_id"],
             r["uma_mei"], r["uma_mei_kana"], r["birth_year"], r["sex_cd"])
            for r in deduped.values()
        ],
    )


def _flush_lineage(cur, batch: list[dict]) -> None:
    if not batch:
        return
    # Deduplicate within the batch by (breed_id, line_code)
    deduped: dict[tuple, dict] = {}
    for r in batch:
        deduped[(r["breed_id"], r["line_code"])] = r
    psycopg2.extras.execute_values(
        cur,
        """
        INSERT INTO lineage_info (breed_id, line_code, line_name)
        VALUES %s
        ON CONFLICT (breed_id, line_code) DO UPDATE SET
            line_name = COALESCE(EXCLUDED.line_name, lineage_info.line_name)
        """,
        [(r["breed_id"], r["line_code"], r["line_name"]) for r in deduped.values()],
    )


def _commit_batch(conn, cur, hn_batch: list[dict], bt_batch: list[dict]) -> None:
    _flush_hanshoku(cur, hn_batch)
    _flush_lineage(cur, bt_batch)
    conn.commit()
    hn_batch.clear()
    bt_batch.clear()


# ── ファイルスキャン ──────────────────────────────────────────────────────────

def _scan_files(data_dir: Path) -> list[Path]:
    files: list[Path] = []
    for ext in ("*.dat", "*.txt", "*.jvd", "*.JVD", "*.DAT"):
        files.extend(data_dir.rglob(ext))
    if not files:
        files = [f for f in data_dir.rglob("*") if f.is_file() and not f.suffix]
    return sorted(set(files))


# ── メイン処理 ────────────────────────────────────────────────────────────────

def run(data_dir: Path, truncate: bool = False) -> None:
    files = _scan_files(data_dir)
    if not files:
        log.error("RAW ファイルが見つかりません: %s", data_dir)
        sys.exit(1)
    log.info("対象ファイル: %d 本", len(files))

    with get_conn() as conn:
        _ensure_tables(conn)

        if truncate:
            with conn.cursor() as cur:
                cur.execute("TRUNCATE TABLE lineage_info, hanshoku_ma_master CASCADE")
            conn.commit()
            log.info("テーブルをトランケートしました")

        hn_batch: list[dict] = []
        bt_batch: list[dict] = []
        total_hn = total_bt = 0

        with conn.cursor() as cur:
            for i, fp in enumerate(files, 1):
                try:
                    raw = fp.read_bytes()
                except OSError as e:
                    log.warning("読み込み失敗: %s — %s", fp.name, e)
                    continue

                for line in raw.splitlines():
                    if len(line) < 12:
                        continue
                    rec = line[:2]
                    if rec == b"HN":
                        parsed = _parse_hn(line)
                        if parsed:
                            hn_batch.append(parsed)
                            total_hn += 1
                    elif rec == b"BT":
                        rows = _parse_bt(line)
                        bt_batch.extend(rows)
                        total_bt += len(rows)

                if len(hn_batch) >= _BATCH_SIZE or len(bt_batch) >= _BATCH_SIZE:
                    _commit_batch(conn, cur, hn_batch, bt_batch)

                if i % 50 == 0:
                    log.info(
                        "  %d / %d ファイル処理中 (HN=%d, BT=%d)",
                        i, len(files), total_hn, total_bt,
                    )

            # 残りをフラッシュ
            _commit_batch(conn, cur, hn_batch, bt_batch)

    log.info("完了 — hanshoku_ma_master: %d 件, lineage_info: %d 件", total_hn, total_bt)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="HN/BT マスタを fukurou_jvdl に取り込む")
    p.add_argument(
        "--data-dir",
        type=Path,
        required=True,
        metavar="DIR",
        help="JVDL RAW ファイルが格納されたディレクトリ（再帰スキャン）",
    )
    p.add_argument(
        "--truncate",
        action="store_true",
        help="実行前に hanshoku_ma_master / lineage_info を TRUNCATE する",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run(args.data_dir, truncate=args.truncate)
