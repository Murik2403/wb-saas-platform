"""Destructive fixtures are allowed ONLY inside the explicitly isolated QA tenant."""
import json
import os
import time
import urllib.request
from pathlib import Path

from db.core import connect

assert os.environ.get("WB_SAAS_TENANT_SLUG") == "release-fixture", "Run only in the isolated release fixture"

BASE = "http://127.0.0.1:8501"


def call(path, body=None, method=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None, headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.load(response)


item = call("/api/v1/action-plan/items", {"title": "Release fixture", "sku": "fixture"})
item_id = item["id"]
call(f"/api/v1/action-plan/items/{item_id}", {"status": "in_progress"}, "PATCH")
done = call(f"/api/v1/action-plan/items/{item_id}", {"status": "done", "result": "Verified fixture operation"}, "PATCH")
assert done["started_at"] and done["completed_at"]
reloaded = call("/api/v1/action-plan/items")
assert any(row["id"] == item_id and row["result"] == "Verified fixture operation" for row in reloaded["items"])
print("ACTION_PLAN_RELOAD_OK", flush=True)

with connect() as conn:
    conn.execute("INSERT INTO execution_tasks(task_key,task_type,task_date,nm_id,chrt_id,planned_units,actual_units,status,updated_at) VALUES ('release-fixture-task','production','2026-09-30',999001,0,3,0,'Не начато','2026-09-25')")
    conn.execute("INSERT INTO manufacturing_boms(nm_id,chrt_id,version,materials_json,operations_json,packaging_per_unit,other_per_unit,note,created_at) VALUES (999001,0,1,?, '[]',0,0,'','2026-09-25')", ('[{"material_key":"release-fixture-fabric","material_name":"Release fixture fabric","quantity_per_unit":2,"unit":"м"}]',))
    conn.execute("INSERT INTO material_inventory_color(material_key,material_name,balance_known,full_rolls,partial_meters,roll_length,unit,tracking_mode,updated_at) VALUES ('release-fixture-fabric','Release fixture fabric',1,0,0,1,'м','quantity','2026-09-25')")
    conn.execute("INSERT INTO suppliers(name,default_currency,lead_time_days,active,created_at,updated_at) VALUES ('Release fixture supplier','RUB',0,1,'2026-09-25','2026-09-25')")
    supplier_id = conn.execute("SELECT id FROM suppliers WHERE name='Release fixture supplier'").fetchone()[0]

with connect() as conn:
    before_preview = tuple(
        conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("procurement_orders", "procurement_items", "inventory_movements", "execution_tasks")
    )
preview = call("/api/v1/supply-planning/preview", {
    "start_date": "2026-09-01",
    "end_date": "2026-09-30",
    "variant_allocations": [],
})
assert len(preview.get("preview_fingerprint", "")) == 64
int(preview["preview_fingerprint"], 16)
with connect() as conn:
    after_preview = tuple(
        conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("procurement_orders", "procurement_items", "inventory_movements", "execution_tasks")
    )
assert before_preview == after_preview, (before_preview, after_preview)
print("SUPPLY_PREVIEW_READ_ONLY_OK", flush=True)

confirmed = call("/api/v1/supply-planning/confirm", {
    "start_date": "2026-09-01",
    "end_date": "2026-09-30",
    "variant_allocations": [],
    "confirmation_key": "00000000-0000-4000-8000-000000000401",
    "preview_fingerprint": preview["preview_fingerprint"],
    "edits": [{"material_key": "release-fixture-fabric", "unit": "м", "quantity": 6, "supplier_id": supplier_id, "expected_date": "2026-09-30"}],
})
assert len(confirmed["order_ids"]) == 1 and confirmed["replayed"] is False
order_id = int(confirmed["order_ids"][0])
with connect() as conn:
    order = conn.execute("SELECT status,note FROM procurement_orders WHERE id=?", (order_id,)).fetchone()
    item = conn.execute("SELECT id,quantity,unit FROM procurement_items WHERE order_id=?", (order_id,)).fetchone()
    after_confirm = tuple(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("procurement_orders", "procurement_items", "inventory_movements", "material_cost_layers"))
    conn.execute("UPDATE procurement_items SET supplier_unit_price=120,delivery_unit_foreign=2,extra_unit_rub=5 WHERE id=?", (item["id"],))
assert order["status"] == "Запланировано" and "не отправлена" in order["note"]
assert (item["quantity"], item["unit"]) == (6.0, "м")
assert after_confirm == (1, 1, 0, 0), after_confirm
receipt = call(f"/api/v1/operations/supply/{order_id}/receipt", {"items": [{"item_id": item["id"], "receive_now": 6}]})
assert receipt.get("errors") == [] and receipt.get("posted") == 6, receipt
with connect() as conn:
    order_status = conn.execute("SELECT status FROM procurement_orders WHERE id=?", (order_id,)).fetchone()[0]
    movement = conn.execute("SELECT id,movement_type,material_key,material_delta,unit_cost_rub FROM inventory_movements WHERE procurement_order_id=?", (order_id,)).fetchone()
    layer = conn.execute("SELECT source_ref,original_meters,remaining_meters,unit_cost_rub_m,original_amount_rub FROM material_cost_layers WHERE source_ref=?", (f"movement:{movement['id']}",)).fetchone()
assert order_status == "Получено"
assert (movement["movement_type"], movement["material_key"], movement["material_delta"], movement["unit_cost_rub"]) == ("procurement_material_receipt", "release-fixture-fabric", 6.0, 127.0)
assert tuple(layer) == (f"movement:{movement['id']}", 6.0, 6.0, 127.0, 762.0)
print("SUPPLY_CONFIRM_RECEIPT_FIFO_OK", flush=True)

reports = call("/api/v1/operations/reports")
codes = list(reports["metric_labels"])
run = call("/api/v1/operations/reports/quick", {"name": "Release acceptance — all sections", "days": 30, "metrics": codes})
for attempt in range(150):
    row = next(row for row in call("/api/v1/operations/reports")["runs"] if row["id"] == run["run_id"])
    assert row["status"] != "error", row.get("error")
    if row["status"] == "ok":
        assert row["metric_count"] == len(codes) and row["file_available"]
        with urllib.request.urlopen(BASE + f'/api/v1/operations/reports/runs/{run["run_id"]}/download', timeout=30) as response:
            pdf = response.read()
        assert pdf.startswith(b"%PDF") and len(pdf) > 10000
        Path("/tmp/release-all-sections.pdf").write_bytes(pdf)
        print(f"REPORT_QUEUE_PDF_OK sections={len(codes)} bytes={len(pdf)}", flush=True)
        break
    time.sleep(2)
else:
    raise RuntimeError("Report worker did not finish within five minutes")
