"""
tests/test_data_split_guard.py
================================
BET-4: データ分割・リーク防止ロジックの単体テスト。

テスト対象:
  - shared.config の分割境界定数（TRAIN_END_DATE / EVAL_START_DATE）
  - scripts.verify_data_split._race_id_to_date_str
  - scripts.verify_data_split.verify_no_eval_leakage
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.verify_data_split import _race_id_to_date_str, verify_no_eval_leakage
from shared.config import EVAL_START_DATE, TRAIN_END_DATE


# ── 定数の存在・形式確認 ─────────────────────────────────────────────────────


class TestConfigConstants:
    """BET-4: 分割境界定数の存在・形式・順序確認（PLAN.md §5-1 G4）"""

    def test_train_end_date_exists_and_nonempty(self):
        assert TRAIN_END_DATE

    def test_eval_start_date_exists_and_nonempty(self):
        assert EVAL_START_DATE

    def test_train_end_before_eval_start(self):
        """学習終了日 < 検証開始日 であること（時系列リーク防止の前提）"""
        assert TRAIN_END_DATE < EVAL_START_DATE

    def test_expected_split_boundary(self):
        """PLAN.md §5-1 G4 で確定した境界日（2025-05-31 / 2025-06-01）が維持されていること。
        変更する場合は PLAN.md §5-1 G4 との整合確認が必須。
        """
        assert TRAIN_END_DATE == "2025-05-31"
        assert EVAL_START_DATE == "2025-06-01"

    def test_format_is_iso_date(self):
        """YYYY-MM-DD 形式であること"""
        import re
        pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")
        assert pattern.match(TRAIN_END_DATE)
        assert pattern.match(EVAL_START_DATE)


# ── race_id → 日付変換 ────────────────────────────────────────────────────────


class TestRaceIdToDateStr:
    """race_id 先頭8文字から YYYY-MM-DD を生成するロジック"""

    def test_16char_race_id(self):
        # 16桁: kaisai_year(4)+kaisai_monthday(4)+keibajo_code(2)+kaisai_kai(2)+kaisai_nichime(2)+race_num(2)
        assert _race_id_to_date_str("2025053105010101") == "2025-05-31"

    def test_12char_race_id(self):
        # 12桁: kaisai_year(4)+kaisai_monthday(4)+keibajo_code(2)+race_num(2)
        assert _race_id_to_date_str("202506010301") == "2025-06-01"

    def test_train_end_date_boundary(self):
        """TRAIN_END_DATE のレースは学習データとして扱われる（境界は含む）"""
        train_raw = TRAIN_END_DATE.replace("-", "")
        assert _race_id_to_date_str(train_raw + "05010101") == TRAIN_END_DATE

    def test_eval_start_date_boundary(self):
        """EVAL_START_DATE のレースは検証データとして扱われる（境界は含む）"""
        eval_raw = EVAL_START_DATE.replace("-", "")
        assert _race_id_to_date_str(eval_raw + "05010101") == EVAL_START_DATE


# ── verify_no_eval_leakage ───────────────────────────────────────────────────


class TestVerifyNoEvalLeakage:
    """verify_no_eval_leakage の動作確認"""

    def _df(self, race_ids: list[str]) -> pd.DataFrame:
        return pd.DataFrame({"race_id": race_ids})

    def test_clean_training_data_passes(self):
        """学習期間のみのデータはリークなし"""
        df = self._df(["2025053005010101", "2025053105010102"])
        result = verify_no_eval_leakage(df)
        assert result["passed"] is True
        assert result["eval_rows"] == 0

    def test_eval_period_race_detected(self):
        """EVAL_START_DATE 以降の race_id はリークとして検出される"""
        df = self._df(["2025053105010101", "2025060105010101"])
        result = verify_no_eval_leakage(df)
        assert result["passed"] is False
        assert result["eval_rows"] == 1

    def test_train_end_date_boundary_is_not_leakage(self):
        """TRAIN_END_DATE 当日のレースはリークとみなさない（学習データに含めてよい）"""
        train_raw = TRAIN_END_DATE.replace("-", "")
        df = self._df([train_raw + "05010101"])
        result = verify_no_eval_leakage(df)
        assert result["passed"] is True

    def test_eval_start_date_boundary_is_leakage(self):
        """EVAL_START_DATE 当日のレースはリーク（検証データ）"""
        eval_raw = EVAL_START_DATE.replace("-", "")
        df = self._df([eval_raw + "05010101"])
        result = verify_no_eval_leakage(df)
        assert result["passed"] is False
        assert result["eval_rows"] == 1

    def test_empty_dataframe_passes(self):
        df = self._df([])
        result = verify_no_eval_leakage(df)
        assert result["passed"] is True
        assert result["total_rows"] == 0
        assert result["total_races"] == 0

    def test_mixed_periods_counts_correctly(self):
        """学習・検証期間が混在する場合、検証期間の行数が正確にカウントされる"""
        df = self._df([
            "2025053005010101",  # 学習
            "2025053105010102",  # 学習（最終日）
            "2025060105010101",  # 検証（最初の日）→ リーク
            "2025062605010101",  # 検証 → リーク
        ])
        result = verify_no_eval_leakage(df)
        assert result["passed"] is False
        assert result["eval_rows"] == 2
        assert result["total_rows"] == 4

    def test_total_races_counted(self):
        """total_races はユニーク race_id 数"""
        df = self._df(["2025053005010101", "2025053005010101", "2025053105010102"])
        result = verify_no_eval_leakage(df)
        assert result["total_races"] == 2  # 重複 race_id を除いた件数

    def test_leaked_race_ids_sample_capped_at_10(self):
        """leaked_race_ids は最大10件のサンプルを返す"""
        eval_raw = EVAL_START_DATE.replace("-", "")
        race_ids = [f"{eval_raw}050{i:02d}01" for i in range(1, 16)]  # 15件
        df = self._df(race_ids)
        result = verify_no_eval_leakage(df)
        assert result["passed"] is False
        assert len(result["leaked_race_ids"]) <= 10
