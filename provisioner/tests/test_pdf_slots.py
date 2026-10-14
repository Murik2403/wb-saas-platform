"""The shared PDF slot mount: opt-in, validated, prepared by a narrow helper, identical spec when not configured."""
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

import app


def request():
    return app.TenantRequest(internal_api_secret="s", billing_url="https://example.test/b", logout_url="https://example.test/l",
                             internal_api_url="https://example.test/i")


def test_not_configured_changes_nothing(monkeypatch):
    monkeypatch.setattr(app, "PDF_SLOT_HOST_DIR", "")
    spec = app.tenant_container_spec("shop-a", request())
    assert set(spec["volumes"]) == {"wb-tenant-data-shop-a"}
    assert "WB_SAAS_PDF_SLOT_DIR" not in spec["environment"] and "WB_SAAS_PDF_SLOTS" not in spec["environment"]
    client = Mock()
    app._prepare_pdf_slots(client)
    client.containers.run.assert_not_called()


@pytest.mark.parametrize("source", ["/opt/wb-saas/pdf-slots", "wb-pdf-slots"])
def test_configured_tenants_mount_the_shared_slots_read_write_and_learn_the_count(monkeypatch, source):
    monkeypatch.setattr(app, "PDF_SLOT_HOST_DIR", source)
    monkeypatch.setattr(app, "PDF_SLOTS", 3)
    spec = app.tenant_container_spec("shop-a", request())
    assert spec["volumes"][source] == {"bind": "/pdf-slots", "mode": "rw"}
    assert spec["volumes"]["wb-tenant-data-shop-a"] == {"bind": "/app/data", "mode": "rw"}
    assert spec["environment"]["WB_SAAS_PDF_SLOT_DIR"] == "/pdf-slots" and spec["environment"]["WB_SAAS_PDF_SLOTS"] == "3"
    assert spec["cap_drop"] == ["ALL"] and spec["read_only"] is True and spec["user"] == "10001:10001"  # nothing else loosened


@pytest.mark.parametrize("source,slots", [("/opt/../etc", 2), ("/opt/wb saas", 2), ("relative/dir", 2), ("/var/run/docker.sock;x", 2), ("/opt/slots", 0), ("/opt/slots", 99)])
def test_invalid_configuration_is_refused_before_any_container_starts(monkeypatch, source, slots):
    monkeypatch.setattr(app, "PDF_SLOT_HOST_DIR", source)
    monkeypatch.setattr(app, "PDF_SLOTS", slots)
    with pytest.raises(HTTPException) as refused:
        app.tenant_container_spec("shop-a", request())
    assert refused.value.status_code == 503


def test_the_directory_is_prepared_by_a_hardened_helper_only_for_the_slot_mount(monkeypatch):
    monkeypatch.setattr(app, "PDF_SLOT_HOST_DIR", "/opt/wb-saas/pdf-slots")
    client = Mock()
    client.containers.run.return_value.wait.return_value = {"StatusCode": 0}
    app._prepare_pdf_slots(client)
    kwargs = client.containers.run.call_args.kwargs
    assert kwargs["volumes"] == {"/opt/wb-saas/pdf-slots": {"bind": "/slots", "mode": "rw"}}
    assert kwargs["network_disabled"] and kwargs["cap_drop"] == ["ALL"] and set(kwargs["cap_add"]) == {"CHOWN", "FOWNER"} and kwargs["read_only"]
    assert "chown 10001:10001 /slots" in kwargs["command"][2]
    client.containers.run.return_value.remove.assert_called_once_with(force=True)


def test_a_failed_preparation_aborts_the_start_and_still_removes_the_helper(monkeypatch):
    monkeypatch.setattr(app, "PDF_SLOT_HOST_DIR", "/opt/wb-saas/pdf-slots")
    client = Mock()
    client.containers.run.return_value.wait.return_value = {"StatusCode": 1}
    with pytest.raises(RuntimeError):
        app._prepare_pdf_slots(client)
    client.containers.run.return_value.remove.assert_called_once_with(force=True)
