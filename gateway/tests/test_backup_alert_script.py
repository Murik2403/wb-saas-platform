import importlib.util
from pathlib import Path
from unittest.mock import Mock
import config
spec=importlib.util.spec_from_file_location('backup_alert',Path(__file__).resolve().parents[2]/'scripts'/'backup_alert.py')
alert=importlib.util.module_from_spec(spec);spec.loader.exec_module(alert)

def test_unconfigured_smtp_does_not_claim_handoff(monkeypatch):
    sender=Mock();monkeypatch.setattr(config,'SMTP_HOST','');monkeypatch.setattr(alert.mailer,'send_email',sender)
    assert alert.notify('owner@example.test',{'code':'local_backup_stale'})==1
    sender.assert_not_called()

def test_smtp_acceptance_and_failure_reported_exactly_and_body_allowlisted(monkeypatch):
    monkeypatch.setattr(config,'SMTP_HOST','synthetic')
    sender=Mock(return_value='accepted');monkeypatch.setattr(alert.mailer,'send_email',sender)
    assert alert.notify('owner@example.test',{'code':'offsite_backup_stale','private':'never-send'})==0
    assert 'never-send' not in sender.call_args.args[2]
    sender.return_value='failed';assert alert.notify('owner@example.test',{'code':'offsite_backup_stale'})==1
    assert alert.notify('owner@example.test',{'code':'private-token'})==1


def test_inventory_failure_reaches_real_mailer_with_fake_smtp_only(monkeypatch):
    """Exercise notify -> send_email -> SMTP handoff, never real transport."""
    monkeypatch.setattr(config,'SMTP_HOST','smtp.synthetic.test')
    monkeypatch.setattr(config,'SMTP_PORT',587)
    monkeypatch.setattr(config,'SMTP_USE_STARTTLS',False)
    monkeypatch.setattr(config,'SMTP_USER','')
    monkeypatch.setattr(config,'SMTP_FROM_EMAIL','no-reply@example.test')
    messages=[]
    class FakeSMTP:
        def __init__(self,host,port,timeout):
            assert host=='smtp.synthetic.test' and port==587 and timeout==15
        def __enter__(self):return self
        def __exit__(self,*args):return None
        def send_message(self,message):messages.append(message)
    monkeypatch.setattr(alert.mailer.smtplib,'SMTP',FakeSMTP)
    payload={'kind':'backup_inventory_failure','code':'backup_inventory_unavailable','private':'credential-must-not-be-sent'}
    assert alert.notify('owner@example.test',payload)==0
    assert len(messages)==1
    assert messages[0]['To']=='owner@example.test'
    body=messages[0].get_content()
    assert 'backup_inventory_unavailable' in body
    assert 'credential-must-not-be-sent' not in body
