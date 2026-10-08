"""Read-only, fail-closed verification of the fixed MARKETSHELPER Timeweb bucket.

Uses existing root-owned rclone credentials in memory only. Does not read env
credentials, print provider errors, follow redirects or change bucket settings.
This checks provider settings, not the operator's separate legal-hold registry.
"""
from __future__ import annotations

import configparser
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import stat
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

CONFIG_PATH = Path('/root/.config/rclone/rclone.conf')
ENDPOINT = 'https://s3.twcstorage.ru'
BUCKET = 'marketshelper-backups'
LIMIT = 65536


class PolicyUnavailable(RuntimeError):
    """No sensitive path, identity, credential or raw provider response text."""


def _is_root():
    return hasattr(os, 'geteuid') and os.geteuid() == 0


def _config():
    try:
        if not _is_root() or any(p.is_symlink() for p in (CONFIG_PATH, *CONFIG_PATH.parents)):
            raise ValueError()
        info = CONFIG_PATH.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077 or info.st_size > 1024 * 1024:
            raise ValueError()
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_string(CONFIG_PATH.read_text(encoding='utf-8'))
        value = dict(parser['timeweb-s3'])
        if value.get('type') != 's3' or value.get('env_auth', 'false').lower() != 'false':
            raise ValueError()
        return value
    except (OSError, ValueError, KeyError, configparser.Error):
        raise PolicyUnavailable('s3_policy_config_unavailable') from None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _opener():
    # Do not let environment proxy configuration change the credentials route.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())


def _request(value, query):
    if value.get('endpoint') != ENDPOINT or value.get('region') != 'ru-1':
        raise PolicyUnavailable('s3_policy_fixed_endpoint_required')
    access, secret = value.get('access_key_id'), value.get('secret_access_key')
    if not isinstance(access, str) or not isinstance(secret, str) or not access or not secret or any(ch in access + secret for ch in '\r\n'):
        raise PolicyUnavailable('s3_policy_config_unavailable')
    if query not in ('versioning', 'object-lock'):
        raise PolicyUnavailable('s3_policy_read_only_query_required')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    day = stamp[:8]
    digest = hashlib.sha256(b'').hexdigest()
    headers = {'host': 's3.twcstorage.ru', 'x-amz-content-sha256': digest, 'x-amz-date': stamp}
    signed = ';'.join(sorted(headers))
    canonical = 'GET\n/' + BUCKET + '\n' + query + '=\n' + ''.join(k + ':' + headers[k] + '\n' for k in sorted(headers)) + '\n' + signed + '\n' + digest
    scope = day + '/ru-1/s3/aws4_request'
    text = 'AWS4-HMAC-SHA256\n' + stamp + '\n' + scope + '\n' + hashlib.sha256(canonical.encode()).hexdigest()
    def sign(key, item):
        return hmac.new(key, item.encode(), hashlib.sha256).digest()
    key = sign(sign(sign(sign(('AWS4' + secret).encode(), day), 'ru-1'), 's3'), 'aws4_request')
    signature = hmac.new(key, text.encode(), hashlib.sha256).hexdigest()
    headers['authorization'] = 'AWS4-HMAC-SHA256 Credential=' + access + '/' + scope + ', SignedHeaders=' + signed + ', Signature=' + signature
    return urllib.request.Request(ENDPOINT + '/' + BUCKET + '?' + query, headers=headers, method='GET')


def _get(opener, value, query):
    request = _request(value, query)
    try:
        try:
            response = opener.open(request, timeout=10)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            status = response.status if hasattr(response, 'status') else response.code
            raw = response.read(LIMIT + 1)
        if len(raw) > LIMIT or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
            raise ValueError()
        root = ET.fromstring(raw)
        return status, root
    except (OSError, ValueError, ET.ParseError):
        raise PolicyUnavailable('s3_policy_response_unavailable') from None


def _name(element):
    return element.tag.split('}')[-1]


def verify():
    """Return all four True flags only for unversioned + absent Object Lock.

    Raises PolicyUnavailable on enabled/suspended/unknown policy, missing access,
    network errors, redirects, malformed responses or unexpected fixed config.
    """
    value = _config()
    opener = _opener()
    status, root = _get(opener, value, 'versioning')
    # Empty VersioningConfiguration is S3's never-enabled bucket state. Suspended
    # is unsafe: existing noncurrent versions may survive ordinary deletions.
    if status != 200 or _name(root) != 'VersioningConfiguration' or list(root) or (root.text or '').strip():
        raise PolicyUnavailable('s3_policy_versioning_not_unversioned')
    status, root = _get(opener, value, 'object-lock')
    children = list(root)
    codes = [child.text for child in children if _name(child) == 'Code']
    if status != 404 or _name(root) != 'Error' or codes != ['ObjectLockConfigurationNotFoundError']:
        raise PolicyUnavailable('s3_policy_object_lock_not_confirmed_disabled')
    return {'versions_checked': True, 'object_lock_checked': True,
            'unversioned': True, 'object_lock_disabled': True}


def main():
    try:
        print(json.dumps(verify(), sort_keys=True))
        return 0
    except PolicyUnavailable:
        print(json.dumps({'status': 'error', 'reason': 's3_policy_verification_failed'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
