from __future__ import annotations
import json,os,sqlite3,subprocess,sys,tempfile
from contextlib import closing
from threading import BoundedSemaphore
from pathlib import Path
from datetime import date
import config
from db import shop_workspaces as shops
_worker_slot=BoundedSemaphore(1)

def execute(shop_id:int,action:str,start:str,end:str,*,_timeout:int|None=None,**kwargs)->dict:
    if not _worker_slot.acquire(blocking=False):
        raise ValueError('Другой магазин уже обрабатывается. Повторите позже')
    try:return _execute(shop_id,action,start,end,_timeout=_timeout,**kwargs)
    finally:_worker_slot.release()

def _execute(shop_id: int, action: str, start: str, end: str, *,_timeout:int|None=None,**kwargs) -> dict:
    if date.fromisoformat(end)<date.fromisoformat(start):raise ValueError('Конец периода раньше начала')
    if (date.fromisoformat(end)-date.fromisoformat(start)).days>366:raise ValueError('Период не больше года')
    state=shops.worker_input(shop_id)
    with tempfile.TemporaryDirectory(dir=config.DATA_DIR,prefix='.shop-workspace-') as private:
        root=Path(private);os.chmod(root,0o700)
        database=root/'wb_dashboard.sqlite3'
        if state['snapshot']:
            database.write_bytes(state['snapshot'])
        env=dict(os.environ,WB_SAAS_DATA_DIR=str(root),SHOP_RESULT_PATH=str(root/'result.json'),WB_SAAS_TENANT_SLUG='shop-worker')
        for name in ('WB_SAAS_INTERNAL_API_URL','WB_SAAS_INTERNAL_API_SECRET','WB_SAAS_BILLING_URL','WB_SAAS_LOGOUT_URL'):
            env.pop(name,None)
        payload={'token':state['token'],'action':action,'start':start,'end':end,'source_errors':state['source_errors'],**kwargs}
        try:
            result=subprocess.run([sys.executable,str(Path(__file__).with_name('shop_workspace_worker.py'))],
                input=json.dumps(payload),capture_output=True,text=True,env=env,timeout=_timeout or (600 if action=='sync' else 90))
        except subprocess.TimeoutExpired as exc:
            raise ValueError('Операция магазина не завершилась вовремя; прежние данные сохранены') from exc
        if result.returncode!=0:
            raise ValueError('Не удалось обработать магазин; прежние данные сохранены. Проверьте подключение и доступ к API')
        report=json.loads((root/'result.json').read_text(encoding='utf-8'))
        if action!='report':
            snapshot=root/'snapshot.sqlite3'
            with closing(sqlite3.connect(database)) as source, closing(sqlite3.connect(snapshot)) as target:
                source.backup(target)
                if target.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('База магазина не прошла проверку')
            if snapshot.stat().st_size>shops.MAX_SNAPSHOT:raise ValueError('База магазина превысила допустимый размер')
            shops.store_snapshot(shop_id,state['revision'],snapshot.read_bytes(),synced=action=='sync',source_errors=report['sync_errors'])
        return report
