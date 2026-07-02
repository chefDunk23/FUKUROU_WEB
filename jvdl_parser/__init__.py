"""
jvdl_parser — JV-Data 固定長バイトレコードの宣言的パーサー。

鉄則: decode より先にスライスしない。フィールド単位で bytes → cp932 decode する。
参照: docs/jvdl_parser_spec.md
"""
from .fields import F, RECORD_DEFS
from .parser import RecordLengthError, iter_records, parse_record
from .sink import BulkSink
from .processor import ProcessResult, process_stream

__all__ = [
    "F", "RECORD_DEFS",
    "RecordLengthError", "iter_records", "parse_record",
    "BulkSink",
    "ProcessResult", "process_stream",
]
