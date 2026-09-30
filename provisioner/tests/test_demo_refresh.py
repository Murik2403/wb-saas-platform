from datetime import datetime, timedelta
import json
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest
from demo_refresh import DailyDemoRefresh


def test_daily_success_is_persisted_across_restart_and_next_date(tmp_path):
    now = [datetime(2026,9,30,3,5,tzinfo=ZoneInfo('Europe/Moscow'))]
    refresh = Mock()
    worker = DailyDemoRefresh(tmp_path, refresh, enabled=True, clock=lambda: now[0])
    assert worker.tick() == 'success'
    assert worker.status()['last_success_date'] == '2026-09-30'
    restarted = DailyDemoRefresh(tmp_path, refresh, enabled=True, clock=lambda: now[0])
    assert restarted.tick() == 'not_due'
    now[0] += timedelta(days=1)
    assert restarted.tick() == 'success'
    assert refresh.call_count == 2


def test_disabled_and_before_schedule_never_call_refresh(tmp_path):
    refresh = Mock()
    assert DailyDemoRefresh(tmp_path, refresh).tick() == 'disabled'
    worker = DailyDemoRefresh(tmp_path, refresh, enabled=True, clock=lambda: datetime(2026,9,30,2,tzinfo=ZoneInfo('Europe/Moscow')))
    assert worker.tick() == 'not_due'
    refresh.assert_not_called()


def test_failed_refresh_sanitized_and_bounded_retry(tmp_path):
    now = [datetime(2026,9,30,3,tzinfo=ZoneInfo('Europe/Moscow'))]
    refresh = Mock(side_effect=RuntimeError('secret must never be stored'))
    worker = DailyDemoRefresh(tmp_path, refresh, enabled=True, clock=lambda: now[0])
    assert worker.tick() == 'failed'
    assert 'last_success_date' not in worker.status()
    assert 'secret' not in (tmp_path/'daily-refresh.json').read_text()
    assert worker.tick() == 'retry_later'
    now[0] += timedelta(hours=1)
    refresh.side_effect = None
    assert worker.tick() == 'success'


def test_second_worker_cannot_refresh_while_first_holds_lock(tmp_path):
    now = lambda: datetime(2026,9,30,3,tzinfo=ZoneInfo('Europe/Moscow'))
    second_refresh = Mock()
    second = DailyDemoRefresh(tmp_path, second_refresh, enabled=True, clock=now)
    results = []
    first = DailyDemoRefresh(tmp_path, lambda: results.append(second.tick()), enabled=True, clock=now)
    assert first.tick() == 'success'
    assert results == ['busy']
    second_refresh.assert_not_called()


def test_corrupt_state_and_failed_initial_state_write_fail_closed(tmp_path, monkeypatch):
    refresh = Mock()
    worker = DailyDemoRefresh(tmp_path, refresh, enabled=True, clock=lambda: datetime(2026,9,30,3,tzinfo=ZoneInfo('Europe/Moscow')))
    (tmp_path/'daily-refresh.json').write_text('broken')
    with pytest.raises(json.JSONDecodeError):
        worker.tick()
    refresh.assert_not_called()
    (tmp_path/'daily-refresh.json').unlink()
    monkeypatch.setattr(worker, '_save', Mock(side_effect=OSError('disk full')))
    with pytest.raises(OSError):
        worker.tick()
    refresh.assert_not_called()


def test_utc_clock_uses_moscow_day_and_hour(tmp_path):
    refresh = Mock()
    worker = DailyDemoRefresh(tmp_path, refresh, enabled=True,
        clock=lambda: datetime.fromisoformat('2026-09-29T23:30:00+00:00'))
    assert worker.tick() == 'not_due'  # 02:30 Moscow on 30 September.
    worker.clock = lambda: datetime.fromisoformat('2026-09-30T00:05:00+00:00')
    assert worker.tick() == 'success'
    assert worker.status()['last_success_date'] == '2026-09-30'


def test_healthcheck_must_pass_before_daily_success(monkeypatch):
    import app
    reseed = Mock()
    monkeypatch.setattr(app, 'reseed_demo', reseed)
    container = Mock()
    container.attrs = {'State': {'Health': {'Status': 'starting'}}}
    client = Mock()
    client.containers.get.return_value = container
    monkeypatch.setattr(app, '_client', lambda: client)
    monkeypatch.setattr(app.time, 'sleep', lambda _seconds: None)
    with pytest.raises(RuntimeError, match='health'):
        app._wait_demo_health(client)
    assert container.reload.call_count == 30
    container.attrs['State']['Health']['Status'] = 'healthy'
    app._wait_demo_health(client)
