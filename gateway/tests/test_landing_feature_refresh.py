"""Published feature claims and lesson links describe the same released cabinet."""
import json
import re
import sys
import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
import app as gateway
from training_materials import catalog


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.training = []

    def handle_starttag(self, tag, attrs):
        href = dict(attrs).get('href', '')
        if tag == 'a' and href.startswith('/training#'):
            self.training.append(urlsplit(href).fragment)


class LandingRefreshTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(gateway.app)

    def tearDown(self):
        self.client.close()

    def test_home_lesson_links_resolve_to_catalog_and_rendered_anchors(self):
        html = self.client.get('/').text
        links = Links()
        links.feed(html)
        slugs = {item['slug'] for item in catalog()}
        training = self.client.get('/training').text
        self.assertEqual(len(set(links.training)), 5)
        for slug in links.training:
            self.assertIn(slug, slugs)
            self.assertIn(f'id="{slug}"', training)
        self.assertIn('href="/training">Видеоуроки', html)

    def test_home_and_capabilities_keep_new_features_and_explicit_sources(self):
        home = self.client.get('/').text
        capabilities = self.client.get('/capabilities').text
        for phrase in ('тепловая карта', 'РНП', 'НДС', 'FBS', 'самовыкупов'):
            self.assertIn(phrase, home)
            self.assertIn(phrase, capabilities)
        self.assertIn('налоговый раздел не отправляет декларации', home)
        self.assertIn('Расчёт ведётся отдельно от P&amp;L', home)
        self.assertIn('в собственных единицах измерения', home)
        self.assertNotIn('рассчитаны на метры и рулоны', home)
        self.assertNotIn('Себестоимость для расчёта маржи задаётся отдельно', home)
        self.assertNotIn('1С', home)
        self.assertNotIn('1С', capabilities)

    def test_structured_data_and_faq_do_not_preserve_stale_features(self):
        html = self.client.get('/').text
        raw = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S).group(1)
        graph = json.loads(raw)['@graph']
        software = next(item for item in graph if item['@type'] == 'SoftwareApplication')
        features = ' '.join(software['featureList'])
        for phrase in ('налоговый расчёт', 'РНП', 'FBS', 'Вкладки разделов', 'Обучающие видео'):
            self.assertIn(phrase, features)
        faq = next(item for item in graph if item['@type'] == 'FAQPage')
        for question in faq['mainEntity']:
            self.assertIn(question['acceptedAnswer']['text'], html)

    def test_new_release_journal_links_to_training_and_preserves_previous_release(self):
        html = self.client.get('/updates').text
        self.assertIn('<time datetime="2026-10-03">', html)
        self.assertIn('<time datetime="2026-09-30">', html)
        self.assertIn('href="/training"', html)
        self.assertIn('FBS-заказов', html)

    def test_author_and_wip_guide_describe_the_implemented_sections(self):
        author = self.client.get('/ob-avtore').text
        self.assertIn('отдельный документальный налоговый расчёт', author)
        self.assertIn('href="/training#lesson-12-tax"', author)
        self.assertIn('подготовка и отправка деклараций в этот раздел не входят', author)
        self.assertNotIn('В сервисе их нет', author)
        wip = self.client.get('/guides/nezavershennoe-proizvodstvo').text
        self.assertIn('Производство → НЗП и сырьё', wip)
        self.assertIn('стоимость сырья в НЗП', wip)
        self.assertIn('не применяются автоматически', wip)
        self.assertNotIn('Оценку НЗП в рублях по способу из раздела 2 ведите в своей расчётной таблице', wip)
