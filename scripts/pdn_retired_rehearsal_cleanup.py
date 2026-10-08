"""Exact-file disposal of 47 independently inspected empty rehearsal archives.

Dry-run by default. This is an explicit disposable-category exception, never a
replacement/alias to a live customer's recovery floor. No recursive removals.
Pinned proof SHA is the operator's content-review attestation, not a filename
inference. All unknown/new files and all holds abort disposal.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import time
import uuid

try:
    from scripts import pdn_s3_policy, backup_offsite_envelope, pdn_retention
except ModuleNotFoundError:
    import pdn_s3_policy, backup_offsite_envelope, pdn_retention

ROOT = Path('/opt/wb-saas/backups')
STAGING = Path('/opt/wb-saas/offsite-v2-staging')
HOLDS = Path('/opt/wb-saas/pdn-legal-holds.json')
PUBLIC_KEY = Path('/opt/wb-saas/offsite-recipient-public.pem')
REMOTE = 'timeweb-s3:marketshelper-backups'
NAMESPACES = {'mvvorokov-4b5d': 15, 'mvvorokov-5079': 15, 'mvvorokov-7fab': 17}
HASH = re.compile(r'[0-9a-f]{64}\Z')
MAX_BYTES = 10 * 1024 * 1024


def _protected(path, *, file=True):
    path = Path(path)
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('rehearsal_unsafe_path')
    info = path.stat()
    if file and (not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES):
        raise ValueError('rehearsal_invalid_file')
    if os.name != 'nt' and (info.st_mode & (0o077 if file else 0o022) or info.st_uid != 0):
        raise ValueError('rehearsal_protected_file_required')
    return path


def _json(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError('rehearsal_duplicate_json_key')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs)


def load_proof(path, expected_sha):
    if not isinstance(expected_sha, str) or not HASH.fullmatch(expected_sha):
        raise ValueError('rehearsal_proof_pin_required')
    raw = _protected(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha:
        raise ValueError('rehearsal_proof_changed')
    value = _json(raw)
    required = {'read_only': True, 'archives_authenticated': 47, 'main_sqlite_checked': 47,
                'nested_sqlite_checked': 74, 'sqlite_integrity_ok': 121,
                'nested_zip_crc_ok': 74, 'nested_manifest_contains_token_false': 74,
                'live_tenant_instances_for_each_namespace': 0,
                'replacement_to_active_mvvorokov_proved': False,
                'disposal_category': 'retired_empty_rehearsal_candidate_not_automatic_authorization'}
    if not isinstance(value, dict) or value.get('checks') != required or not isinstance(value.get('items'), list) or len(value['items']) != 47:
        raise ValueError('rehearsal_content_attestation_invalid')
    counts = dict.fromkeys(NAMESPACES, 0)
    seen = set()
    hashes = set()
    for row in value['items']:
        if not isinstance(row, dict) or row.get('namespace') not in NAMESPACES:
            raise ValueError('rehearsal_scope_invalid')
        parts = str(row.get('path', '')).split('/')
        if len(parts) != 2 or parts[0] != row['namespace'] or not re.fullmatch(r'[A-Za-z0-9_.-]+\.tar\.gz\.enc', parts[1]) or '..' in parts or row['path'] in seen:
            raise ValueError('rehearsal_path_invalid')
        for field in ('cipher_sha256', 'decrypted_tar_sha256', 'db_sha256'):
            if not isinstance(row.get(field), str) or not HASH.fullmatch(row[field]):
                raise ValueError('rehearsal_digest_invalid')
        if row['cipher_sha256'] in hashes:
            raise ValueError('rehearsal_duplicate_digest')
        nested = row.get('nested_zip_db_hashes')
        if row.get('nested_zip_unknown') != 0 or not isinstance(nested, list) or any(not isinstance(h, str) or not HASH.fullmatch(h) for h in nested):
            raise ValueError('rehearsal_nested_proof_invalid')
        prior = row.get('remote_retirement_proof', {})
        if not isinstance(prior, dict) or any(prior.get(k) is not True for k in ('matched', 'original_verified', 'replacement_verified', 'old_remote_deleted')) or not isinstance(prior.get('replacement_sha256'), str) or not HASH.fullmatch(prior['replacement_sha256']) or prior.get('quarantine') is not False:
            raise ValueError('rehearsal_historic_proof_invalid')
        counts[row['namespace']] += 1
        seen.add(row['path'])
        hashes.add(row['cipher_sha256'])
    if counts != NAMESPACES or sum(len(r['nested_zip_db_hashes']) for r in value['items']) != 74:
        raise ValueError('rehearsal_proof_count_invalid')
    return value


def _command(args):
    result = subprocess.run(args, capture_output=True, timeout=30)
    if result.returncode:
        raise ValueError('rehearsal_transport_failed')
    return result.stdout


def _no_active():
    names = _command(['docker', 'ps', '-a', '--format', '{{.Names}}']).decode().splitlines()
    if any(name.startswith('wb-tenant-' + slug) for name in names for slug in NAMESPACES):
        raise ValueError('rehearsal_active_container')
    code = "import sqlite3\nc=sqlite3.connect('file:/app/data/gateway.sqlite3?mode=ro',uri=True)\n" + '\n'.join("assert c.execute('SELECT COUNT(*) FROM tenant_instances WHERE slug=?',(" + repr(slug) + ",)).fetchone()[0]==0" for slug in NAMESPACES)
    _command(['docker', 'exec', 'wb-saas-gateway-1', 'python3', '-c', code])


def _holds():
    raw = _protected(HOLDS).read_bytes()
    value = _json(raw)
    if not isinstance(value, dict) or value.get('version') != 1 or value.get('holds') != {'local': [], 'staging': [], 'remote': []}:
        raise ValueError('rehearsal_holds_not_confirmed_absent')
    return hashlib.sha256(raw).hexdigest()


def _ledger_guard():
    code = "import os\nfrom logic import erasure_ledger\nassert os.environ.get('WB_SAAS_ERASURE_LEDGER_REQUIRED')=='1'\np=erasure_ledger.configured_path()\nassert p is not None\nerasure_ledger.validate_path(p)"
    _command(['docker', 'exec', 'wb-saas-gateway-1', 'python3', '-c', code])


def _remote(path):
    if not path.startswith('offsite-v2/') or len(path.split('/')) != 3 or path.split('/')[1] not in NAMESPACES or not re.fullmatch(r'[A-Za-z0-9_.-]+', path.split('/')[-1]):
        raise ValueError('rehearsal_remote_scope_invalid')
    # Objects are bounded by the already validated local envelope size. Provider
    # cap is also enforced via --head, followed by exact bytes/digest equality.
    raw = _command(['rclone', 'cat', REMOTE + '/' + path, '--head', str(MAX_BYTES + 1), '--retries', '1', '--low-level-retries', '1', '--timeout', '10s'])
    if len(raw) > MAX_BYTES:
        raise ValueError('rehearsal_remote_too_large')
    return {'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw)}


def _file_row(scope, path, relative):
    path = _protected(path)
    raw = path.read_bytes()
    info = path.stat()
    return {'scope': scope, 'path': relative, 'sha256': hashlib.sha256(raw).hexdigest(),
            'size': len(raw), 'mtime_ns': info.st_mtime_ns, 'inode': info.st_ino,
            'device': info.st_dev}


def _age(name, now):
    found = re.search(r'(?<!\d)(2026\d{4})(?:[-_T]?(\d{6}))?', name)
    if not found:
        raise ValueError('rehearsal_original_age_unknown')
    try:
        stamp = datetime.strptime(found[1], '%Y%m%d').replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        raise ValueError('rehearsal_original_age_unknown') from None
    # Taking end-of-day is conservative for every timestamp from that date.
    if stamp + 86400 >= now - 14 * 86400:
        raise ValueError('rehearsal_not_expired')


def plan(proof_path, expected_sha, *, now=None):
    now = time.time() if now is None else now
    proof = load_proof(proof_path, expected_sha)
    policy = pdn_s3_policy.verify()
    if policy != {'versions_checked': True, 'object_lock_checked': True, 'unversioned': True, 'object_lock_disabled': True}:
        raise ValueError('rehearsal_provider_policy_unverified')
    _no_active()
    _ledger_guard()
    holds_sha = _holds()
    candidates = []
    local_hashes = {r['cipher_sha256'] for r in proof['items']}
    expected_paths = {r['path'] for r in proof['items']}
    for group in NAMESPACES:
        folder = _protected(ROOT / group, file=False)
        if not folder.is_dir():
            raise ValueError('rehearsal_namespace_missing')
        actual = {p.relative_to(ROOT).as_posix() for p in folder.iterdir()}
        if actual != {p for p in expected_paths if p.startswith(group + '/')}:
            raise ValueError('rehearsal_local_file_set_changed')
    for row in proof['items']:
        _age(Path(row['path']).name, now)
        current = _file_row('local', ROOT / row['path'], row['path'])
        if current['sha256'] != row['cipher_sha256'] or not (ROOT / row['path']).read_bytes().startswith(b'WBSAASBK1'):
            raise ValueError('rehearsal_local_digest_changed')
        candidates.append(current)
    recipient = backup_offsite_envelope.recipient_id(backup_offsite_envelope.public_key(PUBLIC_KEY))
    for group in NAMESPACES:
        folder = STAGING / group
        if not folder.exists():
            continue
        _protected(folder, file=False)
        covered = set()
        for path in sorted(folder.iterdir()):
            if path.name.endswith('.sha256'):
                continue
            if not path.name.endswith('.tar.gz.enc'):
                raise ValueError('rehearsal_unknown_staging_file')
            row = _file_row('staging', path, path.relative_to(STAGING).as_posix())
            header, _ = backup_offsite_envelope.header(path)
            if header['source_sha256'] not in local_hashes or header['recipient'] != recipient:
                raise ValueError('rehearsal_staging_unrelated_source')
            # Source also must belong to this exact namespace, not another of3.
            if not any(r['namespace'] == group and r['cipher_sha256'] == header['source_sha256'] for r in proof['items']):
                raise ValueError('rehearsal_staging_namespace_mismatch')
            source_row = next(r for r in candidates if r['scope'] == 'local' and r['sha256'] == header['source_sha256'])
            nonce = base64.b64decode(header['nonce'], validate=True)
            wrapped = base64.b64decode(header['wrapped_key'], validate=True)
            _, aad = backup_offsite_envelope.header(path)
            if type(header['source_bytes']) is not int or header['source_bytes'] != source_row['size'] or len(nonce) != 12 or not 384 <= len(wrapped) <= 1024 or row['size'] != len(aad) + source_row['size'] + 16:
                raise ValueError('rehearsal_staging_envelope_invalid')
            candidates.append(row)
            covered.add(path.name)
            sidecar = path.with_name(path.name + '.sha256')
            if not sidecar.exists():
                raise ValueError('rehearsal_digest_sidecar_missing')
            digest_row = _file_row('staging', sidecar, sidecar.relative_to(STAGING).as_posix())
            if sidecar.read_bytes() != row['sha256'].encode():
                raise ValueError('rehearsal_digest_sidecar_changed')
            candidates.append(digest_row)
            covered.add(sidecar.name)
            remote_path = 'offsite-v2/' + row['path']
            remote = _remote(remote_path)
            if remote != {'sha256': row['sha256'], 'size': row['size']}:
                raise ValueError('rehearsal_remote_digest_changed')
            candidates.append({'scope': 'remote', 'path': remote_path, **remote})
        if covered != {p.name for p in folder.iterdir()}:
            raise ValueError('rehearsal_unpaired_staging_file')
    manifest = {'version': 1, 'category': 'retired_empty_rehearsal', 'proof_sha256': expected_sha,
                'holds_sha256': holds_sha, 'days': 14, 'candidates': candidates}
    manifest['manifest_sha256'] = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return manifest


def _reread(row):
    if row['scope'] == 'remote':
        return {'scope': 'remote', 'path': row['path'], **_remote(row['path'])}
    root = ROOT if row['scope'] == 'local' else STAGING
    return _file_row(row['scope'], root / row['path'], row['path'])


def _delete(row):
    if row['scope'] == 'remote':
        _command(['rclone', 'deletefile', REMOTE + '/' + row['path'], '--retries', '1', '--low-level-retries', '1', '--timeout', '10s'])
    else:
        (ROOT if row['scope'] == 'local' else STAGING).joinpath(row['path']).unlink()


def run(proof_path, expected_sha, receipt_dir, *, apply=False, now=None):
    """Caller holds backup flock. Dry-run writes protected manifest only."""
    folder = _protected(receipt_dir, file=False)
    if not folder.is_dir():
        raise ValueError('rehearsal_receipt_directory_required')
    manifest = plan(proof_path, expected_sha, now=now)
    tag = uuid.uuid4().hex
    manifest_path = folder / (tag + '-manifest.json')
    pdn_retention._private_write(manifest_path, manifest, exclusive=True)
    if not apply:
        return {'apply': False, 'category': manifest['category'], 'candidate_count': len(manifest['candidates']), 'manifest_sha256': manifest['manifest_sha256']}
    # Rebuild ALL local/remote/holds/proof/active checks before first deletion.
    if plan(proof_path, expected_sha, now=now) != manifest:
        raise ValueError('rehearsal_preapply_state_changed')
    receipt_path = folder / (tag + '-receipt.json')
    receipt = {'manifest_sha256': manifest['manifest_sha256'], 'state': 'started', 'events': [], 'deleted_count': 0}
    pdn_retention._private_write(receipt_path, receipt, exclusive=True)
    # Remote first; leave local recovery until exact offsite deletions succeed.
    ordered = sorted(manifest['candidates'], key=lambda r: {'remote': 0, 'staging': 1, 'local': 2}[r['scope']])
    try:
        for row in ordered:
            load_proof(proof_path, expected_sha)
            if _holds() != manifest['holds_sha256']:
                raise ValueError('rehearsal_holds_changed')
            _no_active()
            _ledger_guard()
            if row['scope'] == 'remote':
                pdn_s3_policy.verify()
            if _reread(row) != row:
                raise ValueError('rehearsal_file_changed_before_delete')
            event = {'scope': row['scope'], 'path': row['path'], 'sha256': row['sha256'], 'state': 'intent'}
            receipt['events'].append(event)
            pdn_retention._private_write(receipt_path, receipt)
            if _reread(row) != row:
                raise ValueError('rehearsal_file_changed_after_intent')
            _delete(row)
            event['state'] = 'done'
            receipt['deleted_count'] += 1
            pdn_retention._private_write(receipt_path, receipt)
        receipt['state'] = 'completed'
        pdn_retention._private_write(receipt_path, receipt)
        return {'apply': True, 'category': manifest['category'], 'deleted_count': receipt['deleted_count'], 'state': 'completed'}
    except Exception:
        receipt['state'] = 'failed'
        pdn_retention._private_write(receipt_path, receipt)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--proof', type=Path, required=True)
    parser.add_argument('--expected-proof-sha256', required=True)
    parser.add_argument('--receipts', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        if not hasattr(os, 'geteuid') or os.geteuid() != 0:
            raise ValueError('host_root_required')
        import fcntl
        lock_path = Path('/var/lock/marketshelper-backup-cron.lock')
        if lock_path.is_symlink():
            raise ValueError('rehearsal_unsafe_lock')
        with lock_path.open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = run(args.proof, args.expected_proof_sha256, args.receipts, apply=args.apply)
        print(json.dumps(result))
        return 0
    except Exception:
        print(json.dumps({'status': 'error', 'reason': 'rehearsal_cleanup_failed'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
