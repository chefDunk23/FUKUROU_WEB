"""
tests/test_script_text.py
===========================
api_admin/services/script_text.py の純粋関数テスト（DBなし）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from api_admin.services.script_text import (
    apply_reading_dict,
    convert_grade_code,
    convert_race_number,
    strip_marks,
    to_speech_text,
)

_DICT = {
    "horses": {"ニシノイストワール": "にしのいすとわーる"},
    "venues": {"函館": "はこだて"},
    "raceNames": {"函館記念": "はこだてきねん"},
    "grades": {},
    "raceNumbers": {},
    "marks": {"◎": "", "○": "", "▲": "", "△": ""},
}


class TestConvertRaceNumber:
    def test_11r(self):
        assert convert_race_number("11R") == "じゅういちアール"

    def test_1r(self):
        assert convert_race_number("1R") == "いちアール"

    def test_no_match_unchanged(self):
        assert convert_race_number("函館記念") == "函館記念"


class TestConvertGradeCode:
    def test_g3(self):
        assert convert_grade_code("G3函館記念") == "ジーさん函館記念"

    def test_g1(self):
        assert convert_grade_code("G1") == "ジーいち"


class TestStripMarks:
    def test_removes_all_mark_chars(self):
        assert strip_marks("◎5番○2番▲8番△1番") == "5番2番8番1番"


class TestApplyReadingDict:
    def test_replaces_known_terms(self):
        result = apply_reading_dict("函館記念", _DICT)
        assert result == "はこだてきねん"

    def test_longer_match_preferred_over_substring(self):
        # "函館記念" (raceNames) と "函館" (venues) が両方マッチしうる場合、
        # 長い表記から置換されるため venues側の部分置換で壊れない
        result = apply_reading_dict("函館記念に注目", _DICT)
        assert result == "はこだてきねんに注目"


class TestToSpeechText:
    def test_full_pipeline(self):
        result = to_speech_text("◎函館11R 函館記念、本命はニシノイストワール", _DICT)
        assert "◎" not in result
        assert "じゅういちアール" in result
        assert "はこだてきねん" in result
        assert "にしのいすとわーる" in result
