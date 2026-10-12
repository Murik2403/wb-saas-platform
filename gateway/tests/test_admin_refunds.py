"""Admin refund boundary: synthetic provider replies, no real payment calls."""
from unittest import mock
import admin_routes
import yookassa_client
from logic import accounts,billing,db
from .base import GatewayDbTestCase


class AdminRefundBoundaryTests(GatewayDbTestCase):
    def setUp(self):
        super().setUp();self.init_db()
        with self.connect() as c:
            self.owner=accounts.create_account(c,'refund-admin@example.test','password1234')
            billing.record_payment_attempt(c,self.owner,'paid','manual',3000)
            billing.apply_successful_payment(c,self.owner,'paid',period_days=30)
        original=db.connect
        self.enterContext(mock.patch.object(admin_routes.control_db,'connect',side_effect=lambda *args,**kwargs:original(self.db_path)))
        self.enterContext(mock.patch.object(admin_routes,'_require_admin',return_value={'id':self.owner}))
        self.enterContext(mock.patch.object(admin_routes.csrf,'verify',return_value=True))
        self.enterContext(mock.patch.object(admin_routes.logger,'exception'))
        self.provider=self.enterContext(mock.patch.object(yookassa_client,'create_refund'))

    def rows(self):
        with db.connect(self.db_path) as c:return [dict(x) for x in c.execute('SELECT * FROM refunds')]

    def invoke(self,amount=None,payment='paid'):
        self.provider.return_value={'id':'refund-1','payment_id':payment,'status':'pending','amount':{'value':'3000.00' if amount is None else str(amount),'currency':'RUB'}}
        return admin_routes.admin_payment_refund(None,payment,amount,'csrf')

    def test_full_refund_sends_explicit_remaining_and_records_provider_amount(self):
        self.invoke()
        self.provider.assert_called_once_with('paid',amount_rub=3000.0)
        row=self.rows()[0];self.assertEqual(row['amount_rub'],3000.0);self.assertEqual(row['status'],'pending')

    def test_full_refund_uses_remaining_after_succeeded_partial(self):
        with db.connect(self.db_path) as c:billing.apply_refund(c,self.owner,'paid',1000,'previous')
        self.provider.return_value={'id':'refund-1','payment_id':'paid','status':'pending','amount':{'value':'2000.00','currency':'RUB'}}
        admin_routes.admin_payment_refund(None,'paid',None,'csrf')
        self.provider.assert_called_once_with('paid',amount_rub=2000.0)
        self.assertEqual(self.rows()[1]['amount_rub'],2000.0)

    def test_unknown_payment_or_invalid_amount_never_calls_provider(self):
        self.invoke(payment='missing');self.provider.assert_not_called()
        for amount in (0,-1,float('nan'),float('inf'),0.001,3000.01):
            self.invoke(amount);self.provider.assert_not_called()
        self.assertEqual(self.rows(),[])

    def test_pending_refund_reserves_remaining_before_next_outbound_call(self):
        with db.connect(self.db_path) as c:billing.apply_refund(c,self.owner,'paid',3000,'previous',status='pending')
        self.invoke();self.provider.assert_not_called();self.assertEqual(len(self.rows()),1)

    def test_provider_missing_or_mismatched_fields_do_not_record_refund(self):
        valid={'id':'refund-1','payment_id':'paid','status':'pending','amount':{'value':'1500.00','currency':'RUB'}}
        invalid=[None,{},dict(valid,id=''),dict(valid,payment_id='other'),dict(valid,status='unknown'),
                 dict(valid,amount={'value':'1500','currency':'USD'}),dict(valid,amount={'value':'0','currency':'RUB'}),
                 dict(valid,amount={'value':'1499.99','currency':'RUB'}),dict(valid,amount={})]
        for reply in invalid:
            with self.subTest(reply=reply):
                self.provider.return_value=reply
                admin_routes.admin_payment_refund(None,'paid',1500,'csrf')
                self.assertEqual(self.rows(),[])

    def test_successful_response_applies_access_deduction(self):
        self.provider.return_value={'id':'refund-1','payment_id':'paid','status':'succeeded','amount':{'value':'1500.00','currency':'RUB'}}
        admin_routes.admin_payment_refund(None,'paid',1500,'csrf')
        self.assertEqual(self.rows()[0]['status'],'succeeded')
        with db.connect(self.db_path) as c:self.assertEqual(accounts.get_account_by_id(c,self.owner)['status'],'active')

    def test_canceled_response_records_without_deducting_access(self):
        with db.connect(self.db_path) as c:before=dict(accounts.get_account_by_id(c,self.owner))
        self.provider.return_value={'id':'refund-1','payment_id':'paid','status':'canceled','amount':{'value':'3000.00','currency':'RUB'}}
        admin_routes.admin_payment_refund(None,'paid',None,'csrf')
        self.assertEqual(self.rows()[0]['status'],'canceled')
        with db.connect(self.db_path) as c:self.assertEqual(dict(accounts.get_account_by_id(c,self.owner)),before)

    def test_unpaid_payment_never_calls_provider(self):
        with db.connect(self.db_path) as c:billing.record_payment_attempt(c,self.owner,'unpaid','manual',3000)
        self.invoke(payment='unpaid');self.provider.assert_not_called()
