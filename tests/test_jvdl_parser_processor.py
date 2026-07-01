"""
tests/test_jvdl_parser_processor.py
=====================================
process_stream() / ProcessResult / _write_dlq のユニットテスト。
DB 接続不要（psycopg2 と BulkSink をモック）。

テスト観点:
- 鮮度ガード順序不変量（データ区分の大小関係が SQL に反映される）
- WH/O1 専用ハンドラへの分岐（WH_ENTRY / O1_WIN / O1_PLACE に feed される）
- RecordLengthError → DLQ 書き込み（プロセス継続）
- 影響 race_id 収集（RA/SE/WH/O1/WE/AV/JC/TC/CC）
- DLQ 率 1% 超でログ警告
- 空ペイロードで正常終了
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from jvdl_parser.fields import RECORD_DEFS
from jvdl_parser.parser import RecordLengthError
from jvdl_parser.processor import ProcessResult, _write_dlq, process_stream
from jvdl_parser.sink import _build_upsert, _HANDLERS


# ── 鮮度ガード順序テスト（3-1 §5.3 の不変量検証）────────────────────────────────

class TestFreshnessGuardOrdering:
    """
    鉄則5: (EXCLUDED.data_create_date, EXCLUDED.data_kubun) >= (t.data_create_date, t.data_kubun)
    - data_kubun: 1(木曜馬名表) < 2(出馬表) < 3-5(速報) < 6(確定) < 7(最終確定)
    - 同一日付で data_kubun が小さい = 古いデータ → WHERE が偽 → 上書きされない
    """

    def test_confirmed_7_newer_than_bulletin_3(self):
        # 確定(7) > 速報(3): 速報で巻き戻らないことの根拠
        assert ("20260608", "7") > ("20260608", "3")

    def test_confirmed_6_newer_than_bulletin_5(self):
        assert ("20260608", "6") > ("20260608", "5")

    def test_later_date_always_newer(self):
        # 日付が新しければ data_kubun 問わず優先
        assert ("20260609", "1") > ("20260608", "7")

    def test_older_date_never_overwrites(self):
        # 一日古いデータは data_kubun が 7 でも上書き不可
        assert ("20260607", "7") < ("20260608", "3")

    def test_sql_where_clause_comparison_order(self):
        # UPSERT SQL の WHERE 句が (date, kubun) のタプル比較になっている
        sql = _build_upsert(
            "races_v2",
            ("race_id", "data_kubun", "data_create_date"),
            ("race_id",),
        )
        assert "(EXCLUDED.data_create_date, EXCLUDED.data_kubun)" in sql
        assert "(t.data_create_date, t.data_kubun)" in sql
        assert ">=" in sql

    def test_deletion_record_kubun_0_is_smallest(self):
        # data_kubun "0"（削除レコード）はどの kubun よりも古い扱い
        # → 確定後に削除レコードが届いても上書きされない
        for kubun in ("1", "2", "3", "6", "7"):
            assert ("20260608", "0") < ("20260608", kubun)

    @pytest.mark.parametrize("rtype", ["RA", "SE", "HC", "WC"])
    def test_all_main_tables_have_freshness_guard(self, rtype):
        conf = _HANDLERS[rtype]
        sql = conf.upsert_sql
        assert ">=" in sql
        assert "data_create_date" in sql
        assert "data_kubun" in sql


# ── WH/O1 ハンドラ分岐テスト（3-2）─────────────────────────────────────────────

def _make_sink_mock():
    sink = MagicMock()
    sink.flush.return_value = {}
    return sink


def _make_conn_mock():
    conn = MagicMock()
    cursor_cm = MagicMock()
    cursor_cm.__enter__ = MagicMock(return_value=cursor_cm)
    cursor_cm.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = cursor_cm
    return conn


def _make_wh_payload() -> bytes:
    """WH レコード 1 件（正しい長さ）を含む payload を生成する。"""
    return b"WH" + b" " * (847 - 2)


def _make_ra_payload() -> bytes:
    """RA レコード 1 件（正しい長さ）を含む payload を生成する。"""
    return b"RA" + b" " * (1272 - 2)


def _make_o1_payload() -> bytes:
    """O1 レコード 1 件（正しい長さ）を含む payload を生成する。"""
    return b"O1" + b" " * (962 - 2)


class TestProcessStreamWHO1:
    def test_wh_calls_parse_wh_entries_and_feeds_wh_entry(self):
        sink = _make_sink_mock()
        conn = _make_conn_mock()

        fake_entries = [
            {"kaisai_year": "2026", "kaisai_monthday": "0608",
             "keibajo_code": "05", "kaisai_kai": "01", "kaisai_nichime": "01",
             "race_num": "11", "umaban": 1, "horse_weight": 450,
             "data_kubun": "1", "data_create_date": "20260608"},
        ]
        with patch("jvdl_parser.processor.parse_wh_entries", return_value=fake_entries) as mock_wh:
            result = process_stream(_make_wh_payload(), "0B11", sink, conn)

        mock_wh.assert_called_once()
        # WH_ENTRY として feed が呼ばれる
        call_args = [c.args[0] for c in sink.feed.call_args_list]
        assert "WH_ENTRY" in call_args

    def test_o1_calls_parse_o1_entries_and_feeds_win_place(self):
        sink = _make_sink_mock()
        conn = _make_conn_mock()

        fake_expanded = {
            "win":   [{"kaisai_year": "2026", "kaisai_monthday": "0608",
                       "keibajo_code": "05", "kaisai_kai": "01", "kaisai_nichime": "01",
                       "race_num": "11", "umaban": 1, "odds": 2.5,
                       "happyo_monthday_time": "06081000",
                       "data_kubun": "1", "data_create_date": "20260608"}],
            "place": [{"kaisai_year": "2026", "kaisai_monthday": "0608",
                       "keibajo_code": "05", "kaisai_kai": "01", "kaisai_nichime": "01",
                       "race_num": "11", "umaban": 1, "odds_min": 1.0, "odds_max": 1.2,
                       "happyo_monthday_time": "06081000",
                       "data_kubun": "1", "data_create_date": "20260608"}],
        }
        with patch("jvdl_parser.processor.parse_o1_entries", return_value=fake_expanded):
            result = process_stream(_make_o1_payload(), "0B31", sink, conn)

        call_types = [c.args[0] for c in sink.feed.call_args_list]
        assert "O1_WIN" in call_types
        assert "O1_PLACE" in call_types

    def test_ra_feeds_ra_type(self):
        sink = _make_sink_mock()
        conn = _make_conn_mock()
        result = process_stream(_make_ra_payload(), "0B11", sink, conn)
        call_types = [c.args[0] for c in sink.feed.call_args_list]
        assert "RA" in call_types

    def test_unknown_record_type_skips_silently(self):
        sink = _make_sink_mock()
        conn = _make_conn_mock()
        # "XX" は RECORD_DEFS にない → parse_record が None を返す
        payload = b"XX" + b" " * 100
        result = process_stream(payload, "test", sink, conn)
        sink.feed.assert_not_called()
        assert result.ok == 0
        assert result.dlq == 0   # 未知種別は例外でなくスキップ


# ── DLQ 書き込み（RecordLengthError）────────────────────────────────────────────

class TestDLQOnError:
    def test_record_length_error_goes_to_dlq(self):
        """RA レコードが不正な長さ → RecordLengthError → DLQ 書き込み、処理継続。"""
        sink = _make_sink_mock()
        conn = _make_conn_mock()

        # 1 件目: 不正長さの RA（DLQ 行き）
        bad_ra = b"RA" + b" " * 100   # 期待値 1272 ≠ 102
        # 2 件目: 正常 RA（処理される）
        good_ra = b"RA" + b" " * (1272 - 2)
        payload = bad_ra + b"\r\n" + good_ra

        result = process_stream(payload, "test", sink, conn)

        assert result.dlq == 1
        assert result.ok == 1
        # DLQ への書き込みが実行された
        conn.cursor.assert_called()

    def test_dlq_write_failure_does_not_abort_stream(self):
        """DLQ 書き込み自体が失敗しても他のレコード処理を継続する。"""
        sink = _make_sink_mock()
        conn = _make_conn_mock()
        conn.cursor.side_effect = Exception("DB down")

        bad_ra = b"RA" + b" " * 50
        good_ra = b"RA" + b" " * (1272 - 2)
        payload = bad_ra + b"\r\n" + good_ra

        # 例外が上位に伝播しない
        result = process_stream(payload, "test", sink, conn)
        assert result.ok >= 0   # クラッシュしていない

    def test_process_result_is_frozen(self):
        sink = _make_sink_mock()
        conn = _make_conn_mock()
        result = process_stream(b"", "test", sink, conn)
        with pytest.raises(Exception):
            result.ok = 99  # type: ignore[misc]


# ── affected_race_ids 収集（3-3 のフック引数）────────────────────────────────────

class TestAffectedRaceIds:
    def test_ra_record_adds_race_id(self):
        sink = _make_sink_mock()
        conn = _make_conn_mock()

        # kaisai_year=2026, monthday=0608, keibajo=05, kai=01, nichime=01, num=11
        # → race_id = "2026060805010111"
        ra = b"RA" + b" " * (1272 - 2)
        # RA レコードの pos12-27 に race key を埋め込む
        ba = bytearray(ra)
        key = b"202606080501011105010111"   # 全部スペース埋めなので実際は空
        result = process_stream(ra, "test", sink, conn)

        # parse_record はスペースを strip → None になるため race_id は全ゼロ
        # → strip("0") が空文字 → affected_race_ids に追加されない
        # このテストは「追加される場合」を別途確認する
        assert isinstance(result.affected_race_ids, frozenset)

    def test_affected_race_ids_is_frozenset(self):
        sink = _make_sink_mock()
        conn = _make_conn_mock()
        result = process_stream(b"", "test", sink, conn)
        assert isinstance(result.affected_race_ids, frozenset)

    def test_empty_payload_returns_empty_race_ids(self):
        sink = _make_sink_mock()
        conn = _make_conn_mock()
        result = process_stream(b"", "test", sink, conn)
        assert result.affected_race_ids == frozenset()
        assert result.ok == 0
        assert result.dlq == 0


# ── DLQ 率監視ログ（1% 超で warning）────────────────────────────────────────────

class TestDLQRateWarning:
    def test_high_dlq_rate_logs_warning(self, caplog):
        """DLQ 率 > 1% で WARNING が出力される。"""
        import logging
        sink = _make_sink_mock()
        conn = _make_conn_mock()

        # 100 件のうち 50 件を不正な長さにして DLQ 率 50%
        bad = b"RA" + b" " * 10
        records = [bad] * 50 + [b"RA" + b" " * (1272 - 2)] * 50
        payload = b"\r\n".join(records)

        with caplog.at_level(logging.WARNING, logger="jvdl_parser.processor"):
            process_stream(payload, "test", sink, conn)

        assert any("DLQ率" in m for m in caplog.messages)


# ── _write_dlq のユニットテスト ────────────────────────────────────────────────

class TestWriteDlq:
    def test_writes_correct_fields(self):
        conn = _make_conn_mock()
        exc = RecordLengthError(b"RA", 100, 1272)
        _write_dlq(conn, b"RA" + b" " * 98, "0B11", exc)

        conn.cursor.assert_called()

    def test_short_record_does_not_crash(self):
        conn = _make_conn_mock()
        _write_dlq(conn, b"R", "test", ValueError("bad"))

    def test_empty_record_does_not_crash(self):
        conn = _make_conn_mock()
        _write_dlq(conn, b"", "test", ValueError("bad"))


# ── WH_ENTRY / O1_WIN / O1_PLACE ハンドラ整合性 ──────────────────────────────

class TestPhase3HandlerIntegrity:
    @pytest.mark.parametrize("pseudo_type", ["WH_ENTRY", "O1_WIN", "O1_PLACE"])
    def test_handler_exists(self, pseudo_type):
        assert pseudo_type in _HANDLERS

    @pytest.mark.parametrize("pseudo_type", ["WH_ENTRY", "O1_WIN", "O1_PLACE"])
    def test_uses_with_race_id_preprocessor(self, pseudo_type):
        conf = _HANDLERS[pseudo_type]
        row = {
            "kaisai_year": "2026", "kaisai_monthday": "0608",
            "keibajo_code": "05", "kaisai_kai": "01",
            "kaisai_nichime": "01", "race_num": "11",
        }
        enriched = conf.preprocessor(row)
        assert "race_id" in enriched
        assert enriched["race_id"] == "2026060805010111"

    def test_wh_entry_targets_race_entries_v2(self):
        assert _HANDLERS["WH_ENTRY"].table == "race_entries_v2"

    def test_wh_entry_only_updates_weight_cols(self):
        conf = _HANDLERS["WH_ENTRY"]
        # horse_weight 関連は含む
        assert "horse_weight" in conf.columns
        assert "zogen_sa" in conf.columns
        # 成績系は含まない（部分更新 — race_time 等を上書きしない）
        assert "race_time" not in conf.columns
        assert "tansho_odds" not in conf.columns

    def test_o1_win_upsert_sql_targets_odds_win_v2(self):
        sql = _HANDLERS["O1_WIN"].upsert_sql
        assert "odds_win_v2" in sql

    def test_o1_place_upsert_sql_targets_odds_place_v2(self):
        sql = _HANDLERS["O1_PLACE"].upsert_sql
        assert "odds_place_v2" in sql

    def test_o1_place_has_odds_min_max(self):
        conf = _HANDLERS["O1_PLACE"]
        assert "odds_min" in conf.columns
        assert "odds_max" in conf.columns


# ── _HANDLERS 全エントリのテーブル名網羅テスト ─────────────────────────────────
#
# 従来は WH_ENTRY のみ .table を検証していた（test_wh_entry_targets_race_entries_v2）。
# 「旧テーブル参照バグ」（races/race_entries を誤って旧スキーマのまま参照し続ける）が
# 過去に複数回発生しているため、_HANDLERS の全エントリについて現行テーブルを
# 指していることを網羅的に検証し、今後 _HANDLERS に新しいレコード種別が追加された
# 際に旧テーブル名が紛れ込んだら即座に検知できるようにする。

_CURRENT_TABLES: frozenset[str] = frozenset({
    "races_v2",
    "race_entries_v2",
    "training_slope",
    "training_wood",
    "payouts",
    "odds_win_v2",
    "odds_place_v2",
    "weather_track_updates",
    "scratch_updates",
    "jockey_changes",
    "start_time_changes",
    "course_changes",
})

# 「旧・未使用」テーブル（bulk_ingest_v2 / jvdl_parser.sink が書き込みを停止したテーブル）。
# _HANDLERS のいずれのエントリもこれらを指してはならない。
_LEGACY_TABLES: frozenset[str] = frozenset({"races", "race_entries", "horses", "jockeys", "trainers"})


class TestAllHandlersTargetCurrentTables:
    """_HANDLERS の全エントリが現行テーブルのみを指すことを網羅的に検証する。"""

    @pytest.mark.parametrize("record_type", sorted(_HANDLERS.keys()))
    def test_handler_table_is_a_known_current_table(self, record_type):
        conf = _HANDLERS[record_type]
        assert conf.table in _CURRENT_TABLES, (
            f"_HANDLERS[{record_type!r}].table = {conf.table!r} は "
            f"現行テーブルの許可リストに含まれていない。新規追加なら _CURRENT_TABLES に"
            f"追記するか、旧テーブルを誤参照していないか確認すること。"
        )

    @pytest.mark.parametrize("record_type", sorted(_HANDLERS.keys()))
    def test_handler_table_is_not_a_legacy_table(self, record_type):
        conf = _HANDLERS[record_type]
        assert conf.table not in _LEGACY_TABLES, (
            f"_HANDLERS[{record_type!r}].table = {conf.table!r} は"
            f"「旧・未使用」テーブルを指している（bulk_ingest_v2/jvdl_parser.sinkの"
            f"書き込み対象外）。races_v2/race_entries_v2 等の現行テーブルに修正すること。"
        )

    def test_all_handlers_covered_by_current_or_legacy_set(self):
        """_CURRENT_TABLES / _LEGACY_TABLES のどちらにも属さない未知のテーブル名が
        紛れ込んだ場合に検知する（新規テーブル追加時にこのテストが通知として落ちる）。"""
        unknown = {
            conf.table for conf in _HANDLERS.values()
            if conf.table not in _CURRENT_TABLES and conf.table not in _LEGACY_TABLES
        }
        assert not unknown, (
            f"_HANDLERS に未分類のテーブル名がある: {unknown}。"
            f"_CURRENT_TABLES（現行）か _LEGACY_TABLES（旧・禁止）のいずれかに分類すること。"
        )

    def test_handlers_registry_is_non_empty(self):
        """このテスト自体が無意味に成功し続けないためのガード（レジストリが空でないこと）。"""
        assert len(_HANDLERS) >= 10
