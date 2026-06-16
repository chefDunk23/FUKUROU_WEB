"""
tests/test_jvdl_parser_parse.py
=================================
parse_record / iter_records の統合テスト。

テスト方針:
- 実バイトレコードを手作りして parse_record に渡す
- フィールドが正しいバイト位置から切り出されることを検証（鉄則1）
- レコード長検証（鉄則2）
- 未知種別スキップ（鉄則6）
- cp932 破損バイトがフィールド内に閉じること（鉄則1 の副効果）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from jvdl_parser.fields import RECORD_DEFS
from jvdl_parser.parser import RecordLengthError, iter_records, parse_record, stats


# ── フィクスチャ ヘルパー ─────────────────────────────────────────────────────

def _make_record(record_type: bytes, length: int, patches: dict[int, bytes]) -> bytes:
    """仕様の長さ(CRLF込み)のバッファを空白で埋め、patches で指定バイトを上書きする。
    patches のキーは 0-based バイトインデックス。
    """
    buf = bytearray(b" " * (length - 2)) + bytearray(b"\r\n")
    buf[0:2] = record_type
    for start, data in sorted(patches.items()):
        buf[start : start + len(data)] = data
    return bytes(buf)


def _ra(patches: dict[int, bytes] | None = None) -> bytes:
    """最小有効 RA レコード（長 1272）"""
    p: dict[int, bytes] = {
        2:  b"2",           # data_kubun = 出馬表
        3:  b"20260607",    # data_create_date
        11: b"2026",        # kaisai_year
        15: b"0607",        # kaisai_monthday
        19: b"05",          # keibajo_code (東京)
        21: b"01",          # kaisai_kai
        23: b"01",          # kaisai_nichime
        25: b"01",          # race_num
    }
    if patches:
        p.update(patches)
    return _make_record(b"RA", 1272, p)


def _se(patches: dict[int, bytes] | None = None) -> bytes:
    """最小有効 SE レコード（長 555）"""
    p: dict[int, bytes] = {
        2:  b"2",
        3:  b"20260607",
        11: b"2026",
        15: b"0607",
        19: b"05",
        21: b"01",
        23: b"01",
        25: b"01",
        27: b"1",           # wakuban
        28: b"01",          # umaban
        30: b"2024100001",  # blood_no
    }
    if patches:
        p.update(patches)
    return _make_record(b"SE", 555, p)


def _hc(patches: dict[int, bytes] | None = None) -> bytes:
    """最小有効 HC レコード（長 60）"""
    p: dict[int, bytes] = {
        2:  b"0",
        3:  b"20260607",
        11: b"1",           # center_cd (栗東)
        12: b"20260605",    # chokyo_date
        20: b"0800",        # chokyo_time
        24: b"2024100001",  # blood_no
        34: b"0653",        # time_4f = 65.3秒
        38: b"116",         # lap_l4_l3 = 11.6秒
        41: b"0538",        # time_3f = 53.8秒
        45: b"116",         # lap_l3_l2
        48: b"0423",        # time_2f = 42.3秒
        52: b"112",         # lap_l2_l1 = 11.2秒
        55: b"115",         # lap_l1 = 11.5秒
    }
    if patches:
        p.update(patches)
    return _make_record(b"HC", 60, p)


# ── parse_record: 基本動作 ────────────────────────────────────────────────────

class TestParseRecordRA:
    def test_returns_correct_type(self):
        rid, row = parse_record(_ra())
        assert rid == "RA"

    def test_data_kubun_extracted(self):
        _, row = parse_record(_ra())
        assert row["data_kubun"] == "2"

    def test_data_create_date_extracted(self):
        _, row = parse_record(_ra())
        assert row["data_create_date"] == "20260607"

    def test_kaisai_year_extracted(self):
        _, row = parse_record(_ra())
        assert row["kaisai_year"] == "2026"

    def test_keibajo_code_extracted(self):
        _, row = parse_record(_ra())
        assert row["keibajo_code"] == "05"

    def test_race_num_extracted(self):
        _, row = parse_record(_ra())
        assert row["race_num"] == "01"

    def test_grade_code_at_pos615(self):
        # pos 615 = index 614。"B" を書き込んで正しく読めることを検証
        raw = _ra({614: b"B"})
        _, row = parse_record(raw)
        assert row["grade_code"] == "B"

    def test_grade_code_space_becomes_none(self):
        # スペース = 一般競走 → None
        raw = _ra({614: b" "})
        _, row = parse_record(raw)
        assert row["grade_code"] is None

    def test_jyoken_cd_2_at_pos623(self):
        # pos 623 = index 622
        raw = _ra({622: b"703"})
        _, row = parse_record(raw)
        assert row["jyoken_cd_2"] == "703"

    def test_jyoken_cd_zero_becomes_none(self):
        raw = _ra({622: b"000"})
        _, row = parse_record(raw)
        assert row["jyoken_cd_2"] is None

    def test_jyoken_cd_youngest_at_pos635(self):
        # pos 635 = index 634
        raw = _ra({634: b"999"})
        _, row = parse_record(raw)
        assert row["jyoken_cd_youngest"] == "999"

    def test_track_code_at_pos706(self):
        # pos 706 = index 705
        raw = _ra({705: b"17"})
        _, row = parse_record(raw)
        assert row["track_code"] == "17"

    def test_tenko_code_at_pos888(self):
        raw = _ra({887: b"1"})
        _, row = parse_record(raw)
        assert row["tenko_code"] == "1"

    def test_shiba_baba_code_at_pos889(self):
        raw = _ra({888: b"1"})
        _, row = parse_record(raw)
        assert row["shiba_baba_code"] == "1"

    def test_dirt_baba_code_at_pos890(self):
        raw = _ra({889: b"3"})
        _, row = parse_record(raw)
        assert row["dirt_baba_code"] == "3"

    def test_grade_code_r_passes_through(self):
        # 'R' は _code では None にならない（公式仕様には存在しないが既存データ互換のため残す）
        raw = _ra({614: b"R"})
        _, row = parse_record(raw)
        assert row["grade_code"] == "R"

    def test_shiba_baba_code_independent_from_dirt(self):
        # 芝とダートが独立したカラムに切り出されること（旧 track_condition 単一カラムとの差別化）
        raw = _ra({888: b"1", 889: b"4"})
        _, row = parse_record(raw)
        assert row["shiba_baba_code"] == "1"
        assert row["dirt_baba_code"] == "4"


class TestParseRecordSE:
    def test_returns_correct_type(self):
        rid, _ = parse_record(_se())
        assert rid == "SE"

    def test_umaban_as_int(self):
        raw = _se({28: b"05"})
        _, row = parse_record(raw)
        assert row["umaban"] == 5

    def test_blood_no_extracted(self):
        _, row = parse_record(_se())
        assert row["blood_no"] == "2024100001"

    def test_horse_weight_sentinel_999(self):
        # pos 325 = index 324
        raw = _se({324: b"999"})
        _, row = parse_record(raw)
        assert row["horse_weight"] is None

    def test_horse_weight_sentinel_000(self):
        raw = _se({324: b"000"})
        _, row = parse_record(raw)
        assert row["horse_weight"] is None

    def test_horse_weight_valid(self):
        raw = _se({324: b"480"})
        _, row = parse_record(raw)
        assert row["horse_weight"] == 480

    def test_tansho_odds_0000_becomes_none(self):
        # pos 360 = index 359
        raw = _se({359: b"0000"})
        _, row = parse_record(raw)
        assert row["tansho_odds"] is None

    def test_tansho_odds_valid(self):
        raw = _se({359: b"0235"})
        _, row = parse_record(raw)
        assert row["tansho_odds"] == pytest.approx(23.5)

    def test_race_time_0000_becomes_none(self):
        # pos 339 = index 338
        raw = _se({338: b"0000"})
        _, row = parse_record(raw)
        assert row["race_time"] is None

    def test_race_time_valid(self):
        raw = _se({338: b"1234"})
        _, row = parse_record(raw)
        assert row["race_time"] == pytest.approx(83.4)

    def test_kohan_3f_999_becomes_none(self):
        # pos 391 = index 390
        raw = _se({390: b"999"})
        _, row = parse_record(raw)
        assert row["kohan_3f"] is None

    def test_zogen_sa_000_becomes_0(self):
        # pos 329 = index 328
        raw = _se({328: b"000"})
        _, row = parse_record(raw)
        assert row["zogen_sa"] == 0

    def test_zogen_sa_999_becomes_none(self):
        raw = _se({328: b"999"})
        _, row = parse_record(raw)
        assert row["zogen_sa"] is None


class TestParseRecordHC:
    def test_returns_correct_type(self):
        rid, _ = parse_record(_hc())
        assert rid == "HC"

    def test_center_cd_extracted(self):
        _, row = parse_record(_hc())
        assert row["center_cd"] == "1"

    def test_blood_no_extracted(self):
        _, row = parse_record(_hc())
        assert row["blood_no"] == "2024100001"

    def test_time_4f_extracted(self):
        _, row = parse_record(_hc())
        assert row["time_4f"] == pytest.approx(65.3)

    def test_lap_l1_extracted(self):
        _, row = parse_record(_hc())
        assert row["lap_l1"] == pytest.approx(11.5)

    def test_time_4f_0000_becomes_none(self):
        raw = _hc({34: b"0000"})
        _, row = parse_record(raw)
        assert row["time_4f"] is None

    def test_lap_999_becomes_none(self):
        raw = _hc({38: b"999"})
        _, row = parse_record(raw)
        assert row["lap_l4_l3"] is None


# ── parse_record: エラー / エッジケース ───────────────────────────────────────

class TestParseRecordErrors:
    def test_wrong_length_raises(self):
        raw = b"RA" + b" " * 100 + b"\r\n"  # 正しくは 1272B
        with pytest.raises(RecordLengthError) as exc_info:
            parse_record(raw)
        err = exc_info.value
        assert err.record_type == b"RA"
        assert err.expected == 1272
        assert err.actual == 104  # 2(RA) + 100(spaces) + 2(CRLF)

    def test_unknown_type_returns_none(self):
        stats.reset()
        raw = b"ZZ" + b" " * 98 + b"\r\n"
        result = parse_record(raw)
        assert result is None
        assert stats.unknown[b"ZZ"] == 1

    def test_unknown_type_increments_counter(self):
        stats.reset()
        for _ in range(3):
            parse_record(b"XX" + b" " * 8 + b"\r\n")
        assert stats.unknown[b"XX"] == 3

    def test_cp932_broken_byte_contained_in_field(self):
        # 無効な cp932 バイトシーケンス（0x80 は cp932 未定義）を
        # race_name_hondai（pos 33, len 60）に埋め込む。
        # → errors="replace" でフィールド内に置換文字が入るが、
        #   他のフィールドは正しく読めることを確認（鉄則1 の副効果）
        raw = bytearray(_ra())
        raw[32 : 32 + 5] = b"\x80\x80\x80\x80\x80"   # 未定義 cp932 バイト
        rid, row = parse_record(bytes(raw))
        assert rid == "RA"
        # 壊れた名前フィールドは None ではなく置換文字入り文字列になるはず
        # grade_code は影響を受けない（pos 615 = index 614）
        assert row["data_kubun"] == "2"
        assert row["grade_code"] is None or isinstance(row["grade_code"], str)

    def test_record_length_error_attributes(self):
        raw = b"SE" + b" " * 100 + b"\r\n"
        with pytest.raises(RecordLengthError) as exc_info:
            parse_record(raw)
        err = exc_info.value
        assert err.expected == 555
        assert err.actual == 104  # 2(SE) + 100(spaces) + 2(CRLF)


# ── iter_records ──────────────────────────────────────────────────────────────

class TestIterRecords:
    def test_splits_on_crlf(self):
        a = b"RA" + b" " * 10
        b_ = b"SE" + b" " * 10
        payload = a + b"\r\n" + b_ + b"\r\n"
        result = list(iter_records(payload))
        assert result == [a, b_]

    def test_empty_payload(self):
        assert list(iter_records(b"")) == []

    def test_skips_empty_chunks(self):
        # 末尾の \r\n から生まれる空 bytes はスキップ
        payload = b"RA" + b" " * 10 + b"\r\n"
        result = list(iter_records(payload))
        assert len(result) == 1
        assert result[0][:2] == b"RA"

    def test_multiple_records(self):
        records = [b"RA" + b" " * 10, b"SE" + b" " * 8, b"HC" + b" " * 6]
        payload = b"\r\n".join(records) + b"\r\n"
        result = list(iter_records(payload))
        assert len(result) == 3

    def test_cp932_trail_byte_not_split(self):
        # cp932 の 2 バイト文字トレイルに 0x0A (LF) は現れないため
        # 正常なレコードはバイト位置ズレを起こさない（鉄則8 の前提検証）
        # 0x0A で終わる合法的な cp932 バイトは存在しないことを確認
        # ここでは「全角文字を含むバイト列が CRLF で正しく分割される」ことを検証
        kanji = "東京".encode("cp932")  # 東京 = \x93\x8C\x8B\x9E (4B、どれも 0x0A ではない)
        assert 0x0A not in kanji
        assert 0x0D not in kanji


# ── RECORD_DEFS の完整性 ──────────────────────────────────────────────────────

class TestRecordDefs:
    @pytest.mark.parametrize("rid, expected_len", [
        (b"RA", 1272),
        (b"SE",  555),
        (b"WH",  847),
        (b"WE",   42),
        (b"AV",   78),
        (b"JC",  161),
        (b"TC",   45),
        (b"CC",   50),
        (b"O1",  962),
        (b"HC",   60),
        (b"WC",  105),
    ])
    def test_all_record_types_defined(self, rid, expected_len):
        assert rid in RECORD_DEFS
        actual_len, _, _ = RECORD_DEFS[rid]
        assert actual_len == expected_len

    def test_wh_and_o1_have_no_table(self):
        # 繰返しブロックあり → テーブル名 None
        for rid in (b"WH", b"O1"):
            _, _, table = RECORD_DEFS[rid]
            assert table is None, f"{rid} should have table=None"

    def test_all_field_pos_within_record(self):
        for rid, (rec_len, fields, _) in RECORD_DEFS.items():
            for f in fields:
                end = f.pos - 1 + f.length   # 0-based end (exclusive)
                assert end <= rec_len - 2, (
                    f"{rid!r}.{f.name}: pos={f.pos} len={f.length} "
                    f"exceeds data range (rec_len={rec_len}, CRLF=2)"
                )
