import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from test_display_contract import api


def _upload(monkeypatch, tmp_path, previous=True, account="acc-1"):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "p.db")
    client = Mock()
    client.upload_product_prices.return_value = {"data": {"id": 77}, "error": False}
    monkeypatch.setattr(api, "get_token", lambda: "fixture")
    monkeypatch.setattr(api, "WBAPI", lambda *a, **kw: client)
    body = {"goods": [{"nmID": 1, "price": 900, "discount": 10}, {"nmID": 2, "price": 500, "discount": 0}]}
    if previous:
        body["previous"] = [{"nmID": 1, "vendorCode": "A-1", "price": 1000, "discount": 5}]
    request = SimpleNamespace(headers={"x-tenant-account": account} if account else {})
    result = api.upload_wb_product_prices(api.PriceUploadInput(**body), request)
    return client, result


def test_items_recorded_and_wb_payload_unchanged(monkeypatch, tmp_path):
    client, result = _upload(monkeypatch, tmp_path)
    assert result["data"]["id"] == 77
    sent = client.upload_product_prices.call_args[0][0]
    assert sent == [{"nmID": 1, "price": 900, "discount": 10}, {"nmID": 2, "price": 500, "discount": 0}]
    detail = api.get_wb_price_upload_items(77)
    assert detail["actor"] == "acc-1" and detail["item_count"] == 2
    first, second = detail["items"]
    assert (first["vendor_code"], first["old_price"], first["old_discount"], first["new_price"], first["new_discount"]) == ("A-1", 1000, 5, 900, 10)
    assert second["old_price"] is None and second["vendor_code"] is None
    assert api.list_wb_price_uploads()["items"][0]["actor"] == "acc-1"


def test_journal_failure_never_fails_upload(monkeypatch, tmp_path):
    real_connect = sqlite3.connect

    # Make the item INSERT blow up by dropping the table after schema init.
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "p.db")
    api._init_redesign_schema()
    with real_connect(api.DB_PATH) as conn:
        conn.execute("DROP TABLE price_upload_item")
    monkeypatch.setattr(api, "_init_redesign_schema", lambda: None)
    client = Mock()
    client.upload_product_prices.return_value = {"data": {"id": 5}}
    monkeypatch.setattr(api, "get_token", lambda: "fixture")
    monkeypatch.setattr(api, "WBAPI", lambda *a, **kw: client)
    result = api.upload_wb_product_prices(api.PriceUploadInput(goods=[{"nmID": 1, "price": 100, "discount": 0}]))
    assert result["data"]["id"] == 5
    with real_connect(api.DB_PATH) as conn:
        assert conn.execute("SELECT COUNT(*) FROM redesign_price_uploads").fetchone()[0] == 1


def test_unknown_upload_404_and_old_upload_without_items(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "p.db")
    with pytest.raises(HTTPException) as exc:
        api.get_wb_price_upload_items(404)
    assert exc.value.status_code == 404
    api._init_redesign_schema()
    with sqlite3.connect(api.DB_PATH) as conn:
        conn.execute("INSERT INTO redesign_price_uploads(wb_upload_id, goods_count, status, created_at, updated_at) VALUES (9, 3, 'Цены обновлены', 'x', 'x')")
    detail = api.get_wb_price_upload_items(9)
    assert detail["items"] == [] and detail["item_count"] == 0 and detail["actor"] is None


def test_status_poll_stores_item_errors(monkeypatch, tmp_path):
    _upload(monkeypatch, tmp_path, account=None)
    client = Mock()
    client.get_price_upload_status.return_value = {"label": "Обновлено частично", "terminal": True, "items": [{"nmID": 2, "errorText": "price rejected"}, {"nmID": 1}]}
    monkeypatch.setattr(api, "WBAPI", lambda *a, **kw: client)
    api.get_wb_price_upload_status(77)
    detail = api.get_wb_price_upload_items(77)
    assert detail["error_count"] == 1
    assert [i["result_error"] for i in detail["items"]] == ["", "price rejected"]
