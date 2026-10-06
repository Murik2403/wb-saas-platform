import unittest

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from body_limit import BodyLimitMiddleware, DEFAULT_LIMIT, INTERNAL_LIMIT


def _client():
    app = FastAPI()
    app.add_middleware(BodyLimitMiddleware)

    @app.post("/form")
    async def form(request: Request):
        return {"n": len(await request.body())}

    @app.post("/internal/x")
    async def internal(request: Request):
        return {"n": len(await request.body())}

    @app.get("/g")
    def get():
        return {}

    return TestClient(app)


class BodyLimitTests(unittest.TestCase):
    def test_small_body_passes(self):
        self.assertEqual(_client().post("/form", content=b"a" * 100).status_code, 200)

    def test_declared_oversize_rejected(self):
        self.assertEqual(_client().post("/form", content=b"a" * (DEFAULT_LIMIT + 1)).status_code, 413)

    def test_internal_has_larger_limit(self):
        c = _client()
        self.assertEqual(c.post("/internal/x", content=b"a" * (DEFAULT_LIMIT + 1)).status_code, 200)
        self.assertEqual(c.post("/internal/x", content=b"a" * (INTERNAL_LIMIT + 1)).status_code, 413)

    def test_chunked_oversize_rejected(self):
        def gen():
            for _ in range(DEFAULT_LIMIT // 1024 + 2):
                yield b"a" * 1024
        self.assertEqual(_client().post("/form", content=gen()).status_code, 413)

    def test_get_untouched(self):
        self.assertEqual(_client().get("/g").status_code, 200)


if __name__ == "__main__":
    unittest.main()
