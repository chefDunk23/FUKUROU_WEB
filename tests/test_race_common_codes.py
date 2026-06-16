"""
tests/test_race_common_codes.py
================================
_TENKO_LABEL / _JYOKEN_TO_CLASS / JV_GRADE_TO_LABEL /
JV_GRADE_CLASS_SCORE / compute_jv_class_score の網羅テスト。

JV-Data 公式コード表2003準拠の定義検証。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from api_v2.routers._race_common import (
    JV_GRADE_CLASS_SCORE,
    JV_GRADE_TO_LABEL,
    _JYOKEN_TO_CLASS,
    _TENKO_LABEL,
    compute_jv_class_score,
)


class TestTenkoLabel:
    @pytest.mark.parametrize("code, expected", [
        ("1", "晴"),
        ("2", "曇"),
        ("3", "雨"),
        ("4", "小雨"),
        ("5", "雪"),    # 公式2011コード表に存在 — 旧実装で欠落
        ("6", "小雪"),  # 同上
    ])
    def test_known_codes(self, code, expected):
        assert _TENKO_LABEL[code] == expected

    def test_all_official_codes_present(self):
        # 公式コード表2011: 1晴 2曇 3雨 4小雨 5雪 6小雪（0=未設定は辞書に含めない）
        for code in ("1", "2", "3", "4", "5", "6"):
            assert code in _TENKO_LABEL, f"code '{code}' missing from _TENKO_LABEL"


class TestJyokenToClass:
    @pytest.mark.parametrize("code, expected", [
        ("701", "新馬"),
        ("702", "未出走"),   # 公式2007コード表に存在 — 旧実装で欠落
        ("703", "未勝利"),
        ("005", "1勝クラス"),
        ("010", "2勝クラス"),
        ("016", "3勝クラス"),
        ("999", "オープン"),
    ])
    def test_known_codes(self, code, expected):
        assert _JYOKEN_TO_CLASS[code] == expected


class TestJvGradeToLabel:
    """JV_GRADE_TO_LABEL は公式コード表2003準拠。keiba_v2 独自の _GRADE_TO_LABEL とは別物。"""

    @pytest.mark.parametrize("code, expected", [
        ("A", "G1"),
        ("B", "G2"),
        ("C", "G3"),
        ("D", "重賞"),    # グレードなし重賞（旧 _GRADE_TO_LABEL では G3 と誤変換）
        ("F", "J・G1"),   # 障害G1（旧では G2 と誤変換）
        ("G", "J・G2"),   # 障害G2（旧では G1 と誤変換）
        ("H", "J・G3"),   # 障害G3（旧では 2勝クラス と誤変換）
        ("L", "Listed"),
    ])
    def test_official_mapping(self, code, expected):
        assert JV_GRADE_TO_LABEL[code] == expected

    def test_e_not_in_dict(self):
        # 'E'（特別競走）は意図的に除外 — jyoken_cd Tier2 で "1勝クラス"等に細分化するため。
        # is_special フラグで grade_code=='E' の意味は別途保持する。
        assert "E" not in JV_GRADE_TO_LABEL

    def test_r_not_in_dict(self):
        # 'R' は公式コード表に存在しない。パーサー修正後は発生しないはず。
        assert "R" not in JV_GRADE_TO_LABEL

    def test_space_not_in_dict(self):
        # スペース（一般競走/未設定）はラベルなし — 呼び出し側が .get() で None を扱う
        assert " " not in JV_GRADE_TO_LABEL


class TestJvGradeClassScore:
    """JV_GRADE_CLASS_SCORE + compute_jv_class_score の検証。"""

    @pytest.mark.parametrize("code, expected", [
        ("A", 15.0),  # G1
        ("F", 15.0),  # 障害G1（平地同格）
        ("B", 13.0),  # G2
        ("G", 13.0),  # 障害G2
        ("C", 11.0),  # G3
        ("H", 11.0),  # 障害G3
        ("L", 9.0),   # Listed
        ("D", 10.0),  # 格なし重賞
    ])
    def test_grade_score_map(self, code, expected):
        assert JV_GRADE_CLASS_SCORE[code] == expected

    def test_e_not_in_grade_score_map(self):
        # E は jyoken_cd で細分化するため直接エントリなし
        assert "E" not in JV_GRADE_CLASS_SCORE

    @pytest.mark.parametrize("grade, jyoken_cds, expected", [
        ("A",   (),          15.0),  # G1 直接
        ("F",   (),          15.0),  # 障害G1
        ("E",   ("016",),    8.0),   # 特別 + 3勝クラス jyoken_cd
        ("E",   ("005",),    5.0),   # 特別 + 1勝クラス jyoken_cd
        ("E",   ("999",),    9.0),   # 特別 + オープン
        ("E",   ("703",),    3.0),   # 特別 + 未勝利
        ("E",   (),          3.0),   # jyoken_cd なし → デフォルト
        (None,  (),          3.0),   # grade なし → デフォルト
        ("E",   ("000",),    3.0),   # jyoken_cd=000（無効値）→ デフォルト
    ])
    def test_compute_jv_class_score(self, grade, jyoken_cds, expected):
        assert compute_jv_class_score(grade, jyoken_cds) == expected
