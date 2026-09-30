"""Daily fixed-demo worker with durable success and cross-process exclusion."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


class DailyDemoRefresh:
    def __init__(self, directory: Path, refresh, *, enabled=False, hour=3, clock=None):
        if not 0 <= hour <= 23:
            raise ValueError('Demo refresh hour must be 0..23')
        self.directory, self.refresh, self.enabled, self.hour = directory, refresh, enabled, hour
        self.clock = clock or (lambda: datetime.now(ZoneInfo('Europe/Moscow')))

    def status(self):
        path = self.directory / 'daily-refresh.json'
        state = json.loads(path.read_text('utf-8')) if path.exists() else {}
        if not isinstance(state, dict):
            raise ValueError('Invalid demo refresh state')
        allowed = {'last_success_date', 'last_success_at', 'last_attempt_at', 'last_result'}
        return {**{k: v for k, v in state.items() if k in allowed}, 'enabled': self.enabled, 'hour_moscow': self.hour}

    def _save(self, state):
        target = self.directory / 'daily-refresh.json'
        temporary = self.directory / 'daily-refresh.json.tmp'
        with temporary.open('w', encoding='utf-8') as handle:
            os.chmod(temporary, 0o600)
            json.dump(state, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)

    def tick(self):
        if not self.enabled:
            return 'disabled'
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / 'daily-refresh.lock').open('a') as lock:
            try:
                if os.name == 'nt':
                    import msvcrt
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (BlockingIOError, PermissionError):
                return 'busy'
            try:
                return self._locked_tick()
            finally:
                if os.name == 'nt':
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock, fcntl.LOCK_UN)

    def _locked_tick(self):
        now = self.clock().astimezone(ZoneInfo('Europe/Moscow'))
        state = self.status()  # Corrupt state fails closed, never silently resets.
        if now.hour < self.hour or state.get('last_success_date') == now.date().isoformat():
            return 'not_due'
        attempt = state.get('last_attempt_at')
        if attempt and now - datetime.fromisoformat(attempt) < timedelta(hours=1):
            return 'retry_later'
        state.update(last_attempt_at=now.isoformat(), last_result='running')
        self._save(state)  # Failure to persist refuses the destructive operation.
        try:
            self.refresh()
        except Exception:
            state['last_result'] = 'failed'  # No credentials/customer/exception payload.
            self._save(state)
            return 'failed'
        state.update(last_success_date=now.date().isoformat(), last_success_at=self.clock().isoformat(), last_result='success')
        self._save(state)
        return 'success'

    def run(self, stop):
        while not stop.is_set():
            try:
                self.tick()
            except Exception:
                # State/Docker failures retry later. Details never enter public logs.
                import logging
                logging.getLogger(__name__).error('Demo daily refresh check failed; inspect internal status')
            stop.wait(60)
