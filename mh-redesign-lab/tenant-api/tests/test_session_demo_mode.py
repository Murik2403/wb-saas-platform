from unittest.mock import patch
from starlette.requests import Request
from test_display_contract import api


def test_session_mode_matches_server_write_boundary():
    request = Request({'type':'http','headers':[(b'x-tenant-role',b'owner')], 'method':'GET','path':'/api/v1/session'})
    for demo, mode in [(True, 'demo'), (False, 'tenant')]:
        with patch.object(api, 'IS_DEMO', demo):
            session = api.get_dashboard_session(request)
            assert session['mode'] == mode
            assert session['user']['role'] == ('viewer' if demo else 'owner')
