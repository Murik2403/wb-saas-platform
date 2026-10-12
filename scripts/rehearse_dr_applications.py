"""Restore authenticated offsite copies and start isolated application candidates.

Operator-only, not a production restore or a new-host/key-loss drill. Requires
owner-local escrow, existing audited recovery helpers, SSH, and Docker on the
server. Private data and keys are never returned in diagnostics. The server
candidate runs with network=none, no ports, no live mounts, and no background
entrypoint. The private RSA/volume-backup keys never leave the owner's machine.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import datetime, timezone

GROUPS = ("control-plane", "demo", "mvvorokov")
ROOT_RE = re.compile(r"\.dr-app-rehearsal-[0-9a-f]{32}")


def validate_groups(groups):
    if not isinstance(groups, list) or not groups or len(groups) != len(set(groups)) or not set(groups) <= set(GROUPS):
        raise ValueError("Invalid restored application group set")
    return groups


def checked_root(root: Path, parent: Path) -> Path:
    if root.is_symlink():
        raise ValueError("Invalid private rehearsal boundary")
    root = root.resolve()
    if root.parent != parent.resolve() or not ROOT_RE.fullmatch(root.name) or root.is_symlink():
        raise ValueError("Invalid private rehearsal boundary")
    return root


def validate_members(members):
    """Preflight the complete archive before extracting even the first member."""
    seen = set()
    if len(members) > 50000 or sum(m.size for m in members) > 16 * 1024**3:
        raise ValueError("Archive exceeds rehearsal limits")
    for member in members:
        path = PurePosixPath(member.name)
        normalized = str(path).casefold()
        if (path.is_absolute() or ".." in path.parts or "\\" in member.name
                or ":" in member.name or not (member.isdir() or member.isfile())
                or normalized in seen):
            raise ValueError("Unsafe restored archive")
        seen.add(normalized)


def unpack_private(archive: Path, root: Path):
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        validate_members(members)
        root.mkdir(mode=0o700)
        for member in members:
            target = root.joinpath(*PurePosixPath(member.name).parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True, mode=0o700)
            else:
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                incoming = source.extractfile(member)
                with target.open("xb") as out:
                    shutil.copyfileobj(incoming, out)
                if target.stat().st_size != member.size:
                    raise ValueError("Incomplete restored file")
                target.chmod(0o600)


def candidate_config(image: str, group: str, data: Path, auth: str, credentials: dict):
    env = {
        "PYTHONDONTWRITEBYTECODE": "1", "WB_SAAS_PARENT_DOMAIN": "rehearsal.invalid",
        "WB_SAAS_DB_BACKEND": "sqlite", "WB_SAAS_TELEGRAM_ENABLED": "0",
        "WB_SAAS_SENTRY_DSN": "", "WB_SAAS_INTERNAL_API_URL": "",
        "WB_SAAS_INTERNAL_API_SECRET": auth, "MH_LOCAL_JOBS": "0",
        "WB_SAAS_SMTP_HOST": "", "WB_SAAS_SMTP_USER": "", "WB_SAAS_SMTP_PASSWORD": "",
        "WB_SAAS_TELEGRAM_BOT_TOKEN": "", "WB_SAAS_YOOKASSA_SHOP_ID": "",
        "WB_SAAS_YOOKASSA_SECRET_KEY": "", "WB_SAAS_DATABASE_URL": "",
    }
    if group != "control-plane":
        env.update(WB_SAAS_DATA_DIR="/app/data", WB_SAAS_TENANT_SLUG=group,
                   WB_SAAS_IS_DEMO="1" if group == "demo" else "0")
        for key in ("WB_SAAS_DATA_ENCRYPTION_KEY", "WB_SAAS_DATA_ENCRYPTION_KEY_ID", "WB_SAAS_DATA_ENCRYPTION_KEYRING"):
            if key in credentials:
                env[key] = credentials[key]
    binds = [str(data) + ":/app/data:rw"]
    if group == "control-plane":
        env.update(WB_SAAS_ERASURE_LEDGER_REQUIRED="1",
                   WB_SAAS_ERASURE_LEDGER_PATH="/app/erasure-ledger/erasures.sqlite3")
        binds.append(str(data.parent / "ledger") + ":/app/erasure-ledger:rw")
    return {
        "Image": image, "User": "10001:10001",
        "WorkingDir": "/app" if group == "control-plane" else "/app/tenant-app",
        "Entrypoint": ["uvicorn", "app:app" if group == "control-plane" else "api_server:app",
                       "--host", "127.0.0.1", "--port", "8000"], "Cmd": [],
        "Env": [f"{key}={value}" for key, value in env.items()],
        "Labels": {"traefik.enable": "false", "mh.dr.rehearsal": "true"},
        "HostConfig": {
            "NetworkMode": "none", "PortBindings": {}, "RestartPolicy": {"Name": "no"},
            "Binds": binds, "Memory": 512 * 1024**2,
            "NanoCpus": 1000000000, "PidsLimit": 128,
            "LogConfig": {"Type": "none", "Config": {}},
        },
    }


def capture(args, **kwargs):
    result = subprocess.run(args, capture_output=True, **kwargs)
    if result.returncode:
        diagnostic = result.stderr.decode(errors="replace") if isinstance(result.stderr, bytes) else result.stderr
        matches = re.findall(r"DR_APPLICATION_REHEARSAL_FAILED [A-Za-z]+ (dr-[a-z-]+)", diagnostic or "")
        raise RuntimeError(matches[-1] if matches else "Private operator subprocess failed")
    return result.stdout


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DockerConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.connect("/var/run/docker.sock")


def docker(method, path, body=None):
    conn = DockerConnection("docker", timeout=90)
    conn.request(method, "/v1.45" + path, json.dumps(body) if body is not None else None,
                 {"Content-Type": "application/json"})
    response = conn.getresponse()
    raw = response.read()
    conn.close()
    if response.status >= 400:
        raise RuntimeError("Private Docker operation failed")
    return json.loads(raw) if raw else None


def table_signature(path: Path):
    """All restored rows hashed privately using original columns, no row output."""
    result = {}
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("Restored database integrity failed")
        for (table,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
            quote = lambda value: '"' + value.replace('"', '""') + '"'
            columns = [row[1] for row in connection.execute("PRAGMA table_info(" + quote(table) + ")")]
            if table == "suppliers":
                # core.init_db v4.0 re-upserts legacy supplier references on
                # every startup, touching ONLY this metadata timestamp.
                columns = [column for column in columns if column != "updated_at"]
            rows = connection.execute("SELECT " + ",".join(map(quote, columns)) + " FROM " + quote(table)).fetchall()
            hashed = sorted(hashlib.sha256(repr(row).encode()).hexdigest() for row in rows)
            result[table] = {"columns": columns, "rows": hashed}
            if table == "sqlite_sequence":
                # Keep engine counter identities for private diagnostics only.
                # Startup supplier upserts advance counters without changing
                # business rows, so CHECK_CODE excludes this internal table.
                result[table]["row_keys"] = [row[0] for row in rows]
    finally:
        connection.close()
    return result


CHECK_CODE = r'''
import hashlib,json,os,sqlite3,sys,urllib.request
from urllib.error import HTTPError
request=json.load(sys.stdin)
opener=urllib.request.build_opener(type('NoRedirect',(urllib.request.HTTPRedirectHandler,),{'redirect_request':lambda self,*a:None})())
def get(path,signed=True):
 headers={}
 if request['group']!='control-plane' and signed:
  from gateway_request_auth import sign_context
  import config
  headers={'X-Tenant-Role':'owner','X-Tenant-Account':'dr-rehearsal'}
  headers['X-MH-Gateway-Auth']=sign_context(config.INTERNAL_API_SECRET,config.TENANT_SLUG,'GET',path,'owner','dr-rehearsal')
 try:
  response=opener.open(urllib.request.Request('http://127.0.0.1:8000'+path,headers=headers),timeout=35)
  body=response.read()
  return response.status,body
 except HTTPError as e:return e.code,e.read()
paths=['/healthz','/','/login','/register','/privacy','/consent','/training'] if request['group']=='control-plane' else ['/healthz','/api/v1/session','/api/v1/settings/tax?year=2026','/api/v1/dashboard/today?period=7d']
statuses={}
for path in paths:
 status,body=get(path);assert status==200,'dr-http-status';statuses[path]=status
 if request['group']!='control-plane':assert isinstance(json.loads(body),(dict,list))
if request['group']=='control-plane':
 assert get('/admin')[0] in (303,401,403)
elif request['group']!='demo':assert get('/api/v1/session',False)[0]==403
db='/app/data/gateway.sqlite3' if request['group']=='control-plane' else '/app/data/wb_dashboard.sqlite3'
c=sqlite3.connect('file:'+db+'?mode=ro',uri=True);assert c.execute('PRAGMA integrity_check').fetchall()==[('ok',)]
def quote(value):return '"'+value.replace('"','""')+'"'
for table,expected in request['signatures'].items():
 if table=='sqlite_sequence':continue
 rows=c.execute('SELECT '+','.join(map(quote,expected['columns']))+' FROM '+quote(table)).fetchall()
 actual=sorted(hashlib.sha256(repr(row).encode()).hexdigest() for row in rows)
 assert actual==expected['rows'],'dr-row-table-'+table.replace('_','-')
c.close()
if request['group']!='control-plane':
 import config
 from secure_storage import read
 for p in (config.TOKEN_FALLBACK_PATH,config.TOKEN_FALLBACK_PATH_OZON):
  if p.exists() and p.read_bytes().startswith(b'enc:v1:'):
   value,legacy=read(p,config.DATA_ENCRYPTION_KEY);assert value and not legacy
result={'http_statuses':statuses,'existing_tables_preserved':len(request['signatures'])-int('sqlite_sequence' in request['signatures']),'sqlite_autoincrement_bookkeeping_may_change':True,'supplier_updated_at_may_change':True,'integrity':'ok','auth_guard_checked':True}
if request['group']=='control-plane':result['training_video_tags']=get('/training')[1].count(b'<video')
print(json.dumps(result))
'''


def server_rehearsal(root: Path, request: dict):
    root = checked_root(root, Path("/opt/wb-saas"))
    validate_groups(request.get("groups"))
    originals = {}
    proof = []
    stage = "source-runtime"
    try:
        stage = "ledger-extract"
        ledger = root / "ledger"
        unpack_private(root / "ledger.tar.gz", ledger)
        for path in [ledger, *ledger.rglob("*")]:
            os.chown(path, 10001, 10001)
        for group in request['groups']:
            stage = "source-runtime"
            name = "wb-saas-gateway-1" if group == "control-plane" else "wb-tenant-" + group
            original = docker("GET", "/containers/" + name + "/json")
            if not original["State"]["Running"]:
                raise ValueError("Source runtime not running")
            originals[name] = (original["Id"], original["Image"])
            data = root / group
            stage = "private-extract"
            unpack_private(root / (group + ".tar.gz"), data)
            for path in [data, *data.rglob("*")]:
                os.chown(path, 10001, 10001)
            database = data / ("gateway.sqlite3" if group == "control-plane" else "wb_dashboard.sqlite3")
            stage = "database-signature"
            signatures = table_signature(database)
            candidate = "mh-dr-app-" + root.name[-32:] + "-" + group
            config = candidate_config(original["Image"], group, data, secrets.token_urlsafe(32),
                                      request.get("tenant_keys", {}).get(group, {}))
            created = False
            started = time.monotonic()
            try:
                stage = "candidate-create"
                docker("POST", "/containers/create?name=" + candidate, config)
                created = True
                stage = "candidate-start"
                docker("POST", "/containers/" + candidate + "/start")
                stage = "candidate-isolation"
                live = docker("GET", "/containers/" + candidate + "/json")
                assert live["HostConfig"]["NetworkMode"] == "none"
                assert not live["HostConfig"].get("PortBindings")
                assert all(Path(m["Source"]).resolve().is_relative_to(root) for m in live["Mounts"] if m["Type"] == "bind")
                payload = {"group": group, "signatures": signatures}
                check = None
                stage = "candidate-check"
                for attempt in range(30):
                    command = subprocess.run(["docker", "exec", "-i", candidate, "python", "-c", CHECK_CODE],
                                             input=json.dumps(payload), capture_output=True, text=True, timeout=75)
                    if command.returncode == 0:
                        check = json.loads(command.stdout)
                        break
                    # Once HTTP is ready a content mismatch will not improve by
                    # repeatedly issuing business reads; stop at the first one.
                    if "AssertionError: dr-row-table-" in command.stderr:
                        break
                    if not docker("GET", "/containers/" + candidate + "/json")["State"]["Running"]:
                        break
                    time.sleep(1)
                if check is None:
                    matches = re.findall(r"AssertionError: (dr-[a-z-]+)", command.stderr)
                    raise RuntimeError(matches[-1] if matches else "dr-candidate-acceptance")
                proof.append({"group": group, "image": original["Image"], "network": "none",
                              "published_ports": False, "live_mounts": False, "background_entrypoint": False,
                              "seconds": round(time.monotonic() - started, 3), **check})
            finally:
                if created:
                    docker("DELETE", "/containers/" + candidate + "?force=true&v=true")
                assert all(x["Names"] != ["/" + candidate] for x in docker("GET", "/containers/json?all=true"))
        for name, identity in originals.items():
            final = docker("GET", "/containers/" + name + "/json")
            assert (final["Id"], final["Image"]) == identity and final["State"]["Running"]
        return {"applications": proof, "live_containers_unchanged": True, "candidate_containers_removed": True}
    except Exception as error:
        safe = str(error) if re.fullmatch(r"dr-[a-z-]+", str(error)) else "dr-" + stage
        raise RuntimeError(safe) from None
    finally:
        checked_root(root, Path("/opt/wb-saas"))
        shutil.rmtree(root)


def cleanup_server(root: Path):
    """Idempotent failed-run cleanup, limited to UUID names and own labels."""
    root = checked_root(root, Path("/opt/wb-saas"))
    owned = {"mh-dr-app-" + root.name[-32:] + "-" + group for group in GROUPS}
    for container in docker("GET", "/containers/json?all=true"):
        if any(name.removeprefix("/") in owned for name in container["Names"]):
            inspected = docker("GET", "/containers/" + container["Id"] + "/json")
            if (inspected["Config"]["Labels"].get("mh.dr.rehearsal") != "true"
                    or inspected["HostConfig"]["NetworkMode"] != "none"):
                raise ValueError("Cleanup refused foreign container")
            docker("DELETE", "/containers/" + container["Id"] + "?force=true&v=true")
    if root.exists():
        shutil.rmtree(root)


def owner_rehearsal(args):
    validate_groups(args.groups)
    escrow = args.escrow.resolve()
    recovery = json.loads((escrow / "original-backup-recovery.json").read_text())
    helpers = load_module("dr_recovery_helpers", args.recovery_tools / "verify_offsite_set.py")
    crypto = load_module("dr_crypto", args.repo / "provisioner/backup_crypto.py")
    envelope = load_module("dr_envelope", args.repo / "scripts/backup_offsite_envelope.py")
    folder = Path(tempfile.mkdtemp(prefix="dr-app-private-", dir=escrow)).resolve()
    remote = "/opt/wb-saas/.dr-app-rehearsal-" + secrets.token_hex(16)
    remote_created = False
    selected = []
    started = time.monotonic()
    proof = {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "full_host_recovery": False,
             "synthetic_internal_auth": True, "provider_credentials_restored": False,
             "training_media_restored": False, "independent_erasure_ledger_restored": True,
             "private_rsa_uploaded": False, "volume_key_uploaded": False, "source_values_disclosed": False}
    proof["operator_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    proof["selected_groups"] = args.groups
    try:
        capture(["ssh", args.ssh, "mkdir", "-m", "700", remote], timeout=30)
        remote_created = True
        capture(["scp", str(Path(__file__).resolve()), args.ssh + ":" + remote + "/runner.py"], timeout=30)
        ledger_proof = restore_ledger(args, folder, escrow, remote)
        proof["erasure_ledger"] = ledger_proof
        for group in args.groups:
            inventory = json.loads(capture(["ssh", args.ssh, "rclone", "lsjson", "--files-only", "--recursive",
                                            "timeweb-s3:marketshelper-backups/offsite-v2/" + group], timeout=120))
            item = max((x for x in inventory if x["Path"].endswith(".tar.gz.enc")), key=lambda x: x["ModTime"])
            relative = "offsite-v2/" + group + "/" + item["Path"]
            if not re.fullmatch(r"[A-Za-z0-9_./-]+", relative):
                raise ValueError("Unsafe offsite selection")
            outer = folder / (group + ".outer.enc")
            with outer.open("xb") as out:
                status = subprocess.run(["ssh", args.ssh, "rclone", "cat", "timeweb-s3:marketshelper-backups/" + relative,
                                         "--retries", "1", "--low-level-retries", "1"], stdout=out, stderr=subprocess.PIPE, timeout=600)
            if status.returncode or outer.stat().st_size != item["Size"]:
                raise RuntimeError("Offsite retrieval failed")
            inner, plain = folder / (group + ".inner.enc"), folder / (group + ".plain.tar.gz")
            metadata = envelope.decrypt(outer, inner, escrow / "recovery-private.pem")
            if metadata["source_sha256"] != helpers.digest(inner) or metadata["source_sha256"] not in relative:
                raise ValueError("Offsite digest mismatch")
            crypto.decrypt_file(inner, plain, recovery["volume_backup_key"])
            group_root = folder / group
            group_root.mkdir(mode=0o700)
            validation = helpers.restore_archive(plain, group_root / "data", recovery["tenant_data_keys"].get(group))
            transport = folder / (group + ".tar.gz")
            with tarfile.open(transport, "w:gz") as archive:
                for path in sorted((group_root / "data").rglob("*")):
                    archive.add(path, arcname=str(path.relative_to(group_root / "data")).replace("\\", "/"), recursive=False)
            capture(["scp", str(transport), args.ssh + ":" + remote + "/" + group + ".tar.gz"], timeout=300)
            selected.append({"group": group, "object": relative, "modified_utc": item["ModTime"],
                             "inner_sha256": metadata["source_sha256"], "authenticated": True,
                             "databases_authenticated": validation["database_count"]})
        request = {"groups": args.groups, "tenant_keys": {g: recovery["tenant_data_keys"][g] for g in args.groups if g != "control-plane"}}
        result = capture(["ssh", args.ssh, "python3", remote + "/runner.py", "--server", remote],
                         input=json.dumps(request).encode(), timeout=900)
        proof.update(json.loads(result))
        # Server success requires cleanup; verify it again independently.
        capture(["ssh", args.ssh, "test", "!", "-e", remote], timeout=30)
        remote_created = False
        proof.update(selected_objects=selected, remote_plaintext_removed=True, elapsed_seconds=round(time.monotonic() - started, 3))
    finally:
        if remote_created:
            # Only remove the exact checked UUID directory. Never computed broad paths.
            checked_root(Path(remote), Path("/opt/wb-saas")) if os.name != "nt" else None
            # The runner deletes its directory on a handled failure. It remains
            # available for cleanup if SSH timed out while a candidate ran.
            cleanup = "import pathlib,runpy; p=pathlib.Path(" + repr(remote) + "); script=p/'runner.py'; runpy.run_path(str(script))['cleanup_server'](p) if script.is_file() else None"
            capture(["ssh", args.ssh, "python3", "-c", "'" + cleanup.replace("'", "'\"'\"'") + "'"], timeout=60)
        assert folder.parent == escrow and folder.name.startswith("dr-app-private-") and not folder.is_symlink()
        shutil.rmtree(folder)
        assert not folder.exists()
    proof["owner_plaintext_removed"] = True
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
    print("DR_APPLICATION_REHEARSAL_PASSED", len(proof["applications"]))


def restore_ledger(args, folder: Path, escrow: Path, remote: str):
    """Authenticate every immutable ledger snapshot and compare to live authority.

    The copy is independent of the older gateway archive. Matching the canonical
    union to the current read-only ledger is essential: merely choosing the last
    dated encrypted object does not establish current deletion-intent coverage.
    """
    sys.path.insert(0, str(args.repo.resolve()))
    backup = load_module("dr_ledger_backup", args.repo / "scripts/pdn_erasure_backup.py")
    listing = json.loads(capture(["ssh", args.ssh, "rclone", "lsjson", "--files-only", "--recursive",
                                  "timeweb-s3:marketshelper-backups/erasure-ledger-v1"], timeout=120))
    manifests = [x for x in listing if x["Path"].endswith(".ledger.enc.json")]
    if not manifests or len(manifests) > 1000:
        raise ValueError("Invalid independent ledger snapshot set")
    private = folder / "ledger-envelopes"
    private.mkdir(mode=0o700)
    for item in listing:
        if not re.fullmatch(r"[0-9a-f]{64}-[0-9a-f]{64}\.ledger\.enc(?:\.json)?", item["Path"]):
            raise ValueError("Unexpected independent ledger object")
        target = private / item["Path"]
        with target.open("xb") as out:
            result = subprocess.run(["ssh", args.ssh, "rclone", "cat",
                                     "timeweb-s3:marketshelper-backups/erasure-ledger-v1/" + item["Path"]],
                                    stdout=out, stderr=subprocess.PIPE, timeout=120)
        target.chmod(0o600)
        if result.returncode or target.stat().st_size != item["Size"]:
            raise RuntimeError("Independent ledger retrieval failed")
    records = backup.recover_union([private / x["Path"] for x in manifests], escrow / "recovery-private.pem")
    canonical_sha = hashlib.sha256(backup._encode(records)).hexdigest()
    # Only a canonical digest crosses back from the read-only live ledger, no
    # identities, deletion timestamps or records are serialized in diagnostics.
    code = """import hashlib,json,sqlite3
s=sqlite3.connect('file:/opt/wb-saas/erasure-ledger/erasures.sqlite3?mode=ro',uri=True);d=sqlite3.connect(':memory:');s.backup(d);s.close()
assert d.execute('PRAGMA integrity_check').fetchall()==[('ok',)]
assert d.execute('SELECT version FROM metadata').fetchall()==[(1,)]
rows=sorted([list(x) for x in d.execute('SELECT identity,requested_at,finalization_requested_at FROM erasures')]);d.close()
raw=json.dumps({'version':1,'erasures':rows},sort_keys=True,separators=(',',':')).encode();print(hashlib.sha256(raw).hexdigest())
"""
    authoritative = capture(["ssh", args.ssh, "python3", "-"], input=code.encode(), timeout=30).decode().strip()
    if authoritative != canonical_sha:
        raise ValueError("Independent ledger does not cover current authority")
    recovered = folder / "ledger-data"
    recovered.mkdir(mode=0o700)
    database = recovered / "erasures.sqlite3"
    conn = sqlite3.connect(database)
    try:
        conn.executescript("CREATE TABLE metadata(version INTEGER NOT NULL CHECK(version=1)); INSERT INTO metadata VALUES(1); CREATE TABLE erasures(identity TEXT PRIMARY KEY CHECK(length(identity)=64),requested_at TEXT NOT NULL,finalization_requested_at TEXT NOT NULL DEFAULT '');")
        conn.executemany("INSERT INTO erasures VALUES(?,?,?)", records)
        conn.commit()
    finally:
        conn.close()
    database.chmod(0o600)
    if hashlib.sha256(backup.snapshot(database)).hexdigest() != canonical_sha:
        raise ValueError("Independent ledger reconstruction changed content")
    transport = folder / "ledger.tar.gz"
    with tarfile.open(transport, "w:gz") as archive:
        archive.add(database, arcname="erasures.sqlite3")
    capture(["scp", str(transport), args.ssh + ":" + remote + "/ledger.tar.gz"], timeout=30)
    return {"snapshots_authenticated": len(manifests), "canonical_sha256": canonical_sha,
            "matches_current_live_authority": True, "required_on_candidate_startup": True,
            "live_ledger_replaced": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", type=Path)
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--recovery-tools", type=Path)
    parser.add_argument("--escrow", type=Path)
    parser.add_argument("--ssh", default="marketshelper-staging")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--groups", nargs="+", choices=GROUPS, default=list(GROUPS))
    args = parser.parse_args(argv)
    try:
        if args.server:
            print(json.dumps(server_rehearsal(args.server, json.load(sys.stdin))))
        else:
            if not all((args.repo, args.recovery_tools, args.escrow, args.output)):
                parser.error("Owner mode requires repo, recovery-tools, escrow and output")
            owner_rehearsal(args)
    except Exception as error:
        # Exception messages and private subprocess diagnostics can contain values.
        safe_stage = str(error) if re.fullmatch(r"dr-[a-z-]+", str(error)) else ""
        print("DR_APPLICATION_REHEARSAL_FAILED", type(error).__name__, safe_stage, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
