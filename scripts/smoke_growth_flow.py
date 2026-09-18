"""Real loopback tenant -> gateway growth integration, with disposable data.

Run from any directory: python scripts/smoke_growth_flow.py
Requires the gateway and tenant Python dependencies. No Docker, SMTP, WB,
payment provider, production volume, or real account is used. The gateway is
copied into the temporary fixture because its SQLite path is source-relative.
Both unchanged ASGI apps run in separate processes with external sockets
blocked. This tests the HTTP acknowledgment contract, not browser rendering.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler

ROOT = Path(__file__).resolve().parents[1]
SKU = 123456
SLUG = "growth-smoke"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def block_external_network() -> None:
    """Defense in depth: even unexpected application calls cannot leave localhost."""
    def audit(event, args):
        host = args[0] if event == "socket.getaddrinfo" else (
            args[1][0] if event == "socket.connect" and isinstance(args[1], tuple) else None
        )
        if host is None or host == "localhost":
            return
        try:
            allowed = ipaddress.ip_address(host).is_loopback
        except ValueError:
            allowed = False
        if not allowed:
            raise RuntimeError("Growth smoke prohibits non-loopback network access")
    sys.addaudithook(audit)


def seed_tenant() -> None:
    import config
    from db import init_db
    from db.core import ensure_financial_schema

    config.save_token("synthetic-smoke-token-never-send-to-wb")
    if config.DB_PATH.exists():
        return
    init_db()
    ensure_financial_schema()
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    day = stamp[:10]
    with closing(sqlite3.connect(config.DB_PATH)) as conn, conn:
        conn.execute("""INSERT INTO products_catalog(nm_id,supplier_article,product_name,updated_at,raw_json)
            VALUES(?, 'SMOKE-ONLY', 'Synthetic smoke product', ?, '{}')""", (SKU, stamp))
        conn.execute("INSERT INTO costs(nm_id,cost_per_wb_unit,updated_at) VALUES(?,400,?)", (SKU, stamp))
        conn.execute("""INSERT INTO sync_log(started_at,finished_at,status,details)
            VALUES(?,?,'success','products: 1, orders: 2, sales: 2, financial_rows: 1, ads: 0')""", (stamp, stamp))
        for number in (1, 2):
            conn.execute("""INSERT INTO orders(id,order_date,last_change,nm_id,supplier_article,
                total_price,finished_price,price_with_disc,is_cancel,raw_json)
                VALUES(?,?,?,?,'SMOKE-ONLY',1000,1000,1000,0,'{}')""", (str(number), day, stamp, SKU))
            conn.execute("""INSERT INTO sales(id,sale_date,last_change,nm_id,supplier_article,sale_id,
                total_price,finished_price,price_with_disc,for_pay,is_return,raw_json)
                VALUES(?,?,?,?,'SMOKE-ONLY',?,1000,1000,1000,800,0,'{}')""", (str(number), day, stamp, SKU, f"S{number}"))
        conn.execute("""INSERT INTO financial_report(rrd_id,operation_date,sale_date,nm_id,
            supplier_article,quantity,retail_amount,retail_price_withdisc_rub,ppvz_for_pay,
            commission,delivery_rub,operation_name,doc_type_name,raw_json)
            VALUES(1,?,?,?,'SMOKE-ONLY',2,2000,1000,1600,400,100,'Продажа','Продажа','{}')""", (day, day, SKU))


def worker(kind: str, fixture: Path, port: int) -> None:
    block_external_network()
    if kind == "gateway":
        sys.path.insert(0, str(fixture / "gateway"))
        from logic import accounts, db, growth
        with db.connect() as conn:
            db.init_db(conn)
            account = accounts.create_account(conn, "growth-smoke@example.test", "Smoke-only-password-123!")
            accounts.create_tenant_instance(conn, account, SLUG)
            growth.capture_registration_source(conn, account)
            growth.record_trial_started(conn, account)
        (fixture / "account.json").write_text(json.dumps({"id": account}), encoding="utf-8")
        from app import app
    else:
        sys.path[:0] = [str(ROOT / "mh-redesign-lab" / "tenant-api"), str(ROOT / "tenant-app")]
        seed_tenant()
        from api_server import app
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def rows(path: Path, sql: str) -> list[dict]:
    with closing(sqlite3.connect(path, timeout=5)) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(sql)]


def edit_fixture(path: Path, sql: str, parameters=()) -> None:
    with closing(sqlite3.connect(path, timeout=5)) as conn, conn:
        conn.execute(sql, parameters)


def wait_for(check, label: str, seconds: float = 30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("Timed out: " + label)


def run_smoke() -> dict:
    # All WB/app/proxy/telemetry settings inherited from the operator are dropped.
    env = {key: value for key, value in os.environ.items() if key.upper() in {
        "PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "LANG", "LC_ALL",
    }}
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1", NO_PROXY="*",
               WB_SAAS_DB_BACKEND="sqlite", WB_SAAS_PARENT_DOMAIN="localhost",
               WB_SAAS_TENANT_SLUG=SLUG, WB_SAAS_IS_DEMO="0")
    spec = importlib.util.spec_from_file_location("smoke_internal_auth", ROOT / "gateway" / "internal_auth.py")
    auth = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(auth)
    master = secrets.token_urlsafe(32)
    correct_credential = auth.tenant_credential(master, SLUG)
    env["WB_SAAS_DATA_ENCRYPTION_KEY"] = auth.tenant_data_key(master, SLUG)
    env["WB_SAAS_GROWTH_COOKIE_SECRET"] = secrets.token_urlsafe(32)
    opener = build_opener(ProxyHandler({}))
    processes = []
    logs = []

    def stop(process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    with tempfile.TemporaryDirectory(prefix="marketshelper-growth-smoke-") as directory:
        fixture = Path(directory)
        shutil.copytree(ROOT / "gateway", fixture / "gateway",
                        ignore=shutil.ignore_patterns("data", "tests", "__pycache__", ".pytest_cache", ".test-*", ".env*"))
        env.update(WB_SAAS_DATA_DIR=str(fixture / "tenant-data"),
                   MPLCONFIGDIR=str(fixture / "matplotlib"), XDG_CACHE_HOME=str(fixture / "cache"))
        gateway_port, tenant_port = free_port(), free_port()
        while tenant_port == gateway_port:
            tenant_port = free_port()
        gateway_url = f"http://127.0.0.1:{gateway_port}"
        tenant_url = f"http://127.0.0.1:{tenant_port}"
        env["WB_SAAS_INTERNAL_API_URL"] = gateway_url
        gateway_db = fixture / "gateway" / "data" / "gateway.sqlite3"
        tenant_db = fixture / "tenant-data" / "wb_dashboard.sqlite3"

        def request(base, path, body=None, expected=200, identity=True):
            headers = {"Content-Type": "application/json"}
            if identity:
                headers.update({"X-Tenant-Account": str(account), "X-Tenant-Role": "owner"})
            req = Request(base + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
            try:
                response = opener.open(req, timeout=10)
            except HTTPError as error:
                response = error
            with response:
                content = response.read().decode()
                require(response.status == expected, f"{path}: expected {expected}, got {response.status}: {content[:600]}")
                return json.loads(content) if content else None

        def start(kind, credential, *, demo=False):
            child_env = dict(env, WB_SAAS_INTERNAL_API_SECRET=credential, WB_SAAS_IS_DEMO="1" if demo else "0")
            log = (fixture / f"{kind}-{len(processes)}.log").open("w+", encoding="utf-8")
            logs.append(log)
            port = gateway_port if kind == "gateway" else tenant_port
            process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--worker", kind,
                                        "--fixture", str(fixture), "--port", str(port)],
                                       cwd=fixture, env=child_env, stdout=log, stderr=subprocess.STDOUT,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            processes.append(process)
            def ready():
                require(process.poll() is None, kind + " exited before startup")
                try:
                    request(gateway_url if kind == "gateway" else tenant_url,
                            "/healthz", identity=False)
                    return True
                except (URLError, TimeoutError, ConnectionError):
                    return False
            wait_for(ready, kind + " startup")
            return process

        def local_event():
            result = rows(tenant_db, "SELECT * FROM growth_event_outbox")
            return result[0] if result else None

        def gateway_statuses():
            statuses = []
            for line in Path(logs[0].name).read_text(encoding="utf-8").splitlines():
                try:
                    message = json.loads(line).get("message", "")
                except json.JSONDecodeError:
                    continue  # A concurrent log write can end in a partial line.
                if message.startswith("POST /internal/growth/event "):
                    statuses.append(int(message.split()[2]))
            return statuses

        def assert_empty():
            require(not rows(tenant_db, "SELECT * FROM growth_first_value"), "Unexpected local activation")
            require(not rows(tenant_db, "SELECT * FROM growth_event_outbox"), "Unexpected outbox event")
            require(not rows(gateway_db, "SELECT * FROM growth_events WHERE event_name='first_useful_report'"), "Unexpected gateway activation")

        try:
            start("gateway", master)
            account = json.loads((fixture / "account.json").read_text())["id"]
            tenant = start("tenant", "synthetic-incorrect-scoped-credential")
            path = f"/api/v1/growth/activation?nm_id={SKU}"
            first_path = f"/api/v1/growth/first-report?nm_id={SKU}"
            viewed_path = "/api/v1/growth/report-viewed"
            invalid_view = {"nm_id": SKU, "report_revision": "0" * 64}
            initial = request(tenant_url, path)
            require(initial["ready_for_report"] and not initial["first_value_verified"], "Readiness must not imply activation")
            assert_empty()
            for status in ("error", "partial"):
                edit_fixture(tenant_db, "UPDATE sync_log SET status=?", (status,))
                request(tenant_url, first_path, expected=409)
                request(tenant_url, viewed_path, invalid_view, expected=409)
                assert_empty()
            edit_fixture(tenant_db, "UPDATE sync_log SET status='success'")
            edit_fixture(tenant_db, "UPDATE costs SET cost_per_wb_unit=0")
            request(tenant_url, first_path, expected=409)
            request(tenant_url, viewed_path, invalid_view, expected=409)
            assert_empty()
            edit_fixture(tenant_db, "UPDATE costs SET cost_per_wb_unit=400")
            edit_fixture(tenant_db, "UPDATE financial_report SET quantity=0,retail_amount=0")
            request(tenant_url, first_path, expected=409)
            request(tenant_url, viewed_path, invalid_view, expected=409)
            assert_empty()
            edit_fixture(tenant_db, "UPDATE financial_report SET quantity=2,retail_amount=2000")
            stop(tenant)
            tenant = start("tenant", correct_credential, demo=True)
            require(request(tenant_url, path)["next_step"] == "demo", "Demo must not be ready")
            request(tenant_url, first_path, expected=409)
            request(tenant_url, viewed_path, invalid_view, expected=403)
            assert_empty()
            stop(tenant)
            tenant = start("tenant", "synthetic-incorrect-scoped-credential")
            report = request(tenant_url, first_path)
            require(report["revenue"] == 2000 and report["sales"] == 2 and report["unit_cost"] == 400,
                    "Report must use the synthetic persisted sales and cost")
            require(report["margin"] == 700 and report["margin_pct"] == 35,
                    "Accounting result must be 1600 payout - 100 logistics - 800 cost, without double commission")
            assert_empty()
            view = {"nm_id": SKU, "report_revision": report["report_revision"]}
            request(tenant_url, viewed_path, view, expected=401, identity=False)
            request(tenant_url, viewed_path, invalid_view, expected=409)
            assert_empty()
            acknowledged = request(tenant_url, viewed_path, view)
            original = acknowledged["first_value_at"]
            failed = wait_for(lambda: (item if (item := local_event()) and item["attempt_count"] == 1 else None), "failed delivery attempt")
            wait_for(lambda: gateway_statuses() == [403], "gateway received and rejected the first delivery")
            require(failed["delivered_at"] is None and failed["occurred_at"] == original, "Failure must retain pending original event")
            require(not rows(gateway_db, "SELECT * FROM growth_events WHERE event_name='first_useful_report'"), "Bad credential must be rejected")

            # Restart the actual tenant process: SQLite, not process memory, owns the event.
            stop(tenant)
            persisted = local_event()
            require(persisted["occurred_at"] == original, "Event must survive process restart")
            require(rows(tenant_db, "SELECT * FROM growth_first_value")[0]["occurred_at"] == original,
                    "Durable marker must retain original time")
            tenant = start("tenant", correct_credential)
            # Advance only the fixture retry clock; do not sleep through its real 60s cooldown.
            retry_time = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
            edit_fixture(tenant_db, "UPDATE growth_event_outbox SET last_attempt_at=?", (retry_time,))
            request(tenant_url, path)
            delivered = wait_for(lambda: (item if (item := local_event()) and item["delivered_at"] else None), "real gateway delivery")
            require(delivered["attempt_count"] == 2, "Failed and successful delivery must each be counted")
            repeated = request(tenant_url, viewed_path, view)
            require(repeated["first_value_at"] == original, "Repeat acknowledgment must preserve original time")

            # Model a crash after gateway commit but before local acknowledgment.
            # Only the disposable outbox delivery metadata changes, never event identity/time.
            edit_fixture(tenant_db, "UPDATE growth_event_outbox SET delivered_at=NULL,last_attempt_at=?", (retry_time,))
            request(tenant_url, path)
            final = wait_for(lambda: (item if (item := local_event()) and item["delivered_at"] and item["attempt_count"] == 3 else None), "duplicate delivery")
            for name in ("first_report_viewed", "first_useful_report"):
                events = rows(gateway_db, f"SELECT * FROM growth_events WHERE event_name='{name}'")
                require(len(events) == 1 and events[0]["occurred_at"] == original and events[0]["account_id"] == account,
                        f"Gateway must retain exactly one {name} at original time for the account")
            require(len(rows(tenant_db, "SELECT * FROM growth_first_value")) == 1, "Duplicate local marker")
            require(len(rows(tenant_db, "SELECT * FROM growth_event_outbox")) == 1 and final["occurred_at"] == original,
                    "Duplicate outbox or changed time")
            wait_for(lambda: gateway_statuses() == [403, 204, 204], "gateway HTTP receipt evidence")
            return {"status": "PASS", "transport": "two real ASGI processes over loopback HTTP",
                    "report": {key: report[key] for key in ("revenue", "sales", "unit_cost", "margin", "margin_pct")},
                    "rejected_without_activation": ["failed_sync", "partial_sync", "zero_cost", "no_financial_rows", "demo", "missing_identity", "stale_report_revision"],
                    "delivery_attempts": final["attempt_count"], "failed_delivery": "real gateway HTTP 403",
                    "gateway_http_statuses": gateway_statuses(),
                    "process_restart_durability": True, "duplicate_delivery_deduplicated": True,
                    "gateway_first_useful_report_count": 1, "original_occurred_at_preserved": original}
        except Exception:
            for log in logs:
                log.flush()
                log.seek(0)
                print(log.read()[-6000:], file=sys.stderr)
            raise
        finally:
            for process in reversed(processes):
                stop(process)
            for log in logs:
                log.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", choices=("gateway", "tenant"), help=argparse.SUPPRESS)
    parser.add_argument("--fixture", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--report", type=Path, help="Optional JSON result path; contains no credentials")
    args = parser.parse_args()
    if args.worker:
        worker(args.worker, args.fixture, args.port)
        return
    result = run_smoke()
    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.report:
        args.report.write_text(output + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
