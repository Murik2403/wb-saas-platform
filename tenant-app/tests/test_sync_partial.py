from contextlib import ExitStack
from unittest import TestCase, mock

import sync


class PartialSyncTests(TestCase):
    def run_sync(self, fail_ads=False, fail_finance=False, empty_ads=False):
        with ExitStack() as stack:
            names = ['init_db', 'ensure_financial_schema', 'clear_demo_data', 'replace_product_catalog', 'upsert_orders', 'upsert_sales', 'upsert_financial', 'insert_stocks', 'process_sales_fifo_events', 'delete_ads_period', 'upsert_ads', 'cleanup_orphan_zero_costs', 'finish_sync_log', 'update_sync_progress']
            mocks = {name: stack.enter_context(mock.patch.object(sync, name, return_value=3)) for name in names}
            stack.enter_context(mock.patch.object(sync, 'get_token', return_value='test-only'))
            stack.enter_context(mock.patch.object(sync, 'load_settings', return_value={}))
            stack.enter_context(mock.patch.object(sync, 'start_sync_log', return_value=42))
            stack.enter_context(mock.patch.object(sync, 'max_value', return_value=None))
            stack.enter_context(mock.patch.object(sync, 'sales_fifo_tracking_status', return_value={'initialized': True}))
            growth_event = stack.enter_context(mock.patch.object(sync, '_record_first_sync_event'))
            mocks['process_sales_fifo_events'].return_value = {}
            api = stack.enter_context(mock.patch.object(sync, 'WBAPI')).return_value
            for name in ['get_product_cards', 'get_orders', 'get_sales', 'get_financial_report', 'get_stocks', 'get_ad_stats']:
                getattr(api, name).return_value = []
            api.get_campaign_ids.return_value = [123] if fail_ads or empty_ads else []
            if fail_ads:
                api.get_ad_stats.side_effect = RuntimeError('WB API 500: temporary advertising outage')
            if fail_finance:
                api.get_financial_report.side_effect = RuntimeError('finance unavailable')
            result = sync.sync_all()
            return result, mocks, growth_event

    def test_ad_failure_preserves_history_and_reports_saved_sections(self):
        result, mocks, growth_event = self.run_sync(fail_ads=True)
        self.assertEqual(result['orders'], 3)
        self.assertIn('ads_error', result)
        mocks['delete_ads_period'].assert_not_called()
        mocks['upsert_ads'].assert_not_called()
        self.assertEqual(mocks['finish_sync_log'].call_args.args[1], 'partial')
        self.assertIn('orders: 3', mocks['finish_sync_log'].call_args.args[2])
        growth_event.assert_not_called()

    def test_finance_failure_does_not_become_full_success(self):
        _, mocks, growth_event = self.run_sync(fail_finance=True)
        mocks['insert_stocks'].assert_called_once()
        self.assertEqual(mocks['finish_sync_log'].call_args.args[1], 'partial')
        growth_event.assert_not_called()

    def test_complete_import_is_successful(self):
        _, mocks, growth_event = self.run_sync()
        self.assertEqual(mocks['finish_sync_log'].call_args.args[1], 'success')
        growth_event.assert_called_once_with()

    def test_empty_ad_response_preserves_history_and_is_partial(self):
        result, mocks, growth_event = self.run_sync(empty_ads=True)
        self.assertIn('ads_skipped', result)
        mocks['delete_ads_period'].assert_not_called()
        self.assertEqual(mocks['finish_sync_log'].call_args.args[1], 'partial')
        growth_event.assert_not_called()
