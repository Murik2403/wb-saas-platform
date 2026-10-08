import importlib.util
from pathlib import Path
import sys
from unittest import mock

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import pdn_backup_job as job


def test_transport_failure_does_not_echo_provider_credentials():
    with mock.patch.object(job.subprocess, 'run', return_value=mock.Mock(returncode=1, stderr=b'secret provider detail')):
        with pytest.raises(RuntimeError, match='^ledger_backup_transport_failed$'):
            job.transport(['rclone', 'check'])


def test_remote_target_is_fixed_before_reading_ledger(tmp_path):
    with mock.patch.object(job.ledger_backup, 'encrypt') as encrypt:
        with pytest.raises(ValueError, match='remote_invalid'):
            job.run(tmp_path/'ledger', tmp_path/'output', tmp_path/'recipient', 'other:bucket', tmp_path/'state')
    encrypt.assert_not_called()


def test_verified_copy_checks_bytes_and_refuses_mutating_live_ledger(tmp_path):
    output=tmp_path/'output';output.mkdir();manifest=output/'snapshot.json';manifest.write_text('{}');(output/'snapshot.enc').write_bytes(b'cipher')
    info={'envelope':'snapshot.enc','source_sha256':job.hashlib.sha256(b'original').hexdigest()}
    calls=[]
    with mock.patch.object(job.ledger_backup,'encrypt',return_value=manifest), mock.patch.object(job.ledger_backup,'readvalid',return_value=info), mock.patch.object(job.ledger_backup,'snapshot',return_value=b'changed'):
        with pytest.raises(RuntimeError,match='ledger_changed'):
            job.run(tmp_path/'ledger',output,tmp_path/'key','timeweb-s3:marketshelper-backups/erasure-ledger-v1',tmp_path/'state',send=calls.append)
    assert not (tmp_path/'state').exists()
    assert '--download' in calls[-1]
    assert not (output/'.verify-files').exists()
