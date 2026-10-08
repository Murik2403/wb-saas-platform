import ast
import argparse
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest


def load_main(monkeypatch, outcome):
    source = ast.parse(Path(__file__).resolve().parents[1].joinpath("upgrade_all_tenants.py").read_text())
    main = next(n for n in source.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    connection = Mock()
    connection.__enter__ = Mock(return_value=connection)
    connection.__exit__ = Mock(return_value=False)
    connection.execute.return_value.fetchall.return_value = [{"slug": "demo"}, {"slug": "real"}]
    upgrade = Mock(side_effect=outcome)
    namespace = {"argparse": argparse, "control_db": SimpleNamespace(connect=lambda: connection), "provisioning": SimpleNamespace(upgrade_tenant=upgrade)}
    monkeypatch.setattr("sys.argv", ["rollout"])
    exec(compile(ast.Module(body=[main], type_ignores=[]), "rollout-main", "exec"), namespace)
    return namespace["main"], upgrade


@pytest.mark.parametrize("outcome", [[False, True], [RuntimeError("private-error-secret"), True]])
def test_first_failure_stops_before_real_tenant(monkeypatch, capsys, outcome):
    main, upgrade = load_main(monkeypatch, outcome)
    assert main() == 1
    assert upgrade.call_count == 1
    assert "private-error-secret" not in capsys.readouterr().out


def test_success_advances_to_next_tenant(monkeypatch):
    main, upgrade = load_main(monkeypatch, [True, True])
    assert main() == 0
    assert upgrade.call_count == 2
