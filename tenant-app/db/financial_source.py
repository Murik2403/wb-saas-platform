"""Append-only provenance of actual WB financial ingestion, never legacy attestation.

Only the server ingestion adapter supplies source_identity after a successful
read made with the captured client token. It is not a user-controlled DTO.
"""
import hashlib
import json
import re
from datetime import datetime, timezone


def captured_identity(api):
    """Capture before the remote call; never read fresh configuration afterwards."""
    token = getattr(api, 'token', None)
    if not isinstance(token, str):
        return None
    from .shop_workspaces import account_identity
    try:
        return account_identity(token)
    except ValueError:
        return None


def fingerprint(row):
    value = dict(row)
    if 'raw_json' in value:
        value['raw_json'] = json.loads(value['raw_json'] or '{}')
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def ensure_schema(conn):
    # Individual statements preserve an existing ingest transaction.
    conn.execute('''CREATE TABLE IF NOT EXISTS financial_source_ingestions(
      id INTEGER PRIMARY KEY,rrd_id INTEGER NOT NULL,identity TEXT,
      fingerprint TEXT NOT NULL,source_kind TEXT NOT NULL
        CHECK(source_kind IN ('wb_api','legacy_unverified')),
      captured_at TEXT NOT NULL)''')
    conn.execute('CREATE INDEX IF NOT EXISTS financial_source_rrd ON financial_source_ingestions(rrd_id,id DESC)')
    for operation in ('UPDATE', 'DELETE'):
        conn.execute(f"CREATE TRIGGER IF NOT EXISTS financial_source_no_{operation.lower()} BEFORE {operation} ON financial_source_ingestions BEGIN SELECT RAISE(ABORT,'financial source provenance immutable'); END")
    conn.execute("CREATE TRIGGER IF NOT EXISTS financial_source_no_replace BEFORE INSERT ON financial_source_ingestions WHEN NEW.id>0 AND EXISTS(SELECT 1 FROM financial_source_ingestions WHERE id=NEW.id) BEGIN SELECT RAISE(ABORT,'financial source provenance immutable'); END")


def record(conn, rows, source_identity):
    if source_identity is not None and (not isinstance(source_identity, str) or not re.fullmatch('[0-9a-f]{64}', source_identity)):
        raise ValueError('Неподтверждённый источник финансовых данных')
    ensure_schema(conn)
    if source_identity is not None:
        # A verified primary binding forbids merging another seller into its DB.
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='warehouse_shops'").fetchone():
            bound = conn.execute('SELECT identity FROM warehouse_shops WHERE shop_id=0').fetchone()
            if bound and bound['identity'] != source_identity:
                raise ValueError('Источник реализации не соответствует основному продавцу')
    captured_at = datetime.now(timezone.utc).isoformat()
    for row in rows:
        if source_identity is not None:
            old = conn.execute('SELECT identity FROM financial_source_ingestions WHERE rrd_id=? AND identity IS NOT NULL AND identity<>? LIMIT 1',
                               (row['rrd_id'], source_identity)).fetchone()
            if old:
                raise ValueError('Строка реализации уже принадлежит другому продавцу')
        row_fingerprint = fingerprint(row)
        latest = conn.execute('SELECT identity,fingerprint FROM financial_source_ingestions WHERE rrd_id=? ORDER BY id DESC LIMIT 1', (row['rrd_id'],)).fetchone()
        if latest and latest['identity'] == source_identity and latest['fingerprint'] == row_fingerprint:
            continue
        conn.execute('INSERT INTO financial_source_ingestions(rrd_id,identity,fingerprint,source_kind,captured_at) VALUES(?,?,?,?,?)',
                     (row['rrd_id'], source_identity, row_fingerprint,
                      'wb_api' if source_identity is not None else 'legacy_unverified', captured_at))


def current(conn, row):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='financial_source_ingestions'").fetchone():
        return None
    proof = conn.execute('SELECT * FROM financial_source_ingestions WHERE rrd_id=? ORDER BY id DESC LIMIT 1', (row['rrd_id'],)).fetchone()
    try:current_fingerprint=fingerprint(row)
    except (ValueError,TypeError):return None
    if not proof or proof['fingerprint'] != current_fingerprint:
        return None
    return dict(proof)
