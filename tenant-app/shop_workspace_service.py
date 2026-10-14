from __future__ import annotations
import json,os,sqlite3,subprocess,sys,tempfile
import hashlib
from collections import OrderedDict
from contextlib import closing,contextmanager
from threading import BoundedSemaphore
from pathlib import Path
from datetime import date
from time import monotonic,sleep
import config
from db import shop_workspaces as shops
_worker_slot=BoundedSemaphore(1)
_reader_queue=BoundedSemaphore(8)
_read_cache=OrderedDict()
_read_cache_bytes=0

def cached_prepare(shop_id,action,start,end,timeout,kwargs,lock_handle=None):
    """Five-second, eight-MiB cache keyed by frozen shop and exact read context.

    Called only while owning the single worker slot. The HTTP dispatcher still
    verifies ACL, seller identity and revision before and after every response.
    """
    global _read_cache_bytes
    reading=action=='request' and kwargs.get('request',{}).get('method')=='GET'
    key=None
    if reading:
        from db.core import connect
        with connect() as conn:
            row=conn.execute('SELECT account_identity,revision FROM shop_workspaces WHERE id=? AND active=1',(shop_id,)).fetchone() if conn.execute("SELECT 1 FROM sqlite_master WHERE name='shop_workspaces'").fetchone() else None
        if row:
            key=hashlib.sha256(json.dumps([shop_id,row['account_identity'],row['revision'],start,end,kwargs],sort_keys=True,allow_nan=False).encode()).hexdigest()
        now=monotonic()
        for old in list(_read_cache):
            if _read_cache[old][0]<=now:
                _,encoded=_read_cache.pop(old);_read_cache_bytes-=len(encoded)
        if key in _read_cache:return json.loads(_read_cache[key][1])
    result=_execute(shop_id,action,start,end,_timeout=timeout,_prepare=True,_lock_handle=lock_handle,**kwargs)
    if key and result.get('response',{}).get('status',500)<400:
        encoded=json.dumps(result,allow_nan=False).encode()
        if len(encoded)<=1024*1024:
            while _read_cache and (len(_read_cache)>=8 or _read_cache_bytes+len(encoded)>8*1024*1024):
                _,(_,removed)=_read_cache.popitem(last=False);_read_cache_bytes-=len(removed)
            _read_cache[key]=(monotonic()+5,encoded);_read_cache_bytes+=len(encoded)
    return result

@contextmanager
def worker_slot(action,kwargs,deadline):
    reading=action=='report' or (action=='request' and kwargs.get('request',{}).get('method') in {'GET','HEAD','OPTIONS'})
    queued=False;acquired=False
    try:
        if reading:
            queued=_reader_queue.acquire(blocking=False)
            if not queued:raise ValueError('Очередь чтения магазина заполнена. Повторите позже')
            acquired=_worker_slot.acquire(timeout=max(0,deadline-monotonic()))
        else:acquired=_worker_slot.acquire(blocking=False)
        if not acquired:raise ValueError('Другой магазин уже обрабатывается. Повторите позже')
        # Scheduler and web API are different processes. A process-local
        # semaphore alone permits two memory-heavy children in one tenant.
        from sync_lock import sync_lock,SyncBusyError
        guard=None
        while guard is None:
            candidate=sync_lock(config.DATA_DIR/'.shop-worker.lock')
            try:lock_handle=candidate.__enter__();guard=candidate
            except SyncBusyError:
                if not reading or monotonic()>=deadline:raise ValueError('Другой магазин уже обрабатывается. Повторите позже')
                sleep(min(.05,max(0,deadline-monotonic())))
        try:yield lock_handle
        finally:guard.__exit__(None,None,None)
    finally:
        if acquired:_worker_slot.release()
        if queued:_reader_queue.release()

def execute(shop_id:int,action:str,start:str,end:str,*,_timeout:int|None=None,**kwargs)->dict:
    deadline=monotonic()+(_timeout or (600 if action=='sync' else 90))
    with worker_slot(action,kwargs,deadline) as handle:return _execute(shop_id,action,start,end,_timeout=max(.001,deadline-monotonic()),_lock_handle=handle,**kwargs)

def prepare(shop_id:int,action:str,start:str,end:str,*,_timeout:int|None=None,**kwargs)->dict:
    """Run a private child without committing any main snapshot or warehouse fact."""
    deadline=monotonic()+(_timeout or (600 if action=='sync' else 90))
    with worker_slot(action,kwargs,deadline) as handle:return cached_prepare(shop_id,action,start,end,max(.001,deadline-monotonic()),kwargs,handle)

def _execute(shop_id: int, action: str, start: str, end: str, *,_timeout:int|None=None,_prepare:bool=False,_lock_handle=None,**kwargs) -> dict:
    if date.fromisoformat(end)<date.fromisoformat(start):raise ValueError('Конец периода раньше начала')
    if (date.fromisoformat(end)-date.fromisoformat(start)).days>366:raise ValueError('Период не больше года')
    from reports import pdf_budget
    request = kwargs.get('request') or {}
    request_path = request.get('path', '')
    renders_pdf = action == 'scheduled_pdf' or (action == 'request' and (
        request_path == '/api/v1/operations/reports/pdf' or (request.get('method') == 'POST' and (
            request_path == '/api/v1/operations/reports/quick' or request_path.endswith('/generate')))))
    if renders_pdf and pdf_budget.holds_slot():
        # Current dispatch/schedules acquire in the child. Refuse a nested PDF
        # caller rather than have its child wait on a cabinet lock held by its parent.
        raise pdf_budget.PdfBusy('Нельзя запускать второй PDF внутри текущей сборки')
    state=shops.worker_input(shop_id)
    with tempfile.TemporaryDirectory(dir=config.DATA_DIR,prefix='.shop-workspace-') as private:
        root=Path(private);os.chmod(root,0o700)
        database=root/'wb_dashboard.sqlite3'
        if state['snapshot']:
            database.write_bytes(state['snapshot'])
        env={key:value for key,value in os.environ.items() if key.upper() in {
          'PATH','SYSTEMROOT','WINDIR','TEMP','TMP','LANG','LC_ALL','LD_LIBRARY_PATH','SSL_CERT_FILE','SSL_CERT_DIR'}}
        for key in ('WB_SAAS_PDF_SLOT_DIR', 'WB_SAAS_PDF_SLOTS', 'WB_SAAS_PDF_WAIT_SECONDS'):
            if key in os.environ:
                env[key] = os.environ[key]
        # This is computed from the actual cabinet data directory, never the
        # request body or an inherited arbitrary internal environment value.
        env['MH_INTERNAL_PDF_TENANT_DIR'] = str(Path(config.DATA_DIR).resolve())
        env.update(WB_SAAS_DATA_DIR=str(root),SHOP_RESULT_PATH=str(root/'result.json'),WB_SAAS_TENANT_SLUG='shop-worker')
        payload={**kwargs,'token':state['token'],'action':action,'start':start,'end':end,'source_errors':state['source_errors']}
        # On Linux the child inherits the same flock open-file description.
        # A killed parent cannot release the tenant child slot while its child lives.
        inherited={'pass_fds':(_lock_handle.fileno(),)} if os.name!='nt' and _lock_handle is not None else {}
        try:
            result=subprocess.run([sys.executable,str(Path(__file__).with_name('shop_workspace_worker.py'))],
                input=json.dumps(payload),capture_output=True,text=True,env=env,timeout=_timeout or (600 if action=='sync' else 90),**inherited)
        except subprocess.TimeoutExpired as exc:
            raise ValueError('Операция магазина не завершилась вовремя; прежние данные сохранены') from exc
        if result.returncode!=0:
            raise ValueError('Не удалось обработать магазин; прежние данные сохранены. Проверьте подключение и доступ к API')
        report=json.loads((root/'result.json').read_text(encoding='utf-8'))
        prepared_snapshot=None
        snapshot_required=action!='report' and not (action=='request' and kwargs['request']['method'] in {'GET','HEAD','OPTIONS'})
        if action=='request' and report.get('status',500)>=400:
            snapshot_required=False
        if action=='scheduled_pdf' and report.get('pdf_busy') is True:
            snapshot_required=False
        if snapshot_required:
            snapshot=root/'snapshot.sqlite3'
            with closing(sqlite3.connect(database)) as source, closing(sqlite3.connect(snapshot)) as target:
                source.backup(target)
                if target.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('База магазина не прошла проверку')
            if snapshot.stat().st_size>shops.MAX_SNAPSHOT:raise ValueError('База магазина превысила допустимый размер')
            prepared_snapshot=snapshot.read_bytes()
            if not _prepare:shops.store_snapshot(shop_id,state['revision'],prepared_snapshot,synced=report.get('synced') is True,source_errors=report['sync_errors'])
        if _prepare:
            return {'base_revision':state['revision'],'identity':state['identity'],'snapshot':prepared_snapshot,'response':report,
                    'synced':report.get('synced') is True,'source_errors':report.get('sync_errors',[])}
        return report
