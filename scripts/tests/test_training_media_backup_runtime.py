"""No Docker or real secrets: model complete API boundaries and owned cleanup."""
import copy
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts import training_media_backup_runtime as runtime

KEY = "synthetic-purpose-key-" + "k" * 40
CID = "c" * 64
GATEWAY_IMAGE = "sha256:" + "a" * 64
PROVISIONER_IMAGE = "sha256:" + "b" * 64


class FakeDocker:
    def __init__(self, media, directory, *, damage=None, exit_code=0, waiting=False):
        self.media, self.directory = media, directory
        self.damage, self.exit_code, self.waiting = damage, exit_code, waiting
        self.calls, self.config, self.name = [], None, None
        self.started = False

    def own(self):
        mounts = [{"Type": "bind", "Source": row["Source"], "Destination": row["Target"], "RW": not row["ReadOnly"]}
                  for row in self.config["HostConfig"]["Mounts"]]
        value = {"Id": CID, "Name": "/" + self.name, "Image": PROVISIONER_IMAGE, "Config": copy.deepcopy(self.config),
                 "HostConfig": copy.deepcopy(self.config["HostConfig"]), "Mounts": mounts,
                 "State": {"Running": self.started, "ExitCode": self.exit_code, "OOMKilled": False}}
        if self.damage == "extra_db_mount":
            value["Mounts"].append({"Type": "bind", "Source": "/live-database", "Destination": "/app/db", "RW": False})
        elif self.damage == "socket_bind":
            value["HostConfig"]["Binds"] = ["/var/run/docker.sock:/var/run/docker.sock"]
        elif self.damage == "wrong_owner":
            value["Config"]["Labels"]["mh.training.backup.owner"] = "foreign-owner"
        elif self.damage == "network":
            value["HostConfig"]["NetworkMode"] = "host"
        elif self.damage == "key_in_config":
            value["Config"]["Env"].append('WB_SAAS_BACKUP_ENCRYPTION_KEY='+KEY)
        elif self.damage == "gateway_image":
            value["Image"] = GATEWAY_IMAGE
        return value

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, copy.deepcopy(body)))
        if path == "/containers/wb-saas-gateway-1/json":
            return {"Image": GATEWAY_IMAGE, "State": {"Running": True}, "Mounts": [
                {"Type": "bind", "Source": str(self.media), "Destination": "/app/training-media", "RW": False}]}
        if path == "/containers/wb-saas-provisioner-1/json":
            return {"Image": PROVISIONER_IMAGE, "State": {"Running": True}, "Config": {"Env": ["WB_SAAS_BACKUP_ENCRYPTION_KEY=" + KEY,
                "WB_SAAS_BACKUP_ENCRYPTION_KEY_ID=test-id", "WB_SAAS_SMTP_PASSWORD=never-forward-this"]}}
        if method == "POST" and path.startswith("/containers/create?name="):
            self.name = path.split("=", 1)[1]; self.config = body
            return {"Id": CID}
        if path == "/containers/" + CID + "/json":
            return self.own()
        if method == "POST" and path == "/containers/" + CID + "/start":
            self.started = True
            return None
        if method == "DELETE" and path == "/containers/" + CID + "?force=true&v=true":
            return None
        raise AssertionError((method, path))


def setup(tmp_path, monkeypatch, **options):
    source = tmp_path / "committed-release"; source.mkdir()
    for relative in runtime.PUBLIC_FILES:
        path = source / relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b"public committed code")
    media = tmp_path / "media"; media.mkdir()
    directory = tmp_path / "backups" / "training-media"; directory.mkdir(parents=True)
    fake = FakeDocker(media, directory, **options)
    fake.exec_calls=[]
    monkeypatch.setattr(runtime, "api", fake)
    monkeypatch.setattr(runtime, "prepare_directory", lambda root: directory)
    def execute(command,**kwargs):
        fake.exec_calls.append((command,kwargs))
        if fake.waiting:raise runtime.subprocess.TimeoutExpired(command,240,stderr=KEY.encode())
        return SimpleNamespace(returncode=fake.exit_code,stderr=KEY.encode(),stdout=json.dumps({
            "ok":True,"verified_after_write":True,"lessons":16,"files":32,
            "archive":"training-media-aaaaaaaaaaaa-20261015T021500Z-abcdef.tar.gz.enc","must_not_relay":KEY}).encode())
    monkeypatch.setattr(runtime.subprocess,'run',execute)
    return source, directory, fake


def test_purpose_key_goes_only_to_stdin_not_daemon_config_args_logs_or_files(tmp_path, monkeypatch, capsys):
    source, directory, fake = setup(tmp_path, monkeypatch)
    (source / ".env").write_text("must not be mounted")
    (source / "gateway" / "data").mkdir(); (source / "gateway" / "data" / "live.sqlite3").write_bytes(b"must not be mounted")
    result = runtime.run({"WB_SAAS_BACKUP_DIR": str(directory.parent)}, source, 14)
    assert result["ok"] and result["files"] == 32
    config = fake.config
    assert config['Image'] == PROVISIONER_IMAGE and config['Image'] != GATEWAY_IMAGE
    assert not any(v.partition('=')[0] in runtime.PURPOSE_KEYS for v in config["Env"])
    assert not any("SMTP_PASSWORD" in value for value in config["Env"])
    assert KEY not in config["Cmd"][0] and KEY not in str(config["Entrypoint"])
    assert KEY not in str(result) and KEY not in capsys.readouterr().out
    command,kwargs=fake.exec_calls[0]
    assert command[:6]==['docker','--host','unix:///var/run/docker.sock','exec','-i',CID]
    assert KEY not in str(command) and kwargs['timeout']==240
    assert json.loads(kwargs['input'])['WB_SAAS_BACKUP_ENCRYPTION_KEY']==KEY
    assert not set(kwargs['env']) & {'DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH'}
    assert config['Cmd']==['import time;time.sleep(300)']
    mounts = config["HostConfig"]["Mounts"]
    assert len(mounts) == 7 and sum(not row["ReadOnly"] for row in mounts) == 1
    assert all(".env" not in row["Source"] and "live.sqlite3" not in row["Source"] for row in mounts)
    assert fake.calls[-1][:2] == ("DELETE", "/containers/" + CID + "?force=true&v=true")
    assert not list(directory.glob(".training-daily-*.json"))


@pytest.mark.parametrize("damage", ["extra_db_mount", "socket_bind", "network","key_in_config", "gateway_image"])
def test_isolation_failure_blocks_start_but_cleans_exact_owned_container(tmp_path, monkeypatch, damage):
    source, directory, fake = setup(tmp_path, monkeypatch, damage=damage)
    with pytest.raises(RuntimeError, match="training_media_backup_failed"):
        runtime.run({"WB_SAAS_BACKUP_DIR": str(directory.parent)}, source)
    assert not fake.started
    assert not fake.exec_calls
    assert any(method == "DELETE" and CID in path for method, path, _ in fake.calls)


def test_foreign_owner_is_never_started_or_deleted(tmp_path, monkeypatch):
    source, directory, fake = setup(tmp_path, monkeypatch, damage="wrong_owner")
    with pytest.raises(RuntimeError):runtime.run({"WB_SAAS_BACKUP_DIR": str(directory.parent)}, source)
    assert not fake.started and not any(method == "DELETE" for method, _, _ in fake.calls)


def test_failed_job_removes_own_container_without_relaying_private_stdout_stderr(tmp_path, monkeypatch,capsys):
    source, directory, fake = setup(tmp_path, monkeypatch, exit_code=1)
    with pytest.raises(RuntimeError, match="training_media_backup_failed"):
        runtime.run({"WB_SAAS_BACKUP_DIR": str(directory.parent)}, source)
    assert fake.started and any(method == "DELETE" for method, _, _ in fake.calls)
    assert not list(directory.glob(".training-daily-*.json"))
    assert KEY not in capsys.readouterr().out


def test_runtime_timeout_stops_exact_own_container_and_preserves_archives(tmp_path, monkeypatch):
    source, directory, fake = setup(tmp_path, monkeypatch, waiting=True)
    archive = directory / "unknown.tar.gz.enc"; archive.write_bytes(b"preserve")
    with pytest.raises(RuntimeError):runtime.run({"WB_SAAS_BACKUP_DIR": str(directory.parent)}, source)
    assert fake.calls[-1][:2] == ("DELETE", "/containers/" + CID + "?force=true&v=true")
    assert archive.read_bytes() == b"preserve" and not list(directory.glob(".training-daily-*.json"))


def test_missing_purpose_key_rejects_before_creating_any_container(tmp_path, monkeypatch):
    source, directory, fake = setup(tmp_path, monkeypatch)
    original = fake.__call__
    def api(method, path, body=None):
        if "provisioner-1/json" in path:return {"Image": PROVISIONER_IMAGE, "State": {"Running": True}, "Config": {"Env": ["SMTP_PASSWORD=not-a-backup-key"]}}
        return original(method, path, body)
    monkeypatch.setattr(runtime, "api", api)
    with pytest.raises(RuntimeError):runtime.run({"WB_SAAS_BACKUP_DIR": str(directory.parent)}, source)
    assert fake.config is None


def test_key_transport_size_is_bounded_before_exec(tmp_path,monkeypatch):
    monkeypatch.setattr(runtime.subprocess,'run',lambda *a,**kw:pytest.fail('oversized stdin must not run'))
    with pytest.raises(RuntimeError):runtime.execute_job(CID,{'WB_SAAS_BACKUP_ENCRYPTION_KEY':'x'*(runtime.MAX_KEY_TRANSPORT+1)})


@pytest.mark.parametrize('image', ['mh-provisioner:latest', '', 'sha256:' + 'g' * 64])
def test_unpinned_provisioner_image_rejected_before_container_and_key_handoff(tmp_path, monkeypatch, image):
    source, directory, fake = setup(tmp_path, monkeypatch)
    original = fake.__call__
    def api(method, path, body=None):
        result = original(method, path, body)
        if path == '/containers/wb-saas-provisioner-1/json':
            result['Image'] = image
        return result
    monkeypatch.setattr(runtime, 'api', api)
    with pytest.raises(RuntimeError, match='training_media_backup_failed'):
        runtime.run({'WB_SAAS_BACKUP_DIR': str(directory.parent)}, source)
    assert fake.config is None and not fake.exec_calls
