from unittest import mock
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
import config, telegram_bot, internal_routes
from app import app

@pytest.mark.parametrize('send',[lambda:telegram_bot._call('getUpdates'),lambda:telegram_bot.send_message('synthetic','hello'),lambda:telegram_bot.send_document('synthetic',b'%PDF','test.pdf')])
def test_pause_blocks_every_outbound_transport(send):
    with mock.patch.object(config,'TELEGRAM_ENABLED',False,create=True),mock.patch.object(config,'TELEGRAM_BOT_TOKEN','synthetic-token'),mock.patch.object(telegram_bot.requests,'post') as post:
        with pytest.raises(RuntimeError,match='temporarily disabled'):send()
        post.assert_not_called()

def test_pause_exits_poller_without_getme_or_getupdates():
    with mock.patch.object(config,'TELEGRAM_ENABLED',False,create=True),mock.patch.object(config,'TELEGRAM_BOT_TOKEN','synthetic-token'),mock.patch.object(telegram_bot.requests,'post') as post:
        telegram_bot.get_bot_username.cache_clear()
        assert telegram_bot.get_bot_username()==''
        telegram_bot.run()
        post.assert_not_called()
    telegram_bot.get_bot_username.cache_clear()

@pytest.mark.parametrize('operation',[lambda:internal_routes.create_telegram_link_code('synthetic','1'),lambda:internal_routes.deliver_text_telegram('synthetic','hello'),lambda:internal_routes.deliver_report_telegram('synthetic','test',b'%PDF','test.pdf')])
def test_paused_operations_do_not_read_or_mutate_customer_data(operation):
    with mock.patch.object(config,'TELEGRAM_ENABLED',False,create=True),mock.patch.object(internal_routes.control_db,'connect') as connect,mock.patch.object(telegram_bot.requests,'post') as post:
        with pytest.raises(HTTPException) as e:operation()
        assert e.value.status_code==503
        connect.assert_not_called()
        post.assert_not_called()

def test_home_has_email_support_without_telegram_links_when_paused():
    with mock.patch.object(config,'TELEGRAM_ENABLED',False,create=True):
        r=TestClient(app).get('/')
        assert r.status_code==200
        assert 'https://t.me/' not in r.text
        assert 'mailto:' in r.text
