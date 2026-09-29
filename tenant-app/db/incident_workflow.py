"""Local evidence and quarantine; no marketplace writes or financial posting."""
from datetime import date, datetime, timezone
import hashlib
import json
import re
import sqlite3
from .core import connect

MAX_EVIDENCE = 2 * 1024 * 1024
LOCATIONS = {'ready':'ready_units','inbound':'inbound_units','wb':'wb_units'}

def ensure_schema(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS incident_workflow (case_id INTEGER PRIMARY KEY REFERENCES wb_incident_cases(id), assignee TEXT NOT NULL DEFAULT "", deadline TEXT, outcome TEXT NOT NULL DEFAULT "")')
    conn.execute('CREATE TABLE IF NOT EXISTS incident_workflow_history (id INTEGER PRIMARY KEY, case_id INTEGER NOT NULL REFERENCES wb_incident_cases(id), action TEXT NOT NULL, payload_json TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS incident_evidence (id INTEGER PRIMARY KEY, case_id INTEGER NOT NULL REFERENCES wb_incident_cases(id), filename TEXT NOT NULL, media_type TEXT NOT NULL, sha256 TEXT NOT NULL, content BLOB NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(case_id,sha256))')
    conn.execute('CREATE TABLE IF NOT EXISTS incident_holds (id INTEGER PRIMARY KEY, case_id INTEGER NOT NULL REFERENCES wb_incident_cases(id), layer_id INTEGER NOT NULL REFERENCES finished_goods_cost_layers(id), location TEXT NOT NULL, units INTEGER NOT NULL CHECK(units>0), request_key TEXT NOT NULL UNIQUE, status TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL, released_at TEXT)')
    conn.execute('CREATE INDEX IF NOT EXISTS incident_holds_layer_status ON incident_holds(layer_id,location,status)')
    conn.execute('CREATE INDEX IF NOT EXISTS incident_history_case ON incident_workflow_history(case_id,id)')
    for loc,col in LOCATIONS.items():
        conn.execute(f'''CREATE TRIGGER IF NOT EXISTS incident_hold_guard_{loc} BEFORE UPDATE OF {col},status ON finished_goods_cost_layers WHEN
          (NEW.{col} < (SELECT COALESCE(SUM(units),0) FROM incident_holds WHERE layer_id=OLD.id AND location='{loc}' AND status='held'))
          OR (NEW.status='reversed' AND EXISTS(SELECT 1 FROM incident_holds WHERE layer_id=OLD.id AND status='held'))
          BEGIN SELECT RAISE(ABORT,'QUARANTINE: release incident hold before stock movement'); END''')
    conn.execute("CREATE TRIGGER IF NOT EXISTS incident_hold_delete_guard BEFORE DELETE ON finished_goods_cost_layers WHEN EXISTS(SELECT 1 FROM incident_holds WHERE layer_id=OLD.id AND status='held') BEGIN SELECT RAISE(ABORT,'QUARANTINE: held cost layer cannot be deleted'); END")
    for table in ('incident_workflow_history','incident_evidence'):
        for action in ('UPDATE','DELETE'):
            conn.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'Incident journal is append-only'); END")

def now(): return datetime.now(timezone.utc).isoformat()
def checked_case(conn,case_id):
    row=conn.execute('SELECT * FROM wb_incident_cases WHERE id=?',(case_id,)).fetchone()
    if not row: raise ValueError('Инцидент не найден.')
    return row

def journal(conn,case_id,action,payload,actor):
    if not str(actor).strip() or len(actor)>200: raise ValueError('Не указан автор операции.')
    conn.execute('INSERT INTO incident_workflow_history(case_id,action,payload_json,actor,created_at) VALUES(?,?,?,?,?)',(case_id,action,json.dumps(payload,ensure_ascii=False,sort_keys=True),actor,now()))

def save_details(case_id,assignee,deadline,outcome,actor,status=None):
    if len(assignee)>200 or len(outcome)>4000: raise ValueError('Слишком длинный текст.')
    if deadline: date.fromisoformat(deadline)
    after={'assignee':assignee.strip(),'deadline':deadline or None,'outcome':outcome.strip()}
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE');ensure_schema(conn);case=checked_case(conn,case_id)
        if status is not None and status not in {'open','confirmed','closed'}:raise ValueError('Недопустимый статус.')
        if status=='closed' and (not outcome.strip() or conn.execute("SELECT 1 FROM incident_holds WHERE case_id=? AND status='held'",(case_id,)).fetchone()):raise ValueError('Для закрытия укажите решение и снимите карантин.')
        row=conn.execute('SELECT assignee,deadline,outcome FROM incident_workflow WHERE case_id=?',(case_id,)).fetchone()
        before=dict(row) if row else {'assignee':'','deadline':None,'outcome':''}
        if before==after and (status is None or status==case['status']):return
        conn.execute('INSERT INTO incident_workflow VALUES(?,?,?,?) ON CONFLICT(case_id) DO UPDATE SET assignee=excluded.assignee,deadline=excluded.deadline,outcome=excluded.outcome',(case_id,after['assignee'],after['deadline'],after['outcome']))
        old_status=case['status']
        if status is not None:conn.execute('UPDATE wb_incident_cases SET status=?,updated_at=? WHERE id=?',(status,now(),case_id))
        journal(conn,case_id,'details',{'before':{**before,'status':old_status},'after':{**after,'status':status or old_status}},actor)

def attach(case_id,filename,media_type,content,actor):
    # Content is never rendered inline, parsed as HTML, fetched or written to disk.
    if not content or len(content)>MAX_EVIDENCE:raise ValueError('Размер вложения: от 1 байта до 2 МБ.')
    if not filename or len(filename)>180 or re.search(r'[\\/\x00-\x1f\x7f]',filename):raise ValueError('Некорректное имя файла.')
    ext=filename.rsplit('.',1)[-1].lower()
    signatures={'application/pdf':('pdf',content.startswith(b'%PDF-')),'image/png':('png',content.startswith(b'\x89PNG\r\n\x1a\n')),'image/jpeg':('jpg',content.startswith(b'\xff\xd8\xff')),'text/plain':('txt',True)}
    if media_type not in signatures:raise ValueError('Разрешены PDF, PNG, JPG и TXT.')
    expected,valid=signatures[media_type]
    if (ext!=expected and not (expected=='jpg' and ext=='jpeg')) or not valid:raise ValueError('Тип файла не соответствует содержимому.')
    if media_type=='text/plain':
        try:content.decode('utf-8')
        except UnicodeDecodeError:raise ValueError('TXT должен быть в UTF-8.') from None
        if b'\0' in content:raise ValueError('Некорректный TXT.')
    digest=hashlib.sha256(content).hexdigest()
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE');ensure_schema(conn);checked_case(conn,case_id)
        old=conn.execute('SELECT id FROM incident_evidence WHERE case_id=? AND sha256=?',(case_id,digest)).fetchone()
        if old:return int(old['id'])
        count,total=conn.execute('SELECT COUNT(*),COALESCE(SUM(length(content)),0) FROM incident_evidence WHERE case_id=?',(case_id,)).fetchone()
        if count>=20 or total+len(content)>20*1024*1024:raise ValueError('Лимит карточки: 20 файлов / 20 МБ.')
        cur=conn.execute('INSERT INTO incident_evidence(case_id,filename,media_type,sha256,content,actor,created_at) VALUES(?,?,?,?,?,?,?)',(case_id,filename,media_type,digest,content,actor,now()))
        identifier=int(cur.lastrowid);journal(conn,case_id,'evidence',{'id':identifier,'sha256':digest,'filename':filename},actor)
        return identifier

def hold(case_id,layer_id,location,units,request_key,actor):
    if location not in LOCATIONS or type(units) is not int or units<=0 or units>10000000:raise ValueError('Проверьте место и количество.')
    if not re.fullmatch(r'[A-Za-z0-9_-]{8,100}',request_key):raise ValueError('Некорректный ключ запроса.')
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE');ensure_schema(conn)
        old=conn.execute('SELECT * FROM incident_holds WHERE request_key=?',(request_key,)).fetchone()
        if old:
            if (old['case_id'],old['layer_id'],old['location'],old['units'])!=(case_id,layer_id,location,units):raise ValueError('Ключ запроса уже использован для другой операции.')
            return int(old['id'])
        case=checked_case(conn,case_id)
        if conn.execute("SELECT COUNT(*) FROM incident_holds WHERE case_id=? AND status='held'",(case_id,)).fetchone()[0]>=100:raise ValueError('Лимит карточки: 100 активных резервов.')
        if case['status']=='closed':raise ValueError('Закрытый инцидент нельзя помещать в карантин.')
        layer=conn.execute('SELECT * FROM finished_goods_cost_layers WHERE id=?',(layer_id,)).fetchone()
        if not layer or layer['status']=='reversed':raise ValueError('FIFO-слой не найден.')
        reserved=held_units(conn,layer_id,location)
        if int(layer[LOCATIONS[location]])-reserved<units:raise ValueError('Недостаточно свободных единиц в выбранном FIFO-слое.')
        cur=conn.execute("INSERT INTO incident_holds(case_id,layer_id,location,units,request_key,status,actor,created_at) VALUES(?,?,?,?,?,'held',?,?)",(case_id,layer_id,location,units,request_key,actor,now()))
        identifier=int(cur.lastrowid);journal(conn,case_id,'quarantine',{'hold_id':identifier,'layer_id':layer_id,'location':location,'units':units},actor)
        return identifier

def held_units(conn,layer_id,location):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='incident_holds'").fetchone():return 0
    return int(conn.execute("SELECT COALESCE(SUM(units),0) FROM incident_holds WHERE layer_id=? AND location=? AND status='held'",(layer_id,location)).fetchone()[0])

def release(case_id,hold_id,reason,actor):
    if not reason.strip() or len(reason)>2000:raise ValueError('Укажите основание снятия карантина.')
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE');ensure_schema(conn);checked_case(conn,case_id)
        row=conn.execute('SELECT * FROM incident_holds WHERE id=? AND case_id=?',(hold_id,case_id)).fetchone()
        if not row:raise ValueError('Резерв не найден.')
        if row['status']=='released':return
        conn.execute("UPDATE incident_holds SET status='released',released_at=? WHERE id=?",(now(),hold_id))
        journal(conn,case_id,'release',{'hold_id':hold_id,'reason':reason.strip()},actor)

def workspace(case_id):
    with connect() as conn:
        ensure_schema(conn);case=checked_case(conn,case_id)
        details=conn.execute('SELECT * FROM incident_workflow WHERE case_id=?',(case_id,)).fetchone()
        def rows(sql,args=()):return [dict(r) for r in conn.execute(sql,args)]
        layers=rows("SELECT id,nm_id,chrt_id,source_date,ready_units,inbound_units,wb_units FROM finished_goods_cost_layers WHERE status<>'reversed' AND ready_units+inbound_units+wb_units>0 ORDER BY id DESC LIMIT 500")
        for layer in layers:
            for location,column in LOCATIONS.items():
                layer['held_'+location+'_units']=held_units(conn,layer['id'],location)
                layer['available_'+location+'_units']=max(0,layer[column]-layer['held_'+location+'_units'])
        return {'details':{**(dict(details) if details else {'assignee':'','deadline':None,'outcome':''}),'status':case['status']},'history':rows('SELECT * FROM (SELECT * FROM incident_workflow_history WHERE case_id=? ORDER BY id DESC LIMIT 500) ORDER BY id',(case_id,)),'history_count':conn.execute('SELECT COUNT(*) FROM incident_workflow_history WHERE case_id=?',(case_id,)).fetchone()[0],'evidence':rows('SELECT id,filename,media_type,sha256,length(content) AS size,actor,created_at FROM incident_evidence WHERE case_id=? ORDER BY id',(case_id,)),'holds':rows('SELECT h.*,l.nm_id,l.chrt_id FROM incident_holds h JOIN finished_goods_cost_layers l ON l.id=h.layer_id WHERE h.case_id=? ORDER BY (h.status="held") DESC,h.id DESC LIMIT 500',(case_id,)),'layers':layers}

def evidence(case_id,evidence_id):
    with connect() as conn:
        ensure_schema(conn)
        row=conn.execute('SELECT * FROM incident_evidence WHERE id=? AND case_id=?',(evidence_id,case_id)).fetchone()
        if not row:raise ValueError('Вложение не найдено.')
        data=bytes(row['content'])
        if hashlib.sha256(data).hexdigest()!=row['sha256']:raise ValueError('Проверка целостности вложения не прошла.')
        return dict(row)
