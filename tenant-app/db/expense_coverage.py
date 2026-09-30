"""User attestations of expense completeness; never a monetary expense source."""
import hashlib
import json
import math
import uuid
from datetime import date, datetime, timezone

from .core import connect
from .manual_expenses import EXPENSE_ARTICLES

STATES = {'confirmed', 'confirmed_zero', 'not_applicable', 'estimated', 'unknown'}
RAW_FIELDS = ('id', 'nm_id', 'article', 'amount_rub', 'allocation_basis', 'source', 'period_start', 'period_end', 'include_in_management_result', 'updated_at')


def init_expense_coverage_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS expense_article_attestations (
      id INTEGER PRIMARY KEY AUTOINCREMENT, request_key TEXT NOT NULL UNIQUE,
      nm_id INTEGER NOT NULL, article TEXT NOT NULL, period_start TEXT NOT NULL, period_end TEXT NOT NULL,
      state TEXT NOT NULL, source_ref TEXT NOT NULL, reason TEXT NOT NULL, actor TEXT NOT NULL,
      fingerprint TEXT NOT NULL, before_json TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL)''')
    conn.execute("CREATE TRIGGER IF NOT EXISTS expense_attestation_no_replace BEFORE INSERT ON expense_article_attestations WHEN EXISTS(SELECT 1 FROM expense_article_attestations WHERE id=NEW.id OR request_key=NEW.request_key) BEGIN SELECT RAISE(ABORT,'expense attestation history is immutable'); END")
    conn.execute('CREATE INDEX IF NOT EXISTS idx_expense_attestations_scope ON expense_article_attestations(nm_id,period_start,period_end,article,id)')
    for operation in ('UPDATE', 'DELETE'):
        conn.execute(f"CREATE TRIGGER IF NOT EXISTS expense_attestation_no_{operation.lower()} BEFORE {operation} ON expense_article_attestations BEGIN SELECT RAISE(ABORT,'expense attestation history is immutable'); END")


def _dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _hash(value):
    return hashlib.sha256(_dump(value).encode()).hexdigest()


def _scope(nm_id, start, end):
    if not isinstance(nm_id, int) or isinstance(nm_id, bool) or nm_id <= 0:
        raise ValueError('Артикул должен быть положительным.')
    start, end = date.fromisoformat(str(start)).isoformat(), date.fromisoformat(str(end)).isoformat()
    if start > end: raise ValueError('Конец периода раньше начала.')
    return int(nm_id), start, end


def _raw(rows, article):
    return sorted([{key: (row.get('raw_inclusion', row.get(key)) if key == 'include_in_management_result' else row.get(key)) for key in RAW_FIELDS} for row in rows if row['article'] == article], key=lambda row: row['id'])


def fingerprint(article, rows, baseline):
    effective = [{'id': row['id'], 'allocated_rub': row.get('allocated_rub'), 'included': not row.get('excluded_from_result', True)} for row in rows if row['article'] == article]
    return _hash({'raw': _raw(rows, article), 'effective': sorted(effective, key=lambda row: row['id']), 'baseline': baseline})


def read_coverage(*, nm_id, start, end, rows, baseline):
    nm_id, start, end = _scope(nm_id, start, end)
    with connect() as conn:
        records = [dict(row) for row in conn.execute('SELECT * FROM expense_article_attestations WHERE nm_id=? AND period_start=? AND period_end=? ORDER BY id DESC', (nm_id, start, end))]
    latest = {}
    for row in records: latest.setdefault(row['article'], row)
    items = []
    for article in EXPENSE_ARTICLES:
        saved = latest.get(article)
        stale = bool(saved and saved['fingerprint'] != fingerprint(article, rows, baseline))
        state = saved['state'] if saved and not stale else 'unknown'
        items.append({'article': article, 'state': state, 'stale': stale, 'source_ref': saved['source_ref'] if saved else '', 'reason': saved['reason'] if saved else '', 'actor': saved['actor'] if saved else '', 'created_at': saved['created_at'] if saved else '', 'attested_state': saved['state'] if saved else None, 'fingerprint': fingerprint(article, rows, baseline), 'observed_source': baseline.get('observed_nonzero', {}).get(article)})
    covered = [item['article'] for item in items if item['state'] in {'confirmed', 'confirmed_zero', 'not_applicable'}]
    missing = [item['article'] for item in items if item['state'] == 'unknown']
    estimated = [item['article'] for item in items if item['state'] == 'estimated']
    return {'covered': covered, 'missing': missing, 'estimated': estimated, 'count': len(covered), 'total': 9, 'percent': round(len(covered)/9*100, 2), 'preliminary': len(covered) < 9, 'items': items, 'history': [{k: row[k] for k in ('id', 'article', 'state', 'source_ref', 'reason', 'actor', 'created_at', 'before_json')} for row in records[:100]], 'attestation': 'user_document_statement', 'note': 'Это подтверждение пользователя для точного товара и периода. Оно не меняет суммы и не подтверждает полноту FIFO или всего отчёта.'}


def save_attestation(*, nm_id, start, end, article, state, source_ref, reason, actor, request_key, rows, baseline, expected_fingerprint=None):
    nm_id, start, end = _scope(nm_id, start, end)
    if not isinstance(article, str) or article not in EXPENSE_ARTICLES or not isinstance(state, str) or state not in STATES:
        raise ValueError('Недопустимая статья или состояние.')
    for text, limit, name in ((source_ref, 200, 'Источник/документ'), (reason, 500, 'Причина'), (actor, 128, 'Автор')):
        if not isinstance(text, str) or not text.strip() or len(text) > limit or any(ord(c)<32 for c in text):
            raise ValueError(f'{name}: заполните текст до {limit} символов.')
    try:
        if str(uuid.UUID(request_key)) != request_key: raise ValueError()
    except (ValueError, TypeError, AttributeError): raise ValueError('Некорректный ключ операции.') from None
    included = [row for row in rows if row['article'] == article and not row.get('excluded_from_result', True)]
    if state in {'confirmed', 'confirmed_zero', 'not_applicable'} and any(row.get('allocated_rub') is None or not math.isfinite(float(row['allocated_rub'])) for row in included):
        raise ValueError('Сначала уточните неизвестную сумму включённого расхода.')
    if state in {'confirmed_zero', 'not_applicable'} and any(float(row['allocated_rub']) != 0 for row in included):
        raise ValueError('В периоде есть ненулевой включённый расход этой статьи.')
    if state in {'confirmed_zero', 'not_applicable'} and baseline.get('observed_nonzero', {}).get(article):
        raise ValueError('В исходных данных есть ненулевые расходы этой статьи; ноль или неприменимость противоречат источнику.')
    data = {'nm_id': nm_id, 'article': article, 'start': start, 'end': end, 'state': state, 'source_ref': source_ref.strip(), 'reason': reason.strip(), 'actor': actor.strip(), 'fingerprint': fingerprint(article, rows, baseline)}
    if expected_fingerprint is not None and expected_fingerprint != data['fingerprint']:
        raise ValueError('Исходные данные изменились; обновите покрытие перед подтверждением.')
    encoded = _dump(data)
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        prior = conn.execute('SELECT id,payload_json FROM expense_article_attestations WHERE request_key=?', (request_key,)).fetchone()
        if prior:
            if prior['payload_json'] != encoded: raise ValueError('Ключ уже использован для другого подтверждения.')
            return int(prior['id'])
        current = [dict(row) for row in conn.execute('SELECT * FROM manual_expenses WHERE nm_id=? AND period_end>=? AND period_start<=? ORDER BY id', (nm_id, start, end))]
        if _raw(current, article) != _raw(rows, article): raise ValueError('Расходы изменились; обновите расчёт.')
        old = conn.execute('SELECT state,source_ref,reason,actor,created_at FROM expense_article_attestations WHERE nm_id=? AND period_start=? AND period_end=? AND article=? ORDER BY id DESC LIMIT 1', (nm_id, start, end, article)).fetchone()
        cursor = conn.execute('INSERT INTO expense_article_attestations(request_key,nm_id,article,period_start,period_end,state,source_ref,reason,actor,fingerprint,before_json,payload_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)', (request_key,nm_id,article,start,end,state,source_ref.strip(),reason.strip(),actor.strip(),data['fingerprint'],_dump(dict(old) if old else {}),encoded,datetime.now(timezone.utc).isoformat(timespec='seconds')))
        return int(cursor.lastrowid)


def observed_source_amounts(nm_id, start, end, source_row):
    """Contradiction evidence only: presence of source values does not prove completeness."""
    evidence = {}
    with connect() as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='financial_report'").fetchone():
            records = []
        else:
            records = conn.execute("SELECT commission,ppvz_sales_commission,delivery_rub,rebill_logistic_cost,storage_fee,acceptance FROM financial_report WHERE nm_id=? AND substr(operation_date,1,10)>=? AND substr(operation_date,1,10)<=?", (nm_id, str(start), str(end))).fetchall()
    fields = {'wb_commission': ('commission','ppvz_sales_commission'), 'logistics_returns': ('delivery_rub','rebill_logistic_cost'), 'storage': ('storage_fee',), 'acceptance': ('acceptance',)}
    for article, names in fields.items():
        values = [float(row[name]) for row in records for name in names if row[name] is not None and math.isfinite(float(row[name])) and float(row[name]) != 0]
        if values: evidence[article] = {'source': 'WB financial report article rows', 'quality': 'observed_nonzero_only', 'absolute_observed_rub': sum(abs(value) for value in values)}
    for article, names in {'purchase_production': ('Себестоимость','Точная FIFO-себестоимость','Оценка по текущей себестоимости'), 'advertising': ('Реклама',)}.items():
        values = []
        for name in names:
            value = source_row.get(name)
            if isinstance(value, (int,float)) and not isinstance(value,bool) and math.isfinite(value) and value != 0: values.append(value)
        if values: evidence[article] = {'source': 'selected SKU period financial snapshot', 'quality': 'observed_nonzero_only', 'absolute_observed_rub': sum(abs(value) for value in values)}
    return evidence
