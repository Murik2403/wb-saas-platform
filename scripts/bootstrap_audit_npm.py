"""Fetch the official npm CLI into an isolated audit-tools directory."""
import base64
import hashlib
import io
from pathlib import Path
import tarfile
import requests

root = Path(__file__).resolve().parents[1]
metadata = requests.get("https://registry.npmjs.org/npm/latest", timeout=30)
metadata.raise_for_status()
dist = metadata.json()["dist"]
if not dist["tarball"].startswith("https://registry.npmjs.org/npm/-/"):
    raise RuntimeError("Unexpected npm source")
response = requests.get(dist["tarball"], timeout=30)
response.raise_for_status()
digest = "sha512-" + base64.b64encode(hashlib.sha512(response.content).digest()).decode()
if digest != dist["integrity"]:
    raise RuntimeError("npm archive integrity mismatch")
target = root / ".audit-tools" / "npm"
target.mkdir(parents=True, exist_ok=True)
with tarfile.open(fileobj=io.BytesIO(response.content), mode="r:gz") as archive:
    archive.extractall(target, filter="data")
print("npm", metadata.json()["version"], "ready")
