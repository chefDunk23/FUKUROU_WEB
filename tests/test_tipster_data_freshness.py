"""
tests/test_tipster_data_freshness.py
======================================
GET /api/v2/tipster/data-freshness のうち、DB接続を必要としない純粋関数を検証する。

対象:
  _parse_target_dates  対象日パース（省略時は今週末を算出）
  _overall_level       warning リストから overall_level を判定
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from api_v2.routers.tipster import (
    FreshnessWarning,
    _overall_level,
    _parse_target_dates,
)


class TestParseTargetDates:
    def test_explicit_single_date(self):
        assert _parse_target_dates("2026-07-04") == [date(2026, 7, 4)]

    def test_explicit_multiple_dates(self):
        assert _parse_target_dates("2026-07-04,2026-07-05") == [
            date(2026, 7, 4), date(2026, 7, 5),
        ]

    def test_explicit_dates_with_whitespace(self):
        assert _parse_target_dates(" 2026-07-04 , 2026-07-05 ") == [
            date(2026, 7, 4), date(2026, 7, 5),
        ]

    def test_none_falls_back_to_this_weekend(self):
        result = _parse_target_dates(None)
        assert len(result) == 2
        sat, sun = result
        assert sat.weekday() == 5
        assert sun.weekday() == 6
        assert (sun - sat).days == 1

    def test_empty_string_falls_back_to_this_weekend(self):
        result = _parse_target_dates("")
        assert len(result) == 2
        assert result[0].weekday() == 5


class TestOverallLevel:
    def test_no_warnings_is_ok(self):
        assert _overall_level([]) == "ok"

    def test_only_warning_level(self):
        warnings = [FreshnessWarning(level="warning", code="x", message="m")]
        assert _overall_level(warnings) == "warning"

    def test_any_critical_wins(self):
        warnings = [
            FreshnessWarning(level="warning", code="x", message="m"),
            FreshnessWarning(level="critical", code="y", message="m2"),
        ]
        assert _overall_level(warnings) == "critical"
