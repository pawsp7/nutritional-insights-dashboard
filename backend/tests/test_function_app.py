"""Calls the HTTP handlers directly with a fake data source (no Azure, no Functions host)."""
import json
import time
from datetime import datetime, timezone

import azure.functions as func
import pytest

import data_source
import function_app


@pytest.fixture
def fake_store(monkeypatch, real_df):
    calls = {"downloads": 0}

    def fake_download(cfg):
        calls["downloads"] += 1
        return data_source.Dataset(
            df=real_df, account="teststore", container=cfg["container"], blob=cfg["blob"], etag='"0x1"',
            last_modified=datetime(2026, 10, 1, tzinfo=timezone.utc), size_bytes=702514,
            loaded_at=time.time(), load_ms=12.3, raw_rows=7806,
        )

    monkeypatch.setattr(data_source, "_download", fake_download)
    monkeypatch.setattr(data_source, "_cached", None)
    return calls


def call(handler, **params):
    user_function = handler.build().get_user_function()
    req = func.HttpRequest(method="GET", url=f"/api/{user_function.__name__}", params=params, body=b"")
    resp = user_function(req)
    return resp.status_code, json.loads(resp.get_body())


def test_insights_returns_charts_and_metadata(fake_store):
    status, body = call(function_app.insights)
    assert status == 200
    assert body["data"]["dietTypes"] == ["dash", "keto", "mediterranean", "paleo", "vegan"]
    meta = body["meta"]
    assert meta["endpoint"] == "insights"
    assert meta["executionTimeMs"] >= 0
    assert meta["cache"] == "miss"
    assert meta["dataset"]["blob"] == "All_Diets.csv"
    assert meta["dataset"]["storageAccount"] == "teststore"


def test_second_call_uses_warm_cache_and_refresh_bypasses_it(fake_store):
    call(function_app.insights)
    _, warm = call(function_app.insights, diet="vegan")
    assert warm["meta"]["cache"] == "hit"
    assert fake_store["downloads"] == 1
    _, fresh = call(function_app.insights, refresh="true")
    assert fresh["meta"]["cache"] == "miss"
    assert fake_store["downloads"] == 2


def test_unknown_diet_is_404(fake_store):
    status, body = call(function_app.insights, diet="carnivore")
    assert status == 404
    assert "carnivore" in body["error"]


def test_recipes_validates_parameters(fake_store):
    assert call(function_app.recipes, pageSize="500")[0] == 400
    assert call(function_app.recipes, page="abc")[0] == 400
    assert call(function_app.recipes, sort="sugar")[0] == 400
    status, body = call(function_app.recipes, diet="keto", sort="fat", order="asc", page="2", pageSize="5")
    assert status == 200
    assert body["meta"]["filters"] == {"diet": "keto", "search": "", "sort": "fat", "order": "asc", "page": 2, "pageSize": 5}
    assert len(body["data"]["items"]) == 5


def test_clusters_k_range(fake_store):
    assert call(function_app.clusters, k="9")[0] == 400
    status, body = call(function_app.clusters, k="3", diet="paleo")
    assert status == 200
    assert body["data"]["k"] == 3
    assert sum(c["size"] for c in body["data"]["clusters"]) == 1274


def test_missing_storage_setting_is_503(monkeypatch):
    monkeypatch.setattr(data_source, "_cached", None)
    status, body = call(function_app.insights)
    assert status == 503
    assert "DIETS_STORAGE_CONNECTION" in body["error"]
    assert "executionTimeMs" in body["meta"]


def test_health_reports_unreachable_blob(monkeypatch):
    status, body = call(function_app.health)
    assert status == 503
    assert body["storageConfigured"] is False
    assert body["blobReachable"] is False
