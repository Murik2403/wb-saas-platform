from datetime import date
from unittest.mock import patch

import pandas as pd

from calculations import build_dashboard
from db import read_financial, upsert_financial
from db.financial_fifo_reconciliation import read_financial_fifo_reconciliation
from db.core import connect
from tests.base import DbTestCase


class FinancialOperationAccountingTests(DbTestCase):
    def dashboard(self, rows, *, ads=True):
        upsert_financial(rows)
        with connect() as conn:
            conn.execute("INSERT OR REPLACE INTO costs(nm_id,cost_per_wb_unit) VALUES (123,100)")
        tables = {name: pd.DataFrame() for name in
                  ('orders', 'sales', 'stocks', 'costs', 'products_catalog', 'ads_daily')}
        tables['costs'] = pd.DataFrame([{'nm_id': 123, 'cost_per_wb_unit': 100}])
        tables['products_catalog'] = pd.DataFrame([{'nm_id': 123, 'supplier_article': 'A'}])
        if ads:
            tables['ads_daily'] = pd.DataFrame([{'day': '2026-09-10', 'campaign_id': 1,
                                               'nm_id': 0, 'spend': 50, 'ad_revenue': 0,
                                               'views': 0, 'clicks': 0, 'orders_count': 0}])
        with patch('calculations.read_table', side_effect=lambda name, **kw: tables[name].copy()):
            return build_dashboard(date(2026, 9, 1), date(2026, 9, 30))

    def row(self, id, operation='Продажа', **extra):
        return {'rrdId': id, 'rrDate': '2026-09-10', 'saleDt': '2026-09-10',
                'nmId': 123, 'quantity': 1, 'docTypeName': operation,
                'sellerOperName': operation, 'retailPriceWithDisc': 1000,
                'retailAmount': 1000, 'forPay': 900, **extra}

    def test_unsigned_and_already_signed_returns_are_subtracted_once(self):
        for sign in (1, -1):
            with self.subTest(sign=sign):
                result = self.dashboard([
                    self.row(1, quantity=3, retailAmount=3000, forPay=2700),
                    self.row(2, 'Возврат', retailAmount=sign*1000, forPay=sign*900),
                ])
                self.assertEqual(result.financial['to_pay'], 1800)
                self.assertEqual(result.financial['sales'], 2000)
                self.assertEqual(result.financial['cost'], 200)
                self.assertEqual(result.financial['profit'], 1550)
                self.assertAlmostEqual(result.financial_products['Чистая прибыль'].sum(), 1550)

    def test_promotion_settlement_not_subtracted_twice_but_unknown_cost_is_kept(self):
        result = self.dashboard([
            self.row(1),
            self.row(2, 'Удержание', docTypeName='', nmId=0, quantity=0, retailAmount=0, forPay=0,
                     deduction=40, bonusTypeName='Оказание услуг «WB Продвижение», документ №1'),
            self.row(3, 'Удержание', docTypeName='', nmId=0, quantity=0, retailAmount=0, forPay=0,
                     deduction=7, bonus_type_name='Прочие услуги'),
        ])
        self.assertEqual(result.financial['deductions'], 7)
        self.assertEqual(result.financial['advertising_settlements'], 40)
        self.assertEqual(result.financial['advertising_settlements_excluded'], 40)
        self.assertEqual(result.financial['profit'], 743)
        self.assertEqual(result.financial['unallocated_deductions'], 7)
        self.assertNotIn('raw_json', read_financial(omit_raw=True).columns)
        self.assertIn('deduction_reason', read_financial(omit_raw=True).columns)

    def test_without_campaign_data_settlement_is_kept_and_marked_as_fallback(self):
        result = self.dashboard([
            self.row(1),
            self.row(2, 'Удержание', docTypeName='', quantity=0, retailAmount=0, forPay=0,
                     deduction=40, bonus_type_name='Оказание услуг «ВБ.Продвижение»'),
        ], ads=False)
        self.assertEqual(result.financial['deductions'], 40)
        self.assertEqual(result.financial['profit'], 760)
        self.assertEqual(result.financial['advertising_cost_source'], 'settlement_fallback')

    def test_compensation_is_income_not_a_product_sale_or_return(self):
        result = self.dashboard([
            self.row(1),
            self.row(2, 'Добровольная компенсация при возврате', docTypeName='Продажа',
                     retailAmount=0, retailPriceWithDisc=0, forPay=25),
        ])
        self.assertEqual(result.financial['sale_units'], 1)
        self.assertEqual(result.financial['return_units'], 0)
        self.assertEqual(result.financial['cost'], 100)
        self.assertEqual(result.financial['profit'], 775)
        rows = read_financial_fifo_reconciliation('2026-09-01', '2026-09-30')
        self.assertEqual(rows.loc[rows.rrd_id == 2, 'event_type'].iloc[0], '')

    def test_real_additional_logistics_remains_an_expense(self):
        result = self.dashboard([
            self.row(1), self.row(2, 'Возмещение издержек по перемещению и операционной обработке товара',
                                  docTypeName='', quantity=0, retailAmount=0, forPay=0,
                                  rebillLogisticCost=15),
        ])
        self.assertEqual(result.financial['rebill_logistics'], 15)
        self.assertEqual(result.financial['profit'], 735)
