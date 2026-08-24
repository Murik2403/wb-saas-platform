from reports.store import ReportStore


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
