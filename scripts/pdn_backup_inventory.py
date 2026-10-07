"""Read-only recovery-copy metadata inventory; deliberately has no delete mode.

Age candidates are not approvals: holds, contents, recoverability and S3 versions
must be assessed separately. Never imports provider credentials or decrypts data.
"""
import argparse
import json
import math
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


def _policy(days, now):
    if type(days) is not int or not 1 <= days <= 3650:
        raise ValueError('invalid_days')
    if not isinstance(now, (int, float)) or not math.isfinite(now) or now <= 0:
        raise ValueError('invalid_now')


def _kind(name):
    if name.endswith('.tar.gz.enc.sha256'):
        return 'digest_sidecar'
    if name.endswith('.tar.gz.enc'):
        return 'encrypted_archive'
    if name.endswith('.tar.gz'):
        return 'plaintext_archive'
    if name.endswith('.partial'):
        return 'partial'
    return 'unknown'


def _row(path, namespace, size, stamp, days, now, status=None):
    kind = _kind(path)
    row = {'path': path, 'namespace': namespace, 'kind': kind, 'size': size}
    if status:
        row['status'] = status
    elif type(size) is not int or size < 0:
        row['status'] = 'invalid_size'
    elif stamp is None:
        row['status'] = 'missing_timestamp'
    elif not math.isfinite(stamp) or stamp > now:
        row['status'] = 'future_timestamp'
    else:
        row['age_days'] = round((now - stamp) / 86400, 3)
        row['status'] = 'unknown_format' if kind == 'unknown' else (
            'age_candidate' if now - stamp > days * 86400 else 'within_window')
    return row


def inventory(root, days, now):
    _policy(days, now)
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('invalid_root')
    result = []
    # Scan only namespace/file: never recursively traverse an unexpected tree.
    for group in sorted(root.iterdir()):
        if group.is_symlink() or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', group.name):
            result.append(_row(group.name, '', None, None, days, now, 'unsafe_path'))
            continue
        if not group.is_dir():
            result.append(_row(group.name, '', None, None, days, now, 'unexpected_root_file'))
            continue
        for item in sorted(group.iterdir()):
            path = group.name + '/' + item.name
            if item.is_symlink() or not item.is_file():
                result.append(_row(path, group.name, None, None, days, now, 'unsafe_path'))
                continue
            try:
                stat = item.stat(follow_symlinks=False)
                result.append(_row(path, group.name, stat.st_size, stat.st_mtime, days, now))
            except OSError:
                result.append(_row(path, group.name, None, None, days, now, 'unreadable_metadata'))
    return result


def remote_inventory(items, days, now):
    _policy(days, now)
    if not isinstance(items, list):
        raise ValueError('invalid_remote_listing')
    result = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('invalid_remote_listing')
        path = item.get('Path', '')
        if not isinstance(path, str):
            path = ''
        parts = PurePosixPath(path).parts
        safe = bool(path and not path.startswith('/') and '\\' not in path
                    and '..' not in parts and len(parts) >= 2
                    and all(re.fullmatch(r'[a-zA-Z0-9_.-]+', part) for part in parts))
        if not safe:
            result.append(_row(path, '', None, None, days, now, 'unsafe_path'))
            continue
        if item.get('IsDir') is True:
            continue
        # offsite-v2/group/archive and historical group/archive are both reported.
        namespace = parts[-2]
        stamp = None
        raw = item.get('ModTime')
        if isinstance(raw, str):
            try:
                date = datetime.fromisoformat(raw.replace('Z', '+00:00'))
                if date.tzinfo is not None:
                    stamp = date.timestamp()
            except (ValueError, OverflowError):
                pass
        result.append(_row(path, namespace, item.get('Size'), stamp, days, now))
    return result


def summarize(rows):
    return dict(sorted(Counter(row['status'] for row in rows).items()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--local', type=Path, required=True)
    parser.add_argument('--staging', type=Path, required=True)
    parser.add_argument('--remote-json', type=Path)
    parser.add_argument('--days', type=int, default=14)
    args = parser.parse_args()
    now = time.time()
    try:
        local = inventory(args.local, args.days, now)
        staging = inventory(args.staging, args.days, now)
        remote = remote_inventory(json.loads(args.remote_json.read_text(encoding='utf-8')),
                                  args.days, now) if args.remote_json else None
        sections = {'local': local, 'staging': staging, 'remote': remote}
        report = {'mode': 'read_only', 'deletion_authorized': False,
                  'checked_at': datetime.fromtimestamp(now, timezone.utc).isoformat(),
                  'proposed_days': args.days, 'remote_versions_checked': False,
                  'object_lock_checked': False, 'legal_holds_checked': False,
                  'timestamp_basis': 'filesystem_mtime_or_rclone_modtime_not_data_creation',
                  'summary': {name: summarize(rows) if rows is not None else {'not_examined': 1}
                              for name, rows in sections.items()}, 'rows': sections}
        print(json.dumps(report, ensure_ascii=False))
        return 0
    except (ValueError, OSError, TypeError):
        # Never echo full paths, provider output or user data in failure logs.
        print(json.dumps({'mode': 'read_only', 'error': 'inventory_failed'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
