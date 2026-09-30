import base64
import json
import sqlite3
from unittest.mock import patch
import pytest
import config
from db.core import connect
from db import shared_warehouse as warehouse,shop_workspaces as shops
import shared_warehouse_setup as setup
import db.core as core
from cryptography.fernet import Fernet

def token(seller):
    return 'header.'+base64.urlsafe_b64encode(json.dumps({'sid':seller}).encode()).decode().rstrip('=')+'.signature'

@pytest.fixture
def trusted(tmp_path,monkeypatch):
    monkeypatch.setattr(core,"DB_PATH",tmp_path/"main.sqlite3")
    monkeypatch.setattr(config,"DATA_DIR",tmp_path)
    monkeypatch.setattr(config,"DATA_ENCRYPTION_KEY",Fernet.generate_key().decode())
    monkeypatch.setattr(config,"DATA_ENCRYPTION_KEYRING","")
    core.init_db()
    monkeypatch.setattr(config,'TENANT_SLUG','verified-tenant')
    setup.initialize()
    return tmp_path

def test_primary_binding_verifies_then_rejects_rotation_at_both_boundaries(trusted):
    for values in ([token('one'),token('two')],[token('one'),token('one'),token('two')]):
        with patch.object(config,'get_token',side_effect=values),patch('wb_api.WBAPI.test_statistics',return_value=True):
            with pytest.raises(warehouse.WarehouseConflict):setup.bind_primary('account','owner')
        with connect() as conn:assert conn.execute('SELECT COUNT(*) FROM warehouse_shops').fetchone()[0]==0
    with patch.object(config,'get_token',return_value=token('one')),patch('wb_api.WBAPI.test_statistics',return_value=True):
        result=setup.bind_primary('account','owner')
        assert result['refresh_session'] and result['verified_identity']==shops.account_identity(token('one'))
        assert setup.bind_primary('account','owner')==result
    with patch.object(config,'get_token',return_value=token('two')),patch('wb_api.WBAPI.test_statistics',return_value=True):
        with pytest.raises(warehouse.WarehouseConflict):setup.bind_primary('account','owner')

def test_primary_binding_duplicate_secondary_and_failed_wb_are_not_registered(trusted):
    shops.create('secondary',token('same'),shops.account_identity(token('same')),register_warehouse=True)
    with patch.object(config,'get_token',return_value=token('same')),patch('wb_api.WBAPI.test_statistics',return_value=True):
        with pytest.raises(warehouse.WarehouseConflict):setup.bind_primary('owner','owner')
    with patch.object(config,'get_token',return_value=token('other')),patch('wb_api.WBAPI.test_statistics',return_value=False):
        with pytest.raises(warehouse.WarehouseScopeError):setup.bind_primary('owner','owner')
    with connect() as conn:assert conn.execute('SELECT COUNT(*) FROM warehouse_shops WHERE shop_id=0').fetchone()[0]==0

def test_primary_verified_membership_migration_has_current_role_and_immutable_journal(trusted,monkeypatch):
    monkeypatch.setattr(config,'get_token',lambda:token('verified'))
    warehouse.register_shop(0,shops.account_identity(token('verified')))
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        setup.access(conn,0,'existing-member','viewer')
        assert conn.execute('SELECT source_role,tenant_slug,created_at FROM warehouse_primary_memberships').fetchone()['created_at'].endswith('+00:00')
        with pytest.raises(warehouse.WarehouseScopeError):setup.access(conn,0,'existing-member','viewer',True)
        setup.access(conn,0,'existing-member','manager',True)
        with pytest.raises(warehouse.WarehouseScopeError):setup.access(conn,0,'existing-member','viewer',True)
        assert conn.execute('SELECT COUNT(*) FROM warehouse_primary_memberships').fetchone()[0]==2
        for sql in ('DELETE FROM warehouse_primary_memberships','UPDATE warehouse_primary_memberships SET source_role="owner"','INSERT OR REPLACE INTO warehouse_primary_memberships SELECT * FROM warehouse_primary_memberships'):
            with pytest.raises(sqlite3.IntegrityError):conn.execute(sql)
    warehouse.register_shop(1,'secondary')
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        with pytest.raises(warehouse.WarehouseScopeError):setup.access(conn,1,'existing-member','manager')
        assert conn.execute('SELECT COUNT(*) FROM warehouse_shop_acl WHERE shop_id=1').fetchone()[0]==0
    monkeypatch.setattr(config,'TENANT_SLUG','')
    with pytest.raises(warehouse.WarehouseScopeError):setup.actor('existing-member','owner')

def test_mapping_confirms_exact_secondary_catalog_frozen_revision_and_provenance(trusted):
    with connect() as conn:
        conn.execute("INSERT INTO products_catalog(nm_id,product_name,raw_json) VALUES(100,'canonical',?)",(json.dumps({'sizes':[{'chrtID':10}]}),))
    canonical=warehouse.register_item(100,10,'Canonical')
    shop=shops.create('secondary','unused','seller',register_warehouse=True)
    private=sqlite3.connect(':memory:')
    try:
        private.execute('CREATE TABLE products_catalog(nm_id INTEGER,product_name TEXT,supplier_article TEXT,raw_json TEXT)')
        private.execute('INSERT INTO products_catalog VALUES(100,?,?,?)',('External same nm','external',json.dumps({'sizes':[{'chrtID':20,'techSize':'L'}]})))
        private.commit();blob=private.serialize()
    finally:private.close()
    shops.store_snapshot(shop,0,blob,synced=True)
    with pytest.raises(ValueError):setup.mapping_preview(shop,100,10,canonical['id'],'owner','owner')
    preview=setup.mapping_preview(shop,100,20,canonical['id'],'owner','owner')
    shops.store_snapshot(shop,1,blob,synced=True)
    with pytest.raises(warehouse.WarehouseConflict):setup.mapping_confirm(shop,100,20,canonical['id'],preview['fingerprint'],'owner','owner')
    preview=setup.mapping_preview(shop,100,20,canonical['id'],'owner','owner')
    result=setup.mapping_confirm(shop,100,20,canonical['id'],preview['fingerprint'],'owner','owner')
    assert result['external_chrt_id']==20 and result['item_id']==canonical['id']
    assert setup.mapping_confirm(shop,100,20,canonical['id'],preview['fingerprint'],'owner','owner')['id']==result['id']
    with connect() as conn:
        proof=conn.execute('SELECT * FROM warehouse_mapping_proofs').fetchone()
        assert proof['snapshot_revision']==2 and proof['shop_identity']=='seller'
        with pytest.raises(sqlite3.IntegrityError):conn.execute('DELETE FROM warehouse_mapping_proofs')

def test_setup_helpers_respect_supplied_transaction_rollback(trusted):
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        warehouse.register_shop(0,'verified',_conn=conn)
        assert conn.in_transaction
        conn.rollback()
        assert conn.execute('SELECT COUNT(*) FROM warehouse_shops').fetchone()[0]==0

def test_real_wal_backup_catalog_is_readable_without_header_rewrite(trusted):
    with connect() as conn:
        conn.execute("INSERT INTO products_catalog(nm_id,product_name,raw_json) VALUES(100,'Canonical',?)",(json.dumps({'sizes':[{'chrtID':10}]}),))
    item=warehouse.register_item(100,10,'Canonical')
    shop=shops.create('secondary','unused','seller',register_warehouse=True)
    source=sqlite3.connect(trusted/'wal-source.sqlite3')
    backup=sqlite3.connect(trusted/'wal-backup.sqlite3')
    try:
        assert source.execute('PRAGMA journal_mode=WAL').fetchone()[0]=='wal'
        source.execute('CREATE TABLE products_catalog(nm_id INTEGER,product_name TEXT,supplier_article TEXT,raw_json TEXT)')
        source.execute('INSERT INTO products_catalog VALUES(200,?,?,?)',('External','external',json.dumps({'sizes':[{'chrtID':20}]})))
        source.commit()
        source.backup(backup)
    finally:backup.close();source.close()
    blob=(trusted/'wal-backup.sqlite3').read_bytes()
    assert blob[18:20]==b'\x02\x02'
    shops.store_snapshot(shop,0,blob,synced=True)
    preview=setup.mapping_preview(shop,200,20,item['id'],'owner','owner')
    result=setup.mapping_confirm(shop,200,20,item['id'],preview['fingerprint'],'owner','owner')
    assert result['external_nm_id']==200
    assert shops.read(shop)['database_snapshot']==blob
    assert not list(trusted.glob('.warehouse-proof-*'))
