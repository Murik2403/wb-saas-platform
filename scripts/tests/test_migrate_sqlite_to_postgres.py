"""Real disposable PostgreSQL migration checks; synthetic data only."""
import importlib.util
import sqlite3
import uuid
from pathlib import Path

import pytest
psycopg = pytest.importorskip("psycopg", reason="Install gateway locked requirements for real PostgreSQL migration checks")
pytest.importorskip("testcontainers", reason="Disposable PostgreSQL requires gateway test dependencies")
from psycopg import sql
from psycopg.conninfo import make_conninfo

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("migration_pg_fixture", ROOT / "gateway/tests/conftest.py")
fixture_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture_module)
postgres_dsn = fixture_module.postgres_dsn
spec = importlib.util.spec_from_file_location("verified_migration", ROOT / "scripts/migrate_sqlite_to_postgres.py")
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


@pytest.fixture
def target(postgres_dsn):
    schema = "migration_" + uuid.uuid4().hex
    with psycopg.connect(postgres_dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        yield make_conninfo(postgres_dsn, options=f"-c search_path={schema}")
    finally:
        with psycopg.connect(postgres_dsn, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def source(tmp_path, schema):
    path = tmp_path / "source.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.executescript(schema)
    return path


def execute(dsn, statement):
    with psycopg.connect(dsn) as conn:
        cursor = conn.execute(statement)
        return cursor.fetchall() if cursor.description else None


def test_repeated_keyless_rows_preserve_multiplicity(tmp_path, target):
    path = source(tmp_path, "CREATE TABLE notes(value TEXT); INSERT INTO notes VALUES('same'),('same'),(NULL);")
    execute(target, "CREATE TABLE notes(value TEXT)")
    migration.migrate(path, target)
    migration.migrate(path, target)
    assert execute(target, "SELECT COUNT(*) FROM notes") == [(3,)]


def test_conflicting_primary_key_does_not_report_success(tmp_path, target, capsys):
    path = source(tmp_path, "CREATE TABLE items(id INTEGER PRIMARY KEY,value TEXT); INSERT INTO items VALUES(1,'source-secret');")
    execute(target, "CREATE TABLE items(id INTEGER PRIMARY KEY,value TEXT); INSERT INTO items VALUES(1,'target-secret')")
    with pytest.raises(ValueError, match="verification") as error:
        migration.migrate(path, target)
    assert "secret" not in str(error.value)
    assert "Migration complete" not in capsys.readouterr().out
    assert execute(target, "SELECT value FROM items") == [("target-secret",)]


def test_identity_import_and_next_insert(tmp_path, target):
    path = source(tmp_path, "CREATE TABLE items(id INTEGER PRIMARY KEY,value TEXT); INSERT INTO items VALUES(501,'imported');")
    execute(target, "CREATE TABLE items(id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,value TEXT)")
    migration.migrate(path, target)
    migration.migrate(path, target)
    assert execute(target, "INSERT INTO items(value) VALUES('new') RETURNING id")[0][0] > 501


@pytest.mark.parametrize("body", ["NEW.value := 'changed'; RETURN NEW;", "RETURN NULL;"])
def test_silent_trigger_changes_roll_back_all_tables(tmp_path, target, body):
    path = source(tmp_path, "CREATE TABLE first(id INTEGER PRIMARY KEY); INSERT INTO first VALUES(1); CREATE TABLE items(id INTEGER PRIMARY KEY,value TEXT); INSERT INTO items VALUES(1,'expected');")
    execute(target, "CREATE TABLE first(id INTEGER PRIMARY KEY); CREATE TABLE items(id INTEGER PRIMARY KEY,value TEXT)")
    execute(target, "CREATE FUNCTION alter_import() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN " + body + " END; $$; CREATE TRIGGER tamper BEFORE INSERT ON items FOR EACH ROW EXECUTE FUNCTION alter_import()")
    with pytest.raises(ValueError, match="verification"):
        migration.migrate(path, target)
    assert execute(target, "SELECT COUNT(*) FROM first") == [(0,)]
    assert execute(target, "SELECT COUNT(*) FROM items") == [(0,)]


def test_extra_target_row_rejected(tmp_path, target):
    path = source(tmp_path, "CREATE TABLE items(id INTEGER PRIMARY KEY); INSERT INTO items VALUES(1);")
    execute(target, "CREATE TABLE items(id INTEGER PRIMARY KEY); INSERT INTO items VALUES(2)")
    with pytest.raises(ValueError, match="verification"):
        migration.migrate(path, target)
    assert execute(target, "SELECT id FROM items") == [(2,)]


def test_partial_keyless_target_is_rejected(tmp_path, target):
    path = source(tmp_path, "CREATE TABLE notes(value TEXT); INSERT INTO notes VALUES('same'),('same');")
    execute(target, "CREATE TABLE notes(value TEXT); INSERT INTO notes VALUES('same')")
    with pytest.raises(ValueError, match="verification"):
        migration.migrate(path, target)
    assert execute(target, "SELECT COUNT(*) FROM notes") == [(1,)]


def test_missing_source_not_created(tmp_path, target):
    path = tmp_path / "absent.sqlite3"
    with pytest.raises((FileNotFoundError, ValueError, sqlite3.OperationalError)):
        migration.migrate(path, target)
    assert not path.exists()


def test_source_snapshot_is_stable_during_concurrent_write(tmp_path, target, monkeypatch):
    path = source(tmp_path, "PRAGMA journal_mode=WAL; CREATE TABLE items(id INTEGER PRIMARY KEY,value TEXT); INSERT INTO items VALUES(1,'before');")
    execute(target, "CREATE TABLE items(id INTEGER PRIMARY KEY,value TEXT)")
    real_connect = psycopg.connect

    def after_source_snapshot(*args, **kwargs):
        pg = real_connect(*args, **kwargs)
        with sqlite3.connect(path) as writer:
            writer.execute("UPDATE items SET value='after'")
        return pg

    monkeypatch.setattr(psycopg, "connect", after_source_snapshot)
    migration.migrate(path, target)
    monkeypatch.setattr(psycopg, "connect", real_connect)
    assert execute(target, "SELECT value FROM items") == [("before",)]


def test_foreign_keys_composite_key_and_quoted_names(tmp_path, target):
    path = source(tmp_path, 'CREATE TABLE "parent odd"(id INTEGER PRIMARY KEY); INSERT INTO "parent odd" VALUES(7); CREATE TABLE children(parent_id INTEGER REFERENCES "parent odd"(id),code TEXT,payload BLOB,PRIMARY KEY(parent_id,code)); INSERT INTO children VALUES(7,\'a\',x\'0102\');')
    execute(target, 'CREATE TABLE "parent odd"(id INTEGER PRIMARY KEY); CREATE TABLE children(parent_id INTEGER REFERENCES "parent odd"(id),code TEXT,payload BYTEA,PRIMARY KEY(parent_id,code))')
    migration.migrate(path, target)
    migration.migrate(path, target)
    assert execute(target, "SELECT parent_id,code,payload FROM children") == [(7, "a", b"\x01\x02")]


def test_later_trigger_cannot_change_already_verified_table(tmp_path, target):
    path = source(tmp_path, "CREATE TABLE first(id INTEGER PRIMARY KEY,value TEXT); INSERT INTO first VALUES(1,'expected'); CREATE TABLE last(id INTEGER PRIMARY KEY); INSERT INTO last VALUES(1);")
    execute(target, "CREATE TABLE first(id INTEGER PRIMARY KEY,value TEXT); CREATE TABLE last(id INTEGER PRIMARY KEY)")
    execute(target, "CREATE FUNCTION alter_earlier() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN UPDATE first SET value='changed'; RETURN NEW; END; $$; CREATE TRIGGER tamper BEFORE INSERT ON last FOR EACH ROW EXECUTE FUNCTION alter_earlier()")
    with pytest.raises(ValueError, match="verification"):
        migration.migrate(path, target)
    assert execute(target, "SELECT COUNT(*) FROM first") == [(0,)]


def test_full_current_control_plane_schema(tmp_path, target, monkeypatch):
    from logic import accounts, db
    import config
    path = tmp_path / "gateway.sqlite3"
    with monkeypatch.context() as scoped:
        scoped.setattr(config, "DB_BACKEND", "sqlite")
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            db.init_db(conn)
            owner = accounts.create_account(conn, "migration@example.test", "synthetic-password")
            conn.execute("INSERT INTO payments(account_id,yookassa_payment_id,created_at,updated_at) VALUES(?,?,?,?)", (owner, "synthetic-payment", "2026-10-13", "2026-10-13"))
        scoped.setattr(config, "DB_BACKEND", "postgres")
        scoped.setattr(config, "DATABASE_URL", target)
        with db.connect() as conn:
            db.init_db(conn)
    source_bytes = path.read_bytes()
    migration.migrate(path, target)
    migration.migrate(path, target)
    assert path.read_bytes() == source_bytes
    assert execute(target, "SELECT COUNT(*) FROM accounts") == [(1,)]
    assert execute(target, "SELECT COUNT(*) FROM payments") == [(1,)]
    assert execute(target, "INSERT INTO accounts(email,password_hash,password_salt,created_at,updated_at) VALUES('new@example.test','synthetic','synthetic','date','date') RETURNING id")[0][0] > owner


def test_cycle_fails_before_copy(tmp_path, target):
    path = source(tmp_path, "CREATE TABLE a(id INTEGER PRIMARY KEY,b_id INTEGER REFERENCES b(id)); CREATE TABLE b(id INTEGER PRIMARY KEY,a_id INTEGER REFERENCES a(id));")
    with pytest.raises(ValueError, match="cyclic"):
        migration.migrate(path, target)


def test_deferred_trigger_runs_before_final_verification(tmp_path, target):
    path = source(tmp_path, "CREATE TABLE first(id INTEGER PRIMARY KEY,value TEXT); INSERT INTO first VALUES(1,'expected'); CREATE TABLE last(id INTEGER PRIMARY KEY); INSERT INTO last VALUES(1);")
    execute(target, "CREATE TABLE first(id INTEGER PRIMARY KEY,value TEXT); CREATE TABLE last(id INTEGER PRIMARY KEY)")
    execute(target, "CREATE FUNCTION alter_deferred() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN UPDATE first SET value='changed-at-commit'; RETURN NEW; END; $$; CREATE CONSTRAINT TRIGGER tamper AFTER INSERT ON last DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION alter_deferred()")
    with pytest.raises(ValueError, match="verification"):
        migration.migrate(path, target)
    assert execute(target, "SELECT COUNT(*) FROM first") == [(0,)]
    assert execute(target, "SELECT COUNT(*) FROM last") == [(0,)]


def test_sequence_ddl_trigger_runs_before_final_verification(tmp_path, target):
    path = source(tmp_path, "CREATE TABLE first(id INTEGER PRIMARY KEY,value TEXT); INSERT INTO first VALUES(501,'expected');")
    execute(target, "CREATE TABLE first(id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,value TEXT)")
    trigger = "migration_ddl_" + uuid.uuid4().hex
    with psycopg.connect(target) as conn:
        conn.execute("CREATE FUNCTION alter_sequence_rows() RETURNS event_trigger LANGUAGE plpgsql AS $$ BEGIN UPDATE first SET value='changed-by-ddl'; END; $$")
        conn.execute(sql.SQL("CREATE EVENT TRIGGER {} ON ddl_command_end WHEN TAG IN ('ALTER SEQUENCE') EXECUTE FUNCTION alter_sequence_rows()").format(sql.Identifier(trigger)))
    try:
        with pytest.raises(ValueError, match="verification"):
            migration.migrate(path, target)
        assert execute(target, "SELECT COUNT(*) FROM first") == [(0,)]
    finally:
        with psycopg.connect(target) as conn:
            conn.execute(sql.SQL("DROP EVENT TRIGGER {}").format(sql.Identifier(trigger)))
