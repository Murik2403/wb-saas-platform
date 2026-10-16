"""The stand runner: what it runs, in which order, and that it cannot relax a criterion. No Docker is needed."""
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("run_local_daemon_acceptance_under_test", SCRIPTS / "run_local_daemon_acceptance.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def test_the_phases_are_the_agreed_sequences_and_run_in_order():
    assert runner.PHASES["smoke"] == [("load", 2), ("load", 3), ("load", 4)]
    assert runner.PHASES["load"] == [("load", 20), ("load", 25), ("load", 30)]
    assert runner.PHASES["faults"] == [("faults", 3), ("faults", 4)]
    assert all(phase in runner.DEADLINE_SECONDS for phase in runner.PHASES)


def test_the_load_command_uses_two_slots_and_passes_no_relaxing_option():
    command, out = runner.benchmark_command("load", 25, "sha256:abc")
    assert command[:2] == ["python3", "scripts/bench_pdf_slots_load.py"] and out == "/results/pdf-slots-load-25.json"
    assert command[command.index("--slots") + 1] == "2" and command[command.index("--tenants") + 1] == "25"
    relaxing = ("--allow-dirty", "--margin-seconds", "--pdf-seconds", "--min-vm-available-mib", "--health-p95-ms", "--grace-seconds", "--expect-metrics", "--api-pdf-every", "--api-parallel")
    assert not [option for option in relaxing if option in command], "the stand must run the benchmark with its default deadlines and criteria"


def test_the_fault_command_and_unknown_kinds():
    command, out = runner.benchmark_command("faults", 3, "sha256:abc")
    assert command[:2] == ["python3", "scripts/bench_pdf_faults.py"] and out == "/results/pdf-faults-3.json" and "--allow-dirty" not in command
    with pytest.raises(ValueError):
        runner.benchmark_command("other", 3, "sha256:abc")


def test_the_stand_limits_are_the_ones_of_the_docker24_audit():
    assert (runner.DAEMON_MEMORY, runner.DAEMON_CPUS, runner.DAEMON_PIDS) == ("20g", "10", "16000")
    assert runner.MIN_WINDOWS_FREE_BYTES == 2 * 1024 ** 3 and runner.PREFLIGHT_WINDOWS_FREE_BYTES == 7 * 1024 ** 3


def test_a_short_commit_id_is_refused_before_anything_is_touched(tmp_path):
    with pytest.raises(SystemExit):
        runner.main(["--phase", "smoke", "--head", "abc123", "--image-id", "sha256:x", "--image-tar", "a", "--helper-tar", "b", "--bundle", "c", "--evidence", str(tmp_path)])
