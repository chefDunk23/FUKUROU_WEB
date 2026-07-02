"""
api_admin/services/script_text.py
===================================
読み上げ原稿のテキスト整形。keiba_pick_video/data/reading_dict.json を通し、
印記号除去・数字読み変換（11R→じゅういちアール、G3→ジーさん）を適用する。

テロップ用テキスト（表記のまま）と読み上げ用テキスト（かな変換済み）は別物として扱う。
この関数群は読み上げ用テキストの生成のみを担当する。
"""
from __future__ import annotations

import re
from typing import TypedDict


class ReadingDict(TypedDict):
    horses: dict[str, str]
    venues: dict[str, str]
    raceNames: dict[str, str]
    grades: dict[str, str]
    raceNumbers: dict[str, str]
    marks: dict[str, str]


def apply_reading_dict(text: str, reading_dict: ReadingDict) -> str:
    """辞書の表記→読みを長い表記から順に置換する（部分一致誤爆防止のため長さ降順）。"""
    result = text
    for category in ("raceNames", "venues", "horses", "grades", "raceNumbers", "marks"):
        entries = reading_dict.get(category, {})
        for surface in sorted(entries, key=len, reverse=True):
            reading = entries[surface]
            result = result.replace(surface, reading)
    return result


_RACE_NUMBER_RE = re.compile(r"(\d{1,2})R")
_GRADE_RE = re.compile(r"G([1-3])")

_DIGIT_READING = {
    "1": "いち", "2": "に", "3": "さん", "4": "よん", "5": "ご",
    "6": "ろく", "7": "なな", "8": "はち", "9": "きゅう", "10": "じゅう",
    "11": "じゅういち", "12": "じゅうに",
}


def convert_race_number(text: str) -> str:
    """"11R" → "じゅういちアール" のように数字+Rを読みに変換する。"""
    def _replace(m: re.Match[str]) -> str:
        n = m.group(1)
        return f"{_DIGIT_READING.get(n, n)}アール"

    return _RACE_NUMBER_RE.sub(_replace, text)


def convert_grade_code(text: str) -> str:
    """"G3" → "ジーさん" のようにグレード表記を読みに変換する。"""
    def _replace(m: re.Match[str]) -> str:
        return f"ジー{_DIGIT_READING[m.group(1)]}"

    return _GRADE_RE.sub(_replace, text)


_MARK_CHARS = ("◎", "○", "▲", "△", "☆", "注")


def strip_marks(text: str) -> str:
    """印記号を除去する（読み上げ時に記号を読ませないため）。"""
    result = text
    for ch in _MARK_CHARS:
        result = result.replace(ch, "")
    return result


def to_speech_text(text: str, reading_dict: ReadingDict) -> str:
    """テロップ用テキストから読み上げ用テキストを生成する（変換の適用順が重要）。"""
    result = strip_marks(text)
    result = convert_race_number(result)
    result = convert_grade_code(result)
    result = apply_reading_dict(result, reading_dict)
    return result
