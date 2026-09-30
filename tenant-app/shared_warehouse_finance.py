"""Explicit canonical document to seller-proven financial row, no stock mutation."""
import json
import re
from datetime import date
import config
import shared_warehouse_setup as setup
from db.core import connect
from db import financial_source as source, shared_warehouse as warehouse, shop_workspaces as shops

FIELDS = ('shop_id','identity','rrd_id','document_key','source_fingerprint',
          'catalog_fingerprint','external_nm_id','external_chrt_id','event_type',
          'units','cost_rub','source_kind','source_proof','actor','created_at')


def verified_period_errors(errors,financial):
    verified=bool(financial.get('financial_period_complete') is True and
      financial.get('cogs_complete') is True and financial.get('cogs_quality')=='exact' and
      financial.get('exact_unit_coverage_percent')==100 and financial.get('exact_units',0)>0 and
      not financial.get('shared_fifo_manual_source_rows',0))
    return [value for value in errors if not (value=='shared_fifo_pending' and verified)]


def ensure_schema(conn, *, projection=False):
    table='warehouse_financial_projection' if projection else 'warehouse_financial_bindings'
    conn.execute(f'''CREATE TABLE IF NOT EXISTS {table}(
      shop_id INTEGER NOT NULL,identity TEXT NOT NULL,rrd_id INTEGER NOT NULL,
      document_key TEXT NOT NULL,source_fingerprint TEXT NOT NULL,
      catalog_fingerprint TEXT NOT NULL,external_nm_id INTEGER NOT NULL,
      external_chrt_id INTEGER NOT NULL,event_type TEXT NOT NULL,units INTEGER NOT NULL,
      cost_rub REAL NOT NULL,source_kind TEXT NOT NULL,source_proof TEXT,
      actor TEXT NOT NULL,created_at TEXT NOT NULL,
      PRIMARY KEY(shop_id,rrd_id),UNIQUE(document_key))''')
    conn.execute(f'CREATE INDEX IF NOT EXISTS {table}_rrd ON {table}(rrd_id)')
    for op in ('UPDATE','DELETE'):
        conn.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{op.lower()} BEFORE {op} ON {table} BEGIN SELECT RAISE(ABORT,'financial binding immutable'); END")
    conn.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_replace BEFORE INSERT ON {table} WHEN EXISTS(SELECT 1 FROM {table} WHERE (shop_id=NEW.shop_id AND rrd_id=NEW.rrd_id) OR document_key=NEW.document_key) BEGIN SELECT RAISE(ABORT,'financial binding immutable'); END")


def initialize():
    setup.initialize()
    with connect() as conn:ensure_schema(conn)


def _variant(catalog,row):
    """Never use UI input as evidence of the imported financial variant."""
    nm_id=int(row['nm_id']);raw=json.loads(row['raw_json'] or '{}')
    card=catalog.execute('SELECT raw_json FROM products_catalog WHERE nm_id=?',(nm_id,)).fetchone()
    if not card:raise warehouse.WarehouseConflict('Товар отсутствует в подтверждённом каталоге магазина')
    card_raw=json.loads(card['raw_json']);sizes=card_raw.get('sizes') or []
    ids={size.get('chrtID',size.get('chrtId',size.get('chrt_id'))) for size in sizes}
    ids.discard(None)
    exact=raw.get('chrt_id',raw.get('chrtID',raw.get('chrtId')))
    basis='financial_raw_variant'
    if exact is None:
        barcode=raw.get('barcode') or raw.get('Barcode')
        matching=[size.get('chrtID',size.get('chrtId',size.get('chrt_id'))) for size in sizes if barcode and str(barcode) in [str(v) for v in size.get('skus',[])]]
        if len(set(matching))==1:exact=matching[0];basis='financial_raw_barcode'
        elif len(ids)==1:exact=next(iter(ids));basis='single_catalog_variant'
        else:raise warehouse.WarehouseConflict('Вариант строки реализации неоднозначен; нужна исходная карточка или штрихкод')
    if type(exact)!=int and (not isinstance(exact,str) or not re.fullmatch(r'[0-9]{1,16}',exact)):
        raise warehouse.WarehouseConflict('Некорректный вариант источника')
    try:exact=int(exact)
    except (ValueError,TypeError):raise warehouse.WarehouseConflict('Некорректный вариант источника')
    from db.wip import validate_catalog_identity
    validate_catalog_identity(catalog,nm_id,exact,True)
    return exact,warehouse.digest(card_raw),basis


def _proof(conn,catalog,metadata,shop_id,rrd_id,document_key,manual,source_proof):
    if source_proof is not None:source_proof=warehouse.text(source_proof,500)
    row=catalog.execute('SELECT * FROM financial_report WHERE rrd_id=?',(rrd_id,)).fetchone()
    if not row:raise warehouse.WarehouseScopeError('Строка реализации выбранного магазина не найдена')
    row=dict(row);origin=source.current(catalog,row)
    identity=metadata['identity']
    if origin and origin['identity'] and origin['identity']!=identity:
        raise warehouse.WarehouseConflict('Источник реализации принадлежит другому продавцу')
    if not origin or not origin['identity']:
        if not manual or not source_proof:raise warehouse.WarehouseConflict('У старой строки нет подтверждённого продавца; нужно явное ручное подтверждение источника')
        source_kind='manual_legacy'
    else:
        if manual:raise warehouse.WarehouseConflict('Подтверждённая API строка не требует ручного присвоения продавца')
        source_kind='wb_api'
    doc=conn.execute("SELECT * FROM warehouse_documents WHERE request_key=? AND shop_id=? AND shop_identity=? AND kind IN ('sale','return')",(document_key,shop_id,identity)).fetchone()
    if not doc:raise warehouse.WarehouseScopeError('Нужен исходный документ продажи или возврата этого магазина')
    from db.financial_fifo_reconciliation import _event_type
    kind=_event_type(row)
    if not kind or kind!=doc['kind']:raise warehouse.WarehouseConflict('Тип операции не совпадает с документом')
    result=json.loads(doc['result_json']);allocations=result['allocations'];units=sum(int(v['units']) for v in allocations)
    if abs(int(row['quantity']))!=units or units<=0:
        raise warehouse.WarehouseConflict('Количество строки и документа должно совпасть; объединение нескольких строк автоматически не выполняется')
    source_date=str(row['sale_date'] or row['operation_date'])[:10]
    if date.fromisoformat(source_date).isoformat()!=doc['document_date']:
        raise warehouse.WarehouseConflict('Дата исходной продажи или возврата не совпадает с документом')
    chrt,catalog_fingerprint,variant_basis=_variant(catalog,row)
    reservation=conn.execute('SELECT mapping_id FROM warehouse_reservations WHERE id=?',(doc['reservation_id'],)).fetchone()
    mapping=conn.execute('SELECT * FROM warehouse_mappings WHERE id=? AND active=1',(reservation['mapping_id'],)).fetchone()
    if not mapping or (mapping['shop_id'],mapping['shop_identity'],mapping['external_nm_id'],mapping['external_chrt_id'])!=(shop_id,identity,row['nm_id'],chrt):
        raise warehouse.WarehouseConflict('Вариант реализации не соответствует подтверждённому производственному SKU')
    for allocation in allocations:
        layer=conn.execute('SELECT source_type FROM finished_goods_cost_layers WHERE id=?',(allocation['layer_id'],)).fetchone()
        if not layer or layer['source_type'] not in {'production','production_wip','procurement_product'}:
            raise warehouse.WarehouseConflict('Исходная партия не имеет подтверждённой исторической себестоимости')
    return dict(shop_id=shop_id,identity=identity,rrd_id=rrd_id,document_key=document_key,
      source_fingerprint=source.fingerprint(row),catalog_fingerprint=catalog_fingerprint,
      external_nm_id=row['nm_id'],external_chrt_id=chrt,event_type=kind,units=units,
      cost_rub=result['cost_rub'],source_kind=source_kind,source_proof=source_proof,
      snapshot_revision=metadata['snapshot_revision'],variant_basis=variant_basis,
      preliminary=source_kind=='manual_legacy')


def preview(shop_id,rrd_id,document_key,manual,source_proof,account,role):
    initialize()
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        with setup.catalog_connection(conn,shop_id,account,role) as (catalog,metadata):
            result=_proof(conn,catalog,metadata,shop_id,rrd_id,document_key,manual,source_proof)
        return dict(result,fingerprint=warehouse.digest(result))


def rows(shop_id,account,role):
    """Bounded display data without raw WB payloads or credentials."""
    initialize()
    from db.financial_fifo_reconciliation import _event_type
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        with setup.catalog_connection(conn,shop_id,account,role) as (catalog,metadata):
            data=catalog.execute('SELECT * FROM financial_report ORDER BY operation_date DESC,rrd_id DESC LIMIT 201').fetchall()
            result=[]
            for raw in data[:200]:
                row=dict(raw);kind=_event_type(row)
                if not kind:continue
                origin=source.current(catalog,row)
                try:chrt,_,basis=_variant(catalog,row)
                except (ValueError,TypeError):chrt=None;basis='ambiguous'
                binding=conn.execute('SELECT document_key,source_kind FROM warehouse_financial_bindings WHERE shop_id=? AND rrd_id=?',(shop_id,row['rrd_id'])).fetchone()
                result.append(dict(rrd_id=row['rrd_id'],nm_id=row['nm_id'],chrt_id=chrt,variant_basis=basis,
                  operation_date=row['operation_date'],sale_date=row['sale_date'],units=abs(int(row['quantity'])),event_type=kind,
                  source_kind=('wb_api' if origin['identity']==metadata['identity'] else 'seller_mismatch') if origin and origin['identity'] else 'legacy_unverified',
                  document_key=binding['document_key'] if binding else None,
                  preliminary=not origin or origin['identity']!=metadata['identity']))
            return dict(shop_id=shop_id,identity=metadata['identity'],snapshot_revision=metadata['snapshot_revision'],rows=result,truncated=len(data)>200)


def confirm(shop_id,rrd_id,document_key,manual,source_proof,expected,account,role,request_key):
    if role not in {'owner','manager'}:raise warehouse.WarehouseScopeError('Финансовое сопоставление подтверждает владелец или управляющий')
    initialize()
    body=dict(rrd_id=rrd_id,document_key=document_key,manual=manual,source_proof=source_proof,fingerprint=expected)
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        scope=setup.access(conn,shop_id,account,role,True)
        if shop_id:
            active=conn.execute('SELECT * FROM shop_workspaces WHERE id=? AND active=1',(shop_id,)).fetchone()
            if not active or active['account_identity']!=scope['identity']:raise warehouse.WarehouseScopeError('Продавец магазина изменился')
        old=setup._replay(conn,request_key,shop_id,scope['identity'],account,'financial_binding',body)
        if old is not None:return old
        with setup.catalog_connection(conn,shop_id,account,role,writable=bool(shop_id)) as (catalog,metadata):
            proof=_proof(conn,catalog,metadata,shop_id,rrd_id,document_key,manual,source_proof)
            if warehouse.digest(proof)!=expected:raise warehouse.WarehouseConflict('Источник, магазин или документ изменился; повторите проверку')
            value={key:proof[key] for key in FIELDS if key not in {'actor','created_at'}}
            value.update(actor=account,created_at=warehouse.now())
            placeholders=','.join('?' for _ in FIELDS)
            try:conn.execute(f"INSERT INTO warehouse_financial_bindings({','.join(FIELDS)}) VALUES({placeholders})",tuple(value[k] for k in FIELDS))
            except __import__('sqlite3').IntegrityError as exc:raise warehouse.WarehouseConflict('Строка или документ уже сопоставлены') from exc
            if shop_id:
                ensure_schema(catalog,projection=True)
                catalog.execute(f"INSERT INTO warehouse_financial_projection({','.join(FIELDS)}) VALUES({placeholders})",tuple(value[k] for k in FIELDS))
                catalog.commit()
                snapshot=catalog.serialize()
                shops.store_snapshot_in_transaction(conn,shop_id,metadata['snapshot_revision'],snapshot,
                  identity=scope['identity'],source_errors=json.loads(active['sync_quality_json']))
            result=dict(proof,applied_revision=None if shop_id==0 else metadata['snapshot_revision']+1)
        setup._receipt(conn,request_key,shop_id,scope['identity'],account,'financial_binding',body,result)
        return result


def apply_projection(conn,financial_rows,reconciled):
    """Read-only row-level COGS replacement. No local/central physical writes."""
    tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    table='warehouse_financial_projection' if 'warehouse_financial_projection' in tables else 'warehouse_financial_bindings'
    if table not in tables:return reconciled
    current_identity=source.captured_identity(type('Client',(),{'token':config.get_token()})())
    for raw,result in zip(financial_rows,reconciled):
        binding=conn.execute(f'SELECT * FROM {table} WHERE rrd_id=?'+(' AND shop_id=0' if table=='warehouse_financial_bindings' else ''),(raw['rrd_id'],)).fetchone()
        if not binding:continue
        reason=None
        try:source_matches=source.fingerprint(raw)==binding['source_fingerprint']
        except (ValueError,TypeError):source_matches=False
        if current_identity!=binding['identity']:reason='shared_seller_changed'
        elif not source_matches:reason='shared_source_changed'
        else:
            origin=source.current(conn,raw)
            if binding['source_kind']=='wb_api' and (not origin or origin['identity']!=binding['identity']):reason='shared_source_unverified'
            elif binding['source_kind']=='manual_legacy' and origin and origin['identity'] and origin['identity']!=binding['identity']:reason='shared_source_seller_mismatch'
        if not reason:
            try:chrt,card,_=_variant(conn,raw)
            except (ValueError,TypeError):reason='shared_variant_unverified'
            else:
                if card!=binding['catalog_fingerprint'] or chrt!=binding['external_chrt_id']:reason='shared_catalog_changed'
        if reason:
            result.update(status='unmatched',reason=reason,eligible_fifo_cogs_rub=None,matched_units=0)
            continue
        manual=binding['source_kind']=='manual_legacy'
        result.update(status='manual_source' if manual else 'exact',reason='shared_fifo_manual_source' if manual else 'shared_fifo_verified_source',
          matched_event_ids=[binding['document_key']],matched_units=binding['units'],
          eligible_fifo_cogs_rub=(-1 if binding['event_type']=='return' else 1)*binding['cost_rub'],
          observed_fifo_cost_rub=binding['cost_rub'],layer_sources=['canonical_shared_fifo'])
    return reconciled
