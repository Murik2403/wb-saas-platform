"""Purge removes only a cabinet's data, never its shared PDF coordination mount."""
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

import app
from test_tenant_purge import AUX, PRIMARY, SLUG, Client, Container, Volume


@pytest.fixture
def client(monkeypatch):
    value = Client()
    monkeypatch.setattr(app, "_client", lambda: value)
    monkeypatch.setattr(app, "_remove_network", Mock())
    monkeypatch.setattr(app, "PDF_SLOTS", 2)
    return value


def attach_slots(client, monkeypatch, *, source="wb-pdf-slots", include_rollback=True):
    monkeypatch.setattr(app, "PDF_SLOT_HOST_DIR", source)
    if source.startswith("/"):
        mount = {"Type": "bind", "Source": source, "Destination": "/pdf-slots", "RW": True}
    else:
        client.volume_items[source] = Volume(client, source)
        mount = {**Container.mount(source, "/pdf-slots"), "RW": True}
    for container in ([client.current, client.rollback] if include_rollback else [client.current]):
        container.attrs["Mounts"].append(dict(mount))
        container.attrs["Config"]["Env"] += ["WB_SAAS_PDF_SLOT_DIR=/pdf-slots", "WB_SAAS_PDF_SLOTS=2"]
    return mount


@pytest.mark.parametrize("source", ["wb-pdf-slots", "/opt/wb-saas/pdf-slots"])
@pytest.mark.parametrize("include_rollback", [True, False])
def test_purge_accepts_exact_shared_slots_and_preserves_other_tenants(client, monkeypatch, source, include_rollback):
    mount = attach_slots(client, monkeypatch, source=source, include_rollback=include_rollback)
    foreign = client.add("wb-tenant-shop-b", running=True)
    foreign.attrs["Mounts"] = [dict(mount)]
    shared = client.volume_items.get(source)
    assert app.remove_tenant(SLUG, purge_volume=True) == {"status": "purged"}
    assert client.items == [foreign]
    assert PRIMARY not in client.volume_items and AUX not in client.volume_items
    if shared:
        assert client.volume_items == {source: shared}
        shared.remove.assert_not_called()
    foreign.stop.assert_not_called()
    foreign.remove.assert_not_called()


@pytest.mark.parametrize("change", [
    "wrong_source", "wrong_name", "wrong_type", "read_only", "missing_directory_env",
    "duplicate_directory_env", "wrong_directory_env", "duplicate_mount", "unexpected_target",
    "not_configured", "invalid_config", "own_data_collision", "wrong_volume_mountpoint",
    "missing_mount", "duplicate_count", "invalid_count", "unicode_count", "missing_shared_volume",
])
def test_unverified_pdf_mount_refuses_before_any_stop_or_remove(client, monkeypatch, change):
    attach_slots(client, monkeypatch)
    mount = client.rollback.attrs["Mounts"][-1]
    env = client.rollback.attrs["Config"]["Env"]
    if change == "wrong_source": mount["Source"] = "/volumes/untrusted"
    elif change == "wrong_name": mount["Name"] = "untrusted"
    elif change == "wrong_type": mount["Type"] = "bind"
    elif change == "read_only": mount["RW"] = False
    elif change == "missing_directory_env": env.remove("WB_SAAS_PDF_SLOT_DIR=/pdf-slots")
    elif change == "duplicate_directory_env": env.append("WB_SAAS_PDF_SLOT_DIR=/pdf-slots")
    elif change == "wrong_directory_env": env[env.index("WB_SAAS_PDF_SLOT_DIR=/pdf-slots")] = "WB_SAAS_PDF_SLOT_DIR=/other"
    elif change == "duplicate_mount": client.rollback.attrs["Mounts"].append(dict(mount))
    elif change == "unexpected_target": mount["Destination"] = "/customer"
    elif change == "not_configured": monkeypatch.setattr(app, "PDF_SLOT_HOST_DIR", "")
    elif change == "invalid_config": monkeypatch.setattr(app, "PDF_SLOT_HOST_DIR", "/opt/../etc")
    elif change == "own_data_collision":
        monkeypatch.setattr(app, "PDF_SLOT_HOST_DIR", PRIMARY)
        for container in (client.current, client.rollback):
            container.attrs["Mounts"][-1].update(Name=PRIMARY, Source="/volumes/" + PRIMARY)
    elif change == "wrong_volume_mountpoint": client.volume_items["wb-pdf-slots"].attrs["Mountpoint"] = "/other"
    elif change == "missing_mount": client.rollback.attrs["Mounts"].pop()
    elif change == "duplicate_count": env.append("WB_SAAS_PDF_SLOTS=2")
    elif change == "invalid_count": env[env.index("WB_SAAS_PDF_SLOTS=2")] = "WB_SAAS_PDF_SLOTS=0"
    elif change == "unicode_count": env[env.index("WB_SAAS_PDF_SLOTS=2")] = "WB_SAAS_PDF_SLOTS=²"
    elif change == "missing_shared_volume": del client.volume_items["wb-pdf-slots"]
    with pytest.raises(HTTPException) as failure:
        app.remove_tenant(SLUG, purge_volume=True)
    assert failure.value.status_code == 409
    for container in client.items:
        container.stop.assert_not_called()
        container.remove.assert_not_called()
    for volume in client.volume_items.values():
        volume.remove.assert_not_called()


def test_changed_slot_volume_mapping_between_inventories_preserves_all_data(client, monkeypatch):
    attach_slots(client, monkeypatch)
    original_get = client.volume
    reads = 0

    def changed(name):
        nonlocal reads
        value = original_get(name)
        if name == "wb-pdf-slots":
            reads += 1
            if reads == 2:
                value.attrs["Mountpoint"] = "/changed-after-first-inventory"
        return value

    monkeypatch.setattr(client.volumes, "get", changed)
    with pytest.raises(HTTPException):
        app.remove_tenant(SLUG, purge_volume=True)
    client.current.stop.assert_not_called()
    client.rollback.remove.assert_not_called()


def test_bind_source_substitution_is_rejected_before_data_removal(client, monkeypatch):
    attach_slots(client, monkeypatch, source="/opt/wb-saas/pdf-slots")
    client.rollback.attrs["Mounts"][-1]["Source"] = "/opt/other-slots"
    with pytest.raises(HTTPException) as failure:
        app.remove_tenant(SLUG, purge_volume=True)
    assert failure.value.status_code == 409
    client.current.stop.assert_not_called()
    client.rollback.remove.assert_not_called()
