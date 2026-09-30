"""Trusted-gateway primary binding and snapshot-proven exact SKU mappings.

The API port must remain behind existing gateway ForwardAuth. This module does
not turn arbitrary internet headers into authentication or expose credentials.
"""
from contextlib import contextmanager
import json
import sqlite3
import tempfile
import os
from pathlib import Path
import config
from db.core import connect
from db import shared_warehouse as warehouse, shop_workspaces as shops
from db.wip import validate_catalog_identity

ROLES={'owner','manager','viewer','technologist','cutter','seamstress','warehouse'}

def actor(account,role):
    if not config.TENANT_SLUG or role not in ROLES:
        raise warehouse.WarehouseScopeError('Требуется подтверждённая сессия этого кабинета')
    return warehouse.text(account,200)

def ensure_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS warehouse_primary_memberships(
      id INTEGER PRIMARY KEY,account TEXT NOT NULL,source_role TEXT NOT NULL,
      tenant_slug TEXT NOT NULL,identity TEXT NOT NULL,created_at TEXT NOT NULL,
      UNIQUE(account,source_role,tenant_slug,identity))''')
    conn.execute('''CREATE TABLE IF NOT EXISTS warehouse_mapping_proofs(
      mapping_id INTEGER PRIMARY KEY,shop_id INTEGER NOT NULL,shop_identity TEXT NOT NULL,
      snapshot_revision INTEGER,card_fingerprint TEXT NOT NULL,proof_json TEXT NOT NULL,
      actor TEXT NOT NULL,created_at TEXT NOT NULL)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS warehouse_setup_receipts(
      request_key TEXT PRIMARY KEY,shop_id INTEGER NOT NULL,identity TEXT NOT NULL,
      actor TEXT NOT NULL,action TEXT NOT NULL,fingerprint TEXT NOT NULL,
      result_json TEXT NOT NULL,created_at TEXT NOT NULL)''')
    for table in ('warehouse_primary_memberships','warehouse_mapping_proofs'):
        for operation in ('UPDATE','DELETE'):
            conn.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{operation.lower()} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'warehouse setup provenance immutable'); END")
        unique=('id=NEW.id OR (account=NEW.account AND source_role=NEW.source_role AND tenant_slug=NEW.tenant_slug AND identity=NEW.identity)' if table=='warehouse_primary_memberships' else 'mapping_id=NEW.mapping_id')
        conn.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_replace BEFORE INSERT ON {table} WHEN EXISTS(SELECT 1 FROM {table} WHERE {unique}) BEGIN SELECT RAISE(ABORT,'warehouse setup provenance immutable'); END")
    for operation in ('UPDATE','DELETE'):
        conn.execute(f"CREATE TRIGGER IF NOT EXISTS warehouse_setup_receipts_no_{operation.lower()} BEFORE {operation} ON warehouse_setup_receipts BEGIN SELECT RAISE(ABORT,'warehouse setup receipt immutable'); END")
    conn.execute("CREATE TRIGGER IF NOT EXISTS warehouse_setup_receipts_no_replace BEFORE INSERT ON warehouse_setup_receipts WHEN EXISTS(SELECT 1 FROM warehouse_setup_receipts WHERE request_key=NEW.request_key) BEGIN SELECT RAISE(ABORT,'warehouse setup receipt immutable'); END")
    conn.execute("CREATE TRIGGER IF NOT EXISTS warehouse_setup_receipts_no_document_key BEFORE INSERT ON warehouse_setup_receipts WHEN EXISTS(SELECT 1 FROM warehouse_documents WHERE request_key=NEW.request_key) BEGIN SELECT RAISE(ABORT,'warehouse key already used'); END")
    conn.execute("CREATE TRIGGER IF NOT EXISTS warehouse_documents_no_setup_key BEFORE INSERT ON warehouse_documents WHEN EXISTS(SELECT 1 FROM warehouse_setup_receipts WHERE request_key=NEW.request_key) BEGIN SELECT RAISE(ABORT,'warehouse key already used'); END")

def initialize():
    shops.initialize()
    with connect() as conn:
        warehouse.initialize(conn)
        ensure_schema(conn)

def access(conn,shop_id,account,role,write=False):
    account=actor(account,role)
    if shop_id==0:
        primary=conn.execute('SELECT * FROM warehouse_shops WHERE shop_id=0 AND active=1').fetchone()
        if not primary:raise warehouse.WarehouseScopeError('Сначала подтвердите подключение основного магазина')
        token=config.get_token()
        try:current_identity=shops.account_identity(token) if token else None
        except ValueError:current_identity=None
        if current_identity!=primary['identity']:
            raise warehouse.WarehouseScopeError('Подключение основного продавца изменилось; прежние документы недоступны')
        # Existing primary membership is verified upstream on every request.
        # can_write never overrides the current role check in warehouse.access.
        old=conn.execute('SELECT 1 FROM warehouse_primary_memberships WHERE account=? AND source_role=? AND tenant_slug=? AND identity=?',
                         (account,role,config.TENANT_SLUG,primary['identity'])).fetchone()
        if not old:
            if not conn.in_transaction:raise warehouse.WarehouseScopeError('Миграция доступа требует общей транзакции')
            conn.execute('INSERT INTO warehouse_primary_memberships(account,source_role,tenant_slug,identity,created_at) VALUES(?,?,?,?,?)',
                         (account,role,config.TENANT_SLUG,primary['identity'],warehouse.now()))
            conn.execute('INSERT OR IGNORE INTO warehouse_shop_acl VALUES(0,?,1)',(account,))
    return warehouse.access(conn,shop_id,account,role,write)

def bind_primary(account,role):
    actor(account,role)
    if role!='owner':raise warehouse.WarehouseScopeError('Подключение подтверждает владелец')
    token=config.get_token()
    if not token:raise warehouse.WarehouseScopeError('Сначала подключите WB основного магазина')
    from wb_api import WBAPI
    if not WBAPI(token).test_statistics():raise warehouse.WarehouseScopeError('Не удалось подтвердить продавца через WB')
    identity=shops.account_identity(token)
    if config.get_token()!=token:raise warehouse.WarehouseConflict('Подключение WB изменилось во время проверки')
    initialize()
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        if config.get_token()!=token:raise warehouse.WarehouseConflict('Подключение WB изменилось перед сохранением')
        result=warehouse.register_shop(0,identity,_conn=conn)
        access(conn,0,account,role)
        return {'shop_id':0,'verified_identity':result['identity'],'refresh_session':True}

@contextmanager
def catalog_connection(conn,shop_id,account,role,*,writable=False):
    scope=access(conn,shop_id,account,role)
    if shop_id==0:
        yield conn,{'shop_id':0,'identity':scope['identity'],'snapshot_revision':None}
        return
    row=conn.execute('SELECT account_identity,revision,database_snapshot FROM shop_workspaces WHERE id=? AND active=1',(shop_id,)).fetchone()
    if not row or row['account_identity']!=scope['identity'] or not row['database_snapshot']:
        raise warehouse.WarehouseScopeError('Нужна подтверждённая история выбранного магазина')
    # Native backup snapshots retain WAL-mode header bits. deserialize() cannot
    # open such a database in memory; a standalone private file preserves the
    # checkpointed backup bytes and permits SQLite's private WAL sidecars.
    with tempfile.TemporaryDirectory(dir=config.DATA_DIR,prefix='.warehouse-proof-') as directory:
        os.chmod(directory,0o700)
        filename=Path(directory)/'snapshot.sqlite3'
        filename.write_bytes(row['database_snapshot'])
        private=sqlite3.connect(filename);private.row_factory=sqlite3.Row
        try:
            if not writable:private.execute('PRAGMA query_only=ON')
            yield private,{'shop_id':shop_id,'identity':scope['identity'],'snapshot_revision':row['revision']}
        finally:private.close()

def catalog_proof(conn,shop_id,nm_id,chrt_id,account,role):
    nm_id=warehouse.integer(nm_id,maximum=2**53-1)
    chrt_id=warehouse.integer(chrt_id,True,2**53-1)
    with catalog_connection(conn,shop_id,account,role) as (catalog,scope):
        variant=validate_catalog_identity(catalog,nm_id,chrt_id,True)
        card=catalog.execute('SELECT raw_json FROM products_catalog WHERE nm_id=?',(nm_id,)).fetchone()
        raw=json.loads(card['raw_json'])
        return {**scope,'nm_id':nm_id,'chrt_id':chrt_id,'variant':variant,'card_fingerprint':warehouse.digest(raw)}

def mapping_preview(shop_id,nm_id,chrt_id,item_id,account,role):
    initialize()
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        proof=catalog_proof(conn,shop_id,nm_id,chrt_id,account,role)
        if not conn.execute('SELECT 1 FROM warehouse_items WHERE id=?',(item_id,)).fetchone():raise warehouse.WarehouseScopeError('Производственный SKU не найден')
        result={**proof,'item_id':item_id}
        return {**result,'fingerprint':warehouse.digest(result)}

def _replay(conn,key,shop_id,identity,account,action,body):
    if conn.execute('SELECT 1 FROM warehouse_documents WHERE request_key=?',(key,)).fetchone():
        raise warehouse.WarehouseConflict('Ключ уже использован для складского документа')
    fingerprint=warehouse.digest(body)
    row=conn.execute('SELECT * FROM warehouse_setup_receipts WHERE request_key=?',(key,)).fetchone()
    if row:
        if (row['shop_id'],row['identity'],row['actor'],row['action'],row['fingerprint'])!=(shop_id,identity,account,action,fingerprint):
            raise warehouse.WarehouseConflict('Ключ подтверждения уже использован для другого действия')
        return json.loads(row['result_json'])

def _receipt(conn,key,shop_id,identity,account,action,body,result):
    conn.execute('INSERT INTO warehouse_setup_receipts VALUES(?,?,?,?,?,?,?,?)',
      (key,shop_id,identity,account,action,warehouse.digest(body),warehouse.encoded(result),warehouse.now()))

def register_item(nm_id,chrt_id,name,account,role,request_key):
    actor(account,role)
    if role!='owner':raise warehouse.WarehouseScopeError('Соответствия настраивает владелец')
    initialize()
    body={'nm_id':nm_id,'chrt_id':chrt_id,'name':name}
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        scope=access(conn,0,account,role,True)
        old=_replay(conn,request_key,0,scope['identity'],account,'item',body)
        if old is not None:return old
        result=warehouse.register_item(nm_id,chrt_id,name,_conn=conn)
        _receipt(conn,request_key,0,scope['identity'],account,'item',body,result)
        return result

def mapping_confirm(shop_id,nm_id,chrt_id,item_id,expected,account,role,request_key=None):
    actor(account,role)
    if role!='owner':raise warehouse.WarehouseScopeError('Соответствие подтверждает владелец')
    initialize()
    body={'nm_id':nm_id,'chrt_id':chrt_id,'item_id':item_id,'fingerprint':expected}
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        scope=access(conn,shop_id,account,role,True)
        if shop_id:
            current=conn.execute('SELECT account_identity FROM shop_workspaces WHERE id=? AND active=1',(shop_id,)).fetchone()
            if not current or current['account_identity']!=scope['identity']:raise warehouse.WarehouseScopeError('Продавец магазина изменился')
        if request_key:
            old=_replay(conn,request_key,shop_id,scope['identity'],account,'mapping',body)
            if old is not None:return old
        proof=catalog_proof(conn,shop_id,nm_id,chrt_id,account,role)
        if warehouse.digest({**proof,'item_id':item_id})!=expected:raise warehouse.WarehouseConflict('Каталог или магазин изменился; повторите проверку')
        result=warehouse.map_sku(shop_id,nm_id,chrt_id,item_id,account,_conn=conn)
        old=conn.execute('SELECT * FROM warehouse_mapping_proofs WHERE mapping_id=?',(result['id'],)).fetchone()
        if not old:
            conn.execute('INSERT INTO warehouse_mapping_proofs VALUES(?,?,?,?,?,?,?,?)',
              (result['id'],shop_id,proof['identity'],proof['snapshot_revision'],proof['card_fingerprint'],warehouse.encoded(proof),account,warehouse.now()))
        elif old['shop_identity']!=proof['identity'] or old['card_fingerprint']!=proof['card_fingerprint']:
            raise warehouse.WarehouseConflict('Историческое подтверждение каталога нельзя переписать')
        if request_key:_receipt(conn,request_key,shop_id,scope['identity'],account,'mapping',body,result)
        return result

def workspace(shop_id,account,role):
    actor(account,role)
    initialize()
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        if shop_id==0 and not conn.execute('SELECT 1 FROM warehouse_shops WHERE shop_id=0 AND active=1').fetchone():
            return {'shop_id':0,'shop_identity':None,'identity':None,'snapshot_revision':None,
              'needs_primary_binding':True,'can_write':False,'can_configure':role=='owner',
              'products':[],'products_truncated':False,'items':[],'mappings':[],
              'reservations':[],'ownership':[],'documents':[],'holds':[]}
        scope=access(conn,shop_id,account,role)
        writable=True
        try:warehouse.access(conn,shop_id,account,role,True)
        except warehouse.WarehouseScopeError:writable=False
        with catalog_connection(conn,shop_id,account,role) as (catalog,metadata):
            cards=catalog.execute('SELECT nm_id,product_name,raw_json FROM products_catalog ORDER BY nm_id LIMIT 501').fetchall()
            products=[]
            for card in cards[:500]:
                try:
                    raw=json.loads(card['raw_json']);sizes=raw.get('sizes')
                    if not isinstance(sizes,list):continue
                    variants=[size.get('chrtID',size.get('chrtId',size.get('chrt_id'))) for size in sizes] or [0]
                    for chrt in variants:
                        products.append(validate_catalog_identity(catalog,card['nm_id'],chrt,True))
                        if len(products)>500:break
                except (ValueError,TypeError,AttributeError):continue
                if len(products)>500:break
        bounded=lambda table,condition,limit=100:[dict(row) for row in conn.execute(f'SELECT * FROM {table} WHERE {condition} ORDER BY rowid DESC LIMIT ?',(shop_id,limit))]
        reservations=[dict(row) for row in conn.execute('''SELECT r.*,m.external_nm_id,m.external_chrt_id,
          i.nm_id AS canonical_nm_id,i.chrt_id AS canonical_chrt_id,i.name AS item_name
          FROM warehouse_reservations r JOIN warehouse_mappings m ON m.id=r.mapping_id
          JOIN warehouse_items i ON i.id=r.item_id WHERE r.shop_id=? ORDER BY r.created_at DESC,r.id DESC LIMIT 100''',(shop_id,))]
        documents=bounded('warehouse_documents','shop_id=?')
        for document in documents:document['result']=json.loads(document['result_json'])
        return {'shop_id':shop_id,'shop_identity':scope['identity'],**metadata,'needs_primary_binding':False,'can_write':writable,
          'can_configure':role=='owner','products':products[:500],'products_truncated':len(cards)>500 or len(products)>500,
          'items':[dict(row) for row in conn.execute('SELECT * FROM warehouse_items ORDER BY name LIMIT 500')],
          'mappings':bounded('warehouse_mappings','shop_id=?',500),
          'reservations':reservations,
          'ownership':bounded('warehouse_ownership','shop_id=?',500),
          'documents':documents,
          'holds':bounded('warehouse_owned_holds','shop_id=?')}
