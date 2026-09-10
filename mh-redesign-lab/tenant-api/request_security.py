from urllib.parse import urlsplit


def write_origin_allowed(headers, host: str, scheme: str) -> bool:
    """Reject cross-site browser writes; headerless CLI calls remain usable."""
    origin = headers.get("origin")
    if not origin:
        return headers.get("sec-fetch-site", "none") in {"none", "same-origin"}
    try:
        parsed = urlsplit(origin)
        if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            return False
        if parsed.scheme == scheme and parsed.netloc == host:
            return True
        # Local Vite/Next development proxies use a different port. Never
        # extend this exception to a publicly hosted tenant.
        local_hosts = {"127.0.0.1", "localhost"}
        target = urlsplit("http://" + host)
        return (target.hostname in local_hosts and parsed.hostname in local_hosts
                and parsed.scheme == "http" and parsed.port in {3000, 3001, 5177})
    except ValueError:
        return False
