from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from test_display_contract import api
from wb_api import WBAPI


def test_product_card_update_uses_full_card_and_no_retry(monkeypatch):
    client = WBAPI("fixture")
    request = Mock(return_value={"error": False})
    monkeypatch.setattr(client, "_request", request)
    result = client.update_product_card({"nmID": 42, "title": "Новый заголовок", "sizes": []})
    assert result["error"] is False
    request.assert_called_once_with(
        "POST",
        "https://content-api.wildberries.ru/content/v2/cards/update",
        json=[{"nmID": 42, "title": "Новый заголовок", "sizes": []}],
        retry_safe=False,
    )


def test_product_media_save_uses_complete_url_list_and_no_retry(monkeypatch):
    client = WBAPI("fixture")
    request = Mock(return_value={"error": False})
    monkeypatch.setattr(client, "_request", request)
    client.save_product_media(42, ["https://cdn.example/one.jpg", "https://cdn.example/two.jpg"])
    request.assert_called_once_with(
        "POST",
        "https://content-api.wildberries.ru/content/v3/media/save",
        json={"nmId": 42, "data": ["https://cdn.example/one.jpg", "https://cdn.example/two.jpg"]},
        retry_safe=False,
    )


@pytest.mark.parametrize("urls", [[], ["ftp://bad.example/image.jpg"], ["https://ok.example/image.jpg"] * 31])
def test_product_media_save_rejects_unsafe_lists(urls):
    with pytest.raises(ValueError):
        WBAPI("fixture").save_product_media(42, urls)


def test_product_card_update_requires_confirmation_and_preserves_fields(monkeypatch):
    client = Mock()
    client.token_scope_info.return_value = {"read_only": False}
    client.get_product_cards.return_value = [{
        "nmID": 42,
        "title": "Старый заголовок",
        "description": "Описание",
        "brand": "Бренд",
        "sizes": [{"chrtID": 7}],
        "photos": [{"big": "https://cdn.example/old.jpg"}],
    }]
    client.update_product_card.return_value = {"error": False}
    monkeypatch.setattr(api, "_wb_write_client", lambda: client)
    with pytest.raises(HTTPException) as exc:
        api.update_wb_product_card(42, api.ProductCardUpdateInput(title="Новый заголовок"))
    assert exc.value.status_code == 422

    result = api.update_wb_product_card(42, api.ProductCardUpdateInput(confirmed=True, title="Новый заголовок"))
    assert result["status"] == "submitted"
    submitted = client.update_product_card.call_args.args[0]
    assert submitted["title"] == "Новый заголовок"
    assert submitted["sizes"] == [{"chrtID": 7}]
    assert submitted["photos"] == [{"big": "https://cdn.example/old.jpg"}]


def test_product_media_route_requires_confirmation(monkeypatch):
    client = Mock()
    client.token_scope_info.return_value = {"read_only": False}
    client.save_product_media.return_value = {"error": False}
    monkeypatch.setattr(api, "_wb_write_client", lambda: client)
    with pytest.raises(HTTPException) as exc:
        api.save_wb_product_media(42, api.ProductCardMediaInput(urls=["https://cdn.example/image.jpg"]))
    assert exc.value.status_code == 422
    result = api.save_wb_product_media(42, api.ProductCardMediaInput(confirmed=True, urls=["https://cdn.example/image.jpg"]))
    assert result["media_count"] == 1
    client.save_product_media.assert_called_once_with(42, ["https://cdn.example/image.jpg"])


def test_product_card_write_routes_are_registered():
    paths = api.app.openapi()["paths"]
    assert "/api/v1/products/cards/{nm_id}" in paths
    assert "/api/v1/products/cards/{nm_id}/media" in paths
