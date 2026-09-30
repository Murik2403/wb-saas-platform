"""Isolated secondary WB workspaces, stored inside the native backup database."""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from cryptography.fernet import Fernet, InvalidToken
import config
from .core import connect

MAX_SNAPSHOT = 128 * 1024 * 1024
MAX_SHOPS = 5

def initialize() -> None:
    with connect() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS shop_workspaces(
          id INTEGER PRIMARY KEY, name TEXT NOT NULL, account_identity TEXT NOT NULL UNIQUE,
          credential_cipher TEXT NOT NULL, database_snapshot BLOB,
          revision INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
          updated_at TEXT NOT NULL, synced_at TEXT, sync_quality_json TEXT NOT NULL DEFAULT '[]')''')
        if 'sync_quality_json' not in {r['name'] for r in c.execute('PRAGMA table_info(shop_workspaces)')}:
            c.execute("ALTER TABLE shop_workspaces ADD COLUMN sync_quality_json TEXT NOT NULL DEFAULT '[]'")

def _cipher(value: str) -> str:
    if not config.DATA_ENCRYPTION_KEY:
        raise ValueError("Шифрование подключений не настроено")
    return config.DATA_ENCRYPTION_KEY_ID + ':' + Fernet(config.DATA_ENCRYPTION_KEY.encode()).encrypt(value.encode()).decode()

def _decipher(value: str) -> str:
    key_id, encrypted = value.split(':', 1)
    keys = json.loads(config.DATA_ENCRYPTION_KEYRING or '{}')
    if config.DATA_ENCRYPTION_KEY:
        keys[config.DATA_ENCRYPTION_KEY_ID] = config.DATA_ENCRYPTION_KEY
    key = keys.get(key_id)
    if not key:
        raise ValueError("Ключ подключения магазина недоступен")
    try:
        return Fernet(key.encode()).decrypt(encrypted.encode()).decode()
    except (InvalidToken, ValueError) as exc:
        raise ValueError("Не удалось открыть подключение магазина") from exc

def create(name: str, token: str, account_identity: str) -> int:
    name, token, account_identity = name.strip(), token.strip(), account_identity.strip()
    if not name or len(name)>120 or not token or not account_identity or len(account_identity)>200:
        raise ValueError("Название и подтверждённое подключение магазина обязательны")
    initialize()
    encrypted = _cipher(token)
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        if c.execute('SELECT 1 FROM shop_workspaces WHERE account_identity=?',(account_identity,)).fetchone():
            raise ValueError("Этот магазин уже подключён")
        if c.execute('SELECT COUNT(*) FROM shop_workspaces').fetchone()[0]>=MAX_SHOPS:
            raise ValueError('Достигнут предел: пять дополнительных магазинов')
        result=c.execute('INSERT INTO shop_workspaces(name,account_identity,credential_cipher,updated_at) VALUES(?,?,?,?)',
                         (name,account_identity,encrypted,datetime.now(timezone.utc).isoformat()))
        return int(result.lastrowid)

def listing() -> list[dict]:
    initialize()
    with connect() as c:
        return [dict(r) for r in c.execute('SELECT id,name,revision,active,updated_at,synced_at FROM shop_workspaces ORDER BY id')]

def read(shop_id: int) -> dict:
    initialize()
    with connect() as c:
        r=c.execute('SELECT * FROM shop_workspaces WHERE id=?',(shop_id,)).fetchone()
        if not r or not r['active']:
            raise ValueError("Магазин не найден или отключён")
        return dict(r)

def worker_input(shop_id: int) -> dict:
    r=read(shop_id)
    return {'revision':r['revision'],'identity':r['account_identity'],'token':_decipher(r['credential_cipher']), 'snapshot':r['database_snapshot'],'source_errors':json.loads(r['sync_quality_json'])}

def store_snapshot(shop_id: int, revision: int, snapshot: bytes, *, synced: bool=False,source_errors:list[str]|None=None) -> None:
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        store_snapshot_in_transaction(c,shop_id,revision,snapshot,synced=synced,source_errors=source_errors)

def store_snapshot_in_transaction(c,shop_id:int,revision:int,snapshot:bytes,*,synced:bool=False,source_errors:list[str]|None=None,identity:str|None=None)->None:
    """Persist a prepared snapshot in the caller's single writer transaction."""
    if not c.in_transaction:raise ValueError('Запись магазина требует активной транзакции')
    if not snapshot.startswith(b'SQLite format 3\x00') or len(snapshot)>MAX_SNAPSHOT:
        raise ValueError("Некорректная база магазина")
    stamp=datetime.now(timezone.utc).isoformat()
    result=c.execute('UPDATE shop_workspaces SET database_snapshot=?,revision=revision+1,updated_at=?,synced_at=CASE WHEN ? THEN ? ELSE synced_at END,sync_quality_json=? WHERE id=? AND active=1 AND revision=? AND (? IS NULL OR account_identity=?)',
                     (snapshot,stamp,int(synced),stamp,json.dumps(source_errors or [],allow_nan=False),shop_id,revision,identity,identity))
    if result.rowcount != 1:
        raise ValueError("Данные или продавец магазина изменились параллельно. Повторите действие")

def deactivate(shop_id: int) -> None:
    initialize()
    with connect() as c:
        result=c.execute('UPDATE shop_workspaces SET active=0,revision=revision+1 WHERE id=? AND active=1',(shop_id,))
        if result.rowcount!=1:
            raise ValueError("Магазин не найден или уже отключён")

def reconnect(shop_id:int,token:str,identity:str)->None:
    encrypted=_cipher(token)
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        r=c.execute('SELECT account_identity FROM shop_workspaces WHERE id=?',(shop_id,)).fetchone()
        if not r or r['account_identity']!=identity:
            raise ValueError('Новый токен принадлежит другому магазину')
        c.execute('UPDATE shop_workspaces SET credential_cipher=?,active=1,revision=revision+1,updated_at=? WHERE id=?',(encrypted,datetime.now(timezone.utc).isoformat(),shop_id))

def account_identity(token: str) -> str:
    """Use the seller ID from the signed token after a successful API probe.

    Do not persist raw tokens/decoded claims; hash the stable seller identity.
    Missing identity is rejected rather than using token hash (rotation duplicates).
    """
    import base64
    try:
        token=token[7:].strip() if token.lower().startswith('bearer ') else token
        claims=json.loads(base64.urlsafe_b64decode(token.split('.')[1]+'==='))
        identity=str(claims.get('sid') or '')
        if not identity:raise ValueError()
    except (ValueError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("В проверенном токене не найден идентификатор продавца") from exc
    return hashlib.sha256(('wb:'+identity).encode()).hexdigest()
