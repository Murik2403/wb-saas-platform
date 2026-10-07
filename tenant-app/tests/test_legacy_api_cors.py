import importlib.util
from pathlib import Path
import pytest
from fastapi.middleware.cors import CORSMiddleware
import config


@pytest.mark.parametrize("slug,toggle,allowed", [("", "",False),("", "1",True),("demo", "1",False),("synthetic-tenant", "1",False)])
def test_legacy_cors_only_enabled_for_explicit_local_development(monkeypatch,slug,toggle,allowed):
    monkeypatch.setattr(config,"TENANT_SLUG",slug)
    monkeypatch.setenv("MH_ENABLE_DEV_CORS",toggle)
    spec=importlib.util.spec_from_file_location("legacy_cors_probe",Path(__file__).resolve().parents[1]/"api_server.py")
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert any(item.cls is CORSMiddleware for item in module.app.user_middleware) is allowed
