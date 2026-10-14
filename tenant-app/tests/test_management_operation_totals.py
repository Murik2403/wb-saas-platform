from datetime import date
from unittest.mock import patch

import pandas as pd

import calculations
from db import upsert_financial
from tests.base import DbTestCase


class ManagementOperationTotalsTests(DbTestCase):
    def tables(self, catalog=None):
        tables = {name: pd.DataFrame() for name in ('orders','sales','stocks','costs','products_catalog','ads_daily')}
        tables['costs'] = pd.DataFrame([{'nm_id':123,'cost_per_wb_unit':20},{'nm_id':456,'cost_per_wb_unit':0}])
        tables['products_catalog'] = pd.DataFrame(catalog if catalog is not None else [
            {'nm_id':123,'supplier_article':'A','product_name':'Product A'},
            {'nm_id':456,'supplier_article':'B','product_name':'Product B'},
        ])
        tables['ads_daily'] = pd.DataFrame([
            {'day':'2024-02-29','campaign_id':1,'nm_id':0,'spend':50,'ad_revenue':0,'views':0,'clicks':0,'orders_count':0},
            {'day':'2024-02-29','campaign_id':1,'nm_id':456,'spend':60,'ad_revenue':0,'views':0,'clicks':0,'orders_count':0},
        ])
        return tables

    def row(self, identity, day, nm_id=123, event='Продажа', **extra):
        return {'rrdId':identity,'rrDate':day,'saleDt':day,'nmId':nm_id,'quantity':1,
                'docTypeName':event,'sellerOperName':event,'retailPriceWithDisc':100,
                'retailAmount':100,'forPay':80,'supplierArticle':'financial-A',**extra}

    def seed(self):
        upsert_financial([
            self.row(1,'2024-02-28',quantity=3),
            self.row(2,'2024-02-29','123','Возврат',quantity=-1,retailAmount=-100,forPay=-80),
            self.row(3,'2024-03-01',quantity=2,retailPriceWithDisc=125),
            self.row(4,'2024-02-29',event='Добровольная компенсация при возврате',quantity=9,retailPriceWithDisc=0,retailAmount=0,forPay=15),
            self.row(5,'2024-02-29',nm_id=0,event='Удержание',quantity=12,retailAmount=0,forPay=0,deliveryRub=30,deduction=20,bonusTypeName='WB Продвижение'),
            self.row(6,'2024-02-29',nm_id=999,event='Продажа',supplierArticle='Техническая строка',quantity=7),
            self.row(7,'bad-date',quantity=100),
            self.row(8,'2024-03-02',quantity=1,retailPriceWithDisc=0),
        ])

    def projected_reference(self, nm_id, start, end):
        frame=calculations.build_dashboard(start,end).financial_products
        if frame.empty:return (0.0,0.0)
        selected=frame[pd.to_numeric(frame['Артикул WB'],errors='coerce')==nm_id]
        if selected.empty:return (0.0,0.0)
        row=selected.iloc[0]
        return float(row['Выкупы по цене покупателя']),float(row['Продано нетто'])

    def test_partial_windows_equal_full_global_dashboard_projection(self):
        self.seed();tables=self.tables()
        with patch('calculations.read_table',side_effect=lambda name,**kw:tables[name].copy()):
            for start,end in [(date(2024,2,28),date(2024,3,2)),(date(2024,2,29),date(2024,2,29)),
                              (date(2024,2,28),date(2024,2,29)),(date(2024,3,1),date(2024,3,2)),
                              (date(2023,12,1),date(2023,12,31))]:
                for nm_id in (123,456,999,0,777):
                    with self.subTest(window=(start,end),nm_id=nm_id):
                        expected=self.projected_reference(nm_id,start,end)
                        self.assertEqual(calculations.read_product_operation_totals(nm_id,start,end),expected)

    def test_catalog_membership_and_technical_name_use_full_dashboard_rules(self):
        self.seed()
        for catalog in ([],[{'nm_id':456,'supplier_article':'B','product_name':'Product B'}],
                        [{'nm_id':123,'supplier_article':'служебная-A','product_name':'Product A'}],
                        [{'nm_id':123,'supplier_article':'','product_name':'Неопознанный товар'}],
                        [{'nm_id':123,'supplier_article':'A','product_name':None},
                         {'nm_id':123,'supplier_article':'unknown-later','product_name':'later duplicate'}]):
            tables=self.tables(catalog)
            with patch('calculations.read_table',side_effect=lambda name,**kw:tables[name].copy()):
                expected=self.projected_reference(123,date(2024,2,28),date(2024,3,2))
                self.assertEqual(calculations.read_product_operation_totals(123,date(2024,2,28),date(2024,3,2)),expected)

    def test_overlap_scalar_does_not_read_operational_tables_or_reconcile_fifo(self):
        self.seed();catalog=self.tables()['products_catalog']
        def read(name,**kwargs):
            if name!='products_catalog':raise AssertionError('Unneeded full-store operational read: '+name)
            return catalog.copy()
        with patch('calculations.read_table',side_effect=read),patch('calculations.read_financial_fifo_reconciliation',side_effect=AssertionError('Unneeded COGS for expense-rate units/revenue')),patch('calculations.build_dashboard',side_effect=AssertionError('Full Dashboard not allowed for ordinary overlap')):
            self.assertEqual(calculations.read_product_operation_totals(123,date(2024,2,29),date(2024,2,29)),(-100.0,-1.0))

    def test_empty_financial_history_stays_zero_without_other_reads(self):
        with patch('calculations.read_financial',return_value=pd.DataFrame()),patch('calculations.read_table',side_effect=AssertionError('Empty history does not need catalog')):
            self.assertEqual(calculations.read_product_operation_totals(123,date(2024,2,1),date(2024,2,29)),(0.0,0.0))

    def test_null_dates_and_missing_quantity_price_preserve_full_projection(self):
        self.seed();tables=self.tables()
        source=calculations.read_financial(omit_raw=True)
        source.loc[source.rrd_id==1,'operation_date']=None
        source.loc[source.rrd_id==3,'quantity']=None
        source.loc[source.rrd_id==8,'retail_price_withdisc_rub']=None
        with patch('calculations.read_table',side_effect=lambda name,**kw:tables[name].copy()),patch('calculations.read_financial',side_effect=lambda **kw:source.copy()):
            expected=self.projected_reference(123,date(2024,2,28),date(2024,3,2))
            self.assertEqual(calculations.read_product_operation_totals(123,date(2024,2,28),date(2024,3,2)),expected)

    def test_groupby_reducer_keeps_exact_float_summation_order(self):
        upsert_financial([self.row(index,'2024-02-29',retailPriceWithDisc=price,retailAmount=price) for index,price in enumerate((1e16,1,1,1,-1e16),start=1)])
        tables=self.tables()
        with patch('calculations.read_table',side_effect=lambda name,**kw:tables[name].copy()):
            expected=self.projected_reference(123,date(2024,2,29),date(2024,2,29))
            self.assertEqual(calculations.read_product_operation_totals(123,date(2024,2,29),date(2024,2,29)),expected)

    def test_fractional_legacy_identity_falls_back_to_exact_existing_selection(self):
        self.seed();tables=self.tables()
        source=calculations.read_financial(omit_raw=True)
        source['nm_id']=source['nm_id'].astype(float)
        source.loc[source.nm_id==123,'nm_id']=123.5
        with patch('calculations.read_table',side_effect=lambda name,**kw:tables[name].copy()),patch('calculations.read_financial',side_effect=lambda **kw:source.copy()):
            expected=self.projected_reference(123,date(2024,2,28),date(2024,3,2))
            self.assertEqual(calculations.read_product_operation_totals(123,date(2024,2,28),date(2024,3,2)),expected)
