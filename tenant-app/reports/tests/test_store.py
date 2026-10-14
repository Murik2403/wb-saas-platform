from reports.store import ReportStore
from concurrent.futures import ThreadPoolExecutor
import sqlite3


def _initialize_legacy_db(db_path: object) -> None:
    # emulate DB layout before optional report schema columns were introduced
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE report_definitions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            metrics TEXT NOT NULL,
            schedule_type TEXT NOT NULL,
            schedule_time TEXT NOT NULL,
            schedule_weekday INTEGER,
            schedule_day INTEGER,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            last_run_at TEXT
        );
        CREATE TABLE report_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            definition_id INTEGER NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL,
            file_path TEXT,
            error TEXT
        );
        """
    )
    conn.commit()
    conn.close()


def make_store(tmp_path) -> ReportStore:
    return ReportStore(tmp_path / "test_reports.sqlite3")


def test_add_and_list_definition(tmp_path):
    store = make_store(tmp_path)
    def_id = store.add_definition(
        name="Тест", metrics=["sales_orders"], schedule_type="daily", schedule_time="09:00",
    )
    definitions = store.list_definitions()
    assert len(definitions) == 1
    assert definitions[0]["id"] == def_id
    assert definitions[0]["name"] == "Тест"
    assert definitions[0]["enabled"] == 1
    assert definitions[0]["email_enabled"] == 0
    assert definitions[0]["last_run_at"] is None


def test_add_definition_with_email_enabled(tmp_path):
    store = make_store(tmp_path)
    store.add_definition(
        name="Тест", metrics=["sales_orders"], schedule_type="daily", schedule_time="09:00", email_enabled=True,
    )
    definitions = store.list_definitions()
    assert definitions[0]["email_enabled"] == 1


def test_email_enabled_column_added_to_pre_existing_db(tmp_path):
    # Simulates a report_definitions table created before email_enabled
    # existed -- CREATE TABLE IF NOT EXISTS is a no-op against it, so
    # ReportStore.__init__ must ALTER TABLE it in, not silently skip it.
    import sqlite3
    db_path = tmp_path / "legacy_reports.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE report_definitions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, metrics TEXT NOT NULL,
            schedule_type TEXT NOT NULL, schedule_time TEXT NOT NULL,
            schedule_weekday INTEGER, schedule_day INTEGER,
            enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, last_run_at TEXT
        )"""
    )
    conn.commit()
    conn.close()

    store = ReportStore(db_path)
    def_id = store.add_definition(name="Старый", metrics=["ads"], schedule_type="daily", schedule_time="09:00")
    definitions = store.list_definitions()
    assert definitions[0]["id"] == def_id
    assert definitions[0]["email_enabled"] == 0


def test_delete_definition_cascades_runs(tmp_path):
    store = make_store(tmp_path)
    def_id = store.add_definition(name="Тест", metrics=["ads"], schedule_type="daily", schedule_time="09:00")
    run_id = store.start_run(def_id)
    store.finish_run(run_id, status="ok", file_path="/tmp/x.pdf")

    store.delete_definition(def_id)
    assert store.list_definitions() == []
    assert store.list_runs(definition_id=def_id) == []


def test_run_lifecycle(tmp_path):
    store = make_store(tmp_path)
    def_id = store.add_definition(name="Тест", metrics=["stocks"], schedule_type="weekly", schedule_time="10:00", schedule_weekday=2)
    run_id = store.start_run(def_id)
    runs = store.list_runs(definition_id=def_id)
    assert runs[0]["status"] == "running"

    store.finish_run(run_id, status="ok", file_path="/tmp/report.pdf")
    runs = store.list_runs(definition_id=def_id)
    assert runs[0]["status"] == "ok"
    assert runs[0]["file_path"] == "/tmp/report.pdf"


def test_set_last_run(tmp_path):
    store = make_store(tmp_path)
    def_id = store.add_definition(name="Тест", metrics=["ads"], schedule_type="monthly", schedule_time="08:00", schedule_day=1)
    store.set_last_run(def_id, "2026-08-23T08:00:00")
    definitions = store.list_definitions()
    assert definitions[0]["last_run_at"] == "2026-08-23T08:00:00"


def test_queued_run_can_be_claimed_and_completed(tmp_path):
    store = make_store(tmp_path)
    def_id = store.add_definition(name="Большой", metrics=["sales_orders", "pnl"], schedule_type="weekly", schedule_time="09:00")
    run_id = store.enqueue_run(def_id, period_days=7, delivery_requested=True)

    queued = store.list_runs()[0]
    assert queued["status"] == "queued"
    assert queued["definition_name"] == "Большой"

    job = store.claim_next_queued()
    assert job is not None
    assert job["run_id"] == run_id
    assert job["period_days"] == 7
    assert job["delivery_requested"] == 1
    assert store.list_runs()[0]["status"] == "running"

    store.finish_run(run_id, status="ok", file_path="/tmp/report.pdf")
    store.finish_delivery(run_id, status="ok")
    completed = store.list_runs()[0]
    assert completed["status"] == "ok"
    assert completed["delivery_status"] == "ok"


def test_interrupted_run_is_requeued_after_restart(tmp_path):
    store = make_store(tmp_path)
    def_id = store.add_definition(name="Прерванный", metrics=["ads"], schedule_type="daily", schedule_time="09:00")
    run_id = store.start_run(def_id)

    assert store.requeue_interrupted_runs() == 0  # restarting one worker must not steal an active API lease
    with store._conn() as conn:
        conn.execute("UPDATE report_runs SET lease_expires_at='2000-01-01T00:00:00' WHERE id=?", (run_id,))
    assert store.requeue_interrupted_runs() == 1
    run = store.list_runs()[0]
    assert run["id"] == run_id
    assert run["status"] == "queued"
    assert "прервана" in run["error"]


def test_parallel_init_idempotent_for_legacy_db_schema(tmp_path):
    # When several workers start at once on an existing database, schema migration
    # must not fail with "duplicate column name" and must finish with all expected columns.
    db_path = tmp_path / "parallel_reports.sqlite3"
    _initialize_legacy_db(db_path)

    def init_once() -> None:
        ReportStore(db_path)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda _: init_once(), range(12)))

    conn = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(report_definitions)").fetchall()}
        assert "email_enabled" in columns
        assert "telegram_enabled" in columns

        run_columns = {row[1] for row in conn.execute("PRAGMA table_info(report_runs)").fetchall()}
        assert "period_days" in run_columns
        assert "delivery_requested" in run_columns
        assert "delivery_status" in run_columns
        assert "delivery_error" in run_columns
    finally:
        conn.close()
