import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tenant-app"))
import ozon_normalization_api as api


def payload():
    return {"start_date": "2026-09-01", "end_date": "2026-09-30", "namespace": "main",
            "expected_fingerprint": "a" * 64, "source_occurrence_key": "occurrence", "document_event_ids": ["sale-1"]}


@pytest.fixture
def client():
    app = FastAPI(); app.include_router(api.router)
    with TestClient(app) as result:
        yield result


def test_read_returns_unknown_and_does_not_make_marketplace_calls(client, monkeypatch):
    read = Mock(return_value={"loaded": False, "result_after_fifo_rub": None})
    monkeypatch.setattr(api, "preview", read)
    response = client.get("/api/v1/ozon-accounting/normalization/preview?start_date=2026-09-01&end_date=2026-09-30")
    assert response.status_code == 200 and response.json()["result_after_fifo_rub"] is None
    read.assert_called_once_with("2026-09-01", "2026-09-30", "main")


def test_document_selector_uses_bounded_namespace_reader(client, monkeypatch):
    read = Mock(return_value={"lines": [], "bounded": True}); monkeypatch.setattr(api, "documents", read)
    response = client.get("/api/v1/ozon-accounting/normalization/documents?start_date=2026-09-01&end_date=2026-09-30&namespace=second")
    assert response.status_code == 200 and response.json()["bounded"] is True
    read.assert_called_once_with("2026-09-01", "2026-09-30", "second")
    read.side_effect = ValueError("Превышен безопасный объём")
    assert client.get("/api/v1/ozon-accounting/normalization/documents?start_date=2026-09-01&end_date=2026-09-30").status_code == 422


@pytest.mark.parametrize("role", [None, "viewer", "worker", "bogus"])
def test_link_requires_owner_or_manager(client, monkeypatch, role):
    write = Mock(); monkeypatch.setattr(api, "confirm_link", write)
    response = client.post("/api/v1/ozon-accounting/normalization/link", json=payload(), headers={"x-tenant-role": role} if role else {})
    assert response.status_code == 403
    write.assert_not_called()


@pytest.mark.parametrize("role", ["owner", "manager"])
def test_local_confirmation_passes_actor_and_version(client, monkeypatch, role):
    write = Mock(return_value={"saved": True, "duplicate": False}); monkeypatch.setattr(api, "confirm_link", write)
    response = client.post("/api/v1/ozon-accounting/normalization/link", json=payload(), headers={"x-tenant-role": role, "x-tenant-account": "member"})
    assert response.status_code == 200
    assert write.call_args.args == ("2026-09-01", "2026-09-30", "main", "a" * 64, "occurrence", ["sale-1"], "member")


def test_stale_source_conflict_and_bad_request_never_write(client, monkeypatch):
    write = Mock(side_effect=ValueError("Источник изменился")); monkeypatch.setattr(api, "confirm_link", write)
    response = client.post("/api/v1/ozon-accounting/normalization/link", json=payload(), headers={"x-tenant-role": "owner"})
    assert response.status_code == 409
    write.reset_mock()
    response = client.post("/api/v1/ozon-accounting/normalization/link", json={**payload(), "send_prices_to_ozon": True}, headers={"x-tenant-role": "owner"})
    assert response.status_code == 422
    write.assert_not_called()
