"""Bounded Ozon-only read models. Orders are not sales; documents are not tax receipts."""
from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from threading import Lock
from zoneinfo import ZoneInfo

from db import core
from db.ozon_accounting import ensure_schema as accounting_schema, _collection_verified
from db.ozon_pnl import ensure_schema as document_schema, canonical, money, sku_id, text

MAX_ROWS, MAX_BYTES, MAX_PRODUCTS = 20000, 16 * 1024 * 1024, 500
MSK = ZoneInfo('Europe/Moscow')
_lock = Lock()
_cache = None


def window(start, end):
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first > last or first.year < 2022 or (last-first).days >= 366:
        raise ValueError('Период Ozon: от 1 до 366 дней, начиная с 2022 года.')
    return first, last


def init(conn):
    accounting_schema(conn); document_schema(conn)
    conn.execute('CREATE TABLE IF NOT EXISTS ozon_insights_plan(namespace TEXT NOT NULL,sku TEXT NOT NULL,day TEXT NOT NULL,units INTEGER NOT NULL,PRIMARY KEY(namespace,sku,day))')
    conn.execute('CREATE TABLE IF NOT EXISTS ozon_insights_favorite(namespace TEXT NOT NULL,sku TEXT NOT NULL,PRIMARY KEY(namespace,sku))')
    conn.execute('CREATE TABLE IF NOT EXISTS ozon_insights_revision(id INTEGER PRIMARY KEY CHECK(id=1),rev INTEGER NOT NULL)')
    conn.execute('INSERT OR IGNORE INTO ozon_insights_revision VALUES(1,0)')


def _decoded(payload):
    if len(payload.encode('utf-8')) > MAX_BYTES:
        raise ValueError('Сохранённый источник превышает безопасный объём; сократите источник.')
    return json.loads(payload)


def _document_rows(conn, namespace):
    count, size = conn.execute("SELECT COUNT(*),COALESCE(SUM(length(CAST(payload_json AS BLOB))),0) FROM ozon_document_events WHERE json_extract(payload_json,'$.namespace')=?", (namespace,)).fetchone()
    if count > MAX_ROWS or size > MAX_BYTES: raise ValueError('Документальный реестр превышает безопасный объём отчёта.')
    sql = "SELECT payload_json FROM ozon_document_events WHERE json_extract(payload_json,'$.namespace')=? ORDER BY id LIMIT ?"
    values = conn.execute(sql, (namespace, MAX_ROWS+1)).fetchall()
    if len(values) > MAX_ROWS or sum(len(r[0].encode('utf-8')) for r in values) > MAX_BYTES:
        raise ValueError('Документальный реестр превышает безопасный объём отчёта.')
    return [json.loads(r[0]) for r in values]


def _amount(value):
    return format(Decimal(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP), 'f')


def _valid_sku(value):
    try: return sku_id(str(value)) if not isinstance(value, bool) else None
    except ValueError: return None


def _new_product(sku, offer=''):
    return {'sku': sku, 'offer_id': offer, 'order_units': None, 'sales_units': None,
            'return_units': None, 'revenue_rub': None, 'known_cogs_rub': None,
            'historical_cogs_rub': None, 'result_after_fifo_rub': None,
            'cost_complete': False, 'unknown_cost_units': None}


def _stock_catalog(data):
    """Only literal SKU fields from the saved stocks endpoint; product_id is metadata, never SKU."""
    result = {}; count = 0
    stocks = data.get('stocks', [])
    if not isinstance(stocks, list) or len(stocks)>MAX_ROWS: raise ValueError('Неизвестная или слишком большая коллекция остатков Ozon.')
    offers = {_valid_sku(p.get('product_id')): str(p.get('offer_id') or '') for p in data.get('products', []) if isinstance(p, dict) and _valid_sku(p.get('product_id'))}
    covered_product_ids = set()
    for product in stocks:
        if not isinstance(product, dict): raise ValueError('Неизвестная строка остатков Ozon.')
        children = product.get('stocks', [])
        if not isinstance(children, list): raise ValueError('Неизвестный состав остатков Ozon.')
        for stock in [product]+children:
            count += 1
            if count>MAX_ROWS: raise ValueError('Слишком много SKU в остатках Ozon.')
            if not isinstance(stock, dict): continue
            sku = _valid_sku(stock.get('sku'))
            if not sku: continue
            product_id = _valid_sku(product.get('product_id'))
            offer = str(product.get('offer_id') or offers.get(product_id, ''))
            result.setdefault(sku, _new_product(sku, offer))
            if product_id: covered_product_ids.add(product_id)
    return result, covered_product_ids


def _read(start, end, namespace):
    global _cache
    window(start, end); namespace = text(namespace, 'Реестр Ozon', 80)
    with _lock, core.connect() as conn:
        init(conn)
        conn.commit(); conn.execute('BEGIN')
        saved = conn.execute('SELECT CASE WHEN length(CAST(payload_json AS BLOB))<=? THEN payload_json END,created_at FROM ozon_accounting_snapshots WHERE period_key=?', (MAX_BYTES, start+':'+end)).fetchone()
        if saved and saved[0] is None: raise ValueError('Сохранённый источник превышает безопасный объём.')
        stale = conn.execute('SELECT created_at FROM ozon_accounting_snapshots WHERE start_date<=? AND end_date>=? AND created_at>? ORDER BY created_at DESC LIMIT 1', (end,start,saved[1])).fetchone() if saved else None
        if stale: saved = None  # Unknown order source must not invalidate independent immutable documents.
        source_revision = hashlib.sha256(saved[0].encode()).hexdigest() if saved else None
        doc_revision = conn.execute('SELECT COALESCE(MAX(id),0) FROM ozon_document_events').fetchone()[0]
        projection_row = conn.execute('SELECT id,CASE WHEN length(CAST(payload_json AS BLOB))<=? THEN payload_json END FROM ozon_cost_projections ORDER BY id DESC LIMIT 1', (MAX_BYTES,)).fetchone()
        if projection_row and projection_row[1] is None: raise ValueError('FIFO-проекция превышает безопасный объём.')
        projection_revision = projection_row[0] if projection_row else None
        key = (str(core.DB_PATH), start, end, namespace, source_revision, stale[0] if stale else None, doc_revision, projection_revision)
        if _cache and _cache[0] == key:
            return _cache[1]
        raw = _decoded(saved[0]) if saved else None
        docs = _document_rows(conn, namespace)
        docs = [r for r in docs if start <= datetime.fromisoformat(r['accrued_at']).astimezone(MSK).date().isoformat() <= end]
        projection = _decoded(projection_row[1]) if projection_row else {'events': []}
        if len(projection.get('events', [])) > MAX_ROWS:
            raise ValueError('FIFO-проекция превышает безопасный объём отчёта.')
        costs = {canonical([r['namespace'], r['event_id']]): r for r in projection['events'] if r['namespace']==namespace}
        products, order_days, document_days, months = {}, {}, {}, {}
        blockers = []
        verified = bool(raw and _collection_verified(raw, start, end))
        order_parse = True
        catalog_parse = True
        if raw and (not isinstance(raw, dict) or any(not isinstance(raw.get(k, []), list) for k in ('products', 'fbo', 'fbs'))):
            raise ValueError('Неизвестная структура каталога/отправлений Ozon.')
        if raw:
            if len(raw.get('products', [])) > MAX_ROWS: raise ValueError('Каталог превышает безопасный объём.')
            stock_products, stock_product_ids = _stock_catalog(raw)
            products.update(stock_products)
            for product in raw.get('products', []):
                if not isinstance(product, dict): raise ValueError('Неизвестная строка каталога Ozon.')
                sku = _valid_sku(product.get('sku'))
                if sku: products[sku] = _new_product(sku, str(product.get('offer_id') or ''))
                elif _valid_sku(product.get('product_id')) not in stock_product_ids: catalog_parse = False
            postings = raw.get('fbo', []) + raw.get('fbs', [])
            if len(postings) > MAX_ROWS: raise ValueError('Слишком много отправлений Ozon.')
            unique, conflicts = {}, set()
            for posting in postings:
                if not isinstance(posting, dict):
                    order_parse = False; continue
                identity = posting.get('posting_number')
                if not isinstance(identity, str) or not identity:
                    order_parse = False; continue
                if identity in unique and canonical(unique[identity]) != canonical(posting):
                    conflicts.add(identity); order_parse = False
                unique[identity] = posting
            for identity, posting in unique.items():
                if identity in conflicts: continue
                try:
                    when = datetime.fromisoformat(posting['created_at'].replace('Z', '+00:00'))
                    if when.tzinfo is None: raise ValueError()
                    day = when.astimezone(timezone.utc).date().isoformat()
                    if not start <= day <= end: continue
                    items = posting['products']
                    if not isinstance(items, list) or not items: raise ValueError()
                    prepared = []
                    for item in items:
                        sku = _valid_sku(item.get('sku')); quantity = item.get('quantity')
                        if not sku or isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= 1000000:
                            raise ValueError()
                        prepared.append((sku, quantity))
                    for sku, quantity in prepared:
                        row = products.setdefault(sku, _new_product(sku))
                        row['order_units'] = (row['order_units'] or 0)+quantity
                        order_days[(sku, day)] = order_days.get((sku, day), 0)+quantity
                except (KeyError, ValueError, TypeError): order_parse = False
        unallocated_fees = Decimal(0)
        for event in docs:
            day = datetime.fromisoformat(event['accrued_at']).astimezone(MSK).date().isoformat()
            month = day[:7]
            totals = months.setdefault(month, {'revenue': Decimal(0), 'net': Decimal(0), 'cogs': Decimal(0), 'unknown': 0, 'lines': 0})
            totals['revenue'] += Decimal(event['revenue_rub']); totals['net'] += Decimal(event['source_total_rub']); totals['lines'] += 1
            if event['kind']=='fee':
                unallocated_fees += Decimal(event['source_total_rub']); continue
            sku = event['sku']; row = products.setdefault(sku, _new_product(sku))
            cost = costs.get(canonical([namespace, event['external_event_id']]))
            unknown = cost['unknown_units'] if cost else event['quantity']
            cogs = Decimal(cost['known_cogs_rub']) if cost else Decimal(0)
            totals['cogs'] += cogs; totals['unknown'] += unknown
            row.setdefault('_net', Decimal(0)); row['_net'] += Decimal(event['source_total_rub'])
            row['sales_units'] = (row['sales_units'] or 0)+(event['quantity'] if event['kind']=='sale' else 0)
            row['return_units'] = (row['return_units'] or 0)+(event['quantity'] if event['kind']=='return' else 0)
            row['revenue_rub'] = str(Decimal(row['revenue_rub'] or '0')+Decimal(event['revenue_rub']))
            row['known_cogs_rub'] = str(Decimal(row['known_cogs_rub'] or '0')+cogs)
            row['unknown_cost_units'] = (row['unknown_cost_units'] or 0)+unknown
            cell = document_days.setdefault((sku, day), {'document_sales_units': 0, 'document_revenue_rub': Decimal(0)})
            cell['document_sales_units'] += event['quantity']*(1 if event['kind']=='sale' else -1)
            cell['document_revenue_rub'] += Decimal(event['revenue_rub'])
        for row in products.values():
            row['result_excludes_unallocated_fees'] = True
            if row['order_units'] is None and verified and order_parse: row['order_units'] = 0
            if row['revenue_rub'] is not None:
                row['revenue_rub'] = _amount(row['revenue_rub']); row['known_cogs_rub'] = _amount(row['known_cogs_rub'])
                row['cost_complete'] = row['unknown_cost_units']==0
                row['historical_cogs_rub'] = row['known_cogs_rub'] if row['cost_complete'] else None
                row['result_after_fifo_rub'] = _amount(row['_net']-Decimal(row['known_cogs_rub'])) if row['cost_complete'] else None
                row.pop('_net', None)
        if not raw: blockers.append('API-источник за точный период не загружен.')
        if stale: blockers.append('API-период устарел после перекрывающей загрузки; заказы скрыты до повторной загрузки всего периода.')
        if not verified: blockers.append('Полнота коллекции API за период не подтверждена.')
        if not catalog_parse: blockers.append('В каталоге есть товар без SKU Ozon; product_id не подставляется вместо SKU.')
        if not order_parse: blockers.append('Есть отправления с неизвестным составом/датой либо конфликтом идентичности.')
        if not docs: blockers.append('Нет документальных движений за период; это не нулевая прибыль.')
        data = {'namespace': namespace, 'period': {'start_date': start, 'end_date': end}, 'loaded': bool(raw or docs),
                'source_revision': source_revision, 'projection_revision': projection_revision,
                'unallocated_document_fees_rub': _amount(unallocated_fees) if docs else None,
                'coverage': {'source_collection_complete': verified, 'order_parse_complete': order_parse,
                             'source_snapshot_stale': bool(stale),
                             'catalog_sku_complete': catalog_parse if raw else False,
                             'document_lines_present': bool(docs), 'marketplace_complete': False,
                             'tax_input_confirmed': False}, 'blockers': blockers,
                'items': [products[k] for k in sorted(products)], '_order_days': order_days,
                '_document_days': document_days, '_months': months,
                'order_basis': 'posting_created_at_utc', 'document_basis': 'accrual_timestamp_europe_moscow',
                'note': 'Заказы — созданные отправления, включая отменённые; это не продажи. Прибыль только по документам, до налогов/внешних расходов.'}
        _cache = (key, data)
        return data


def products(start, end, namespace='main'):
    data = _read(start, end, namespace)
    result = {k: copy.deepcopy(v) for k, v in data.items() if not k.startswith('_')}
    result['total_products'] = len(result['items']); result['truncated'] = len(result['items']) > MAX_PRODUCTS
    result['items'] = result['items'][:MAX_PRODUCTS]
    return result


def heatmap(start, end, namespace='main', metric='order_units'):
    if metric not in {'order_units', 'document_sales_units', 'document_revenue_rub'}:
        raise ValueError('Метрика: order_units, document_sales_units, document_revenue_rub.')
    first, last = window(start, end); data = _read(start, end, namespace)
    dates = [(first+timedelta(days=i)).isoformat() for i in range((last-first).days+1)]
    rows = []
    for row in data['items'][:100]:
        values = []
        for day in dates:
            if metric=='order_units':
                value = data['_order_days'].get((row['sku'], day))
                if value is None and data['coverage']['source_collection_complete'] and data['coverage']['order_parse_complete']: value = 0
            else:
                value = data['_document_days'].get((row['sku'], day), {}).get(metric)
                if isinstance(value, Decimal): value = _amount(value)
            values.append(value)
        rows.append({'sku': row['sku'], 'values': values})
    return {'metric': metric, 'basis': data['order_basis'] if metric=='order_units' else data['document_basis'],
            'dates': dates, 'items': rows, 'truncated': len(data['items']) > 100,
            'coverage': data['coverage'], 'blockers': data['blockers'], 'namespace': namespace,
            'metric_note': 'Созданные отправления, включая отменённые; не продажи.' if metric=='order_units' else 'Продажи минус возвраты по внесённым документам; полнота маркетплейса не подтверждена.'}


def _known_sku(conn, sku, namespace):
    found = conn.execute("SELECT 1 FROM ozon_document_events WHERE json_extract(payload_json,'$.namespace')=? AND json_extract(payload_json,'$.sku')=? LIMIT 1", (namespace, sku)).fetchone()
    if found: return True
    # Read only the latest bounded catalog. WB nm_id/product_id is never an Ozon SKU.
    saved = conn.execute('SELECT CASE WHEN length(CAST(payload_json AS BLOB))<=? THEN payload_json END FROM ozon_accounting_snapshots ORDER BY created_at DESC LIMIT 1', (MAX_BYTES,)).fetchone()
    if not saved: return False
    if saved[0] is None: raise ValueError('Каталог превышает безопасный объём.')
    data = _decoded(saved[0])
    stock_products, _ = _stock_catalog(data)
    return sku in stock_products or any(_valid_sku(row.get('sku'))==sku for row in data.get('products', [])) or any(
        _valid_sku(item.get('sku'))==sku for posting in data.get('fbo', [])+data.get('fbs', []) if isinstance(posting, dict)
        for item in (posting.get('products') or []) if isinstance(item, dict))


def save_plan(namespace, sku, day, units):
    namespace = text(namespace, 'Реестр Ozon', 80); sku = sku_id(sku)
    value = date.fromisoformat(day)
    if value.year < 2022 or value > date.today()+timedelta(days=366): raise ValueError('Дата плана вне допустимого диапазона.')
    if units is not None and (isinstance(units, bool) or not isinstance(units, int) or not 0 <= units <= 1000000):
        raise ValueError('План: целое число от 0 до 1000000 либо null для удаления.')
    with core.connect() as conn:
        init(conn)
        if units is not None and not _known_sku(conn, sku, namespace): raise ValueError('Неизвестный SKU Ozon в каталоге или документальном реестре.')
        if units is None:
            conn.execute('DELETE FROM ozon_insights_plan WHERE namespace=? AND sku=? AND day=?', (namespace, sku, day))
        else:
            conn.execute('INSERT INTO ozon_insights_plan VALUES(?,?,?,?) ON CONFLICT(namespace,sku,day) DO UPDATE SET units=excluded.units', (namespace, sku, day, units))
        conn.execute('UPDATE ozon_insights_revision SET rev=rev+1 WHERE id=1')
    return {'saved': True, 'namespace': namespace, 'sku': sku, 'day': day, 'plan_units': units}


def save_favorite(namespace, sku, favorite):
    namespace = text(namespace, 'Реестр Ozon', 80); sku = sku_id(sku)
    if type(favorite) is not bool: raise ValueError('favorite: логическое значение.')
    with core.connect() as conn:
        init(conn)
        if favorite and not _known_sku(conn, sku, namespace): raise ValueError('Неизвестный SKU Ozon.')
        if favorite: conn.execute('INSERT OR IGNORE INTO ozon_insights_favorite VALUES(?,?)', (namespace, sku))
        else: conn.execute('DELETE FROM ozon_insights_favorite WHERE namespace=? AND sku=?', (namespace, sku))
        conn.execute('UPDATE ozon_insights_revision SET rev=rev+1 WHERE id=1')
    return {'saved': True, 'namespace': namespace, 'sku': sku, 'favorite': favorite}


def rnp(start, end, namespace='main'):
    data = products(start, end, namespace)
    with core.connect() as conn:
        init(conn)
        plan_rows = conn.execute('SELECT sku,day,units FROM ozon_insights_plan WHERE namespace=? AND day BETWEEN ? AND ? LIMIT ?', (namespace, start, end, MAX_ROWS+1)).fetchall()
        favorites = {r[0] for r in conn.execute('SELECT sku FROM ozon_insights_favorite WHERE namespace=? LIMIT ?', (namespace, MAX_PRODUCTS+1))}
        revision = conn.execute('SELECT rev FROM ozon_insights_revision WHERE id=1').fetchone()[0]
    if len(plan_rows) > MAX_ROWS or len(favorites) > MAX_PRODUCTS: raise ValueError('План/избранное превышает безопасный объём.')
    plans = {}; by_day = {}
    for sku, day, units in plan_rows:
        plans[sku] = plans.get(sku, 0)+units; by_day.setdefault(sku, []).append({'day': day, 'units': units})
    existing = {row['sku'] for row in data['items']}
    for sku in sorted((set(plans)|favorites)-existing):
        data['items'].append(_new_product(sku))
    data['truncated'] = data['truncated'] or len(data['items']) > MAX_PRODUCTS
    data['items'] = data['items'][:MAX_PRODUCTS]
    for row in data['items']:
        row.update(plan_units=plans.get(row['sku']), fact_units=row['sales_units']-row['return_units'] if row['sales_units'] is not None else None,
                   favorite=row['sku'] in favorites, plan_days=by_day.get(row['sku'], []))
        row['completion_pct'] = _amount(Decimal(row['fact_units'])*100/row['plan_units']) if row['fact_units'] is not None and row['plan_units'] else None
    data.update(rnp_revision=revision, fact_basis='documentary_net_sales_units', forecast=None,
                forecast_note='Прогноз не подставляется при неполных продажах; заказы не используются вместо продаж.')
    return data


def monthly(start, end, namespace='main'):
    first, last = window(start, end); data = _read(start, end, namespace)
    cursor = first.replace(day=1); items = []
    while cursor <= last:
        key = cursor.isoformat()[:7]; values = data['_months'].get(key)
        if values:
            result = values['net']-values['cogs'] if not values['unknown'] else None
            items.append({'month': key, 'document_lines': values['lines'], 'revenue_rub': _amount(values['revenue']),
                          'net_document_total_rub': _amount(values['net']), 'known_cogs_rub': _amount(values['cogs']),
                          'historical_cogs_rub': _amount(values['cogs']) if not values['unknown'] else None,
                          'result_after_fifo_rub': _amount(result) if result is not None else None,
                          'unknown_cost_units': values['unknown'], 'marketplace_complete': False})
        else:
            items.append({'month': key, 'document_lines': 0, 'revenue_rub': None, 'net_document_total_rub': None,
                          'known_cogs_rub': None, 'historical_cogs_rub': None, 'result_after_fifo_rub': None,
                          'unknown_cost_units': None, 'marketplace_complete': False})
        cursor = (cursor.replace(day=28)+timedelta(days=4)).replace(day=1)
    return {'items': items, 'namespace': namespace, 'period': data['period'], 'coverage': data['coverage'],
            'basis': data['document_basis'], 'blockers': data['blockers'], 'note': data['note']}


def monthly_csv(start, end, namespace='main'):
    fields = ('month', 'document_lines', 'revenue_rub', 'net_document_total_rub', 'known_cogs_rub',
              'historical_cogs_rub', 'result_after_fifo_rub', 'unknown_cost_units', 'marketplace_complete')
    output = io.StringIO(); writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader(); writer.writerows(monthly(start, end, namespace)['items'])
    return '\ufeff'+output.getvalue()


def tax_input(start, end, namespace='main', options=None):
    data = monthly(start, end, namespace)
    loaded = [r for r in data['items'] if r['revenue_rub'] is not None]
    suggestion = _amount(sum((Decimal(r['revenue_rub']) for r in loaded), Decimal(0))) if loaded else None
    result = {'income_input_rub': None, 'tax_input_confirmed': False, 'estimate': None,
              'document_revenue_suggestion_rub': suggestion, 'source': 'documentary_suggestion_only',
              'namespace': namespace, 'period': data['period'],
              'note': 'Выручка по начислению не равна полученному налоговому доходу. Введите подтверждённые суммы и период явно; net_accrual не используется.'}
    if options is None: return result
    from tax_calculator import calculate_tax, BASE_FIELDS, EXTRA_MONEY
    allowed = set(BASE_FIELDS+EXTRA_MONEY) | {'month','regime','period_mode','rate','taxpayer_type','has_staff','vat_mode'}
    if set(options)-allowed: raise ValueError('Неизвестные параметры налогового расчёта.')
    values = dict(options)
    month = values.get('month')
    if not isinstance(month, str) or len(month)!=7: raise ValueError('Укажите налоговый месяц ГГГГ-ММ.')
    date.fromisoformat(month+'-01')
    for key in BASE_FIELDS+EXTRA_MONEY:
        if values.get(key) is not None:
            values[key] = money(values[key])
            if values[key] < 0: raise ValueError('Явные налоговые суммы должны быть неотрицательными.')
    if values.get('rate') is not None: values['rate'] = money(values['rate'])
    if values.get('has_staff') is not None and type(values['has_staff']) is not bool: raise ValueError('has_staff: логическое значение.')
    estimate = calculate_tax(values)
    result.update(source='explicit_manual_tax_inputs', inputs={k:str(v) if isinstance(v,Decimal) else v for k,v in values.items()},
                  income_input_rub=_amount(estimate['income']) if estimate['income'] is not None else None,
                  tax_input_confirmed=estimate['status']=='estimate',
                  estimate={k:_amount(v) if isinstance(v,float) else v for k,v in estimate.items()})
    return result
