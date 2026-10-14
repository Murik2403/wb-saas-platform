"""Copy-only encrypted backup and verified restore of the approved training media (16 lessons, MP4 + VTT).

The source directory is the one the gateway mounts read-only at /app/training-media: ``manifest.json`` plus
``<slug>.<sha256>.mp4|vtt``. Nothing is rebuilt, re-encoded or modified; the source is only read.

  verify-source   check the source against its manifest with the gateway's own rules and the 16-lesson catalog
  pack            verify, then write ONE new encrypted archive (existing backup_crypto format, WBSAASBK2); never
                  overwrites or deletes anything; skips when an archive of the same manifest already verifies
  verify-archive  decrypt, check every member's SHA-256 against the inventory and the manifest it carries
  restore         decrypt + verify, then extract into an EMPTY directory and re-verify it as a gateway media root
  acceptance      the whole rehearsal in an isolated work directory (see docs/TRAINING_MEDIA_BACKUP_*.md)

The key comes from WB_SAAS_BACKUP_ENCRYPTION_KEY (and ..._KEY_ID / ..._KEYRING for rotation) like every other backup tool;
it is never printed. Examples:

    python scripts/training_media_backup.py pack --source /opt/wb-saas/public-media/course-... --backup-dir /opt/wb-saas/backups/training-media
    python scripts/training_media_backup.py restore --archive <file.tar.gz.enc> --dest /srv/restored-media
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "provisioner"))
from backup_crypto import DEFAULT_KEY_ID, decrypt_file, decryption_options_from_environment, encrypt_file, validate_key_id  # noqa: E402

INVENTORY_NAME = "BACKUP-INVENTORY.json"
MANIFEST_NAME = "manifest.json"
ARCHIVE_SUFFIX = ".tar.gz.enc"  # the suffix the existing retention and offsite tools already recognise
_DIGEST = re.compile(r"[0-9a-f]{64}")
CHUNK = 1024 * 1024


class MediaError(Exception):
    """One or more problems; ``problems`` lists them all (nothing is hidden behind the first)."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems[:5]) + (f" (+{len(problems) - 5} more)" if len(problems) > 5 else ""))
        self.problems = problems


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def catalog_slugs() -> list[str]:
    return [item["slug"] for item in json.loads((ROOT / "gateway" / "training_catalog.json").read_text(encoding="utf-8"))]


def _entry_problems(slug: str, entry: dict, extension: str, root: Path) -> tuple[list[str], dict | None]:
    problems: list[str] = []
    digest, filename = entry.get("sha256"), entry.get("filename")
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest) or filename != f"{slug}.{digest}.{extension}":
        return [f"{slug}.{extension}: invalid approved asset name"], None
    path = root / filename
    if type(entry.get("bytes")) is not int or entry["bytes"] <= 0:
        return [f"{filename}: invalid size in manifest"], None
    if not path.is_file() or path.is_symlink():
        return [f"{filename}: missing"], None
    if path.stat().st_size != entry["bytes"]:
        problems.append(f"{filename}: size {path.stat().st_size} differs from manifest {entry['bytes']}")
    actual = sha256_of(path)
    if actual != digest:
        problems.append(f"{filename}: SHA-256 differs from manifest")
    return problems, {"name": filename, "bytes": entry["bytes"], "sha256": digest, "slug": slug, "kind": extension}


def verify_source(root: Path, *, expect_all_lessons: bool = True) -> dict:
    """Apply the gateway's own acceptance rules plus full SHA-256 verification; return the inventory."""
    root = Path(root)
    problems: list[str] = []
    try:
        manifest = json.loads((root / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise MediaError([f"{MANIFEST_NAME}: missing or unreadable"]) from None
    if manifest.get("version") != 1 or manifest.get("approved") is not True or not isinstance(manifest.get("videos"), dict):
        raise MediaError(["manifest is not an approved version 1 release"])
    videos = manifest["videos"]
    lessons = catalog_slugs()
    if expect_all_lessons:
        missing = [slug for slug in lessons if slug not in videos]
        problems += [f"{slug}: lesson absent from the manifest" for slug in missing]
    unknown = [slug for slug in videos if slug not in set(lessons) | {"overview"}]
    problems += [f"{slug}: not an approved lesson slug" for slug in unknown]
    files: list[dict] = []
    for slug, entry in videos.items():
        found, item = _entry_problems(slug, entry, "mp4", root)
        problems += found
        files += [item] if item else []
        if expect_all_lessons and "captions" not in entry:
            problems.append(f"{slug}: subtitles (VTT) absent")
        if "captions" in entry:
            found, item = _entry_problems(slug, entry["captions"], "vtt", root)
            problems += found
            files += [item] if item else []
    listed = {item["name"] for item in files}
    extra = sorted(name for name in os.listdir(root) if name != MANIFEST_NAME and name not in listed)
    if problems:
        raise MediaError(problems)
    return {"manifest_sha256": sha256_of(root / MANIFEST_NAME), "lessons": len([s for s in videos if s != "overview"]),
            "files": sorted(files, key=lambda item: item["name"]), "unlisted_files_in_source": extra,
            "bytes": sum(item["bytes"] for item in files)}


def _options(key_id: str | None) -> dict:
    options = decryption_options_from_environment()
    if key_id:
        options["key_id"] = validate_key_id(key_id)
    return options


def _safe_member(member: tarfile.TarInfo) -> None:
    path = Path(member.name)
    if not member.isreg() or path.is_absolute() or ".." in path.parts or len(path.parts) != 1:
        raise MediaError([f"unsafe archive member: {member.name!r}"])


def read_archive(archive: Path, key: str, workdir: Path, *, key_id: str | None = None) -> tuple[dict, Path]:
    """Decrypt into ``workdir``, verify every member, return (inventory, plain tar path)."""
    plain = workdir / f"verify-{secrets.token_hex(4)}.tar.gz"
    try:
        decrypt_file(Path(archive), plain, key, **_options(key_id))
    except Exception:
        raise MediaError(["archive does not decrypt or authenticate (wrong key or modified file)"]) from None
    problems: list[str] = []
    with tarfile.open(plain, "r:gz") as tar:
        members = {m.name: m for m in tar.getmembers()}
        for member in members.values():
            _safe_member(member)
        if INVENTORY_NAME not in members or MANIFEST_NAME not in members:
            raise MediaError(["archive carries no inventory or manifest"])
        inventory = json.loads(tar.extractfile(members[INVENTORY_NAME]).read())
        declared = {item["name"]: item for item in inventory["files"]}
        if set(members) != set(declared) | {INVENTORY_NAME, MANIFEST_NAME}:
            problems.append("archive members differ from its inventory")
        for name, item in declared.items():
            if name not in members:
                continue
            digest, size = hashlib.sha256(), 0
            handle = tar.extractfile(members[name])
            while chunk := handle.read(CHUNK):
                digest.update(chunk)
                size += len(chunk)
            if digest.hexdigest() != item["sha256"] or size != item["bytes"]:
                problems.append(f"{name}: content differs from the inventory")
        manifest_bytes = tar.extractfile(members[MANIFEST_NAME]).read()
        if hashlib.sha256(manifest_bytes).hexdigest() != inventory["manifest_sha256"]:
            problems.append("manifest.json differs from the inventory")
    if problems:
        plain.unlink(missing_ok=True)
        raise MediaError(problems)
    return inventory, plain


def pack(source: Path, backup_dir: Path, key: str, *, key_id: str = DEFAULT_KEY_ID, force: bool = False) -> dict:
    source, backup_dir = Path(source), Path(backup_dir)
    before = {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in source.iterdir()}
    inventory = verify_source(source)
    backup_dir.mkdir(parents=True, exist_ok=True)
    stem12 = inventory["manifest_sha256"][:12]
    if not force:
        for existing in sorted(backup_dir.glob(f"training-media-{stem12}-*{ARCHIVE_SUFFIX}")):
            try:
                with tempfile.TemporaryDirectory(dir=backup_dir) as scratch:
                    current, plain = read_archive(existing, key, Path(scratch), key_id=key_id)
                if current["manifest_sha256"] == inventory["manifest_sha256"]:
                    return {"result": "already_backed_up", "archive": existing.name, "manifest_sha256": inventory["manifest_sha256"]}
            except MediaError:
                continue  # an unreadable archive is left alone (never deleted); a fresh one is written next to it
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"training-media-{stem12}-{stamp}-{secrets.token_hex(3)}{ARCHIVE_SUFFIX}"
    destination = backup_dir / name
    if destination.exists():
        raise MediaError([f"{name} already exists; nothing was written"])
    partial = backup_dir / f".{name[:-len(ARCHIVE_SUFFIX)]}.tar.gz.partial"
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as raw, tarfile.open(fileobj=raw, mode="w:gz", compresslevel=1, format=tarfile.GNU_FORMAT) as tar:
            payload = json.dumps({**inventory, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                  "tool": "training_media_backup.py", "format": 1}, ensure_ascii=False, indent=2).encode("utf-8")
            info = tarfile.TarInfo(INVENTORY_NAME)
            info.size, info.mtime, info.mode = len(payload), int(time.time()), 0o644
            tar.addfile(info, io.BytesIO(payload))
            for member in (MANIFEST_NAME, *[item["name"] for item in inventory["files"]]):
                tar.add(source / member, arcname=member, filter=lambda i: (setattr(i, "uid", 0), setattr(i, "gid", 0), setattr(i, "uname", ""), setattr(i, "gname", ""), i)[-1])
        encrypt_file(partial, destination, key, key_id=key_id)
    finally:
        partial.unlink(missing_ok=True)  # our own plaintext partial only
    try:
        with tempfile.TemporaryDirectory(dir=backup_dir) as scratch:
            checked, _ = read_archive(destination, key, Path(scratch), key_id=key_id)
    except MediaError:
        destination.unlink(missing_ok=True)  # only the archive this very call wrote
        raise
    after = {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in source.iterdir()}
    if before != after:
        raise MediaError(["the source directory changed while it was being copied"])
    return {"result": "created", "archive": name, "archive_bytes": destination.stat().st_size, "manifest_sha256": inventory["manifest_sha256"],
            "files": len(inventory["files"]), "lessons": inventory["lessons"], "verified_after_write": checked["manifest_sha256"] == inventory["manifest_sha256"]}


def restore(archive: Path, dest: Path, key: str, *, key_id: str | None = None, read_only: bool = False) -> dict:
    dest = Path(dest)
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise MediaError([f"{dest} exists and is not empty: restore never overwrites"])
    with tempfile.TemporaryDirectory() as scratch:
        inventory, plain = read_archive(archive, key, Path(scratch), key_id=key_id)
        dest.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        try:
            with tarfile.open(plain, "r:gz") as tar:
                for member in tar.getmembers():
                    if member.name == INVENTORY_NAME:
                        continue
                    target = dest / member.name
                    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
                    written.append(target)
                    with os.fdopen(descriptor, "wb") as outgoing:
                        shutil.copyfileobj(tar.extractfile(member), outgoing, CHUNK)
            again = verify_source(dest)
            if again["files"] != inventory["files"] or again["manifest_sha256"] != inventory["manifest_sha256"]:
                raise MediaError(["restored directory differs from the archive inventory"])
        except BaseException:
            for path in written:  # only the files this call created in an empty directory
                path.unlink(missing_ok=True)
            raise
    if read_only:
        for path in written:
            path.chmod(0o444)
    return {"result": "restored", "dest": str(dest), "files": len(inventory["files"]), "lessons": inventory["lessons"],
            "bytes": inventory["bytes"], "manifest_sha256": inventory["manifest_sha256"]}


# ---- isolated gateway rehearsal -------------------------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _get(url: str, headers: dict | None = None) -> tuple[int, dict, bytes]:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def check_isolated_gateway(media_root: Path, inventory: dict, work: Path) -> dict:
    """Run a COPY of the real gateway app on localhost against ``media_root``; verify page, files, subtitles, Range."""
    gateway_copy = work / "gateway"
    shutil.copytree(ROOT / "gateway", gateway_copy, ignore=shutil.ignore_patterns("data", "tests", "__pycache__", ".venv"))
    port = _free_port()
    env = {**os.environ, "WB_SAAS_TRAINING_MEDIA_DIR": str(media_root)}
    log = (work / "gateway.log").open("wb")
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port)], cwd=gateway_copy,
                              env=env, stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    problems: list[str] = []
    counts = {"files_full_get": 0, "range_first_byte": 0, "range_middle": 0, "range_suffix": 0, "range_unsatisfiable_416": 0, "vtt_webvtt": 0}
    try:
        for _ in range(60):
            try:
                if _get(base + "/training")[0] == 200:
                    break
            except OSError:
                pass
            time.sleep(0.5)
        else:
            raise MediaError(["isolated gateway did not start"])
        status, _, page = _get(base + "/training")
        html = page.decode("utf-8")
        videos, sources, tracks = html.count("<video"), html.count("<source "), html.count("<track ")
        if videos != 16 or sources != 16 or tracks != 16:
            problems.append(f"/training shows {videos} videos, {sources} sources, {tracks} subtitle tracks (expected 16 each)")
        for item in inventory["files"]:
            url = f"{base}/training/media/{item['name']}"
            if f"/training/media/{item['name']}" not in html:
                problems.append(f"{item['name']}: not linked from /training")
            status, headers, body = _get(url)
            headers = {k.lower(): v for k, v in headers.items()}
            expected_type = "text/vtt" if item["kind"] == "vtt" else "video/mp4"
            if status != 200 or hashlib.sha256(body).hexdigest() != item["sha256"] or len(body) != item["bytes"]:
                problems.append(f"{item['name']}: full GET differs from the manifest")
                continue
            counts["files_full_get"] += 1
            if not headers.get("content-type", "").startswith(expected_type) or "immutable" not in headers.get("cache-control", "") or headers.get("x-content-type-options") != "nosniff":
                problems.append(f"{item['name']}: wrong content type or cache/nosniff headers")
            if item["kind"] == "vtt":
                counts["vtt_webvtt"] += body.startswith(b"WEBVTT")
                if not body.startswith(b"WEBVTT"):
                    problems.append(f"{item['name']}: subtitles do not start with WEBVTT")
            size = item["bytes"]
            mid = size // 2
            for label, header, start, end in (("range_first_byte", "bytes=0-0", 0, 0), ("range_middle", f"bytes={mid}-{mid + 4095}", mid, min(size - 1, mid + 4095)),
                                              ("range_suffix", "bytes=-100", max(0, size - 100), size - 1)):
                code, response_headers, part = _get(url, {"Range": header})
                response_headers = {k.lower(): v for k, v in response_headers.items()}
                if code != 206 or part != body[start:end + 1] or response_headers.get("content-range") != f"bytes {start}-{end}/{size}":
                    problems.append(f"{item['name']}: {header} answered {code}, not the exact slice")
                else:
                    counts[label] += 1
            code, _, _ = _get(url, {"Range": f"bytes={size}-"})
            if code != 416:
                problems.append(f"{item['name']}: a Range past the end answered {code}, expected 416")
            else:
                counts["range_unsatisfiable_416"] += 1
        for bad in ("/training/media/unknown.mp4", "/training/media/..%2fmanifest.json", "/training/media/%2e%2e/manifest.json", "/training/media/manifest.json"):
            if _get(base + bad)[0] != 404:
                problems.append(f"{bad}: not a 404")
    finally:
        server.terminate()
        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server.kill()
        log.close()
    if problems:
        raise MediaError(problems)
    return {"port_used": port, "training_page": {"videos": videos, "sources": sources, "subtitle_tracks": tracks}, **counts}


def _tree_state(root: Path) -> dict:
    return {p.name: (p.stat().st_size, p.stat().st_mtime_ns, sha256_of(p)) for p in sorted(root.iterdir())}


def acceptance(source: Path, work: Path, out: Path, key: str | None = None) -> dict:
    source, work = Path(source), Path(work)
    if work.exists() and any(work.iterdir()):
        raise MediaError([f"{work} is not empty"])
    work.mkdir(parents=True, exist_ok=True)
    ephemeral = key is None
    key = key or secrets.token_urlsafe(48)
    act: dict = {"started": datetime.now(timezone.utc).isoformat(timespec="seconds"), "source": str(source), "ephemeral_key_used": ephemeral, "steps": {}}
    started = time.time()
    state_before = _tree_state(source)
    inventory = verify_source(source)
    act["steps"]["1_verify_source"] = {"lessons": inventory["lessons"], "files": len(inventory["files"]), "bytes": inventory["bytes"],
                                       "manifest_sha256": inventory["manifest_sha256"], "unlisted_files_in_source": inventory["unlisted_files_in_source"]}
    backups = work / "backups"
    backups.mkdir()
    decoys = {}
    for name in ("training-media-000000000000-20200101T000000Z-old.tar.gz.enc", "unrelated.txt", ".keep"):
        (backups / name).write_bytes(b"pre-existing, must stay")
        decoys[name] = sha256_of(backups / name)
    act["steps"]["2_pack"] = pack(source, backups, key)
    again = pack(source, backups, key)
    act["steps"]["2b_pack_again_same_manifest"] = {"result": again["result"]}
    archive = backups / act["steps"]["2_pack"]["archive"]
    act["steps"]["2_pack"]["encryption_magic"] = archive.read_bytes()[:9].decode("ascii", "replace")
    with tempfile.TemporaryDirectory(dir=work) as scratch:
        checked, _ = read_archive(archive, key, Path(scratch))
    act["steps"]["3_verify_archive"] = {"files": len(checked["files"]), "manifest_sha256": checked["manifest_sha256"]}
    # negative checks on copies: a modified archive and a wrong key must not verify; a non-empty restore target is refused
    tampered = work / "tampered.tar.gz.enc"
    raw = bytearray(archive.read_bytes())
    raw[len(raw) // 2] ^= 0xFF
    tampered.write_bytes(bytes(raw))
    negatives = {}
    for label, candidate, candidate_key in (("modified_archive", tampered, key), ("wrong_key", archive, secrets.token_urlsafe(48))):
        try:
            with tempfile.TemporaryDirectory(dir=work) as scratch:
                read_archive(candidate, candidate_key, Path(scratch))
            negatives[label] = "ACCEPTED (bad)"
        except MediaError:
            negatives[label] = "rejected"
    occupied = work / "occupied"
    occupied.mkdir()
    (occupied / "x").write_bytes(b"x")
    try:
        restore(archive, occupied, key)
        negatives["restore_into_non_empty_directory"] = "ACCEPTED (bad)"
    except MediaError:
        negatives["restore_into_non_empty_directory"] = "refused"
    act["steps"]["4_negative_checks"] = negatives
    restored = work / "restored-media"
    act["steps"]["5_restore"] = restore(archive, restored, key, read_only=True)
    act["steps"]["5_restore"]["byte_identical_to_source"] = all(sha256_of(restored / item["name"]) == item["sha256"] for item in inventory["files"]) \
        and sha256_of(restored / MANIFEST_NAME) == inventory["manifest_sha256"]
    act["steps"]["6_isolated_gateway"] = check_isolated_gateway(restored, inventory, work)
    act["steps"]["7_source_and_old_archives_untouched"] = {
        "source_unchanged": _tree_state(source) == state_before,
        "preexisting_files_unchanged": all(sha256_of(backups / name) == digest for name, digest in decoys.items()),
        "archives_in_backup_dir": sorted(p.name for p in backups.iterdir() if not p.name.startswith("."))}
    failed = [name for name, value in negatives.items() if "bad" in value] + ([] if act["steps"]["5_restore"]["byte_identical_to_source"] else ["restore not identical"]) \
        + [k for k, v in act["steps"]["7_source_and_old_archives_untouched"].items() if v is False]
    act["verdict"] = "PASS" if not failed else "FAIL"
    act["failed_checks"] = failed
    act["seconds"] = round(time.time() - started, 1)
    act["files"] = [{"name": item["name"], "bytes": item["bytes"], "sha256": item["sha256"]} for item in inventory["files"]]
    Path(out).write_text(json.dumps(act, ensure_ascii=False, indent=2), encoding="utf-8")
    return act


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("verify-source", "pack", "acceptance"):
        sub.add_parser(name).add_argument("--source", required=True)
    sub.choices["pack"].add_argument("--backup-dir", required=True)
    sub.choices["pack"].add_argument("--force", action="store_true", help="write a new archive even if one of this manifest already verifies")
    sub.choices["acceptance"].add_argument("--work", required=True)
    sub.choices["acceptance"].add_argument("--out", required=True)
    for name in ("verify-archive", "restore"):
        sub.add_parser(name).add_argument("--archive", required=True)
    sub.choices["restore"].add_argument("--dest", required=True)
    sub.choices["restore"].add_argument("--read-only", action="store_true")
    args = parser.parse_args(argv)
    key = os.environ.get("WB_SAAS_BACKUP_ENCRYPTION_KEY", "")
    key_id = os.environ.get("WB_SAAS_BACKUP_ENCRYPTION_KEY_ID") or DEFAULT_KEY_ID
    try:
        if args.command == "verify-source":
            inventory = verify_source(Path(args.source))
            result = {k: inventory[k] for k in ("lessons", "bytes", "manifest_sha256", "unlisted_files_in_source")} | {"files": len(inventory["files"])}
        elif args.command == "acceptance":
            result = acceptance(Path(args.source), Path(args.work), Path(args.out), key or None)
        else:
            if not key:
                raise MediaError(["WB_SAAS_BACKUP_ENCRYPTION_KEY is not set"])
            if args.command == "pack":
                result = pack(Path(args.source), Path(args.backup_dir), key, key_id=key_id, force=args.force)
            elif args.command == "verify-archive":
                with tempfile.TemporaryDirectory() as scratch:
                    inventory, _ = read_archive(Path(args.archive), key, Path(scratch))
                result = {"verified": True, "files": len(inventory["files"]), "manifest_sha256": inventory["manifest_sha256"]}
            else:
                result = restore(Path(args.archive), Path(args.dest), key, read_only=args.read_only)
    except MediaError as error:
        print(json.dumps({"ok": False, "problems": error.problems}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, **result} if "verdict" not in result else {"ok": result["verdict"] == "PASS", "verdict": result["verdict"],
                                                                            "failed_checks": result["failed_checks"], "seconds": result["seconds"]}, ensure_ascii=False, indent=2))
    return 0 if result.get("verdict", "PASS") == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
