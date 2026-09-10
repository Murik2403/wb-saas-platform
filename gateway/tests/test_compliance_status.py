from __future__ import annotations

import unittest
import sys
from pathlib import Path

GATEWAY_ROOT = Path(__file__).resolve().parents[1]
if str(GATEWAY_ROOT) not in sys.path:
    sys.path.insert(0, str(GATEWAY_ROOT))

from logic import compliance


class ComplianceStatusTests(unittest.TestCase):
    def test_compliance_status_shape(self) -> None:
        context = compliance.get_compliance_status()
        self.assertIsInstance(context, dict)
        self.assertEqual(context["rkn"]["operator_inn"], "071513433550")
        self.assertIn("localization", context)
        self.assertGreaterEqual(len(context["localization"]), 5)
        self.assertIn("incident_steps", context)
        self.assertGreaterEqual(len(context["incident_steps"]), 4)
        self.assertEqual(context["incident_contact_email"], "mvvorokov@yandex.ru")
