"""pytest-only fixtures. Everything else in this directory is unittest.TestCase
(see base.py) and runs fine under pytest without this file -- this exists
solely for test_db_backend_postgres.py, which needs a real, disposable
Postgres to actually exercise the psycopg code path (see that file's
docstring for why db.py's own sqlite-based tests can't cover this).
"""
from __future__ import annotations

import json
import os
import socket
import shutil
import subprocess
import time
from contextlib import closing
from pathlib import Path

import pytest


def _docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(
            ["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5
        ).returncode == 0
    except Exception:
        return False


def _podman_machine_ssh_config() -> dict[str, object] | None:
    """Return Podman machine SSH details when Docker CLI targets Podman."""
    if not shutil.which("podman"):
        return None
    try:
        server = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}} {{.OperatingSystem}}"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.lower()
        if "fedora" not in server and "podman" not in server:
            return None
        result = subprocess.run(
            ["podman", "machine", "inspect", "--format", "{{json .SSHConfig}}", "podman-machine-default"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None


def _container_ip(pg) -> str:
    info = pg.get_docker_client().get_container(pg.get_container_id())
    networks = info["NetworkSettings"]["Networks"]
    return next(network["IPAddress"] for network in networks.values() if network.get("IPAddress"))


def _free_local_port() -> int:
    with closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _podman_tunnel(pg) -> tuple[subprocess.Popen, int] | None:
    """Expose a Podman VM container to Windows when published ports are not forwarded."""
    config = _podman_machine_ssh_config()
    if not config or not shutil.which("ssh"):
        return None

    identity = Path(str(config["IdentityPath"]))
    vm_port = str(config["Port"])
    local_port = _free_local_port()
    command = [
        "ssh",
        "-N",
        "-L",
        f"127.0.0.1:{local_port}:{_container_ip(pg)}:5432",
        "-p",
        vm_port,
        "-i",
        str(identity),
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=NUL",
        f"{config['RemoteUsername']}@127.0.0.1",
    ]
    tunnel = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if tunnel.poll() is not None:
            error = (tunnel.stderr.read() if tunnel.stderr else "").strip()
            raise RuntimeError(f"Podman SSH tunnel exited early: {error}")
        try:
            with socket.create_connection(("127.0.0.1", local_port), timeout=0.25):
                return tunnel, local_port
        except OSError:
            time.sleep(0.1)

    tunnel.terminate()
    tunnel.wait(timeout=2)
    raise RuntimeError("Podman SSH tunnel did not become ready")


@pytest.fixture(scope="session")
def postgres_dsn():
    if not _docker_available():
        pytest.skip("Docker not available -- skipping real-Postgres tests")

    from testcontainers.community.postgres import PostgresContainer
    if _podman_machine_ssh_config():
        # Podman machine does not expose Ryuk's published port to Windows.
        os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")
        from testcontainers.core import config as testcontainers_config

        testcontainers_config.ryuk_disabled = True

    # get_connection_url() returns a SQLAlchemy-style URL
    # ("postgresql+psycopg://...") regardless of the driver= we pass --
    # that "+psycopg" segment isn't valid libpq conninfo syntax, so
    # psycopg.connect() rejects it outright ("missing '=' after
    # postgresql+psycopg://..."). Strip the driver suffix from the scheme
    # before handing it to our own code, which uses plain psycopg.connect().
    with PostgresContainer("postgres:16-alpine", driver="psycopg") as pg:
        tunnel = _podman_tunnel(pg)
        try:
            if tunnel:
                url = f"postgresql+psycopg://{pg.username}:{pg.password}@127.0.0.1:{tunnel[1]}/{pg.dbname}"
            else:
                url = pg.get_connection_url()
            scheme, _, rest = url.partition("://")
            plain_scheme = scheme.split("+", 1)[0]
            yield f"{plain_scheme}://{rest}"
        finally:
            if tunnel:
                tunnel[0].terminate()
                tunnel[0].wait(timeout=2)
