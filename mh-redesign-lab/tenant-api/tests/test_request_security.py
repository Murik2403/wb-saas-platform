from request_security import write_origin_allowed


def test_browser_and_server_origins():
    assert write_origin_allowed({}, "127.0.0.1:8001", "http")
    assert write_origin_allowed({"origin": "http://127.0.0.1:5177"}, "127.0.0.1:8001", "http")
    assert write_origin_allowed({"origin": "https://shop.example"}, "shop.example", "https")
    for origin in ["null", "https://evil.example", "https://shop.example.evil", "http://shop.example", "http://localhost:5177"]:
        assert not write_origin_allowed({"origin": origin}, "shop.example", "https")
    assert not write_origin_allowed({"sec-fetch-site": "cross-site"}, "shop.example", "https")
