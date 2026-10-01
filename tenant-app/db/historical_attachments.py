"""Documentary COGS attachment to existing financial rows, never source edits."""
import csv
import io
import json
from decimal import Decimal, InvalidOperation
from .core import connect
from .financial_source import fingerprint
from .financial_fifo_reconciliation import _event_type, _json_srid


def source_quantity(value):
    """Keep unknown/non-integral source units unknown; never round WB facts."""
    if value is None or isinstance(value, bool): return None
    try:
        quantity = abs(Decimal(str(value)))
        if not quantity.is_finite() or not 0 < quantity <= 10**9:
            return None
        if quantity != quantity.to_integral_value(): return None
        return int(quantity)
    except (InvalidOperation, ValueError, TypeError):
        return None


def variant(conn, fact):
    try:
        raw = json.loads(fact.get('raw_json') or '{}')
        if not isinstance(raw, dict): return None
        direct = raw.get('chrt_id', raw.get('chrtID'))
        if type(direct) is int and direct >= 0: return direct
        card = conn.execute('SELECT raw_json FROM products_catalog WHERE nm_id=?', (fact['nm_id'],)).fetchone()
        if not card: return None
        sizes = json.loads(card['raw_json'] or '{}').get('sizes', [])
        barcode = raw.get('barcode')
        matches = [s['chrtID'] for s in sizes if isinstance(s, dict) and type(s.get('chrtID')) is int and isinstance(barcode, str) and barcode in s.get('skus', [])]
        if len(matches) == 1: return matches[0]
        if not sizes: return 0
    except (ValueError, TypeError, KeyError):
        pass
    return None


def validate_sources(conn, rows):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='financial_report'").fetchone():
        raise ValueError('Строки WB отсутствуют: сначала загрузите финансовый отчёт')
    facts = {}
    sales = {item['data']['document_id']: item['data'] for item in rows if item['data']['event_type'] == 'sale'}
    for item in rows:
        row = item['data']
        source = conn.execute('SELECT * FROM financial_report WHERE rrd_id=?', (row['rrd_id'],)).fetchone()
        if source is None: raise ValueError('Строка WB отсутствует: прикрепление не создаёт финансовые документы')
        fact = dict(source)
        identity = (fact['nm_id'], fact['operation_date'], _event_type(fact), source_quantity(fact['quantity']), variant(conn, fact))
        expected = (row['nm_id'], row['operation_date'], row['event_type'], row['quantity'], row['chrt_id'])
        if identity != expected: raise ValueError('Дата, SKU, вариант, вид операции или количество не совпадают со строкой WB')
        if not _json_srid(fact.get('raw_json')) or _json_srid(fact['raw_json']) != row['source_srid']:
            raise ValueError('Исходный SRID WB отсутствует или не совпадает')
        if row['event_type'] == 'return':
            original = sales.get(row['original_sale_document'])
            if not original or original['source_srid'] != row['source_srid']:
                raise ValueError('Возврат требует исходную продажу с тем же SRID WB')
        if fingerprint(fact) != row['financial_fingerprint']:
            raise ValueError('Версия строки WB изменилась: выгрузите шаблон и проверьте корректировку')
        from .finished_history_import import MONEY
        for field in MONEY:
            actual = fact.get('ppvz_sales_commission' if field == 'commission' else field)
            if actual is None or Decimal(str(actual)) != Decimal(row[field]):
                raise ValueError('Сумма '+field+' не совпадает с исходной строкой WB')
        facts[str(row['rrd_id'])] = fact
    return facts


def template(start, end):
    from .finished_history_import import FIELDS, MONEY
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=FIELDS['historical_attachments'], delimiter=';')
    writer.writeheader()
    with connect() as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='financial_report'").fetchone(): return output.getvalue()
        facts = conn.execute('SELECT * FROM financial_report WHERE operation_date>=? AND operation_date<=? ORDER BY operation_date,rrd_id LIMIT 1001', (start.isoformat(), end.isoformat())).fetchall()
        if len(facts) > 1000: raise ValueError('В периоде больше 1000 строк: выберите меньший период')
        for source in facts:
            fact = dict(source)
            if not _event_type(fact): continue
            row = dict.fromkeys(FIELDS['historical_attachments'], '')
            row.update(rrd_id=fact['rrd_id'], nm_id=fact['nm_id'], chrt_id=variant(conn, fact), operation_date=fact['operation_date'], event_type=_event_type(fact), quantity=source_quantity(fact['quantity']), source_srid=_json_srid(fact.get('raw_json')), financial_fingerprint=fingerprint(fact))
            for field in MONEY: row[field]=fact.get('ppvz_sales_commission' if field=='commission' else field)
            # Financial numbers retain their signs. External source text remains
            # inert in spreadsheet software; re-check SRID against the source.
            for field, value in row.items():
                if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')): row[field]="'"+value
            writer.writerow(row)
    return output.getvalue()
