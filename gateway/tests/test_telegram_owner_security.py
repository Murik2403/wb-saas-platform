"""Owner-only recipient changes, tenant-scoped link codes and safe bot failures."""
from unittest import mock
import traceback
import requests
from fastapi import HTTPException
import internal_routes,telegram_bot
from logic import accounts
from .base import GatewayDbTestCase
class TelegramOwnerSecurity(GatewayDbTestCase):
 def setUp(self):
  super().setUp();self.init_db()
  with self.connect() as conn:
   self.owner=accounts.create_account(conn,'owner-security@shop.ru','password1234');self.manager=accounts.create_account(conn,'manager-security@shop.ru','password1234')
   self.slug='security-shop';accounts.create_tenant_instance(conn,self.owner,self.slug)
 def test_actor_must_be_exact_owner_before_code_issuance(self):
  with mock.patch.object(internal_routes,'control_db',mock.Mock(connect=self.connect)):
   for actor in ('',str(self.manager),'unknown'):
    with self.assertRaises(HTTPException) as error:internal_routes.create_telegram_link_code(self.slug,actor)
    self.assertEqual(error.exception.status_code,403)
  with self.connect() as conn:self.assertEqual(conn.execute('SELECT COUNT(*) FROM telegram_link_codes').fetchone()[0],0)
 def test_code_updates_only_bound_tenant(self):
  with self.connect() as conn:accounts.create_tenant_instance(conn,self.manager,'other-security-shop')
  with mock.patch.object(internal_routes,'control_db',mock.Mock(connect=self.connect)):
   code=internal_routes.create_telegram_link_code(self.slug,str(self.owner))
  with mock.patch.object(telegram_bot,'control_db',mock.Mock(connect=self.connect)),mock.patch.object(telegram_bot,'_call'):
   telegram_bot._handle_link_command(222,'/link '+code)
  with self.connect() as conn:
   self.assertEqual(accounts.get_telegram_chat_id_for_slug(conn,self.slug),'222')
   self.assertEqual(accounts.get_telegram_chat_id_for_slug(conn,'other-security-shop'),'')
 def test_legacy_unscoped_codes_rejected_by_bot(self):
  with self.connect() as conn:code=accounts.create_telegram_link_code(conn,self.owner)
  with mock.patch.object(telegram_bot,'control_db',mock.Mock(connect=self.connect)),mock.patch.object(telegram_bot,'_call'):
   telegram_bot._handle_link_command(333,'/link '+code)
  with self.connect() as conn:self.assertEqual(accounts.get_telegram_chat_id_for_slug(conn,self.slug),'')
 def test_transport_errors_never_expose_token_or_url(self):
  secret='synthetic-private-bot-token'
  with mock.patch.object(telegram_bot.config,'TELEGRAM_BOT_TOKEN',secret),mock.patch.object(telegram_bot.requests,'post',side_effect=requests.ConnectionError('https://api.telegram.org/bot'+secret+'/getUpdates')):
   for fn,args in ((telegram_bot._call,('getUpdates',)),(telegram_bot.send_document,('1',b'pdf','file.pdf')),(telegram_bot.send_message,('1','hello'))):
    try:fn(*args)
    except RuntimeError as error:self.assertNotIn(secret,''.join(traceback.format_exception(error)))
    else:self.fail('expected safe transport error')
