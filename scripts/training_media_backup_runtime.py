"""Host-only, purpose-key RAM handoff into a bounded course-copy container.

No Docker SDK, shell, key arguments/logs/files, gateway startup or production DB.
Only five public code/catalog files and the gateway's read-only media source are
bound. The sole host writable bind is the dedicated training-media backup group.
"""
from __future__ import annotations
import http.client
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import uuid

MEMORY = 512 * 1024 ** 2
MAX_RESPONSE = 6 * 1024 ** 2
MAX_KEY_TRANSPORT = 256 * 1024
PURPOSE_KEYS = ("WB_SAAS_BACKUP_ENCRYPTION_KEY", "WB_SAAS_BACKUP_ENCRYPTION_KEY_ID",
                "WB_SAAS_BACKUP_ENCRYPTION_KEYRING", "WB_SAAS_BACKUP_LEGACY_KEY_ID")
PUBLIC_FILES = ("scripts/training_media_backup.py", "scripts/training_media_backup_job.py",
                "scripts/backup_offsite.py", "provisioner/backup_crypto.py", "gateway/training_catalog.json")


class DockerConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.settimeout(self.timeout)
        self.sock.connect("/var/run/docker.sock")


def api(method, path, body=None):
    connection = DockerConnection("docker", timeout=15)
    try:
        connection.request(method, "/v1.45" + path, json.dumps(body) if body is not None else None,
                           {"Content-Type": "application/json"})
        response = connection.getresponse()
        raw = response.read(MAX_RESPONSE + 1)
        if response.status >= 400 or len(raw) > MAX_RESPONSE:
            raise RuntimeError("training_media_backup_failed")
        return json.loads(raw) if raw else None
    finally:
        connection.close()


def _no_links(path):
    if not path.is_absolute() or any(p.is_symlink() or getattr(p, "is_junction", lambda: False)() for p in (path, *path.parents)):
        raise RuntimeError("training_media_backup_failed")


def prepare_directory(root):
    _no_links(root)
    if not root.is_dir():
        raise RuntimeError("training_media_backup_failed")
    directory = root / "training-media"
    _no_links(directory)
    if not directory.exists():
        directory.mkdir(mode=0o770)
        os.chown(directory, 10001, 10001)
    entry = directory.stat()
    if not stat.S_ISDIR(entry.st_mode) or entry.st_uid != 10001 or entry.st_mode & 0o007:
        raise RuntimeError("training_media_backup_failed")
    return directory


def purpose_keys(provisioner):
    values = {}
    for item in provisioner["Config"].get("Env", []):
        name, sep, value = item.partition("=")
        if sep and name in PURPOSE_KEYS and value:
            if name in values:
                raise RuntimeError("training_media_backup_failed")
            values[name] = value
    key = values.get(PURPOSE_KEYS[0], "")
    if not 32 <= len(key) <= 4096 or any(len(v.encode()) > 65536 for v in values.values()):
        raise RuntimeError("training_media_backup_failed")
    return values


def child_code():
    # Docker exec Cmd/Env contain no secret. Input bytes are a stdin stream;
    # os.environ is changed only inside this short-lived child process, so no
    # additional daemon config.v2.json can persist another plaintext key copy.
    return """import contextlib,io,json,os,pathlib,sys
line=sys.stdin.buffer.readline(262145)
assert len(line)<=262144 and line.endswith(b'\\n')
keys=json.loads(line)
allowed={'WB_SAAS_BACKUP_ENCRYPTION_KEY','WB_SAAS_BACKUP_ENCRYPTION_KEY_ID','WB_SAAS_BACKUP_ENCRYPTION_KEYRING','WB_SAAS_BACKUP_LEGACY_KEY_ID'}
assert isinstance(keys,dict) and set(keys)<=allowed and all(isinstance(v,str) for v in keys.values())
assert len(keys.get('WB_SAAS_BACKUP_ENCRYPTION_KEY',''))>=32
os.environ.update(keys)
del line,keys
sys.path.insert(0,'/release')
from scripts import training_media_backup_job as job
output=io.StringIO()
with contextlib.redirect_stdout(output):status=job.run()
result=json.loads(output.getvalue())
raw=json.dumps(result).encode()
assert len(raw)<=65536
sys.stdout.buffer.write(raw)
raise SystemExit(status)
"""


def configuration(gateway, source_root, media_source, directory, name, keep_days):
    mounts = []
    for relative in PUBLIC_FILES:
        source = source_root / relative
        _no_links(source)
        if not source.is_file():
            raise RuntimeError("training_media_backup_failed")
        mounts.append({"Type": "bind", "Source": str(source), "Target": "/release/" + relative, "ReadOnly": True})
    mounts.extend([{"Type": "bind", "Source": str(media_source), "Target": "/media", "ReadOnly": True},
                   {"Type": "bind", "Source": str(directory), "Target": "/backups/training-media", "ReadOnly": False}])
    return {"Image": gateway["Image"], "User": "10001:10001", "WorkingDir": "/release",
            "Entrypoint": ["python", "-c"], "Cmd": ["import time;time.sleep(300)"], "Labels": {"mh.training.backup.owner": name},
            "Env": ["PYTHONDONTWRITEBYTECODE=1", "PYTHONUNBUFFERED=1", "WB_SAAS_TRAINING_MEDIA_SOURCE_DIR=/media",
                    "WB_SAAS_BACKUP_DIR=/backups", "WB_SAAS_BACKUP_KEEP_DAYS=" + str(keep_days)],
            "HostConfig": {"NetworkMode": "none", "Memory": MEMORY, "MemorySwap": MEMORY,
                           "NanoCpus": 1000000000, "PidsLimit": 128, "CgroupnsMode": "private",
                           "LogConfig": {"Type": "none"}, "ReadonlyRootfs": True, "CapDrop": ["ALL"],
                           "Ulimits": [{"Name": "core", "Soft": 0, "Hard": 0}],
                           "SecurityOpt": ["no-new-privileges:true"], "Mounts": mounts,
                           "Tmpfs": {"/tmp": "rw,noexec,nosuid,nodev,size=16m",
                                     "/app/data": "rw,noexec,nosuid,nodev,size=16m,uid=10001,gid=10001,mode=0700"},
                           "RestartPolicy": {"Name": "no"}}}


def validate_identity(observed, identifier, name):
    if observed["Id"] != identifier or observed["Name"] != "/" + name or observed["Config"].get("Labels", {}).get("mh.training.backup.owner") != name:
        raise RuntimeError("training_media_backup_failed")


def validate_owned(observed, identifier, name, config):
    validate_identity(observed, identifier, name)
    host = observed["HostConfig"]
    if observed["Id"] != identifier or observed["Name"] != "/" + name or observed["Config"].get("Labels", {}).get("mh.training.backup.owner") != name or observed["Image"] != config["Image"] or observed["Config"]["User"] != "10001:10001":
        raise RuntimeError("training_media_backup_failed")
    exact = {"NetworkMode": "none", "Memory": MEMORY, "MemorySwap": MEMORY, "NanoCpus": 1000000000,
             "PidsLimit": 128, "CgroupnsMode": "private", "ReadonlyRootfs": True}
    if any(host.get(key) != value for key, value in exact.items()) or host.get("PortBindings") or host.get("Binds") or host.get("Privileged") or host.get("CapAdd") or host.get("PidMode") or host.get("RestartPolicy", {}).get("Name") != "no" or host.get("LogConfig", {}).get("Type") != "none" or "ALL" not in (host.get("CapDrop") or []) or "no-new-privileges:true" not in (host.get("SecurityOpt") or []):
        raise RuntimeError("training_media_backup_failed")
    if {"Name": "core", "Soft": 0, "Hard": 0} not in (host.get("Ulimits") or []):
        raise RuntimeError("training_media_backup_failed")
    if any(item.partition('=')[0] in PURPOSE_KEYS for item in observed['Config'].get('Env', [])):
        raise RuntimeError("training_media_backup_failed")
    actual = [(m["Source"], m["Destination"], m["RW"]) for m in observed["Mounts"] if m["Type"] == "bind"]
    expected = [(m["Source"], m["Target"], not m["ReadOnly"]) for m in config["HostConfig"]["Mounts"]]
    if sorted(actual) != sorted(expected) or any(m["Type"] not in {"bind", "tmpfs"} for m in observed["Mounts"]):
        raise RuntimeError("training_media_backup_failed")


def read_result(raw):
    if len(raw) > 65536:
        raise RuntimeError("training_media_backup_failed")
    result = json.loads(raw)
    archive = result.get("archive", "")
    if result.get("ok") is not True or result.get("verified_after_write") is not True or result.get("lessons") != 16 or result.get("files") != 32 or not isinstance(archive, str) or not re.fullmatch(r"training-media-[0-9a-f]{12}-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}\.tar\.gz\.enc", archive):
        raise RuntimeError("training_media_backup_failed")
    # Never relay arbitrary child metadata or failure details into host logs.
    return {"kind": "training_media_backup", "ok": True, "archive": archive, "lessons": 16,
            "files": 32, "verified_after_write": True}


def execute_job(identifier, keys):
    encoded=json.dumps(keys,ensure_ascii=False).encode()+b'\n'
    if len(encoded)>MAX_KEY_TRANSPORT:
        raise RuntimeError('training_media_backup_failed')
    command=['docker','--host','unix:///var/run/docker.sock','exec','-i',identifier,'python','-c',child_code()]
    environment={name:value for name,value in os.environ.items() if name not in {'DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH'}}
    try:
        result=subprocess.run(command,input=encoded,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=240,env=environment)
        if result.returncode:
            raise RuntimeError('training_media_backup_failed')
        return read_result(result.stdout)
    except (OSError,subprocess.SubprocessError,ValueError,TypeError,AttributeError):
        raise RuntimeError('training_media_backup_failed') from None


def run(env, source_root, keep_days=14):
    if type(keep_days) is not int or not 1 <= keep_days <= 3650:
        raise RuntimeError("training_media_backup_failed")
    if os.name == "posix":
        import resource
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    source_root = Path(source_root).resolve()
    _no_links(source_root)
    gateway = api("GET", "/containers/wb-saas-gateway-1/json")
    provisioner = api("GET", "/containers/wb-saas-provisioner-1/json")
    if not gateway["State"]["Running"] or not provisioner["State"]["Running"] or not re.fullmatch(r"sha256:[0-9a-f]{64}", gateway["Image"]):
        raise RuntimeError("training_media_backup_failed")
    media_mounts = [m for m in gateway["Mounts"] if m["Destination"] == "/app/training-media"]
    if len(media_mounts) != 1 or media_mounts[0]["Type"] != "bind" or media_mounts[0]["RW"] is not False:
        raise RuntimeError("training_media_backup_failed")
    media_source = Path(media_mounts[0]["Source"])
    _no_links(media_source)
    if not media_source.is_dir():
        raise RuntimeError("training_media_backup_failed")
    directory = prepare_directory(Path(env.get("WB_SAAS_BACKUP_DIR", "/opt/wb-saas/backups")))
    keys = purpose_keys(provisioner)
    token = uuid.uuid4().hex
    name = "mh-training-daily-" + token
    config = configuration(gateway, source_root, media_source, directory, name, keep_days)
    identifier = None
    try:
        response = api("POST", "/containers/create?name=" + name, config)
        identifier = response["Id"]
        if not re.fullmatch(r"[0-9a-f]{64}", identifier):
            raise RuntimeError("training_media_backup_failed")
        observed = api("GET", "/containers/" + identifier + "/json")
        validate_owned(observed, identifier, name, config)
        api("POST", "/containers/" + identifier + "/start")
        live = api("GET", "/containers/" + identifier + "/json")
        validate_owned(live, identifier, name, config)
        if not live['State']['Running']:
            raise RuntimeError('training_media_backup_failed')
        result=execute_job(identifier,keys)
        del keys
        final=api('GET','/containers/'+identifier+'/json')
        validate_owned(final,identifier,name,config)
        if final['State'].get('OOMKilled'):
            raise RuntimeError('training_media_backup_failed')
        return result
    finally:
        if identifier:
            observed = api("GET", "/containers/" + identifier + "/json")
            # Failed isolation validation must still remove OUR created stopped
            # object. Foreign ID/name/label is never a cleanup authorization.
            validate_identity(observed, identifier, name)
            api("DELETE", "/containers/" + identifier + "?force=true&v=true")
