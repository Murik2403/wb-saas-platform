import sys,json
from pathlib import Path
from uuid import uuid4
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/"tenant-app"));sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import db.core as core
import production_workspace_api as api
from production_workspace_api import router
P="/api/v1/production-workspace/workcenters"
@pytest.fixture
def client(monkeypatch,tmp_path):
 monkeypatch.setattr(core,"DB_PATH",tmp_path/"centers.sqlite");monkeypatch.setattr(api.config,"IS_DEMO",False);core.init_db()
 with core.connect() as c:
  c.execute("INSERT INTO products_catalog(nm_id,raw_json) VALUES (10,?)",(json.dumps({"sizes":[{"chrtID":100},{"chrtID":101}]}),))
  c.execute("INSERT INTO execution_tasks(task_key,task_type,task_date,nm_id,chrt_id,planned_units,actual_units,status,updated_at) VALUES ('t','production','2026-09-01',10,100,5,0,'Не начато','original')")
 app=FastAPI();app.include_router(router)
 with TestClient(app) as client:yield client

def setup(client):
 assert client.post(P+"/centers",json=dict(name="Цех",daily_minutes=30,workdays=[0,1,2,3,4],revision=0)).status_code==200
 assert client.post(P+"/norms",json=dict(nm_id=10,chrt_id=100,operation="Пошив",minutes_per_unit=10,revision=0)).status_code==200

def payload():
 return dict(start="2026-09-01",days=7,change=dict(task_key="t",operation="Пошив",center_id=1,scheduled_date="2026-09-02",revision=0))

def test_preview_confirm_history_conservation(client):
 setup(client);p=payload();r=client.post(P+"/preview",json=p);assert r.status_code==200,r.text
 row=next(x for x in r.json()["calendar"] if x["date"]=="2026-09-02");assert row["planned_minutes"]==50 and row["overload_minutes"]==20
 assert client.get(P).json()["assignments"]==[]
 body=dict(**p,preview_fingerprint=r.json()["preview_fingerprint"],request_id=str(uuid4()))
 assert client.post(P+"/confirm",json=body).status_code==200
 assert client.post(P+"/confirm",json=body).json()["replayed"] is True
 with core.connect() as c:
  t=c.execute("SELECT task_date,actual_units,updated_at FROM execution_tasks WHERE task_key='t'").fetchone();assert tuple(t)==('2026-09-01',0,'original')
  assert c.execute("SELECT COUNT(*) FROM production_workcenter_history").fetchone()[0]==1
 assert client.get(P+"?start=2026-09-01&days=7").json()["history"][0]["task_key"]=="t"

def test_nonworkday_missing_exact_variant_norm(client):
 setup(client);p=payload();p["change"].update(operation="Раскрой",scheduled_date="2026-09-05")
 r=client.post(P+"/preview",json=p).json();row=next(x for x in r["calendar"] if x["date"]=='2026-09-05')
 assert row["available_minutes"]==0 and row["load_complete"] is False and row["tasks"][0]["planned_minutes"] is None
 assert any(x["code"]=="missing_norm" for x in r["warnings"])

def test_stale_capacity_and_started_task_block_confirm(client):
 setup(client);p=payload();r=client.post(P+"/preview",json=p).json()
 client.post(P+"/centers",json=dict(id=1,name="Цех",daily_minutes=60,workdays=[0,1,2,3,4],revision=1))
 body=dict(**p,preview_fingerprint=r["preview_fingerprint"],request_id=str(uuid4()))
 assert client.post(P+"/confirm",json=body).status_code==409
 with core.connect() as c:c.execute("UPDATE execution_tasks SET actual_units=1 WHERE task_key='t'")
 assert client.post(P+"/preview",json=p).status_code==422
 assert client.get(P).json()["assignments"]==[]

@pytest.mark.parametrize("role",['viewer','cutter','seamstress','warehouse','technologist','integration_reader'])
def test_schedule_roles(client,role):
 setup(client);p=payload();r=client.post(P+"/preview",json=p)
 assert client.post(P+"/confirm",headers={"x-tenant-role":role},json=dict(**p,preview_fingerprint=r.json()["preview_fingerprint"],request_id=str(uuid4()))).status_code==403

def test_norm_variant_and_revision_guards(client):
 setup(client)
 assert client.post(P+"/norms",json=dict(nm_id=10,chrt_id=999,operation="Пошив",minutes_per_unit=10,revision=0)).status_code==422
 assert client.post(P+"/norms",json=dict(nm_id=10,chrt_id=100,operation="Пошив",minutes_per_unit=20,revision=0)).status_code==409
 assert client.post(P+"/centers",json=dict(name="Bad",daily_minutes=-1,workdays=[0],revision=0)).status_code==422
 assert client.post(P+"/centers",json=dict(name="Bad",daily_minutes=10,workdays=[0,0],revision=0)).status_code==422
 assert client.get(P).json()["centers"][0]["revision"]==1


def test_reschedule_history_and_conflicting_retry(client):
 setup(client);p=payload();r=client.post(P+"/preview",json=p).json();body=dict(**p,preview_fingerprint=r["preview_fingerprint"],request_id=str(uuid4()))
 assert client.post(P+"/confirm",json=body).status_code==200
 next_plan=payload();next_plan["change"].update(revision=1,scheduled_date='2026-09-03')
 r=client.post(P+"/preview",json=next_plan).json();second=dict(**next_plan,preview_fingerprint=r["preview_fingerprint"],request_id=str(uuid4()))
 assert client.post(P+"/confirm",json=second).status_code==200
 second['request_id']=body['request_id'];assert client.post(P+"/confirm",json=second).status_code==409
 view=client.get(P+"?start=2026-09-01&days=7").json()
 assert view["assignments"][0]["revision"]==2 and len(view["history"])==2
 assert json.loads(view["history"][0]["before_json"])["scheduled_date"]=='2026-09-02'
 with core.connect() as c:assert c.execute("SELECT task_date FROM execution_tasks").fetchone()[0]=='2026-09-01'

def test_existing_inventory_fact_blocks_date_change_even_when_task_fact_is_zero(client):
 setup(client)
 with core.connect() as c:c.execute("INSERT INTO inventory_movements(event_key,movement_type,source_task_key,created_at) VALUES ('fact','production_receipt','t|chrt:100','2026-09-01')")
 assert client.post(P+"/preview",json=payload()).status_code==422
 assert client.get(P).json()["assignments"]==[]

def test_demo_and_configuration_roles_are_enforced(client,monkeypatch):
 request=dict(name='Other',daily_minutes=60,workdays=[0],revision=0)
 assert client.post(P+'/centers',json=request,headers={'x-tenant-role':'technologist'}).status_code==200
 assert client.post(P+'/centers',json=dict(request,name='Wrong'),headers={'x-tenant-role':'warehouse'}).status_code==403
 monkeypatch.setattr(api.config,'IS_DEMO',True)
 assert client.post(P+'/centers',json=dict(request,name='Demo')).status_code==403
 assert client.get(P).json()['can_schedule'] is False

def test_multiple_operations_sum_loading_without_zeroing_missing_norm(client):
 setup(client);p=payload();r=client.post(P+'/preview',json=p).json()
 client.post(P+'/confirm',json=dict(**p,preview_fingerprint=r['preview_fingerprint'],request_id=str(uuid4())))
 p['change']['operation']='Раскрой';r=client.post(P+'/preview',json=p).json()
 row=next(x for x in r['calendar'] if x['date']=='2026-09-02')
 assert row['planned_minutes']==50 and row['load_complete'] is False and row['overload_minutes']==20
 assert len(row['tasks'])==2


@pytest.mark.parametrize('status',['В работе','Выполнено','Отменено'])
def test_closed_and_inprogress_tasks_refuse_assignment(client,status):
 setup(client)
 with core.connect() as c:c.execute("UPDATE execution_tasks SET status=? WHERE task_key='t'",(status,))
 assert client.post(P+'/preview',json=payload()).status_code==422

def test_calendar_uses_remaining_units_and_exact_variant_norm(client):
 setup(client);p=payload();r=client.post(P+'/preview',json=p).json()
 client.post(P+'/confirm',json=dict(**p,preview_fingerprint=r['preview_fingerprint'],request_id=str(uuid4())))
 client.post(P+'/norms',json=dict(nm_id=10,chrt_id=101,operation='Пошив',minutes_per_unit=999,revision=0))
 with core.connect() as c:c.execute("UPDATE execution_tasks SET actual_units=2,status='В работе' WHERE task_key='t'")
 view=client.get(P+'?start=2026-09-01&days=7').json();row=next(x for x in view['calendar'] if x['date']=='2026-09-02')
 assert row['planned_minutes']==30 and row['overload_minutes']==0

def test_minute_bounds_and_boolean_rejected(client):
 for value in [True,0,144001]:
  assert client.post(P+'/centers',json=dict(name='Bad',daily_minutes=value,workdays=[0],revision=0)).status_code==422

@pytest.mark.parametrize('role',['integration_reader','unknown'])
def test_restricted_identity_cannot_read_or_preview_calendar(client,role):
 setup(client);headers={'x-tenant-role':role}
 assert client.get(P,headers=headers).status_code==403
 assert client.post(P+'/preview',headers=headers,json=payload()).status_code==403


def test_verified_actor_utc_and_append_only_history(client):
 import sqlite3
 from datetime import datetime,timedelta
 setup(client);p=payload();r=client.post(P+'/preview',json=p).json()
 assert client.post(P+'/confirm',json=dict(**p,preview_fingerprint=r['preview_fingerprint'],request_id=str(uuid4())),headers={'x-tenant-account':'owner@example.test'}).status_code==200
 history=client.get(P).json()['history'][0]
 assert history['actor']=='owner@example.test' and datetime.fromisoformat(history['created_at']).utcoffset()==timedelta(0)
 with core.connect() as c:
  for statement in ['UPDATE production_workcenter_history SET actor_role=\'viewer\'','DELETE FROM production_workcenter_history']:
   with pytest.raises(sqlite3.IntegrityError,match='immutable'):c.execute(statement)
  assert c.execute('SELECT COUNT(*) FROM production_workcenter_history').fetchone()[0]==1

def test_history_migration_preserves_legacy_records_without_fabricating_actor():
 import sqlite3
 from db.workcenters import init_workcenter_schema
 c=sqlite3.connect(':memory:')
 c.execute("CREATE TABLE production_workcenter_history(id INTEGER PRIMARY KEY,request_id TEXT NOT NULL UNIQUE,request_hash TEXT NOT NULL,task_key TEXT NOT NULL,operation TEXT NOT NULL,before_json TEXT NOT NULL,after_json TEXT NOT NULL,actor_role TEXT NOT NULL,created_at TEXT NOT NULL)")
 c.execute("INSERT INTO production_workcenter_history VALUES (1,'legacy','hash','old','Sew','null','{}','manager','2026-09-01T12:00:00')")
 init_workcenter_schema(c);init_workcenter_schema(c)
 assert c.execute('SELECT request_id,actor_role,created_at,actor FROM production_workcenter_history').fetchone()==('legacy','manager','2026-09-01T12:00:00',None)
 with pytest.raises(sqlite3.IntegrityError,match='immutable'):c.execute('DELETE FROM production_workcenter_history')
 c.close()


@pytest.mark.parametrize("reuse", ["id", "request_id"])
def test_history_cannot_be_replaced_even_with_recursive_triggers_disabled(client,reuse):
 import sqlite3
 setup(client);p=payload();r=client.post(P+'/preview',json=p).json()
 client.post(P+'/confirm',json=dict(**p,preview_fingerprint=r['preview_fingerprint'],request_id=str(uuid4())))
 with core.connect() as c:
  c.execute('PRAGMA recursive_triggers=OFF')
  before=dict(c.execute('SELECT * FROM production_workcenter_history').fetchone())
  changed=dict(before,actor='forged')
  if reuse=='id':changed['request_id']=str(uuid4())
  else:changed['id']+=100
  columns=list(changed)
  with pytest.raises(sqlite3.IntegrityError,match='immutable'):
   c.execute('INSERT OR REPLACE INTO production_workcenter_history ('+','.join(columns)+') VALUES ('+','.join('?' for _ in columns)+')',[changed[k] for k in columns])
  assert dict(c.execute('SELECT * FROM production_workcenter_history').fetchone())==before
