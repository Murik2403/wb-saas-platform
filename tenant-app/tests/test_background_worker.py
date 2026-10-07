import importlib.util,sys,subprocess
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import background_worker as worker
class Clock:
 def __init__(self):self.now=0
 def __call__(self):return self.now
class Stop:
 def __init__(self,clock,until=905):self.clock=clock;self.until=until;self.stopped=False
 def is_set(self):return self.stopped or self.clock.now>=self.until
 def set(self):self.stopped=True
 def wait(self,seconds):self.clock.now+=seconds;return self.is_set()
def test_import_does_not_load_heavy_business_modules():
 code="import sys,background_worker; assert all(n not in sys.modules for n in ('pandas','sync','agent_sync','report_scheduler')); print('lazy-ok')"
 result=subprocess.run([sys.executable,'-c',code],cwd=ROOT,capture_output=True,text=True,timeout=10)
 assert result.returncode==0 and 'lazy-ok' in result.stdout

def test_serial_ticks_preserve_intervals_and_errors_isolated(caplog):
 clock=Clock();stop=Stop(clock);calls=[];active=0
 def job(name):
  def run():
   nonlocal active
   assert active==0;active+=1;calls.append((name,clock()))
   active-=1
   if name=='sync':raise RuntimeError('private-api-key-must-not-escape')
  return run
 worker.run(stop,clock=clock,load_settings=lambda:{'sync_interval_minutes':1,'agent_interval_minutes':15},jobs={name:job(name) for name in ('sync','agents','reports')})
 assert [at for name,at in calls if name=='sync']==[0,900]
 assert [at for name,at in calls if name=='agents']==[0,900]
 assert [at for name,at in calls if name=='reports'][:3]==[0,5,10]
 assert 'private-api-key' not in caplog.text

def test_stop_during_tick_does_not_start_other_jobs():
 clock=Clock();stop=Stop(clock);calls=[]
 def first():calls.append('sync');stop.set()
 worker.run(stop,clock=clock,load_settings=lambda:{},jobs={'sync':first,'agents':lambda:calls.append('agents')})
 assert calls==['sync']

def test_failed_or_invalid_settings_does_not_kill_tick(caplog):
 clock=Clock();stop=Stop(clock,until=1);calls=[]
 worker.run(stop,clock=clock,load_settings=lambda:None,jobs={'reports':lambda:calls.append('report')})
 assert calls==['report']
 assert worker.interval({'sync_interval_minutes':'bad'},'sync_interval_minutes',30)==1800

def test_supervisor_holds_existing_locks_recovers_then_releases(monkeypatch,tmp_path):
 from contextlib import contextmanager
 import config,sync_lock
 import reports.store
 monkeypatch.setattr(config,'DB_PATH',tmp_path/'data.sqlite')
 events=[]
 @contextmanager
 def lock(path):
  events.append(('lock',path.suffix))
  try:yield
  finally:events.append(('unlock',path.suffix))
 class Store:
  def __init__(self,path):pass
  def requeue_interrupted_runs(self):events.append(('recover',None))
 monkeypatch.setattr(sync_lock,'sync_lock',lock);monkeypatch.setattr(reports.store,'ReportStore',Store);monkeypatch.setattr(worker,'run',lambda stop:events.append(('run',None)))
 worker.main()
 assert events==[('lock','.lock'),('lock','.lock'),('recover',None),('run',None),('unlock','.lock'),('unlock','.lock')]
