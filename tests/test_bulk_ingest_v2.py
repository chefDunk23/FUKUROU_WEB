"""
tests/test_bulk_ingest_v2.py
==============================
scripts/bulk_ingest_v2.py の回帰テスト。

2026-07-02: 本番未来レース検証(7/4-5)で発見したバグの回帰テスト。
run_ingest() は元々 CLI 単体実行(python scripts/bulk_ingest_v2.py)専用に
sys.exit() で終了コードを設定する設計だった。これが
jvdl_client/sync_jvdata.py からライブラリとして呼び出されるようになった際、
sys.exit() が呼び出し元プロセス（shared/worker/job_runner.py のワーカー
プロセス全体）を巻き込んで強制終了させるバグがあった。
sync_jvdata ジョブが実行中のままプロセスごと終了し、次回ワーカー起動時に
孤児 running ジョブとして failed 化する現象（過去の job id=36 のクラッシュ）
の原因になっていたと推測される。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _write_raw_file(tmp_path, name: str, records: list[bytes]) -> None:
    p = tmp_path / name
    with open(p, "wb") as f:
        for rec in records:
            f.write(rec)
            if not rec.endswith(b"\n"):
                f.write(b"\n")


class TestRunIngestDoesNotExitProcess:
    """run_ingest() が sys.exit() で呼び出し元プロセスを道連れにしないこと。"""

    def test_run_ingest_returns_dict_not_sys_exit(self, tmp_path, monkeypatch):
        """正常系: run_ingest が SystemExit を送出せず dict を返すこと。"""
        import scripts.bulk_ingest_v2 as mod

        monkeypatch.setattr(mod, "_RAW_DIR", tmp_path)
        _write_raw_file(tmp_path, "raw_RACE.txt", [b"RA" + b"0" * 1270])

        with patch("scripts.bulk_ingest_v2.psycopg2.connect", return_value=MagicMock()):
            result = mod.run_ingest(files=["raw_RACE.txt"], dry_run=True)

        assert isinstance(result, dict)
        assert result["ok"] is True
        assert result["total_files"] == 1

    def test_run_ingest_no_valid_files_returns_dict_not_sys_exit(self, tmp_path, monkeypatch):
        """異常系(ファイル無し): 従来は sys.exit(2) していたが、ok=False の dict を返すこと。"""
        import scripts.bulk_ingest_v2 as mod

        monkeypatch.setattr(mod, "_RAW_DIR", tmp_path)
        # tmp_path 配下にファイルを一切置かない

        result = mod.run_ingest(files=["raw_NONEXISTENT.txt"], dry_run=True)

        assert isinstance(result, dict)
        assert result["ok"] is False
        assert result["total_files"] == 0

    def test_run_ingest_result_contains_dlq_rate(self, tmp_path, monkeypatch):
        """戻り値に dlq_rate が含まれ、CLI 側 (main()) が終了コード判定に使えること。"""
        import scripts.bulk_ingest_v2 as mod

        monkeypatch.setattr(mod, "_RAW_DIR", tmp_path)
        _write_raw_file(tmp_path, "raw_RACE.txt", [b"RA" + b"0" * 1270])

        with patch("scripts.bulk_ingest_v2.psycopg2.connect", return_value=MagicMock()):
            result = mod.run_ingest(files=["raw_RACE.txt"], dry_run=True)

        assert "dlq_rate" in result
        assert isinstance(result["dlq_rate"], float)


class TestSyncJvdataBulkIngestCall:
    """jvdl_client.sync_jvdata が bulk_ingest_v2.run_ingest を
    存在しない引数(hook)無しで呼び出すこと。

    2026-07-02: scripts/bulk_ingest_v2.run_ingest() から --hook オプション
    (V2アンサンブル引退に伴う削除)を削除した際、呼び出し元の
    jvdl_client/sync_jvdata.py 側の `hook=False` 引数を消し忘れ、
    TypeError: run_ingest() got an unexpected keyword argument 'hook'
    で sync_jvdata ジョブが毎回失敗するリグレッションがあった。
    """

    def test_bulk_ingest_called_without_hook_kwarg(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JVLINK_SID", "TEST_SID")

        mock_jv = MagicMock()
        mock_jv.__enter__ = MagicMock(return_value=mock_jv)
        mock_jv.__exit__ = MagicMock(return_value=False)
        mock_jv.fetch_stored.return_value = iter([b"RA" + b"0" * 100])

        captured_kwargs = {}

        def _fake_run_ingest(**kwargs):
            captured_kwargs.update(kwargs)
            return {"ok": True, "total_files": 1, "total_ok": 1, "total_dlq": 0,
                    "dlq_rate": 0.0, "type_counts": {}}

        with (
            patch("jvdl_client.jvlink.JVLinkClient", return_value=mock_jv) as MockCls,
            patch.object(MockCls, "get_watermark", return_value="20220101000000"),
            patch.object(MockCls, "set_watermark"),
            patch("jvdl_client.sync_jvdata._submit_job"),
            patch("jvdl_client.sync_jvdata._notify_result"),
            patch("jvdl_client.sync_jvdata.psycopg2.connect"),
            patch("scripts.bulk_ingest_v2.run_ingest", side_effect=_fake_run_ingest),
        ):
            from jvdl_client.sync_jvdata import sync_from_jvlink

            results = sync_from_jvlink(
                dataspecs=["RACE"],
                output_dir=str(tmp_path),
                run_ingest=True,
                run_stores=False,
                run_recompute=False,
            )

        assert "hook" not in captured_kwargs, (
            "run_ingest に存在しない 'hook' 引数が渡された（TypeErrorの原因）"
        )
        assert results.get("RACE") == "ok"


class TestWeeklyOptionResolution:
    """weekly=True 時の option/from_time 解決ロジックの回帰テスト。

    2026-07-02 実地検証: JVOpen(option=2/OPT_WEEKLY) は RACE では
    正常応答するが SLOP/WOOD には ret=-111(エラー)になる。また
    OPT_WEEKLY は from_time に sync_watermark 由来の直近時刻を渡すと
    ret=-1(データなし)になり、当週月曜 00:00:00 を渡すと正しく
    readcount が返ることを確認した。
    """

    def test_race_uses_weekly_option_with_monday_from_time(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JVLINK_SID", "TEST_SID")

        mock_jv = MagicMock()
        mock_jv.__enter__ = MagicMock(return_value=mock_jv)
        mock_jv.__exit__ = MagicMock(return_value=False)
        captured_calls = []

        def _fetch_side_effect(ds, from_time, option):
            captured_calls.append((ds, from_time, option))
            return iter([b"RA" + b"0" * 100])

        mock_jv.fetch_stored.side_effect = _fetch_side_effect

        with (
            patch("jvdl_client.jvlink.JVLinkClient", return_value=mock_jv) as MockCls,
            patch.object(MockCls, "get_watermark", return_value="20260702112706"),
            patch.object(MockCls, "set_watermark"),
            patch("jvdl_client.sync_jvdata._submit_job"),
            patch("jvdl_client.sync_jvdata._notify_result"),
            patch("jvdl_client.sync_jvdata.psycopg2.connect"),
        ):
            from jvdl_client.sync_jvdata import sync_from_jvlink

            sync_from_jvlink(
                dataspecs=["RACE"],
                output_dir=str(tmp_path),
                run_ingest=False,
                run_stores=False,
                weekly=True,
            )

        assert len(captured_calls) == 1
        ds, from_time, option = captured_calls[0]
        assert option == 2  # OPT_WEEKLY
        # watermark(20260702112706) をそのまま使っていないこと（ret=-1になるバグの回帰）
        assert from_time != "20260702112706"
        assert from_time.endswith("000000")

    def test_slop_wood_stay_on_stored_diff_even_when_weekly(self, tmp_path, monkeypatch):
        """weekly=True でも SLOP/WOOD は OPT_WEEKLY 非対応なため通常差分のままであること。"""
        monkeypatch.setenv("JVLINK_SID", "TEST_SID")

        mock_jv = MagicMock()
        mock_jv.__enter__ = MagicMock(return_value=mock_jv)
        mock_jv.__exit__ = MagicMock(return_value=False)
        captured_calls = []

        def _fetch_side_effect(ds, from_time, option):
            captured_calls.append((ds, from_time, option))
            return iter([])

        mock_jv.fetch_stored.side_effect = _fetch_side_effect

        with (
            patch("jvdl_client.jvlink.JVLinkClient", return_value=mock_jv) as MockCls,
            patch.object(MockCls, "get_watermark", return_value="20260702112706"),
            patch.object(MockCls, "set_watermark"),
            patch("jvdl_client.sync_jvdata._submit_job"),
            patch("jvdl_client.sync_jvdata._notify_result"),
            patch("jvdl_client.sync_jvdata.psycopg2.connect"),
        ):
            from jvdl_client.sync_jvdata import sync_from_jvlink

            sync_from_jvlink(
                dataspecs=["SLOP", "WOOD"],
                output_dir=str(tmp_path),
                run_ingest=False,
                run_stores=False,
                weekly=True,
            )

        assert len(captured_calls) == 2
        for ds, from_time, option in captured_calls:
            assert option == 1  # OPT_STORED_DIFF (weeklyの影響を受けない)
            assert from_time == "20260702112706"  # watermarkそのまま使われる

    def test_explicit_from_time_overrides_monday_default(self, tmp_path, monkeypatch):
        """weekly=True でも from_time を明示指定した場合はそちらを優先すること。"""
        monkeypatch.setenv("JVLINK_SID", "TEST_SID")

        mock_jv = MagicMock()
        mock_jv.__enter__ = MagicMock(return_value=mock_jv)
        mock_jv.__exit__ = MagicMock(return_value=False)
        captured_calls = []

        def _fetch_side_effect(ds, from_time, option):
            captured_calls.append((ds, from_time, option))
            return iter([])

        mock_jv.fetch_stored.side_effect = _fetch_side_effect

        with (
            patch("jvdl_client.jvlink.JVLinkClient", return_value=mock_jv) as MockCls,
            patch.object(MockCls, "get_watermark", return_value="20260702112706"),
            patch.object(MockCls, "set_watermark"),
            patch("jvdl_client.sync_jvdata._submit_job"),
            patch("jvdl_client.sync_jvdata._notify_result"),
            patch("jvdl_client.sync_jvdata.psycopg2.connect"),
        ):
            from jvdl_client.sync_jvdata import sync_from_jvlink

            sync_from_jvlink(
                dataspecs=["RACE"],
                output_dir=str(tmp_path),
                run_ingest=False,
                run_stores=False,
                weekly=True,
                from_time="20250101000000",
            )

        assert captured_calls[0] == ("RACE", "20250101000000", 2)
