"""Owner opt-in delegation, UTC leases and immutable scheduler receipts."""
from __future__ import annotations
import hashlib,json,uuid
from datetime import datetime,timezone,timedelta
from .core import connect
from . import shop_workspaces as shops,shared_warehouse as warehouse,shop_request_bridge as bridge
from shop_membership import account_id

METRICS=('sales_orders','ads','stocks','pnl','abc_analysis','buyout_rate','oos_risk','wb_deductions','ad_performance','warehouse_stocks','unit_economics','returns_cancellations','organic_vs_ads','ad_funnel','cost_structure','wb_commission_rate','stock_in_transit','weekday_pattern','wb_incident_losses','frozen_capital','roi_by_sku')
NAMESPACE=uuid.UUID('b51caa01-e5e6-4f79-8b5a-9461088e18e6')
LEASE_SECONDS=660
MAX_ATTEMPTS=3

def utc(value=None):
    value=value or datetime.now(timezone.utc)
    if value.tzinfo is None:raise ValueError('Нужна дата UTC с часовым поясом')
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()

def timevalue(value):return datetime.fromisoformat(value).astimezone(timezone.utc)
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()).hexdigest()
def credential(row):return hashlib.sha256(row['credential_cipher'].encode()).hexdigest()

def initialize():
    bridge.initialize()
    with connect() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS shop_schedules(
          id INTEGER PRIMARY KEY,shop_id INTEGER NOT NULL,kind TEXT NOT NULL CHECK(kind IN ('sync','pdf')),
          enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),generation INTEGER NOT NULL,
          actor TEXT NOT NULL,identity TEXT NOT NULL,interval_minutes INTEGER NOT NULL,
          period_days INTEGER NOT NULL,metrics_json TEXT NOT NULL,next_due_utc TEXT NOT NULL,
          retry_at_utc TEXT,attempts INTEGER NOT NULL DEFAULT 0,lease_key TEXT,lease_until_utc TEXT,
          updated_at TEXT NOT NULL,UNIQUE(shop_id,kind))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS shop_schedule_events(
          id INTEGER PRIMARY KEY,run_key TEXT NOT NULL,schedule_id INTEGER NOT NULL,generation INTEGER NOT NULL,
          shop_id INTEGER NOT NULL,identity TEXT NOT NULL,kind TEXT NOT NULL,due_utc TEXT NOT NULL,
          status TEXT NOT NULL CHECK(status IN ('ok','failed')),error_code TEXT,created_at TEXT NOT NULL,
          artifact_json TEXT,applied_revision INTEGER)""")
        conn.execute("CREATE TRIGGER IF NOT EXISTS shop_schedule_success_no_replace BEFORE INSERT ON shop_schedule_events WHEN NEW.status='ok' AND EXISTS(SELECT 1 FROM shop_schedule_events WHERE run_key=NEW.run_key AND status='ok') BEGIN SELECT RAISE(ABORT,'schedule history immutable'); END")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS shop_schedule_success_once ON shop_schedule_events(run_key) WHERE status='ok'")
        conn.execute("""CREATE TABLE IF NOT EXISTS shop_schedule_config_receipts(
          request_key TEXT PRIMARY KEY,actor TEXT NOT NULL,fingerprint TEXT NOT NULL,result_json TEXT NOT NULL,created_at TEXT NOT NULL)""")
        for table,key in [('shop_schedule_events','id'),('shop_schedule_config_receipts','request_key')]:
            for operation in ('UPDATE','DELETE'):
                conn.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{operation.lower()} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'schedule history immutable'); END")
            conn.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_replace BEFORE INSERT ON {table} WHEN EXISTS(SELECT 1 FROM {table} WHERE {key}=NEW.{key}) BEGIN SELECT RAISE(ABORT,'schedule history immutable'); END")
        conn.execute("""CREATE TRIGGER IF NOT EXISTS shop_schedules_no_replace BEFORE INSERT ON shop_schedules WHEN EXISTS(SELECT 1 FROM shop_schedules WHERE id=NEW.id OR (shop_id=NEW.shop_id AND kind=NEW.kind)) BEGIN SELECT RAISE(ABORT,'schedule identity immutable'); END""")
        conn.execute("""CREATE TRIGGER IF NOT EXISTS shop_schedules_identity BEFORE UPDATE ON shop_schedules WHEN NEW.id!=OLD.id OR NEW.shop_id!=OLD.shop_id OR NEW.kind!=OLD.kind OR NEW.generation<OLD.generation OR NEW.generation>OLD.generation+1 OR (NEW.generation=OLD.generation AND (NEW.actor!=OLD.actor OR NEW.identity!=OLD.identity OR NEW.enabled!=OLD.enabled OR NEW.interval_minutes!=OLD.interval_minutes OR NEW.period_days!=OLD.period_days OR NEW.metrics_json!=OLD.metrics_json)) BEGIN SELECT RAISE(ABORT,'schedule identity immutable'); END""")
        conn.execute("CREATE TRIGGER IF NOT EXISTS shop_schedules_no_delete BEFORE DELETE ON shop_schedules BEGIN SELECT RAISE(ABORT,'disable schedule instead of deleting'); END")

def parameters(kind,enabled,interval_minutes,period_days,metrics):
    if kind not in ('sync','pdf') or type(enabled)!=bool:raise ValueError('Некорректный вид расписания')
    minimum=15 if kind=='sync' else 60
    maximum=1440 if kind=='sync' else 10080
    if type(interval_minutes)!=int or not minimum<=interval_minutes<=maximum:raise ValueError('Некорректный интервал расписания')
    if type(period_days)!=int or not 1<=period_days<=90:raise ValueError('Период от 1 до 90 дней')
    if not isinstance(metrics,list) or len(metrics)>8 or len(set(metrics))!=len(metrics) or any(type(x)!=str or x not in METRICS for x in metrics):raise ValueError('Некорректные метрики PDF')
    if kind=='pdf' and not metrics:raise ValueError('Выберите метрики PDF')
    if kind=='sync' and metrics:raise ValueError('Метрики применяются только к PDF')

def target(conn,shop_id,actor,write=False):
    account_id(actor)
    if type(shop_id)!=int or shop_id<=0:raise ValueError('Расписание доступно дополнительному магазину')
    row=conn.execute('SELECT * FROM shop_workspaces WHERE id=? AND active=1',(shop_id,)).fetchone()
    if not row:raise warehouse.WarehouseScopeError('Магазин отключён')
    # Fixed manager role deliberately requires explicit ACL even for current owner.
    scope=warehouse.access(conn,shop_id,actor,'manager',write)
    if scope['identity']!=row['account_identity']:raise warehouse.WarehouseScopeError('Продавец изменился')
    return row

def public_schedule(row):
    value=dict(row)
    for key in ('metrics_json',):value['metrics']=json.loads(value.pop(key))
    for key in ('actor','lease_key','lease_until_utc','attempts','retry_at_utc'):value.pop(key,None)
    value['enabled']=bool(value['enabled'])
    value['exists']=True
    return value

def listing(shop_id,actor,role):
    initialize()
    with connect() as conn:
        conn.execute('BEGIN')
        warehouse.access(conn,shop_id,actor,role)
        rows=conn.execute('SELECT * FROM shop_schedules WHERE shop_id=? ORDER BY kind',(shop_id,)).fetchall()
        events=conn.execute('SELECT * FROM shop_schedule_events WHERE shop_id=? ORDER BY id DESC LIMIT 50',(shop_id,)).fetchall()
        result=[]
        for kind in ('sync','pdf'):
            row=next((r for r in rows if r['kind']==kind),None)
            value=public_schedule(row) if row else dict(id=None,shop_id=shop_id,kind=kind,enabled=False,exists=False,generation=0,interval_minutes=30 if kind=='sync' else 1440,period_days=30,metrics=[] if kind=='sync' else ['pnl'],next_due_utc=None,updated_at=None)
            event=next((r for r in events if r['kind']==kind),None)
            value.update(last_status=event['status'] if event else None,last_error=event['error_code'] if event else None,last_run_key=event['run_key'] if event else None)
            result.append(value)
        runs=[]
        for event in events:
            event=dict(event);artifact=event.pop('artifact_json');event['artifact']=json.loads(artifact) if artifact else None;runs.append(event)
        return dict(shop_id=shop_id,schedules=result,runs=runs,metrics=list(METRICS),delivery='local_only')

def configure(shop_id,kind,actor,role,request_key,*,enabled,expected_generation,interval_minutes,period_days,metrics,now=None):
    if role!='owner':raise warehouse.WarehouseScopeError('Расписание настраивает владелец')
    actor=account_id(actor);key=bridge._key(request_key)
    parameters(kind,enabled,interval_minutes,period_days,metrics)
    if type(expected_generation)!=int or expected_generation<0:raise ValueError('Нужна версия расписания')
    body=dict(shop_id=shop_id,kind=kind,enabled=enabled,expected_generation=expected_generation,interval_minutes=interval_minutes,period_days=period_days,metrics=metrics)
    stamp=utc(now)
    initialize()
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        if enabled:record=target(conn,shop_id,actor,True)
        else:
            warehouse.access(conn,shop_id,actor,'owner')
            record=conn.execute('SELECT * FROM shop_workspaces WHERE id=? AND active=1',(shop_id,)).fetchone()
            if not record:raise warehouse.WarehouseScopeError('Магазин отключён')
        fingerprint=digest({**body,'identity':record['account_identity']})
        prior=conn.execute('SELECT * FROM shop_schedule_config_receipts WHERE request_key=?',(key,)).fetchone()
        if prior:
            if prior['actor']!=actor or prior['fingerprint']!=fingerprint:raise warehouse.WarehouseConflict('UUID уже использован для другого расписания')
            return json.loads(prior['result_json'])
        row=conn.execute('SELECT * FROM shop_schedules WHERE shop_id=? AND kind=?',(shop_id,kind)).fetchone()
        if (row['generation'] if row else 0)!=expected_generation:raise warehouse.WarehouseConflict('Расписание изменилось')
        due=utc(timevalue(stamp)+timedelta(minutes=interval_minutes));generation=expected_generation+1
        if row:
            conn.execute('UPDATE shop_schedules SET enabled=?,generation=?,actor=?,identity=?,interval_minutes=?,period_days=?,metrics_json=?,next_due_utc=?,retry_at_utc=NULL,attempts=0,lease_key=NULL,lease_until_utc=NULL,updated_at=? WHERE id=?',(int(enabled),generation,actor,record['account_identity'],interval_minutes,period_days,json.dumps(metrics),due,stamp,row['id']))
        else:
            conn.execute('INSERT INTO shop_schedules(shop_id,kind,enabled,generation,actor,identity,interval_minutes,period_days,metrics_json,next_due_utc,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(shop_id,kind,int(enabled),generation,actor,record['account_identity'],interval_minutes,period_days,json.dumps(metrics),due,stamp))
        value=public_schedule(conn.execute('SELECT * FROM shop_schedules WHERE shop_id=? AND kind=?',(shop_id,kind)).fetchone())
        conn.execute('INSERT INTO shop_schedule_config_receipts VALUES(?,?,?,?,?)',(key,actor,fingerprint,json.dumps(value,ensure_ascii=False,allow_nan=False),stamp))
        return value

def claim(now=None):
    # Disabled-by-default tenants do not initialize or mutate scheduling state.
    with connect() as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='shop_schedules'").fetchone():return None
    initialize();stamp=utc(now)
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT * FROM shop_schedules WHERE enabled=1 AND next_due_utc<=? AND (retry_at_utc IS NULL OR retry_at_utc<=?) AND (lease_key IS NULL OR lease_until_utc<=?) ORDER BY next_due_utc,id LIMIT 1',(stamp,stamp,stamp)).fetchone()
        if not row:return None
        lease=str(uuid.uuid4());until=utc(timevalue(stamp)+timedelta(seconds=LEASE_SECONDS))
        conn.execute('UPDATE shop_schedules SET lease_key=?,lease_until_utc=? WHERE id=?',(lease,until,row['id']))
        job=dict(row);job.update(lease_key=lease,lease_until_utc=until,run_key=str(uuid.uuid5(NAMESPACE,f"{row['id']}:{row['generation']}:{row['next_due_utc']}")))
        return job

def check_lease(conn,job,now=None):
    row=conn.execute('SELECT * FROM shop_schedules WHERE id=?',(job['id'],)).fetchone()
    if not row or not row['enabled'] or row['generation']!=job['generation'] or row['lease_key']!=job['lease_key'] or row['lease_until_utc']<=utc(now):raise warehouse.WarehouseConflict('Расписание или аренда запуска изменились')
    return row

def check(conn,job,now=None):
    check_lease(conn,job,now)
    record=target(conn,job['shop_id'],job['actor'],True)
    if record['account_identity']!=job['identity']:raise warehouse.WarehouseScopeError('Продавец расписания изменился')
    return record


def defer_pdf_busy(conn,job,now=None):
    """Release only this current PDF lease; preserve due, attempts and immutable event history."""
    row=check_lease(conn,job,now)
    if row['kind']!='pdf':raise ValueError('PDF budget deferral requires PDF schedule')
    retry=utc(timevalue(utc(now))+timedelta(seconds=5))
    updated=conn.execute('UPDATE shop_schedules SET retry_at_utc=?,lease_key=NULL,lease_until_utc=NULL '
      'WHERE id=? AND generation=? AND lease_key=?',(retry,job['id'],job['generation'],job['lease_key']))
    if updated.rowcount!=1:raise warehouse.WarehouseConflict('Расписание или аренда запуска изменились')

def finish(conn,job,*,status,error_code=None,artifact=None,revision=None,now=None):
    (check if status=='ok' else check_lease)(conn,job,now);stamp=utc(now)
    conn.execute('INSERT INTO shop_schedule_events(run_key,schedule_id,generation,shop_id,identity,kind,due_utc,status,error_code,created_at,artifact_json,applied_revision) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(job['run_key'],job['id'],job['generation'],job['shop_id'],job['identity'],job['kind'],job['next_due_utc'],status,error_code,stamp,json.dumps(artifact,allow_nan=False) if artifact else None,revision))
    attempts=job['attempts']+1
    due=timevalue(job['next_due_utc']);current=timevalue(stamp)
    if status=='ok' or attempts>=MAX_ATTEMPTS:
        interval=timedelta(minutes=job['interval_minutes'])
        due+=interval*max(1,int((current-due).total_seconds()//interval.total_seconds())+1)
        retry=None;attempts=0
    else:retry=utc(current+timedelta(minutes=5*attempts))
    conn.execute('UPDATE shop_schedules SET next_due_utc=?,retry_at_utc=?,attempts=?,lease_key=NULL,lease_until_utc=NULL WHERE id=?',(utc(due),retry,attempts,job['id']))
