"""Regression checks for public-page privacy assets.

These tests deliberately inspect source templates: they prevent a future visual
change from silently restoring a third-party font request or removing the
visitor's visible cookie choice.
"""
from __future__ import annotations

from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
GATEWAY_BASE = PROJECT_ROOT / "gateway" / "templates" / "base.html"
TENANT_APP = PROJECT_ROOT / "tenant-app" / "app.py"
PRIVACY_TEMPLATE = PROJECT_ROOT / "gateway" / "templates" / "privacy.html"


class PublicPrivacyAssetsTests(unittest.TestCase):
    def test_public_templates_do_not_reference_google_fonts(self) -> None:
        for path in (GATEWAY_BASE, TENANT_APP):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("fonts.googleapis.com", source)
            self.assertNotIn("fonts.gstatic.com", source)

    def test_base_template_has_cookie_notice_with_a_real_decline_path(self) -> None:
        source = GATEWAY_BASE.read_text(encoding="utf-8")
        self.assertIn('id="cookie-notice"', source)
        self.assertIn('id="cookie-accept"', source)
        self.assertIn('id="cookie-decline"', source)
        self.assertIn("marketshelper_cookie_notice", source)

    def test_cookie_notice_details_link_has_a_matching_policy_anchor(self) -> None:
        source = PRIVACY_TEMPLATE.read_text(encoding="utf-8")
        self.assertIn('id="cookies"', source)
