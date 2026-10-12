import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

SCRIPT = Path(__file__).resolve().parents[1] / "bench_tenant_density.py"
spec = importlib.util.spec_from_file_location("bench_density_safety", SCRIPT)
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


class LocalEngineSafetyTests(unittest.TestCase):
    def guarded(self, endpoint, context="test-context"):
        with patch.dict(bench.os.environ, {}, clear=True), patch.object(bench, "docker", side_effect=[context, endpoint]):
            bench.assert_local_engine()

    def test_remote_and_unrecognized_transports_fail_closed(self):
        for endpoint in ("https://remote.example:2376", "http://remote.example:2375", "tcp://127.0.0.1:2375",
                         "ssh://remote.example", "unix://remote.example/run/docker.sock",
                         "npipe:////remote-machine/pipe/docker_engine", "npipe://remote-machine/pipe/docker_engine",
                         "npipe:////./pipe/", "unix://", "unix:////remote/docker.sock", "", "fd://", "unix:///run/docker.sock?host=remote"):
            with self.subTest(endpoint=endpoint), self.assertRaises(SystemExit):
                self.guarded(endpoint)

    def test_accepts_only_valid_local_unix_and_local_named_pipe(self):
        for endpoint in ("unix:///var/run/docker.sock", "unix:///run/user/1000/docker.sock",
                         "npipe:////./pipe/docker_engine", "npipe:////./pipe/dockerDesktopLinuxEngine"):
            with self.subTest(endpoint=endpoint):
                self.guarded(endpoint)
                self.assertEqual(bench._LOCAL_ENDPOINT, endpoint)

    def test_docker_host_override_is_rejected_before_any_command(self):
        with patch.dict(bench.os.environ, {"DOCKER_HOST": "https://remote.example"}, clear=True), patch.object(bench, "docker") as docker:
            with self.assertRaises(SystemExit):
                bench.assert_local_engine()
            docker.assert_not_called()

    def test_validated_cli_endpoint_stays_pinned_when_context_changes(self):
        with patch.object(bench, "_LOCAL_ENDPOINT", "unix:///var/run/docker.sock"), patch.dict(bench.os.environ, {"DOCKER_CONTEXT": "remote-later"}), patch.object(bench.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="ok", stderr="")) as run:
            bench.docker("ps", "-q")
            self.assertEqual(run.call_args.args[0], ["docker", "--host", "unix:///var/run/docker.sock", "ps", "-q"])
            self.assertNotIn("DOCKER_CONTEXT", run.call_args.kwargs["env"])

    def test_admission_sdk_uses_the_pinned_engine_not_environment_default(self):
        instance = bench.Bench(SimpleNamespace(prefix="bench", image="test-image", limit_mem="1536m", reservation="384m", min_vm_available_mib=1500))
        sdk = Mock()
        sdk.DockerClient.side_effect = RuntimeError("stop at verified client construction")
        sdk.from_env.side_effect = RuntimeError("wrong environment default client")
        with patch.object(bench, "_LOCAL_ENDPOINT", "unix:///run/user/1000/docker.sock"), patch.dict(bench.sys.modules, {"docker": sdk}):
            with self.assertRaisesRegex(RuntimeError, "verified client construction"):
                instance.admission("bench-next")
        sdk.DockerClient.assert_called_once_with(base_url="unix:///run/user/1000/docker.sock")
        sdk.from_env.assert_not_called()


class CleanupOwnershipSafetyTests(unittest.TestCase):
    def instance(self):
        instance = bench.Bench(SimpleNamespace(prefix="bench", image="test-image", limit_mem="1536m", reservation="384m", min_vm_available_mib=1500))
        instance.run_id = "owned123"
        instance.network = "bench-net-owned123"
        return instance

    def test_existing_foreign_volume_never_reaches_chown_or_seeder(self):
        instance = self.instance()
        with patch.object(bench, "docker", side_effect=["bench-data-01-owned123", "bench-data-01-owned123 someoneelse"]) as docker:
            with self.assertRaisesRegex(RuntimeError, "ownership label"):
                instance.create_tenant(1)
        self.assertFalse(any(call.args[0] == "run" for call in docker.call_args_list))

    def test_short_lived_helpers_have_names_and_run_labels_for_cleanup(self):
        instance = self.instance()
        def fake(*args, **kwargs):
            if args[:2] == ("volume", "inspect"): return "bench-data-01-owned123 owned123"
            return ""
        with patch.object(bench, "docker", side_effect=fake) as docker:
            instance.create_tenant(1)
        runs = [call.args for call in docker.call_args_list if call.args[0] == "run"]
        self.assertEqual(len(runs), 3)
        for args in runs:
            self.assertTrue(args[args.index("--name") + 1].startswith("bench-"))
            self.assertIn(f"{bench.LABEL}=owned123", args)

    def test_filters_are_not_sufficient_without_name_and_run_label(self):
        instance = self.instance()
        metadata = {
            ("container", "owned-id"): "/bench-t01-owned123 owned123",
            ("container", "foreign-id"): "/wb-tenant-demo owned123",
            ("container", "wrong-label-id"): "/bench-t02-owned123 someoneelse",
            ("volume", "bench-data-owned123"): "bench-data-owned123 owned123",
            ("volume", "customer-data"): "customer-data owned123",
            ("network", instance.network): f"{instance.network} someoneelse",
        }
        def fake(*args, **kwargs):
            if args[:2] == ("ps", "-aq"): return "owned-id foreign-id wrong-label-id"
            if args[:3] == ("volume", "ls", "-q"): return "bench-data-owned123 customer-data"
            if len(args) > 1 and args[1] == "inspect": return metadata.get((args[0], args[-1]), "")
            return ""
        with patch.object(bench, "docker", side_effect=fake) as docker:
            instance.cleanup()
        removes = [call.args for call in docker.call_args_list if "rm" in call.args]
        self.assertIn(("rm", "-f", "owned-id"), removes)
        self.assertIn(("volume", "rm", "-f", "bench-data-owned123"), removes)
        self.assertEqual(len(removes), 2)

    def test_missing_metadata_does_not_authorize_network_deletion(self):
        instance = bench.Bench(SimpleNamespace(prefix="bench", image="test-image", limit_mem="1536m", reservation="384m", min_vm_available_mib=1500))
        with patch.object(bench, "docker", return_value="") as docker:
            instance.cleanup()
        self.assertFalse(any("rm" in call.args for call in docker.call_args_list))


if __name__ == "__main__":
    unittest.main()
