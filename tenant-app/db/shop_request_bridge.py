"""One main SQLite transaction for isolated snapshot + canonical warehouse facts.

Only the trusted adapter builds operations; the worker cannot supply arbitrary
SQL or select another shop. This module has no marketplace/client imports.
"""
from __future__ import annotations
import hashlib
import json
import uuid
from .core import connect
from . import shared_warehouse as warehouse, shop_workspaces as shops

OPERATIONS={name:getattr(warehouse,name) for name in (
    'reserve','transition','sell','return_sale','quarantine_owned','release_owned_hold')}

def initialize():
    shops.initialize()
    with connect() as conn:
        warehouse.initialize(conn)
        conn.execute('''CREATE TABLE IF NOT EXISTS shop_request_receipts(
          request_key TEXT PRIMARY KEY,shop_id INTEGER NOT NULL,identity TEXT NOT NULL,
          base_revision INTEGER NOT NULL,applied_revision INTEGER NOT NULL,
          fingerprint TEXT NOT NULL,result_json TEXT NOT NULL,created_at TEXT NOT NULL,
          actor TEXT NOT NULL)''')
        for operation in ('UPDATE','DELETE'):
            conn.execute(f'''CREATE TRIGGER IF NOT EXISTS shop_request_receipts_no_{operation.lower()}
              BEFORE {operation} ON shop_request_receipts BEGIN
              SELECT RAISE(ABORT,'SHARED_WAREHOUSE: request receipt is immutable'); END''')
        conn.execute('''CREATE TRIGGER IF NOT EXISTS shop_request_receipts_no_replace
          BEFORE INSERT ON shop_request_receipts WHEN EXISTS(
            SELECT 1 FROM shop_request_receipts WHERE request_key=NEW.request_key)
          BEGIN SELECT RAISE(ABORT,'SHARED_WAREHOUSE: request receipt is immutable'); END''')

def _key(value):
    if not isinstance(value,str):raise warehouse.WarehouseConflict('Нужен UUID запроса')
    try:parsed=uuid.UUID(value)
    except ValueError as exc:raise warehouse.WarehouseConflict('Нужен UUID запроса') from exc
    if str(parsed)!=value.lower():raise warehouse.WarehouseConflict('Нужен канонический UUID запроса')
    return str(parsed)

def _actor(account,role):
    # Forwarded identity is established by the gateway, never by worker output.
    account=warehouse.text(account,200)
    if role not in {'owner','manager','viewer','technologist','cutter','seamstress','warehouse'}:
        raise warehouse.WarehouseScopeError('Нет подтверждённой роли магазина')
    return account

def _scope(conn,shop_id,identity,account,role,write=True):
    account=_actor(account,role)
    record=conn.execute('SELECT id,account_identity,revision,active FROM shop_workspaces WHERE id=? AND active=1',(shop_id,)).fetchone()
    if not record or record['account_identity']!=identity:
        raise warehouse.WarehouseScopeError('Магазин или продавец недоступен')
    scope=warehouse.access(conn,shop_id,account,role,write)
    if scope['identity']!=identity:raise warehouse.WarehouseConflict('Подтверждённый продавец изменился')
    return record

def fingerprint(shop_id,identity,base_revision,request):
    if type(base_revision)!=int or base_revision<0:raise warehouse.WarehouseConflict('Нужна версия магазина')
    payload=warehouse.encoded(dict(shop_id=shop_id,identity=identity,base_revision=base_revision,request=request))
    if len(payload.encode())>1024*1024:raise warehouse.WarehouseConflict('Запрос слишком большой')
    # encoded rejects NaN explicitly below; bool/decimal revisions rejected above.
    json.dumps(request,allow_nan=False)
    return hashlib.sha256(payload.encode()).hexdigest()

def _result(conn,row,record):
    result=json.loads(row['result_json'])
    return {'result':result,'bridge':{'applied_revision':row['applied_revision'],
      'current_revision':record['revision'],'shop_id':row['shop_id'],
      'identity':row['identity'],'current_fingerprint':warehouse.digest({'identity':record['account_identity'],'revision':record['revision']})}}

def replay(shop_id,identity,base_revision,request_key,request,account,role):
    """Recover lost success without executing a child or physical action twice."""
    key=_key(request_key);expected=fingerprint(shop_id,identity,base_revision,request)
    initialize()
    with connect() as conn:
        conn.execute('BEGIN')
        record=_scope(conn,shop_id,identity,account,role)
        row=conn.execute('SELECT * FROM shop_request_receipts WHERE request_key=?',(key,)).fetchone()
        if not row:return None
        if row['fingerprint']!=expected or row['shop_id']!=shop_id or row['identity']!=identity:
            raise warehouse.WarehouseConflict('UUID уже использован для другого запроса')
        return _result(conn,row,record)

def _operation_scope(conn,shop_id,operation):
    name=operation.get('name');kwargs=operation.get('arguments')
    if name not in OPERATIONS or not isinstance(kwargs,dict):raise warehouse.WarehouseScopeError('Неизвестная операция центрального склада')
    if any(key in kwargs for key in ('_conn','actor','role')):raise warehouse.WarehouseScopeError('Контекст операции задаёт сервер')
    if name=='reserve':target=kwargs.get('shop_id')
    elif name=='return_sale':
        row=conn.execute('SELECT shop_id FROM warehouse_documents WHERE request_key=? AND kind=\'sale\'',(kwargs.get('sale_request_key'),)).fetchone()
        target=row['shop_id'] if row else None
    elif name=='release_owned_hold':
        row=conn.execute('SELECT shop_id FROM warehouse_owned_holds WHERE id=?',(kwargs.get('hold_id'),)).fetchone()
        target=row['shop_id'] if row else None
    else:
        row=conn.execute('SELECT shop_id FROM warehouse_reservations WHERE id=?',(kwargs.get('reservation_id'),)).fetchone()
        target=row['shop_id'] if row else None
    if type(target)!=int or target!=shop_id:raise warehouse.WarehouseScopeError('Документ принадлежит другому магазину')
    return name,kwargs

def commit(shop_id,identity,base_revision,request_key,request,account,role,*,snapshot,response,
           operations=(),synced=False,source_errors=None):
    key=_key(request_key);expected=fingerprint(shop_id,identity,base_revision,request)
    if type(shop_id)!=int or shop_id<=0:raise warehouse.WarehouseScopeError('Мост предназначен для дополнительного магазина')
    if not isinstance(operations,(list,tuple)) or len(operations)>100:raise warehouse.WarehouseScopeError('Слишком много физических операций')
    # Do not initialize or commit inside the shared business transaction.
    initialize()
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        record=_scope(conn,shop_id,identity,account,role)
        prior=conn.execute('SELECT * FROM shop_request_receipts WHERE request_key=?',(key,)).fetchone()
        if prior:
            if prior['fingerprint']!=expected or prior['identity']!=identity or prior['shop_id']!=shop_id:
                raise warehouse.WarehouseConflict('UUID уже использован для другого запроса')
            return _result(conn,prior,record)
        if record['revision']!=base_revision:raise warehouse.WarehouseConflict('Версия магазина изменилась; повторите проверку')
        outcomes=[]
        for operation in operations:
            name,arguments=_operation_scope(conn,shop_id,operation)
            outcomes.append(OPERATIONS[name](**arguments,actor=account,role=role,_conn=conn))
        shops.store_snapshot_in_transaction(conn,shop_id,base_revision,snapshot,synced=synced,
                                            source_errors=source_errors,identity=identity)
        result={'response':response,'warehouse_results':outcomes}
        result_json=json.dumps(result,ensure_ascii=False,allow_nan=False,separators=(',',':'))
        if len(result_json.encode())>4*1024*1024:raise warehouse.WarehouseConflict('Ответ слишком большой')
        conn.execute('INSERT INTO shop_request_receipts VALUES(?,?,?,?,?,?,?,?,?)',
                     (key,shop_id,identity,base_revision,base_revision+1,expected,result_json,warehouse.now(),account))
        receipt=conn.execute('SELECT * FROM shop_request_receipts WHERE request_key=?',(key,)).fetchone()
        current=conn.execute('SELECT id,account_identity,revision,active FROM shop_workspaces WHERE id=?',(shop_id,)).fetchone()
        return _result(conn,receipt,current)
