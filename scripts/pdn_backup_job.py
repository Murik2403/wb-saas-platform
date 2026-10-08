"""Host-only independent ledger offsite backup; no deletion or ledger replacement."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

import backup_offsite
import pdn_erasure_backup as ledger_backup


def transport(args):
    try:
        result = subprocess.run(args, capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError('ledger_backup_transport_failed') from None
    if result.returncode:
        raise RuntimeError('ledger_backup_transport_failed')


def run(ledger, output, recipient, remote, state, *, send=transport):
    if not remote.startswith('timeweb-s3:marketshelper-backups/erasure-ledger-v1') or remote != 'timeweb-s3:marketshelper-backups/erasure-ledger-v1':
        raise ValueError('ledger_backup_remote_invalid')
    manifest = ledger_backup.encrypt(Path(ledger), Path(output), Path(recipient))
    info = ledger_backup.readvalid(manifest)
    cipher = manifest.parent / info['envelope']
    for file in (cipher, manifest):
        send(['rclone', 'copyto', str(file), remote+'/'+file.name, '--immutable', '--retries', '1', '--low-level-retries', '1'])
    # check/download verifies bytes, not just a matching size in the object store.
    files = manifest.parent / ('.verify-files-'+uuid.uuid4().hex+'.partial')
    descriptor = os.open(files, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(cipher.name+'\n'+manifest.name+'\n')
        send(['rclone', 'check', str(manifest.parent), remote, '--files-from', str(files), '--one-way', '--download', '--retries', '1', '--low-level-retries', '1'])
    finally:
        files.unlink(missing_ok=True)
    ledger_backup.readvalid(manifest)
    if hashlib.sha256(ledger_backup.snapshot(ledger)).hexdigest() != info['source_sha256']:
        raise RuntimeError('ledger_changed_during_copy')
    result = {'verified_at': time.time(), 'source_sha256': info['source_sha256'],
              'manifest': manifest.name, 'offsite_verified': True,
              'plaintext_written': False, 'live_ledger_replaced': False}
    backup_offsite.write_status(Path(state), result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ledger', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--recipient', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    try:
        if os.geteuid() != 0:
            raise RuntimeError('host_root_required')
        if args.check:
            state = json.loads(args.state.read_text())
            if state.get('offsite_verified') is not True or not 0 <= time.time()-state['verified_at'] <= 1800:
                raise RuntimeError('ledger_backup_stale')
            print(json.dumps({'ledger_backup_fresh': True})); return 0
        import fcntl
        with open('/var/lock/marketshelper-erasure-backup.lock', 'a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print(json.dumps({'skipped': 'backup_running'})); return 0
            result = run(args.ledger, args.output, args.recipient,
                         'timeweb-s3:marketshelper-backups/erasure-ledger-v1', args.state)
        print(json.dumps({'offsite_verified': result['offsite_verified']}))
        return 0
    except Exception:
        env = os.environ.copy()
        from run_backup_cron import alert_command
        env['WB_SAAS_BACKUP_ALERT_COMMAND_JSON'] = json.dumps(alert_command('mvvorokov@yandex.ru'))
        status = {'attempt_at': time.time(), 'offsite_verified': False, 'error': 'ledger_backup_failed',
                  'alert': backup_offsite.alert({'kind': 'erasure_backup_failure', 'code': 'ledger_backup_failed'}, env)}
        backup_offsite.write_status(args.state, status)
        print(json.dumps(status)); return 1


if __name__ == '__main__':
    raise SystemExit(main())
