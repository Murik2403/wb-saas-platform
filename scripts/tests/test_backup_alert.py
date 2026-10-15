"""Exercise the same source-exec boundary used by the host cron, without SMTP."""
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]


def execute(monkeypatch, payload, *, result='accepted', recipient='owner@example.com', configured=True):
    calls = []
    monkeypatch.setitem(sys.modules, 'config', SimpleNamespace(SMTP_HOST='smtp.example.invalid' if configured else ''))
    monkeypatch.setitem(sys.modules, 'mailer', SimpleNamespace(send_email=lambda *args: calls.append(args) or result))
    monkeypatch.setattr(sys, 'argv', ['-c', '--to', recipient])
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    monkeypatch.setattr(sys, 'stdin', io.TextIOWrapper(io.BytesIO(raw), encoding='utf-8'))
    with pytest.raises(SystemExit) as exited:
        exec((ROOT/'scripts/backup_alert.py').read_text(encoding='utf-8'),
             {'__name__': '__main__', '__file__': '/app/scripts/backup_alert.py'})
    return exited.value.code, calls


def test_source_exec_accepts_gateway_explicit_transport_success(monkeypatch):
    code, calls = execute(monkeypatch, {'kind': 'backup_prerequisite', 'code': 'separate_key_escrow_unconfirmed'})
    assert code == 0
    assert len(calls) == 1 and calls[0][0] == 'owner@example.com'
    assert 'separate_key_escrow_unconfirmed' in calls[0][2]


@pytest.mark.parametrize('result', ['failed', 'disabled', None, True])
def test_source_exec_never_claims_unconfirmed_handoff_accepted(monkeypatch, result):
    code, calls = execute(monkeypatch, {'code': 'local_backup_stale'}, result=result)
    assert code == 1 and len(calls) == 1


@pytest.mark.parametrize('payload', [b'not-json', b'\xff', b' ' * 4097,
                                    {'code': 'private-provider-token'}, [], {'code': None}])
def test_source_exec_rejects_invalid_payload_without_mail_or_output(monkeypatch, capsys, payload):
    code, calls = execute(monkeypatch, payload)
    assert code == 1 and not calls
    assert capsys.readouterr() == ('', '')


def test_source_exec_never_forwards_arbitrary_provider_payload_fields(monkeypatch, capsys):
    code, calls = execute(monkeypatch, {'code': 'local_backup_stale', 'stderr': 'secret-provider-token',
                                      'password': 'secret-provider-token', 'data': 'secret-financial-data'})
    assert code == 0 and len(calls) == 1
    assert 'secret-' not in str(calls)
    assert capsys.readouterr() == ('', '')


@pytest.mark.parametrize('options', [{'configured': False}, {'recipient': 'owner@example.com\nBcc: other@example.com'}])
def test_source_exec_rejects_disabled_smtp_or_invalid_recipient_before_handoff(monkeypatch, options):
    code, calls = execute(monkeypatch, {'code': 'local_backup_stale'}, **options)
    assert code == 1 and not calls
