import pytest
import requests
from unittest.mock import Mock

from wb_api import WBAPI, WBAPIError


@pytest.mark.parametrize('method', ['orders', 'statuses'])
@pytest.mark.parametrize('failure', ['429', '503', 'timeout'])
def test_fbs_manual_refresh_fails_fast_without_backoff(monkeypatch, method, failure):
    api = WBAPI('fixture-token', timeout=15)
    response = Mock(status_code=int(failure) if failure != 'timeout' else 200,
                    headers={'Retry-After': '60'}, text='upstream error', content=b'{}')
    request = Mock(side_effect=requests.Timeout('private detail')) if failure == 'timeout' else Mock(return_value=response)
    monkeypatch.setattr(api.session, 'request', request)
    sleep = Mock()
    monkeypatch.setattr('wb_api.time.sleep', sleep)
    with pytest.raises(WBAPIError):
        if method == 'orders':
            api.get_fbs_orders(1, 2)
        else:
            api.get_fbs_order_statuses([1])
    assert request.call_count == 1
    assert request.call_args.kwargs['timeout'] == 15
    sleep.assert_not_called()
