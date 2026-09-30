"""Bounded private-shop PDF archive carried in its encrypted SQLite snapshot."""
from pathlib import Path
import hashlib
from db.core import connect,DB_PATH

MAX_FILE=4*1024*1024
MAX_ARCHIVE=32*1024*1024

def restore():
    root=(Path(DB_PATH).parent/'reports'/'shop-archive').resolve()
    with connect() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS shop_report_assets(run_id INTEGER PRIMARY KEY,sha256 TEXT NOT NULL,pdf BLOB NOT NULL)')
        for operation in ('UPDATE','DELETE'):
            conn.execute(f"CREATE TRIGGER IF NOT EXISTS shop_report_assets_no_{operation.lower()} BEFORE {operation} ON shop_report_assets BEGIN SELECT RAISE(ABORT,'report artifact immutable'); END")
        conn.execute("CREATE TRIGGER IF NOT EXISTS shop_report_assets_no_replace BEFORE INSERT ON shop_report_assets WHEN EXISTS(SELECT 1 FROM shop_report_assets WHERE run_id=NEW.run_id) BEGIN SELECT RAISE(ABORT,'report artifact immutable'); END")
        total=0
        for row in conn.execute('SELECT run_id,sha256,pdf FROM shop_report_assets'):
            data=bytes(row['pdf']);total+=len(data)
            if len(data)>MAX_FILE or total>MAX_ARCHIVE or not data.startswith(b'%PDF-') or hashlib.sha256(data).hexdigest()!=row['sha256']:
                raise ValueError('Invalid shop report archive')
            root.mkdir(parents=True,exist_ok=True)
            path=root/f"report-{int(row['run_id'])}.pdf";path.write_bytes(data)
            conn.execute('UPDATE report_runs SET file_path=? WHERE id=? AND status=?',(str(path),row['run_id'],'ok'))

def persist():
    root=(Path(DB_PATH).parent/'reports').resolve()
    with connect() as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='report_runs'").fetchone():return
        total=conn.execute('SELECT COALESCE(SUM(length(pdf)),0) FROM shop_report_assets').fetchone()[0]
        for row in conn.execute("SELECT id,file_path FROM report_runs WHERE status='ok' AND file_path IS NOT NULL"):
            if conn.execute('SELECT 1 FROM shop_report_assets WHERE run_id=?',(row['id'],)).fetchone():continue
            path=Path(row['file_path']).resolve()
            if root not in path.parents or not path.is_file():continue
            if path.stat().st_size>MAX_FILE:raise ValueError('Shop PDF exceeds size limit')
            data=path.read_bytes();total+=len(data)
            if not data.startswith(b'%PDF-') or total>MAX_ARCHIVE:raise ValueError('Shop PDF archive exceeds limit')
            conn.execute('INSERT INTO shop_report_assets VALUES(?,?,?)',(row['id'],hashlib.sha256(data).hexdigest(),data))

def generate_one(run_id):
    # Manual generation only. Private workers never send emails or Telegram.
    from reports.store import ReportStore
    import report_scheduler
    store=ReportStore(DB_PATH)
    if type(run_id)!=int or run_id<1:raise ValueError('Invalid selected report run')
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute("SELECT r.id AS run_id,r.period_days,r.delivery_requested,d.* FROM report_runs r JOIN report_definitions d ON d.id=r.definition_id WHERE r.id=? AND r.status='queued'",(run_id,)).fetchone()
        if row is None:return
        job=dict(row)
        conn.execute("UPDATE report_runs SET status='running' WHERE id=? AND status='queued'",(run_id,))
    run_id=int(job.pop('run_id'));days=job.pop('period_days',None)
    delivery=bool(job.pop('delivery_requested',False))
    report_scheduler.generate_and_save(store,job,period_days=int(days) if days else None,run_id=run_id)
    if delivery:store.finish_delivery(run_id,status='error',error='Доставка дополнительных магазинов не подключена; файл сохранён')
    persist()
