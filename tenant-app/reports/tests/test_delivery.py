from reports import delivery


def _configure(monkeypatch, *, url="https://marketshelper.ru", secret="s3cr3t", slug="acme"):
    monkeypatch.setattr(delivery.config, "INTERNAL_API_URL", url)
    monkeypatch.setattr(delivery.config, "INTERNAL_API_SECRET", secret)
    monkeypatch.setattr(delivery.config, "TENANT_SLUG", slug)


def test_email_is_configured_true_when_all_three_set(monkeypatch):
    _configure(monkeypatch)
    assert delivery.email_is_configured() is True


def test_email_is_configured_false_when_any_missing(monkeypatch):
    _configure(monkeypatch, url="")
    assert delivery.email_is_configured() is False


def test_send_report_email_returns_false_when_not_configured(monkeypatch):
    _configure(monkeypatch, secret="")
    assert delivery.send_report_email("Отчёт", b"%PDF-1.4...", "report.pdf") is False


def test_send_report_email_posts_to_gateway_and_returns_true_on_200(monkeypatch):
    _configure(monkeypatch)
    captured = {}

    class FakeResponse:
        status_code = 200
        text = ""

    def fake_post(url, headers=None, data=None, files=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["data"] = data
        captured["files"] = files
        return FakeResponse()

    monkeypatch.setattr(delivery.requests, "post", fake_post)
    ok = delivery.send_report_email("Еженедельная сводка", b"%PDF-1.4...", "report.pdf")

    assert ok is True
    assert captured["url"] == "https://marketshelper.ru/internal/send-report-email"
    assert captured["headers"]["X-Internal-Secret"] == "s3cr3t"
    assert captured["data"]["slug"] == "acme"
    assert captured["data"]["report_name"] == "Еженедельная сводка"
    assert captured["files"]["file"][0] == "report.pdf"


def test_send_report_email_returns_false_on_non_200(monkeypatch):
    _configure(monkeypatch)

    class FakeResponse:
        status_code = 403
        text = "forbidden"

    monkeypatch.setattr(delivery.requests, "post", lambda *a, **k: FakeResponse())
    assert delivery.send_report_email("Отчёт", b"%PDF-1.4...", "report.pdf") is False


def test_send_report_email_returns_false_on_network_error(monkeypatch):
    _configure(monkeypatch)

    def raise_error(*a, **k):
        raise ConnectionError("no route to host")

    monkeypatch.setattr(delivery.requests, "post", raise_error)
    assert delivery.send_report_email("Отчёт", b"%PDF-1.4...", "report.pdf") is False


def test_send_report_telegram_posts_to_gateway_and_returns_true_on_200(monkeypatch):
    _configure(monkeypatch)
    captured = {}

    class FakeResponse:
        status_code = 200
        text = ""

    def fake_post(url, headers=None, data=None, files=None, timeout=None):
        captured["url"] = url
        captured["data"] = data
        return FakeResponse()

    monkeypatch.setattr(delivery.requests, "post", fake_post)
    ok = delivery.send_report_telegram("Отчёт", b"%PDF-1.4...", "report.pdf")

    assert ok is True
    assert captured["url"] == "https://marketshelper.ru/internal/send-report-telegram"
    assert captured["data"]["slug"] == "acme"


def test_send_report_telegram_returns_false_when_not_configured(monkeypatch):
    _configure(monkeypatch, secret="")
    assert delivery.send_report_telegram("Отчёт", b"%PDF-1.4...", "report.pdf") is False


def test_telegram_is_linked_false_when_not_configured(monkeypatch):
    _configure(monkeypatch, url="")
    assert delivery.telegram_is_linked() is False


def test_telegram_is_linked_reflects_gateway_response(monkeypatch):
    _configure(monkeypatch)

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"linked": True}

    monkeypatch.setattr(delivery.requests, "get", lambda *a, **k: FakeResponse())
    assert delivery.telegram_is_linked() is True


def test_telegram_is_linked_false_on_non_200(monkeypatch):
    _configure(monkeypatch)

    class FakeResponse:
        status_code = 500

        def json(self):
            return {}

    monkeypatch.setattr(delivery.requests, "get", lambda *a, **k: FakeResponse())
    assert delivery.telegram_is_linked() is False


def test_telegram_is_linked_false_on_network_error(monkeypatch):
    _configure(monkeypatch)

    def raise_error(*a, **k):
        raise ConnectionError("no route to host")

    monkeypatch.setattr(delivery.requests, "get", raise_error)
    assert delivery.telegram_is_linked() is False


def test_request_telegram_link_code_returns_code_and_ttl(monkeypatch):
    _configure(monkeypatch)

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"code": "AB12CD", "ttl_minutes": 15}

    monkeypatch.setattr(delivery.requests, "post", lambda *a, **k: FakeResponse())
    result = delivery.request_telegram_link_code()

    assert result == ("AB12CD", 15)


def test_request_telegram_link_code_returns_none_when_not_configured(monkeypatch):
    _configure(monkeypatch, slug="")
    assert delivery.request_telegram_link_code() is None


def test_request_telegram_link_code_returns_none_on_failure(monkeypatch):
    _configure(monkeypatch)

    class FakeResponse:
        status_code = 404
        text = "unknown tenant"

    monkeypatch.setattr(delivery.requests, "post", lambda *a, **k: FakeResponse())
    assert delivery.request_telegram_link_code() is None
