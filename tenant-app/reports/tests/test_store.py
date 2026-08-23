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
    assert definitions[0]["last_run_at"] is None


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
