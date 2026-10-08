import sys
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
