from __future__ import annotations

import io
import json
import logging
import unittest

from logging_config import (
    JSONFormatter,
    account_id_ctx,
    bind_account_context,
    request_id_ctx,
    tenant_slug_ctx,
)


class JSONFormatterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stream = io.StringIO()
        handler = logging.StreamHandler(self.stream)
        handler.setFormatter(JSONFormatter())
        self.logger = logging.getLogger("test_wb_saas_logging")
        self.logger.setLevel(logging.INFO)
        self.logger.handlers = [handler]
        self.logger.propagate = False

    def _last_line(self) -> dict:
        return json.loads(self.stream.getvalue().strip().splitlines()[-1])

    def test_output_is_valid_json_with_request_id(self) -> None:
        token = request_id_ctx.set("req-1234")
        try:
            self.logger.info("hello %s", "world")
            payload = self._last_line()
        finally:
            request_id_ctx.reset(token)

        self.assertEqual(payload["level"], "INFO")
        self.assertEqual(payload["message"], "hello world")
        self.assertEqual(payload["request_id"], "req-1234")
        self.assertIn("timestamp", payload)

    def test_no_request_id_field_when_context_unset(self) -> None:
        self.logger.info("no context here")
        payload = self._last_line()
        self.assertNotIn("request_id", payload)

    def test_bind_account_context_adds_account_and_tenant_fields(self) -> None:
        req_token = request_id_ctx.set("req-5678")
        try:
            bind_account_context(42, "acme-seller")
            self.logger.info("account action")
            payload = self._last_line()
        finally:
            request_id_ctx.reset(req_token)
            account_id_ctx.set(None)
            tenant_slug_ctx.set(None)

        self.assertEqual(payload["account_id"], "42")
        self.assertEqual(payload["tenant_slug"], "acme-seller")

    def test_exception_logging_includes_traceback(self) -> None:
        try:
            raise ValueError("boom")
        except ValueError:
            self.logger.exception("failed")
        payload = self._last_line()
        self.assertEqual(payload["level"], "ERROR")
        self.assertIn("ValueError: boom", payload["exception"])


if __name__ == "__main__":
    unittest.main()
