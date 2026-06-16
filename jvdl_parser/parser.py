"""
jvdl_parser/parser.py
======================
parse_record / iter_records / WH・O1 専用エントリ展開。

鉄則1: bytes スライス → フィールド単位 cp932 decode。レコード全体 decode 禁止。
鉄則2: レコード長検証。不一致は RecordLengthError → DLQ。
鉄則6: 未知レコード種別はエラーではなくスキップ+カウント。

参照: docs/jvdl_parser_spec.md §2, §5.1
"""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field

from .fields import F, RECORD_DEFS, _weight, _zogen_sa

logger = logging.getLogger(__name__)


# ── エラー型 ───────────────────────────────────────────────────────────────────

class RecordLengthError(ValueError):
    """レコード長が仕様と一致しない場合。DLQ に格納すること。"""
    def __init__(self, record_type: bytes, actual: int, expected: int) -> None:
        self.record_type = record_type
        self.actual = actual
        self.expected = expected
        super().__init__(
            f"{record_type!r}: expected {expected}B, got {actual}B"
        )


# ── 統計カウンター ─────────────────────────────────────────────────────────────

@dataclass
class ParseStats:
    unknown: Counter = field(default_factory=Counter)
    ok: int = 0
    dlq: int = 0

    def reset(self) -> None:
        self.unknown.clear()
        self.ok = 0
        self.dlq = 0


# モジュールレベルの統計（長期実行プロセス用）
stats = ParseStats()


# ── コアパーサー ───────────────────────────────────────────────────────────────

def parse_record(raw: bytes) -> tuple[str, dict] | None:
    """1 レコード（CRLF なし）をパースして (record_type, fields_dict) を返す。

    - 未知種別: None を返す（鉄則6）
    - 長さ不一致: RecordLengthError を raise → 呼び出し側が DLQ へ（鉄則2）
    """
    rid = raw[0:2]
    spec = RECORD_DEFS.get(rid)
    if spec is None:
        stats.unknown[rid] += 1
        return None

    expected_len, fields, _ = spec
    if len(raw) != expected_len:
        raise RecordLengthError(rid, len(raw), expected_len)

    out: dict = {}
    for f in fields:
        chunk = raw[f.pos - 1 : f.pos - 1 + f.length]          # 鉄則1: bytesスライス
        text = chunk.decode("cp932", errors="replace")          # フィールド単位decode
        out[f.name] = f.conv(text)

    return rid.decode("ascii"), out


def iter_records(payload: bytes):
    """CRLF 区切りでレコードを切り出して yield する（鉄則8）。"""
    for raw in payload.split(b"\r\n"):
        if raw:
            yield raw


# ── WH 繰返しブロック展開 ───────────────────────────────────────────────────────

# WH エントリ 1 件のオフセット（仕様書: 馬番2B + 馬名36B + 馬体重3B + 増減符号1B + 増減差3B = 45B）
_WH_ENTRY_SIZE = 45
_WH_ENTRY_START = 36 - 1   # 0-based
_WH_MAX_ENTRIES = 18


def parse_wh_entries(raw: bytes, header: dict) -> list[dict]:
    """WH レコードの繰返しブロックを展開し、エントリごとの dict リストを返す。"""
    entries: list[dict] = []
    base = _WH_ENTRY_START
    for _ in range(_WH_MAX_ENTRIES):
        chunk = raw[base : base + _WH_ENTRY_SIZE]
        if len(chunk) < _WH_ENTRY_SIZE:
            break
        umaban_raw = chunk[0:2].decode("cp932", errors="replace").strip()
        # umaban が空 = エントリなし（末尾パディング）
        if not umaban_raw or not umaban_raw.isdigit():
            base += _WH_ENTRY_SIZE
            continue
        entries.append({
            **header,
            "umaban":       int(umaban_raw),
            "horse_name":   chunk[2:38].decode("cp932", errors="replace").strip() or None,
            "horse_weight": _weight(chunk[38:41].decode("cp932", errors="replace")),
            "zogen_fugo":   chunk[41:42].decode("cp932", errors="replace").strip() or None,
            "zogen_sa":     _zogen_sa(chunk[42:45].decode("cp932", errors="replace")),
        })
        base += _WH_ENTRY_SIZE
    return entries


# ── O1 繰返しブロック展開 ───────────────────────────────────────────────────────

_O1_WIN_START  = 44 - 1   # 0-based  単勝 8B×28
_O1_PLACE_START = 268 - 1  # 0-based  複勝 12B×28
_O1_MAX_HORSES = 28


def _parse_odds_value(raw4: bytes) -> float | None:
    """オッズ 4 バイト。0000=無投票、非数値（----等）→ None"""
    s = raw4.decode("cp932", errors="replace").strip()
    if not s.isdigit() or s == "0000":
        return None
    return int(s) / 10.0


def _parse_ninki(raw2: bytes) -> int | None:
    s = raw2.decode("cp932", errors="replace").strip()
    if not s or s in ("--", "  ", "**"):
        return None
    return int(s) if s.isdigit() else None


def parse_o1_entries(raw: bytes, header: dict) -> dict:
    """O1 レコードの単勝・複勝オッズを展開して返す。

    戻り値: {
        "win":   [{umaban, odds, ninki}, ...],
        "place": [{umaban, odds_min, odds_max, ninki}, ...],
    }
    """
    win_entries: list[dict] = []
    place_entries: list[dict] = []

    base = _O1_WIN_START
    for _ in range(_O1_MAX_HORSES):
        chunk = raw[base : base + 8]
        if len(chunk) < 8:
            break
        umaban_s = chunk[0:2].decode("cp932", errors="replace").strip()
        if not umaban_s or not umaban_s.isdigit():
            base += 8
            continue
        win_entries.append({
            **header,
            "umaban": int(umaban_s),
            "odds":   _parse_odds_value(chunk[2:6]),
            "ninki":  _parse_ninki(chunk[6:8]),
        })
        base += 8

    base = _O1_PLACE_START
    for _ in range(_O1_MAX_HORSES):
        chunk = raw[base : base + 12]
        if len(chunk) < 12:
            break
        umaban_s = chunk[0:2].decode("cp932", errors="replace").strip()
        if not umaban_s or not umaban_s.isdigit():
            base += 12
            continue
        place_entries.append({
            **header,
            "umaban":    int(umaban_s),
            "odds_min":  _parse_odds_value(chunk[2:6]),
            "odds_max":  _parse_odds_value(chunk[6:10]),
            "ninki":     _parse_ninki(chunk[10:12]),
        })
        base += 12

    return {"win": win_entries, "place": place_entries}
