from pathlib import Path
import sqlite3
import pytest
import db.core as core
import shop_report_assets as assets
from reports.store import ReportStore

def test_archive_restore_identity_integrity_and_foreign_path_rejected(tmp_path,monkeypatch):
    root=tmp_path/'child';root.mkdir();database=root/'db.sqlite'
    monkeypatch.setattr(core,'DB_PATH',database);monkeypatch.setattr(assets,'DB_PATH',database)
    core.init_db();store=ReportStore(database)
    definition=store.add_definition(name='private',metrics=['stocks'],schedule_type='daily',schedule_time='00:00')
    run=store.start_run(definition);folder=root/'reports';folder.mkdir();pdf=folder/'first.pdf';pdf.write_bytes(b'%PDF-1.4 original')
    store.finish_run(run,status='ok',file_path=str(pdf))
    assets.restore();assets.persist()
    external=tmp_path/'foreign.pdf';external.write_bytes(b'%PDF-1.4 another shop')
    other=store.start_run(definition);store.finish_run(other,status='ok',file_path=str(external))
    assets.persist()
    with core.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM shop_report_assets').fetchone()[0]==1
        for statement in ('DELETE FROM shop_report_assets','UPDATE shop_report_assets SET pdf=pdf','INSERT OR REPLACE INTO shop_report_assets SELECT * FROM shop_report_assets'):
            with pytest.raises(sqlite3.IntegrityError):conn.execute(statement)
    assets.restore()
    restored=next(r for r in store.list_runs() if r['id']==run)
    assert Path(restored['file_path']).read_bytes()==b'%PDF-1.4 original'
    monkeypatch.setattr(assets,'MAX_FILE',2)
    with pytest.raises(ValueError):assets.restore()

def test_selected_generation_does_not_claim_older_queued_run(tmp_path,monkeypatch):
    import report_scheduler
    database=tmp_path/'db.sqlite'
    monkeypatch.setattr(core,'DB_PATH',database);monkeypatch.setattr(assets,'DB_PATH',database)
    monkeypatch.setattr(report_scheduler,'REPORTS_DIR',tmp_path/'reports')
    monkeypatch.setattr(report_scheduler,'build_report_pdf',lambda *args:b'%PDF-1.4 selected')
    core.init_db();store=ReportStore(database);assets.restore()
    first=store.add_definition(name='old',metrics=['stocks'],schedule_type='daily',schedule_time='00:00')
    second=store.add_definition(name='new',metrics=['stocks'],schedule_type='daily',schedule_time='00:00')
    old=store.enqueue_run(first);new=store.enqueue_run(second)
    assets.generate_one(new);assets.generate_one(new)
    rows={r['id']:r for r in store.list_runs()}
    assert rows[old]['status']=='queued' and rows[new]['status']=='ok'
    with core.connect() as conn:
        assert [r['run_id'] for r in conn.execute('SELECT run_id FROM shop_report_assets')]==[new]
