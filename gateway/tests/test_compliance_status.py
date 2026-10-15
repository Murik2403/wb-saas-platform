from __future__ import annotations

import unittest
import sys
import json
from html.parser import HTMLParser
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

GATEWAY_ROOT = Path(__file__).resolve().parents[1]
if str(GATEWAY_ROOT) not in sys.path:
    sys.path.insert(0, str(GATEWAY_ROOT))

from logic import compliance
import app as gateway_app


class ComplianceMarkup(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.flows = 0
        self.ids: set[str] = set()
        self.local_targets: set[str] = set()
        self.links: set[str] = set()
        self.tables = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        self.ids.update([values["id"]] if values.get("id") else [])
        if tag == "article" and "compliance-flow" in (values.get("class") or "").split():
            self.flows += 1
        if tag == "table":
            self.tables += 1
        if tag == "a" and values.get("href"):
            self.links.add(values["href"])
            if values["href"].startswith("#"):
                self.local_targets.add(values["href"][1:])


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

    def test_registered_operator_and_disabled_channel_are_current(self) -> None:
        context = compliance.get_compliance_status()
        self.assertEqual(context["rkn"]["record_number"], "77-26-574895")
        self.assertEqual(context["rkn"]["record_url"],
                         "https://pd.rkn.gov.ru/operators-registry/operators-list/?id=77-26-574895")
        self.assertIn("Внесён в реестр", context["rkn"]["status"])
        self.assertIn("официальном реестре Роскомнадзора", context["rkn"]["status_hint"])
        self.assertIn("Telegram временно отключён", json.dumps(context, ensure_ascii=False))
        for stale in ("Не подано", "требуется подача", "Подать уведомление РКН", "командой tar", "жестко зашита"):
            self.assertNotIn(stale, json.dumps(context, ensure_ascii=False))

    def test_incident_deadlines_are_distinct_and_fallback_does_not_restore_stale_advice(self) -> None:
        with TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.json"
            with patch.object(compliance, "_DATA_PATH", missing):
                fallback = compliance.get_compliance_status()
        for context in (compliance.get_compliance_status(), fallback):
            steps = " ".join(context["incident_steps"])
            self.assertIn("24 часов", steps)
            self.assertIn("72 часов", steps)
            self.assertNotIn("уведомить уполномоченный орган и затронутых субъектов", steps)
            self.assertIn("Telegram временно отключён", json.dumps(context, ensure_ascii=False))
        self.assertEqual(fallback["rkn"]["status"], "Требуется ручная проверка")

    def test_public_page_omits_storage_description_and_preserves_navigation(self) -> None:
        response = TestClient(gateway_app.app).get("/compliance")
        self.assertEqual(response.status_code, 200)
        body = response.text
        markup = ComplianceMarkup()
        markup.feed(body)
        self.assertEqual(markup.flows, 0)
        self.assertEqual(markup.tables, 0, "Multi-column technical table must not return on phones")
        self.assertEqual(markup.local_targets, {"registry", "incidents", "requests"})
        self.assertTrue(markup.local_targets.issubset(markup.ids))
        for href in (compliance.get_compliance_status()["rkn"]["record_url"],
                     "/privacy", "/consent", "/contacts", "/delete-account",
                     "https://mintrud.gov.ru/docs/laws/130"):
            self.assertIn(href, markup.links)
        self.assertIn("77-26-574895", body)
        for removed in ("Хранение данных", "Внешние каналы", "Журналы событий", "Резервные копии", "Timeweb", 'id="data"'):
            self.assertNotIn(removed, body)
        self.assertNotIn("Не подано", body)
        self.assertNotIn("в рабочее время", body)
