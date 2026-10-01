"""Telegram "data loaded" ping: once per store, best-effort, never affects sync."""
from __future__ import annotations

import sys
import types
from unittest import TestCase, mock

import sync
from reports import delivery


def _run(first, notify_effect=None):
    """Run _record_first_sync_event with growth modules faked."""
    growth = types.SimpleNamespace(record_growth_event=mock.Mock(return_value=first))
    gdel = types.SimpleNamespace(deliver_pending_growth_events=mock.Mock())
    with mock.patch.dict(sys.modules, {"growth_activation": growth, "growth_delivery": gdel}), \
         mock.patch.object(delivery, "send_text_telegram", side_effect=notify_effect, return_value=True) as notify:
        sync._record_first_sync_event()
    return notify


class FirstSyncNotifyTests(TestCase):
    def test_notifies_once_on_first_success(self):
        notify = _run(True)
        notify.assert_called_once_with(sync.FIRST_SYNC_TELEGRAM_TEXT)

    def test_not_notified_on_later_success(self):
        _run(False).assert_not_called()

    def test_notify_failure_is_swallowed(self):
        _run(True, notify_effect=RuntimeError("boom"))


class SendTextTelegramTests(TestCase):
    def cfg(self, **kw):
        values = dict(INTERNAL_API_URL="https://gw", INTERNAL_API_SECRET="s", TENANT_SLUG="shop")
        values.update(kw)
        return mock.patch.multiple(delivery.config, **values)

    def test_skipped_when_not_configured(self):
        with self.cfg(INTERNAL_API_URL=""), mock.patch.object(delivery.requests, "post") as post:
            self.assertFalse(delivery.send_text_telegram("hi"))
        post.assert_not_called()

    def test_success_posts_slug_and_text(self):
        with self.cfg(), mock.patch.object(delivery.requests, "post", return_value=mock.Mock(status_code=200)) as post:
            self.assertTrue(delivery.send_text_telegram("hi"))
        self.assertEqual(post.call_args.args[0], "https://gw/internal/send-text-telegram")
        self.assertEqual(post.call_args.kwargs["data"], {"slug": "shop", "text": "hi"})

    def test_not_linked_returns_false(self):
        with self.cfg(), mock.patch.object(delivery.requests, "post", return_value=mock.Mock(status_code=409)):
            self.assertFalse(delivery.send_text_telegram("hi"))

    def test_network_error_returns_false(self):
        with self.cfg(), mock.patch.object(delivery.requests, "post", side_effect=OSError("down")):
            self.assertFalse(delivery.send_text_telegram("hi"))
