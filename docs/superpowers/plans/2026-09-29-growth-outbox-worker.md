# Growth Outbox Worker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Execute this plan inline with a test checkpoint after each task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retry durable tenant growth events periodically while the tenant API is running, even when no user opens the cabinet.

**Architecture:** Add a small asyncio worker owned by the existing FastAPI lifespan. It calls the existing synchronous outbox delivery function through `asyncio.to_thread`, waits between attempts, and exits cleanly during lifespan shutdown. Start it only for non-demo tenants with the existing internal callback configuration; preserve request-triggered delivery as the fast path and rely on the existing SQLite attempt claim/backoff to avoid duplicate dispatch.

**Tech Stack:** Python 3.12, FastAPI lifespan, asyncio, SQLite, pytest.

**Spec:** Production growth event delivery currently has a durable outbox and activity-triggered retries, but no autonomous retry in the production redesign tenant startup path. Do not enable `MH_LOCAL_JOBS`, because it also starts sync, reporting, and backup schedules already launched separately.

## Global Constraints

- Do not start or change unrelated sync, recommendation, report, or backup schedulers.
- Do not send product identifiers, report contents, marketplace credentials, or secrets in logs.
- Demo tenants and tenants without internal callback settings must not start delivery attempts.
- Keep request-triggered delivery intact.
- Keep each delivery attempt bounded by the existing `(2, 3)` second HTTP timeout.
- Preserve user edits already present in this checkout.

---

### Task 1: Add a testable periodic worker

**Files:**
- Create: `mh-redesign-lab/tenant-api/growth_outbox_worker.py`
- Test: `mh-redesign-lab/tenant-api/tests/test_growth_outbox_worker.py`

**Interfaces:**
- Consumes: `deliver_pending_growth_events(db_path)` from `growth_delivery.py` and `asyncio.Event` for shutdown.
- Produces: `async def run_growth_outbox_worker(stop_event, db_path, deliver=..., interval_seconds=60)`; performs a delivery attempt, then waits interruptibly before retrying; catches unexpected delivery exceptions without exposing exception text or event payload.

- [x] **Step 1: Write tests for an immediate attempt and clean stop**

```python
@pytest.mark.asyncio
async def test_worker_attempts_delivery_then_stops_when_requested(monkeypatch, tmp_path):
    stop = asyncio.Event()
    calls = []

    def deliver(path):
        calls.append(path)
        stop.set()
        return {"status": "complete", "attempted": 0, "delivered": 0}

    await run_growth_outbox_worker(stop, tmp_path / "tenant.sqlite3", deliver=deliver, interval_seconds=60)
    assert calls == [tmp_path / "tenant.sqlite3"]
```

- [x] **Step 2: Run the new test and confirm it fails because the worker does not exist**
- [x] **Step 3: Implement the loop with `asyncio.to_thread`, interruptible `stop_event.wait()`, and a sanitized warning on unexpected errors**
- [x] **Step 4: Add a retry test where one failed attempt does not terminate the worker and the next attempt runs**
- [x] **Step 5: Run `python -m pytest mh-redesign-lab/tenant-api/tests/test_growth_outbox_worker.py -q`**

### Task 2: Wire the worker into the tenant API lifecycle

**Files:**
- Modify: `mh-redesign-lab/tenant-api/api_server.py`
- Modify: `mh-redesign-lab/Dockerfile.tenant-staging`
- Test: `mh-redesign-lab/tenant-api/tests/test_growth_outbox_worker.py`

**Interfaces:**
- Consumes: `IS_DEMO`, `INTERNAL_API_URL`, `INTERNAL_API_SECRET`, `TENANT_SLUG`, and `DB_PATH` from the existing `config` module.
- Produces: lifespan startup and shutdown ownership for at most one worker task in the API process.

- [x] **Step 1: Add a predicate test showing demo tenants and missing callback settings do not launch the worker**
- [x] **Step 2: Run the predicate test and confirm it fails before implementation**
- [x] **Step 3: Start the worker only when the tenant is non-demo and all three existing callback values are present; create its `asyncio.Event` before `yield`**
- [x] **Step 4: Set the event and await task completion in `finally`; do not change the `MH_LOCAL_JOBS` branch**
- [x] **Step 5: Add the worker module to the tenant Docker build context**
- [x] **Step 6: Run the worker tests plus `mh-redesign-lab/tenant-api/tests/test_growth_delivery.py` and `test_growth_routes.py`**

### Task 3: Verify release behavior and deployment eligibility

**Files:**
- Modify: `docs/marketing/PRODUCTION_RELEASE_2026-09-29.md` only if recording the new tenant release evidence.

- [x] **Step 1: Run focused tenant API tests and the repository tenant release test command from CI/release documentation**
- [ ] **Step 2: Build the tenant staging image with a source revision label and run it against an isolated empty temporary data volume; verify worker startup/shutdown and `/healthz` plus business API probes**
- [ ] **Step 3: Verify the live `mvvorokov` container configuration and encrypted backup prerequisites without printing secret values**
- [ ] **Step 4: Deploy with `scripts/deploy_verified_tenant.py` only after the build, tests, backup inspection, and pre-deploy health checks pass; preserve the existing volume and rollback container**
- [ ] **Step 5: Verify the deployed image revision, unchanged tenant environment, healthy state, four business probes, and rollback container**
- [ ] **Step 6: Record exact image/revision, backup evidence, verification results, and remaining limits**
