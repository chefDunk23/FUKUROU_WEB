"""
tests/test_jvdl_parser_fields.py
==================================
conv 関数群の境界値テスト（§4 センチネル値変換表の全ケースを網羅）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from jvdl_parser.fields import (
    F,
    _code,
    _default_conv,
    _int,
    _lap3,
    _laptime4,
    _odds,
    _time4,
    _weight,
    _zogen_sa,
)


class TestDefaultConv:
    @pytest.mark.parametrize("s, expected", [
        ("ABC",   "ABC"),
        (" ABC ", "ABC"),
        ("",      None),
        ("   ",   None),
        ("0",     "0"),    # "0" は有効値として保持（data_kubun の削除レコード等）
    ])
    def test_basic(self, s, expected):
        assert _default_conv(s) == expected


class TestInt:
    @pytest.mark.parametrize("s, expected", [
        ("0",    0),
        ("42",   42),
        ("999",  999),
        ("  42 ", 42),
        ("",     None),
        ("abc",  None),
        (" ",    None),
        ("1.5",  None),
    ])
    def test_values(self, s, expected):
        assert _int(s) == expected


class TestWeight:
    @pytest.mark.parametrize("s, expected", [
        ("450",  450),
        ("001",  1),     # 小さい値は有効
        ("999",  None),  # 計量不能
        ("000",  None),  # 出走取消
        ("   ",  None),
        ("",     None),
    ])
    def test_sentinels(self, s, expected):
        assert _weight(s) == expected


class TestZogenSa:
    @pytest.mark.parametrize("s, expected", [
        ("000",  0),     # 前差なし
        ("004",  4),
        ("012",  12),
        ("999",  None),  # 計量不能
        ("   ",  None),  # 初出走
        ("",     None),
    ])
    def test_sentinels(self, s, expected):
        assert _zogen_sa(s) == expected


class TestOdds:
    @pytest.mark.parametrize("s, expected", [
        ("0010",  1.0),   # 1.0倍
        ("0235",  23.5),
        ("9990",  999.0),
        ("0000",  None),  # 無投票
        ("----",  None),  # 取消
        ("    ",  None),
        ("",      None),
    ])
    def test_sentinels(self, s, expected):
        assert _odds(s) == expected


class TestTime4:
    @pytest.mark.parametrize("s, expected", [
        ("1234",  83.4),   # 1分23.4秒
        ("0594",  59.4),   # 0分59.4秒
        ("0000",  None),
        ("    ",  None),
        ("9999",  639.9),  # 9分99.9秒（上限）
    ])
    def test_race_time(self, s, expected):
        result = _time4(s)
        if expected is None:
            assert result is None
        else:
            assert result == pytest.approx(expected, abs=0.01)


class TestLaptime4:
    @pytest.mark.parametrize("s, expected", [
        ("0653",  65.3),
        ("1200",  120.0),
        ("0000",  None),
        ("    ",  None),
        ("9999",  999.9),
    ])
    def test_training_total(self, s, expected):
        result = _laptime4(s)
        if expected is None:
            assert result is None
        else:
            assert result == pytest.approx(expected, abs=0.01)


class TestLap3:
    @pytest.mark.parametrize("s, expected", [
        ("116",  11.6),
        ("120",  12.0),
        ("999",  None),  # 取消等
        ("000",  None),
        ("   ",  None),
        ("",     None),
    ])
    def test_lap_sentinels(self, s, expected):
        result = _lap3(s)
        if expected is None:
            assert result is None
        else:
            assert result == pytest.approx(expected, abs=0.01)


class TestCode:
    @pytest.mark.parametrize("s, expected", [
        ("A",    "A"),
        ("01",   "01"),
        ("703",  "703"),
        ("0",    None),    # 未設定 sentinel（1桁）
        ("00",   None),    # 未設定 sentinel（2桁）
        ("000",  None),    # 未設定 sentinel（3桁）
        ("",     None),
        (" ",    None),
        ("  ",   None),
    ])
    def test_sentinels(self, s, expected):
        assert _code(s) == expected

    def test_does_not_null_0000(self):
        # "0000" は _code の対象外（4桁コードは別途 _odds/_time4 で処理）
        assert _code("0000") == "0000"


class TestFDataclass:
    def test_frozen(self):
        f = F("grade_code", 615, 1, _code)
        with pytest.raises(Exception):
            f.name = "other"  # type: ignore[misc]

    def test_default_conv_is_default_conv_fn(self):
        f = F("test", 1, 1)
        assert f.conv("  hello  ") == "hello"
        assert f.conv("   ") is None

    def test_pos_length(self):
        f = F("grade_code", 615, 1, _code)
        assert f.pos == 615
        assert f.length == 1
