"""
tests/test_lab_api.py
=====================
条件ラボ API のユニットテスト。

実際の DB / Parquet には依存しない（CRUD と JSON 永続化のみ検証）。
バックテスト実行エンドポイントはジョブ起動の確認のみ（実行はしない）。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# ─────────────────────────────────────────────────────────────────────────
# 一時 data/lab/conditions.json を使うようにパッチする
# ─────────────────────────────────────────────────────────────────────────

_TMP_DIR = Path(tempfile.mkdtemp())
_TMP_DATA = _TMP_DIR / "conditions.json"


@pytest.fixture(autouse=True)
def _patch_data_file(monkeypatch):
    """各テスト前に空のJSONファイルを用意し、ルーターのパスを差し替える。"""
    _TMP_DATA.write_text('{"custom_conditions": [], "condition_sets": []}', encoding="utf-8")
    import api_v2.routers.lab as lab_module
    monkeypatch.setattr(lab_module, "_DATA_FILE", _TMP_DATA)
    monkeypatch.setattr(lab_module, "_jobs", {})


@pytest.fixture
def client(monkeypatch):
    """TestClient — API_KEY を空にして認証をスキップ。"""
    import api_v2.deps as _deps
    import shared.config as _cfg
    monkeypatch.setattr(_cfg, "API_KEY", "")
    monkeypatch.setattr(_deps, "API_KEY", "")
    from api_v2.main import app
    return TestClient(app)


# ─────────────────────────────────────────────────────────────────────────
# 条件エンドポイント
# ─────────────────────────────────────────────────────────────────────────


def test_get_conditions_returns_builtin(client):
    """GET /api/v2/lab/conditions は組み込み条件一覧を返す。"""
    res = client.get("/api/v2/lab/conditions")
    assert res.status_code == 200
    data = res.json()
    assert "builtin" in data
    assert len(data["builtin"]) > 0
    assert "custom" in data
    assert data["custom"] == []


def test_get_conditions_builtin_has_required_fields(client):
    """組み込み条件はid/name/description/layerを持つ。"""
    res = client.get("/api/v2/lab/conditions")
    for cond in res.json()["builtin"]:
        assert "id" in cond
        assert "name" in cond
        assert "description" in cond
        assert "layer" in cond
        assert "params_schema" in cond


def test_create_custom_condition(client):
    """POST /api/v2/lab/conditions でカスタム条件を作成できる。"""
    res = client.post("/api/v2/lab/conditions", json={
        "name": "テスト条件",
        "description": "テスト用",
        "base_condition_id": "v2_past_margin",
        "params": {"lookback": 2, "max_sec": 0.5, "bonus_score": 1.5},
    })
    assert res.status_code == 201
    data = res.json()
    assert data["name"] == "テスト条件"
    assert data["base_condition_id"] == "v2_past_margin"
    assert data["id"].startswith("custom_")


def test_create_custom_condition_invalid_base(client):
    """存在しないベース条件IDは400を返す。"""
    res = client.post("/api/v2/lab/conditions", json={
        "name": "エラー条件",
        "base_condition_id": "nonexistent_condition",
    })
    assert res.status_code == 400


def test_update_custom_condition(client):
    """PUT で カスタム条件の名前を変更できる。"""
    create_res = client.post("/api/v2/lab/conditions", json={
        "name": "変更前",
        "base_condition_id": "v2_past_margin",
    })
    cond_id = create_res.json()["id"]

    update_res = client.put(f"/api/v2/lab/conditions/{cond_id}", json={"name": "変更後"})
    assert update_res.status_code == 200
    assert update_res.json()["name"] == "変更後"


def test_update_builtin_condition_forbidden(client):
    """組み込み条件は変更不可（403）。"""
    res = client.put("/api/v2/lab/conditions/v2_past_margin", json={"name": "改ざん"})
    assert res.status_code == 403


def test_delete_custom_condition(client):
    """DELETE でカスタム条件を削除できる。"""
    create_res = client.post("/api/v2/lab/conditions", json={
        "name": "削除対象",
        "base_condition_id": "v2_race_quality",
    })
    cond_id = create_res.json()["id"]

    del_res = client.delete(f"/api/v2/lab/conditions/{cond_id}")
    assert del_res.status_code == 204

    # 削除後は一覧に存在しない
    list_res = client.get("/api/v2/lab/conditions")
    ids = [c["id"] for c in list_res.json()["custom"]]
    assert cond_id not in ids


def test_delete_builtin_condition_forbidden(client):
    """組み込み条件は削除不可（403）。"""
    res = client.delete("/api/v2/lab/conditions/v2_past_margin")
    assert res.status_code == 403


def test_delete_nonexistent_condition(client):
    """存在しないIDの削除は404。"""
    res = client.delete("/api/v2/lab/conditions/custom_nonexistent")
    assert res.status_code == 404


# ─────────────────────────────────────────────────────────────────────────
# 条件セットエンドポイント
# ─────────────────────────────────────────────────────────────────────────


def test_get_condition_sets_empty(client):
    """GET /api/v2/lab/condition-sets は空リストを返す（初期状態）。"""
    res = client.get("/api/v2/lab/condition-sets")
    assert res.status_code == 200
    assert res.json()["condition_sets"] == []


def test_create_condition_set(client):
    """POST /api/v2/lab/condition-sets で条件セットを作成できる。"""
    res = client.post("/api/v2/lab/condition-sets", json={
        "name": "テストセット",
        "description": "テスト用セット",
        "conditions": [
            {"condition_id": "v2_past_margin", "mode": "filter", "enabled": True, "params": {}},
            {"condition_id": "v2_jockey_positive", "mode": "scoring", "enabled": True, "params": {}},
        ],
        "ranking": {"primary": "condition_clear_count", "secondary": "ai_score", "max_selections": 3},
    })
    assert res.status_code == 201
    data = res.json()
    assert data["name"] == "テストセット"
    assert data["id"].startswith("set_")
    assert len(data["conditions"]) == 2
    assert data["conditions"][0]["mode"] == "filter"


def test_update_condition_set(client):
    """PUT で条件セットの名前と条件を変更できる。"""
    create_res = client.post("/api/v2/lab/condition-sets", json={"name": "更新前"})
    set_id = create_res.json()["id"]

    update_res = client.put(f"/api/v2/lab/condition-sets/{set_id}", json={
        "name": "更新後",
        "conditions": [{"condition_id": "v2_f3_top", "mode": "scoring", "enabled": True, "params": {}}],
    })
    assert update_res.status_code == 200
    updated = update_res.json()
    assert updated["name"] == "更新後"
    assert len(updated["conditions"]) == 1


def test_delete_condition_set(client):
    """DELETE で条件セットを削除できる。"""
    create_res = client.post("/api/v2/lab/condition-sets", json={"name": "削除対象セット"})
    set_id = create_res.json()["id"]

    del_res = client.delete(f"/api/v2/lab/condition-sets/{set_id}")
    assert del_res.status_code == 204

    list_res = client.get("/api/v2/lab/condition-sets")
    ids = [s["id"] for s in list_res.json()["condition_sets"]]
    assert set_id not in ids


def test_delete_nonexistent_set(client):
    """存在しない条件セットの削除は404。"""
    res = client.delete("/api/v2/lab/condition-sets/set_nonexistent")
    assert res.status_code == 404


def test_condition_set_persistence(client):
    """JSONファイルに保存され、再読み込み後も存在する。"""
    client.post("/api/v2/lab/condition-sets", json={"name": "永続化テスト"})

    # ファイルから直接確認
    saved = json.loads(_TMP_DATA.read_text(encoding="utf-8"))
    assert len(saved["condition_sets"]) == 1
    assert saved["condition_sets"][0]["name"] == "永続化テスト"


# ─────────────────────────────────────────────────────────────────────────
# バックテストエンドポイント
# ─────────────────────────────────────────────────────────────────────────


def test_start_backtest_nonexistent_set(client):
    """存在しない条件セットIDはバックテスト実行前に404。"""
    res = client.post("/api/v2/lab/backtest", json={
        "condition_set_id": "set_nonexistent",
        "periods": ["3m"],
    })
    assert res.status_code == 404


def test_start_backtest_returns_job_id(client):
    """バックテスト開始は job_id を返す（実行は非同期）。"""
    create_res = client.post("/api/v2/lab/condition-sets", json={
        "name": "バックテストセット",
        "conditions": [
            {"condition_id": "v2_past_margin", "mode": "filter", "enabled": True, "params": {}},
        ],
    })
    set_id = create_res.json()["id"]

    # バックグラウンドタスクの実際の実行をモック（DB接続不要）
    with patch("api_v2.routers.lab._run_backtest_job"):
        res = client.post("/api/v2/lab/backtest", json={
            "condition_set_id": set_id,
            "periods": ["3m"],
        })
    assert res.status_code == 202
    data = res.json()
    assert "job_id" in data
    assert data["status"] == "pending"


def test_get_backtest_result_job_not_found(client):
    """存在しないジョブIDは404。"""
    res = client.get("/api/v2/lab/backtest/result/nonexistent_job_id")
    assert res.status_code == 404


def test_compare_backtest_same_set_id(client):
    """同じ条件セットIDを2つ指定すると400ではなくジョブ開始されるが、
    フロント側で重複チェックするためバックエンドは許容する。"""
    create_res = client.post("/api/v2/lab/condition-sets", json={"name": "比較セット"})
    set_id = create_res.json()["id"]

    with patch("api_v2.routers.lab._run_compare_job"):
        res = client.post("/api/v2/lab/backtest/compare", json={
            "condition_set_id_a": set_id,
            "condition_set_id_b": set_id,
            "periods": ["3m"],
        })
    assert res.status_code == 202


# ─────────────────────────────────────────────────────────────────────────
# lab_adapter のユニットテスト
# ─────────────────────────────────────────────────────────────────────────


def test_condition_set_to_strategy():
    """condition_set_to_strategy は正しい Strategy モデルを返す。"""
    from tipster.lab_adapter import condition_set_to_strategy

    cset = {
        "id": "set_test",
        "name": "テスト戦略",
        "conditions": [
            {"condition_id": "v2_past_margin", "mode": "filter", "enabled": True, "params": {"lookback": 2}},
            {"condition_id": "v2_f3_top", "mode": "scoring", "enabled": True, "params": {}},
        ],
        "ranking": {"primary": "condition_clear_count", "secondary": "ai_score", "max_selections": 2},
    }
    strategy = condition_set_to_strategy(cset)

    assert strategy.name == "テスト戦略"
    assert strategy.type == "honmei"
    assert len(strategy.conditions) == 2
    assert strategy.conditions[0].id == "v2_past_margin"
    assert strategy.conditions[0].required is True   # mode=filter → required=True
    assert strategy.conditions[0].params["lookback"] == 2
    assert strategy.ranking.max_selections == 2


def test_builtin_conditions_catalog():
    """BUILTIN_CONDITIONS は v2_past_margin を含む。"""
    from tipster.lab_adapter import BUILTIN_CONDITIONS, BUILTIN_IDS

    assert "v2_past_margin" in BUILTIN_IDS
    assert "v2_f3_top" in BUILTIN_IDS
    assert "v2_baba_track_record" in BUILTIN_IDS

    pm = next(c for c in BUILTIN_CONDITIONS if c["id"] == "v2_past_margin")
    assert "params_schema" in pm
    assert "lookback" in pm["params_schema"]


# ─────────────────────────────────────────────────────────────────────────
# 戦略情報 API のテスト
# ─────────────────────────────────────────────────────────────────────────


def test_get_strategies_returns_all(client):
    """GET /strategies は5戦略すべてを返す。"""
    res = client.get("/api/v2/lab/strategies")
    assert res.status_code == 200
    body = res.json()
    assert "strategies" in body
    strategies = body["strategies"]
    ids = [s["id"] for s in strategies]
    assert "s1_pattern" in ids
    assert "honmei_v7" in ids
    assert "anaba_v5" in ids
    assert "training_tr1" in ids
    assert "anaba_ai_v1" in ids
    assert len(strategies) == 5


def test_get_strategies_s1_has_verified_stats(client):
    """s1_pattern には検証済み数値が付属する。"""
    res = client.get("/api/v2/lab/strategies")
    assert res.status_code == 200
    strats = {s["id"]: s for s in res.json()["strategies"]}
    s1 = strats["s1_pattern"]
    assert s1["stats"]["place_rate"] == pytest.approx(0.650, abs=0.001)
    assert s1["stats"]["holdout_place_rate"] == pytest.approx(0.706, abs=0.001)
    assert s1["stats"]["race_count"] == 123


def test_get_strategies_s1_has_conditions(client):
    """s1_pattern には5条件が含まれる。"""
    res = client.get("/api/v2/lab/strategies")
    strats = {s["id"]: s for s in res.json()["strategies"]}
    s1 = strats["s1_pattern"]
    assert len(s1["conditions"]) == 5
    cond_ids = [c["id"] for c in s1["conditions"]]
    assert "v2_past_margin" in cond_ids


def test_get_strategies_training_has_priorities(client):
    """training_tr1 には優先度リスト(7件)が含まれる。"""
    res = client.get("/api/v2/lab/strategies")
    strats = {s["id"]: s for s in res.json()["strategies"]}
    tr = strats["training_tr1"]
    assert "training_priorities" in tr
    assert len(tr["training_priorities"]) == 7
    priorities = [p["priority"] for p in tr["training_priorities"]]
    assert priorities == sorted(priorities)


def test_get_strategies_ai_has_submodels(client):
    """anaba_ai_v1 には5サブモデルが含まれる。"""
    res = client.get("/api/v2/lab/strategies")
    strats = {s["id"]: s for s in res.json()["strategies"]}
    ai = strats["anaba_ai_v1"]
    assert "ai_submodels" in ai
    assert len(ai["ai_submodels"]) == 5
    total_contribution = sum(m["contribution"] for m in ai["ai_submodels"])
    assert abs(total_contribution - 1.0) < 0.01


def test_copy_strategy_to_experiment(client, tmp_path, monkeypatch):
    """POST /strategies/{id}/copy は条件セットを作成する。"""
    import json
    from pathlib import Path
    data_file = tmp_path / "conditions.json"
    data_file.write_text(
        json.dumps({"custom_conditions": [], "condition_sets": []}),
        encoding="utf-8",
    )
    import api_v2.routers.lab as lab_module
    monkeypatch.setattr(lab_module, "_DATA_FILE", Path(data_file))

    res = client.post("/api/v2/lab/strategies/s1_pattern/copy", json={"name": "S-1テスト"})
    assert res.status_code == 201
    body = res.json()
    assert body["name"] == "S-1テスト"
    assert body["source_strategy_id"] == "s1_pattern"
    assert len(body["conditions"]) == 5
    cond_ids = [c["condition_id"] for c in body["conditions"]]
    assert "v2_past_margin" in cond_ids


def test_copy_strategy_unknown_returns_404(client):
    """存在しない戦略IDは 404 を返す。"""
    res = client.post("/api/v2/lab/strategies/nonexistent/copy", json={"name": "test"})
    assert res.status_code == 404


def test_create_condition_set_stores_source_strategy_id(client, tmp_path, monkeypatch):
    """CreateConditionSet に source_strategy_id を指定すると保存される。"""
    import json
    from pathlib import Path
    data_file = tmp_path / "conditions.json"
    data_file.write_text(
        json.dumps({"custom_conditions": [], "condition_sets": []}),
        encoding="utf-8",
    )
    import api_v2.routers.lab as lab_module
    monkeypatch.setattr(lab_module, "_DATA_FILE", Path(data_file))

    body = {"name": "テストセット", "source_strategy_id": "s1_pattern"}
    res = client.post("/api/v2/lab/condition-sets", json=body)
    assert res.status_code == 201
    result = res.json()
    assert result["source_strategy_id"] == "s1_pattern"
