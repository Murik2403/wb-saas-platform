#!/usr/bin/env python3
"""One-time data transfer: control-plane SQLite database -> Postgres.

Only relevant when actually cutting the control-plane db over to Postgres
(see DEPLOY.md's "Switching the control-plane db to Postgres" section) --
this does NOT create the Postgres schema itself, only copies rows into
tables that must already exist there (created by logic.db.init_db() with
config.DB_BACKEND=postgres set).

Schema-agnostic on purpose: it introspects the SQLite side (sqlite_master +
PRAGMA table_info/foreign_key_list) rather than hardcoding the control-plane
table list, so it keeps working if a table gets added later without this
script needing an update to match. Tables are copied in FK-safe order (a
table referenced by another table's FOREIGN KEY is always copied first) and
each INSERT uses "ON CONFLICT (<primary key>) DO NOTHING", so re-running
this against a Postgres database that already has some of the rows is a
no-op for those rows rather than a duplicate-key error -- safe to re-run if
it's interrupted partway through.

Requires `psycopg[binary]` (in gateway/requirements.txt already) and must
run with the SAME sys.path setup as the other
scripts here (PYTHONPATH=/app inside the gateway container) since it reads
config.DATABASE_URL and logic.db.DEFAULT_DB_PATH for its defaults; see
backup_tenant_volumes.py's docstring for why a bare host-side run would
open the wrong (empty) SQLite file.

Usage (from inside the gateway container, PYTHONPATH=/app):
    python3 scripts/migrate_sqlite_to_postgres.py
    python3 scripts/migrate_sqlite_to_postgres.py --sqlite-path /path/to/gateway.sqlite3 --pg-dsn postgresql://...
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict, deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gateway"))

import config  # noqa: E402
from logic import db as control_db  # noqa: E402


def _topo_sorted_tables(sqlite_conn: sqlite3.Connection) -> list[str]:
    """Tables in an order where every table a FOREIGN KEY points to comes
    before the table that references it -- so inserting in this order never
    violates a Postgres FK constraint on the target side."""
    cur = sqlite_conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    tables = [row[0] for row in cur.fetchall()]

    # depends_on[t] = set of tables t has a FOREIGN KEY into.
    depends_on: dict[str, set[str]] = defaultdict(set)
    for t in tables:
        cur.execute(f"PRAGMA foreign_key_list('{t}')")
        for fk in cur.fetchall():
            ref_table = fk[2]
            if ref_table in tables and ref_table != t:
                depends_on[t].add(ref_table)

    in_degree = {t: len(depends_on[t]) for t in tables}
    dependents: dict[str, set[str]] = defaultdict(set)
    for t, parents in depends_on.items():
        for p in parents:
            dependents[p].add(t)

    queue = deque(t for t in tables if in_degree[t] == 0)
    ordered: list[str] = []
    while queue:
        t = queue.popleft()
        ordered.append(t)
        for child in dependents[t]:
            in_degree[child] -= 1
            if in_degree[child] == 0:
                queue.append(child)

    if len(ordered) != len(tables):
        print(
            "warning: FK cycle detected among control-plane tables; "
            "falling back to sqlite_master's declaration order",
            file=sys.stderr,
        )
        return tables
    return ordered


def migrate(sqlite_path: Path, pg_dsn: str) -> None:
    try:
        import psycopg
        from psycopg import sql
    except ImportError as exc:
        raise SystemExit(
            "psycopg[binary] is missing -- run `pip install -r requirements.txt` "
            "inside the gateway container first (see DEPLOY.md)."
        ) from exc

    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row
    try:
        tables = _topo_sorted_tables(sqlite_conn)
        summary: dict[str, int] = {}

        with psycopg.connect(pg_dsn) as pg_conn:
            with pg_conn.transaction():
                for table in tables:
                    columns_info = sqlite_conn.execute(f"PRAGMA table_info('{table}')").fetchall()
                    col_names = [c["name"] for c in columns_info]
                    pk_cols = [c["name"] for c in sorted((c for c in columns_info if c["pk"] > 0), key=lambda c: c["pk"])]

                    rows = sqlite_conn.execute(f'SELECT * FROM "{table}"').fetchall()
                    summary[table] = len(rows)
                    if not rows:
                        continue

                    insert_stmt = sql.SQL("INSERT INTO {table} ({cols}) VALUES ({placeholders}){conflict}").format(
                        table=sql.Identifier(table),
                        cols=sql.SQL(", ").join(sql.Identifier(c) for c in col_names),
                        placeholders=sql.SQL(", ").join(sql.Placeholder() * len(col_names)),
                        conflict=(
                            sql.SQL(" ON CONFLICT ({}) DO NOTHING").format(
                                sql.SQL(", ").join(sql.Identifier(c) for c in pk_cols)
                            )
                            if pk_cols
                            else sql.SQL("")
                        ),
                    )
                    with pg_conn.cursor() as cur:
                        cur.executemany(insert_stmt, [tuple(row) for row in rows])
    finally:
        sqlite_conn.close()

    print("\nMigration complete:")
    for table, count in summary.items():
        print(f"  {table:<28} {count} row(s)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sqlite-path",
        type=Path,
        default=control_db.DEFAULT_DB_PATH,
        help=f"Source SQLite file (default: {control_db.DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--pg-dsn",
        default=config.DATABASE_URL,
        help="Destination Postgres DSN (default: config.DATABASE_URL / WB_SAAS_DATABASE_URL)",
    )
    args = parser.parse_args()

    if not args.sqlite_path.exists():
        raise SystemExit(f"No SQLite file at {args.sqlite_path}")
    if not args.pg_dsn:
        raise SystemExit("No Postgres DSN given (pass --pg-dsn or set WB_SAAS_DATABASE_URL)")

    migrate(args.sqlite_path, args.pg_dsn)


if __name__ == "__main__":
    main()
