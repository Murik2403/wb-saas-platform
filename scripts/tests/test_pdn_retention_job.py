import sys
import json,struct
from datetime import datetime,timezone
from pathlib import Path
from unittest import mock
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pdn_retention_job as job


def test_failed_provider_command_does_not_echo_credential_bearing_errors():
    with mock.patch.object(job.subprocess,'run',return_value=mock.Mock(returncode=1,stderr=b'secret')):
        with pytest.raises(RuntimeError,match='^retention_transport_failed$'):job.command(['rclone','lsjson'])


def test_unknown_recovery_format_is_never_blessed():
    with pytest.raises(ValueError,match='encrypted_recovery_required'):
        job.source_for({'format':'SQLite3','sha256':'a'*64},{})


def test_encrypted_recovery_requires_exact_independent_ciphertext():
    with pytest.raises(ValueError,match='independent_recovery_source_missing'):
        job.source_for({'format':'WBSAASBK1','sha256':'a'*64},{'b'*64:Path('/different')})


def test_remote_parser_recognizes_sqlite_header_and_stable_timestamp():
    with mock.patch.object(job,'object_content',return_value=('a'*64,b'SQLite format 3\0')):
        row,head=job.metadata('old/source.sqlite3',{'ModTime':'2026-09-01T00:00:00.123456789Z','Size':188416})
    assert row['format']=='SQLite3' and row['mtime_ns']>0 and row['path']=='old/source.sqlite3'


def test_restore_probe_is_valid_python_without_null_argv_bytes():
    commands=[]
    def execute(args):
        commands.append(args)
        compile(args[-1],'<restore probe>','exec')
        return b'{"restore_ok":true}'
    with mock.patch.object(job,'command',side_effect=execute):
        job.restore_inner(job.ROOT/'synthetic/good.tar.gz.enc')
    assert '\0' not in commands[0][-1]
    assert commands[0][3]=='10001:10001'


def test_copy_does_not_restart_date_bearing_gateway_archive_age():
    recent=int(datetime(2026,10,8,tzinfo=timezone.utc).timestamp()*1e9)
    original=int(datetime(2026,9,19,23,42,44,tzinfo=timezone.utc).timestamp()*1e9)
    assert job.archive_creation('manual-seo-20260919T234244Z.sqlite3',recent)==original
    assert job.archive_creation('gateway-abcdef0-20260919-234244.sqlite3.enc',recent)==original


def test_unknown_creation_name_remains_explicit_mtime_fallback():
    assert job.archive_creation('unknown.sqlite3.enc',123456789)==123456789


def test_envelope_header_uses_actual_ten_byte_magic():
    value={'source_sha256':'a'*64,'recipient':'b'*64}
    raw=json.dumps(value).encode()
    assert job.envelope_header(b'MHOFFSITE2'+struct.pack('>I',len(raw))+raw+b'cipher')==value


def test_remote_envelope_without_independent_local_ciphertext_is_refused():
    with pytest.raises(ValueError,match='untrusted_remote_envelope'):
        job.source_for({'format':'MHOFFSITE2','mode':'remote','sha256':'a'*64,'path':'remote/copy.enc'},{},{'b'*64})


def _envelope(source_sha):
    raw=json.dumps({'source_sha256':source_sha,'recipient':'f'*64}).encode()
    return b'MHOFFSITE2'+struct.pack('>I',len(raw))+raw


def test_remote_selection_inspects_recent_old_copy_and_actual_original_creation_floor():
    now=datetime(2026,10,8,tzinfo=timezone.utc).timestamp()
    ns=lambda age:int((now-age*86400)*1e9)
    # All upload dates are within14d. The middle item contains expired data,
    # while the real creation-date floor has an older upload than the first.
    definitions=[('a'*64,1,'1'*64,8),('b'*64,2,'2'*64,30),('c'*64,3,'3'*64,3)]
    listing=[{'Path':'offsite-v2/synthetic/'+digest+'.enc','Size':200,'ModTime':'recent'} for digest,_,_,_ in definitions]
    listing.append({'Path':'offsite-v2/synthetic/source.py','Size':200,'ModTime':'old'})
    rows={item['Path']:{'path':item['Path'],'size':200,'mtime_ns':ns(upload_age),
                         'sha256':digest,'format':'MHOFFSITE2','version_id':''}
          for item,(digest,upload_age,_,_) in zip(listing,definitions)}
    predecessor={listing[index]['Path']:source_sha for index,(_,_,source_sha,_) in enumerate(definitions)}
    dates={source_sha:ns(original_age) for _,_,source_sha,original_age in definitions}
    calls=[]
    def inspect(path,item):
        calls.append(path)
        return rows[path],_envelope(predecessor[path])
    inspected=job.inspect_remote_listing(listing,dates,inspector=inspect)
    assert set(calls)==set(rows) and len(inspected)==3
    manifest=job.retention.plan_remote(inspected,now=now,creation_by_sha256=dates)
    assert [row['sha256'] for row in manifest['candidates']]==['b'*64]
    assert [row['sha256'] for row in manifest['retained']]==['c'*64]
    assert dates['b'*64]==ns(30)


def test_remote_selection_preserves_catalog_date_and_reads_recent_digest_sidecars():
    now=datetime(2026,10,8,tzinfo=timezone.utc).timestamp()
    original=int((now-30*86400)*1e9)
    names=['synthetic/copy.tar.gz.enc','synthetic/copy.tar.gz.enc.sha256']
    listing=[{'Path':name,'Size':200,'ModTime':'recent'} for name in names]
    calls=[]
    def inspect(path,item):
        calls.append(path)
        row={'path':path,'size':200,'mtime_ns':int(now*1e9),'sha256':'a'*64 if path==names[0] else 'b'*64,
             'format':'WBSAASBK1' if path==names[0] else '', 'version_id':''}
        if path==names[1]:row['archive_sha256']='a'*64
        return row,b''
    dates={'a'*64:original}
    inspected=job.inspect_remote_listing(listing,dates,inspector=inspect)
    assert set(calls)==set(names) and len(inspected)==2
    assert dates['a'*64]==original


def test_remote_inspection_parallelism_is_bounded_to_four():
    import threading,time
    lock=threading.Lock();active=maximum=0
    listing=[{'Path':f'synthetic/archive-{index}.tar.gz.enc','Size':200} for index in range(12)]
    def inspect(path,item):
        nonlocal active,maximum
        with lock:active+=1;maximum=max(maximum,active)
        try:
            time.sleep(.01)
            return {'path':path,'size':200,'mtime_ns':1,'sha256':'a'*64,'format':'WBSAASBK1'},b''
        finally:
            with lock:active-=1
    result=job.inspect_remote_listing(listing,{},inspector=inspect)
    assert len(result)==12 and 1<=maximum<=4 and active==0
    with pytest.raises(ValueError,match='remote_inspection_workers_invalid'):
        job.inspect_remote_listing(listing,{},inspector=inspect,max_workers=5)


def test_remote_inspection_failure_never_returns_partial_selection():
    listing=[{'Path':'synthetic/a.tar.gz.enc','Size':200}]
    def inspect(path,item):raise RuntimeError('synthetic read failure')
    with pytest.raises(RuntimeError,match='synthetic read failure'):
        job.inspect_remote_listing(listing,{},inspector=inspect)
