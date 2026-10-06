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
