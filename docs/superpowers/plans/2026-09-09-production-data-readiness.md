# Production Data Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make missing material-stock data visible before a production plan is treated as actionable.

**Architecture:** Extend the existing `/api/v1/data-status` response with a read-only production readiness flag. The Vite dashboard maps that flag to an explicit warning on the Production page; it does not fabricate stock balances, FIFO layers, or production capacity.

**Tech Stack:** Python, FastAPI, SQLite, React, TypeScript, Vite, pytest.

**Spec:** `docs/PROJECT_AUDIT_2026-09-09.md`

## Global Constraints

- Do not create inventory, FIFO layers, or production events automatically.
- A product cost greater than zero remains independent from material-stock readiness.
- Keep the API response backward compatible by adding an optional field only.
- Do not expose credentials or raw SQLite errors in the response.

---

### Task 1: Expose material-stock readiness

**Files:**
- Modify: `mh-redesign-lab/tenant-api/api_server.py:504-530`
- Test: `mh-redesign-lab/tenant-api/tests/test_audit_regressions.py`

**Interfaces:**
- Consumes: `material_inventory` and `production_settings` tables.
- Produces: `GET /api/v1/data-status` field `missing_material_inventory: bool`.

- [ ] **Step 1: Write the failing test**

```python
def test_data_status_reports_missing_material_inventory(monkeypatch, tmp_path):
    from test_display_contract import setup_sync
    setup_sync(monkeypatch, tmp_path, [(1, "success", "2026-09-09T20:00:00")], True)
    with sqlite3.connect(api.DB_PATH) as conn:
        conn.execute("CREATE TABLE production_settings(nm_id INTEGER, enabled INTEGER)")
        conn.execute("CREATE TABLE material_inventory(material_key TEXT)")
        conn.execute("INSERT INTO production_settings VALUES(1, 1)")
    assert api.get_data_status()["missing_material_inventory"] is True
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `../../tenant-app/.venv/Scripts/python.exe -m pytest tests/test_audit_regressions.py::test_data_status_reports_missing_material_inventory -q`

Expected: failure because the response does not contain `missing_material_inventory`.

- [ ] **Step 3: Add the minimal API check**

```python
has_enabled_production = conn.execute(
    "SELECT 1 FROM production_settings WHERE enabled=1 LIMIT 1"
).fetchone() is not None
has_material_inventory = conn.execute(
    "SELECT 1 FROM material_inventory LIMIT 1"
).fetchone() is not None
missing_material_inventory = has_enabled_production and not has_material_inventory
```

Return the Boolean in `get_data_status`.

- [ ] **Step 4: Run the focused API test**

Run: `../../tenant-app/.venv/Scripts/python.exe -m pytest tests/test_audit_regressions.py -q -p no:cacheprovider`

Expected: all tests pass.

### Task 2: Show the production warning

**Files:**
- Modify: `mh-redesign-lab/src/data-source.ts:844-860`
- Modify: `mh-redesign-lab/src/App.tsx:492`

**Interfaces:**
- Consumes: `DataStatus.missing_material_inventory`.
- Produces: `DashboardViewModel.dataWarning` on the Production page.

- [ ] **Step 1: Extend the response type**

```ts
type DataStatus = {
  missing_material_inventory: boolean;
  // existing fields remain unchanged
};
```

- [ ] **Step 2: Map a specific warning**

```ts
dataWarning: status?.missing_material_inventory
  ? "Остатки сырья не заведены. Производственный план не может подтвердить наличие материала."
  : status?.missing_costs
    ? "Себестоимость не заполнена. Прибыль и маржа показаны без подтверждённых затрат на товары."
    : undefined,
```

- [ ] **Step 3: Render the existing banner on Production**

```tsx
{dashboard.dataWarning && (activeNav === "finance" || activeNav === "products" || activeNav === "production") ? (
  <p className="data-banner stale" role="status">{dashboard.dataWarning}</p>
) : null}
```

- [ ] **Step 4: Build the Vite application**

Run: `node node_modules/typescript/bin/tsc -b; node node_modules/vite/bin/vite.js build`

Expected: exit code 0.

### Task 3: Verify the live service

**Files:**
- Modify: none

**Interfaces:**
- Consumes: local API and Vite application.
- Produces: verified warning in the Production view.

- [ ] **Step 1: Restart the local service after the API change**

Run: `./stop-local.ps1; ./start-local.ps1`

- [ ] **Step 2: Verify the live API value**

Run: `Invoke-RestMethod http://127.0.0.1:8001/api/v1/data-status`

Expected: `missing_material_inventory` is `true` while production is enabled and `material_inventory` is empty.

- [ ] **Step 3: Verify the Production page**

Open: `http://127.0.0.1:5177/?period=30d&view=production`

Expected: the warning explains that material balances must be entered before relying on the plan.
