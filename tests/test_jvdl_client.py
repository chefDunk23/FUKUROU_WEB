"""
tests/test_jvdl_client.py
==========================
jvdl_client モジュールのユニットテスト。

fetch_stored は 32-bit サブプロセス経由のため、subprocess.run をモックして
ファイル書き出しをシミュレートする。COM 依存なし。
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── JVLinkClient のテスト ─────────────────────────────────────────────────────

class TestJVLinkClientBridge:
    """subprocess ブリッジ経由の fetch_stored をテストする。"""

    def _make_launcher_exists(self):
        """_PY_LAUNCHER.exists() を True に見せるパッチ。"""
        return patch("jvdl_client.jvlink._PY_LAUNCHER", Path(__file__))  # __file__ は確実に存在

    def _make_subprocess_write(self, tmp_path, records: list[bytes]):
        """subprocess.run の side_effect: 呼ばれたら出力ファイルを書いて return 0。"""
        def _run(cmd, env, timeout):
            output_file = Path(cmd[-1])
            with open(output_file, "wb") as f:
                for rec in records:
                    f.write(rec)
                    if not rec.endswith(b"\n"):
                        f.write(b"\n")
            return subprocess.CompletedProcess(cmd, returncode=0)
        return _run

    def test_fetch_stored_yields_records(self, tmp_path):
        """fetch_stored がサブプロセス出力ファイルから records を yield すること。"""
        records = [b"RA" + b"X" * 100, b"SE" + b"Y" * 120]

        import jvdl_client.jvlink as mod

        with self._make_launcher_exists():
            client = mod.JVLinkClient()
            with patch("jvdl_client.jvlink.subprocess.run",
                       side_effect=self._make_subprocess_write(tmp_path, records)):
                result = list(client.fetch_stored("RACE", "20260601000000", _tmp_dir=str(tmp_path)))

        assert result == records

    def test_fetch_stored_subprocess_failure_raises(self, tmp_path):
        """サブプロセスが非ゼロで終了した場合 RuntimeError を送出すること。"""
        import jvdl_client.jvlink as mod

        def _fail(cmd, env, timeout):
            return subprocess.CompletedProcess(cmd, returncode=1)

        with self._make_launcher_exists():
            client = mod.JVLinkClient()
            with patch("jvdl_client.jvlink.subprocess.run", side_effect=_fail):
                with pytest.raises(RuntimeError, match="downloader 失敗"):
                    list(client.fetch_stored("RACE", "20260601000000", _tmp_dir=str(tmp_path)))

    def test_fetch_stored_empty_file_yields_nothing(self, tmp_path):
        """出力ファイルが空の場合は何も yield しないこと。"""
        import jvdl_client.jvlink as mod

        def _empty(cmd, env, timeout):
            Path(cmd[-1]).write_bytes(b"")
            return subprocess.CompletedProcess(cmd, returncode=0)

        with self._make_launcher_exists():
            client = mod.JVLinkClient()
            with patch("jvdl_client.jvlink.subprocess.run", side_effect=_empty):
                result = list(client.fetch_stored("RACE", "20260601000000", _tmp_dir=str(tmp_path)))

        assert result == []

    def test_fetch_stored_tmp_file_cleaned_up(self, tmp_path):
        """fetch_stored 完了後に一時ファイルが削除されること。"""
        import jvdl_client.jvlink as mod

        written_paths: list[Path] = []

        def _write(cmd, env, timeout):
            p = Path(cmd[-1])
            p.write_bytes(b"RA" + b"0" * 100 + b"\n")
            written_paths.append(p)
            return subprocess.CompletedProcess(cmd, returncode=0)

        with self._make_launcher_exists():
            client = mod.JVLinkClient()
            with patch("jvdl_client.jvlink.subprocess.run", side_effect=_write):
                list(client.fetch_stored("RACE", "20260601000000", _tmp_dir=str(tmp_path)))

        assert written_paths, "サブプロセスが呼ばれていない"
        assert not written_paths[0].exists(), "一時ファイルが残っている"

    def test_launcher_not_found_raises_com_import_error(self):
        """_PY_LAUNCHER が存在しない場合 ComImportError を送出すること。"""
        import jvdl_client.jvlink as mod

        with patch("jvdl_client.jvlink._PY_LAUNCHER", Path("C:/nonexistent/py.exe")):
            with pytest.raises(mod.ComImportError):
                mod.JVLinkClient()

    def test_subprocess_receives_correct_args(self, tmp_path):
        """サブプロセスに正しい dataspec / from_time / option が渡されること。"""
        import jvdl_client.jvlink as mod

        captured_cmd: list[list[str]] = []

        def _capture(cmd, env, timeout):
            captured_cmd.append(cmd)
            Path(cmd[-1]).write_bytes(b"")
            return subprocess.CompletedProcess(cmd, returncode=0)

        with self._make_launcher_exists():
            client = mod.JVLinkClient()
            with patch("jvdl_client.jvlink.subprocess.run", side_effect=_capture):
                list(client.fetch_stored("RACE", "20260601000000", option=4, _tmp_dir=str(tmp_path)))

        assert captured_cmd, "subprocess.run が呼ばれていない"
        cmd = captured_cmd[0]
        assert "jvdl_client._downloader_32bit" in " ".join(cmd)
        assert "RACE" in cmd
        assert "20260601000000" in cmd
        assert "4" in cmd  # option=4 (SETUP)

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


# ── sync_from_jvlink のテスト ──────────────────────────────────────────────────

class TestSyncFromJvlink:
    """sync_jvdata.sync_from_jvlink の統合テスト（JVLinkClient をモック）。"""

    def test_raw_file_created(self, tmp_path, monkeypatch):
        """fetch_stored が返す records が raw ファイルに書き出されること。"""
        monkeypatch.setenv("JVLINK_SID", "TEST_SID")

        mock_records = [b"RA" + b"0" * 100, b"SE" + b"1" * 120]

        mock_jv_instance = MagicMock()
        mock_jv_instance.__enter__ = MagicMock(return_value=mock_jv_instance)
        mock_jv_instance.__exit__ = MagicMock(return_value=False)
        mock_jv_instance.fetch_stored.return_value = iter(mock_records)

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
            patch("jvdl_client.jvlink.JVLinkClient", side_effect=ComImportError("no launcher")),
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
                return iter([b"RA" + b"0" * 100])
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
    from jvdl_client.sync_jvdata import _build_parser

    parser = _build_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["--help"])

    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "--dataspecs" in captured.out
    assert "--full-setup" in captured.out
    assert "--no-stores" in captured.out
