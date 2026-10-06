from __future__ import annotations
import base64,json,sqlite3
from contextlib import closing
from unittest.mock import patch
from cryptography.fernet import Fernet
import pytest
import config
import db.core as core
from db import shop_workspaces as shops
from shop_workspace_service import execute,prepare,_worker_slot

def test_parallel_read_queue_serializes_one_worker_and_writes_failfast(database,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event,Lock
    import shop_workspace_service as service
    entered=Event();release=Event();lock=Lock();active=0;peak=0
    def fake(*args,**kwargs):
        nonlocal active,peak
        with lock:active+=1;peak=max(peak,active)
        entered.set();assert release.wait(5)
        with lock:active-=1
        return {'ok':True}
    monkeypatch.setattr(service,'_execute',fake)
    with ThreadPoolExecutor(3) as pool:
        first=pool.submit(prepare,1,'request','2026-09-01','2026-09-02',request={'method':'GET'})
        assert entered.wait(5)
        second=pool.submit(prepare,1,'request','2026-09-01','2026-09-02',request={'method':'GET'})
        with pytest.raises(ValueError):prepare(1,'request','2026-09-01','2026-09-02',request={'method':'PATCH'})
        release.set()
        assert first.result()['ok'] and second.result()['ok']
    assert peak==1

def test_exact_read_cache_revision_actor_isolation_and_total_deadline(database,monkeypatch):
    import shop_workspace_service as service
    shop=shops.create('One','token','identity');calls=[]
    def fake(*args,**kwargs):
        calls.append(kwargs['_timeout'])
        return {'base_revision':len(calls),'identity':'identity','snapshot':None,'response':{'status':200,'body':'','content_type':'application/json'},'source_errors':[]}
    monkeypatch.setattr(service,'_execute',fake)
    common={'request':{'method':'GET','path':'/api/v1/dashboard/overview','query':'','body':''},'actor':'one','role':'owner'}
    first=prepare(shop,'request','2026-09-01','2026-09-02',_timeout=2,**common)
    assert prepare(shop,'request','2026-09-01','2026-09-02',_timeout=2,**common)==first
    assert len(calls)==1 and 0<calls[0]<=2
    prepare(shop,'request','2026-09-01','2026-09-02',_timeout=2,**dict(common,actor='two'))
    with core.connect() as conn:conn.execute('UPDATE shop_workspaces SET revision=revision+1 WHERE id=?',(shop,))
    prepare(shop,'request','2026-09-01','2026-09-02',_timeout=2,**common)
    assert len(calls)==3

@pytest.fixture
def database(tmp_path,monkeypatch):
    monkeypatch.setattr(core,'DB_PATH',tmp_path/'main.sqlite3')
    monkeypatch.setattr(config,'DATA_DIR',tmp_path)
    monkeypatch.setattr(config,'DATA_ENCRYPTION_KEY',Fernet.generate_key().decode())
    monkeypatch.setattr(config,'DATA_ENCRYPTION_KEY_ID','current')
    monkeypatch.setattr(config,'DATA_ENCRYPTION_KEYRING','')
    core.init_db()
    yield tmp_path

def test_credentials_cipher_duplicate_cap_rotation_and_inactive(database):
    shop=shops.create('One','secret-token','identity')
    assert 'secret-token' not in str(shops.listing())
    assert 'secret-token' not in shops.read(shop)['credential_cipher']
    assert shops.worker_input(shop)['token']=='secret-token'
    with pytest.raises(ValueError,match='уже подключён'):shops.create('Two','new-token','identity')
    shops.deactivate(shop)
    with pytest.raises(ValueError):shops.worker_input(shop)
    with pytest.raises(ValueError):shops.reconnect(shop,'wrong','other')
    shops.reconnect(shop,'rotated','identity')
    assert shops.worker_input(shop)['token']=='rotated'
    for i in range(4):shops.create(str(i),'token',str(i))
    with pytest.raises(ValueError,match='предел'):shops.create('Six','token','six')

def test_keyring_only_read(database,monkeypatch):
    shop=shops.create('One','secret-token','identity')
    key=config.DATA_ENCRYPTION_KEY
    monkeypatch.setattr(config,'DATA_ENCRYPTION_KEY','')
    monkeypatch.setattr(config,'DATA_ENCRYPTION_KEYRING',json.dumps({'current':key}))
    assert shops.worker_input(shop)['token']=='secret-token'

def test_snapshot_revision_failure_preserves_original(database):
    shop=shops.create('One','token','identity')
    path=database/'copy.sqlite3'
    with closing(sqlite3.connect(path)) as c:c.execute('CREATE TABLE value(n INTEGER)');c.commit()
    blob=path.read_bytes()
    shops.store_snapshot(shop,0,blob,synced=True,source_errors=['ads_skipped'])
    before=shops.read(shop)
    with pytest.raises(ValueError,match='параллельно'):shops.store_snapshot(shop,0,blob)
    assert shops.read(shop)==before
    assert shops.worker_input(shop)['source_errors']==['ads_skipped']

def test_real_child_report_no_main_mutation_and_failed_cost(database):
    shop=shops.create('One','unused-token','identity')
    before=shops.read(shop)
    report=execute(shop,'report','2026-09-01','2026-09-02')
    assert report['product_count']==0
    assert report['financial']['report_rows']==0
    assert shops.read(shop)==before
    with pytest.raises(ValueError,match='прежние данные сохранены'):
        execute(shop,'cost','2026-09-01','2026-09-02',cost={'nm_id':999,'cost_per_wb_unit':100})
    assert shops.read(shop)==before

def test_capacity_and_invalid_range(database):
    shop=shops.create('One','token','identity')
    assert _worker_slot.acquire(False)
    try:
        with pytest.raises(ValueError,match='обрабатывается'):execute(shop,'cost','2026-09-01','2026-09-02',cost={})
    finally:_worker_slot.release()
    with pytest.raises(ValueError,match='раньше'):execute(shop,'report','2026-09-03','2026-09-01')

def test_account_identity_rotation_stable():
    def token(sid,exp):return 'header.'+base64.urlsafe_b64encode(json.dumps({'sid':sid,'exp':exp}).encode()).decode().rstrip('=')+'.signature'
    assert shops.account_identity(token('a',1))==shops.account_identity('Bearer '+token('a',2))
    assert shops.account_identity(token('a',1))!=shops.account_identity(token('b',1))
    with pytest.raises(ValueError):shops.account_identity('not-jwt')

def test_two_real_shop_snapshots_keep_same_wb_article_cost_isolated(database,monkeypatch):
    main_path=core.DB_PATH
    ids=[shops.create('First','unused','first'),shops.create('Second','unused','second')]
    for index,shop in enumerate(ids):
        child=database/f'child-{index}.sqlite3'
        monkeypatch.setattr(core,'DB_PATH',child)
        core.init_db()
        with core.connect() as c:
            c.execute("INSERT INTO products_catalog(nm_id,supplier_article,product_name,raw_json) VALUES(123,'same','Same product','{}')")
            c.execute('INSERT INTO costs(nm_id,cost_per_wb_unit) VALUES(123,?)',(10+index*20,))
        monkeypatch.setattr(core,'DB_PATH',main_path)
        shops.store_snapshot(shop,0,child.read_bytes(),synced=True)
    assert execute(ids[0],'report','2026-09-01','2026-09-02')['products'][0]['cost']==10
    assert execute(ids[1],'report','2026-09-01','2026-09-02')['products'][0]['cost']==30
    before=shops.read(ids[0])
    prepared=prepare(ids[0],'cost','2026-09-01','2026-09-02',cost={'nm_id':123,'cost_per_wb_unit':44})
    assert prepared['response']['products'][0]['cost']==44
    assert prepared['identity']=='first' and prepared['base_revision']==before['revision']
    assert prepared['snapshot'].startswith(b'SQLite format 3\x00')
    assert shops.read(ids[0])==before
    assert execute(ids[0],'report','2026-09-01','2026-09-02')['products'][0]['cost']==10
    changed=execute(ids[0],'cost','2026-09-01','2026-09-02',cost={'nm_id':123,'cost_per_wb_unit':55})
    assert changed['products'][0]['cost']==55
    assert execute(ids[1],'report','2026-09-01','2026-09-02')['products'][0]['cost']==30
    with core.connect() as c:assert c.execute('SELECT COUNT(*) FROM costs WHERE nm_id=123').fetchone()[0]==0

def test_native_encrypted_backup_contains_secondary_snapshot(database,monkeypatch):
    import backup_tools as backups
    import io,zipfile
    shop=shops.create('One','private-token','identity')
    child=database/'copy.sqlite3'
    with closing(sqlite3.connect(child)) as c:c.execute('CREATE TABLE marker(value TEXT)');c.execute("INSERT INTO marker VALUES('preserved')");c.commit()
    blob=child.read_bytes();shops.store_snapshot(shop,0,blob,synced=True)
    backup_dir=database/'backups';backup_dir.mkdir(exist_ok=True)
    for name,value in {'DATA_DIR':database,'DB_PATH':core.DB_PATH,'SETTINGS_PATH':database/'settings.json','BACKUP_DIR':backup_dir,'DATA_ENCRYPTION_KEY':config.DATA_ENCRYPTION_KEY,'DATA_ENCRYPTION_KEY_ID':'current','DATA_ENCRYPTION_KEYRING':''}.items():monkeypatch.setattr(backups,name,value)
    archive=backups.create_backup(keep=5).read_bytes()
    assert archive.startswith(b'mh-backup-enc:')
    decoded=backups._maybe_decrypt_backup(archive)
    with zipfile.ZipFile(io.BytesIO(decoded)) as z:restored=z.read('wb_dashboard.sqlite3')
    restored_path=database/'restored.sqlite3';restored_path.write_bytes(restored)
    with closing(sqlite3.connect(restored_path)) as c:
        assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        saved=c.execute('SELECT database_snapshot,credential_cipher FROM shop_workspaces WHERE id=?',(shop,)).fetchone()
        assert saved[0]==blob
        assert 'private-token' not in saved[1]
