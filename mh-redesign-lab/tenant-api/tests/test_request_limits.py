import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import asyncio
import pytest
from request_limits import RequestLimitsMiddleware, body_limit


def invoke(messages, *, headers=(), path="/login", service="gateway", downstream=None):
    output=[]; calls=[]; queue=iter(messages)
    async def receive(): return next(queue, {"type":"http.disconnect"})
    async def send(message): output.append(message)
    async def app(scope,receive,send):
        calls.append(True)
        if downstream:
            return await downstream(scope,receive,send)
        body=b""
        while True:
            message=await receive(); body+=message.get("body",b"")
            if not message.get("more_body",False):break
        await send({"type":"http.response.start","status":206,"headers":[(b"content-range",b"bytes 0-2/10")]})
        await send({"type":"http.response.body","body":body,"more_body":True})
        await send({"type":"http.response.body","body":b"end"})
    asyncio.run(RequestLimitsMiddleware(app,service=service)({"type":"http","headers":list(headers),"path":path},receive,send))
    return calls,output


def test_declared_large_body_is_rejected_before_receive_or_parser():
    calls,out=invoke([],headers=[(b"content-length",b"1048577")])
    assert not calls and out[0]["status"]==413


def test_chunked_body_cannot_bypass_limit():
    messages=[{"type":"http.request","body":b"x"*600000,"more_body":True},{"type":"http.request","body":b"x"*600000}]
    calls,out=invoke(messages)
    assert not calls and out[0]["status"]==413


@pytest.mark.parametrize("headers", [[(b"content-length",b"-1")],[(b"content-length",b"1"),(b"content-length",b"1")]])
def test_malformed_length_is_rejected(headers):
    calls,out=invoke([],headers=headers)
    assert not calls and out[0]["status"]==413


def test_large_headers_rejected_before_application():
    calls,out=invoke([],headers=[(b"cookie",b"x"*32769)])
    assert not calls and out[0]["status"]==431


def test_zero_size_fragment_flood_is_bounded():
    calls,out=invoke([{"type":"http.request","body":b"","more_body":True}]*4097)
    assert not calls and out[0]["status"]==413


def test_request_chunks_and_streaming_range_response_are_preserved():
    calls,out=invoke([{"type":"http.request","body":b"a","more_body":True},{"type":"http.request","body":b"bc"}],headers=[(b"range",b"bytes=0-2")])
    assert calls and out[0]["status"]==206
    assert out[0]["headers"]==[(b"content-range",b"bytes 0-2/10")]
    assert out[1]["body"]==b"abc" and out[1]["more_body"] and out[2]["body"]==b"end"


def test_only_known_upload_routes_receive_larger_limit():
    assert body_limit("/internal/send-report-email","gateway")==24*1024*1024
    assert body_limit("/api/v1/settings/store-expenses/import/preview","tenant")==10*1024*1024
    assert body_limit("/api/v1/incident-workflow/abc/attachments","tenant")==4*1024*1024
    assert body_limit("/api/v1/ozon-accounting/movements/preview","tenant")==10*1024*1024
    assert body_limit("/login/upload","gateway")==1024*1024


def test_development_cors_is_explicit_and_never_enabled_for_tenants():
    from request_limits import development_cors_origins
    assert development_cors_origins("", "") == []
    assert development_cors_origins("demo", "1") == []
    assert development_cors_origins("mvvorokov", "1") == []
    assert development_cors_origins("", "true") == []
    assert development_cors_origins("", "1") == ["http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:3001", "http://127.0.0.1:3001"]
