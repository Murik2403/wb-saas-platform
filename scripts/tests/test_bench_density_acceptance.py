import hashlib
import ast
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
import urllib.request
from io import BytesIO
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

SCRIPT = Path(__file__).resolve().parents[1] / "bench_tenant_density.py"
spec = importlib.util.spec_from_file_location("bench_density_acceptance", SCRIPT)
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


def arguments(**changes):
    values = dict(prefix="bench", image="test-image", limit_mem="1536m", reservation="384m", min_vm_available_mib=2048,
                  rounds=1, parallel=1, drivers=1, fixture_dir=None, endpoints_from=None, heavy_variants=0,
                  synthetic_days=365, enforce_admission=False, max_calc_ms=20000, max_health_ms=5000, health_p95_ms=1000)
    values.update(changes)
    return SimpleNamespace(**values)


def complete_metrics():
    return {"cur": 1000000, "peak": 1000000, "ev_high": 0, "ev_max": 0, "ev_oom": 0, "ev_oom_kill": 0,
            "cpu_usage_usec": 0, "cpu_nr_periods": 0, "cpu_nr_throttled": 0, "cpu_throttled_usec": 0}


class RestoredFixtureSafetyTests(unittest.TestCase):
    def fixture(self, root):
        root.mkdir()
        database = root / "wb_dashboard.sqlite3"
        with closing(sqlite3.connect(database)) as c, c:
            c.execute("CREATE TABLE sales(nm_id INTEGER, sale_date TEXT, value INTEGER)")
            c.execute("INSERT INTO sales VALUES(100001,'2026-09-01',123)")
            c.execute("CREATE TABLE shop_workspaces(credential_cipher TEXT, active INTEGER)")
            c.execute("INSERT INTO shop_workspaces VALUES('synthetic-secret',1)")
            c.execute("CREATE TABLE report_definitions(id INTEGER, schedule_type TEXT, email_enabled INTEGER, telegram_enabled INTEGER)")
            c.execute("INSERT INTO report_definitions VALUES(1,'daily',1,1)")
            c.execute("CREATE TABLE report_runs(id INTEGER)")
            c.execute("INSERT INTO report_runs VALUES(1)")
        (root / ".wb_token").write_text("synthetic-provider-secret")
        (root / "settings.json").write_text('{"smtp_password":"synthetic"}')
        return database

    def test_restore_copies_data_and_scrubs_credentials_without_touching_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, destination = Path(tmp) / "input", Path(tmp) / "copy"
            database = self.fixture(source)
            before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.iterdir()}
            result = bench.copy_frozen_fixture(source, destination)
            self.assertEqual(result["databases"], 1)
            self.assertFalse((destination / ".wb_token").exists())
            with closing(sqlite3.connect(destination / database.name)) as c:
                self.assertEqual(c.execute("SELECT value FROM sales").fetchone(), (123,))
                self.assertEqual(c.execute("SELECT credential_cipher,active FROM shop_workspaces").fetchone(), ("", 0))
                self.assertEqual(c.execute("SELECT email_enabled,telegram_enabled FROM report_definitions").fetchone(), (0, 0))
                self.assertEqual(c.execute("SELECT COUNT(*) FROM report_runs").fetchone()[0], 0)
            self.assertEqual(before, {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.iterdir()})

    def test_live_wal_and_nonempty_destination_fail_before_overwriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, destination = Path(tmp) / "input", Path(tmp) / "copy"
            self.fixture(source)
            (source / "wb_dashboard.sqlite3-wal").write_text("live")
            with self.assertRaisesRegex(ValueError, "frozen"):
                bench.copy_frozen_fixture(source, destination)
            (source / "wb_dashboard.sqlite3-wal").unlink()
            destination.mkdir()
            sentinel = destination / "owned.txt"
            sentinel.write_text("keep")
            with self.assertRaises(ValueError):
                bench.copy_frozen_fixture(source, destination)
            self.assertEqual(sentinel.read_text(), "keep")

    def test_links_are_rejected_before_destination_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, destination = Path(tmp) / "input", Path(tmp) / "copy"
            self.fixture(source)
            with patch.object(Path, "is_symlink", return_value=True), self.assertRaises(ValueError):
                bench.copy_frozen_fixture(source, destination)
            self.assertFalse(destination.exists())

    def test_nested_source_or_destination_are_rejected_without_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "input"
            self.fixture(source)
            before = set(source.iterdir())
            with self.assertRaises(ValueError):
                bench.copy_frozen_fixture(source, source / "nested-copy")
            with self.assertRaises(ValueError):
                bench.copy_frozen_fixture(source, source.parent)
            self.assertEqual(set(source.iterdir()), before)

    def test_junction_in_parent_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, destination = Path(tmp) / "input", Path(tmp) / "copy"
            self.fixture(source)
            with patch.object(Path, "is_junction", create=True, side_effect=[False, True]), self.assertRaises(ValueError):
                bench.copy_frozen_fixture(source, destination)
            self.assertFalse(destination.exists())

    def test_restore_helper_keeps_owned_volume_writable_until_copy_finishes(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "input"
            self.fixture(source)
            instance = bench.Bench(arguments(fixture_dir=source))
            calls = []
            def docker(*args, **kwargs):
                calls.append(args)
                if args[0] == "run" and "--entrypoint" in args and args[args.index("--entrypoint") + 1] == "python3":
                    return json.dumps({"favorites": 1, "sample_nm_id": 100001})
                return ""
            with patch.object(instance, "owns", return_value=True), patch.object(bench, "docker", side_effect=docker):
                instance.create_tenant(1)
            self.assertEqual(instance.report["fixture_summary"], {"favorites": 1})
            self.assertNotIn("sample_nm_id", json.dumps(instance.report))
            helper = next(c for c in calls if c[0] == "run" and "python3" in c)
            self.assertFalse(any("chown" in c for c in calls))
            self.assertIn(f"{source.resolve()}:/fixture:ro", helper)
            self.assertTrue(any("target=/app/data,volume-nocopy" in arg for arg in helper))
            for option in ("--memory", "--cpus", "--pids-limit", "--oom-score-adj"):
                self.assertIn(option, helper)
            self.assertEqual(helper[helper.index("--network") + 1], "none")
            code = helper[helper.index("-c") + 1]
            postcopy = code[code.index("for p in sorted(Path('/app/data')"):code.index("print(json.dumps(result))")]
            # Simulate CAP_CHOWN without CAP_FOWNER: chmod after chown is denied.
            copied = Path(tmp) / "simulated"
            copied.mkdir()
            (copied / "nested").mkdir()
            (copied / "nested" / "db").write_text("safe copy")
            ownership, events = {}, []
            class LimitedRoot:
                @staticmethod
                def chmod(path, mode):
                    path = Path(path)
                    if ownership.get(path) == 10001:
                        raise PermissionError("not owner; CAP_FOWNER absent")
                    events.append(("chmod", path))
                @staticmethod
                def chown(path, uid, gid):
                    path = Path(path)
                    ownership[path] = uid
                    events.append(("chown", path))
            exec(postcopy.replace("/app/data", str(copied).replace("\\", "/")), {"Path": Path, "os": LimitedRoot})
            self.assertEqual(events[-1], ("chown", copied))
            self.assertEqual(len(ownership), 3)


class ResidentReportObservationTests(unittest.TestCase):
    def test_only_exact_completed_job_with_valid_owned_pdf_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database, pdf = root / "report.sqlite", root / "report.pdf"
            pdf.write_bytes(b"%PDF-1.4\nsynthetic test document\n%%EOF\n")
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute("CREATE TABLE report_runs(id INTEGER,definition_id INTEGER,status TEXT,file_path TEXT,delivery_requested INTEGER,delivery_status TEXT,started_at TEXT,finished_at TEXT)")
                connection.execute("INSERT INTO report_runs VALUES(23,7,'ok',?,0,'not_requested','2026-10-13T10:00:00','2026-10-13T10:00:05')", (str(pdf),))
            status = lambda: bench.resident_report_state(database, 23, 7, root, worker_alive=True)
            self.assertTrue(status()["pdf_complete"])
            self.assertEqual(status()['work_window']['end']-status()['work_window']['start'],5)
            self.assertEqual(bench.resident_report_state(database, 24, 7, root, worker_alive=True)["status"], "missing")
            self.assertEqual(bench.resident_report_state(database, 23, 8, root, worker_alive=True)["status"], "missing")
            pdf.write_bytes(b"incomplete PDF")
            self.assertFalse(status()["pdf_complete"])
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute("UPDATE report_runs SET status='running'")
            self.assertFalse(status()["pdf_complete"])

    def test_completed_file_outside_owned_data_and_delivery_requested_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            owned = root / "owned"
            owned.mkdir()
            pdf = root / "foreign.pdf"
            pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
            database = owned / "report.sqlite"
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute("CREATE TABLE report_runs(id INTEGER,definition_id INTEGER,status TEXT,file_path TEXT,delivery_requested INTEGER,delivery_status TEXT,started_at TEXT,finished_at TEXT)")
                connection.execute("INSERT INTO report_runs VALUES(23,7,'ok',?,0,'not_requested','2026-10-13T10:00:00','2026-10-13T10:00:05')", (str(pdf),))
            self.assertFalse(bench.resident_report_state(database, 23, 7, owned, worker_alive=True)["pdf_complete"])
            owned_pdf = owned / "pdf"
            owned_pdf.write_bytes(pdf.read_bytes())
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute("UPDATE report_runs SET file_path=?,delivery_requested=1", (str(owned_pdf),))
            self.assertFalse(bench.resident_report_state(database, 23, 7, owned, worker_alive=True)["pdf_complete"])

    def test_process_detection_requires_actual_background_worker_argument(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = Path(tmp)
            process = proc / "123"
            process.mkdir()
            (process / "cmdline").write_bytes(b"python3\0api_server.py\0")
            self.assertFalse(bench.resident_worker_alive(proc))
            (process / "cmdline").write_bytes(b"python3\0/app/tenant-app/background_worker.py\0")
            self.assertTrue(bench.resident_worker_alive(proc))


class PhaseAcceptanceTests(unittest.TestCase):
    def instance(self):
        instance = bench.Bench(arguments())
        instance.names = ["bench-t01-owned"]
        return instance

    def phase(self, instance, driver, available=5000):
        metrics = {instance.names[0]: complete_metrics()}
        with patch.object(bench, "vm_available_mib", return_value=available), patch.object(bench, "vm_loadavg", return_value=0), \
             patch.object(instance, "drive", side_effect=driver), patch.object(instance, "probe_all", return_value=metrics), \
             patch.object(instance, "flags", return_value=(0, 0)), patch.object(instance, "stop_owned_phase", create=True):
            return instance.run_phase("calc_serial", True, False, 1, ["/api/v1/dashboard/finance"], metrics)

    def valid(self, instance):
        return {instance.names[0]: {"bucket": {"ok": 1, "refused": 0, "fail": 0, "health_fail": 0, "codes": {}}, "calc": [10], "health": [10]}}

    def test_empty_driver_results_cannot_pass(self):
        phase = self.phase(self.instance(), lambda *args: {})
        self.assertTrue(phase.get("failed"))

    def test_driver_thread_exception_is_reported_as_failure(self):
        phase = self.phase(self.instance(), RuntimeError("synthetic driver failure"))
        self.assertTrue(phase.get("failed"))
        self.assertIn("worker_exception", phase["failure_reasons"])

    def test_missing_request_or_unexpected_4xx_cannot_pass(self):
        instance = self.instance()
        data = self.valid(instance)
        data[instance.names[0]]["bucket"].update(ok=0, refused=1)
        phase = self.phase(instance, lambda *args: data)
        self.assertTrue(phase.get("failed"))

    def test_complete_successful_requests_do_pass(self):
        instance = self.instance()
        phase = self.phase(instance, lambda *args: self.valid(instance))
        self.assertFalse(phase.get("failed"))
        self.assertEqual(phase["calc"]["requests"], 1)

    def test_watchdog_stops_only_owned_phase_on_floor_breach(self):
        instance = self.instance()
        metrics = {instance.names[0]: complete_metrics()}
        def driver(*args):
            time.sleep(.05)
            return self.valid(instance)
        with patch.object(bench, "vm_available_mib", side_effect=[5000, 1000, 1000]), patch.object(bench, "vm_loadavg", return_value=0), \
             patch.object(instance, "drive", side_effect=driver), patch.object(instance, "probe_all", return_value=metrics), \
             patch.object(instance, "flags", return_value=(0, 0)), patch.object(instance, "stop_owned_phase", create=True) as stop:
            phase = instance.run_phase("calc_serial", True, False, 1, ["/api/v1/dashboard/finance"], metrics)
        stop.assert_called_once()
        self.assertTrue(phase.get("failed"))
        self.assertIn("vm_floor", phase["failure_reasons"])

    def test_local_endpoint_set_covers_new_features_with_valid_query_parameters(self):
        paths = self.instance().endpoints()
        by_path = {urlsplit(path).path: parse_qs(urlsplit(path).query) for path in paths}
        for route in ("ads/analytics", "products/heatmap", "products/trend", "products/previous", "products/price-advice", "rnp", "management-pnl/monthly"):
            self.assertIn("/api/v1/dashboard/" + route, by_path)
        params = by_path["/api/v1/dashboard/management-economics"]
        for required in ("nm_id", "start", "end"):
            self.assertIn(required, params)
        self.assertNotIn("/api/v1/dashboard/customer-quality", by_path)
        self.assertNotIn("/api/v1/dashboard/analytics", by_path)

    def test_admission_refusal_happens_before_any_volume_or_container_creation(self):
        instance = bench.Bench(arguments(enforce_admission=True))
        with patch.object(instance, "claim_start", side_effect=RuntimeError("capacity refused"), create=True), patch.object(bench, "docker") as docker:
            with self.assertRaisesRegex(RuntimeError, "capacity refused"):
                instance.create_tenant(1)
        docker.assert_not_called()

    def test_abort_prevents_waiting_driver_from_creating_or_starting(self):
        instance = self.instance()
        started, errors = threading.Event(), []
        def waiting_worker():
            started.set()
            try:
                instance.drive(instance.names, ["/api/v1/dashboard/finance"], 1, 0)
            except RuntimeError as error:
                errors.append(str(error))
        with patch.object(bench, "docker", return_value="") as docker, \
             patch.object(instance, "owns", return_value=False):
            with instance.phase_spawn_lock:
                thread = threading.Thread(target=waiting_worker)
                thread.start()
                self.assertTrue(started.wait(1))
                instance.stop_owned_phase()
            thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertTrue(errors)
        self.assertFalse(any(call.args[0] in ("create", "start", "run") for call in docker.call_args_list))

    def test_missing_cgroup_is_unknown_and_failed_not_zero(self):
        instance = self.instance()
        with patch.object(bench, "vm_available_mib", return_value=5000), patch.object(bench, "vm_loadavg", return_value=0), \
             patch.object(instance, "drive", return_value=self.valid(instance)), patch.object(instance, "probe_all", return_value={}), \
             patch.object(instance, "flags", return_value=(0, 0)):
            phase = instance.run_phase("calc_serial", True, False, 1, ["/api/v1/dashboard/finance"], {})
        self.assertTrue(phase["failed"])
        self.assertIsNone(phase["memory"])
        self.assertIsNone(phase["oom_killed_containers"])
        self.assertIn("measurement_unavailable", phase["failure_reasons"])

    def test_partial_or_unexecuted_background_does_not_pass(self):
        for output in (0, 1):
            instance = self.instance()
            instance.names.append("bench-t02-owned")
            metrics = {name: complete_metrics() for name in instance.names}
            with patch.object(bench, "vm_available_mib", return_value=5000), patch.object(bench, "vm_loadavg", return_value=0), \
                 patch.object(instance, "probe_all", return_value=metrics), patch.object(instance, "flags", return_value=(0, 0)), \
                 patch.object(bench, "docker", return_value=json.dumps({"reports": {"ok": True, "out": output, "s": 1}})):
                phase = instance.run_phase("background", False, True, 0, [], metrics, instance.names[:1])
            self.assertTrue(phase["failed"])
            self.assertIn("incomplete_background", phase["failure_reasons"])

    def test_failed_step_refuses_later_steps_and_preserves_report(self):
        instance = bench.Bench(arguments(steps=[20, 25, 30], admission_only=False, probe_max=False))
        client = SimpleNamespace(info=lambda: {"NCPU": 8, "MemTotal": 20 * 1024 * bench.MIB}, close=lambda: None)
        with patch.object(bench, "assert_local_engine"), patch.object(bench, "docker"), \
             patch.object(instance, "admission_state", return_value=(client, None)), patch.object(instance, "can_start", return_value=(True, {})), \
             patch.object(instance, "one_step", return_value={"target": 20, "failed": True, "result": "FAILED"}) as step, \
             patch.object(instance, "cleanup"), patch.object(instance, "verify_cleanup"), patch.object(instance, "pin_runtime_image"):
            report = instance.run()
        step.assert_called_once()
        self.assertFalse(report["passed"])
        self.assertEqual([s["result"] for s in report["steps"]], ["FAILED", "REFUSED_NOT_RUN", "REFUSED_NOT_RUN"])
        self.assertFalse(report["production_capacity_proven"])

    def test_failed_cli_writes_report_and_returns_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "failed.json"
            with patch.object(bench.sys, "argv", ["bench", "--image", "test-image", "--out", str(output)]), \
                 patch.object(bench.Bench, "run", return_value={"passed": False, "steps": [{"result": "FAILED"}]}):
                result = bench.main()
            self.assertEqual(result, 1)
            self.assertFalse(json.loads(output.read_text())["passed"])

    def test_missing_event_and_cpu_counters_are_not_confirmed_zero(self):
        instance = self.instance()
        for field in ("peak", "ev_high", "ev_max", "ev_oom", "ev_oom_kill", "cpu_usage_usec", "cpu_nr_periods", "cpu_nr_throttled", "cpu_throttled_usec"):
            metrics = complete_metrics()
            metrics.pop(field)
            with self.subTest(field=field), self.assertRaisesRegex(RuntimeError, "unknown"):
                instance.memory_summary({instance.names[0]: metrics}, None)

    def test_live_image_tag_is_pinned_to_immutable_identity_before_use(self):
        instance = self.instance()
        identifier = "sha256:" + "a" * 64
        client = SimpleNamespace(images=SimpleNamespace(get=lambda _: SimpleNamespace(attrs={"Id": identifier, "RepoDigests": [], "Config": {"Labels": {"org.opencontainers.image.revision": "source-revision"}}})))
        instance.pin_runtime_image(client)
        self.assertEqual(instance.args.image, identifier)
        self.assertEqual(instance.report["runtime_image"]["revision"], "source-revision")
        client.images.get = lambda _: SimpleNamespace(attrs={"Id": "mutable-tag"})
        with self.assertRaisesRegex(RuntimeError, "identity"):
            instance.pin_runtime_image(client)

    def test_cleanup_detects_failed_removal_without_relying_on_command_stdout(self):
        instance = self.instance()
        def failed_remove(*args, **kwargs):
            if args[:2] == ("ps", "-aq"):
                return "owned-id"
            if args[:2] == ("container", "inspect"):
                return f"/{instance.names[0]} {instance.run_id}"
            return ""
        with patch.object(bench, "docker", side_effect=failed_remove), patch.object(instance, "owns", return_value=True):
            instance.cleanup()  # rm failed, ignored stdout; independent inventory must catch this.
            with self.assertRaisesRegex(RuntimeError, "remain"):
                instance.verify_cleanup()
        self.assertEqual(instance.report["cleanup_residual_counts"]["container"], 1)

    def test_successful_cleanup_uses_mutable_inventory_and_checks_no_own_residuals(self):
        instance = self.instance()
        inventory = {"container": {"owned-id"}, "volume": {"owned-volume"}, "network": {instance.network}}
        def engine(*args, **kwargs):
            if args[:2] == ("ps", "-aq"):
                return " ".join(inventory["container"])
            if args[:3] in (("volume", "ls", "-q"), ("network", "ls", "-q")):
                return " ".join(inventory[args[0]])
            if args[0] == "rm":
                self.assertIn("-v", args)
                inventory["container"].clear()
            if args[:2] in (("volume", "rm"), ("network", "rm")):
                inventory[args[0]].clear()
            return ""
        with patch.object(bench, "docker", side_effect=engine), patch.object(instance, "owns", return_value=True):
            instance.cleanup()
            instance.verify_cleanup()
        self.assertEqual(instance.report["cleanup_residual_counts"], {"container": 0, "volume": 0, "network": 0})

    def test_unavailable_cleanup_inventory_fails_closed(self):
        instance = self.instance()
        with patch.object(bench, "docker", side_effect=RuntimeError("engine unavailable")), self.assertRaises(RuntimeError):
            instance.verify_cleanup()

    def test_default_deployed_topology_does_not_override_real_entrypoint(self):
        instance = self.instance()
        calls = []
        with patch.object(instance, "owns", return_value=True), patch.object(bench, "docker", side_effect=lambda *args, **kw: calls.append(args) or ""):
            instance.create_tenant(1)
        tenant = next(c for c in calls if c[:2] == ("run", "-d"))
        self.assertNotIn("--entrypoint", tenant)
        self.assertEqual(instance.report["runtime_mode"], "deployed")
        self.assertIn("resident", instance.report["topology"])

    def test_api_only_comparison_remains_explicit_and_overrides_entrypoint(self):
        instance = bench.Bench(arguments(runtime_mode="api-only"))
        calls = []
        with patch.object(instance, "owns", return_value=True), patch.object(bench, "docker", side_effect=lambda *args, **kw: calls.append(args) or ""):
            instance.create_tenant(1)
        tenant = next(c for c in calls if c[:2] == ("run", "-d"))
        self.assertIn(bench.API_ONLY_ENTRYPOINT, tenant)
        self.assertFalse(instance.report["automatic_background"])

    def test_deployed_background_waits_for_the_exact_resident_job(self):
        instance = self.instance()
        outputs = [json.dumps({"run_id": 23, "definition_id": 7, "worker_alive": True}),
                   json.dumps({"run_id": 23, "definition_id": 7, "status": "running", "worker_alive": True}),
                   json.dumps({"run_id": 23, "definition_id": 7, "status": "ok", "worker_alive": True, "pdf_complete": True})]
        with patch.object(bench, "docker", side_effect=outputs) as docker, patch.object(bench.time, "sleep"):
            result = instance.run_background(instance.names[0])
        self.assertTrue(result["reports"]["ok"])
        self.assertEqual(result["reports"]["out"], 1)
        self.assertFalse(any(bench.BACKGROUND in call.args for call in docker.call_args_list))

    def test_resident_failure_wrong_job_missing_worker_or_invalid_pdf_cannot_pass(self):
        for state in ({"status": "error"}, {"status": "ok", "pdf_complete": False},
                      {"status": "ok", "pdf_complete": True, "run_id": 99}, {"status": "running", "worker_alive": False}):
            instance = self.instance()
            response = {"run_id": 23, "definition_id": 7, "status": "running", "worker_alive": True, **state}
            with patch.object(bench, "docker", side_effect=[json.dumps({"run_id": 23, "definition_id": 7, "worker_alive": True}), json.dumps(response)]):
                result = instance.run_background(instance.names[0])
            self.assertFalse(result["reports"]["ok"])
            self.assertEqual(result["reports"]["out"], 0)

    def test_resident_job_deadline_is_a_failure_without_pdf_generation_fallback(self):
        instance = bench.Bench(arguments(report_timeout=2))
        with patch.object(bench.time, "monotonic", side_effect=[0, 3, 3]), \
             patch.object(bench, "docker", return_value=json.dumps({"run_id": 23, "definition_id": 7, "worker_alive": True})) as docker:
            result = instance.run_background("bench-t01-owned")
        self.assertFalse(result["reports"]["ok"])
        self.assertEqual(result["reports"]["err"], "report_deadline")
        self.assertFalse(any(bench.BACKGROUND in call.args for call in docker.call_args_list))

    def test_background_phase_generates_one_resident_job_for_every_tenant(self):
        instance = self.instance()
        instance.names.append("bench-t02-owned")
        metrics = {name: complete_metrics() for name in instance.names}
        with patch.object(bench, "vm_available_mib", return_value=5000), patch.object(bench, "vm_loadavg", return_value=0), \
             patch.object(instance, "probe_all", return_value=metrics), patch.object(instance, "flags", return_value=(0, 0)), \
             patch.object(instance, "run_background", return_value={"reports": {"ok": True, "out": 1, "s": 1, "executor": "resident_worker"}}) as worker:
            phase = instance.run_phase("background", False, True, 0, [], metrics)
        self.assertFalse(phase["failed"])
        self.assertEqual(phase["background"]["reports_generated"], 2)
        self.assertEqual({call.args[0] for call in worker.call_args_list}, set(instance.names))

    def test_calibration_uses_watchdog_and_records_retained_resident_memory(self):
        instance = self.instance()
        before = {instance.names[0]: complete_metrics()}
        after = {instance.names[0]: {**complete_metrics(), "peak": 2 * bench.MIB}}
        phase = {"failed": False, "acceptance_phase": False, "memory": {"cur_mib_max": 350}, "_after": after}
        with patch.object(instance, "probe_all", return_value=before), patch.object(instance, "run_phase", return_value=phase) as watched:
            instance.calibrate_background()
        self.assertEqual(watched.call_args.kwargs, {"calibration": True})
        self.assertEqual(watched.call_args.args[-1], instance.names)
        self.assertEqual(instance.footprint, 350)
        self.assertFalse(instance.report["background_calibration"]["acceptance_phase"])
        with patch.object(instance, "probe_all", return_value=before), \
             patch.object(instance, "run_phase", return_value={"failed": True, "_after": {}}), self.assertRaisesRegex(RuntimeError, "safety gate"):
            instance.calibrate_background()

    def test_aborted_phase_never_enqueues_another_resident_report(self):
        instance = self.instance()
        instance.phase_stop.set()
        with patch.object(bench, "docker") as docker:
            result = instance.run_background(instance.names[0])
        docker.assert_not_called()
        self.assertEqual(result["reports"]["err"], "phase_aborted")

    def test_http_200_empty_html_or_empty_json_cannot_count_as_success(self):
        # Run the actual embedded driver's HTTP function with synthetic response objects.
        nodes = [n for n in ast.parse(bench.DRIVER).body if isinstance(n, ast.FunctionDef) and n.name in ('one', 'validate_api_body')]
        namespace = {'json': json, 'time': time, 'threading': threading, 'urllib': urllib}
        namespace['lock'] = threading.Lock()
        class Response(BytesIO):
            status = 200
        exec(compile(ast.Module(body=nodes,type_ignores=[]), '<embedded density driver>', 'exec'), namespace)
        for body in (b'', b'<html>SPA fallback</html>', b'{}', b'[]', b'"scalar"'):
            namespace['opener'] = SimpleNamespace(open=lambda *args, **kw: Response(body))
            bucket = {'ok': 0, 'fail': 0, 'refused': 0, 'codes': {}}
            namespace['one']('bench-only', '/api/v1/dashboard/finance', bucket)
            self.assertEqual(bucket['ok'], 0)
            self.assertEqual(bucket['fail'], 1)
        namespace['opener'] = SimpleNamespace(open=lambda *args, **kw: Response(b'{"data":[1]}'))
        bucket = {'ok': 0, 'fail': 0, 'refused': 0, 'codes': {}}
        namespace['one']('bench-only', '/api/v1/dashboard/finance', bucket)
        self.assertEqual(bucket['ok'], 1)

    def test_api_body_is_bounded_and_requires_200(self):
        for body, status in ((b'{"ok":true}', 302), (b'x' * (16 * 1024 * 1024 + 1), 200)):
            with self.assertRaises(ValueError):
                bench.validate_api_body(body, status)

    def test_persisted_whole_seconds_cannot_invent_report_overlap(self):
        self.assertFalse(bench.combined_overlap({'start':100.2,'end':100.8}, {'start':100,'end':101,'resolution_s':1})['proven'])
        self.assertFalse(bench.combined_overlap({'start':100,'end':103}, {'start':101,'end':101,'resolution_s':1})['proven'])
        self.assertTrue(bench.combined_overlap({'start':100,'end':105}, {'start':101,'end':104,'resolution_s':1})['proven'])
        self.assertFalse(bench.combined_overlap({'start':float('nan'),'end':105}, {'start':101,'end':104,'resolution_s':1})['proven'])
        self.assertFalse(bench.combined_overlap({'start':100,'end':105}, None)['proven'])

    def test_combined_requires_actual_report_overlap_for_every_tenant(self):
        for delayed, disjoint in ((False, False), (True, False), (False, True)):
            instance = self.instance()
            instance.names.append('bench-t02-owned')
            metrics = {name:complete_metrics() for name in instance.names}
            driver = {name:{'bucket':{'ok':1,'refused':0,'fail':0,'health_fail':0,'codes':{}},'calc':[10],'health':[10],'calc_window':{'start':100,'end':105}} for name in instance.names}
            if disjoint:
                driver[instance.names[1]]['calc_window'] = {'start':110,'end':115}
            def report(name):
                late = delayed and name == instance.names[1]
                if disjoint and name == instance.names[1]:
                    return {'reports':{'ok':True,'out':1,'s':1,'executor':'resident_worker','work_window':{'start':111,'end':114,'resolution_s':1}}}
                return {'reports':{'ok':True,'out':1,'s':1,'executor':'resident_worker','work_window':{'start':106 if late else 101,'end':110 if late else 104,'resolution_s':1}}}
            with patch.object(bench,'vm_available_mib',return_value=5000), patch.object(bench,'vm_loadavg',return_value=0), \
                 patch.object(instance,'probe_all',return_value=metrics), patch.object(instance,'flags',return_value=(0,0)), \
                 patch.object(instance,'drive',return_value=driver), patch.object(instance,'run_background',side_effect=report):
                phase=instance.run_phase('combined',True,True,1,['/api/v1/dashboard/finance'],metrics)
            self.assertEqual(phase['failed'],delayed or disjoint)
            self.assertEqual(phase['combined_overlap']['proven_tenants'],1 if delayed else 2)
            if delayed:
                self.assertIn('combined_overlap_unproven',phase['failure_reasons'])
            if disjoint:
                self.assertIn('combined_all_tenants_overlap_unproven',phase['failure_reasons'])

    def test_individual_overlaps_do_not_prove_simultaneous_cohort_peak(self):
        first = bench.combined_overlap({'start':100,'end':105}, {'start':101,'end':104,'resolution_s':1})
        later = bench.combined_overlap({'start':110,'end':115}, {'start':111,'end':114,'resolution_s':1})
        self.assertTrue(first['proven'] and later['proven'])
        self.assertFalse(bench.combined_cohort_overlap({'first':first,'later':later})['proven'])
        self.assertTrue(bench.combined_cohort_overlap({'first':first,'same':first})['proven'])
        self.assertEqual(bench.combined_cohort_overlap({'first':first})['guaranteed_overlap_s'],2)
        self.assertFalse(bench.combined_cohort_overlap({})['proven'])


if __name__ == "__main__":
    unittest.main()
