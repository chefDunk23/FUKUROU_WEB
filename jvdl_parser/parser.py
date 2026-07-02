"""
jvdl_parser/parser.py
======================
parse_record / iter_records / WH・O1 専用エントリ展開。

鉄則1: bytes スライス → フィールド単位 cp932 decode。レコード全体 decode 禁止。
鉄則2: レコード長検証。不一致は RecordLengthError → DLQ。
鉄則6: 未知レコード種別はエラーではなくスキップ+カウント。

参照: docs/design/jvdl_parser_spec.md §2, §5.1
"""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field

from .fields import F, RECORD_DEFS, _weight, _zogen_sa

# HR セクション定数 (0始まり絶対オフセット)
_HR_BET_WIN       = 1
_HR_BET_PLACE     = 2
_HR_BET_BRACKET   = 3
_HR_BET_QUINELLA  = 4
_HR_BET_WIDE      = 5
_HR_BET_EXACTA    = 6
_HR_BET_TRIO      = 7
_HR_BET_TRIFECTA  = 8

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


# ── HR 払戻セクション展開 ────────────────────────────────────────────────────────
# JV-Data HR レコード (719B, CRLF含む) の 8 払戻セクションをパースして
# payouts テーブル向け dict リストに展開する。
#
# セクション構造 (0始まり絶対オフセット):
#   S1 単勝  raw[27:141]  winner entries at section offset 75, 3 slots × 13B
#   S2 複勝  raw[141:206] 5 entries × 13B
#   S3 枠連  raw[206:245] 3 entries × 13B  (combo = frame1(1B) + frame2(1B))
#   S4 馬連  raw[245:293] 3 entries × 16B
#   S5 ワイド raw[293:453] 10 entries × 16B
#   S6 馬単  raw[453:549] 6 entries × 16B
#   S7 三連複 raw[549:603] 3 entries × 18B
#   S8 三連単 raw[603:717] 6 entries × 19B
#
# 13B entry: combo_or_horse(2B) + payout(9B) + rank(2B)
# 16B entry: horse1(2B) + horse2(2B) + payout(8B) + rank(4B)
# 18B entry: h1(2B)+h2(2B)+h3(2B)+payout(8B)+rank(3B)+pad(1B)
# 19B entry: h1(2B)+h2(2B)+h3(2B)+payout(8B)+rank(3B)+pad(2B)

def _decode(raw: bytes, start: int, length: int) -> str:
    return raw[start:start + length].decode("cp932", errors="replace").strip()


def _parse_payout(s: str) -> int | None:
    return int(s) if (s and s.isdigit() and int(s) > 0) else None


def _parse_rank(s: str) -> int | None:
    return int(s) if (s and s.isdigit()) else None


def _parse_horse(s: str) -> int | None:
    return int(s) if (s and s.isdigit() and int(s) > 0) else None


def _entry_13b(raw: bytes, abs_start: int, header: dict, bet_type: int) -> dict | None:
    """13B entry: combo(2B) + payout(9B) + rank(2B)"""
    h1_s = _decode(raw, abs_start, 2)
    h1 = _parse_horse(h1_s)
    if h1 is None:
        return None
    payout = _parse_payout(_decode(raw, abs_start + 2, 9))
    rank   = _parse_rank(_decode(raw, abs_start + 11, 2))
    return {
        **header,
        "bet_type": bet_type,
        "combo_key": h1_s.zfill(2),
        "horse_1": h1,
        "horse_2": None,
        "horse_3": None,
        "payout": payout,
        "popularity_rank": rank,
    }


def _entry_13b_bracket(raw: bytes, abs_start: int, header: dict) -> dict | None:
    """13B entry for 枠連: frame1(1B) + frame2(1B) + payout(9B) + rank(2B)"""
    f1_s = _decode(raw, abs_start, 1)
    f2_s = _decode(raw, abs_start + 1, 1)
    f1 = _parse_horse(f1_s)
    f2 = _parse_horse(f2_s)
    if f1 is None or f2 is None:
        return None
    payout = _parse_payout(_decode(raw, abs_start + 2, 9))
    rank   = _parse_rank(_decode(raw, abs_start + 11, 2))
    return {
        **header,
        "bet_type": _HR_BET_BRACKET,
        "combo_key": f"{f1:01d}-{f2:01d}",
        "horse_1": f1,
        "horse_2": f2,
        "horse_3": None,
        "payout": payout,
        "popularity_rank": rank,
    }


def _entry_16b(raw: bytes, abs_start: int, header: dict, bet_type: int) -> dict | None:
    """16B entry: horse1(2B) + horse2(2B) + payout(8B) + rank(4B)"""
    h1_s = _decode(raw, abs_start, 2)
    h2_s = _decode(raw, abs_start + 2, 2)
    h1 = _parse_horse(h1_s)
    h2 = _parse_horse(h2_s)
    if h1 is None or h2 is None:
        return None
    payout = _parse_payout(_decode(raw, abs_start + 4, 8))
    rank   = _parse_rank(_decode(raw, abs_start + 12, 4))
    return {
        **header,
        "bet_type": bet_type,
        "combo_key": f"{h1:02d}-{h2:02d}",
        "horse_1": h1,
        "horse_2": h2,
        "horse_3": None,
        "payout": payout,
        "popularity_rank": rank,
    }


def _entry_3horse(raw: bytes, abs_start: int, header: dict, bet_type: int,
                  payout_len: int, rank_len: int) -> dict | None:
    """18B or 19B entry: h1(2B)+h2(2B)+h3(2B)+payout(payout_len B)+rank(rank_len B)"""
    h1_s = _decode(raw, abs_start, 2)
    h2_s = _decode(raw, abs_start + 2, 2)
    h3_s = _decode(raw, abs_start + 4, 2)
    h1 = _parse_horse(h1_s)
    h2 = _parse_horse(h2_s)
    h3 = _parse_horse(h3_s)
    if h1 is None or h2 is None or h3 is None:
        return None
    payout = _parse_payout(_decode(raw, abs_start + 6, payout_len))
    rank   = _parse_rank(_decode(raw, abs_start + 6 + payout_len, rank_len))
    return {
        **header,
        "bet_type": bet_type,
        "combo_key": f"{h1:02d}-{h2:02d}-{h3:02d}",
        "horse_1": h1,
        "horse_2": h2,
        "horse_3": h3,
        "payout": payout,
        "popularity_rank": rank,
    }


def parse_hr_payouts(raw: bytes, header: dict) -> list[dict]:
    """HR レコードの 8 払戻セクションを展開して payouts テーブル向け dict リストを返す。

    引数:
        raw    : parse_record() に渡した raw バイト列 (719B、CRLF含む)
        header : parse_record() が返した dict (race_id 計算用フィールドを含む)
    戻り値:
        payouts テーブルに UPSERT する dict のリスト
    """
    entries: list[dict] = []

    # ── S1: 単勝 (WIN) raw[27:141] — winner slots at section offsets 75, 88, 101 ──
    for slot_offset in (75, 88, 101):
        abs_pos = 27 + slot_offset
        e = _entry_13b(raw, abs_pos, header, _HR_BET_WIN)
        if e:
            entries.append(e)

    # ── S2: 複勝 (PLACE) raw[141:206] — 5 entries × 13B ──────────────────────────
    for k in range(5):
        e = _entry_13b(raw, 141 + k * 13, header, _HR_BET_PLACE)
        if e:
            entries.append(e)

    # ── S3: 枠連 (BRACKET QUINELLA) raw[206:245] — 3 entries × 13B ───────────────
    for k in range(3):
        e = _entry_13b_bracket(raw, 206 + k * 13, header)
        if e:
            entries.append(e)

    # ── S4: 馬連 (QUINELLA) raw[245:293] — 3 entries × 16B ───────────────────────
    for k in range(3):
        e = _entry_16b(raw, 245 + k * 16, header, _HR_BET_QUINELLA)
        if e:
            entries.append(e)

    # ── S5: ワイド (WIDE) raw[293:453] — 10 entries × 16B ────────────────────────
    for k in range(10):
        e = _entry_16b(raw, 293 + k * 16, header, _HR_BET_WIDE)
        if e:
            entries.append(e)

    # ── S6: 馬単 (EXACTA) raw[453:549] — 6 entries × 16B ────────────────────────
    for k in range(6):
        e = _entry_16b(raw, 453 + k * 16, header, _HR_BET_EXACTA)
        if e:
            entries.append(e)

    # ── S7: 三連複 (TRIO) raw[549:603] — 3 entries × 18B ─────────────────────────
    # 18B: h1(2B)+h2(2B)+h3(2B)+payout(8B)+rank(3B)+pad(1B)
    for k in range(3):
        e = _entry_3horse(raw, 549 + k * 18, header, _HR_BET_TRIO,
                          payout_len=8, rank_len=3)
        if e:
            entries.append(e)

    # ── S8: 三連単 (TRIFECTA) raw[603:717] — 6 entries × 19B ────────────────────
    # 19B: h1(2B)+h2(2B)+h3(2B)+payout(8B)+rank(3B)+pad(2B)
    for k in range(6):
        e = _entry_3horse(raw, 603 + k * 19, header, _HR_BET_TRIFECTA,
                          payout_len=8, rank_len=3)
        if e:
            entries.append(e)

    return entries
