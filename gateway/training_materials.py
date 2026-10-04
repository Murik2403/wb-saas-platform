"""Approved public lessons. Large immutable media live on a read-only mount."""
from __future__ import annotations
import json
import os
import re
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

MEDIA_ROOT = Path(os.environ.get("WB_SAAS_TRAINING_MEDIA_DIR", "/app/training-media"))
CATALOG_PATH = Path(__file__).with_name("training_catalog.json")

def catalog():
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))

def _media_entry(slug, entry, extension):
    digest = entry["sha256"]
    filename = entry["filename"]
    if not re.fullmatch(r"[0-9a-f]{64}", digest) or filename != f"{slug}.{digest}.{extension}":
        raise ValueError("Invalid approved asset name")
    path = (MEDIA_ROOT / filename).resolve()
    path.relative_to(MEDIA_ROOT.resolve())
    if type(entry["bytes"]) is not int or entry["bytes"] <= 0 or not path.is_file() or path.stat().st_size != entry["bytes"]:
        raise ValueError("Missing approved asset")
    return {**entry, "url": f"/training/media/{filename}", "path": path}

def available_media():
    """Fail closed on unapproved, incomplete or inconsistent release manifests."""
    try:
        manifest = json.loads((MEDIA_ROOT / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("version") != 1 or manifest.get("approved") is not True:
            return {}
        allowed = {item["slug"] for item in catalog()} | {"overview"}
        videos = manifest["videos"]
        if not isinstance(videos, dict) or not set(videos) <= allowed:
            return {}
        result = {}
        for slug, entry in videos.items():
            result[slug] = _media_entry(slug, entry, "mp4")
            if "captions" in entry:
                result[slug]["captions"] = _media_entry(slug, entry["captions"], "vtt")
        return result
    except (OSError, ValueError, KeyError, TypeError):
        return {}

def training_context():
    media = available_media()
    lessons = []
    for item in catalog():
        seconds = round(item["duration"])
        lessons.append({**item, "duration_label": f"{seconds // 60}:{seconds % 60:02d}", "media": media.get(item["slug"])})
    return {"training_lessons": lessons, "training_overview": media.get("overview")}

def create_training_media_router():
    router = APIRouter()
    @router.get("/training/media/{filename}")
    def media_file(filename: str):
        items = list(available_media().values())
        items += [item["captions"] for item in items if "captions" in item]
        entry = next((item for item in items if item["filename"] == filename), None)
        if entry is None:
            raise HTTPException(status_code=404, detail="Учебное видео не найдено")
        return FileResponse(entry["path"], media_type="text/vtt" if filename.endswith(".vtt") else "video/mp4", headers={"Cache-Control": "public, max-age=31536000, immutable", "ETag": f'"{entry["sha256"]}"', "X-Content-Type-Options": "nosniff"})
    return router
