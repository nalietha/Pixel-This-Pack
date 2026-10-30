"""Saving, validating and exporting a pack folder.

Nothing here deletes image folders. Removing an image from the list only
drops it from pack.json; its folder is reported as orphaned by validate() and
left for the author to delete by hand.
"""

from __future__ import annotations

import hashlib
import shutil
import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from .model import (
    PACK_TYPES,
    RATINGS,
    REQUIRED_IMAGE_FILES,
    Pack,
    write_manifest,
)

ERROR = "error"
WARNING = "warning"

# Never shipped in an export zip.
_EXPORT_SKIP_NAMES = {".pmt_pack.json", ".DS_Store", "Thumbs.db", "desktop.ini"}
_EXPORT_SKIP_SUFFIXES = {".bak", ".tmp"}


@dataclass(frozen=True)
class Issue:
    level: str
    message: str

    def __str__(self) -> str:
        return f"[{self.level}] {self.message}"


class BuildError(Exception):
    pass


# --- save --------------------------------------------------------------------


def save_pack(pack: Pack, pack_dir: Path) -> None:
    """Bring the folder in line with `pack`, then write pack.json.

    Order matters: folders are renamed and art copied first, so pack.json is
    only written once every image it names has somewhere to live.
    """
    pack_dir.mkdir(parents=True, exist_ok=True)
    _check_ids(pack)

    for image in pack.images:
        root = pack_dir / "images"
        if image.saved_id and image.saved_id != image.id:
            old, new = root / image.saved_id, root / image.id
            if old.is_dir():
                if new.exists():
                    raise BuildError(f"Cannot rename {image.saved_id!r} to {image.id!r}: images/{image.id} already exists")
                old.rename(new)

        if image.source is not None:
            dest = pack.image_dir(pack_dir, image.id)
            dest.mkdir(parents=True, exist_ok=True)
            for name in REQUIRED_IMAGE_FILES:
                src = image.source / name
                if src.is_file():
                    shutil.copy2(src, dest / name)

    write_manifest(pack, pack_dir)

    for image in pack.images:
        image.saved_id = image.id
        image.source = None


def _check_ids(pack: Pack) -> None:
    seen: set[str] = set()
    for image in pack.images:
        if not image.id:
            raise BuildError("An image has an empty id")
        if image.id in seen:
            raise BuildError(f"Image id {image.id!r} is used twice")
        seen.add(image.id)


# --- validate ----------------------------------------------------------------


def validate(pack: Pack, pack_dir: Path | None) -> list[Issue]:
    """Everything the game or launcher would trip over, worst first.

    Pass pack_dir=None to check only the manifest (e.g. before first save).
    """
    issues: list[Issue] = []
    err = lambda m: issues.append(Issue(ERROR, m))  # noqa: E731
    warn = lambda m: issues.append(Issue(WARNING, m))  # noqa: E731

    if not pack.title.strip():
        err("Pack has no title")
    if not pack.guid.strip():
        err("Pack has no GUID; the game cannot track saves for it")
    if pack.type not in PACK_TYPES:
        err(f"Unknown pack type {pack.type!r} (expected one of {', '.join(PACK_TYPES)})")
    if pack.rating not in RATINGS:
        err(f"Unknown rating {pack.rating!r} (expected one of {', '.join(RATINGS)})")
    if not pack.images:
        err("Pack has no images")
    if not pack.author.name.strip():
        warn("No author name")
    if not pack.tagline.strip():
        warn("No tagline (shown on the store card)")
    if pack.rating == "mature" and not pack.triggers:
        warn("Mature pack has no content warnings")

    if pack.type == "daily":
        start = pack.schedule.start_date if pack.schedule else ""
        if not start:
            err("Daily pack needs a schedule start date")
        else:
            try:
                date.fromisoformat(start)
            except ValueError:
                err(f"Schedule start date {start!r} is not YYYY-MM-DD")

    ids = [img.id for img in pack.images]
    guids = [img.guid for img in pack.images]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        err(f"Image id {dup!r} is used more than once")
    for dup in sorted({g for g in guids if g and guids.count(g) > 1}):
        err(f"Image GUID {dup} is used more than once")
    if pack.guid and pack.guid in guids:
        err("An image shares the pack's GUID")

    id_set = set(ids)
    for image in pack.images:
        if not image.id:
            err("An image has no id")
        if not image.guid:
            err(f"{image.id}: no GUID")
        if image.alternative and image.alternative not in id_set:
            err(f"{image.id}: alternative {image.alternative!r} is not an image in this pack")
        if image.alternative == image.id:
            err(f"{image.id}: is its own alternative")

    if pack_dir is None:
        return _sorted(issues)

    for image in pack.images:
        if image.source is not None:
            missing = [n for n in REQUIRED_IMAGE_FILES if not (image.source / n).is_file()]
            if missing:
                err(f"{image.id}: source {image.source} is missing {', '.join(missing)}")
            continue
        folder = pack.image_dir(pack_dir, image.saved_id or image.id)
        if not folder.is_dir():
            err(f"{image.id}: folder {folder.relative_to(pack_dir)} is missing")
            continue
        missing = [n for n in REQUIRED_IMAGE_FILES if not (folder / n).is_file()]
        if missing:
            err(f"{image.id}: missing {', '.join(missing)}")

    images_root = pack_dir / "images"
    if images_root.is_dir():
        known = {img.saved_id or img.id for img in pack.images}
        for child in sorted(images_root.iterdir()):
            if child.is_dir() and child.name not in known:
                warn(f"images/{child.name} is not in pack.json (orphaned; delete it or re-add it)")

    if not (pack_dir / "cover.png").is_file():
        warn("No cover.png (used on pack and store screens)")

    return _sorted(issues)


def _sorted(issues: list[Issue]) -> list[Issue]:
    return sorted(issues, key=lambda i: i.level != ERROR)


# --- export ------------------------------------------------------------------


@dataclass(frozen=True)
class ExportResult:
    zip_path: Path
    sha256: str
    size_bytes: int
    catalog_entry: dict


def export_zip(pack: Pack, pack_dir: Path, out_dir: Path, download_url: str = "") -> ExportResult:
    """Zip the saved pack for the store, plus a matching catalog.json entry.

    The zip holds one top-level folder named after the pack folder, which is
    the layout the launcher's find_pack_root expects. Refuses to export a pack
    that has validation errors or unsaved art.
    """
    if any(img.source is not None for img in pack.images):
        raise BuildError("Save the pack before exporting; some images have not been copied in yet")
    errors = [i for i in validate(pack, pack_dir) if i.level == ERROR]
    if errors:
        raise BuildError("Fix these first:\n" + "\n".join(f"  - {e.message}" for e in errors))

    out_dir.mkdir(parents=True, exist_ok=True)
    folder = pack_dir.name
    zip_path = out_dir / f"{_file_stem(folder)}-{pack.version}.zip"
    tmp = zip_path.with_suffix(".zip.tmp")

    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(pack_dir.rglob("*")):
            if not path.is_file() or _skip_in_export(path):
                continue
            zf.write(path, f"{folder}/{path.relative_to(pack_dir).as_posix()}")
    tmp.replace(zip_path)

    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    size = zip_path.stat().st_size
    entry = catalog_entry(pack, folder, download_url or zip_path.name, size, digest)
    return ExportResult(zip_path, digest, size, entry)


def catalog_entry(pack: Pack, install_folder: str, url: str, size: int, sha256: str) -> dict:
    """A packs[] entry for PMT_Store's catalog.json (launcher PackEntry.from_dict)."""
    return {
        "guid": pack.guid,
        "title": pack.title,
        "type": pack.type,
        "tagline": pack.tagline,
        "description": pack.description,
        "version": pack.version,
        "rating": pack.rating,
        "author": pack.author.name or "Unknown",
        "installFolder": install_folder,
        "targetRoot": "dlc",
        "price": {"model": "free"},
        "tags": list(pack.tags),
        "triggers": list(pack.triggers),
        "imageCount": len(pack.images),
        "releasedAt": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "download": {"url": url, "sizeBytes": size, "sha256": sha256, "format": "zip"},
    }


def _skip_in_export(path: Path) -> bool:
    return (
        path.name in _EXPORT_SKIP_NAMES
        or path.suffix in _EXPORT_SKIP_SUFFIXES
        or "__pycache__" in path.parts
    )


def _file_stem(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name).strip("_") or "pack"
