"""Synthetic-only credential tests: never query live logs or credentials."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest
import httpx
import logging
import io
from logging_config import JSONFormatter
import sentry_integration as gateway_sentry

spec = importlib.util.spec_from_file_location("tenant_telemetry_policy", Path(__file__).resolve().parents[2] / "tenant-app" / "sentry_integration.py")
tenant_sentry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tenant_sentry)

TOKEN = "123456:synthetic_abcdefghijklmnopqrstuvwxyz"


@pytest.mark.parametrize("policy", [gateway_sentry, tenant_sentry], ids=["gateway", "tenant"])
def test_event_exception_and_breadcrumb_redact_nested_secrets_without_mutating_input(policy):
    event = {
        "exception": {"values": [{"type": "ConnectTimeout", "value": "HTTPSConnectionPool: https://api.telegram.org/bot"+TOKEN+"/getUpdates"}]},
        "request": {"headers": [["Authorization","Basic synthetic-auth"],["Api-Key","synthetic-api"],["Client-Id","synthetic-client"]]},
        "extra": {"nested": [{"data":{"aPiKeY":"synthetic-camel","client_id":"synthetic-snake","password":"synthetic-password"}}]},
        "breadcrumbs": {"values": [{"data": {"url":"https://api.telegram.org/file/bot"+TOKEN+"/file/path","status_code":429}}]},
    }
    original = copy.deepcopy(event)
    cleaned = policy.before_send(event,{})
    rendered = json.dumps(cleaned)
    for secret in (TOKEN,"synthetic-auth","synthetic-api","synthetic-client","synthetic-camel","synthetic-snake","synthetic-password"):
        assert secret not in rendered
    assert "getUpdates" in rendered and "ConnectTimeout" in rendered
    assert cleaned["breadcrumbs"]["values"][0]["data"]["status_code"] == 429
    assert event == original
    breadcrumb = {"category":"http","message":"GET https://api.telegram.org/bot"+TOKEN+"/sendMessage","data":{"Authorization":"synthetic-auth"}}
    assert TOKEN not in json.dumps(policy.before_breadcrumb(breadcrumb,{}))
    assert "synthetic-auth" not in json.dumps(policy.before_breadcrumb(breadcrumb,{}))


@pytest.mark.parametrize("policy", [gateway_sentry, tenant_sentry], ids=["gateway", "tenant"])
def test_raw_structured_strings_encoded_urls_and_cycles_are_safe(policy):
    text = "https://api.telegram.org/bot"+TOKEN.replace(":","%3A")+"/sendMessage {'Api-Key': 'synthetic-text', 'Authorization': 'Basic synthetic-basic'}"
    rendered = json.dumps(policy.before_send({"message":text},{}))
    assert "synthetic" not in rendered
    assert "sendMessage" in rendered
    cyclic = {}; cyclic["self"] = cyclic
    assert policy.before_send(cyclic,{}) == {"self":"[REDACTED]"}


@pytest.mark.parametrize("policy", [gateway_sentry, tenant_sentry], ids=["gateway", "tenant"])
def test_asgi_byte_headers_and_environment_credential_keys(policy):
    event={"nested":{"headers":[(b"authorization",b"synthetic-header"),(b"api-key",b"synthetic-api")], "environment":{"WB_TOKEN":"synthetic-wb", "OPENAI_API_KEY":"synthetic-openai", "WB_SAAS_DATA_ENCRYPTION_KEY":"synthetic-key"}, "token_count":42}}
    rendered=json.dumps(policy.before_send(event,{}))
    assert "synthetic" not in rendered
    assert '"token_count": 42' in rendered


@pytest.mark.parametrize("policy", [gateway_sentry, tenant_sentry], ids=["gateway", "tenant"])
def test_gateway_identity_proof_redacted_as_header_url_and_rendered_field(policy):
    proof="1700000000."+"a"*64
    event={"headers":{"X-MH-Gateway-Auth":proof},"message":"X-MH-Gateway-Auth: "+proof, "url":"https://example.test/api?x-mh-gateway-auth="+proof}
    assert proof not in json.dumps(policy.before_send(event,{}))
    record=logging.makeLogRecord({"msg":"proof %s", "args":(event,)})
    assert proof not in JSONFormatter().format(record)


def test_real_httpx_request_logger_is_redacted_without_network():
    stream=io.StringIO(); handler=logging.StreamHandler(stream); handler.setFormatter(JSONFormatter())
    logger=logging.getLogger("httpx"); old_handlers,old_level,old_propagate=logger.handlers[:],logger.level,logger.propagate
    logger.handlers=[handler];logger.setLevel(logging.INFO);logger.propagate=False
    try:
        with httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(429))) as client:
            response=client.get("https://api.telegram.org/bot"+TOKEN+"/getUpdates")
            assert response.status_code==429
    finally:
        logger.handlers=old_handlers;logger.setLevel(old_level);logger.propagate=old_propagate
    rendered=stream.getvalue()
    assert TOKEN not in rendered and "getUpdates" in rendered and "429" in rendered


@pytest.mark.parametrize("policy", [gateway_sentry, tenant_sentry], ids=["gateway", "tenant"])
def test_real_sentry_client_emits_only_redacted_event_to_in_memory_transport(policy):
    from sentry_sdk import Client
    from sentry_sdk.transport import Transport
    captured=[]
    class MemoryTransport(Transport):
        def capture_envelope(self,envelope):
            for item in envelope.items:
                if item.headers.get("type")=="event":
                    captured.append(item.payload.json)
    with Client(dsn="https://synthetic@example.test/1",transport=MemoryTransport,default_integrations=False,
                include_local_variables=False,send_default_pii=False,before_send=policy.before_send,
                before_breadcrumb=policy.before_breadcrumb,before_send_transaction=policy.before_send) as client:
        assert client.options["include_local_variables"] is False
        client.capture_event({"message":"GET https://api.telegram.org/bot"+TOKEN+"/getUpdates", "extra":{"Api-Key":"synthetic-secret"}})
    assert len(captured)==1
    rendered=json.dumps(captured[0])
    assert TOKEN not in rendered and "synthetic-secret" not in rendered and "getUpdates" in rendered
