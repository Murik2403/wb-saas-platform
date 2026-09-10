from unittest.mock import patch
import sync


def test_ads_retry_does_not_fetch_orders_or_change_other_sections():
    with patch.object(sync, "init_db"), patch.object(sync, "get_token", return_value="fake"), patch.object(sync, "load_settings", return_value={}), patch.object(sync, "start_sync_log", return_value=1), patch.object(sync, "finish_sync_log") as finish, patch.object(sync, "WBAPI") as factory, patch.object(sync, "_sync_ads") as ads:
        sync.sync_ads_only()
        ads.assert_called_once()
        factory.return_value.get_orders.assert_not_called()
        factory.return_value.get_sales.assert_not_called()
        assert finish.call_args.args[2].startswith("scope: ads")
