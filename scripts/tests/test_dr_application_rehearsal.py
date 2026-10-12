import importlib.util
from contextlib import closing
from pathlib import Path
import sqlite3
import tarfile
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "rehearse_dr_applications.py"
spec = importlib.util.spec_from_file_location("dr_application_rehearsal", SCRIPT)
dr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dr)


class DrRehearsalSafetyTests(unittest.TestCase):
    def member(self, name, kind=tarfile.REGTYPE):
        item = tarfile.TarInfo(name)
        item.type = kind
        return item

    def test_all_archive_members_preflight_links_traversal_and_collisions(self):
        for bad in ("../outside", "/outside", "C:/outside", "folder\\outside"):
            with self.subTest(name=bad), self.assertRaises(ValueError):
                dr.validate_members([self.member(bad)])
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE, tarfile.FIFOTYPE):
            with self.subTest(type=kind), self.assertRaises(ValueError):
                dr.validate_members([self.member("unsafe", kind)])
        with self.assertRaises(ValueError):
            dr.validate_members([self.member("db.sqlite3"), self.member("DB.sqlite3")])
        dr.validate_members([self.member("./data", tarfile.DIRTYPE), self.member("./data/db.sqlite3")])

    def test_cleanup_boundary_rejects_root_foreign_and_nested_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            valid = parent / (".dr-app-rehearsal-" + "a" * 32)
            self.assertEqual(dr.checked_root(valid, parent), valid.resolve())
            for bad in (parent, parent / "foreign", valid / "nested"):
                with self.subTest(path=bad), self.assertRaises(ValueError):
                    dr.checked_root(bad, parent)

    def test_selected_groups_are_nonempty_unique_and_whitelisted(self):
        for groups in (None, [], ["demo", "demo"], ["../live"], ["marketing"]):
            with self.subTest(groups=groups), self.assertRaises(ValueError):
                dr.validate_groups(groups)
        self.assertEqual(dr.validate_groups(["demo"]), ["demo"])

    def test_candidate_has_only_private_mount_no_network_ports_restart_or_providers(self):
        data = Path("/opt/wb-saas/.dr-app-rehearsal-" + "a" * 32 + "/demo")
        for group in dr.GROUPS:
            cfg = dr.candidate_config("sha256:immutable", group, data, "synthetic-key", {})
            host = cfg["HostConfig"]
            self.assertEqual(host["NetworkMode"], "none")
            self.assertEqual(host["PortBindings"], {})
            self.assertEqual(host["RestartPolicy"]["Name"], "no")
            self.assertEqual(host["LogConfig"]["Type"], "none")
            expected = [str(data) + ":/app/data:rw"]
            if group == "control-plane":
                expected.append(str(data.parent / "ledger") + ":/app/erasure-ledger:rw")
            self.assertEqual(host["Binds"], expected)
            self.assertEqual(cfg["User"], "10001:10001")
            self.assertEqual(cfg["Entrypoint"][0], "uvicorn")
            env = dict(x.split("=", 1) for x in cfg["Env"])
            self.assertEqual(env["MH_LOCAL_JOBS"], "0")
            self.assertEqual(env["WB_SAAS_INTERNAL_API_URL"], "")
            self.assertEqual(env["WB_SAAS_TELEGRAM_ENABLED"], "0")
            for secret in ("WB_SAAS_SMTP_PASSWORD", "WB_SAAS_TELEGRAM_BOT_TOKEN", "WB_SAAS_YOOKASSA_SECRET_KEY"):
                self.assertEqual(env[secret], "")
            if group == "control-plane":
                self.assertEqual(env["WB_SAAS_ERASURE_LEDGER_REQUIRED"], "1")

    def test_row_signature_detects_equal_count_corruption_and_has_no_row_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "test.sqlite3"
            with closing(sqlite3.connect(path)) as conn:
                conn.execute("CREATE TABLE settings (key TEXT, value TEXT)")
                conn.execute("INSERT INTO settings VALUES ('private-key-name','private-value')")
                conn.commit()
            original = dr.table_signature(path)
            self.assertNotIn("private-value", str(original))
            with closing(sqlite3.connect(path)) as conn:
                conn.execute("UPDATE settings SET value='corrupted-value'")
                conn.commit()
            changed = dr.table_signature(path)
            self.assertEqual(len(original["settings"]["rows"]), len(changed["settings"]["rows"]))
            self.assertNotEqual(original, changed)

    def test_failed_run_cleanup_never_removes_other_run_or_live_container(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / (".dr-app-rehearsal-" + "a" * 32)
            root.mkdir()
            own = "mh-dr-app-" + "a" * 32 + "-demo"
            foreign = "mh-dr-app-" + "b" * 32 + "-demo"
            calls = []
            def fake_api(method, path, body=None):
                calls.append((method, path))
                if path == "/containers/json?all=true":
                    return [{"Id": "own", "Names": ["/" + own]},
                            {"Id": "foreign", "Names": ["/" + foreign]},
                            {"Id": "live", "Names": ["/wb-tenant-demo"]}]
                if path == "/containers/own/json":
                    return {"Config": {"Labels": {"mh.dr.rehearsal": "true"}},
                            "HostConfig": {"NetworkMode": "none"}}
            with patch.object(dr, "checked_root", return_value=root), patch.object(dr, "docker", side_effect=fake_api):
                dr.cleanup_server(root)
            self.assertIn(("DELETE", "/containers/own?force=true&v=true"), calls)
            self.assertFalse(any("foreign" in p or "live" in p for _, p in calls))
            self.assertFalse(root.exists())

    def test_failed_run_cleanup_refuses_matching_name_with_wrong_label(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / (".dr-app-rehearsal-" + "a" * 32)
            root.mkdir()
            own_name = "mh-dr-app-" + "a" * 32 + "-demo"
            calls = []
            def fake_api(method, path, body=None):
                calls.append((method, path))
                if path == "/containers/json?all=true":
                    return [{"Id": "foreign", "Names": ["/" + own_name]}]
                return {"Config": {"Labels": {}}, "HostConfig": {"NetworkMode": "none"}}
            with patch.object(dr, "checked_root", return_value=root), patch.object(dr, "docker", side_effect=fake_api):
                with self.assertRaises(ValueError):
                    dr.cleanup_server(root)
            self.assertFalse(any(m == "DELETE" for m, _ in calls))
            self.assertTrue(root.exists())

    def test_signature_retains_original_autoincrement_counter_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "test.sqlite3"
            with closing(sqlite3.connect(path)) as conn:
                conn.execute("CREATE TABLE original (id INTEGER PRIMARY KEY AUTOINCREMENT)")
                conn.execute("INSERT INTO original DEFAULT VALUES")
                conn.commit()
            original = dr.table_signature(path)["sqlite_sequence"]
            self.assertEqual(original["row_keys"], ["original"])
            with closing(sqlite3.connect(path)) as conn:
                conn.execute("CREATE TABLE additive (id INTEGER PRIMARY KEY AUTOINCREMENT)")
                conn.execute("INSERT INTO additive DEFAULT VALUES")
                conn.commit()
                old_counter = conn.execute("SELECT * FROM sqlite_sequence WHERE name='original'").fetchall()
            expected_old_counter = sorted(dr.hashlib.sha256(repr(row).encode()).hexdigest() for row in old_counter)
            self.assertEqual(original["rows"], expected_old_counter)
            self.assertNotEqual(original["rows"], dr.table_signature(path)["sqlite_sequence"]["rows"])

    def test_supplier_startup_timestamp_only_is_excluded_not_business_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "test.sqlite3"
            with closing(sqlite3.connect(path)) as conn:
                conn.execute("CREATE TABLE suppliers (id INTEGER, name TEXT, contact TEXT, created_at TEXT, updated_at TEXT)")
                conn.execute("INSERT INTO suppliers VALUES (1,'synthetic-name','synthetic-contact','2020-01-01','2020-01-01')")
                conn.commit()
            original = dr.table_signature(path)
            with closing(sqlite3.connect(path)) as conn:
                conn.execute("UPDATE suppliers SET updated_at='2026-10-13'")
                conn.commit()
            self.assertEqual(original, dr.table_signature(path))
            with closing(sqlite3.connect(path)) as conn:
                conn.execute("UPDATE suppliers SET name='corrupt-name'")
                conn.commit()
            self.assertNotEqual(original, dr.table_signature(path))
            with closing(sqlite3.connect(path)) as conn:
                conn.execute("UPDATE suppliers SET name='synthetic-name',contact='corrupt-contact'")
                conn.commit()
            self.assertNotEqual(original, dr.table_signature(path))


if __name__ == "__main__":
    unittest.main()
