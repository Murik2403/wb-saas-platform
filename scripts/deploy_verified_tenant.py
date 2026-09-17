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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--container", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--expected-image-id", required=True)
    args = parser.parse_args()
    original = docker_api("GET", f"/containers/{args.container}/json")
    image = docker_api("GET", f"/images/{quote(args.image, safe='')}/json")
    assert image["Id"] == args.expected_image_id, "Image identity mismatch"
    revision = (image.get("Config", {}).get("Labels") or {}).get("org.opencontainers.image.revision", "")
    assert revision and revision not in {"dev", "unknown"}, "Image must contain a non-empty source revision label"
    assert original["State"]["Running"], "Original container must be running"
    previous = args.container + "-rollback-" + time.strftime("%Y%m%d-%H%M%S")
    # Uses the application's verified SQLite backup and retains all existing backups.
    backup = subprocess.run(["docker", "exec", args.container, "python", "-c", "from backup_tools import create_backup,inspect_backup; p=create_backup(keep=1000000); assert inspect_backup(p.read_bytes()); print(p.name)"], capture_output=True, text=True)
    if backup.returncode:
        raise RuntimeError("Verified pre-release backup failed; original container untouched")
    print("Verified backup:", backup.stdout.strip(), flush=True)

    config = copy.deepcopy(original["Config"])
    config["Image"] = image["Id"]
    config.pop("Hostname", None)
    config["HostConfig"] = copy.deepcopy(original["HostConfig"])
    config["NetworkingConfig"] = {"EndpointsConfig": {name: {"Aliases": [args.container]} for name in original["NetworkSettings"]["Networks"]}}
    stopped = renamed = created = False
    try:
        docker_api("POST", f"/containers/{args.container}/stop?t=30")
        stopped = True
        docker_api("POST", f"/containers/{args.container}/rename?name={previous}")
        renamed = True
        docker_api("POST", f"/containers/create?name={args.container}", config)
        created = True
        docker_api("POST", f"/containers/{args.container}/start")
        probe = "import urllib.request,json; base='http://127.0.0.1:8501'; paths=['/healthz','/api/v1/dashboard/today','/api/v1/operations/reports','/api/v1/action-plan/items']; [json.load(urllib.request.urlopen(base+p,timeout=25)) for p in paths]"
        for attempt in range(20):
            result = subprocess.run(["docker", "exec", args.container, "python", "-c", probe], capture_output=True, timeout=110)
            if result.returncode == 0:
                actual = docker_api("GET", f"/containers/{args.container}/json")
                assert actual["Image"] == image["Id"]
                assert actual["Config"]["Env"] == original["Config"]["Env"]
                assert actual["Config"]["Labels"] == original["Config"]["Labels"]
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
