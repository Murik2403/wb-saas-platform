import hashlib
import base64
import json
import os
import struct
from datetime import datetime,timezone

import pytest
from scripts import pdn_retired_rehearsal_cleanup as cleanup

NOW=datetime(2026,10,8,tzinfo=timezone.utc).timestamp()
FLAGS={'versions_checked':True,'object_lock_checked':True,'unversioned':True,'object_lock_disabled':True}


def private(path,data):
    path.write_bytes(data);os.chmod(path,0o600)


@pytest.fixture
def setup(tmp_path,monkeypatch):
    root=tmp_path/'backups';stage=tmp_path/'stage';receipts=tmp_path/'receipts'
    for p in (root,stage,receipts):p.mkdir(mode=0o700)
    holds=tmp_path/'holds.json';private(holds,json.dumps({'version':1,'holds':{'local':[],'staging':[],'remote':[]}}).encode())
    monkeypatch.setattr(cleanup,'ROOT',root);monkeypatch.setattr(cleanup,'STAGING',stage);monkeypatch.setattr(cleanup,'HOLDS',holds)
    monkeypatch.setattr(cleanup,'_no_active',lambda:None)
    monkeypatch.setattr(cleanup,'_ledger_guard',lambda:None)
    monkeypatch.setattr(cleanup.pdn_s3_policy,'verify',lambda:dict(FLAGS))
    monkeypatch.setattr(cleanup.backup_offsite_envelope,'public_key',lambda p:object())
    monkeypatch.setattr(cleanup.backup_offsite_envelope,'recipient_id',lambda key:'b'*64)
    items=[]
    for namespace,count in cleanup.NAMESPACES.items():
        folder=root/namespace;folder.mkdir(mode=0o700)
        for i in range(count):
            path=folder/('backup-20260901_000000-'+str(i)+'.tar.gz.enc');raw=b'WBSAASBK1'+namespace.encode()+str(i).encode();private(path,raw)
            items.append({'namespace':namespace,'path':path.relative_to(root).as_posix(),'cipher_sha256':hashlib.sha256(raw).hexdigest(),'decrypted_tar_sha256':'c'*64,'db_sha256':'d'*64,'nested_zip_unknown':0,'nested_zip_db_hashes':[],
                          'remote_retirement_proof':{'matched':True,'original_verified':True,'replacement_verified':True,'old_remote_deleted':True,'quarantine':False,'replacement_sha256':'e'*64}})
    items[15]['nested_zip_db_hashes']=['f'*64]*15;items[30]['nested_zip_db_hashes']=['f'*64]*59
    checks={'read_only':True,'archives_authenticated':47,'main_sqlite_checked':47,'nested_sqlite_checked':74,'sqlite_integrity_ok':121,'nested_zip_crc_ok':74,'nested_manifest_contains_token_false':74,'live_tenant_instances_for_each_namespace':0,'replacement_to_active_mvvorokov_proved':False,'disposal_category':'retired_empty_rehearsal_candidate_not_automatic_authorization'}
    proof=tmp_path/'proof.json';value={'items':items,'checks':checks};private(proof,json.dumps(value).encode());sha=hashlib.sha256(proof.read_bytes()).hexdigest()
    return {'root':root,'stage':stage,'receipts':receipts,'holds':holds,'proof':proof,'sha':sha,'value':value}


def repin(fixture):
    private(fixture['proof'],json.dumps(fixture['value']).encode());fixture['sha']=hashlib.sha256(fixture['proof'].read_bytes()).hexdigest()


def test_dryrun_never_deletes_and_writes_private_manifest(setup,monkeypatch):
    monkeypatch.setattr(cleanup,'_delete',lambda row:pytest.fail('dryrun deleted'))
    result=cleanup.run(setup['proof'],setup['sha'],setup['receipts'],now=NOW)
    assert result['apply'] is False and result['candidate_count']==47
    assert sum(1 for p in setup['root'].rglob('*.enc'))==47
    files=list(setup['receipts'].iterdir());assert len(files)==1 and files[0].name.endswith('-manifest.json')


@pytest.mark.parametrize('change',['count','namespace','traversal','digest','nested','checks','quarantine','duplicate'])
def test_malformed_attestation_never_passes_even_when_repinned(setup,change):
    v=setup['value']
    if change=='count':v['items'].pop()
    elif change=='namespace':v['items'][0]['namespace']='mvvorokov'
    elif change=='traversal':v['items'][0]['path']='mvvorokov-4b5d/../current.tar.gz.enc'
    elif change=='digest':v['items'][0]['cipher_sha256']='bad'
    elif change=='nested':v['items'][0]['nested_zip_unknown']=1
    elif change=='checks':v['checks']['sqlite_integrity_ok']=120
    elif change=='quarantine':v['items'][0]['remote_retirement_proof']['quarantine']=True
    elif change=='duplicate':v['items'][1]=dict(v['items'][0])
    repin(setup)
    with pytest.raises(ValueError):cleanup.plan(setup['proof'],setup['sha'],now=NOW)


def test_proof_hash_change_aborts(setup):
    setup['proof'].write_bytes(setup['proof'].read_bytes()+b' ')
    with pytest.raises(ValueError,match='proof_changed'):cleanup.plan(setup['proof'],setup['sha'],now=NOW)


def test_local_hash_change_aborts_before_any_delete(setup,monkeypatch):
    path=setup['root']/setup['value']['items'][0]['path'];path.write_bytes(b'WBSAASBK1changed')
    monkeypatch.setattr(cleanup,'_delete',lambda row:pytest.fail('deleted before all validation'))
    with pytest.raises(ValueError,match='digest_changed'):cleanup.run(setup['proof'],setup['sha'],setup['receipts'],apply=True,now=NOW)


def test_unlisted_local_file_never_silently_disposed(setup):
    private(setup['root']/'mvvorokov-4b5d'/'other.tar.gz.enc',b'WBSAASBK1unrelated')
    with pytest.raises(ValueError,match='file_set_changed'):cleanup.plan(setup['proof'],setup['sha'],now=NOW)


def test_legal_hold_aborts(setup):
    private(setup['holds'],json.dumps({'version':1,'holds':{'local':['mvvorokov-4b5d'],'staging':[],'remote':[]}}).encode())
    with pytest.raises(ValueError,match='holds_not'):cleanup.plan(setup['proof'],setup['sha'],now=NOW)


def test_active_identity_aborts(setup,monkeypatch):
    def active():raise ValueError('rehearsal_active_container')
    monkeypatch.setattr(cleanup,'_no_active',active)
    with pytest.raises(ValueError,match='active'):cleanup.plan(setup['proof'],setup['sha'],now=NOW)


def test_young_original_snapshot_cannot_use_old_mtime(setup):
    with pytest.raises(ValueError,match='not_expired'):cleanup.plan(setup['proof'],setup['sha'],now=datetime(2026,9,5,tzinfo=timezone.utc).timestamp())


def add_stage(setup):
    source=setup['value']['items'][0];folder=setup['stage']/source['namespace'];folder.mkdir(mode=0o700)
    source_size=(setup['root']/source['path']).stat().st_size
    path=folder/'exact.tar.gz.enc';info={'recipient':'b'*64,'wrapped_key':base64.b64encode(bytes(384)).decode(),'nonce':base64.b64encode(bytes(12)).decode(),'source_sha256':source['cipher_sha256'],'source_bytes':source_size};encoded=json.dumps(info).encode();raw=b'MHOFFSITE2'+struct.pack('>I',len(encoded))+encoded+bytes(source_size+16)
    private(path,raw);private(folder/(path.name+'.sha256'),hashlib.sha256(raw).hexdigest().encode())
    return path,{'sha256':hashlib.sha256(raw).hexdigest(),'size':len(raw)}


def test_stage_requires_remote_exact_full_digest(setup,monkeypatch):
    path,remote=add_stage(setup);calls=[]
    def read(relative):calls.append(relative);return dict(remote)
    monkeypatch.setattr(cleanup,'_remote',read)
    manifest=cleanup.plan(setup['proof'],setup['sha'],now=NOW)
    assert len(manifest['candidates'])==50 and calls==['offsite-v2/mvvorokov-4b5d/exact.tar.gz.enc']
    remote['sha256']='a'*64
    with pytest.raises(ValueError,match='remote_digest'):cleanup.plan(setup['proof'],setup['sha'],now=NOW)


def test_unrelated_stage_source_aborts(setup,monkeypatch):
    path,remote=add_stage(setup);raw=path.read_bytes().replace(setup['value']['items'][0]['cipher_sha256'].encode(),b'a'*64);private(path,raw)
    monkeypatch.setattr(cleanup,'_remote',lambda p:remote)
    with pytest.raises(ValueError,match='unrelated_source'):cleanup.plan(setup['proof'],setup['sha'],now=NOW)


def test_apply_intent_done_receipt_and_only_pinned_files(setup,monkeypatch):
    deleted=[]
    # Never perform actual fixture deletion: exercise receipt state with stub.
    monkeypatch.setattr(cleanup,'_delete',lambda row:deleted.append(row['path']))
    result=cleanup.run(setup['proof'],setup['sha'],setup['receipts'],apply=True,now=NOW)
    assert result['deleted_count']==47 and set(deleted)=={r['path'] for r in setup['value']['items']}
    receipt=json.loads(next(setup['receipts'].glob('*-receipt.json')).read_bytes())
    assert receipt['state']=='completed' and all(e['state']=='done' for e in receipt['events'])


def test_change_after_intent_is_recorded_failed_without_delete(setup,monkeypatch):
    original=cleanup._reread;count=0
    def changed(row):
        nonlocal count
        count+=1
        value=original(row)
        if count==2:value['sha256']='0'*64
        return value
    monkeypatch.setattr(cleanup,'_reread',changed)
    monkeypatch.setattr(cleanup,'_delete',lambda row:pytest.fail('changed file deleted'))
    with pytest.raises(ValueError,match='after_intent'):cleanup.run(setup['proof'],setup['sha'],setup['receipts'],apply=True,now=NOW)
    receipt=json.loads(next(setup['receipts'].glob('*-receipt.json')).read_bytes())
    assert receipt['state']=='failed' and receipt['deleted_count']==0 and receipt['events'][0]['state']=='intent'


def test_provider_unverified_aborts(setup,monkeypatch):
    monkeypatch.setattr(cleanup.pdn_s3_policy,'verify',lambda:{'unversioned':False})
    with pytest.raises(ValueError,match='provider_policy'):cleanup.plan(setup['proof'],setup['sha'],now=NOW)


def test_missing_live_ledger_aborts_even_empty_rehearsal_cleanup(setup,monkeypatch):
    def unavailable():raise ValueError('ledger_unavailable')
    monkeypatch.setattr(cleanup,'_ledger_guard',unavailable)
    with pytest.raises(ValueError,match='ledger'):cleanup.plan(setup['proof'],setup['sha'],now=NOW)


def test_concurrent_new_hold_stops_before_delete(setup,monkeypatch):
    original=cleanup._holds;calls=0
    def changed():
        nonlocal calls
        calls+=1
        return '0'*64 if calls>2 else original()
    monkeypatch.setattr(cleanup,'_holds',changed)
    monkeypatch.setattr(cleanup,'_delete',lambda r:pytest.fail('new hold ignored'))
    with pytest.raises(ValueError,match='holds_changed'):cleanup.run(setup['proof'],setup['sha'],setup['receipts'],apply=True,now=NOW)
