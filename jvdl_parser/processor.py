"""
jvdl_parser/processor.py
=========================
process_stream(): iter_records / parse_record / BulkSink / DLQ を統合する。

フロー:
  payload bytes
    → iter_records (CRLF split, 鉄則8)
    → parse_record per record (bytes スライス, 鉄則1-2)
    → WH / O1 のみ専用展開ハンドラ
    → sink.feed()
    → sink.flush() + conn.commit()
    → DLQ 書き込み (RecordLengthError / 予期せぬ例外, 鉄則3)

参照: docs/jvdl_parser_spec.md §5.2, §5.3, §5.4
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .fields import RECORD_DEFS
from .parser import RecordLengthError, iter_records, parse_record, parse_wh_entries, parse_o1_entries, stats
from .sink import BulkSink, _build_race_id

logger = logging.getLogger(__name__)

# WH / O1 以外でレース影響範囲を収集する種別
_RACE_AFFECTING = frozenset(["RA", "SE", "WE", "AV", "JC", "TC", "CC"])


# ── DLQ ───────────────────────────────────────────────────────────────────────

def _write_dlq(
    conn,
    raw: bytes,
    dataspec: str,
    exc: Exception,
) -> None:
    """破損レコードを parse_dlq に BYTEA で保存する（鉄則3）。"""
    rtype = raw[0:2].decode("ascii", errors="replace") if len(raw) >= 2 else "??"
    try:
        import psycopg2
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO parse_dlq"
                " (record_type, dataspec, raw_record, error_class, error_detail)"
                " VALUES (%s, %s, %s, %s, %s)",
                (rtype, dataspec, psycopg2.Binary(raw), type(exc).__name__, str(exc)),
            )
    except Exception:
        logger.exception("[DLQ] write failed for record_type=%s", rtype)


# ── 結果型 ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ProcessResult:
    ok: int
    dlq: int
    counts: dict[str, int]              # レコード種別ごとの投入行数
    affected_race_ids: frozenset[str]   # 完了フック用


# ── メイン処理 ────────────────────────────────────────────────────────────────

def process_stream(
    payload: bytes,
    dataspec: str,
    sink: BulkSink,
    conn,
) -> ProcessResult:
    """1 ストリームのバイト列をパース → BulkSink 投入 → DLQ 記録。

    Args:
        payload:   JV-Link が返した raw bytes（CRLF 区切りレコード列）
        dataspec:  JV-Link の dataspec 文字列（例: "0B31"）— DLQ のトレース用
        sink:      BulkSink インスタンス（呼び出し前に初期化済み）
        conn:      psycopg2 接続（DLQ 書き込みと sink.flush() の commit に使用）
    """
    ok = 0
    dlq = 0
    affected: set[str] = set()

    for raw in iter_records(payload):
        try:
            result = parse_record(raw)
            if result is None:
                continue

            rtype, row = result

            if rtype == "WH":
                entries = parse_wh_entries(raw, row)
                for entry in entries:
                    sink.feed("WH_ENTRY", entry)
                _collect_race_id(row, affected)

            elif rtype == "O1":
                expanded = parse_o1_entries(raw, row)
                for entry in expanded["win"]:
                    sink.feed("O1_WIN", entry)
                for entry in expanded["place"]:
                    sink.feed("O1_PLACE", entry)
                _collect_race_id(row, affected)

            else:
                sink.feed(rtype, row)
                if rtype in _RACE_AFFECTING:
                    _collect_race_id(row, affected)

            ok += 1

        except Exception as exc:
            _write_dlq(conn, raw, dataspec, exc)
            dlq += 1

    counts = sink.flush()

    # DLQ 率監視: 1% 超は仕様変更 / オフセット誤りのサイン（§5.2）
    total = ok + dlq
    if total > 0 and dlq / total > 0.01:
        logger.warning(
            "[Processor] %s: DLQ率 %.1f%% (dlq=%d / total=%d) — "
            "仕様変更またはオフセット誤りの可能性",
            dataspec, dlq / total * 100, dlq, total,
        )

    logger.info(
        "[Processor] %s: ok=%d dlq=%d unknown=%s counts=%s",
        dataspec, ok, dlq, dict(stats.unknown), counts,
    )

    return ProcessResult(
        ok=ok,
        dlq=dlq,
        counts=counts,
        affected_race_ids=frozenset(affected),
    )


def _collect_race_id(row: dict, out: set[str]) -> None:
    """race_id を計算して out に追加する。全フィールドが空の場合は追加しない。"""
    rid = _build_race_id(row)
    if rid.strip("0"):
        out.add(rid)
