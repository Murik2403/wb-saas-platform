"""Contract tests for opt-in gateway error reporting."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

GATEWAY_ROOT = Path(__file__).resolve().parents[1]
if str(GATEWAY_ROOT) not in sys.path:
    sys.path.insert(0, str(GATEWAY_ROOT))

import config  # noqa: E402
import sentry_integration  # noqa: E402


class SentryIntegrationTests(unittest.TestCase):
    def test_initialization_is_a_no_op_without_a_dsn(self) -> None:
        with mock.patch.object(config, "SENTRY_DSN", ""):
            sentry_integration.initialize()

    def test_initialization_uses_non_pii_configuration(self) -> None:
        sdk = mock.Mock()
        with (
            mock.patch.object(config, "SENTRY_DSN", "https://key@example.ingest.sentry.io/1"),
            mock.patch.object(config, "SENTRY_ENVIRONMENT", "staging"),
            mock.patch.dict(sys.modules, {"sentry_sdk": sdk}),
        ):
            sentry_integration.initialize()

        sdk.init.assert_called_once_with(
            dsn="https://key@example.ingest.sentry.io/1",
            environment="staging",
            send_default_pii=False,
        )
        sdk.set_tag.assert_called_once_with("service", "gateway")


if __name__ == "__main__":
    unittest.main()
