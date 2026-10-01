"""User-defined product groups (a filter for the Products page).

Local SQLite only. Writes are limited to owner/manager by the global role
middleware in api_server (viewer and shop-floor roles are denied there).
"""
import sqlite3
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from db import core

router = APIRouter(prefix="/api/v1/product-groups", tags=["product-groups"])
MAX_GROUPS = 200
MAX_MEMBERS = 2000


class GroupCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    # Non-positive ids are rejected with 422; ids not in the catalog are kept
    # (the catalog lives in the dashboard cache, the UI only offers real SKUs).
    nm_ids: list[StrictInt] = Field(default_factory=list, max_length=MAX_MEMBERS)


class GroupUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    nm_ids: list[StrictInt] | None = Field(default=None, max_length=MAX_MEMBERS)
    archived: StrictBool | None = None


def _init(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS product_group (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, name_key TEXT NOT NULL,
        created_at TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0)""")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_product_group_active_name ON product_group(name_key) WHERE archived = 0")
    conn.execute("""CREATE TABLE IF NOT EXISTS product_group_item (
        group_id INTEGER NOT NULL, nm_id INTEGER NOT NULL, PRIMARY KEY (group_id, nm_id))""")


def _name(value: str) -> str:
    name = " ".join(value.split())
    if not 1 <= len(name) <= 60:
        raise HTTPException(422, detail="Название группы: от 1 до 60 символов")
    return name


def _ids(values: list[int]) -> list[int]:
    if any(v <= 0 for v in values):
        raise HTTPException(422, detail="Артикулы WB должны быть положительными числами")
    return sorted(set(values))


def _view(conn, rows) -> list[dict]:
    members: dict[int, list[int]] = {}
    for gid, nm in conn.execute("SELECT group_id, nm_id FROM product_group_item ORDER BY nm_id"):
        members.setdefault(gid, []).append(nm)
    return [{"id": r["id"], "name": r["name"], "archived": bool(r["archived"]), "created_at": r["created_at"],
             "nm_ids": members.get(r["id"], []), "count": len(members.get(r["id"], []))} for r in rows]


def _one(conn, gid: int) -> dict:
    rows = conn.execute("SELECT * FROM product_group WHERE id = ?", (gid,)).fetchall()
    if not rows:
        raise HTTPException(404, detail="Группа не найдена")
    return _view(conn, rows)[0]


def _conflict() -> HTTPException:
    return HTTPException(409, detail="Группа с таким названием уже есть")


@router.get("")
def list_groups(include_archived: bool = False) -> dict:
    with core.connect() as conn:
        _init(conn)
        rows = conn.execute("SELECT * FROM product_group WHERE archived = 0 OR ? ORDER BY name_key, id", (int(include_archived),)).fetchall()
        return {"groups": _view(conn, rows)}


@router.post("", status_code=201)
def create_group(payload: GroupCreate) -> dict:
    name, ids = _name(payload.name), _ids(payload.nm_ids)
    with core.connect() as conn:
        _init(conn)
        if conn.execute("SELECT COUNT(*) FROM product_group WHERE archived = 0").fetchone()[0] >= MAX_GROUPS:
            raise HTTPException(422, detail=f"Не больше {MAX_GROUPS} групп")
        try:
            with conn:
                gid = conn.execute("INSERT INTO product_group (name, name_key, created_at, archived) VALUES (?, ?, ?, 0)",
                                   (name, name.casefold(), datetime.now(timezone.utc).isoformat(timespec="seconds"))).lastrowid
                conn.executemany("INSERT INTO product_group_item (group_id, nm_id) VALUES (?, ?)", [(gid, n) for n in ids])
        except sqlite3.IntegrityError:
            raise _conflict()
        return _one(conn, gid)


@router.put("/{group_id}")
def update_group(group_id: int, payload: GroupUpdate) -> dict:
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(422, detail="Нечего менять")
    with core.connect() as conn:
        _init(conn)
        _one(conn, group_id)
        try:
            with conn:
                if changes.get("name") is not None:
                    name = _name(changes["name"])
                    conn.execute("UPDATE product_group SET name = ?, name_key = ? WHERE id = ?", (name, name.casefold(), group_id))
                if changes.get("nm_ids") is not None:
                    ids = _ids(changes["nm_ids"])
                    conn.execute("DELETE FROM product_group_item WHERE group_id = ?", (group_id,))
                    conn.executemany("INSERT INTO product_group_item (group_id, nm_id) VALUES (?, ?)", [(group_id, n) for n in ids])
                if changes.get("archived") is not None:
                    conn.execute("UPDATE product_group SET archived = ? WHERE id = ?", (int(changes["archived"]), group_id))
        except sqlite3.IntegrityError:
            raise _conflict()
        return _one(conn, group_id)


@router.delete("/{group_id}")
def archive_group(group_id: int) -> dict:
    """Soft delete: the group disappears from the list, members are kept."""
    with core.connect() as conn:
        _init(conn)
        _one(conn, group_id)
        with conn:
            conn.execute("UPDATE product_group SET archived = 1 WHERE id = ?", (group_id,))
        return _one(conn, group_id)
