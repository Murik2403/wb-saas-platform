from unittest.mock import Mock

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
