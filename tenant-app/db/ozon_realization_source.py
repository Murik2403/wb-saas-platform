"""Immutable monthly documentary observations, never monetary/movement postings."""
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import calendar
from ozon_accounting_client import checked_realization_month
from .core import connect
from .ozon_pnl import capture_source_snapshot, digest


def _text(value):
    return value if isinstance(value,str) and 0 < len(value) <= 256 else None


def _date(value):
    if not isinstance(value,str): return None
    try:
        parsed = date.fromisoformat(value)
        return value if parsed.isoformat() == value else None
    except ValueError: return None


def _money(value):
    if type(value) not in (str,int): return None
    try:
        amount = Decimal(value)
        if not amount.is_finite() or abs(amount) >= Decimal('1e18') or amount != amount.quantize(Decimal('.01')): return None
        return str(amount)
    except (InvalidOperation,ValueError): return None


def _header(source):
    checked_realization_month(source['month'],source['year'])
    report=source.get('report')
    if not isinstance(report,dict) or not isinstance(report.get('header'),dict) or not isinstance(report.get('rows'),list) or any(not isinstance(r,dict) for r in report['rows']):
        raise ValueError('Не распознана структура месячного отчёта Ozon.')
    header=report['header']; start=_date(header.get('start_date')); end=_date(header.get('stop_date'))
    expected=f"{source['year']:04d}-{source['month']:02d}-"
    if not start or not end or not start.startswith(expected) or not end.startswith(expected) or start>end:
        raise ValueError('Период документа Ozon не совпадает с запрошенным месяцем. Сохранённый отчёт не заменён.')
    if header.get('currency_sys_name') != 'RUB':
        raise ValueError('Для предварительного отчёта поддерживается только документ в RUB; конвертация не выполняется.')
    if not _text(header.get('number')) or not _date(header.get('doc_date')):
        raise ValueError('Номер или дата документа Ozon не распознаны. Сохранённый отчёт не заменён.')
    return header


def preview(source):
    header=_header(source); version=digest(source); rows=[]
    for index,row in enumerate(source['report']['rows'][:100]):
        item=row.get('item') if isinstance(row.get('item'),dict) else {}
        order=row.get('order') if isinstance(row.get('order'),dict) else {}
        result={'source_occurrence_key':f'ozon:realization-observation:v1:{version}:{index}',
                'row_number_text':str(row['row_number']) if type(row.get('row_number')) is int and 0 <= row['row_number'] < 2**31 else None,
                'sku_text':str(item['sku']) if type(item.get('sku')) is int and 0 < item['sku'] < 2**63 else None,
                'posting_number':_text(order.get('posting_number'))}
        reasons=[]
        for branch in ('delivery_commission','return_commission'):
            fields=row.get(branch) if isinstance(row.get(branch),dict) else {}
            qty=fields.get('quantity'); total=_money(fields.get('total'))
            result[branch]={'quantity_text':str(qty) if type(qty) is int and -(2**31) <= qty < 2**31 else None,'total_rub':total}
            if result[branch]['quantity_text'] is None or total is None:
                reasons.append(('Доставка' if branch=='delivery_commission' else 'Возврат')+': количество или итог не распознаны; неизвестное не заменено нулём.')
        result['reason']=' '.join(reasons) or None
        rows.append(result)
    count=len(source['report']['rows'])
    return {'loaded':True,'month':source['month'],'year':source['year'],'source_version':version,
            'document_number':header['number'],'document_date':header['doc_date'],'currency':'RUB',
            'period':{'start_date':header['start_date'],'end_date':header['stop_date']},'source_row_count':count,'rows':rows,'display_truncated':count>100,
            'partial_period':header['start_date']!=f"{source['year']:04d}-{source['month']:02d}-01" or header['stop_date']!=f"{source['year']:04d}-{source['month']:02d}-{calendar.monthrange(source['year'],source['month'])[1]:02d}",
            'current_month_provisional':(source['year'],source['month'])==(date.today().year,date.today().month),
            'financial_normalization_verified':False,'marketplace_source_complete':False,'source_complete':False,'accounting_complete':False,'profit_rub':None,'movement_verified':False,'correction_identity_verified':False,'ledger_confirmation_allowed':False}


def _schema(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS ozon_realization_latest (month_key TEXT PRIMARY KEY,source_key TEXT NOT NULL,updated_at TEXT NOT NULL)')


def save(source):
    if source.get('source_kind') != 'ozon-realization-v1': raise ValueError('Не распознан источник отчёта Ozon.')
    result=preview(source)
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE'); _schema(conn)
        capture_source_snapshot(conn,source)
        key=f"{source['year']:04d}-{source['month']:02d}"
        conn.execute('INSERT INTO ozon_realization_latest VALUES(?,?,?) ON CONFLICT(month_key) DO UPDATE SET source_key=excluded.source_key,updated_at=excluded.updated_at',(key,result['source_version'],datetime.now(timezone.utc).isoformat()))
    return result


def saved(month,year):
    checked_realization_month(month,year)
    with connect() as conn:
        _schema(conn)
        from .ozon_pnl import ensure_schema
        ensure_schema(conn)
        row=conn.execute('SELECT v.payload_json FROM ozon_realization_latest p JOIN ozon_raw_source_versions v ON v.source_key=p.source_key WHERE p.month_key=?',(f'{year:04d}-{month:02d}',)).fetchone()
    return preview(json.loads(row[0])) if row else {'loaded':False,'month':month,'year':year,'rows':[],
            'financial_normalization_verified':False,'source_complete':False,'marketplace_source_complete':False,'accounting_complete':False,'profit_rub':None,'ledger_confirmation_allowed':False}
