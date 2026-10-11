from unittest.mock import Mock
import os
import pytest
from fastapi import HTTPException
import app
import backup_crypto as crypto

def test_new_backup_namespace_helper_owner_and_archive_uid(tmp_path,monkeypatch):
    monkeypatch.setattr(app,'BACKUP_MOUNT',tmp_path)
    monkeypatch.setattr(app,'BACKUP_DIR','/host/new-backups')
    monkeypatch.setattr(app,'BACKUP_ENCRYPTION_KEY','x'*40)
    client=Mock();client.containers.run.return_value.wait.return_value={'StatusCode':0}
    monkeypatch.setattr(app,'encrypt_file',lambda src,dst,key:dst.write_bytes(b'encrypted'))
    result=app._backup_volume('wb-tenant-data-new','new-shop',14,client)
    prepare,archive=client.containers.run.call_args_list
    assert prepare.kwargs['user']=='0:0' and prepare.kwargs['cap_add']==['CHOWN','FOWNER']
    assert prepare.kwargs['command'][-1]=='/backup/new-shop'
    assert archive.kwargs['user']=='10001:10001' and archive.kwargs['cap_drop']==['ALL']
    assert archive.kwargs['detach'] and archive.kwargs['network_disabled']
    assert result['archive'].endswith('.enc') and not list((tmp_path/'new-shop').glob('*.partial'))

def test_timed_out_archive_removes_helper_and_partials(tmp_path,monkeypatch):
    monkeypatch.setattr(app,'BACKUP_MOUNT',tmp_path);monkeypatch.setattr(app,'BACKUP_ENCRYPTION_KEY','x'*40)
    prepare,archive=Mock(),Mock();prepare.wait.return_value={'StatusCode':0};archive.wait.side_effect=TimeoutError('private details')
    client=Mock();client.containers.run.side_effect=[prepare,archive]
    with pytest.raises(HTTPException) as failure:app._backup_volume('volume','new-shop',14,client)
    assert 'private' not in failure.value.detail
    archive.remove.assert_called_once_with(force=True)
    assert not list((tmp_path/'new-shop').iterdir())

def test_crypto_temporary_is_private_from_creation_and_collision_not_deleted(tmp_path,monkeypatch):
    source=tmp_path/'source';source.write_bytes(b'fixture')
    destination=tmp_path/'encrypted';temporary=tmp_path/'.encrypted.tmp'
    calls=[];original=os.open
    monkeypatch.setattr(crypto.os,'open',lambda path,flags,mode:(calls.append(mode),original(path,flags,mode))[1])
    crypto.encrypt_file(source,destination,'x'*40)
    assert calls==[0o600] and not temporary.exists()
    temporary.write_bytes(b'other-operation')
    with pytest.raises(FileExistsError):crypto.encrypt_file(source,destination,'x'*40)
    assert temporary.read_bytes()==b'other-operation'

def test_two_gib_source_is_consumed_only_in_bounded_chunks(tmp_path,monkeypatch):
    class Stream:
        remaining=2*1024**3
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,size):
            assert size==crypto.CHUNK_SIZE
            count=min(size,self.remaining);self.remaining-=count
            return b'x'*count
    class Source:
        def open(self,mode):assert mode=='rb';return Stream()
    class Encryptor:
        calls=0;tag=b'x'*16
        def authenticate_additional_data(self,value):assert value.startswith(b'WBSAASBK2')
        def update(self,value):self.calls+=1;return b''
        def finalize(self):return b''
    encryptor=Encryptor()
    monkeypatch.setattr(crypto,'Cipher',lambda *_:Mock(encryptor=lambda:encryptor))
    crypto.encrypt_file(Source(),tmp_path/'large.enc','x'*40)
    assert encryptor.calls==2048

def test_startup_cleanup_only_abandoned_temporary_outputs(tmp_path,monkeypatch):
    import time
    monkeypatch.setattr(app,'BACKUP_MOUNT',tmp_path)
    directory=tmp_path/'fixture';directory.mkdir()
    old=directory/'.old.tar.gz.partial';old.write_bytes(b'plaintext');os.utime(old,(time.time()-7200,)*2)
    active=directory/'.active.tar.gz.partial';active.write_bytes(b'active')
    legacy=directory/'historical.tar.gz';legacy.write_bytes(b'legacy')
    assert app._cleanup_stale_backup_partials()==1 and not old.exists()
    assert active.exists() and legacy.exists()

def test_crypto_deadline_removes_private_partial(tmp_path,monkeypatch):
    source=tmp_path/'source';source.write_bytes(b'fixture')
    ticks=iter([0,121]);monkeypatch.setattr(crypto.time,'monotonic',lambda:next(ticks))
    with pytest.raises(TimeoutError):crypto.encrypt_file(source,tmp_path/'archive.enc','x'*40)
    assert not (tmp_path/'archive.enc').exists() and not (tmp_path/'.archive.enc.tmp').exists()
