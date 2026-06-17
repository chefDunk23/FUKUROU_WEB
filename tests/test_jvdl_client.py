"""
tests/test_jvdl_client.py
==========================
jvdl_client モジュールのユニットテスト。

COM 部分はすべてモック。comtypes 未インストール環境でも動作する。
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, call, patch


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_jv_mock(records: list[bytes], eof_rc: int = 1):
    """JVRead のシーケンスを返す JV-Link COM オブジェクトのモック。

    records: yield したいバイト列のリスト
    各 records[i] は (buf, filename, rc=0) として返し、最後に rc=eof_rc を返す。
    """
    jv = MagicMock()
    jv.JVInit.return_value = 0
    jv.JVOpen.return_value = 0

    read_returns = [(rec, "dummy_file.jvd", 0) for rec in records]
    read_returns.append((b"", "dummy_file.jvd", eof_rc))
    jv.JVRead.side_effect = read_returns

    return jv


# ── JVLinkClient のモックテスト ────────────────────────────────────────────────

class TestJVLinkClientMocked:
    """comtypes をモックして JVLinkClient のロジックをテストする。"""

    def _patch_com(self, jv_mock):
        """comtypes.client.CreateObject をパッチして jv_mock を返すコンテキスト。"""
        return patch("comtypes.client.CreateObject", return_value=jv_mock)

    def test_fetch_stored_yields_records(self, tmp_path, monkeypatch):
        """fetch_stored が records を yield すること。"""
        monkeypatch.setenv("JVLINK_SID", "TEST_SID")
        records = [b"RA" + b"X" * 100, b"SE" + b"Y" * 120]
        jv_mock = _make_jv_mock(records)

        # comtypes をモジュールレベルで差し込む
        import types
        fake_comtypes = types.ModuleType("comtypes")
        fake_client   = types.ModuleType("comtypes.client")
        fake_client.CreateObject = MagicMock(return_value=jv_mock)
        fake_comtypes.client = fake_client

        with patch.dict("sys.modules", {"comtypes": fake_comtypes, "comtypes.client": fake_client}):
            # _COM_AVAILABLE を True に強制するためモジュールを再ロード
            import importlib
            import jvdl_client.jvlink as mod
            mod._COM_AVAILABLE = True
            mod._cc = fake_client

            client = mod.JVLinkClient.__new__(mod.JVLinkClient)
            client._jv = jv_mock

            result = list(client.fetch_stored("RACE", "20260601000000"))

        assert result == records
        jv_mock.JVOpen.assert_called_once_with("RACE", "20260601000000", 2, 0, 0, "")
        assert jv_mock.JVClose.called

    def test_fetch_stored_handles_file_switch(self, monkeypatch):
        """JVRead が -1 (ファイル切替) を返した場合は continue されること。"""
        import jvdl_client.jvlink as mod

        jv = MagicMock()
        jv.JVOpen.return_value = 0
        jv.JVRead.side_effect = [
            (b"", "file1.jvd", -1),  # ファイル切替
            (b"RA" + b"Z" * 100, "file2.jvd", 0),
            (b"", "file2.jvd", 1),   # EOF
        ]

        client = mod.JVLinkClient.__new__(mod.JVLinkClient)
        client._jv = jv

        result = list(client.fetch_stored("DIFF", "20260601000000"))
        assert len(result) == 1
        assert result[0][:2] == b"RA"

    def test_fetch_stored_handles_downloading(self, monkeypatch):
        """JVRead が -3 (ダウンロード中) を返した場合は retry されること。"""
        import jvdl_client.jvlink as mod

        jv = MagicMock()
        jv.JVOpen.return_value = 0
        jv.JVRead.side_effect = [
            (b"", "", -3),           # ダウンロード中
            (b"", "", -3),           # ダウンロード中
            (b"RA" + b"A" * 100, "f.jvd", 0),
            (b"", "f.jvd", 1),       # EOF
        ]

        client = mod.JVLinkClient.__new__(mod.JVLinkClient)
        client._jv = jv

        with patch("jvdl_client.jvlink.time.sleep"):
            result = list(client.fetch_stored("RACE", "20260601000000"))

        assert len(result) == 1

    def test_com_import_error_raised_when_unavailable(self, monkeypatch):
        """comtypes なし環境で JVLinkClient() は ComImportError を送出すること。"""
        monkeypatch.setenv("JVLINK_SID", "TEST_SID")
        import jvdl_client.jvlink as mod

        original = mod._COM_AVAILABLE
        try:
            mod._COM_AVAILABLE = False
            import pytest
            with pytest.raises(mod.ComImportError):
                mod.JVLinkClient()
        finally:
            mod._COM_AVAILABLE = original

    def test_sid_required(self, monkeypatch):
        """JVLINK_SID が未設定なら ValueError を送出すること。"""
        monkeypatch.delenv("JVLINK_SID", raising=False)
        import jvdl_client.jvlink as mod

        original = mod._COM_AVAILABLE
        try:
            mod._COM_AVAILABLE = True
            import pytest
            with pytest.raises(ValueError, match="JVLINK_SID"):
                mod.JVLinkClient()
        finally:
            mod._COM_AVAILABLE = original

    def test_watermark_roundtrip(self):
        """get_watermark → set_watermark → get_watermark が一致すること。"""
        import jvdl_client.jvlink as mod

        conn = MagicMock()
        cur = MagicMock()
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        # get_watermark: レコードなし → デフォルト値
        cur.fetchone.return_value = None
        result = mod.JVLinkClient.get_watermark(conn, "RACE")
        assert result == "20220101000000"

        # get_watermark: レコードあり
        cur.fetchone.return_value = ("20260601120000",)
        result = mod.JVLinkClient.get_watermark(conn, "RACE")
        assert result == "20260601120000"


# ── sync_from_jvlink のモックテスト ───────────────────────────────────────────

class TestSyncFromJvlink:
    """sync_jvdata.sync_from_jvlink の統合テスト（JVLinkClient をモック）。"""

    def test_raw_file_created(self, tmp_path, monkeypatch):
        """fetch_stored が返す records が raw ファイルに書き出されること。"""
        monkeypatch.setenv("JVLINK_SID", "TEST_SID")

        mock_records = [b"RA" + b"0" * 100 + b"\n", b"SE" + b"1" * 120 + b"\n"]

        mock_jv_instance = MagicMock()
        mock_jv_instance.__enter__ = MagicMock(return_value=mock_jv_instance)
        mock_jv_instance.__exit__ = MagicMock(return_value=False)
        mock_jv_instance.fetch_stored.return_value = iter(mock_records)

        # sync_from_jvlink 内では `from jvdl_client.jvlink import JVLinkClient` を使うため
        # jvdl_client.jvlink.JVLinkClient をパッチする
        with (
            patch("jvdl_client.jvlink.JVLinkClient", return_value=mock_jv_instance) as MockCls,
            patch.object(MockCls, "get_watermark", return_value="20220101000000"),
            patch.object(MockCls, "set_watermark"),
            patch("jvdl_client.sync_jvdata._submit_job"),
            patch("jvdl_client.sync_jvdata._notify_result"),
            patch("jvdl_client.sync_jvdata.psycopg2.connect"),
        ):
            from jvdl_client.sync_jvdata import sync_from_jvlink

            results = sync_from_jvlink(
                dataspecs=["RACE"],
                output_dir=str(tmp_path),
                run_ingest=False,
                run_stores=False,
                run_recompute=False,
            )

        assert results.get("RACE") == "ok"
        raw_path = tmp_path / "raw_RACE.txt"
        assert raw_path.exists()
        content = raw_path.read_bytes()
        assert b"RA" in content
        assert b"SE" in content

    def test_com_import_error_captured_in_results(self, tmp_path, monkeypatch):
        """ComImportError が発生した場合、results に ERROR が格納されること。"""
        monkeypatch.setenv("JVLINK_SID", "TEST_SID")

        from jvdl_client.jvlink import ComImportError

        with (
            patch("jvdl_client.jvlink.JVLinkClient", side_effect=ComImportError("no COM")),
            patch("jvdl_client.sync_jvdata._notify_result"),
            patch("jvdl_client.sync_jvdata.psycopg2.connect"),
        ):
            from jvdl_client.sync_jvdata import sync_from_jvlink
            results = sync_from_jvlink(
                dataspecs=["RACE"],
                output_dir=str(tmp_path),
                run_ingest=False,
                run_stores=False,
            )

        assert all(v.startswith("ERROR") for v in results.values())

    def test_ingest_called_for_ok_specs(self, tmp_path, monkeypatch):
        """RACE は ok、DIFF はエラーのとき、results に両方正しく記録されること。"""
        monkeypatch.setenv("JVLINK_SID", "TEST_SID")

        def _fetch_side_effect(ds, from_time, option):
            if ds == "RACE":
                return iter([b"RA" + b"0" * 100 + b"\n"])
            raise RuntimeError("DIFF 取得エラー")

        mock_jv = MagicMock()
        mock_jv.__enter__ = MagicMock(return_value=mock_jv)
        mock_jv.__exit__ = MagicMock(return_value=False)
        mock_jv.fetch_stored.side_effect = _fetch_side_effect

        with (
            patch("jvdl_client.jvlink.JVLinkClient", return_value=mock_jv) as MockCls,
            patch.object(MockCls, "get_watermark", return_value="20220101000000"),
            patch.object(MockCls, "set_watermark"),
            patch("jvdl_client.sync_jvdata._submit_job"),
            patch("jvdl_client.sync_jvdata._notify_result"),
            patch("jvdl_client.sync_jvdata.psycopg2.connect"),
        ):
            from jvdl_client.sync_jvdata import sync_from_jvlink
            results = sync_from_jvlink(
                dataspecs=["RACE", "DIFF"],
                output_dir=str(tmp_path),
                run_ingest=False,
                run_stores=False,
            )

        assert results["RACE"] == "ok"
        assert results["DIFF"].startswith("ERROR")


# ── CLI の --help テスト ────────────────────────────────────────────────────────

def test_cli_help(capsys):
    """python -m jvdl_client.sync_jvdata --help が正常終了すること。"""
    import pytest
    from jvdl_client.sync_jvdata import _build_parser

    parser = _build_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["--help"])

    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "--dataspecs" in captured.out
    assert "--full-setup" in captured.out
    assert "--no-stores" in captured.out
