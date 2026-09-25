"""Replace one tenant with a verified image, preserving its complete runtime config.

Run on the Docker host. No secrets are written to stdout or to a config file.
The stopped previous container is retained for rollback; volumes are never removed.
"""
import argparse
import copy
import http.client
import json
import socket
import subprocess
import time
from urllib.parse import quote


class DockerConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect("/var/run/docker.sock")


def docker_api(method, path, body=None):
    conn = DockerConnection("localhost", timeout=90)
    conn.request(method, "/v1.45" + path, json.dumps(body) if body is not None else None, {"Content-Type": "application/json"})
    response = conn.getresponse()
    payload = response.read()
    conn.close()
    if response.status >= 400:
        raise RuntimeError(f"Docker operation {method} {path.split('?')[0]} failed: HTTP {response.status}")
    return json.loads(payload) if payload else None


def _derive_tenant_encryption_env(gateway_container, original_env):
    """Derive a tenant-scoped key in the gateway without logging its master/key."""
    if original_env.get("WB_SAAS_DATA_ENCRYPTION_KEY"):
        return {}
    slug = original_env.get("WB_SAAS_TENANT_SLUG", "")
    if not gateway_container or not slug:
        raise RuntimeError("Tenant encryption key is missing and cannot be derived")
    code = (
        "import json,sys; import config; from internal_auth import tenant_data_key; "
        "key=tenant_data_key(config.TENANT_ENCRYPTION_MASTER_KEY,sys.argv[1]); "
        "assert key; "
        "print(json.dumps({'key':key,'key_id':config.TENANT_ENCRYPTION_KEY_ID,"
        "'keyring':config.TENANT_ENCRYPTION_KEYRING},separators=(',',':')))"
    )
    result = subprocess.run(
        ["docker", "exec", gateway_container, "python", "-c", code, slug],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise RuntimeError("Tenant encryption key derivation failed; tenant untouched")
    try:
        derived = json.loads(result.stdout)
        key = derived["key"]
        key_id = derived["key_id"]
        keyring = derived["keyring"]
        assert key and key_id and isinstance(keyring, str)
    except (ValueError, KeyError, AssertionError, TypeError) as exc:
        raise RuntimeError("Gateway returned invalid tenant encryption configuration") from exc
    return {
        "WB_SAAS_DATA_ENCRYPTION_KEY": key,
        "WB_SAAS_DATA_ENCRYPTION_KEY_ID": key_id,
        "WB_SAAS_DATA_ENCRYPTION_KEYRING": keyring,
    }


def _verified_backup_script(encryption_env):
    """Build the stdin payload so key material never appears in docker argv."""
    return (
        "import json,os\n"
        f"os.environ.update(json.loads({json.dumps(json.dumps(encryption_env))}))\n"
        "from config import DATA_ENCRYPTION_KEY\n"
        "from backup_tools import create_backup,inspect_backup\n"
        "assert DATA_ENCRYPTION_KEY, 'Tenant backup encryption key is not configured'\n"
        "p=create_backup(keep=1000000); payload=p.read_bytes()\n"
        "assert payload.startswith((b'mh-backup-enc:v1:', b'mh-backup-enc:v2:')), 'Backup is not encrypted'\n"
        "assert inspect_backup(payload); print(p.name)\n"
    )


def _container_create_body(original, image_id, container_name, expected_env):
    """Build the flattened Docker Engine ContainerCreate request body.

    The Engine API accepts Config fields at the body root, with HostConfig and
    NetworkingConfig as sibling fields.  Keep the original runtime settings
    and endpoint aliases while allowing the replacement image and derived
    tenant key to change.
    """
    body = copy.deepcopy(original["Config"])
    body["Image"] = image_id
    body["Env"] = expected_env
    body.pop("Hostname", None)
    body["HostConfig"] = copy.deepcopy(original["HostConfig"])

    networks = original.get("NetworkSettings", {}).get("Networks", {}) or {}
    endpoints = {}
    for network_name, network in networks.items():
        endpoint = {}
        # Keep endpoint options that are safe to share. Do not copy IPAM
        # addresses or MAC addresses: the stopped rollback container still
        # owns them until a successful release is accepted.
        for field in ("Links", "DriverOpts"):
            if network.get(field) is not None:
                endpoint[field] = copy.deepcopy(network[field])
        aliases = list(network.get("Aliases") or [])
        if container_name not in aliases:
            aliases.append(container_name)
        endpoint["Aliases"] = aliases
        endpoints[network_name] = endpoint
    body["NetworkingConfig"] = {"EndpointsConfig": endpoints}
    return body


def _mount_signature(container):
    return sorted(
        (
            str(mount.get("Type") or ""), str(mount.get("Name") or ""),
            str(mount.get("Source") or ""), str(mount.get("Destination") or ""),
            bool(mount.get("RW")),
        )
        for mount in container.get("Mounts", [])
    )


def _assert_runtime_parity(original, actual, container_name):
    """Fail closed if a replacement lost persistent data or routing config."""
    assert _mount_signature(actual) == _mount_signature(original), "Tenant mounts changed during replacement"
    original_host = original.get("HostConfig", {}) or {}
    actual_host = actual.get("HostConfig", {}) or {}
    for field in ("Binds", "Mounts", "PortBindings", "PublishAllPorts", "NetworkMode", "RestartPolicy"):
        assert actual_host.get(field) == original_host.get(field), f"Tenant HostConfig.{field} changed during replacement"

    original_ports = (original.get("Config", {}) or {}).get("ExposedPorts") or {}
    actual_ports = (actual.get("Config", {}) or {}).get("ExposedPorts") or {}
    assert actual_ports == original_ports, "Tenant exposed ports changed during replacement"

    original_networks = (original.get("NetworkSettings", {}) or {}).get("Networks", {}) or {}
    actual_networks = (actual.get("NetworkSettings", {}) or {}).get("Networks", {}) or {}
    assert set(actual_networks) == set(original_networks), "Tenant Docker networks changed during replacement"
    for network_name, old_network in original_networks.items():
        actual_aliases = set((actual_networks[network_name] or {}).get("Aliases") or [])
        expected_aliases = set(old_network.get("Aliases") or [])
        expected_aliases.add(container_name)
        assert expected_aliases <= actual_aliases, f"Tenant aliases changed on network {network_name}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--container", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--expected-image-id", required=True)
    parser.add_argument("--gateway-container", help="Gateway used to derive a missing tenant-scoped encryption key")
    args = parser.parse_args()
    original = docker_api("GET", f"/containers/{args.container}/json")
    image = docker_api("GET", f"/images/{quote(args.image, safe='')}/json")
    assert image["Id"] == args.expected_image_id, "Image identity mismatch"
    revision = (image.get("Config", {}).get("Labels") or {}).get("org.opencontainers.image.revision", "")
    assert revision and revision not in {"dev", "unknown"}, "Image must contain a non-empty source revision label"
    assert original["State"]["Running"], "Original container must be running"
    original_env = {
        item.split("=", 1)[0]: item.split("=", 1)[1]
        for item in original["Config"].get("Env", [])
        if "=" in item
    }
    added_env = _derive_tenant_encryption_env(args.gateway_container, original_env)
    expected_env = list(original["Config"].get("Env", []))
    for name, value in added_env.items():
        prefix = name + "="
        positions = [i for i, item in enumerate(expected_env) if item.startswith(prefix)]
        entry = f"{name}={value}"
        if positions:
            expected_env[positions[0]] = entry
            for duplicate in reversed(positions[1:]):
                expected_env.pop(duplicate)
        else:
            expected_env.append(entry)
    previous = args.container + "-rollback-" + time.strftime("%Y%m%d-%H%M%S")
    # Uses the application's verified SQLite backup and retains all existing backups.
    # The tenant image has a 64 MiB /tmp tmpfs by default.  SQLite's verified
    # snapshot is larger than that for real tenants, so place temporary backup
    # files on the persistent data mount instead.
    # Tenant archives contain the full database and possibly marketplace
    # credentials. Never let a release silently create a plaintext copy on the
    # persistent tenant volume when the encryption key is absent.
    backup_code = _verified_backup_script(added_env)
    backup = subprocess.run(["docker", "exec", "-i", "-e", "TMPDIR=/app/data", args.container, "python", "-"], input=backup_code, capture_output=True, text=True)
    if backup.returncode:
        raise RuntimeError("Verified pre-release backup failed; original container untouched")
    print("Verified backup:", backup.stdout.strip(), flush=True)

    create_body = _container_create_body(original, image["Id"], args.container, expected_env)
    stopped = renamed = created = False
    try:
        # A live tenant may be finishing a WB sync while the release starts.
        # Give its workers enough time to release SQLite/flock guards cleanly;
        # Docker still force-stops after this bounded grace period.
        docker_api("POST", f"/containers/{args.container}/stop?t=60")
        stopped = True
        docker_api("POST", f"/containers/{args.container}/rename?name={previous}")
        renamed = True
        docker_api("POST", f"/containers/create?name={args.container}", create_body)
        created = True
        docker_api("POST", f"/containers/{args.container}/start")
        probe = "import urllib.request,json; base='http://127.0.0.1:8501'; paths=['/healthz','/api/v1/dashboard/today','/api/v1/operations/reports','/api/v1/action-plan/items']; [json.load(urllib.request.urlopen(base+p,timeout=25)) for p in paths]"
        for attempt in range(20):
            result = subprocess.run(["docker", "exec", args.container, "python", "-c", probe], capture_output=True, timeout=110)
            if result.returncode == 0:
                actual = docker_api("GET", f"/containers/{args.container}/json")
                assert actual["Image"] == image["Id"]
                assert actual["Config"]["Env"] == expected_env
                _assert_runtime_parity(original, actual, args.container)
                # Docker merges image metadata with explicitly supplied labels.
                # Preserve every original routing label while allowing a new
                # image revision label on older containers that lacked one.
                expected_labels = {**(image.get("Config", {}).get("Labels") or {}), **(original["Config"].get("Labels") or {})}
                assert (actual["Config"].get("Labels") or {}) == expected_labels
                print("DEPLOY_VERIFIED", json.dumps({"container": args.container, "image": image["Id"], "rollback": previous, "probes": 4}), flush=True)
                return
            time.sleep(3)
        raise RuntimeError("Post-deploy business API checks failed")
    except BaseException:
        if created:
            docker_api("DELETE", f"/containers/{args.container}?force=true")
        if renamed:
            docker_api("POST", f"/containers/{previous}/rename?name={args.container}")
        if stopped:
            docker_api("POST", f"/containers/{args.container}/start")
        print("ROLLED_BACK", flush=True)
        raise


if __name__ == "__main__":
    main()
