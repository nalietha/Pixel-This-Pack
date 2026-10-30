"""Finding Pixel This Art output to pull into a pack.

The art tool writes one folder per image (output/<name>/) holding
pixel_art.png, mask_index.png, palette.json, thumbnail.png and settings.json.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .model import REQUIRED_IMAGE_FILES

_MAX_DEPTH = 4


@dataclass(frozen=True)
class ArtImage:
    folder: Path
    missing: tuple[str, ...]

    @property
    def name(self) -> str:
        return self.folder.name

    @property
    def complete(self) -> bool:
        return not self.missing


def inspect_folder(folder: Path) -> ArtImage | None:
    """An ArtImage if `folder` looks like art-tool output (has pixel_art.png), else None."""
    if not (folder / "pixel_art.png").is_file():
        return None
    missing = tuple(name for name in REQUIRED_IMAGE_FILES if not (folder / name).is_file())
    return ArtImage(folder=folder, missing=missing)


def scan(root: Path) -> list[ArtImage]:
    """Every art-tool image folder at or below `root`, sorted by path.

    Stops descending once a folder is an image, so story clue sub-folders are
    not picked up as separate images.
    """
    found: list[ArtImage] = []

    def walk(folder: Path, depth: int) -> None:
        image = inspect_folder(folder)
        if image is not None:
            found.append(image)
            return
        if depth >= _MAX_DEPTH:
            return
        try:
            children = sorted(p for p in folder.iterdir() if p.is_dir() and not p.name.startswith("."))
        except OSError:
            return
        for child in children:
            walk(child, depth + 1)

    walk(root, 0)
    return found
