from unittest.mock import Mock

import pytest
import requests
import yookassa_client


def test_create_payment_adds_receipt_when_enabled(monkeypatch):
    monkeypatch.setattr(yookassa_client.config, "YOOKASSA_SHOP_ID", "shop")
    monkeypatch.setattr(yookassa_client.config, "YOOKASSA_SECRET_KEY", "secret")
    monkeypatch.setattr(yookassa_client.config, "RECEIPT_ENABLED", True)
    response = Mock(status_code=200)
    response.json.return_value = {"id": "pay-1"}
    monkeypatch.setattr("requests.post", Mock(return_value=response))
    yookassa_client.create_payment(2990, "Подписка", customer_email="buyer@example.com")
    body = __import__("requests").post.call_args.kwargs["json"]
    assert body["receipt"]["customer"] == {"email": "buyer@example.com"}
    assert body["receipt"]["items"][0]["payment_subject"] == "service"
    assert body["receipt"]["items"][0]["payment_mode"] == "full_payment"


def test_create_payment_rejects_receipt_without_email(monkeypatch):
    monkeypatch.setattr(yookassa_client.config, "YOOKASSA_SHOP_ID", "shop")
    monkeypatch.setattr(yookassa_client.config, "YOOKASSA_SECRET_KEY", "secret")
    monkeypatch.setattr(yookassa_client.config, "RECEIPT_ENABLED", True)
    try:
        yookassa_client.create_payment(2990, "Подписка")
    except yookassa_client.YooKassaError as exc:
        assert "email" in str(exc)
    else:
        raise AssertionError("receipt without email must fail")


def test_get_refund_uses_authenticated_provider_lookup(monkeypatch):
    monkeypatch.setattr(yookassa_client.config, "YOOKASSA_SHOP_ID", "test-shop")
    monkeypatch.setattr(yookassa_client.config, "YOOKASSA_SECRET_KEY", "test-secret")
    response = Mock(status_code=200)
    response.json.return_value = {"id": "refund-1", "status": "succeeded", "payment_id": "payment-1"}
    transport = Mock(return_value=response)
    monkeypatch.setattr(requests, "get", transport)

    assert yookassa_client.get_refund("refund-1") == response.json.return_value
    transport.assert_called_once_with(
        "https://api.yookassa.ru/v3/refunds/refund-1",
        auth=("test-shop", "test-secret"), timeout=30,
    )


def test_get_refund_rejects_provider_error(monkeypatch):
    monkeypatch.setattr(yookassa_client.config, "YOOKASSA_SHOP_ID", "test-shop")
    monkeypatch.setattr(yookassa_client.config, "YOOKASSA_SECRET_KEY", "test-secret")
    response = Mock(status_code=404, text="test refund missing")
    monkeypatch.setattr(requests, "get", Mock(return_value=response))

    with pytest.raises(yookassa_client.YooKassaError, match="refund lookup failed \\(404\\)"):
        yookassa_client.get_refund("refund-missing")
    response.json.assert_not_called()


def test_get_refund_maps_network_failure_to_retryable_verification_error(monkeypatch):
    monkeypatch.setattr(yookassa_client.config, "YOOKASSA_SHOP_ID", "test-shop")
    monkeypatch.setattr(yookassa_client.config, "YOOKASSA_SECRET_KEY", "test-secret")
    monkeypatch.setattr(requests, "get", Mock(side_effect=requests.Timeout("private provider details")))

    with pytest.raises(yookassa_client.YooKassaError, match="refund lookup unavailable") as caught:
        yookassa_client.get_refund("refund-1")
    assert "private provider details" not in str(caught.value)
    assert caught.value.__suppress_context__
