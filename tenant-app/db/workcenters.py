"""Minute-based shop-floor planning, separate from immutable production facts."""
from datetime import date,timedelta,datetime,timezone
import hashlib,json,math

def canonical(value):
 return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"))
def fingerprint(value):
 return hashlib.sha256(canonical(value).encode()).hexdigest()
def init_workcenter_schema(conn):
 schema="""
 CREATE TABLE IF NOT EXISTS production_workcenters(id INTEGER PRIMARY KEY,name TEXT NOT NULL UNIQUE,daily_minutes REAL NOT NULL,workdays_json TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1);
 CREATE TABLE IF NOT EXISTS production_operation_norms(id INTEGER PRIMARY KEY,nm_id INTEGER NOT NULL,chrt_id INTEGER NOT NULL,operation TEXT NOT NULL,minutes_per_unit REAL NOT NULL,revision INTEGER NOT NULL DEFAULT 1,UNIQUE(nm_id,chrt_id,operation));
 CREATE TABLE IF NOT EXISTS production_workcenter_assignments(task_key TEXT NOT NULL,operation TEXT NOT NULL,center_id INTEGER NOT NULL,scheduled_date TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1,PRIMARY KEY(task_key,operation),FOREIGN KEY(center_id) REFERENCES production_workcenters(id));
 CREATE TABLE IF NOT EXISTS production_workcenter_history(id INTEGER PRIMARY KEY,request_id TEXT NOT NULL UNIQUE,request_hash TEXT NOT NULL,task_key TEXT NOT NULL,operation TEXT NOT NULL,before_json TEXT NOT NULL,after_json TEXT NOT NULL,actor_role TEXT NOT NULL,created_at TEXT NOT NULL);
 """
 for statement in schema.split(";"):
  if statement.strip():conn.execute(statement)
 columns={r[1] for r in conn.execute("PRAGMA table_info(production_workcenter_history)")}
 if "actor" not in columns:conn.execute("ALTER TABLE production_workcenter_history ADD COLUMN actor TEXT")
 for operation in ("UPDATE","DELETE"):
  conn.execute(f"CREATE TRIGGER IF NOT EXISTS workcenter_history_{operation.lower()}_immutable BEFORE {operation} ON production_workcenter_history BEGIN SELECT RAISE(ABORT, 'Workcenter history is immutable'); END")

def positive(value,limit):
 if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0<value<=limit: raise ValueError("Укажите конечное положительное число в допустимом диапазоне")
 return value

def save_center(conn,data):
 positive(data["daily_minutes"],144000)
 days=data["workdays"]
 if not days or len(set(days))!=len(days) or any(type(d)!=int or not 0<=d<=6 for d in days): raise ValueError("Некорректные рабочие дни")
 name=str(data["name"]).strip()
 if not 1<=len(name)<=100: raise ValueError("Укажите название центра")
 current=conn.execute("SELECT * FROM production_workcenters WHERE id=?",(data.get("id"),)).fetchone()
 if data.get("id") is not None and current is None: raise ValueError("Центр не найден")
 if (current["revision"] if current else 0)!=data["revision"]: raise ValueError("stale_revision")
 if current:
  conn.execute("UPDATE production_workcenters SET name=?,daily_minutes=?,workdays_json=?,revision=revision+1 WHERE id=?",(name,data["daily_minutes"],canonical(days),current["id"]))
  return current["id"]
 if conn.execute("SELECT COUNT(*) FROM production_workcenters").fetchone()[0]>=100: raise ValueError("Максимум 100 центров")
 return conn.execute("INSERT INTO production_workcenters(name,daily_minutes,workdays_json) VALUES (?,?,?)",(name,data["daily_minutes"],canonical(days))).lastrowid

def save_norm(conn,data):
 from .wip import validate_catalog_identity
 validate_catalog_identity(conn,data["nm_id"],data["chrt_id"],require_catalog=True)
 positive(data["minutes_per_unit"],1440)
 operation=str(data["operation"]).strip()
 if not 1<=len(operation)<=100: raise ValueError("Укажите операцию")
 current=conn.execute("SELECT * FROM production_operation_norms WHERE nm_id=? AND chrt_id=? AND operation=?",(data["nm_id"],data["chrt_id"],operation)).fetchone()
 if (current["revision"] if current else 0)!=data["revision"]: raise ValueError("stale_revision")
 if current: conn.execute("UPDATE production_operation_norms SET minutes_per_unit=?,revision=revision+1 WHERE id=?",(data["minutes_per_unit"],current["id"]))
 else:
  if conn.execute("SELECT COUNT(*) FROM production_operation_norms").fetchone()[0]>=5000: raise ValueError("Максимум 5000 норм")
  conn.execute("INSERT INTO production_operation_norms(nm_id,chrt_id,operation,minutes_per_unit) VALUES (?,?,?,?)",(data["nm_id"],data["chrt_id"],operation,data["minutes_per_unit"]))

def snapshot(conn,start,days):
 start=date.fromisoformat(start)
 if type(days)!=int or not 1<=days<=90: raise ValueError("Период от 1 до 90 дней")
 tasks=[dict(r) for r in conn.execute("SELECT * FROM execution_tasks WHERE LOWER(TRIM(task_type))='production' OR TRIM(task_type) IN ('Производство','производство') ORDER BY task_key LIMIT 2001")]
 if len(tasks)>2000: raise ValueError("Разбейте план: максимум 2000 производственных заданий")
 assignments=[dict(r) for r in conn.execute("SELECT * FROM production_workcenter_assignments ORDER BY task_key,operation LIMIT 10001")]
 if len(assignments)>10000:raise ValueError("Максимум 10000 назначений; завершите старые задания")
 return dict(start=start.isoformat(),days=days,centers=[dict(r) for r in conn.execute("SELECT * FROM production_workcenters ORDER BY id")],norms=[dict(r) for r in conn.execute("SELECT * FROM production_operation_norms ORDER BY id")],assignments=assignments,tasks=tasks)

def loading(source):
 centers={c["id"]:c for c in source["centers"]};tasks={t["task_key"]:t for t in source["tasks"]}
 norms={(n["nm_id"],n["chrt_id"],n["operation"]):n for n in source["norms"]}
 rows=[];warnings=[];assigned=set()
 for offset in range(source["days"]):
  day=date.fromisoformat(source["start"])+timedelta(days=offset)
  for center in centers.values():
   rows.append(dict(date=day.isoformat(),center_id=center["id"],center_name=center["name"],available_minutes=center["daily_minutes"] if day.weekday() in json.loads(center["workdays_json"]) else 0,planned_minutes=0,missing_norm=False,tasks=[]))
 index={(r["date"],r["center_id"]):r for r in rows}
 for assignment in source["assignments"]:
  task=tasks.get(assignment["task_key"])
  if not task: warnings.append(dict(code="missing_task",task_key=assignment["task_key"]));continue
  assigned.add(task["task_key"])
  remaining=max(0,int(task["planned_units"] or 0)-int(task["actual_units"] or 0))
  if remaining==0 or task["status"] in {"Завершено","Отменено","Выполнено"}:continue
  row=index.get((assignment["scheduled_date"],assignment["center_id"]))
  if row is None:continue
  norm=norms.get((task["nm_id"],int(task["chrt_id"] or 0),assignment["operation"]))
  minutes=remaining*norm["minutes_per_unit"] if norm else None
  row["tasks"].append(dict(**assignment,nm_id=task["nm_id"],chrt_id=task["chrt_id"],remaining_units=remaining,planned_minutes=minutes))
  if minutes is None: row["missing_norm"]=True;warnings.append(dict(code="missing_norm",task_key=task["task_key"],operation=assignment["operation"]))
  else: row["planned_minutes"]+=minutes
 for row in rows:
  row["planned_minutes"]=round(row["planned_minutes"],4)
  row["overload_minutes"]=round(max(0,row["planned_minutes"]-row["available_minutes"]),4)
  row["load_complete"]=not row["missing_norm"]
 for task in tasks.values():
  assigned_operations={a["operation"] for a in source["assignments"] if a["task_key"]==task["task_key"]}
  if int(task["planned_units"] or 0)>int(task["actual_units"] or 0) and task["status"] not in {"Завершено","Отменено","Выполнено"}:
   for key in norms:
    if key[:2]==(task["nm_id"],int(task["chrt_id"] or 0)) and key[2] not in assigned_operations:warnings.append(dict(code="unassigned_operation",task_key=task["task_key"],operation=key[2]))
  if task["task_key"] not in assigned and int(task["planned_units"] or 0)>int(task["actual_units"] or 0) and task["status"] not in {"Завершено","Отменено","Выполнено"}:warnings.append(dict(code="unassigned_task",task_key=task["task_key"]))
 return dict(calendar=rows,warnings=warnings)

def preview(conn,source,change):
 from copy import deepcopy
 current=next((a for a in source["assignments"] if a["task_key"]==change["task_key"] and a["operation"]==change["operation"]),None)
 if (current["revision"] if current else 0)!=change["revision"]:raise ValueError("stale_revision")
 if change["center_id"] not in {c["id"] for c in source["centers"]}:raise ValueError("Центр не найден")
 task=next((t for t in source["tasks"] if t["task_key"]==change["task_key"]),None)
 if not task:raise ValueError("Производственное задание не найдено")
 if int(task["actual_units"] or 0)>0 or task["status"] not in {"Не начато","Запланировано",""}:raise ValueError("Нельзя менять задания с фактом, начатые или закрытые задания")
 if conn.execute("SELECT 1 FROM inventory_movements WHERE (source_task_key=? OR substr(source_task_key,1,length(?)+6)=?||'|chrt:') AND reversed_at IS NULL LIMIT 1",(task["task_key"],task["task_key"],task["task_key"],)).fetchone():raise ValueError("У задания есть проведённый факт")
 due=date.fromisoformat(change["scheduled_date"])
 if not date.fromisoformat(source["start"])<=due<date.fromisoformat(source["start"])+timedelta(days=source["days"]):raise ValueError("Дата вне выбранного периода")
 result=deepcopy(source);result["assignments"]=[a for a in result["assignments"] if not(a["task_key"]==change["task_key"] and a["operation"]==change["operation"])];result["assignments"].append(dict(change))
 return dict(preview_fingerprint=fingerprint(dict(source=source,change=change)),before=current,after=change,**loading(result))

def confirm(conn,start,days,change,token,request_id,role,actor=None):
 digest=fingerprint(dict(start=start,days=days,change=change,token=token))
 old=conn.execute("SELECT request_hash,after_json FROM production_workcenter_history WHERE request_id=?",(request_id,)).fetchone()
 if old:
  if old["request_hash"]!=digest:raise ValueError("idempotency_conflict")
  return dict(replayed=True,assignment=json.loads(old["after_json"]))
 source=snapshot(conn,start,days);result=preview(conn,source,change)
 if result["preview_fingerprint"]!=token:raise ValueError("stale_preview")
 after=dict(change,revision=change["revision"]+1)
 conn.execute("INSERT INTO production_workcenter_assignments(task_key,operation,center_id,scheduled_date,revision) VALUES (?,?,?,?,?) ON CONFLICT(task_key,operation) DO UPDATE SET center_id=excluded.center_id,scheduled_date=excluded.scheduled_date,revision=excluded.revision",(after["task_key"],after["operation"],after["center_id"],after["scheduled_date"],after["revision"]))
 conn.execute("INSERT INTO production_workcenter_history(request_id,request_hash,task_key,operation,before_json,after_json,actor_role,created_at,actor) VALUES (?,?,?,?,?,?,?,?,?)",(request_id,digest,after["task_key"],after["operation"],canonical(result["before"]),canonical(after),role,datetime.now(timezone.utc).isoformat(),str(actor or f"role:{role}").strip()))
 return dict(replayed=False,assignment=after)
