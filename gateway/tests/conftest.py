"""pytest-only fixtures. Everything else in this directory is unittest.TestCase
(see base.py) and runs fine under pytest without this file -- this exists
solely for test_db_backend_postgres.py, which needs a real, disposable
Postgres to actually exercise the psycopg code path (see that file's
docstring for why db.py's own sqlite-based tests can't cover this).
"""
from __future__ import annotations

import shutil
import subprocess

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


@pytest.fixture(scope="session")
def postgres_dsn():
    if not _docker_available():
        pytest.skip("Docker not available -- skipping real-Postgres tests")

    from testcontainers.postgres import PostgresContainer

    # get_connection_url() returns a SQLAlchemy-style URL
    # ("postgresql+psycopg://...") regardless of the driver= we pass --
    # that "+psycopg" segment isn't valid libpq conninfo syntax, so
    # psycopg.connect() rejects it outright ("missing '=' after
    # postgresql+psycopg://..."). Strip the driver suffix from the scheme
    # before handing it to our own code, which uses plain psycopg.connect().
    with PostgresContainer("postgres:16-alpine", driver="psycopg") as pg:
        url = pg.get_connection_url()
        scheme, _, rest = url.partition("://")
        plain_scheme = scheme.split("+", 1)[0]
        yield f"{plain_scheme}://{rest}"
