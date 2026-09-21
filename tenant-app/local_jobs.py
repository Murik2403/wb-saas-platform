"""Local background jobs, owned by the API lifespan and one OS lock."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Event, Lock, Thread
from time import monotonic

from config import DB_PATH, load_settings, get_token
from sync_lock import sync_lock, SyncBusyError


def build_default_jobs():
    """Build the API's periodic jobs without starting their supervisor."""
    from backup_tools import ensure_daily_backup
    from sync import sync_all
    from agent_sync import run_once
    from report_scheduler import check_once

    def deliver_growth_events():
        # The outbox is durable and scoped to this tenant. Keep its retry loop
        # independent of WB sync and user page visits so a transient
        # gateway/API outage cannot strand a lifecycle milestone indefinitely.
        from growth_delivery import deliver_pending_growth_events
        return deliver_pending_growth_events(DB_PATH)

    return {
        "backup": (ensure_daily_backup, lambda: 3600),
        "sync": (lambda: sync_all() if get_token() else None, lambda: max(15, int(load_settings()["sync_interval_minutes"])) * 60),
        "recommendations": (lambda: run_once() if get_token() else None, lambda: max(15, int(load_settings()["agent_interval_minutes"])) * 60),
        # Local scheduling generates saved reports. Sending to other people
        # remains controlled by the production delivery worker.
        "reports": (lambda: check_once(deliver=False), lambda: 60),
        # Lifecycle events are retried even when WB is disconnected or no
        # dashboard request arrives. The outbox itself applies a 60-second
        # per-event backoff and idempotent delivery.
        "growth_delivery": (deliver_growth_events, lambda: 60),
    }


class LocalJobs:
    def __init__(self, jobs=None):
        self.stop_event = Event()
        self.lock = Lock()
        self.states = {}
        self.jobs = jobs
        self.thread = None

    def start(self):
        self.thread = Thread(target=self.run, daemon=True, name="local-jobs")
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)

    def status(self):
        with self.lock:
            return {name: dict(state) for name, state in self.states.items()}

    def execute(self, name, action):
        with self.lock:
            self.states[name] = {"status": "running"}
        try:
            action()
            status, error = "success", None
        except Exception as exc:
            status, error = "error", type(exc).__name__
        with self.lock:
            self.states[name] = {"status": status, "finished_at": datetime.now(timezone.utc).isoformat(), "error": error}

    def run(self):
        try:
            with sync_lock(DB_PATH.with_suffix(".jobs.lock")):
                self._loop()
        except SyncBusyError:
            with self.lock:
                self.states["supervisor"] = {"status": "owned_by_other_process"}

    def _loop(self):
        if self.jobs is None:
            self.jobs = build_default_jobs()
        due = {name: monotonic() + (0 if name == "backup" else interval()) for name, (_, interval) in self.jobs.items()}
        futures = {}
        with self.lock:
            self.states.update({name: {"status": "waiting"} for name in self.jobs})
        with ThreadPoolExecutor(max_workers=len(self.jobs)) as pool:
            while not self.stop_event.wait(1):
                now = monotonic()
                for name, (action, interval) in self.jobs.items():
                    if now >= due[name] and (name not in futures or futures[name].done()):
                        futures[name] = pool.submit(self.execute, name, action)
                        due[name] = now + interval()
