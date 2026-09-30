from pathlib import Path
import re

def test_selected_worker_asgi_dependency_is_declared_and_locked_with_hashes():
    root=Path(__file__).resolve().parents[1]
    assert 'httpx==0.28.1' in (root/'requirements.in').read_text()
    lock=(root/'requirements.lock').read_text()
    for package,version in [('httpx','0.28.1'),('httpcore','1.0.9')]:
        assert re.search(rf'(?m)^{package}=={re.escape(version)} \\\n\s+--hash=sha256:[0-9a-f]{{64}}',lock)
    docker=(root.parent/'mh-redesign-lab'/'Dockerfile.tenant-staging').read_text()
    assert 'from httpx import ASGITransport' in docker
