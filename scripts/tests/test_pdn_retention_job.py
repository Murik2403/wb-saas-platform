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
