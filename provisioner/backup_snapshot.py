"""Consistent point-in-time archive of one tenant (or control-plane) data volume.

Runs inside a short-lived helper container (the tenant image: Python with sqlite3), never in the provisioner. Replaces
``tar czf -C /data .`` on a live volume, which copies a SQLite file and its -wal while the application writes and so
can produce an archive that does not open or mixes two moments in time.

- Every SQLite database (recognised by its header, not its name) is copied with SQLite's online backup API from a
  read-only connection. The copy is one transaction-consistent snapshot, the application keeps running, and the
  -wal/-shm/-journal side files of a snapshotted database are left out because the copy already contains their content.
- The copy is checked with ``PRAGMA quick_check`` before it is archived; a bad copy fails the backup instead of
  being stored as if it were good.
- Other files are read as a point-in-time copy: small files are read twice-checked (size and mtime must not change
  while they are read), large ones are streamed and the backup fails if one changed under the read.
- The archive layout (``./relative/path``), ownership, mode and mtime of every member match what ``tar`` wrote, so the
  encryption, restore and export code that consume it are unchanged.

Usage: backup_snapshot.py DATA_DIR OUTPUT_TAR_GZ STAGE_DIR   (STAGE_DIR must not exist; it is always removed).
Prints one JSON summary line on success and exits 0; any problem exits 1 with the reason on stderr.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sqlite3
import stat
import sys
import tarfile
from pathlib import Path

SQLITE_MAGIC = b"SQLite format 3\x00"
SIDE_SUFFIXES = ("-wal", "-shm", "-journal")
SMALL_FILE_LIMIT = 32 * 1024 * 1024
STABILITY_RETRIES = 3
FREE_SPACE_MARGIN = 256 * 1024 * 1024


class SnapshotError(Exception):
    pass


def is_sqlite(path: Path) -> bool:
    try:
        with open(path, "rb") as handle:
            return handle.read(16) == SQLITE_MAGIC
    except OSError:
        return False


def snapshot_database(source: Path, destination: Path) -> None:
    """Online-backup ``source`` into ``destination`` and verify the copy."""
    reader = sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True, timeout=60)
    try:
        writer = sqlite3.connect(destination)
        try:
            reader.backup(writer)  # one step: a single consistent snapshot, writers are not blocked in WAL mode
            verdict = writer.execute("PRAGMA quick_check").fetchall()
        finally:
            writer.close()
    finally:
        reader.close()
    if verdict != [("ok",)]:
        raise SnapshotError(f"SQLite copy of {source.name} failed its integrity check")


def _member(tar: tarfile.TarFile, path: Path, arcname: str, size: int | None = None) -> tarfile.TarInfo:
    info = tar.gettarinfo(str(path), arcname)  # uid, gid, mode and mtime of the original, like tar does
    if size is not None:
        info.size = size
    return info


def _add_stable_file(tar: tarfile.TarFile, path: Path, arcname: str) -> None:
    first = path.stat()
    if first.st_size > SMALL_FILE_LIMIT:
        with open(path, "rb") as handle:
            before = os.fstat(handle.fileno())
            tar.addfile(_member(tar, path, arcname, before.st_size), handle)
            after = os.fstat(handle.fileno())
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise SnapshotError(f"{arcname} changed while it was being archived")
        return
    for _ in range(STABILITY_RETRIES):
        with open(path, "rb") as handle:
            before = os.fstat(handle.fileno())
            data = handle.read()
            after = os.fstat(handle.fileno())
        if (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns) and len(data) == after.st_size:
            tar.addfile(_member(tar, path, arcname, len(data)), io.BytesIO(data))
            return
    raise SnapshotError(f"{arcname} kept changing while it was being archived")


def build(data_dir: str, output: str, stage_dir: str) -> dict[str, int]:
    root, stage = Path(data_dir), Path(stage_dir)
    files: list[Path] = []
    directories: list[Path] = []
    for current, names, entries in os.walk(root, followlinks=False):
        names.sort()
        directories.extend(Path(current, name) for name in names)
        files.extend(Path(current, name) for name in sorted(entries))
    databases = {path for path in files if path.is_file() and not path.is_symlink() and is_sqlite(path)}
    skipped_side = {path for path in files if path.name.endswith(SIDE_SUFFIXES)
                    and path.with_name(path.name.rsplit("-", 1)[0]) in databases}
    largest = max((path.stat().st_size for path in databases), default=0)
    stage.mkdir(mode=0o700)
    try:
        free = shutil.disk_usage(stage.parent).free
        if free < largest * 2 + FREE_SPACE_MARGIN:
            raise SnapshotError("Not enough free space to stage the largest database")
        summary = {"databases": 0, "files": 0, "directories": 0, "bytes": 0}
        with tarfile.open(output, "w:gz", compresslevel=6, format=tarfile.GNU_FORMAT) as tar:
            tar.add(str(root), arcname=".", recursive=False)
            for directory in sorted(directories):
                tar.add(str(directory), arcname="./" + directory.relative_to(root).as_posix(), recursive=False)
                summary["directories"] += 1
            for path in files:
                arcname = "./" + path.relative_to(root).as_posix()
                if path in skipped_side:
                    continue
                if path in databases:
                    copy = stage / "snapshot.sqlite3"
                    try:
                        snapshot_database(path, copy)
                        size = copy.stat().st_size
                        with open(copy, "rb") as handle:
                            tar.addfile(_member(tar, path, arcname, size), handle)
                    finally:
                        copy.unlink(missing_ok=True)  # peak staging is the largest database, not their sum
                    summary["databases"] += 1
                    summary["bytes"] += size
                elif path.is_symlink():
                    tar.add(str(path), arcname=arcname, recursive=False)
                    summary["files"] += 1
                elif path.is_file():
                    _add_stable_file(tar, path, arcname)
                    summary["files"] += 1
                    summary["bytes"] += path.stat().st_size
                # sockets, fifos and device nodes are not part of a data backup (tar would have stored them empty)
        return summary
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: backup_snapshot.py DATA_DIR OUTPUT STAGE_DIR", file=sys.stderr)
        return 2
    try:
        print(json.dumps(build(*argv)))
        return 0
    except Exception as exc:  # noqa: BLE001 -- the reason goes to the helper's log, the exit code to the provisioner
        print(f"snapshot failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
