"""Canonical FIFO allocation for explicitly mapped shops; no marketplace writes.

The main tenant SQLite is the only physical inventory authority. A child shop
snapshot may project these facts, but must never manufacture an independent
physical copy. Public adapters and the snapshot bridge are separate stages.
"""
from datetime import date, datetime, timezone
import hashlib
import json
import math
import uuid
from .core import connect
from .incident_workflow import held_units

class WarehouseError(ValueError):
    code='SHARED_WAREHOUSE'
    def __init__(self,message): super().__init__(f'{self.code}: {message}')
class WarehouseStockError(WarehouseError): code='SHARED_STOCK_UNAVAILABLE'
class WarehouseScopeError(WarehouseError): code='SHARED_SCOPE_REQUIRED'
class WarehouseConflict(WarehouseError): code='SHARED_DOCUMENT_CONFLICT'

def now():return datetime.now(timezone.utc).isoformat()
def encoded(value):return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'))
def digest(value):return hashlib.sha256(encoded(value).encode()).hexdigest()
def text(value,maximum=120):
    if not isinstance(value,str) or not value.strip() or len(value)>maximum: raise WarehouseError('Нужна непустая строка допустимой длины')
    return value.strip()
def integer(value,allow_zero=False,maximum=10**9):
    if type(value)!=int or value<(0 if allow_zero else 1) or value>maximum: raise WarehouseError('Нужно целое число допустимого диапазона')
    return value
def day(value):return date.fromisoformat(str(value)).isoformat()

def initialize(conn):
    from .incident_workflow import ensure_schema
    ensure_schema(conn)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS warehouse_shops(
      shop_id INTEGER PRIMARY KEY CHECK(shop_id>=0), identity TEXT NOT NULL UNIQUE,
      active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS warehouse_shop_acl(
      shop_id INTEGER NOT NULL, account TEXT NOT NULL, can_write INTEGER NOT NULL,
      PRIMARY KEY(shop_id,account));
    CREATE TABLE IF NOT EXISTS warehouse_items(
      id TEXT PRIMARY KEY, nm_id INTEGER NOT NULL, chrt_id INTEGER NOT NULL,
      name TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
      UNIQUE(nm_id,chrt_id));
    CREATE TABLE IF NOT EXISTS warehouse_mappings(
      id INTEGER PRIMARY KEY, shop_id INTEGER NOT NULL, shop_identity TEXT NOT NULL,
      external_nm_id INTEGER NOT NULL, external_chrt_id INTEGER NOT NULL,
      item_id TEXT NOT NULL REFERENCES warehouse_items(id), revision INTEGER NOT NULL DEFAULT 1,
      active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, actor TEXT,
      UNIQUE(shop_id,external_nm_id,external_chrt_id));
    CREATE TABLE IF NOT EXISTS warehouse_reservations(
      id TEXT PRIMARY KEY, request_key TEXT NOT NULL UNIQUE, fingerprint TEXT NOT NULL,
      shop_id INTEGER NOT NULL, shop_identity TEXT NOT NULL, source_ref TEXT NOT NULL,
      mapping_id INTEGER NOT NULL, mapping_revision INTEGER NOT NULL,
      item_id TEXT NOT NULL, quantity INTEGER NOT NULL CHECK(quantity>0),
      document_date TEXT NOT NULL, status TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
      created_at TEXT NOT NULL, actor TEXT, UNIQUE(shop_id,source_ref));
    CREATE TABLE IF NOT EXISTS warehouse_reservation_layers(
      reservation_id TEXT NOT NULL, layer_id INTEGER NOT NULL,
      units INTEGER NOT NULL CHECK(units>0), cost_rub REAL NOT NULL,
      provenance_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'reserved',
      PRIMARY KEY(reservation_id,layer_id));
    CREATE TABLE IF NOT EXISTS warehouse_ownership(
      reservation_id TEXT NOT NULL, layer_id INTEGER NOT NULL, shop_id INTEGER NOT NULL,
      inbound_units INTEGER NOT NULL DEFAULT 0 CHECK(inbound_units>=0),
      wb_units INTEGER NOT NULL DEFAULT 0 CHECK(wb_units>=0),
      PRIMARY KEY(reservation_id,layer_id));
    CREATE TABLE IF NOT EXISTS warehouse_documents(
      id TEXT PRIMARY KEY, request_key TEXT NOT NULL UNIQUE, fingerprint TEXT NOT NULL,
      shop_id INTEGER NOT NULL, shop_identity TEXT NOT NULL, source_ref TEXT NOT NULL,
      kind TEXT NOT NULL, reservation_id TEXT, document_date TEXT NOT NULL,
      result_json TEXT NOT NULL, created_at TEXT NOT NULL, actor TEXT,
      UNIQUE(shop_id,kind,source_ref));
    CREATE TABLE IF NOT EXISTS warehouse_events(
      id INTEGER PRIMARY KEY, document_id TEXT NOT NULL, event_json TEXT NOT NULL,
      created_at TEXT NOT NULL, actor TEXT);
    CREATE TABLE IF NOT EXISTS warehouse_sale_layers(
      document_id TEXT NOT NULL, reservation_id TEXT NOT NULL, layer_id INTEGER NOT NULL,
      units INTEGER NOT NULL CHECK(units>0), returned_units INTEGER NOT NULL DEFAULT 0,
      unit_cost_rub REAL NOT NULL, PRIMARY KEY(document_id,layer_id));
    """)
    for table in ('warehouse_documents','warehouse_events'):
        for operation in ('UPDATE','DELETE'):
            conn.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{operation.lower()} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'shared warehouse history is immutable'); END")
        conflict='id=NEW.id OR request_key=NEW.request_key OR (shop_id=NEW.shop_id AND kind=NEW.kind AND source_ref=NEW.source_ref)' if table=='warehouse_documents' else 'id=NEW.id'
        conn.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_replace BEFORE INSERT ON {table} WHEN EXISTS(SELECT 1 FROM {table} WHERE {conflict}) BEGIN SELECT RAISE(ABORT,'shared warehouse history is immutable'); END")
    for table,identity in (
        ('warehouse_shops','shop_id=NEW.shop_id OR identity=NEW.identity'),
        ('warehouse_items','id=NEW.id OR (nm_id=NEW.nm_id AND chrt_id=NEW.chrt_id)'),
        ('warehouse_mappings','id=NEW.id OR (shop_id=NEW.shop_id AND external_nm_id=NEW.external_nm_id AND external_chrt_id=NEW.external_chrt_id)'),
        ('warehouse_reservations','id=NEW.id OR request_key=NEW.request_key OR (shop_id=NEW.shop_id AND source_ref=NEW.source_ref)')):
        conn.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_replace BEFORE INSERT ON {table} WHEN EXISTS(SELECT 1 FROM {table} WHERE {identity}) BEGIN SELECT RAISE(ABORT,'SHARED_WAREHOUSE: immutable identity'); END")
        conn.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_delete BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT,'SHARED_WAREHOUSE: immutable identity'); END")
    for table,columns in (
        ('warehouse_shops',('shop_id','identity','created_at')),
        ('warehouse_items',('id','nm_id','chrt_id','created_at')),
        ('warehouse_mappings',('id','shop_id','shop_identity','external_nm_id','external_chrt_id','item_id','revision','created_at')),
        ('warehouse_reservations',('id','request_key','fingerprint','shop_id','shop_identity','source_ref','mapping_id','mapping_revision','item_id','quantity','document_date','created_at','actor'))):
        condition=' OR '.join(f'NEW.{c} IS NOT OLD.{c}' for c in columns)
        conn.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_immutable_header BEFORE UPDATE ON {table} WHEN {condition} BEGIN SELECT RAISE(ABORT,'SHARED_WAREHOUSE: immutable identity'); END")
    # Defense in depth: existing legacy UPDATE/reversal paths cannot reduce a
    # layer below incident holds + any shop's reservation/physical ownership.
    conn.executescript("""
    CREATE TRIGGER IF NOT EXISTS warehouse_reservation_state_guard BEFORE UPDATE OF status,revision ON warehouse_reservations
    WHEN NEW.revision!=OLD.revision+1 OR NOT (
      (OLD.status='reserved' AND NEW.status IN ('inbound','released')) OR
      (OLD.status='inbound' AND NEW.status='wb'))
    BEGIN SELECT RAISE(ABORT,'SHARED_WAREHOUSE: invalid reservation state transition'); END;
    CREATE TRIGGER IF NOT EXISTS warehouse_reservation_provenance_guard BEFORE UPDATE ON warehouse_reservation_layers
    WHEN NEW.reservation_id IS NOT OLD.reservation_id OR NEW.layer_id IS NOT OLD.layer_id
      OR NEW.units IS NOT OLD.units OR NEW.cost_rub IS NOT OLD.cost_rub
      OR NEW.provenance_json IS NOT OLD.provenance_json
    BEGIN SELECT RAISE(ABORT,'SHARED_WAREHOUSE: immutable reservation provenance'); END;
    CREATE TRIGGER IF NOT EXISTS warehouse_reservation_delete_guard BEFORE DELETE ON warehouse_reservation_layers
    BEGIN SELECT RAISE(ABORT,'SHARED_WAREHOUSE: immutable reservation provenance'); END;
    CREATE TRIGGER IF NOT EXISTS warehouse_reservation_insert_guard BEFORE INSERT ON warehouse_reservation_layers
    WHEN EXISTS(SELECT 1 FROM warehouse_reservation_layers WHERE reservation_id=NEW.reservation_id AND layer_id=NEW.layer_id)
      OR NOT EXISTS(SELECT 1 FROM finished_goods_cost_layers WHERE id=NEW.layer_id)
      OR (NEW.status='reserved' AND NEW.units+
        COALESCE((SELECT SUM(units) FROM warehouse_reservation_layers WHERE layer_id=NEW.layer_id AND status='reserved'),0)+
        COALESCE((SELECT SUM(units) FROM incident_holds WHERE layer_id=NEW.layer_id AND location='ready' AND status='held'),0)>
        (SELECT ready_units FROM finished_goods_cost_layers WHERE id=NEW.layer_id))
    BEGIN SELECT RAISE(ABORT,'SHARED_WAREHOUSE: reservation exceeds physical stock or replaces provenance'); END;
    CREATE TRIGGER IF NOT EXISTS warehouse_reservation_reactivate_guard BEFORE UPDATE OF status ON warehouse_reservation_layers
    WHEN NEW.status='reserved' AND OLD.status!='reserved'
    BEGIN SELECT RAISE(ABORT,'SHARED_WAREHOUSE: closed reservation cannot reactivate'); END;
    CREATE TRIGGER IF NOT EXISTS warehouse_ownership_insert_guard BEFORE INSERT ON warehouse_ownership
    WHEN EXISTS(SELECT 1 FROM warehouse_ownership WHERE reservation_id=NEW.reservation_id AND layer_id=NEW.layer_id)
      OR NOT EXISTS(SELECT 1 FROM warehouse_reservations r JOIN warehouse_reservation_layers l ON l.reservation_id=r.id
        WHERE r.id=NEW.reservation_id AND l.layer_id=NEW.layer_id AND r.shop_id=NEW.shop_id)
      OR NEW.inbound_units+COALESCE((SELECT SUM(inbound_units) FROM warehouse_ownership WHERE layer_id=NEW.layer_id),0)>
        (SELECT inbound_units FROM finished_goods_cost_layers WHERE id=NEW.layer_id)
      OR NEW.wb_units+COALESCE((SELECT SUM(wb_units) FROM warehouse_ownership WHERE layer_id=NEW.layer_id),0)>
        (SELECT wb_units FROM finished_goods_cost_layers WHERE id=NEW.layer_id)
    BEGIN SELECT RAISE(ABORT,'SHARED_WAREHOUSE: invalid physical ownership'); END;
    CREATE TRIGGER IF NOT EXISTS warehouse_ownership_update_guard BEFORE UPDATE ON warehouse_ownership
    WHEN NEW.reservation_id IS NOT OLD.reservation_id OR NEW.layer_id IS NOT OLD.layer_id OR NEW.shop_id IS NOT OLD.shop_id
      OR NEW.inbound_units+COALESCE((SELECT SUM(inbound_units) FROM warehouse_ownership WHERE layer_id=NEW.layer_id AND reservation_id!=OLD.reservation_id),0)>
        (SELECT inbound_units FROM finished_goods_cost_layers WHERE id=NEW.layer_id)
      OR NEW.wb_units+COALESCE((SELECT SUM(wb_units) FROM warehouse_ownership WHERE layer_id=NEW.layer_id AND reservation_id!=OLD.reservation_id),0)>
        (SELECT wb_units FROM finished_goods_cost_layers WHERE id=NEW.layer_id)
    BEGIN SELECT RAISE(ABORT,'SHARED_WAREHOUSE: invalid physical ownership'); END;
    CREATE TRIGGER IF NOT EXISTS warehouse_ownership_delete_guard BEFORE DELETE ON warehouse_ownership
    BEGIN SELECT RAISE(ABORT,'SHARED_WAREHOUSE: physical ownership history cannot be deleted'); END;
    CREATE TRIGGER IF NOT EXISTS warehouse_physical_guard BEFORE UPDATE ON finished_goods_cost_layers
    WHEN NEW.ready_units <
      COALESCE((SELECT SUM(units) FROM warehouse_reservation_layers WHERE layer_id=OLD.id AND status='reserved'),0)+
      COALESCE((SELECT SUM(units) FROM incident_holds WHERE layer_id=OLD.id AND location='ready' AND status='held'),0)
      OR NEW.inbound_units <
      COALESCE((SELECT SUM(inbound_units) FROM warehouse_ownership WHERE layer_id=OLD.id),0)+
      COALESCE((SELECT SUM(units) FROM incident_holds WHERE layer_id=OLD.id AND location='inbound' AND status='held'),0)
      OR NEW.wb_units <
      COALESCE((SELECT SUM(wb_units) FROM warehouse_ownership WHERE layer_id=OLD.id),0)+
      COALESCE((SELECT SUM(units) FROM incident_holds WHERE layer_id=OLD.id AND location='wb' AND status='held'),0)
    BEGIN SELECT RAISE(ABORT,'SHARED_WAREHOUSE: physical quantity is reserved or allocated'); END;
    CREATE TRIGGER IF NOT EXISTS warehouse_layer_delete_guard BEFORE DELETE ON finished_goods_cost_layers
    WHEN EXISTS(SELECT 1 FROM warehouse_reservation_layers WHERE layer_id=OLD.id)
      OR EXISTS(SELECT 1 FROM warehouse_ownership WHERE layer_id=OLD.id)
    BEGIN SELECT RAISE(ABORT,'SHARED_WAREHOUSE: immutable layer provenance'); END;
    CREATE TRIGGER IF NOT EXISTS warehouse_layer_provenance_guard BEFORE UPDATE ON finished_goods_cost_layers
    WHEN EXISTS(SELECT 1 FROM warehouse_reservation_layers WHERE layer_id=OLD.id)
      AND (NEW.nm_id IS NOT OLD.nm_id OR NEW.chrt_id IS NOT OLD.chrt_id
        OR NEW.unit_cost_rub IS NOT OLD.unit_cost_rub OR NEW.source_type IS NOT OLD.source_type
        OR NEW.source_ref IS NOT OLD.source_ref OR NEW.source_date IS NOT OLD.source_date
        OR NEW.producer_release_id IS NOT OLD.producer_release_id)
    BEGIN SELECT RAISE(ABORT,'SHARED_WAREHOUSE: immutable layer provenance'); END;
    CREATE TRIGGER IF NOT EXISTS warehouse_layer_replace_guard BEFORE INSERT ON finished_goods_cost_layers
    WHEN EXISTS(SELECT 1 FROM finished_goods_cost_layers l WHERE l.id=NEW.id OR (l.source_type=NEW.source_type AND l.source_ref=NEW.source_ref))
      AND EXISTS(SELECT 1 FROM warehouse_reservation_layers r JOIN finished_goods_cost_layers l ON l.id=r.layer_id WHERE l.id=NEW.id OR (l.source_type=NEW.source_type AND l.source_ref=NEW.source_ref))
    BEGIN SELECT RAISE(ABORT,'SHARED_WAREHOUSE: immutable layer provenance'); END;
    """)
    for location in ('ready','inbound','wb'):
        allocated=("COALESCE((SELECT SUM(units) FROM warehouse_reservation_layers WHERE layer_id=NEW.layer_id AND status='reserved'),0)" if location=='ready' else f"COALESCE((SELECT SUM({location}_units) FROM warehouse_ownership WHERE layer_id=NEW.layer_id),0)")
        for operation in ('INSERT','UPDATE'):
            exclude='' if operation=='INSERT' else ' AND id!=OLD.id'
            condition=f"NEW.status='held' AND NEW.location='{location}' AND NEW.units+{allocated}+COALESCE((SELECT SUM(units) FROM incident_holds WHERE layer_id=NEW.layer_id AND location='{location}' AND status='held'{exclude}),0)>(SELECT {location}_units FROM finished_goods_cost_layers WHERE id=NEW.layer_id)"
            conn.execute(f"CREATE TRIGGER IF NOT EXISTS shared_hold_{location}_{operation.lower()} BEFORE {operation} ON incident_holds WHEN {condition} BEGIN SELECT RAISE(ABORT,'SHARED_WAREHOUSE: quarantine conflicts with shop allocation'); END")
        if location!='ready':
            for operation in ('INSERT','UPDATE'):
                exclude='' if operation=='INSERT' else ' AND reservation_id!=OLD.reservation_id'
                condition=f"NEW.{location}_units+COALESCE((SELECT SUM({location}_units) FROM warehouse_ownership WHERE layer_id=NEW.layer_id{exclude}),0)+COALESCE((SELECT SUM(units) FROM incident_holds WHERE layer_id=NEW.layer_id AND location='{location}' AND status='held'),0)>(SELECT {location}_units FROM finished_goods_cost_layers WHERE id=NEW.layer_id)"
                conn.execute(f"CREATE TRIGGER IF NOT EXISTS shared_ownership_hold_{location}_{operation.lower()} BEFORE {operation} ON warehouse_ownership WHEN {condition} BEGIN SELECT RAISE(ABORT,'SHARED_WAREHOUSE: ownership conflicts with quarantine'); END")
    conn.commit()

def allocated_units(conn,layer_id,location):
    if location=='ready':return reserved_units(conn,layer_id)
    if location not in {'inbound','wb'}:raise WarehouseError('Неизвестное расположение')
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='warehouse_ownership'").fetchone():return 0
    return int(conn.execute(f'SELECT COALESCE(SUM({location}_units),0) FROM warehouse_ownership WHERE layer_id=?',(layer_id,)).fetchone()[0])

def register_shop(shop_id,verified_identity,primary_accounts=()):
    shop_id=integer(shop_id,True);identity=text(verified_identity,200)
    with connect() as conn:
        initialize(conn);conn.execute('BEGIN IMMEDIATE')
        old=conn.execute('SELECT * FROM warehouse_shops WHERE shop_id=?',(shop_id,)).fetchone()
        if old and old['identity']!=identity:raise WarehouseConflict('Нельзя заменить продавца в существующем магазине')
        duplicate=conn.execute('SELECT shop_id FROM warehouse_shops WHERE identity=?',(identity,)).fetchone()
        if duplicate and duplicate['shop_id']!=shop_id:raise WarehouseConflict('Этот продавец уже зарегистрирован, включая основной магазин')
        if not old:conn.execute('INSERT INTO warehouse_shops(shop_id,identity,created_at) VALUES(?,?,?)',(shop_id,identity,now()))
        if shop_id==0:
            # Caller supplies the existing verified membership snapshot once;
            # new secondary workspaces do not inherit this migration.
            for account,can_write in primary_accounts:
                conn.execute('INSERT OR IGNORE INTO warehouse_shop_acl VALUES(?,?,?)',(0,text(account,200),int(bool(can_write))))
        return dict(conn.execute('SELECT * FROM warehouse_shops WHERE shop_id=?',(shop_id,)).fetchone())

def access(conn,shop_id,account,role,write=False):
    shop_id=integer(shop_id,True)
    shop=conn.execute('SELECT * FROM warehouse_shops WHERE shop_id=? AND active=1',(shop_id,)).fetchone()
    if not shop:raise WarehouseScopeError('Неизвестный или отключённый магазин')
    if role=='owner':return dict(shop)
    if role=='integration_reader':raise WarehouseScopeError('Интеграционной роли недоступен общий склад')
    acl=conn.execute('SELECT * FROM warehouse_shop_acl WHERE shop_id=? AND account=?',(shop_id,account)).fetchone()
    if not acl or (write and (not acl['can_write'] or role not in {'manager','warehouse'})):raise WarehouseScopeError('Нет разрешения на этот магазин или операцию')
    return dict(shop)

def grant(shop_id,account,can_write,role):
    if role!='owner':raise WarehouseScopeError('Доступ выдаёт владелец')
    with connect() as conn:
        initialize(conn);conn.execute('BEGIN IMMEDIATE');access(conn,shop_id,None,'owner')
        conn.execute('INSERT INTO warehouse_shop_acl VALUES(?,?,?) ON CONFLICT(shop_id,account) DO UPDATE SET can_write=excluded.can_write',(shop_id,text(account,200),int(bool(can_write))))

def register_item(nm_id,chrt_id,name):
    nm_id,chrt_id=integer(nm_id,maximum=2**53-1),integer(chrt_id,True,2**53-1)
    with connect() as conn:
        initialize(conn);conn.execute('BEGIN IMMEDIATE')
        # Explicitly choose a production SKU, not a secondary seller nmID join.
        if not conn.execute('SELECT 1 FROM products_catalog WHERE nm_id=?',(nm_id,)).fetchone():raise WarehouseScopeError('Выберите существующий производственный товар')
        from .wip import validate_catalog_identity
        validate_catalog_identity(conn,nm_id,chrt_id,True)
        old=conn.execute('SELECT * FROM warehouse_items WHERE nm_id=? AND chrt_id=?',(nm_id,chrt_id)).fetchone()
        if old:return dict(old)
        item=str(uuid.uuid4());conn.execute('INSERT INTO warehouse_items(id,nm_id,chrt_id,name,created_at) VALUES(?,?,?,?,?)',(item,nm_id,chrt_id,text(name),now()))
        return dict(conn.execute('SELECT * FROM warehouse_items WHERE id=?',(item,)).fetchone())

def map_sku(shop_id,external_nm_id,external_chrt_id,item_id,actor=None):
    external_nm_id,external_chrt_id=integer(external_nm_id,maximum=2**53-1),integer(external_chrt_id,True,2**53-1)
    with connect() as conn:
        initialize(conn);conn.execute('BEGIN IMMEDIATE');shop=access(conn,shop_id,actor,'owner')
        if not conn.execute('SELECT 1 FROM warehouse_items WHERE id=?',(item_id,)).fetchone():raise WarehouseScopeError('Производственный SKU не найден')
        old=conn.execute('SELECT * FROM warehouse_mappings WHERE shop_id=? AND external_nm_id=? AND external_chrt_id=?',(shop_id,external_nm_id,external_chrt_id)).fetchone()
        if old:
            if old['item_id']!=item_id:raise WarehouseConflict('Существующее соответствие нельзя переписать задним числом')
            return dict(old)
        result=conn.execute('INSERT INTO warehouse_mappings(shop_id,shop_identity,external_nm_id,external_chrt_id,item_id,created_at,actor) VALUES(?,?,?,?,?,?,?)',(shop_id,shop['identity'],external_nm_id,external_chrt_id,item_id,now(),actor))
        return dict(conn.execute('SELECT * FROM warehouse_mappings WHERE id=?',(result.lastrowid,)).fetchone())

def managed(conn,nm_id,chrt_id):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='warehouse_items'").fetchone():return False
    # A missing/wrong variant cannot fall back to a synthetic base-card layer.
    # Once a production card enters central allocation all its physical events
    # require an exact explicit mapping, even for variants not yet configured.
    return bool(conn.execute('SELECT 1 FROM warehouse_items WHERE nm_id=?',(nm_id,)).fetchone())

def reserved_units(conn,layer_id):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='warehouse_reservation_layers'").fetchone():return 0
    return int(conn.execute("SELECT COALESCE(SUM(units),0) FROM warehouse_reservation_layers WHERE layer_id=? AND status='reserved'",(layer_id,)).fetchone()[0])

def _mapping(conn,shop_id,nm_id,chrt_id):
    mapping=conn.execute('SELECT m.*,i.nm_id,i.chrt_id,i.name FROM warehouse_mappings m JOIN warehouse_items i ON i.id=m.item_id WHERE m.shop_id=? AND m.external_nm_id=? AND m.external_chrt_id=? AND m.active=1',(shop_id,nm_id,chrt_id)).fetchone()
    if not mapping:raise WarehouseScopeError('Нет явно подтверждённого соответствия SKU и размера')
    return dict(mapping)

def _preview(conn,shop_id,nm_id,chrt_id,quantity,document_date,actor,role):
    shop=access(conn,shop_id,actor,role)
    mapping=_mapping(conn,shop_id,nm_id,chrt_id)
    if mapping['shop_identity']!=shop['identity']:raise WarehouseScopeError('Идентичность магазина не совпадает')
    layers=conn.execute("SELECT * FROM finished_goods_cost_layers WHERE nm_id=? AND chrt_id=? AND status!='reversed' AND ready_units>0 AND source_date IS NOT NULL AND source_date<=? AND source_type NOT IN ('synthetic_sale','return_unmatched','reconstruction_cost_pending') ORDER BY source_date,id",(mapping['nm_id'],mapping['chrt_id'],document_date)).fetchall()
    remaining=quantity;chosen=[]
    for layer in layers:
        available=max(0,int(layer['ready_units'])-reserved_units(conn,layer['id'])-held_units(conn,layer['id'],'ready'))
        take=min(available,remaining)
        if not take:continue
        cost=float(layer['unit_cost_rub'])
        if not math.isfinite(cost) or cost<0:raise WarehouseStockError('Стоимость партии неизвестна или недопустима')
        provenance={k:layer[k] for k in ('id','nm_id','chrt_id','source_type','source_ref','source_date','producer_release_id','unit_cost_rub')}
        chosen.append(dict(layer_id=layer['id'],units=take,cost_rub=round(cost*take,2),provenance=provenance));remaining-=take
        if remaining==0:break
    return dict(shop_id=shop_id,shop_identity=shop['identity'],mapping_id=mapping['id'],mapping_revision=mapping['revision'],item_id=mapping['item_id'],quantity=quantity,document_date=document_date,layers=chosen,missing_units=remaining)

def preview(shop_id,nm_id,chrt_id,quantity,document_date,actor=None,role='owner'):
    nm_id,chrt_id,quantity=integer(nm_id,maximum=2**53-1),integer(chrt_id,True,2**53-1),integer(quantity);document_date=day(document_date)
    with connect() as conn:
        initialize(conn)
        result=_preview(conn,shop_id,nm_id,chrt_id,quantity,document_date,actor,role)
        return dict(result,fingerprint=digest(result))

def reserve(shop_id,nm_id,chrt_id,quantity,document_date,source_ref,request_key,expected,actor=None,role='owner'):
    nm_id,chrt_id,quantity=integer(nm_id,maximum=2**53-1),integer(chrt_id,True,2**53-1),integer(quantity);document_date=day(document_date)
    source_ref,request_key=text(source_ref),text(request_key)
    body=dict(shop_id=shop_id,nm_id=nm_id,chrt_id=chrt_id,quantity=quantity,document_date=document_date,source_ref=source_ref,expected=expected)
    fingerprint=digest(body)
    with connect() as conn:
        initialize(conn);conn.execute('BEGIN IMMEDIATE');access(conn,shop_id,actor,role,True)
        old=conn.execute('SELECT * FROM warehouse_reservations WHERE request_key=? OR (shop_id=? AND source_ref=?)',(request_key,shop_id,source_ref)).fetchone()
        if old:
            if old['fingerprint']!=fingerprint:raise WarehouseConflict('Документ или ключ резерва уже использован')
            return dict(old)
        result=_preview(conn,shop_id,nm_id,chrt_id,quantity,document_date,actor,role)
        if digest(result)!=expected:raise WarehouseConflict('Остатки изменились; повторите предварительную проверку')
        if result['missing_units']:raise WarehouseStockError('Не хватает подтверждённого запаса; ожидайте выпуск/приход')
        reservation=str(uuid.uuid4())
        conn.execute("INSERT INTO warehouse_reservations(id,request_key,fingerprint,shop_id,shop_identity,source_ref,mapping_id,mapping_revision,item_id,quantity,document_date,status,created_at,actor) VALUES(?,?,?,?,?,?,?,?,?,?,?,'reserved',?,?)",(reservation,request_key,fingerprint,shop_id,result['shop_identity'],source_ref,result['mapping_id'],result['mapping_revision'],result['item_id'],quantity,document_date,now(),actor))
        for layer in result['layers']:
            conn.execute('INSERT INTO warehouse_reservation_layers(reservation_id,layer_id,units,cost_rub,provenance_json) VALUES(?,?,?,?,?)',(reservation,layer['layer_id'],layer['units'],layer['cost_rub'],encoded(layer['provenance'])))
        _receipt(conn,shop_id,result['shop_identity'],'reserve',source_ref,request_key,fingerprint,reservation,document_date,dict(reservation_id=reservation,**result),actor)
        return dict(conn.execute('SELECT * FROM warehouse_reservations WHERE id=?',(reservation,)).fetchone())

def _receipt(conn,shop_id,identity,kind,source_ref,request_key,fingerprint,reservation,document_date,result,actor):
    document=str(uuid.uuid4())
    conn.execute('INSERT INTO warehouse_documents VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(document,request_key,fingerprint,shop_id,identity,source_ref,kind,reservation,document_date,encoded(result),now(),actor))
    conn.execute('INSERT INTO warehouse_events(document_id,event_json,created_at,actor) VALUES(?,?,?,?)',(document,encoded(dict(kind=kind,result=result)),now(),actor))
    return document

def _replay(conn,shop_id,kind,source_ref,key,fingerprint):
    old=conn.execute('SELECT * FROM warehouse_documents WHERE request_key=? OR (shop_id=? AND kind=? AND source_ref=?)',(key,shop_id,kind,source_ref)).fetchone()
    if old:
        if old['fingerprint']!=fingerprint:raise WarehouseConflict('Документ уже подтверждён с другим содержимым')
        return json.loads(old['result_json'])

def _reservation(conn,reservation,actor,role):
    row=conn.execute('SELECT * FROM warehouse_reservations WHERE id=?',(reservation,)).fetchone()
    if not row:raise WarehouseScopeError('Резерв не найден')
    shop=access(conn,row['shop_id'],actor,role,True)
    if row['shop_identity']!=shop['identity']:raise WarehouseScopeError('Продавец не совпадает с документом')
    return dict(row)

def transition(reservation_id,kind,source_ref,request_key,document_date,revision,actor=None,role='owner'):
    """Whole-reservation commit/receipt/release, preserving every layer's cost."""
    if kind not in {'commit','receipt','release'}:raise WarehouseError('Неподдерживаемый складской переход')
    source_ref,request_key=text(source_ref),text(request_key);document_date=day(document_date);revision=integer(revision)
    body=dict(reservation_id=reservation_id,kind=kind,source_ref=source_ref,document_date=document_date,revision=revision);fingerprint=digest(body)
    with connect() as conn:
        initialize(conn);conn.execute('BEGIN IMMEDIATE');reservation=_reservation(conn,reservation_id,actor,role)
        replay=_replay(conn,reservation['shop_id'],kind,source_ref,request_key,fingerprint)
        if replay is not None:return replay
        if reservation['revision']!=revision:raise WarehouseConflict('Резерв изменился; обновите данные')
        if document_date<reservation['document_date']:raise WarehouseConflict('Движение раньше исходного документа')
        latest=conn.execute('SELECT MAX(document_date) FROM warehouse_documents WHERE reservation_id=?',(reservation_id,)).fetchone()[0]
        if latest and document_date<latest:raise WarehouseConflict('Движение раньше предыдущего подтверждённого перехода')
        expected={'commit':'reserved','release':'reserved','receipt':'inbound'}[kind]
        if reservation['status']!=expected:raise WarehouseConflict('Неверный статус или порядок движения')
        layers=conn.execute('SELECT r.*,l.unit_cost_rub,l.ready_units,l.inbound_units,l.wb_units,l.nm_id,l.chrt_id FROM warehouse_reservation_layers r JOIN finished_goods_cost_layers l ON l.id=r.layer_id WHERE r.reservation_id=? ORDER BY l.source_date,l.id',(reservation_id,)).fetchall()
        allocations=[];total=0
        for layer in layers:
            quantity=layer['units'];layer_id=layer['layer_id']
            if kind=='release':
                conn.execute("UPDATE warehouse_reservation_layers SET status='released' WHERE reservation_id=? AND layer_id=?",(reservation_id,layer_id));continue
            provenance=json.loads(layer['provenance_json'])
            if float(provenance['unit_cost_rub'])!=float(layer['unit_cost_rub']):raise WarehouseConflict('Историческая стоимость партии изменилась')
            from_location,to_location=('ready','inbound') if kind=='commit' else ('inbound','wb')
            if held_units(conn,layer_id,from_location)+quantity>layer[from_location+'_units']:raise WarehouseStockError('Количество находится в карантине или отсутствует')
            if kind=='commit':
                # Own reserve leaves the active sum first, still under the same
                # writer transaction; foreign reserves remain in SQL guard.
                conn.execute("UPDATE warehouse_reservation_layers SET status='allocated' WHERE reservation_id=? AND layer_id=? AND status='reserved'",(reservation_id,layer_id))
            else:
                owned=conn.execute('SELECT * FROM warehouse_ownership WHERE reservation_id=? AND layer_id=?',(reservation_id,layer_id)).fetchone()
                if not owned or owned['inbound_units']!=quantity:raise WarehouseStockError('Магазину не принадлежит эта входящая партия')
                conn.execute('UPDATE warehouse_ownership SET inbound_units=0 WHERE reservation_id=? AND layer_id=?',(reservation_id,layer_id))
            movement_key='shared:'+request_key+':'+str(layer_id)
            cursor=conn.execute("INSERT INTO inventory_movements(event_key,movement_type,nm_id,chrt_id,quantity,ready_delta,inbound_delta,source_task_key,movement_date,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,'applied',?)",(movement_key,'shared_'+kind,layer['nm_id'],layer['chrt_id'],quantity,-quantity if kind=='commit' else 0,quantity if kind=='commit' else -quantity,source_ref,document_date,now()))
            conn.execute(f'UPDATE finished_goods_cost_layers SET {from_location}_units={from_location}_units-?,{to_location}_units={to_location}_units+?,updated_at=? WHERE id=?',(quantity,quantity,now(),layer_id))
            from .production import _apply_pipeline_delta
            _apply_pipeline_delta(conn,layer['nm_id'],'',-quantity if kind=='commit' else 0,quantity if kind=='commit' else -quantity)
            conn.execute('UPDATE inventory_movements SET goods_cost_rub=?,goods_unit_cost_rub=? WHERE id=?',(layer['cost_rub'],provenance['unit_cost_rub'],cursor.lastrowid))
            if kind=='commit':conn.execute('INSERT INTO warehouse_ownership(reservation_id,layer_id,shop_id,inbound_units) VALUES(?,?,?,?)',(reservation_id,layer_id,reservation['shop_id'],quantity))
            else:conn.execute('UPDATE warehouse_ownership SET wb_units=? WHERE reservation_id=? AND layer_id=?',(quantity,reservation_id,layer_id))
            conn.execute("INSERT INTO finished_goods_fifo_allocations(movement_id,layer_id,units,amount_rub,from_location,to_location,status,created_at) VALUES(?,?,?,?,?,?,'applied',?)",(cursor.lastrowid,layer_id,quantity,layer['cost_rub'],from_location,to_location,now()))
            allocations.append(dict(layer_id=layer_id,movement_id=cursor.lastrowid,units=quantity,cost_rub=layer['cost_rub']));total+=layer['cost_rub']
        target={'commit':'inbound','receipt':'wb','release':'released'}[kind]
        conn.execute('UPDATE warehouse_reservations SET status=?,revision=revision+1 WHERE id=?',(target,reservation_id))
        result=dict(reservation_id=reservation_id,shop_id=reservation['shop_id'],shop_identity=reservation['shop_identity'],status=target,revision=revision+1,cost_rub=round(total,2),allocations=allocations)
        _receipt(conn,reservation['shop_id'],reservation['shop_identity'],kind,source_ref,request_key,fingerprint,reservation_id,document_date,result,actor)
        return result
