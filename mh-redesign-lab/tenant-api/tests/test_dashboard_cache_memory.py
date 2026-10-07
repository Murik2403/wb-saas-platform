import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dataclasses import dataclass
from threading import Event
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
from dashboard_cache import DashboardResponseCache,retained_size

def test_byte_budget_evicts_lru_and_clear_reclaims_accounting():
 cache=DashboardResponseCache(max_bytes=1000)
 cache.get_or_build('first',lambda:'a'*500)
 cache.get_or_build('second',lambda:'b'*500)
 assert list(cache._values)==['second'] and cache.retained_bytes<=1000
 cache.clear();assert cache.retained_bytes==0 and cache._sizes=={}

def test_oversize_returned_without_retaining_or_serializing(monkeypatch):
 cache=DashboardResponseCache(max_bytes=1000);value={'huge':'x'*2000};calls=[]
 assert cache.get_or_build('huge',lambda:calls.append(1) or value) is value
 assert not cache._values and cache.retained_bytes==0
 cache.get_or_build('huge',lambda:calls.append(1) or value);assert calls==[1,1]

def test_deep_dataframe_dataclass_size_and_shared_objects():
 @dataclass
 class Snapshot:
  frame:object
  metadata:object
 df=pd.DataFrame({'strings':['large-value-'+str(i)+'x'*200 for i in range(100)]})
 snapshot=Snapshot(df,{'also_same_frame':df})
 size=retained_size(snapshot,1000000)
 assert size>=int(df.memory_usage(index=True,deep=True).sum())
 assert size<int(df.memory_usage(index=True,deep=True).sum())*1.5
 cache=DashboardResponseCache(max_bytes=1000)
 assert cache.get_or_build('frame',lambda:snapshot) is snapshot and not cache._values


def test_nested_dict_size_counts_all_unique_values_and_cycles():
 values={str(i):'unshared'+str(i)+'x'*100 for i in range(200)}
 assert retained_size(values,1000000)>=sum(sys.getsizeof(v) for v in values.values())
 cycle=[];cycle.append(cycle)
 assert retained_size(cycle,1000)==sys.getsizeof(cycle)

def test_expiry_and_generation_race_do_not_leave_phantom_bytes(monkeypatch):
 clock=[1.0];monkeypatch.setattr('dashboard_cache.monotonic',lambda:clock[0]);cache=DashboardResponseCache(ttl_seconds=1,max_bytes=1000)
 cache.get_or_build('old',lambda:'old');clock[0]=2
 cache.get_or_build('new',lambda:'new');assert list(cache._sizes)==['new']
 started,release=Event(),Event()
 def build():started.set();assert release.wait(2);return 'pending'
 with ThreadPoolExecutor(max_workers=1) as executor:
  pending=executor.submit(cache.get_or_build,'pending',build);assert started.wait(2);cache.clear();release.set();assert pending.result()=='pending'
 assert cache.retained_bytes==0 and not cache._values

def test_memory_accounting_failure_does_not_fail_successful_response(monkeypatch):
 monkeypatch.setattr('dashboard_cache.retained_size',lambda *a:(_ for _ in ()).throw(ValueError()))
 cache=DashboardResponseCache();assert cache.get_or_build('ready',lambda:{'ok':True})=={'ok':True}
 assert cache.retained_bytes==0 and not cache._values

def test_tiny_dataframe_view_does_not_hide_large_retained_numpy_buffer():
 import numpy as np
 owner=np.arange(100000,dtype=np.int64)
 frame=pd.DataFrame(owner,copy=False).iloc[:1]
 assert retained_size(frame,1000)>1000
 cache=DashboardResponseCache(max_bytes=1000)
 assert cache.get_or_build('small-view',lambda:frame) is frame and not cache._values
