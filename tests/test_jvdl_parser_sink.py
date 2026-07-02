"""
tests/test_jvdl_parser_sink.py
================================
BulkSink のユニットテスト。DB 接続不要（psycopg2 をモック）。

テスト観点:
- _build_race_id: 正常系・フィールド欠落・zfill
- _build_upsert: SQL の構造（INSERT / ON CONFLICT / WHERE 鮮度ガード）
- _SinkConf.to_tuple: カラム順抽出・プリプロセッサ適用
- BulkSink.feed: バッファリング・BATCH 自動フラッシュ
- BulkSink.flush: execute_values 呼び出し・commit・戻り値
- BulkSink.pending: バッファ残数確認
- _prep_training: chokyo_time None → '0000'
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from jvdl_parser.sink import (
    BATCH,
    BulkSink,
    _SinkConf,
    _build_race_id,
    _build_upsert,
    _HANDLERS,
    _HR_BET_NAMES,
    _identity,
    _prep_payout,
    _prep_training,
    _with_race_id,
    build_payout_race_id,
)


# ── _build_race_id ─────────────────────────────────────────────────────────────

class TestBuildRaceId:
    def test_full_row(self):
        row = {
            "kaisai_year":     "2026",
            "kaisai_monthday": "0607",
            "keibajo_code":    "05",
            "kaisai_kai":      "01",
            "kaisai_nichime":  "01",
            "race_num":        "11",
        }
        assert _build_race_id(row) == "2026060705010111"

    def test_single_digit_fields_are_zfilled(self):
        row = {
            "kaisai_year":     "2026",
            "kaisai_monthday": "0607",
            "keibajo_code":    "5",    # 1桁 → zfill(2)
            "kaisai_kai":      "1",
            "kaisai_nichime":  "1",
            "race_num":        "1",
        }
        result = _build_race_id(row)
        assert result == "2026060705010101"

    def test_missing_fields_become_empty(self):
        row = {
            "kaisai_year":     "2026",
            "kaisai_monthday": "0607",
            # keibajo_code など欠落
        }
        result = _build_race_id(row)
        assert result.startswith("20260607")
        assert len(result) == 16  # 常に 16 文字の連結構造

    def test_none_fields_become_empty(self):
        row = {
            "kaisai_year":     None,
            "kaisai_monthday": "0101",
            "keibajo_code":    "01",
            "kaisai_kai":      "01",
            "kaisai_nichime":  "01",
            "race_num":        "01",
        }
        result = _build_race_id(row)
        assert result == "0101010101010101"[4:]  # kaisai_year 欠落で先頭が空
        assert "0101" in result


# ── _build_upsert ──────────────────────────────────────────────────────────────

class TestBuildUpsert:
    def _sql(self):
        return _build_upsert(
            table="races_v2",
            columns=("race_id", "grade_code", "data_kubun", "data_create_date"),
            pkey=("race_id",),
        )

    def test_contains_insert_into(self):
        sql = self._sql()
        assert "INSERT INTO races_v2 AS t" in sql

    def test_contains_values_placeholder(self):
        assert "VALUES %s" in self._sql()

    def test_contains_on_conflict(self):
        sql = self._sql()
        assert "ON CONFLICT (race_id)" in sql

    def test_non_pkey_cols_in_update_set(self):
        sql = self._sql()
        assert "grade_code = EXCLUDED.grade_code" in sql
        assert "data_kubun = EXCLUDED.data_kubun" in sql

    def test_pkey_not_in_update_set(self):
        sql = self._sql()
        # pkey は UPDATE SET に含まれない
        assert "race_id = EXCLUDED.race_id" not in sql

    def test_loaded_at_updated(self):
        assert "loaded_at = now()" in self._sql()

    def test_freshness_guard_where_clause(self):
        sql = self._sql()
        assert "EXCLUDED.data_create_date" in sql
        assert "EXCLUDED.data_kubun" in sql
        assert ">=" in sql
        assert "t.data_create_date" in sql

    def test_composite_pkey(self):
        sql = _build_upsert(
            table="race_entries_v2",
            columns=("race_id", "umaban", "horse_name", "data_kubun", "data_create_date"),
            pkey=("race_id", "umaban"),
        )
        assert "ON CONFLICT (race_id, umaban)" in sql
        assert "race_id = EXCLUDED.race_id" not in sql
        assert "umaban = EXCLUDED.umaban" not in sql
        assert "horse_name = EXCLUDED.horse_name" in sql


# ── _SinkConf.to_tuple ─────────────────────────────────────────────────────────

class TestSinkConfToTuple:
    def test_ra_to_tuple_adds_race_id(self):
        conf = _HANDLERS["RA"]
        row = {
            "kaisai_year": "2026", "kaisai_monthday": "0608",
            "keibajo_code": "05", "kaisai_kai": "01",
            "kaisai_nichime": "02", "race_num": "05",
            "grade_code": "A", "data_kubun": "7", "data_create_date": "20260608",
        }
        tup = conf.to_tuple(row)
        assert tup[0] == "2026060805010205"      # race_id が先頭
        assert len(tup) == len(conf.columns)

    def test_we_to_tuple_no_race_id(self):
        conf = _HANDLERS["WE"]
        row = {
            "keibajo_code": "05", "kaisai_year": "2026",
            "kaisai_monthday": "0608", "kaisai_nichime": "01",
            "happyo_monthday_time": "06081000",
            "tenko_code": "1", "data_kubun": "1", "data_create_date": "20260608",
        }
        tup = conf.to_tuple(row)
        assert tup[0] == "05"   # keibajo_code が先頭
        assert len(tup) == len(conf.columns)

    def test_hc_to_tuple_chokyo_time_default(self):
        conf = _HANDLERS["HC"]
        row = {
            "blood_no": "0000001234", "chokyo_date": "20260608",
            "center_cd": "0",
            "chokyo_time": None,   # 空 → '0000' に変換される
            "time_4f": 65.3, "data_kubun": "1", "data_create_date": "20260608",
        }
        tup = conf.to_tuple(row)
        chokyo_time_idx = conf.columns.index("chokyo_time")
        assert tup[chokyo_time_idx] == "0000"

    def test_wc_to_tuple_has_full_lap_columns(self):
        conf = _HANDLERS["WC"]
        assert "time_10f" in conf.columns
        assert "lap_l10_l9" in conf.columns
        assert "lap_l1" in conf.columns
        assert len(conf.columns) > 20   # 9 タイム + 10 ラップ + key/provenance

    def test_missing_columns_become_none(self):
        conf = _HANDLERS["RA"]
        row = {
            "kaisai_year": "2026", "kaisai_monthday": "0101",
            "keibajo_code": "01", "kaisai_kai": "01",
            "kaisai_nichime": "01", "race_num": "01",
            "data_kubun": "1", "data_create_date": "20260101",
            # grade_code etc. 欠落
        }
        tup = conf.to_tuple(row)
        grade_idx = conf.columns.index("grade_code")
        assert tup[grade_idx] is None


# ── _prep_training ─────────────────────────────────────────────────────────────

class TestPrepTraining:
    def test_none_becomes_default(self):
        result = _prep_training({"chokyo_time": None, "blood_no": "abc"})
        assert result["chokyo_time"] == "0000"

    def test_empty_str_becomes_default(self):
        result = _prep_training({"chokyo_time": "", "blood_no": "abc"})
        assert result["chokyo_time"] == "0000"

    def test_existing_value_preserved(self):
        result = _prep_training({"chokyo_time": "0930", "blood_no": "abc"})
        assert result["chokyo_time"] == "0930"

    def test_original_row_not_mutated(self):
        row = {"chokyo_time": None, "blood_no": "abc"}
        _prep_training(row)
        assert row["chokyo_time"] is None   # 元の dict は変更されない（鉄則: immutable）


# ── BulkSink ──────────────────────────────────────────────────────────────────

def _make_mock_conn():
    conn = MagicMock()
    cursor_cm = MagicMock()
    cursor_cm.__enter__ = MagicMock(return_value=cursor_cm)
    cursor_cm.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = cursor_cm
    return conn, cursor_cm


def _ra_row(**kwargs) -> dict:
    base = {
        "kaisai_year": "2026", "kaisai_monthday": "0608",
        "keibajo_code": "05", "kaisai_kai": "01",
        "kaisai_nichime": "01", "race_num": "01",
        "grade_code": "A", "data_kubun": "7", "data_create_date": "20260608",
    }
    return {**base, **kwargs}


class TestBulkSinkBuffering:
    def test_feed_unknown_type_is_ignored(self):
        conn, _ = _make_mock_conn()
        sink = BulkSink(conn)
        sink.feed("XX", {"foo": "bar"})   # 未知種別
        assert sink.pending() == {}

    def test_feed_known_type_buffers(self):
        conn, _ = _make_mock_conn()
        sink = BulkSink(conn)
        sink.feed("RA", _ra_row())
        assert sink.pending() == {"RA": 1}

    def test_feed_multiple_types_buffered_independently(self):
        conn, _ = _make_mock_conn()
        sink = BulkSink(conn)
        sink.feed("RA", _ra_row())
        sink.feed("RA", _ra_row(race_num="02"))
        sink.feed("SE", {**_ra_row(), "umaban": 1, "blood_no": "001"})
        assert sink.pending()["RA"] == 2
        assert sink.pending()["SE"] == 1

    def test_auto_flush_at_batch_size(self):
        conn, cur = _make_mock_conn()
        sink = BulkSink(conn)
        with patch("psycopg2.extras.execute_values") as mock_ev:
            for i in range(BATCH):
                sink.feed("RA", _ra_row(race_num=str(i).zfill(2)))
            # BATCH 件目で自動フラッシュされている
            mock_ev.assert_called_once()
        # バッファは空になっている
        assert sink.pending().get("RA", 0) == 0


class TestBulkSinkFlush:
    def test_flush_calls_execute_values(self):
        conn, cur = _make_mock_conn()
        sink = BulkSink(conn)
        sink.feed("RA", _ra_row())
        sink.feed("RA", _ra_row(race_num="02"))

        with patch("psycopg2.extras.execute_values") as mock_ev:
            counts = sink.flush()

        mock_ev.assert_called_once()
        args = mock_ev.call_args
        rows_arg = args[0][2]
        assert len(rows_arg) == 2

    def test_flush_commits(self):
        conn, _ = _make_mock_conn()
        sink = BulkSink(conn)
        sink.feed("RA", _ra_row())
        with patch("psycopg2.extras.execute_values"):
            sink.flush()
        conn.commit.assert_called_once()

    def test_flush_returns_counts(self):
        conn, _ = _make_mock_conn()
        sink = BulkSink(conn)
        sink.feed("RA", _ra_row())
        sink.feed("RA", _ra_row(race_num="02"))
        with patch("psycopg2.extras.execute_values"):
            counts = sink.flush()
        assert counts == {"RA": 2}

    def test_flush_clears_buffer(self):
        conn, _ = _make_mock_conn()
        sink = BulkSink(conn)
        sink.feed("RA", _ra_row())
        with patch("psycopg2.extras.execute_values"):
            sink.flush()
        assert sink.pending() == {}

    def test_flush_empty_buffer_still_commits(self):
        conn, _ = _make_mock_conn()
        sink = BulkSink(conn)
        with patch("psycopg2.extras.execute_values") as mock_ev:
            counts = sink.flush()
        mock_ev.assert_not_called()
        conn.commit.assert_called_once()
        assert counts == {}

    def test_flush_multiple_types(self):
        conn, _ = _make_mock_conn()
        sink = BulkSink(conn)
        we_row = {
            "keibajo_code": "05", "kaisai_year": "2026",
            "kaisai_monthday": "0608", "kaisai_nichime": "01",
            "happyo_monthday_time": "06081000",
            "data_kubun": "1", "data_create_date": "20260608",
        }
        sink.feed("RA", _ra_row())
        sink.feed("WE", we_row)

        with patch("psycopg2.extras.execute_values") as mock_ev:
            counts = sink.flush()

        assert mock_ev.call_count == 2
        assert counts.get("RA") == 1
        assert counts.get("WE") == 1

    def test_flush_passes_upsert_sql_to_execute_values(self):
        conn, _ = _make_mock_conn()
        sink = BulkSink(conn)
        sink.feed("HC", {
            "blood_no": "0000001234", "chokyo_date": "20260608",
            "center_cd": "0", "chokyo_time": "0900",
            "time_4f": 65.3, "data_kubun": "1", "data_create_date": "20260608",
        })
        with patch("psycopg2.extras.execute_values") as mock_ev:
            sink.flush()
        sql_passed = mock_ev.call_args[0][1]
        assert "INSERT INTO training_slope" in sql_passed
        assert "ON CONFLICT" in sql_passed
        assert ">=" in sql_passed


# ── _HANDLERS integrity ────────────────────────────────────────────────────────

class TestHandlersIntegrity:
    @pytest.mark.parametrize("rtype", ["RA", "SE", "WE", "AV", "JC", "TC", "CC", "HC", "WC"])
    def test_handler_exists(self, rtype):
        assert rtype in _HANDLERS

    @pytest.mark.parametrize("rtype", ["RA", "SE", "WE", "AV", "JC", "TC", "CC", "HC", "WC"])
    def test_pkey_is_subset_of_columns(self, rtype):
        conf = _HANDLERS[rtype]
        for pk_col in conf.pkey:
            assert pk_col in conf.columns, f"{rtype}: pkey column '{pk_col}' not in columns"

    @pytest.mark.parametrize("rtype", ["RA", "SE", "AV", "JC", "TC", "CC"])
    def test_race_id_handlers_use_with_race_id_preprocessor(self, rtype):
        conf = _HANDLERS[rtype]
        test_row = {
            "kaisai_year": "2026", "kaisai_monthday": "0101",
            "keibajo_code": "01", "kaisai_kai": "01",
            "kaisai_nichime": "01", "race_num": "01",
        }
        enriched = conf.preprocessor(test_row)
        assert "race_id" in enriched

    @pytest.mark.parametrize("rtype", ["RA", "SE", "AV", "JC", "TC", "CC"])
    def test_provenance_cols_present(self, rtype):
        conf = _HANDLERS[rtype]
        assert "data_kubun" in conf.columns
        assert "data_create_date" in conf.columns

    @pytest.mark.parametrize("rtype", ["HC", "WC"])
    def test_training_handlers_use_prep_training(self, rtype):
        conf = _HANDLERS[rtype]
        row = {"blood_no": "x", "chokyo_date": "20260608", "chokyo_time": None,
               "center_cd": "0", "data_kubun": "1", "data_create_date": "20260608"}
        enriched = conf.preprocessor(row)
        assert enriched["chokyo_time"] == "0000"

    def test_wc_has_more_lap_columns_than_hc(self):
        hc_cols = set(_HANDLERS["HC"].columns)
        wc_cols = set(_HANDLERS["WC"].columns)
        wc_only = wc_cols - hc_cols
        assert any("l10" in c or "l9" in c or "l8" in c for c in wc_only)


# ── build_payout_race_id ───────────────────────────────────────────────────────

class TestBuildPayoutRaceId:
    """PLAN.md §1-1 確定変換式の検証: 12桁、kaisai_kai/kaisai_nichime を含まない。"""

    def test_returns_12_chars(self):
        row = {
            "kaisai_year": "2026", "kaisai_monthday": "0621",
            "keibajo_code": "05", "kaisai_kai": "01",
            "kaisai_nichime": "01", "race_num": "03",
        }
        assert len(build_payout_race_id(row)) == 12

    def test_excludes_kaisai_kai_and_nichime(self):
        row_a = {
            "kaisai_year": "2026", "kaisai_monthday": "0621",
            "keibajo_code": "05", "kaisai_kai": "01",
            "kaisai_nichime": "01", "race_num": "03",
        }
        row_b = {**row_a, "kaisai_kai": "02", "kaisai_nichime": "03"}
        # 開催回・日目が異なっても同日同場なら同じ12桁になる
        assert build_payout_race_id(row_a) == build_payout_race_id(row_b)

    def test_format_matches_existing_payouts_race_id(self):
        # 既存データ例: '202604050912' = 2026年04月05日・場コード09・レース12
        row = {
            "kaisai_year": "2026", "kaisai_monthday": "0405",
            "keibajo_code": "09", "race_num": "12",
        }
        assert build_payout_race_id(row) == "202604050912"

    def test_differs_from_16char_race_id(self):
        row = {
            "kaisai_year": "2026", "kaisai_monthday": "0621",
            "keibajo_code": "05", "kaisai_kai": "01",
            "kaisai_nichime": "01", "race_num": "03",
        }
        assert build_payout_race_id(row) != _build_race_id(row)
        assert len(_build_race_id(row)) == 16


# ── _HR_BET_NAMES / _prep_payout ───────────────────────────────────────────────

def _hr_base_row(**kwargs) -> dict:
    base = {
        "kaisai_year": "2026", "kaisai_monthday": "0621",
        "keibajo_code": "05", "kaisai_kai": "01",
        "kaisai_nichime": "01", "race_num": "03",
        "bet_type": 1,
        "combo_key": "11",
        "popularity_rank": 7,
        "payout": 1510,
    }
    return {**base, **kwargs}


class TestHRBetNames:
    def test_all_eight_bet_types_covered(self):
        expected = {
            1: "tansho", 2: "fukusho", 3: "wakuren",
            4: "umaren", 5: "wide", 6: "umatan",
            7: "sanrenpuku", 8: "sanrentan",
        }
        assert _HR_BET_NAMES == expected

    def test_no_extra_keys(self):
        assert len(_HR_BET_NAMES) == 8


class TestPrepPayout:
    def test_bet_type_int_to_text_tansho(self):
        result = _prep_payout(_hr_base_row(bet_type=1))
        assert result["bet_type"] == "tansho"

    def test_bet_type_int_to_text_wide(self):
        result = _prep_payout(_hr_base_row(bet_type=5, combo_key="0306"))
        assert result["bet_type"] == "wide"

    def test_bet_type_int_to_text_sanrenpuku(self):
        result = _prep_payout(_hr_base_row(bet_type=7, combo_key="060911"))
        assert result["bet_type"] == "sanrenpuku"

    def test_combo_key_becomes_combination(self):
        result = _prep_payout(_hr_base_row(combo_key="0611"))
        assert result["combination"] == "0611"

    def test_popularity_rank_becomes_popularity(self):
        result = _prep_payout(_hr_base_row(popularity_rank=3))
        assert result["popularity"] == 3

    def test_race_id_generated(self):
        # payouts テーブルは 12 桁 race_id（kaisai_kai/kaisai_nichime を含まない）
        # PLAN.md §1-1: kaisai_year(4)+kaisai_monthday(4)+keibajo_code(2)+race_num(2)
        result = _prep_payout(_hr_base_row())
        assert "race_id" in result
        assert result["race_id"] == "202606210503"

    def test_original_row_not_mutated(self):
        row = _hr_base_row(bet_type=1)
        original_bet = row["bet_type"]
        _prep_payout(row)
        assert row["bet_type"] == original_bet  # int 値が元のまま


# ── HR_PAYOUT ハンドラ整合性 ────────────────────────────────────────────────────

class TestHRPayoutHandler:
    def test_handler_exists(self):
        assert "HR_PAYOUT" in _HANDLERS

    def test_pkey_is_subset_of_columns(self):
        conf = _HANDLERS["HR_PAYOUT"]
        for k in conf.pkey:
            assert k in conf.columns, f"HR_PAYOUT: pkey '{k}' not in columns"

    def test_columns_match_existing_schema(self):
        conf = _HANDLERS["HR_PAYOUT"]
        assert set(conf.columns) == {"race_id", "bet_type", "combination", "payout", "popularity"}

    def test_sql_override_used_instead_of_build_upsert(self):
        conf = _HANDLERS["HR_PAYOUT"]
        sql = conf.upsert_sql
        assert "ON CONFLICT ON CONSTRAINT payouts_race_bet_combo_key" in sql
        assert "INSERT INTO payouts" in sql
        # 鮮度ガード（data_kubun/data_create_date）は含まない
        assert "data_kubun" not in sql
        assert "loaded_at = now()" not in sql

    def test_to_tuple_order_and_conversion(self):
        conf = _HANDLERS["HR_PAYOUT"]
        row = _hr_base_row(bet_type=2, combo_key="06", popularity_rank=4, payout=470)
        tup = conf.to_tuple(row)
        assert len(tup) == 5
        idx = {c: i for i, c in enumerate(conf.columns)}
        assert tup[idx["bet_type"]]    == "fukusho"
        assert tup[idx["combination"]] == "06"
        assert tup[idx["payout"]]      == 470
        assert tup[idx["popularity"]]  == 4
        assert len(tup[idx["race_id"]]) == 12  # payouts は 12 桁（kaisai_kai/nichime 除外）

    def test_flush_uses_sql_override(self):
        """BulkSink経由でHR_PAYOUTをflushすると sql_override の SQL が execute_values に渡る。"""
        from unittest.mock import MagicMock, patch
        conn = MagicMock()
        cur_cm = MagicMock()
        cur_cm.__enter__ = MagicMock(return_value=cur_cm)
        cur_cm.__exit__ = MagicMock(return_value=False)
        conn.cursor.return_value = cur_cm

        sink = BulkSink(conn)
        sink.feed("HR_PAYOUT", _hr_base_row())

        with patch("psycopg2.extras.execute_values") as mock_ev:
            sink.flush()

        sql_passed = mock_ev.call_args[0][1]
        assert "ON CONFLICT ON CONSTRAINT payouts_race_bet_combo_key" in sql_passed


# ── SE (race_entries_v2) umaban=0 重複除去バグの回帰テスト ──────────────────────
# 2026-07-03: 木曜配信の出走馬名表(SEレコード)は枠番・馬番確定前で全頭 umaban=0。
# pkey が (race_id, umaban) だった当時、BulkSink._flush_type のバッチ内PK重複
# 除去(last-wins)により1レース16頭中15頭が消失する重大バグがあった。
# 実地検証(合成データ16頭)で execute_values 到達が1行のみになることを確認し、
# pkey を (race_id, blood_no, umaban) に修正した。本テストは同シナリオの回帰確認。

def _se_row(**kwargs) -> dict:
    base = {
        "kaisai_year": "2026", "kaisai_monthday": "0704",
        "keibajo_code": "05", "kaisai_kai": "01",
        "kaisai_nichime": "01", "race_num": "01",
        "umaban": 0, "wakuban": 0,
        "blood_no": "2023100001", "horse_name": "テストウマ",
        "sex_cd": "1", "horse_age": 3, "chokyosi_code": "00000",
        "kinryo": 550, "blinker": 0, "kishu_code": "00000",
        "horse_weight": 480, "zogen_fugo": " ", "zogen_sa": 0, "ijyo_kubun": "0",
        "nyusen_juni": 0, "kakutei_chakujun": 0, "race_time": "0000",
        "corner_1": 0, "corner_2": 0, "corner_3": 0, "corner_4": 0,
        "tansho_odds": 0, "tansho_ninki": 0,
        "kohan_4f": "000", "kohan_3f": "000",
        "data_kubun": "1", "data_create_date": "20260703",
    }
    return {**base, **kwargs}


class TestSeUmabanZeroDedupRegression:
    def test_se_pkey_includes_blood_no(self):
        """SEハンドラのpkeyにblood_noが含まれること(umaban単独キーへの後退防止)。"""
        conf = _HANDLERS["SE"]
        assert conf.pkey == ("race_id", "blood_no", "umaban")

    def test_16_horses_all_umaban_zero_all_survive(self):
        """木曜出走馬名表を模した16頭(全員umaban=0, blood_no違い)が
        BulkSinkのバッチ内デデュープで消失せず全頭 execute_values に渡ること。"""
        conn, _ = _make_mock_conn()
        sink = BulkSink(conn)
        for i in range(16):
            sink.feed("SE", _se_row(blood_no=f"202600000{i:03d}", horse_name=f"テストウマ{i:02d}"))

        with patch("psycopg2.extras.execute_values") as mock_ev:
            counts = sink.flush()

        assert counts == {"SE": 16}
        rows_arg = mock_ev.call_args[0][2]
        assert len(rows_arg) == 16
        blood_no_idx = _HANDLERS["SE"].columns.index("blood_no")
        blood_nos = {row[blood_no_idx] for row in rows_arg}
        assert len(blood_nos) == 16, "重複除去で頭数が減っている"

    def test_same_horse_umaban_0_then_confirmed_are_different_pk(self):
        """同一馬(同一blood_no)が umaban=0(木曜) -> umaban確定(金曜)と2バッチに
        分かれて配信された場合、PKが異なるため別行としてUPSERTされる
        (上書きにはならない。既存DBに umaban=0 の残骸が残る挙動の実証)。"""
        conn, _ = _make_mock_conn()
        sink = BulkSink(conn)

        sink.feed("SE", _se_row(umaban=0, blood_no="2023100001", data_kubun="1", data_create_date="20260703"))
        with patch("psycopg2.extras.execute_values") as mock_ev1:
            sink.flush()
        row1 = mock_ev1.call_args[0][2][0]

        sink.feed("SE", _se_row(umaban=5, blood_no="2023100001", data_kubun="2", data_create_date="20260704"))
        with patch("psycopg2.extras.execute_values") as mock_ev2:
            sink.flush()
        row2 = mock_ev2.call_args[0][2][0]

        umaban_idx = _HANDLERS["SE"].columns.index("umaban")
        assert row1[umaban_idx] == 0
        assert row2[umaban_idx] == 5  # 別行として追加される(PKが異なるため)

    def test_field_size_of_16_matches_shusso_tosu_scenario(self):
        """1レース分の合成データで、投入頭数がそのままDB到達件数と一致すること
        (実DB検証: races_v2.shusso_tosu と race_entries_v2 の実件数比較の代替)。"""
        conn, _ = _make_mock_conn()
        sink = BulkSink(conn)
        shusso_tosu = 18  # フルゲート想定
        for i in range(shusso_tosu):
            sink.feed("SE", _se_row(blood_no=f"20231000{i:02d}", horse_name=f"馬{i:02d}"))
        with patch("psycopg2.extras.execute_values") as mock_ev:
            counts = sink.flush()
        assert counts["SE"] == shusso_tosu
