"""pack.json as the game reads it.

Field names mirror Pixel-Me-This scripts/models (Pack, ImageInfo,
DailyImageInfo, Author, SocialLink, ScheduleInfo) and the launcher's
PackEntry. Anything in a pack.json this module does not know about is kept in
``extra`` and written back untouched, so hand-added fields such as ``packId``
survive a round trip.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PACK_TYPES = ("dlc", "daily", "story", "misc")
RATINGS = ("everyone", "mature")
FREQUENCIES = ("daily", "weekly")
REQUIRED_IMAGE_FILES = ("pixel_art.png", "mask_index.png", "palette.json", "thumbnail.png")
PACK_ART_FILES = ("cover.png", "banner.png", "board_bg.png", "map_bg.png", "gallery_bg.png")
MANIFEST_NAME = "pack.json"

_ID_UNSAFE = re.compile(r"[^A-Za-z0-9_-]+")


def new_guid() -> str:
    return str(uuid.uuid4())


def make_image_id(name: str) -> str:
    """Folder-safe image id from an arbitrary name ("Attic Wedding (2)" -> "attic_wedding_2")."""
    cleaned = _ID_UNSAFE.sub("_", name.strip()).strip("_").lower()
    return cleaned or "image"


def unique_id(base: str, taken: set[str]) -> str:
    if base not in taken:
        return base
    n = 2
    while f"{base}_{n}" in taken:
        n += 1
    return f"{base}_{n}"


def split_list(text: str) -> list[str]:
    """Comma separated UI text -> clean list, order kept, duplicates dropped."""
    seen: list[str] = []
    for part in text.split(","):
        item = part.strip()
        if item and item not in seen:
            seen.append(item)
    return seen


def _str(data: dict, key: str) -> str:
    value = data.get(key)
    return "" if value is None else str(value)


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v) for v in value if str(v).strip()]


def _rest(data: dict, known: tuple[str, ...]) -> dict:
    return {k: v for k, v in data.items() if k not in known}


@dataclass
class SocialLink:
    username: str = ""
    directlink: str = ""


@dataclass
class Author:
    name: str = ""
    onlinestore: str = ""
    website: str = ""
    links: dict[str, SocialLink] = field(default_factory=dict)
    extra: dict = field(default_factory=dict)

    _KEYS = ("name", "onlinestore", "website", "links")

    @classmethod
    def from_dict(cls, data: Any) -> Author:
        if isinstance(data, str):
            return cls(name=data)
        if not isinstance(data, dict):
            return cls()
        links = {}
        raw_links = data.get("links")
        if isinstance(raw_links, dict):
            for platform, link in raw_links.items():
                if isinstance(link, dict):
                    links[str(platform)] = SocialLink(_str(link, "username"), _str(link, "directlink"))
        return cls(
            name=_str(data, "name"),
            onlinestore=_str(data, "onlinestore"),
            website=_str(data, "website"),
            links=links,
            extra=_rest(data, cls._KEYS),
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "onlinestore": self.onlinestore,
            "website": self.website,
            "links": {
                platform: {"username": link.username, "directlink": link.directlink}
                for platform, link in self.links.items()
            },
            **self.extra,
        }


@dataclass
class Schedule:
    start_date: str = ""
    end_date: str = ""
    frequency: str = "daily"
    extra: dict = field(default_factory=dict)

    _KEYS = ("startDate", "endDate", "frequency")

    @classmethod
    def from_dict(cls, data: Any) -> Schedule | None:
        if not isinstance(data, dict):
            return None
        return cls(
            start_date=_str(data, "startDate"),
            end_date=_str(data, "endDate"),
            frequency=_str(data, "frequency") or "daily",
            extra=_rest(data, cls._KEYS),
        )

    def to_dict(self) -> dict:
        out: dict[str, Any] = {}
        if self.start_date:
            out["startDate"] = self.start_date
        if self.end_date:
            out["endDate"] = self.end_date
        out["frequency"] = self.frequency or "daily"
        out.update(self.extra)
        return out


@dataclass
class ImageEntry:
    id: str
    guid: str = field(default_factory=new_guid)
    title: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    alternative: str = ""
    extra: dict = field(default_factory=dict)

    # Not serialized. `source` is a Pixel This Art output folder waiting to be
    # copied in on save; `saved_id` is the id this image's folder has on disk,
    # so a rename can move the folder instead of orphaning it.
    source: Path | None = None
    saved_id: str | None = None

    _KEYS = ("id", "guid", "title", "description", "tags", "alternative", "unlockDay", "folder")

    @classmethod
    def from_dict(cls, data: dict) -> ImageEntry:
        image_id = _str(data, "id")
        return cls(
            id=image_id,
            guid=_str(data, "guid") or new_guid(),
            title=_str(data, "title"),
            description=_str(data, "description"),
            tags=_str_list(data.get("tags")),
            alternative=_str(data, "alternative"),
            extra=_rest(data, cls._KEYS),
            saved_id=image_id or None,
        )

    def to_dict(self, pack_type: str, position: int) -> dict:
        out: dict[str, Any] = {"id": self.id, "guid": self.guid}
        if pack_type == "daily":
            # Order in the list is the unlock order.
            out["folder"] = f"images/{self.id}"
            out["unlockDay"] = position + 1
        if self.title:
            out["title"] = self.title
        if self.description:
            out["description"] = self.description
        if self.tags:
            out["tags"] = list(self.tags)
        if self.alternative:
            out["alternative"] = self.alternative
        out.update(self.extra)
        return out


@dataclass
class Pack:
    title: str = ""
    guid: str = field(default_factory=new_guid)
    type: str = "dlc"
    tagline: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    triggers: list[str] = field(default_factory=list)
    author: Author = field(default_factory=Author)
    version: str = "1.0.0"
    rating: str = "everyone"
    schedule: Schedule | None = None
    images: list[ImageEntry] = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    _KEYS = ("title", "guid", "type", "tagline", "description", "tags", "triggers",
             "author", "version", "rating", "schedule", "images")

    @classmethod
    def from_dict(cls, data: dict) -> Pack:
        images = [ImageEntry.from_dict(i) for i in data.get("images") or [] if isinstance(i, dict)]
        return cls(
            title=_str(data, "title"),
            # Never invent a guid for an existing pack on load: saves are keyed
            # on it. A missing one is reported by validate() instead.
            guid=_str(data, "guid"),
            type=(_str(data, "type") or "dlc").lower(),
            tagline=_str(data, "tagline"),
            description=_str(data, "description"),
            tags=_str_list(data.get("tags")),
            triggers=_str_list(data.get("triggers")),
            author=Author.from_dict(data.get("author")),
            version=_str(data, "version") or "1.0.0",
            rating=(_str(data, "rating") or "everyone").lower(),
            schedule=Schedule.from_dict(data.get("schedule")),
            images=images,
            extra=_rest(data, cls._KEYS),
        )

    def to_dict(self) -> dict:
        out: dict[str, Any] = {
            "title": self.title,
            "guid": self.guid,
            "type": self.type,
            "tagline": self.tagline,
            "description": self.description,
            "tags": list(self.tags),
            "triggers": list(self.triggers),
            "author": self.author.to_dict(),
            "version": self.version,
            "rating": self.rating,
        }
        if self.schedule is not None or self.type == "daily":
            out["schedule"] = (self.schedule or Schedule()).to_dict()
        out.update(self.extra)
        out["images"] = [img.to_dict(self.type, i) for i, img in enumerate(self.images)]
        return out

    def image_ids(self) -> set[str]:
        return {img.id for img in self.images}

    def image_dir(self, pack_dir: Path, image_id: str) -> Path:
        """Where the game looks for an image's files (PackManager.ResolveImagePaths)."""
        base = pack_dir / "images" / image_id
        return base / "base" if self.type == "story" else base


def load_pack(pack_dir: Path) -> Pack:
    with open(pack_dir / MANIFEST_NAME, encoding="utf-8-sig") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{pack_dir / MANIFEST_NAME} is not a JSON object")
    return Pack.from_dict(data)


def write_manifest(pack: Pack, pack_dir: Path) -> Path:
    """Atomically write pack.json, keeping the previous one as pack.json.bak."""
    pack_dir.mkdir(parents=True, exist_ok=True)
    target = pack_dir / MANIFEST_NAME
    tmp = target.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(pack.to_dict(), fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    if target.exists():
        os.replace(target, target.with_suffix(".json.bak"))
    os.replace(tmp, target)
    return target
