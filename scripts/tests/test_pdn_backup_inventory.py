import os
from datetime import datetime, timezone

import pytest

from scripts.pdn_backup_inventory import inventory, remote_inventory, summarize

NOW = datetime(2026, 10, 8, tzinfo=timezone.utc).timestamp()


def test_local_age_and_unknown_formats_never_authorize_deletion(tmp_path):
    group = tmp_path / 'synthetic'
    group.mkdir()
    old = group / 'old.tar.gz.enc'
    old.write_bytes(b'fixture')
    os.utime(old, (NOW - 15 * 86400,) * 2)
    fresh = group / 'fresh.tar.gz.enc'
    fresh.write_bytes(b'fixture')
    os.utime(fresh, (NOW - 86400,) * 2)
    (group / 'private.key').write_text('synthetic')
    rows = inventory(tmp_path, 14, NOW)
    assert {r['status'] for r in rows} == {'age_candidate', 'within_window', 'unknown_format'}
    assert summarize(rows)['age_candidate'] == 1
    assert all('eligible_for_deletion' not in r for r in rows)
    assert old.exists() and fresh.exists()


def test_symlinks_are_not_followed(tmp_path):
    external = tmp_path / 'external'
    external.mkdir()
    (external / 'secret').write_text('fixture')
    root = tmp_path / 'root'
    root.mkdir()
    try:
        (root / 'linked').symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip('Host cannot create symlinks')
    rows = inventory(root, 14, NOW)
    assert len(rows) == 1 and rows[0]['status'] == 'unsafe_path'
    assert 'secret' not in str(rows)


def test_remote_missing_or_future_dates_block_age_classification():
    rows = remote_inventory([
        {'Path': 'offsite-v2/synthetic/a.tar.gz.enc', 'Size': 1},
        {'Path': 'offsite-v2/synthetic/b.tar.gz.enc', 'Size': 1, 'ModTime': '2027-01-01T00:00:00Z'},
    ], 14, NOW)
    assert [r['status'] for r in rows] == ['missing_timestamp', 'future_timestamp']


@pytest.mark.parametrize('path', ['../other/a.tar.gz.enc', '/absolute.tar.gz.enc', 'offsite-v2/../a.tar.gz.enc', 'a\\b.tar.gz.enc'])
def test_remote_unsafe_path_is_rejected(path):
    result = remote_inventory([{'Path': path, 'Size': 1, 'ModTime': '2026-09-01T00:00:00Z'}], 14, NOW)
    assert result[0]['status'] == 'unsafe_path'


def test_remote_age_directories_and_unknown_size():
    rows = remote_inventory([
        {'Path': 'offsite-v2/synthetic/old.tar.gz.enc', 'Size': 5, 'ModTime': '2026-09-01T00:00:00Z'},
        {'Path': 'offsite-v2/synthetic', 'IsDir': True},
        {'Path': 'offsite-v2/synthetic/b.tar.gz.enc', 'Size': -1, 'ModTime': '2026-09-01T00:00:00Z'},
    ], 14, NOW)
    assert len(rows) == 2
    assert rows[0]['status'] == 'age_candidate'
    assert rows[1]['status'] == 'invalid_size'


def test_staging_digest_sidecars_are_inventory_not_archive(tmp_path):
    group = tmp_path / 'synthetic'
    group.mkdir()
    (group / 'old.tar.gz.enc.sha256').write_text('fixture')
    assert inventory(tmp_path, 14, NOW)[0]['kind'] == 'digest_sidecar'


def test_missing_root_is_failure(tmp_path):
    with pytest.raises(ValueError, match='invalid_root'):
        inventory(tmp_path / 'missing', 14, NOW)
