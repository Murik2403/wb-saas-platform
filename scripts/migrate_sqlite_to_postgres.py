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
no-op only when the existing content is identical. The entire source is
read under one SQLite snapshot; destination tables are locked and their
full row multisets are checked before commit. Conflicts or unexpected rows
roll back the transfer. Keyless tables must be empty or exactly identical.
Run only against an offline destination and stop source writers for cutover;
this does not synchronize changes made after the source snapshot.

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
from collections import Counter, defaultdict, deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gateway"))

import config  # noqa: E402
from logic import db as control_db  # noqa: E402


def _sqlite_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


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
        cur.execute(f"PRAGMA foreign_key_list({_sqlite_identifier(t)})")
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
        raise ValueError("Migration verification failed: cyclic table dependencies")
    return ordered


def _rows_equal(source_rows, target_rows) -> bool:
    # Multisets retain duplicate counts in keyless tables; no values printed.
    def normalized(rows):
        return Counter(tuple(bytes(v) if isinstance(v, memoryview) else v for v in row) for row in rows)
    return normalized(source_rows) == normalized(target_rows)


def _advance_sequences(pg_conn, table: str, columns: list[str]) -> None:
    """Identity imports preserve IDs and leave future generated IDs collision-free.

    ALTER SEQUENCE is transactional (unlike setval). Never move its position
    backwards on an exact re-run, including IDs of previously deleted rows.
    """
    from psycopg import sql
    for column in columns:
        sequence = pg_conn.execute("SELECT pg_get_serial_sequence(%s, %s)",
                                   (_sqlite_identifier(table), column)).fetchone()[0]
        if not sequence:
            continue
        namespace, name, start, increment, last = pg_conn.execute(
            "SELECT n.nspname,c.relname,s.seqstart,s.seqincrement,pg_sequence_last_value(c.oid) "
            "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "JOIN pg_sequence s ON s.seqrelid=c.oid WHERE c.oid=%s::regclass", (sequence,)
        ).fetchone()
        if increment <= 0:
            raise ValueError("Migration verification failed: unsupported descending sequence")
        largest = pg_conn.execute(sql.SQL("SELECT MAX({}) FROM {}").format(
            sql.Identifier(column), sql.Identifier(table))).fetchone()[0]
        next_id = max(start, (last + increment) if last is not None else start,
                      (largest + increment) if largest is not None else start)
        pg_conn.execute(sql.SQL("ALTER SEQUENCE {} RESTART WITH {}").format(
            sql.Identifier(namespace, name), sql.Literal(next_id)))


def migrate(sqlite_path: Path, pg_dsn: str) -> None:
    try:
        import psycopg
        from psycopg import sql
    except ImportError as exc:
        raise SystemExit(
            "psycopg[binary] is missing -- run `pip install -r requirements.txt` "
            "inside the gateway container first (see DEPLOY.md)."
        ) from exc

    sqlite_path = sqlite_path.resolve(strict=True)
    sqlite_conn = sqlite3.connect(sqlite_path.as_uri() + "?mode=ro", uri=True)
    sqlite_conn.row_factory = sqlite3.Row
    try:
        sqlite_conn.execute("BEGIN")
        tables = _topo_sorted_tables(sqlite_conn)
        if not tables:
            raise ValueError("Migration verification failed: source has no tables")
        summary: dict[str, int] = {}

        with psycopg.connect(pg_dsn) as pg_conn:
            with pg_conn.transaction():
                pg_conn.execute("SET LOCAL lock_timeout='10s'")
                pg_conn.execute(sql.SQL("LOCK TABLE {} IN ACCESS EXCLUSIVE MODE").format(
                    sql.SQL(", ").join(sql.Identifier(t) for t in sorted(tables))))
                for table in tables:
                    columns_info = sqlite_conn.execute(f"PRAGMA table_info({_sqlite_identifier(table)})").fetchall()
                    col_names = [c["name"] for c in columns_info]
                    pk_cols = [c["name"] for c in sorted((c for c in columns_info if c["pk"] > 0), key=lambda c: c["pk"])]

                    rows = sqlite_conn.execute(f'SELECT * FROM {_sqlite_identifier(table)}').fetchall()
                    summary[table] = len(rows)
                    select_stmt = sql.SQL("SELECT {cols} FROM {table}").format(
                        cols=sql.SQL(", ").join(sql.Identifier(c) for c in col_names),
                        table=sql.Identifier(table))
                    if not pk_cols:
                        existing = pg_conn.execute(select_stmt).fetchall()
                        if existing:
                            if not _rows_equal(rows, existing):
                                raise ValueError(f"Migration verification failed for table {table!r}")
                            continue

                    insert_stmt = sql.SQL("INSERT INTO {table} ({cols}) OVERRIDING SYSTEM VALUE VALUES ({placeholders}){conflict}").format(
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
                    if not _rows_equal(rows, pg_conn.execute(select_stmt).fetchall()):
                        raise ValueError(f"Migration verification failed for table {table!r}")
                for table in tables:
                    columns = [c["name"] for c in sqlite_conn.execute(
                        f"PRAGMA table_info({_sqlite_identifier(table)})").fetchall()]
                    _advance_sequences(pg_conn, table, columns)
                # Later table / sequence DDL triggers can change earlier rows.
                # Flush deferred triggers, then verify after ALL mutations.
                pg_conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
                for table in tables:
                    columns = [c["name"] for c in sqlite_conn.execute(
                        f"PRAGMA table_info({_sqlite_identifier(table)})").fetchall()]
                    rows = sqlite_conn.execute(f"SELECT * FROM {_sqlite_identifier(table)}").fetchall()
                    actual = pg_conn.execute(sql.SQL("SELECT {} FROM {}").format(
                        sql.SQL(", ").join(sql.Identifier(c) for c in columns),
                        sql.Identifier(table))).fetchall()
                    if not _rows_equal(rows, actual):
                        raise ValueError(f"Migration verification failed for table {table!r}")
    finally:
        sqlite_conn.close()

    print("\nMigration complete: destination row counts and contents verified.")
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

    try:
        migrate(args.sqlite_path, args.pg_dsn)
    except Exception as exc:
        # Driver errors can contain row values, connection credentials or
        # provider tokens. Operators get a safe failure, never a partial success.
        raise SystemExit(f"Migration failed ({type(exc).__name__}); transaction rolled back, no verified cutover.") from None


if __name__ == "__main__":
    main()
