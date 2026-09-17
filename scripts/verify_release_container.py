"""Destructive fixtures are allowed ONLY inside the explicitly isolated QA tenant."""
import json
import os
import time
import urllib.request
from pathlib import Path

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
