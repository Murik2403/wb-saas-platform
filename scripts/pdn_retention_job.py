"""Host scheduler: independently restore retained data before exact-file cleanup."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import re
import select
import struct
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import backup_offsite
import backup_offsite_envelope
import pdn_erasure_backup
import pdn_retention as retention

ROOT=Path('/opt/wb-saas/backups')
STAGING=Path('/opt/wb-saas/offsite-v2-staging')
REMOTE='timeweb-s3:marketshelper-backups'
ALIASES={'67324db':'control-plane'}
MAX_REMOTE_OBJECT_BYTES=4*1024**3
REMOTE_READ_TIMEOUT_SECONDS=900
MAX_RESTORE_UNCOMPRESSED_BYTES=8*1024**3
RESTORE_TIMEOUT_SECONDS=900
RESTORE_DISK_RESERVE_BYTES=8*1024**2


def command(args, *, timeout=180):
    result=subprocess.run(args,capture_output=True,timeout=timeout)
    if result.returncode:raise RuntimeError('retention_transport_failed')
    return result.stdout


def object_content(path,size):
    if type(size) is not int or not 0<size<=MAX_REMOTE_OBJECT_BYTES:raise ValueError('archive_size_limit')
    process=subprocess.Popen(['rclone','cat',REMOTE+'/'+path,'--retries','1','--low-level-retries','1'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
    digest=hashlib.sha256();header=b'';count=0;started=time.monotonic()
    try:
        while True:
            if time.monotonic()-started>REMOTE_READ_TIMEOUT_SECONDS:raise ValueError('archive_read_limit')
            if not select.select([process.stdout],[],[],1)[0]:continue
            block=os.read(process.stdout.fileno(),1024*1024)
            if not block:break
            count+=len(block);digest.update(block)
            if len(header)<8192:header+=block[:8192-len(header)]
            if count>size or time.monotonic()-started>REMOTE_READ_TIMEOUT_SECONDS:raise ValueError('archive_read_limit')
        if process.wait(timeout=10)!=0 or count!=size:raise RuntimeError('retention_transport_failed')
        return digest.hexdigest(),header
    finally:
        if process.poll() is None:process.kill();process.wait()
        process.stdout.close()


def metadata(path,item=None):
    if item is None:
        items=json.loads(command(['rclone','lsjson',REMOTE+'/'+path,'--stat','--hash','--retries','1','--low-level-retries','1']))
        item=items if isinstance(items,dict) else items[0]
    raw=re.sub(r'(\.\d{6})\d+(?=[+-]|Z)',r'\1',item['ModTime']).replace('Z','+00:00')
    stamp=int(datetime.fromisoformat(raw).timestamp()*1e9)
    digest,head=object_content(path,item['Size'])
    fmt='SQLite3' if head.startswith(b'SQLite format 3\0') else next((value for value in ('WBSAASBK1','WBSAASBK2','MHOFFSITE2') if head.startswith(value.encode())), '')
    row={'path':path,'size':item['Size'],'mtime_ns':stamp,'sha256':digest,'format':fmt,'version_id':item.get('VersionID','')}
    if retention._kind(path)=='digest':
        match=re.match(rb'^([0-9a-f]{64})(?:\s|$)',head)
        row['archive_sha256']=match.group(1).decode() if match else ''
    return row,head


def restore_inner(path):
    relative=path.relative_to(ROOT).as_posix()
    retention._parts(relative)
    code='''import json,os,tempfile,sqlite3,tarfile,time,shutil
from pathlib import Path
from backup_crypto import decrypt_file
source=Path('/backups')/'''+repr(relative)+'''
MAX_SOURCE_BYTES='''+str(MAX_REMOTE_OBJECT_BYTES)+'''
MAX_UNCOMPRESSED_BYTES='''+str(MAX_RESTORE_UNCOMPRESSED_BYTES)+'''
DISK_RESERVE_BYTES='''+str(RESTORE_DISK_RESERVE_BYTES)+'''
deadline=time.monotonic()+'''+str(RESTORE_TIMEOUT_SECONDS)+'''
def guard_space(folder,needed):
 assert time.monotonic()<deadline and needed>=0
 assert shutil.disk_usage(folder).free>=needed+DISK_RESERVE_BYTES
def sqlite_ok(path):
 c=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
 c.set_progress_handler(lambda: time.monotonic()>=deadline,10000)
 try: assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
 finally:c.close()
with tempfile.TemporaryDirectory(prefix='pdn-restore-') as tmp:
 assert 0<source.stat().st_size<=MAX_SOURCE_BYTES
 guard_space(tmp,source.stat().st_size)
 target=Path(tmp)/'restored'
 options={}
 for env_name,option_name in (('WB_SAAS_BACKUP_ENCRYPTION_KEY_ID','key_id'),('WB_SAAS_BACKUP_LEGACY_KEY_ID','legacy_key_id')):
  if os.environ.get(env_name):options[option_name]=os.environ[env_name]
 if os.environ.get('WB_SAAS_BACKUP_ENCRYPTION_KEYRING'):
  from backup_crypto import parse_keyring
  options['keyring']=parse_keyring(os.environ['WB_SAAS_BACKUP_ENCRYPTION_KEYRING'])
 decrypt_file(source,target,os.environ['WB_SAAS_BACKUP_ENCRYPTION_KEY'],**options)
 assert target.stat().st_size<=MAX_SOURCE_BYTES and time.monotonic()<deadline
 with target.open('rb') as f:magic=f.read(16)
 if magic.hex()=='53514c69746520666f726d6174203300':sqlite_ok(target);count=1
 else:
  count=total=0
  with tarfile.open(target,'r:*') as archive:
   for member in archive:
    assert not member.issym() and not member.islnk() and not member.name.startswith('/') and '..' not in Path(member.name).parts
    if not member.isfile():continue
    total+=member.size;assert 0<=member.size and total<=MAX_UNCOMPRESSED_BYTES and time.monotonic()<deadline
    with archive.extractfile(member) as f:head=f.read(16)
    if head.hex()=='53514c69746520666f726d6174203300':
     p=Path(tmp)/('db-'+str(count))
     guard_space(tmp,member.size)
     with archive.extractfile(member) as f,p.open('wb') as out:
      while block:=f.read(1024*1024):
       assert time.monotonic()<deadline;out.write(block)
     sqlite_ok(p);p.unlink();count+=1
  assert count>0
 print(json.dumps({'restore_ok':True,'sqlite_databases':count}))
'''
    output=command(['docker','exec','--user','10001:10001','wb-saas-provisioner-1','python','-c',code],timeout=RESTORE_TIMEOUT_SECONDS)
    if json.loads(output).get('restore_ok') is not True:raise ValueError('restore_failed')


def envelope_header(raw):
    offset=len(backup_offsite_envelope.MAGIC)
    if not raw.startswith(backup_offsite_envelope.MAGIC) or len(raw)<offset+4:raise ValueError('envelope_invalid')
    length=struct.unpack('>I',raw[offset:offset+4])[0]
    if not 1<=length<=4096 or len(raw)<offset+4+length:raise ValueError('envelope_invalid')
    return json.loads(raw[offset+4:offset+4+length])


def source_for(row,local_sources,trusted_envelopes=None):
    if row['format'] in ('WBSAASBK1','WBSAASBK2'):source_sha=row['sha256']
    elif row['format']=='MHOFFSITE2':
        if row.get('mode')=='remote':
            if trusted_envelopes is None or row['sha256'] not in trusted_envelopes:raise ValueError('untrusted_remote_envelope')
            _,raw=metadata(row['path'])
        else:raw=Path(row['absolute']).read_bytes()[:8192]
        header=envelope_header(raw);source_sha=header['source_sha256']
        public=backup_offsite_envelope.public_key('/opt/wb-saas/offsite-recipient-public.pem')
        if header['recipient']!=backup_offsite_envelope.recipient_id(public):raise ValueError('recipient_changed')
    else:raise ValueError('encrypted_recovery_required')
    if source_sha not in local_sources:raise ValueError('independent_recovery_source_missing')
    return local_sources[source_sha]


def archive_creation(name,fallback):
    # Date-bearing names are produced by the committed gateway/provisioner
    # snapshot writers. A copy/envelope must not restart the source's age.
    found=re.search(r'(?:^|[-_])(\d{8})[-_T](\d{6})(?:_|Z|[.\-])',name)
    if found:
        try:
            stamp=int(datetime.strptime(found[1]+found[2],'%Y%m%d%H%M%S').replace(tzinfo=timezone.utc).timestamp()*1e9)
            return min(fallback,stamp)
        except ValueError:pass
    found=re.search(r'-(1[6-9]\d{8})\.sqlite3\.enc$',name)
    return min(fallback,int(found[1])*10**9) if found else fallback


def inspect_remote_listing(listed, dates, *, inspector=None, max_workers=4):
    """Inspect every known recovery object before selecting by original age.

    ModTime is transport metadata: a recently uploaded old envelope can expire,
    and the newest recovery by creation may not have the newest upload time.
    Only exact known layouts/formats are read; no deletion is performed here.
    Catalog updates are sequential after bounded parallel content inspection.
    """
    if type(max_workers) is not int or not 1<=max_workers<=4:
        raise ValueError('remote_inspection_workers_invalid')
    inspector=metadata if inspector is None else inspector
    selected=[];seen=set()
    for item in listed:
        path=item['Path']
        try:parts=retention._parts(path,remote=True)
        except ValueError:continue
        if item.get('IsDir') is True or retention._kind(parts[-1])=='unknown':continue
        if path in seen:raise ValueError('duplicate_remote_path')
        seen.add(path);selected.append((path,item))
    def inspect(pair):
        return inspector(pair[0],pair[1])
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        results=list(executor.map(inspect,selected))
    inspected=[]
    for row,head in results:
        digest=row['sha256']
        if row['format']=='MHOFFSITE2':
            predecessor=envelope_header(head)['source_sha256']
            if not isinstance(predecessor,str) or not retention.HASH.fullmatch(predecessor):
                raise ValueError('envelope_source_digest_invalid')
            stamp=min(row['mtime_ns'],dates.get(predecessor,dates.get(digest,row['mtime_ns'])))
        else:stamp=archive_creation(row['path'],row['mtime_ns'])
        # Preserve already established creation dates; rereading/copying must
        # never replace a protected older catalog value with a recent ModTime.
        dates[digest]=min(dates.get(digest,stamp),stamp)
        inspected.append(row)
    return inspected


def provenance():
    catalog_path=Path('/opt/wb-saas/pdn-archive-origins.json')
    if catalog_path.is_symlink() or not catalog_path.is_file() or catalog_path.stat().st_mode&0o077:raise ValueError('creation_catalog_unavailable')
    catalog=json.loads(catalog_path.read_text())
    if catalog.get('version')!=1 or not isinstance(catalog.get('creation_by_sha256'),dict):raise ValueError('creation_catalog_invalid')
    dates=catalog['creation_by_sha256'];sources={};trusted_envelopes=set()
    for group in ROOT.iterdir():
        if not group.is_dir() or group.is_symlink() or retention._reserved([group.name]):continue
        for file in group.iterdir():
            if not file.is_file() or file.is_symlink() or retention._kind(file.name) not in {'encrypted','sqlite'}:continue
            digest=retention._hash(file);stamp=archive_creation(file.name,file.stat().st_mtime_ns)
            dates[digest]=min(dates.get(digest,stamp),stamp)
            with file.open('rb') as stream:magic=stream.read(9)
            if magic in (b'WBSAASBK1',b'WBSAASBK2'):sources[digest]=file
    for group in STAGING.iterdir():
        if not group.is_dir() or group.is_symlink():continue
        for file in group.glob('*.enc'):
            if file.is_symlink():raise ValueError('unsafe_staging')
            header,_=backup_offsite_envelope.header(file)
            cipher_sha=retention._hash(file);trusted_envelopes.add(cipher_sha)
            if header['source_sha256'] in dates:
                digest=cipher_sha;stamp=dates[header['source_sha256']]
                dates[digest]=min(dates.get(digest,stamp),stamp)
    backup_offsite.write_status(catalog_path,catalog)
    return dates,sources,catalog_path,catalog_path.read_bytes(),trusted_envelopes


def run(*,apply=False):
    import pdn_s3_policy
    provider=pdn_s3_policy.verify()
    if not provider.get('unversioned') or not provider.get('object_lock_disabled'):raise ValueError('provider_policy_not_verified')
    holds_path=Path('/opt/wb-saas/pdn-legal-holds.json')
    if holds_path.is_symlink() or not holds_path.is_file() or holds_path.stat().st_mode&0o077:raise ValueError('legal_holds_registry_unavailable')
    holds_raw=holds_path.read_bytes();holds=json.loads(holds_raw)
    if holds.get('version')!=1 or set(holds.get('holds',{}))!={'local','staging','remote'} or any(not isinstance(v,list) or any(not isinstance(p,str) for p in v) for v in holds['holds'].values()):raise ValueError('legal_holds_registry_invalid')
    def holds_unchanged():
        if holds_path.read_bytes()!=holds_raw:raise ValueError('legal_holds_changed')
    state=json.loads(Path('/opt/wb-saas/erasure-backup-status.json').read_text())
    if state.get('offsite_verified') is not True or not 0<=time.time()-state['verified_at']<=1800:raise ValueError('ledger_backup_stale')
    actual=hashlib.sha256(pdn_erasure_backup.snapshot('/opt/wb-saas/erasure-ledger/erasures.sqlite3')).hexdigest()
    if actual!=state['source_sha256']:raise ValueError('ledger_checkpoint_changed')
    guard=json.loads(command(['docker','exec','wb-saas-gateway-1','python','-m','logic.erasure_recovery','--check']))
    if not guard.get('enabled') or guard.get('status')!='ok':raise ValueError('erasure_guard_missing')
    # This retired directory contains a historical gateway DB, not a live tenant.
    identity_check="from logic import db\nwith db.connect() as c:\n assert c.execute(\"SELECT 1 FROM tenant_instances WHERE slug='67324db'\").fetchone() is None"
    command(['docker','exec','wb-saas-gateway-1','python','-c',identity_check])
    dates,sources,catalog_path,catalog_raw,trusted_envelopes=provenance()
    local=retention.plan_local(ROOT,days=14,namespace_replacements={k:v for k,v in ALIASES.items() if (ROOT/k).is_dir() and any(retention._kind(p.name) in {'encrypted','sqlite'} for p in (ROOT/k).iterdir())},legal_holds=holds['holds']['local'],creation_by_sha256=dates)
    staging=retention.plan_local(STAGING,days=14,legal_holds=holds['holds']['staging'],creation_by_sha256=dates)
    listed=json.loads(command(['rclone','lsjson',REMOTE,'--recursive','--files-only','--retries','1','--low-level-retries','1']))
    inspected=inspect_remote_listing(listed,dates)
    # Bind every inspected object's original age before any manifest is applied.
    backup_offsite.write_status(catalog_path,{'version':1,'creation_by_sha256':dates});catalog_raw=catalog_path.read_bytes()
    remote=retention.plan_remote(inspected,days=14,namespace_replacements={k:v for k,v in ALIASES.items() if any(retention._parts(r['path'],remote=True)[0]==k for r in inspected)},legal_holds=holds['holds']['remote'],creation_by_sha256=dates)
    for manifest in (local,staging,remote):
        if any(row['created_ns']<int((time.time()-14*86400)*1e9) for row in manifest['retained']):
            raise ValueError('expired_recovery_floor_requires_classification')
    alias_path=Path('/opt/wb-saas/pdn-namespace-replacements.json')
    if alias_path.is_symlink() or not alias_path.is_file() or alias_path.stat().st_mode&0o077:raise ValueError('namespace_proof_unavailable')
    aliases=json.loads(alias_path.read_text())
    for manifest in (local,remote):
        for row in manifest['candidates']:
            if row['namespace'] in ALIASES:
                proof=aliases.get(row['namespace'],{})
                if proof.get('target')!=ALIASES[row['namespace']] or proof.get('source_sha256')!=row['sha256'] or proof.get('identity_lineage_retained') is not True or proof.get('financial_history_retained') is not True:
                    raise ValueError('namespace_lineage_proof_mismatch')
    # Provider versioning is checked on each run; never delete only current versions.
    folder=Path('/opt/wb-saas/pdn-retention-receipts')/str(time.time_ns());folder.mkdir(parents=True,mode=0o700)
    receipts=[];validated=set()
    for name,manifest in [('local',local),('staging',staging),('remote',remote)]:
        retention._private_write(folder/(name+'-manifest.json'),manifest,exclusive=True)
        floor={r['namespace']:r for r in manifest['retained']};hashes={}
        needed={manifest['namespace_replacements'].get(r['namespace'],r['namespace']) for r in manifest['candidates']}
        for group in needed:
            row=dict(floor[group]);row['mode']=name
            if name!='remote':row['absolute']=str(Path(manifest['root'])/row['path'])
            source=source_for(row,sources,trusted_envelopes)
            if source not in validated:restore_inner(source);validated.add(source)
            hashes[group]=row['sha256']
        proof={'manifest_sha256':manifest['manifest_sha256'],'verified_at':time.time(),'restore_ok':True,'erasure_guard_ok':True,'independent_copy':True,'recovery_sha256_by_namespace':hashes,'verified_namespace_replacements':manifest['namespace_replacements'],'versions_checked':True,'object_lock_checked':True,'legal_holds_checked':True}
        retention._private_write(folder/(name+'-proof.json'),proof,exclusive=True)
        if apply:
            holds_unchanged()
            if catalog_path.read_bytes()!=catalog_raw:raise ValueError('creation_catalog_changed')
            if name=='remote':
                def delete_remote(path):
                    holds_unchanged();command(['rclone','deletefile',REMOTE+'/'+path,'--retries','1','--low-level-retries','1'])
                receipt=retention.apply_remote(manifest,acknowledgment=manifest['manifest_sha256'],proof=proof,receipt_path=folder/(name+'-receipt.json'),read_metadata=lambda p:metadata(p)[0],delete_file=delete_remote)
            else:receipt=retention.apply_local(manifest,acknowledgment=manifest['manifest_sha256'],proof=proof,receipt_path=folder/(name+'-receipt.json'),guard=holds_unchanged)
            receipts.append({'scope':name,'deleted_count':receipt['deleted_count'],'state':receipt['state']})
        else:receipts.append({'scope':name,'candidates':len(manifest['candidates'])})
    result={'apply':apply,'receipts':receipts,'verified_recovery_sources':len(validated),'verified_at':time.time(),'retention_ok':True}
    backup_offsite.write_status(Path('/opt/wb-saas/pdn-retention-status.json'),result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    try:
        if os.geteuid()!=0:raise ValueError('host_root_required')
        import fcntl
        with open('/var/lock/marketshelper-backup-cron.lock','a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            result=run(apply=args.apply)
        print(json.dumps(result));return 0
    except Exception:
        from run_backup_cron import alert_command
        env=os.environ.copy();env['WB_SAAS_BACKUP_ALERT_COMMAND_JSON']=json.dumps(alert_command('mvvorokov@yandex.ru'))
        status={'retention_ok':False,'alert':backup_offsite.alert({'kind':'retention_failure','code':'remote_retention_failed'},env)}
        backup_offsite.write_status(Path('/opt/wb-saas/pdn-retention-status.json'),status)
        print(json.dumps(status));return 1


if __name__=='__main__':raise SystemExit(main())
