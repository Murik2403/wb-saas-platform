import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "audit_public_seo.py"
spec = importlib.util.spec_from_file_location("public_seo_audit", SCRIPT)
audit = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(audit)


def test_audit_passes_complete_page():
    html = '<html><head><title>Маржа</title><meta name="description" content="Расчёт"><link rel="canonical" href="https://example.test/calculator"></head><body><h1>Калькулятор</h1><a href="/pricing">Тариф</a></body></html>'
    result = audit.audit_html(html, "https://example.test/calculator", known_urls={"https://example.test/pricing"})
    assert result["status"] == "pass"


def test_audit_warns_or_fails_missing_canonical():
    html = '<title>Маржа</title><meta name="description" content="Расчёт"><h1>Калькулятор</h1>'
    result = audit.audit_html(html, "https://example.test/calculator")
    assert result["status"] == "fail"
    assert {item["code"] for item in result["issues"]} == {"missing_canonical"}


def test_audit_finds_broken_internal_link():
    html = '<title>Маржа</title><meta name="description" content="Расчёт"><link rel="canonical" href="https://example.test/calculator"><h1>Калькулятор</h1><a href="/missing">Нет</a>'
    result = audit.audit_html(html, "https://example.test/calculator", known_urls={"https://example.test/calculator"})
    assert result["status"] == "fail"
    assert any(item["code"] == "broken_internal_links" for item in result["issues"])
