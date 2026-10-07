"""One serial background supervisor; business ticks keep their existing locks and schedules."""
from __future__ import annotations
from contextlib import ExitStack
import importlib,logging,signal,time
from threading import Event
logger=logging.getLogger('marketshelper.background')

def interval(settings,key,default):
    try:return max(15,int(settings.get(key,default)))*60
    except (ValueError,TypeError,OverflowError):return default*60

def run(stop=None,*,clock=time.monotonic,load_settings=None,jobs=None):
    stop=stop or Event()
    if load_settings is None:
        from config import load_settings
    if jobs is None:
        # Imports are lazy: one interpreter shares pandas/db/agent modules.
        jobs={
            'reports':lambda:importlib.import_module('report_scheduler').check_once(),
            'sync':lambda:importlib.import_module('sync').sync_all(),
            'agents':lambda:importlib.import_module('agent_sync').run_once(),
        }
    if not jobs:return
    due={name:clock() for name in jobs}
    while not stop.is_set():
        try:settings=load_settings()
        except Exception:
            logger.warning('Background settings unavailable; using normal schedule defaults')
            settings={}
        if not isinstance(settings,dict):settings={}
        waits={'sync':interval(settings,'sync_interval_minutes',30),'agents':interval(settings,'agent_interval_minutes',60),'reports':5}
        for name,job in jobs.items():
            if stop.is_set():break
            if clock()<due[name]:continue
            try:job()
            except Exception:
                # A failed source must not stop other schedules or expose credentials.
                logger.warning('Background tick failed: %s',name)
            finally:due[name]=clock()+waits.get(name,5)
        if not stop.is_set():stop.wait(max(.05,min(5,min(due.values())-clock())))

def main():
    stop=Event()
    previous={}
    def stopping(*_):stop.set()
    for sig in (signal.SIGTERM,signal.SIGINT):previous[sig]=signal.signal(sig,stopping)
    try:
        from config import DB_PATH
        from sync_lock import sync_lock
        with ExitStack() as locks:
            # Existing separate agent/report loops cannot run alongside this supervisor.
            locks.enter_context(sync_lock(DB_PATH.with_suffix('.agents.lock')))
            locks.enter_context(sync_lock(DB_PATH.with_suffix('.reports.lock')))
            # Recovery remains before the first tick, as in report_scheduler.loop().
            from reports.store import ReportStore
            ReportStore(DB_PATH).requeue_interrupted_runs()
            run(stop)
    finally:
        for sig,handler in previous.items():signal.signal(sig,handler)
if __name__=='__main__':
    logging.basicConfig(level=logging.INFO)
    main()
