"""Shared start budget: atomic claims, refusal/wait, release after failure, running cabinets untouched.

Everything runs against an in-memory Docker double and isolated slugs: no daemon, no production data.
"""
import threading
import time

import pytest
from docker.errors import NotFound
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app
import tenant_budget

MIB = 1024 * 1024
RESERVATION = 384 * MIB


class FakeContainer:
    def __init__(self, docker, name, slug, reservation, status="running"):
        self._docker, self.name = docker, name
        self.attrs = {"State": {"Status": status}, "Config": {"Labels": {"wb-saas.slug": slug}},
                      "HostConfig": {"MemoryReservation": reservation}}

    def reload(self):
        pass

    def stop(self, timeout=10):
        self._docker.calls.append(("stop", self.name))
        self.attrs["State"]["Status"] = "exited"

    def remove(self, force=False):
        self._docker.calls.append(("remove", self.name))
        self._docker.items.pop(self.name, None)


class FakeDocker:
    """containers.* and info() with the few behaviours admission reads."""

    def __init__(self, mem_total=16 * 1024 * MIB, run_delay=0.0, fail_slugs=()):
        self.items, self.calls, self.mem_total = {}, [], mem_total
        self.run_delay, self.fail_slugs = run_delay, set(fail_slugs)
        self._lock = threading.Lock()
        self.containers = self
        self.started = 0

    def info(self):
        return {"MemTotal": self.mem_total}

    def list(self, all=False, filters=None):
        with self._lock:
            return [c for c in self.items.values() if "wb-saas.slug" in c.attrs["Config"]["Labels"]]

    def get(self, name):
        with self._lock:
            if name not in self.items:
                raise NotFound(name)
            return self.items[name]

    def run(self, **spec):
        slug = spec["labels"]["wb-saas.slug"]
        time.sleep(self.run_delay)
        if slug in self.fail_slugs:
            with self._lock:  # Docker leaves the created-but-never-started container behind
                self.items[spec["name"]] = FakeContainer(self, spec["name"], slug, 0, status="created")
            raise RuntimeError("port is already allocated")
        reservation = int(spec["mem_reservation"][:-1]) * MIB
        with self._lock:
            self.items[spec["name"]] = FakeContainer(self, spec["name"], slug, reservation)
            self.started += 1

    def add_running(self, slug, reservation=RESERVATION):
        self.items[f"wb-tenant-{slug}"] = FakeContainer(self, f"wb-tenant-{slug}", slug, reservation)


def tenant_request():
    return app.TenantRequest(internal_api_secret="s", billing_url="https://example.test/b",
                             logout_url="https://example.test/l", internal_api_url="https://example.test/i")


@pytest.fixture
def env(monkeypatch):
    def make(slots, **docker_kwargs):
        docker = FakeDocker(**docker_kwargs)
        monkeypatch.setattr(app, "_client", lambda: docker)
        for name in ("_prepare_backup_directory", "_volume", "_prepare_tenant_volume", "_network", "_attach_traefik"):
            monkeypatch.setattr(app, name, lambda *a, **k: None)
        monkeypatch.setattr(app, "_BUDGET", tenant_budget.TenantBudget())
        monkeypatch.setattr(app, "TENANT_MEM_RESERVATION", "384m")
        monkeypatch.setattr(app, "TENANT_MEM_LIMIT", "1536m")
        monkeypatch.setattr(app, "TENANT_MEMORY_BUDGET", f"{slots * 384}m")
        monkeypatch.setattr(app, "TENANT_MIN_AVAILABLE", "0")
        monkeypatch.setattr(app, "TENANT_ADMISSION_WAIT", 0.0)
        monkeypatch.setattr(tenant_budget, "host_available_bytes", lambda: None)
        return docker
    return make


def create(slug):
    try:
        app.create_tenant(slug, tenant_request())
        return "ok"
    except HTTPException as exc:
        return exc


def test_parse_size_units():
    assert tenant_budget.parse_size("384m") == 384 * MIB
    assert tenant_budget.parse_size("1.5g") == 1536 * MIB
    assert tenant_budget.parse_size("1610612736") == 1610612736
    for bad in ("", "abc", "12 parsecs", "-1m"):
        with pytest.raises(ValueError):
            tenant_budget.parse_size(bad)


def test_reservation_is_set_and_never_exceeds_the_limit(monkeypatch):
    spec = app.tenant_container_spec("shop-a", tenant_request())
    assert spec["mem_reservation"] == "384m" and spec["mem_limit"] == app.TENANT_MEM_LIMIT
    monkeypatch.setattr(app, "TENANT_MEM_RESERVATION", "4g")
    monkeypatch.setattr(app, "TENANT_MEM_LIMIT", "768m")
    assert app.tenant_container_spec("shop-a", tenant_request())["mem_reservation"] == "768m"


def test_refuses_with_503_and_a_reason_when_the_budget_is_full(env):
    docker = env(slots=3)
    for slug in ("a", "b", "c"):
        assert create(slug) == "ok"
    refused = create("d")
    assert isinstance(refused, HTTPException) and refused.status_code == 503
    assert refused.headers == {"Retry-After": "30"}
    assert refused.detail["code"] == "capacity_exhausted" and refused.detail["reason"] == "budget"
    assert refused.detail["running_tenants"] == 3 and refused.detail["free_mib"] == 0
    assert "d" not in {c.attrs["Config"]["Labels"]["wb-saas.slug"] for c in docker.items.values()}


def test_running_cabinets_are_never_stopped_by_a_refusal(env):
    docker = env(slots=2)
    docker.add_running("old-1")
    docker.add_running("old-2")
    assert isinstance(create("new"), HTTPException)
    assert docker.calls == []
    assert all(c.attrs["State"]["Status"] == "running" for c in docker.items.values())


def test_containers_without_a_reservation_count_at_the_default(env):
    docker = env(slots=2)
    docker.add_running("legacy-1", reservation=0)  # started before reservations existed
    docker.add_running("legacy-2", reservation=0)
    assert isinstance(create("new"), HTTPException)


def test_stopped_cabinets_do_not_count(env):
    docker = env(slots=1)
    docker.add_running("asleep")
    docker.items["wb-tenant-asleep"].attrs["State"]["Status"] = "exited"
    assert create("new") == "ok"


def test_concurrent_requests_cannot_overspend_the_budget(env):
    docker = env(slots=12, run_delay=0.01)
    results, barrier = [], threading.Barrier(40)

    def worker(i):
        barrier.wait()
        results.append(create(f"t{i}"))
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(40)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    ok = [r for r in results if r == "ok"]
    refused = [r for r in results if isinstance(r, HTTPException)]
    assert len(ok) == 12 and len(refused) == 28 and docker.started == 12
    assert {r.status_code for r in refused} == {503}
    assert app._BUDGET.pending() == {}


def test_failed_start_releases_the_claim_and_removes_the_dead_container(env):
    docker = env(slots=1, fail_slugs={"broken"})
    failed = create("broken")
    assert isinstance(failed, HTTPException) and failed.status_code == 502
    assert app._BUDGET.pending() == {}
    assert "wb-tenant-broken" not in docker.items  # no name conflict on the retry
    assert create("healthy") == "ok"  # the freed room is usable at once


def test_failed_start_does_not_touch_a_container_that_existed_before(env):
    docker = env(slots=3, fail_slugs={"stuck"})
    docker.items["wb-tenant-stuck"] = FakeContainer(docker, "wb-tenant-stuck", "stuck", 0, status="exited")
    create("stuck")
    assert ("remove", "wb-tenant-stuck") not in docker.calls


def test_refusal_while_waiting_is_a_503_after_the_wait(env, monkeypatch):
    docker = env(slots=1)
    docker.add_running("only")
    monkeypatch.setattr(app, "TENANT_ADMISSION_WAIT", 0.3)
    started = time.monotonic()
    refused = create("late")
    assert isinstance(refused, HTTPException) and refused.status_code == 503
    assert 0.25 <= time.monotonic() - started < 3


def test_a_waiting_start_proceeds_when_a_stop_frees_room(env, monkeypatch):
    docker = env(slots=1)
    docker.add_running("leaving")
    monkeypatch.setattr(app, "TENANT_ADMISSION_WAIT", 10.0)
    outcome = []
    waiter = threading.Thread(target=lambda: outcome.append(create("arriving")))
    waiter.start()
    time.sleep(0.3)
    assert outcome == []  # still waiting: nothing was started or stopped for it
    monkeypatch.setattr(app, "_remove_network", lambda *a: None)
    app.stop_tenant("leaving")
    waiter.join(timeout=5)
    assert outcome == ["ok"] and "wb-tenant-arriving" in docker.items


def test_waiting_never_blocks_the_stop_that_would_free_room(env, monkeypatch):
    docker = env(slots=1)
    docker.add_running("leaving")
    monkeypatch.setattr(app, "TENANT_ADMISSION_WAIT", 10.0)
    waiter = threading.Thread(target=lambda: create("arriving"))
    waiter.start()
    time.sleep(0.2)
    monkeypatch.setattr(app, "_remove_network", lambda *a: None)
    done = threading.Event()
    threading.Thread(target=lambda: (app.stop_tenant("leaving"), done.set())).start()
    assert done.wait(timeout=3), "stop was blocked behind a waiting start"
    waiter.join(timeout=5)


def test_upgrade_of_a_running_cabinet_is_never_refused_even_when_over_budget(env, monkeypatch):
    docker = env(slots=1)  # two cabinets already run: the budget was lowered after they started
    docker.add_running("a")
    docker.add_running("b")
    monkeypatch.setattr(docker, "images", type("I", (), {"pull": staticmethod(lambda *a: None)})(), raising=False)
    app.upgrade_tenant("a", tenant_request())
    assert docker.items["wb-tenant-a"].attrs["State"]["Status"] == "running"
    assert ("stop", "wb-tenant-b") not in docker.calls


def test_upgrade_of_a_stopped_cabinet_needs_room_like_a_new_one(env, monkeypatch):
    docker = env(slots=1)
    docker.add_running("busy")
    monkeypatch.setattr(docker, "images", type("I", (), {"pull": staticmethod(lambda *a: None)})(), raising=False)
    with pytest.raises(HTTPException) as refused:
        app.upgrade_tenant("asleep", tenant_request())
    assert refused.value.status_code == 503


def test_low_host_memory_refuses_even_inside_the_budget(env, monkeypatch):
    env(slots=10)
    monkeypatch.setattr(app, "TENANT_MIN_AVAILABLE", "1g")
    monkeypatch.setattr(tenant_budget, "host_available_bytes", lambda: 1200 * MIB)
    monkeypatch.setattr(tenant_budget.TenantBudget, "acquire", _with_host(lambda: 1200 * MIB))
    refused = create("x")
    assert isinstance(refused, HTTPException) and refused.detail["reason"] == "host_memory"


def _with_host(reader):
    original = tenant_budget.TenantBudget.acquire

    def acquire(self, *args, **kwargs):
        kwargs.setdefault("host_available", reader)
        return original(self, *args, **kwargs)
    return acquire


def test_budget_defaults_to_a_fraction_of_host_memory(env, monkeypatch):
    docker = env(slots=1, mem_total=4096 * MIB)
    monkeypatch.setattr(app, "TENANT_MEMORY_BUDGET", "")
    monkeypatch.setattr(app, "TENANT_MEMORY_FRACTION", 0.5)  # 2048 MiB -> five 384 MiB cabinets
    assert [create(f"s{i}") for i in range(6)].count("ok") == 5


def test_budget_can_be_switched_off(env, monkeypatch):
    env(slots=1)
    monkeypatch.setattr(app, "TENANT_MEMORY_BUDGET", "off")
    assert [create(f"s{i}") for i in range(5)] == ["ok"] * 5


def test_unreadable_docker_state_admits_instead_of_inventing_a_refusal(monkeypatch):
    from unittest.mock import Mock
    client = Mock()
    monkeypatch.setattr(app, "_client", lambda: client)
    for name in ("_prepare_backup_directory", "_volume", "_prepare_tenant_volume", "_network", "_attach_traefik"):
        monkeypatch.setattr(app, name, lambda *a, **k: None)
    monkeypatch.setattr(app, "_BUDGET", tenant_budget.TenantBudget())
    assert create("shop-a") == "ok"


def test_capacity_endpoint_and_503_over_http(env, monkeypatch):
    docker = env(slots=2)
    monkeypatch.setattr(app, "PROVISIONER_BEARER_KEY", "k")
    http = TestClient(app.app)
    headers = {"Authorization": "Bearer k"}
    body = {"internal_api_secret": "s", "billing_url": "https://example.test/b", "logout_url": "https://example.test/l",
            "internal_api_url": "https://example.test/i"}
    assert http.get("/v1/capacity", headers=headers).json() | {} == http.get("/v1/capacity", headers=headers).json()
    assert http.get("/v1/capacity", headers=headers).json()["would_admit"] is True
    assert http.post("/v1/tenants/a", json=body, headers=headers).status_code == 200
    assert http.post("/v1/tenants/b", json=body, headers=headers).status_code == 200
    full = http.post("/v1/tenants/c", json=body, headers=headers)
    assert full.status_code == 503 and full.headers["retry-after"] == "30"
    assert full.json()["detail"]["code"] == "capacity_exhausted"
    status = http.get("/v1/capacity", headers=headers).json()
    assert status["would_admit"] is False and status["running_tenants"] == 2 and status["free_mib"] == 0
    assert http.get("/v1/capacity").status_code == 403  # control plane stays authenticated
    assert docker.started == 2


# -- 20-30 cabinet scenarios on isolated, synthetic tenants -------------------------------------------------------

@pytest.mark.parametrize("tenants,slots", [(20, 20), (24, 30), (30, 30), (36, 30)])
def test_signup_waves_fill_exactly_the_budget(env, tenants, slots):
    docker = env(slots=slots, run_delay=0.002)
    results = []
    threads = [threading.Thread(target=lambda i=i: results.append(create(f"w{i}"))) for i in range(tenants)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count("ok") == min(tenants, slots)
    assert sum(isinstance(r, HTTPException) and r.status_code == 503 for r in results) == max(0, tenants - slots)
    assert docker.calls == []  # nobody was stopped to make room


def test_churn_stops_free_room_and_retries_land_without_overspend(env, monkeypatch):
    docker = env(slots=25, run_delay=0.001)
    monkeypatch.setattr(app, "_remove_network", lambda *a: None)
    first = [create(f"c{i}") for i in range(30)]
    assert first.count("ok") == 25
    queued = [f"c{i}" for i, r in enumerate(first) if r != "ok"]
    for slug in ("c0", "c1", "c2"):
        app.stop_tenant(slug)
    retries = [create(slug) for slug in queued]
    assert retries.count("ok") == 3  # exactly the three freed slots, the other two keep waiting
    running = [c for c in docker.items.values() if c.attrs["State"]["Status"] == "running"]
    assert len(running) == 25
    assert app._BUDGET.pending() == {}
