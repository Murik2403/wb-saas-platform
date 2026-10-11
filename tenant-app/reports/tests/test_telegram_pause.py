from unittest.mock import Mock
from reports import delivery
from report_scheduler import deliver_report

def test_pause_blocks_notification_link_and_document_without_http(monkeypatch):
    monkeypatch.setenv('WB_SAAS_TELEGRAM_ENABLED','0')
    monkeypatch.setattr(delivery.config,'INTERNAL_API_URL','https://example.test')
    monkeypatch.setattr(delivery.config,'INTERNAL_API_SECRET','synthetic')
    monkeypatch.setattr(delivery.config,'TENANT_SLUG','synthetic')
    post=Mock()
    monkeypatch.setattr(delivery.requests,'post',post)
    assert not delivery.send_text_telegram('synthetic')
    assert not delivery.send_report_telegram('synthetic',b'%PDF','test.pdf')
    assert delivery.request_telegram_link_code('1') is None
    post.assert_not_called()

def test_pause_preserves_email_delivery_and_schedule_preference(monkeypatch):
    monkeypatch.setenv('WB_SAAS_TELEGRAM_ENABLED','0')
    email=Mock(return_value=True);telegram=Mock()
    monkeypatch.setattr(delivery,'send_report_email',email)
    monkeypatch.setattr(delivery,'send_report_telegram',telegram)
    definition={'name':'synthetic','email_enabled':True,'telegram_enabled':True}
    store=Mock()
    result=deliver_report(definition,b'%PDF','test.pdf',store=store,run_id=1)
    assert result=={'email':True}
    store.finish_delivery.assert_called_once_with(1,status='ok',error=None)
    telegram.assert_not_called()
    assert definition['telegram_enabled'] is True
