"""Docker's actual in-use behavior is modelled: even stopped refs block removal."""
from unittest.mock import Mock
from concurrent.futures import ThreadPoolExecutor
import threading
import pytest
from fastapi import HTTPException
from docker.errors import NotFound
import app

SLUG = "shop-a"
PRIMARY = "wb-tenant-data-shop-a"
AUX = "a" * 64


class Container:
    def __init__(self, client, name, running=False, aux=False):
        self.client = client
        self.id = name
        mounts = [self.mount(PRIMARY, "/app/data")]
        if aux:
            mounts.append(self.mount(AUX, "/app/tenant-app/data"))
        self.attrs = {"Name": "/" + name, "Config": {"Labels": {"wb-saas.slug": SLUG, "traefik.docker.network": "wbsaas-tenant-shop-a"}, "Env": ["WB_SAAS_TENANT_SLUG=shop-a"], "Volumes": {"/app/tenant-app/data": {}}}, "State": {"Running": running, "Status": "running" if running else "exited"}, "Mounts": mounts}
        self.stop = Mock(side_effect=self._stop)
        self.remove = Mock(side_effect=self._remove)

    @staticmethod
    def mount(name, target):
        return {"Type": "volume", "Name": name, "Destination": target, "Source": "/volumes/" + name}

    def reload(self):
        pass

    def _stop(self, **kwargs):
        self.attrs["State"] = {"Running": False, "Status": "exited"}

    def _remove(self, **kwargs):
        assert kwargs == {"force": False}
        assert not self.attrs["State"]["Running"]
        self.client.items.remove(self)


class Volume:
    def __init__(self, client, name):
        self.name = name
        self.client = client
        self.attrs = {"Mountpoint": "/volumes/" + name}
        self.remove = Mock(side_effect=self._remove)

    def reload(self):
        pass

    def _remove(self, **kwargs):
        assert kwargs == {"force": False}
        if self.client.list(all=True, filters={"volume": self.name}, limit=1):
            raise RuntimeError("volume is in use")
        del self.client.volume_items[self.name]


class Client:
    def __init__(self, aux=True):
        self.items = []
        self.containers = self
        self.volume_items = {PRIMARY: Volume(self, PRIMARY)}
        if aux:
            self.volume_items[AUX] = Volume(self, AUX)
        self.volumes = Mock(get=self.volume)
        self.current = self.add("wb-tenant-shop-a", running=True, aux=aux)
        self.rollback = self.add("wb-tenant-shop-a-rollback-audit-123", aux=aux)

    def add(self, name, **kwargs):
        container = Container(self, name, **kwargs)
        self.items.append(container)
        return container

    def get(self, name):
        for item in self.items:
            if name in {item.id, item.attrs["Name"].lstrip("/")}:
                return item
        raise NotFound("absent")

    def volume(self, name):
        if name not in self.volume_items:
            raise NotFound("absent")
        return self.volume_items[name]

    def list(self, all, filters, limit):
        return [item for item in self.items if any(mount.get("Name") == filters["volume"] for mount in item.attrs["Mounts"])][:limit]


@pytest.fixture
def client(monkeypatch):
    result = Client()
    monkeypatch.setattr(app, "_client", lambda: result)
    monkeypatch.setattr(app, "_remove_network", Mock())
    return result


def test_purge_removes_stopped_rollbacks_then_current_and_both_data_volumes(client):
    volumes = list(client.volume_items.values())
    assert app.remove_tenant(SLUG, purge_volume=True) == {"status": "purged"}
    assert client.items == []
    assert client.volume_items == {}
    client.rollback.stop.assert_not_called()
    client.rollback.remove.assert_called_once_with(force=False)
    client.current.stop.assert_called_once_with(timeout=10)
    for volume in volumes:
        volume.remove.assert_called_once_with(force=False)


def test_distinct_old_auxiliary_volume_is_removed_only_after_its_scoped_references(client):
    old_aux = "b" * 64
    client.volume_items[old_aux] = Volume(client, old_aux)
    client.rollback.attrs["Mounts"][1] = Container.mount(old_aux, "/app/tenant-app/data")
    assert app.remove_tenant(SLUG, purge_volume=True)["status"] == "purged"
    assert client.volume_items == {}


def test_running_foreign_aux_reference_in_last_inventory_preserves_current(client):
    original = client.list
    reads = 0
    def changed(**kwargs):
        nonlocal reads
        if kwargs["filters"]["volume"] == PRIMARY:
            reads += 1
            if reads == 3:
                foreign = client.add("foreign-worker", running=True)
                foreign.attrs["Mounts"] = [Container.mount(AUX, "/data")]
        return original(**kwargs)
    client.list = changed
    with pytest.raises(HTTPException): app.remove_tenant(SLUG, purge_volume=True)
    client.current.stop.assert_not_called()
    client.current.remove.assert_not_called()


@pytest.mark.parametrize("change", ["foreign_name", "foreign_slug", "running_rollback", "wrong_target", "wrong_source", "bind", "undeclared_aux", "extra_mount"])
def test_unsafe_reference_refuses_before_any_container_removed(client, change):
    attrs = client.rollback.attrs
    if change == "foreign_name": attrs["Name"] = "/foreign-worker"
    if change == "foreign_slug": attrs["Config"]["Labels"]["wb-saas.slug"] = "other"
    if change == "running_rollback": attrs["State"] = {"Running": True, "Status": "running"}
    if change == "wrong_target": attrs["Mounts"][0]["Destination"] = "/other"
    if change == "wrong_source": attrs["Mounts"][0]["Source"] = "/other"
    if change == "bind": attrs["Mounts"][0]["Type"] = "bind"
    if change == "undeclared_aux": attrs["Config"]["Volumes"] = {}
    if change == "extra_mount": attrs["Mounts"].append(Container.mount("other", "/customer"))
    with pytest.raises(HTTPException) as failure:
        app.remove_tenant(SLUG, purge_volume=True)
    assert failure.value.status_code == 409
    client.current.stop.assert_not_called()
    client.current.remove.assert_not_called()
    client.rollback.remove.assert_not_called()


def test_auxiliary_shared_with_foreign_container_fails_before_primary_removed(client):
    foreign = client.add("foreign-worker", running=True)
    foreign.attrs["Mounts"] = [Container.mount(AUX, "/data")]
    with pytest.raises(HTTPException): app.remove_tenant(SLUG, purge_volume=True)
    client.current.stop.assert_not_called()
    client.rollback.remove.assert_not_called()


def test_reference_limit_is_bounded_and_refuses_without_partial_deletion(client):
    for number in range(app._PURGE_MAX_REFERENCES):
        client.add(f"wb-tenant-shop-a-rollback-{number}")
    with pytest.raises(HTTPException): app.remove_tenant(SLUG, purge_volume=True)
    client.current.stop.assert_not_called()
    assert all(not item.remove.called for item in client.items)


def test_changed_inventory_second_read_refuses_before_removal(client):
    original = client.list
    reads = 0
    def changed(**kwargs):
        nonlocal reads
        if kwargs["filters"]["volume"] == PRIMARY:
            reads += 1
            if reads == 2: client.add("wb-tenant-shop-a-refresh-rollback-new")
        return original(**kwargs)
    client.list = changed
    with pytest.raises(HTTPException): app.remove_tenant(SLUG, purge_volume=True)
    client.current.stop.assert_not_called()
    client.rollback.remove.assert_not_called()


def test_already_purged_is_idempotent_without_deleting_foreign_objects(client):
    client.items = []
    client.volume_items = {}
    assert app.remove_tenant(SLUG, purge_volume=True) == {"status": "purged"}


def test_plain_remove_does_not_erase_rollback_or_volumes(client):
    assert app.remove_tenant(SLUG) == {"status": "removed"}
    client.rollback.remove.assert_not_called()
    assert set(client.volume_items) == {PRIMARY, AUX}


def test_concurrent_duplicate_reseed_fails_immediately_and_never_runs_twice(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    calls = []
    def reseed_demo():
        calls.append("refresh")
        entered.set()
        assert release.wait(2)
        return "refreshed"
    refresh = app._serialized_lifecycle(reseed_demo)
    docker_client = Mock()
    monkeypatch.setattr(app, "_client", docker_client)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(refresh)
        assert entered.wait(2)
        try:
            # Test both the shared wrapper and the actual route's wrapper;
            # neither duplicate may queue or begin Docker/reseed work.
            with pytest.raises(HTTPException) as failure: refresh()
            assert failure.value.status_code == 409
            with pytest.raises(HTTPException) as actual: app.reseed_demo()
            assert actual.value.status_code == 409
            docker_client.assert_not_called()
            assert calls == ["refresh"]
        finally:
            release.set()
        assert first.result(timeout=2) == "refreshed"
    assert calls == ["refresh"]
