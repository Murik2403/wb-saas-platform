"""Document-backed Ozon cost ledger; no WB identity or physical-stock mutations."""
import csv
import hashlib
import io
import json
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo
from .core import connect

MSK = ZoneInfo('Europe/Moscow')
COLUMNS = ('external_event_id','accrued_at','movement_at','kind','sku','posting_ref','return_of','quantity','revenue_rub','commission_rub','logistics_rub','other_rub','source_total_rub','currency','document_ref')


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def ensure_schema(conn):
    tables = {
        'ozon_document_requests': 'id INTEGER PRIMARY KEY AUTOINCREMENT,source_key TEXT NOT NULL UNIQUE,payload_json TEXT NOT NULL,actor TEXT NOT NULL,created_at TEXT NOT NULL',
        'ozon_document_events': 'id INTEGER PRIMARY KEY AUTOINCREMENT,source_key TEXT NOT NULL UNIQUE,payload_json TEXT NOT NULL,actor TEXT NOT NULL,created_at TEXT NOT NULL',
        'ozon_document_cost_lots': 'id INTEGER PRIMARY KEY AUTOINCREMENT,source_key TEXT NOT NULL UNIQUE,payload_json TEXT NOT NULL,actor TEXT NOT NULL,created_at TEXT NOT NULL',
        'ozon_cost_projections': 'id INTEGER PRIMARY KEY AUTOINCREMENT,source_key TEXT NOT NULL UNIQUE,payload_json TEXT NOT NULL,actor TEXT NOT NULL,created_at TEXT NOT NULL',
        'ozon_raw_source_versions': 'id INTEGER PRIMARY KEY AUTOINCREMENT,source_key TEXT NOT NULL UNIQUE,payload_json TEXT NOT NULL,actor TEXT NOT NULL,created_at TEXT NOT NULL',
    }
    for table, fields in tables.items():
        conn.execute(f'CREATE TABLE IF NOT EXISTS {table} ({fields})')
        for verb in ['UPDATE','DELETE']:
            conn.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{verb.lower()} BEFORE {verb} ON {table} BEGIN SELECT RAISE(ABORT,'Ozon document history is immutable'); END")
        conn.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_replace BEFORE INSERT ON {table} WHEN EXISTS(SELECT 1 FROM {table} WHERE id=NEW.id OR source_key=NEW.source_key) BEGIN SELECT RAISE(ABORT,'Ozon document history is immutable'); END")


def text(value, label, limit=160):
    if not isinstance(value,str) or not value.strip() or len(value)>limit or any(ord(c)<32 or 0xD800<=ord(c)<=0xDFFF for c in value):
        raise ValueError(f'{label}: заполните текст до {limit} символов.')
    return value.strip()


def money(value, *, cost=False):
    if isinstance(value,bool) or value is None or str(value).strip()=='': raise ValueError('Сумма отсутствует; неизвестную сумму нельзя заменять нулём.')
    try: result=Decimal(str(value))
    except InvalidOperation: raise ValueError('Неверная сумма RUB.') from None
    if not result.is_finite() or abs(result)>Decimal('1000000000000') or result.as_tuple().exponent < (-6 if cost else -2):
        raise ValueError('Сумма RUB должна быть конечной, до 2 знаков (ставка партии до 6).')
    return result


def timestamp(value):
    if not isinstance(value,str): raise ValueError('Укажите время с часовым поясом.')
    try: result=datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError: raise ValueError('Укажите время ISO с часовым поясом.') from None
    if result.tzinfo is None or result.year<2022 or result>datetime.now(timezone.utc):
        raise ValueError('Время должно быть прошедшим, с часовым поясом, начиная с 2022 года.')
    return result.astimezone(timezone.utc).isoformat()


def sku_id(value):
    if not isinstance(value,str) or not re.fullmatch('[1-9][0-9]{0,19}',value): raise ValueError('Укажите отдельный числовой SKU Ozon.')
    return value


def quantity(value, allow_zero=False):
    if isinstance(value,bool) or not re.fullmatch('[0-9]+',str(value)): raise ValueError('Количество должно быть целым.')
    result=int(value)
    if result < (0 if allow_zero else 1) or result>1000000: raise ValueError('Проверьте количество единиц.')
    return result


def _parse_movements(csv_text, namespace):
    namespace=text(namespace,'Источник/реестр',80)
    if not isinstance(csv_text,str):raise ValueError('CSV должен быть текстом.')
    try:encoded_csv=csv_text.encode('utf-8')
    except UnicodeEncodeError:raise ValueError('CSV должен содержать корректный Unicode.') from None
    if len(encoded_csv)>1000000:raise ValueError('CSV должен быть до 1 МБ.')
    reader=csv.DictReader(io.StringIO(csv_text.lstrip('\ufeff')))
    if reader.fieldnames is None or set(reader.fieldnames)!=set(COLUMNS) or len(reader.fieldnames)!=len(COLUMNS):
        raise ValueError('Используйте CSV с полным набором колонок шаблона Ozon.')
    prepared={}; duplicates=0
    for row_number,row in enumerate(reader,2):
        if row_number>5001: raise ValueError('В одном CSV допускается до 5000 строк.')
        if None in row or any(value is None for value in row.values()): raise ValueError(f'Строка {row_number}: нарушена структура CSV.')
        event=text(row['external_event_id'],'Идентификатор операции')
        kind=row['kind']
        if kind not in {'sale','return','fee'}: raise ValueError('Вид операции: sale, return или fee.')
        if row['currency']!='RUB': raise ValueError('Поддерживается только RUB; конвертация не выполняется.')
        financial=timestamp(row['accrued_at'])
        values={key:money(row[key]) for key in ('revenue_rub','commission_rub','logistics_rub','other_rub','source_total_rub')}
        if sum((values[key] for key in ('revenue_rub','commission_rub','logistics_rub','other_rub')),Decimal(0))!=values['source_total_rub']:
            raise ValueError(f'Строка {row_number}: сумма компонентов не совпадает с итогом документа.')
        qty=quantity(row['quantity'],allow_zero=kind=='fee')
        sku=sku_id(row['sku']) if kind!='fee' else ''
        posting=text(row['posting_ref'],'Отправление') if kind!='fee' else row['posting_ref'].strip()
        movement=timestamp(row['movement_at']) if kind!='fee' else ''
        original=text(row['return_of'],'Исходная продажа') if kind=='return' else ''
        if kind=='sale' and (values['revenue_rub']<0 or row['return_of']): raise ValueError('Продажа: неотрицательная выручка, без ссылки на возврат.')
        if kind=='return' and values['revenue_rub']>0: raise ValueError('Возврат: неположительная выручка.')
        if kind=='fee' and (qty or values['revenue_rub'] or row['sku'] or row['movement_at'] or row['return_of']): raise ValueError('Сбор: количество и выручка 0, без движения товара.')
        data={'external_event_id':event,'namespace':namespace,'kind':kind,'accrued_at':financial,'movement_at':movement,'sku':sku,'posting_ref':posting,'return_of':original,'quantity':qty,'currency':'RUB','document_ref':text(row['document_ref'],'Документ'),**{key:str(value) for key,value in values.items()}}
        key=canonical([namespace,event])
        if key in prepared:
            if prepared[key]!=data: raise ValueError('Один ID операции содержит разные значения; ничего не сохранено.')
            duplicates+=1
        prepared[key]=data
    if not prepared: raise ValueError('CSV не содержит операций; это не подтверждение нулевых начислений.')
    return list(prepared.values()),duplicates


def parse_movements(csv_text,namespace):
    try:return _parse_movements(csv_text,namespace)
    except csv.Error as exc:raise ValueError('Нарушена структура CSV или поле превышает допустимый размер.') from exc


def capture_source_snapshot(conn,snapshot):
    """Preserve source versions; no guessed normalization of uncertain API shapes."""
    ensure_schema(conn)
    key=digest(snapshot)
    if not conn.execute('SELECT 1 FROM ozon_raw_source_versions WHERE source_key=?',(key,)).fetchone():
        conn.execute('INSERT INTO ozon_raw_source_versions(source_key,payload_json,actor,created_at) VALUES(?,?,?,?)',(key,canonical(snapshot),'Ozon Seller API read-only',datetime.now(timezone.utc).isoformat()))


def project(lots,events):
    pool=[]; sales={}; results={}
    timeline=[(lot['received_at'],0,str(index),lot) for index,lot in enumerate(lots)]
    timeline += [(event['movement_at'],1 if event['kind']=='sale' else 2,canonical([event['namespace'],event['external_event_id']]),event) for event in events if event['kind']!='fee']
    for when,kind,key,row in sorted(timeline,key=lambda entry:entry[:3]):
        if kind==0:
            pool.append({'namespace':row['namespace'],'sku':row['sku'],'quantity':row['quantity'],'cost':row['unit_cost_rub'],'origin':row['source_key'],'fifo_at':when,'fifo_sequence':int(key),'available_at':when,'remaining_cost_rub':str(Decimal(row['unit_cost_rub'])*row['quantity'])}); continue
        result={'event_id':row['external_event_id'],'namespace':row['namespace'],'sku':row['sku'],'kind':row['kind'],'quantity':row['quantity'],'known_units':0,'unknown_units':row['quantity'],'known_cogs_rub':'0','allocations':[],'errors':[]}
        if kind==1:
            remaining=row['quantity']; cost=Decimal(0);known=0
            for lot in sorted(pool,key=lambda entry:(entry['fifo_at'],entry['fifo_sequence'],entry['available_at'],entry['origin'])):
                if lot['namespace']!=row['namespace'] or lot['sku']!=row['sku'] or lot['available_at']>when: continue
                take=min(remaining,lot['quantity'])
                if not take: continue
                prior_quantity=lot['quantity'];lot['quantity']-=take; remaining-=take
                allocated=None
                if lot['remaining_cost_rub'] is not None:
                    prior_cost=Decimal(lot['remaining_cost_rub'])
                    allocated=prior_cost if take==prior_quantity else prior_cost*take/prior_quantity
                    lot['remaining_cost_rub']=str(prior_cost-allocated)
                    cost+=allocated;known+=take
                result['allocations'].append({'lot_ref':lot['origin'],'quantity':take,'unit_cost_rub':lot['cost'],'allocated_cost_rub':str(allocated) if allocated is not None else None,'fifo_at':lot['fifo_at'],'fifo_sequence':lot['fifo_sequence'],'original_lots':lot.get('original_lots',[])})
                if not remaining: break
            result.update(known_units=known,unknown_units=row['quantity']-known,known_cogs_rub=str(cost))
            if result['unknown_units']: result['errors'].append('Недостаточно подтверждённых исторических партий Ozon.')
            sales[key]={'row':row,'result':result,'returned_total':0}
        else:
            original=sales.get(canonical([row['namespace'],row['return_of']]))
            if not original or original['row']['sku']!=row['sku'] or original['row']['posting_ref']!=row['posting_ref']:
                result['errors'].append('Возврат не сопоставлен с исходной продажей этого SKU/отправления.')
            elif row['quantity']>original['row']['quantity']-original['returned_total']:
                result['errors'].append('Количество возвратов превышает исходную продажу.')
            else:
                previous_returned=original['returned_total']
                original['returned_total']+=row['quantity']
                if original['result']['unknown_units']:
                    result['errors'].append('Себестоимость исходной продажи неполная: стоимость возвращённых единиц не подтверждена.')
                    pool.append({'namespace':row['namespace'],'sku':row['sku'],'quantity':row['quantity'],'cost':None,'origin':'unknown-return:'+key,'fifo_at':when,'fifo_sequence':len(lots)+len(results),'available_at':when,'remaining_cost_rub':None})
                else:
                    # Without physical serial identity, use proportional original-sale
                    # cost rather than asserting which priced unit was returned.
                    original_cost=Decimal(original['result']['known_cogs_rub'])
                    cumulative_after=original_cost*original['returned_total']/original['row']['quantity']
                    cumulative_before=original_cost*previous_returned/original['row']['quantity']
                    cost=-(cumulative_after-cumulative_before)
                    rate=-cost/row['quantity']
                    refs=[{'lot_ref':entry['lot_ref'],'original_units':entry['quantity'],'original_unit_cost_rub':entry['unit_cost_rub'],'fifo_at':entry['fifo_at'],'fifo_sequence':entry['fifo_sequence'],'allocated_cost_rub':entry['allocated_cost_rub'],'parent_lots':entry.get('original_lots',[])} for entry in original['result']['allocations']]
                    result.update(known_units=row['quantity'],unknown_units=0,known_cogs_rub=str(cost),cost_method='proportional_original_sale',allocations=refs)
                    pool.append({'namespace':row['namespace'],'sku':row['sku'],'quantity':row['quantity'],'cost':str(rate),'origin':'return:'+key,'fifo_at':min(entry['fifo_at'] for entry in refs),'fifo_sequence':min(entry['fifo_sequence'] for entry in refs),'available_at':when,'remaining_cost_rub':str(-cost),'original_sale':row['return_of'],'original_lots':refs})
        results[key]=result
    return {'events':list(results.values()),'available_lots':pool}


def _rows(conn,table):
    return [json.loads(row['payload_json']) for row in conn.execute(f'SELECT payload_json FROM {table} ORDER BY id')]


def _projection(conn,actor):
    lots=_rows(conn,'ozon_document_cost_lots');events=_rows(conn,'ozon_document_events')
    key=digest({'lots':lots,'events':events})
    found=conn.execute('SELECT id FROM ozon_cost_projections WHERE source_key=?',(key,)).fetchone()
    if found:return int(found['id'])
    cursor=conn.execute('INSERT INTO ozon_cost_projections(source_key,payload_json,actor,created_at) VALUES(?,?,?,?)',(key,canonical(project(lots,events)),actor,datetime.now(timezone.utc).isoformat()))
    return int(cursor.lastrowid)


def preview_import(csv_text,namespace):
    rows,duplicates=parse_movements(csv_text,namespace)
    with connect() as conn:
        ensure_schema(conn)
        existing={row['source_key']:row['payload_json'] for row in conn.execute('SELECT source_key,payload_json FROM ozon_document_events')}
    new=0
    for row in rows:
        key=canonical([row['namespace'],row['external_event_id']])
        if key in existing:
            if existing[key]!=canonical(row):raise ValueError('ID уже сохранён с другими значениями; перезапись истории запрещена.')
            duplicates+=1
        else:new+=1
    return {'rows':rows,'new_count':new,'duplicates':duplicates,'fingerprint':digest(rows),'source':'explicit_manual_document_csv','net_accrual_rub':str(sum((Decimal(row['source_total_rub']) for row in rows),Decimal(0)))}


def confirm_import(csv_text,namespace,expected_fingerprint,actor):
    rows,duplicates=parse_movements(csv_text,namespace);actor=text(actor,'Автор')
    if expected_fingerprint!=digest(rows):raise ValueError('CSV изменился после предпросмотра.')
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE');ensure_schema(conn)
        new=0
        for row in rows:
            key=canonical([row['namespace'],row['external_event_id']]);encoded=canonical(row)
            found=conn.execute('SELECT payload_json FROM ozon_document_events WHERE source_key=?',(key,)).fetchone()
            if found:
                if found['payload_json']!=encoded:raise ValueError('ID уже сохранён с другими значениями; ничего не изменено.')
                duplicates+=1;continue
            conn.execute('INSERT INTO ozon_document_events(source_key,payload_json,actor,created_at) VALUES(?,?,?,?)',(key,encoded,actor,datetime.now(timezone.utc).isoformat()));new+=1
        revision=_projection(conn,actor)
    return {'imported':new,'duplicates':duplicates,'projection_revision':revision}


def add_cost_lot(*,namespace,sku,received_at,quantity_units,unit_cost_rub,document_ref,reason,request_key,actor,external_lot_id=None):
    namespace=text(namespace,'Магазин/реестр',80)
    actor=text(actor,'Автор');sku=sku_id(sku);received_at=timestamp(received_at)
    count=quantity(quantity_units);cost=money(unit_cost_rub,cost=True)
    if cost<0:raise ValueError('Себестоимость партии не может быть отрицательной.')
    try:
        if str(uuid.UUID(request_key))!=request_key:raise ValueError()
    except (ValueError,TypeError,AttributeError):raise ValueError('Неверный ключ операции.') from None
    external_lot_id=text(external_lot_id if external_lot_id is not None else request_key,'ID строки партии',120)
    lot_key=canonical([namespace,external_lot_id])
    data={'source_key':lot_key,'external_lot_id':external_lot_id,'namespace':namespace,'sku':sku,'received_at':received_at,'quantity':count,'unit_cost_rub':str(cost),'currency':'RUB','document_ref':text(document_ref,'Документ'),'reason':text(reason,'Причина',500)}
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE');ensure_schema(conn)
        replay=conn.execute('SELECT payload_json FROM ozon_document_requests WHERE source_key=?',(request_key,)).fetchone()
        if replay and replay['payload_json']!=canonical(data):raise ValueError('Ключ операции использован для другой партии.')
        if not replay:
            conn.execute('INSERT INTO ozon_document_requests(source_key,payload_json,actor,created_at) VALUES(?,?,?,?)',(request_key,canonical(data),actor,datetime.now(timezone.utc).isoformat()))
        found=conn.execute('SELECT id,payload_json FROM ozon_document_cost_lots WHERE source_key=?',(lot_key,)).fetchone()
        if found:
            if found['payload_json']!=canonical(data):raise ValueError('Ключ использован для другой партии.')
            return {'id':int(found['id']),'projection_revision':_projection(conn,actor)}
        cursor=conn.execute('INSERT INTO ozon_document_cost_lots(source_key,payload_json,actor,created_at) VALUES(?,?,?,?)',(lot_key,canonical(data),actor,datetime.now(timezone.utc).isoformat()))
        return {'id':int(cursor.lastrowid),'projection_revision':_projection(conn,actor)}


def pnl(start,end,namespace="main"):
    namespace=text(namespace,'Магазин/реестр',80)
    with connect() as conn:
        ensure_schema(conn)
        conn.execute('BEGIN')
        events=_rows(conn,'ozon_document_events');lots=_rows(conn,'ozon_document_cost_lots')
        saved=conn.execute('SELECT id,payload_json,created_at FROM ozon_cost_projections ORDER BY id DESC LIMIT 1').fetchone()
        raw_count=conn.execute('SELECT COUNT(*) FROM ozon_raw_source_versions').fetchone()[0]
    projection=json.loads(saved['payload_json']) if saved else project(lots,events)
    projected={canonical([row['namespace'],row['event_id']]):row for row in projection['events']}
    selected=[row for row in events if row['namespace']==namespace and start<=datetime.fromisoformat(row['accrued_at']).astimezone(MSK).date().isoformat()<=end]
    totals={key:Decimal(0) for key in ('revenue_rub','commission_rub','logistics_rub','other_rub','source_total_rub')}
    cogs=Decimal(0);unknown=0;known=0;lines=[]
    for row in selected:
        for key in totals:totals[key]+=Decimal(row[key])
        cost=projected.get(canonical([row['namespace'],row['external_event_id']]))
        if cost:cogs+=Decimal(cost['known_cogs_rub']);unknown+=cost['unknown_units'];known+=cost['known_units']
        lines.append({**row,'historical_cost':cost})
    reconciled=sum((totals[key] for key in ('revenue_rub','commission_rub','logistics_rub','other_rub')),Decimal(0))==totals['source_total_rub']
    return {'source':'explicit_manual_document_csv','api_normalization_verified':False,'marketplace_source_complete':False,'period':{'start':start,'end':end,'timezone':'Europe/Moscow','basis':'accrual_timestamp'},'movement_basis':'documented_movement_timestamp','return_cost_policy':'proportional_original_sale_without_serial_identity','returned_fifo_age_policy':'earliest_original_age_available_from_actual_return','loaded':bool(selected),'totals':{key:str(value) for key,value in totals.items()},'sale_revenue_rub':str(sum((Decimal(row['revenue_rub']) for row in selected if row['kind']=='sale'),Decimal(0))),'return_revenue_rub':str(sum((Decimal(row['revenue_rub']) for row in selected if row['kind']=='return'),Decimal(0))),'components_reconciled':reconciled,'known_cogs_rub':format(cogs.quantize(Decimal('0.01'),rounding=ROUND_HALF_UP),'f'),'historical_cogs_rub':format(cogs.quantize(Decimal('0.01'),rounding=ROUND_HALF_UP),'f') if not unknown and selected else None,'result_after_fifo_rub':format((totals['source_total_rub']-cogs).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP),'f') if selected and not unknown and reconciled else None,'unknown_cost_units':unknown,'known_cost_units':known,'cost_complete':bool(selected) and not unknown,'projection_revision':saved['id'] if saved else None,'lines':lines,'namespace':namespace,'cost_lots':[lot for lot in lots if lot['namespace']==namespace],'api_source_versions':raw_count,'note':'Результат только по внесённым документам Ozon, до налогов и внешних расходов. Это не подтверждение полноты финансовых данных Ozon API; физические остатки не изменяются.'}
