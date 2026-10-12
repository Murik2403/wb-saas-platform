"""Verified pending/canceled refund states; every provider call is mocked."""
import asyncio
from unittest import mock
import billing_routes,admin_routes,yookassa_client
from logic import accounts,billing,db
from .base import GatewayDbTestCase
from .test_billing_routes_webhook import _post_request,_real_connect


class RefundWebhookStateTests(GatewayDbTestCase):
    def setUp(self):
        super().setUp();self.init_db()
        with self.connect() as c:
            self.owner=accounts.create_account(c,'webhook-refund@example.test','password1234')
            billing.record_payment_attempt(c,self.owner,'paid','manual',3000)
            billing.apply_successful_payment(c,self.owner,'paid',period_days=30)
            self.before=dict(accounts.get_account_by_id(c,self.owner))
        self.enterContext(mock.patch.object(billing_routes.control_db,'connect',side_effect=lambda *a,**k:_real_connect(self.db_path)))
        self.enterContext(mock.patch.object(billing_routes.config,'YOOKASSA_WEBHOOK_CIDRS',('198.51.100.0/24',)))
        billing_routes.webhook_global_limiter.clear();billing_routes.webhook_source_limiter.clear()
        self.provider=self.enterContext(mock.patch.object(yookassa_client,'get_refund'))

    def call(self,status='pending',amount='1500.00',reply=None):
        self.provider.return_value=reply if reply is not None else {'id':'r1','payment_id':'paid','status':status,'amount':{'value':amount,'currency':'RUB'}}
        # Deliberately forged event/state; only authoritative GET decides.
        return asyncio.run(billing_routes.yookassa_webhook(_post_request({'event':'refund.succeeded','object':{'id':'r1','status':'succeeded'}})))

    def state(self):
        with db.connect(self.db_path) as c:
            row=c.execute("SELECT * FROM refunds WHERE yookassa_refund_id='r1'").fetchone()
            return dict(accounts.get_account_by_id(c,self.owner)),dict(row) if row else None

    def test_pending_is_recorded_without_reducing_access_and_success_applies_once(self):
        self.assertEqual(self.call().status_code,200)
        account,row=self.state();self.assertEqual(account,self.before);self.assertEqual(row['status'],'pending')
        self.assertEqual(self.call('succeeded').status_code,200)
        first=self.state();self.assertEqual(first[1]['status'],'succeeded');self.assertNotEqual(first[0]['current_period_end'],self.before['current_period_end'])
        self.call('succeeded');self.call('pending');self.call('canceled')
        self.assertEqual(self.state(),first)

    def test_canceled_releases_pending_reservation_and_preserves_access(self):
        with db.connect(self.db_path) as c:billing.apply_refund(c,self.owner,'paid',3000,'r1',status='pending')
        self.assertEqual(self.call('canceled','3000.00').status_code,200)
        account,row=self.state();self.assertEqual(account,self.before);self.assertEqual(row['status'],'canceled')
        with mock.patch.object(admin_routes,'_require_admin',return_value={'id':self.owner}),mock.patch.object(admin_routes.csrf,'verify',return_value=True),mock.patch.object(yookassa_client,'create_refund',return_value={'id':'r2','payment_id':'paid','status':'pending','amount':{'value':'3000.00','currency':'RUB'}}) as create:
            admin_routes.admin_payment_refund(None,'paid',None,'csrf')
        create.assert_called_once_with('paid',amount_rub=3000.0,idempotence_key=mock.ANY)

    def test_mismatched_provider_metadata_never_releases_reservation(self):
        with db.connect(self.db_path) as c:billing.apply_refund(c,self.owner,'paid',3000,'r1',status='pending')
        baseline=self.state()
        valid={'id':'r1','payment_id':'paid','status':'canceled','amount':{'value':'3000.00','currency':'RUB'}}
        for reply in ({},dict(valid,id='other'),dict(valid,payment_id='other'),dict(valid,status='unknown'),dict(valid,amount={'value':'3000','currency':'USD'}),dict(valid,amount={'value':'0','currency':'RUB'}),dict(valid,amount={'value':'2999.99','currency':'RUB'}),dict(valid,amount={'value':'0.001','currency':'RUB'})):
            with self.subTest(reply=reply):
                self.assertEqual(self.call(reply=reply).status_code,502)
                self.assertEqual(self.state(),baseline)

    def test_provider_read_failure_is_retryable_without_mutation(self):
        self.provider.side_effect=yookassa_client.YooKassaError('synthetic transport failure')
        response=asyncio.run(billing_routes.yookassa_webhook(_post_request({'event':'refund.canceled','object':{'id':'r1'}})))
        self.assertEqual(response.status_code,502);self.assertEqual(self.state(),(self.before,None))

    def test_unknown_local_payment_acknowledged_without_creating_record(self):
        reply={'id':'r1','payment_id':'unknown','status':'pending','amount':{'value':'1500.00','currency':'RUB'}}
        self.assertEqual(self.call(reply=reply).status_code,200)
        self.assertEqual(self.state(),(self.before,None))
