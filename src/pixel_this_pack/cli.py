"""Command line entry point. With no command it opens the GUI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import version_string
from .core import builder, sources
from .core.model import PACK_TYPES, RATINGS, ImageEntry, Pack, load_pack, make_image_id, unique_id


def _cmd_gui(args: argparse.Namespace) -> int:
    try:
        from .ui.app import run
    except ImportError as exc:  # tkinter missing
        print(f"Cannot start the GUI: {exc}\n"
              "Install Tk (Debian/Ubuntu: sudo apt install python3-tk) or use a headless command.",
              file=sys.stderr)
        return 1
    return run(Path(args.pack) if args.pack else None)


def _cmd_new(args: argparse.Namespace) -> int:
    pack_dir = Path(args.pack)
    if (pack_dir / "pack.json").exists():
        print(f"{pack_dir / 'pack.json'} already exists", file=sys.stderr)
        return 1
    pack = Pack(title=args.title or pack_dir.name, type=args.type, rating=args.rating)
    pack.author.name = args.author or ""
    builder.save_pack(pack, pack_dir)
    print(f"Created {pack_dir / 'pack.json'} (guid {pack.guid})")
    return 0


def _cmd_add(args: argparse.Namespace) -> int:
    pack_dir = Path(args.pack)
    pack = load_pack(pack_dir)
    taken = pack.image_ids()
    added = 0
    for root in args.art:
        for art in sources.scan(Path(root)):
            if not art.complete:
                print(f"skip {art.folder}: missing {', '.join(art.missing)}")
                continue
            image_id = unique_id(make_image_id(art.name), taken)
            taken.add(image_id)
            pack.images.append(ImageEntry(id=image_id, title=art.name, source=art.folder))
            added += 1
    builder.save_pack(pack, pack_dir)
    print(f"Added {added} image(s); pack now has {len(pack.images)}")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    pack_dir = Path(args.pack)
    issues = builder.validate(load_pack(pack_dir), pack_dir)
    for issue in issues:
        print(issue)
    errors = sum(1 for i in issues if i.level == builder.ERROR)
    print(f"{errors} error(s), {len(issues) - errors} warning(s)")
    return 1 if errors else 0


def _cmd_export(args: argparse.Namespace) -> int:
    pack_dir = Path(args.pack)
    try:
        result = builder.export_zip(load_pack(pack_dir), pack_dir, Path(args.out), args.url or "")
    except builder.BuildError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"Wrote {result.zip_path} ({result.size_bytes} bytes, sha256 {result.sha256})")
    print("Catalog entry:")
    print(json.dumps(result.catalog_entry, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pixel-this-pack", description="Build Pixel Me This content packs.")
    parser.add_argument("--version", action="version", version=version_string())
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("gui", help="open the editor (default)")
    p.add_argument("pack", nargs="?", help="pack folder to open")
    p.set_defaults(func=_cmd_gui)

    p = sub.add_parser("new", help="create an empty pack folder")
    p.add_argument("pack")
    p.add_argument("--title")
    p.add_argument("--author")
    p.add_argument("--type", choices=PACK_TYPES, default="dlc")
    p.add_argument("--rating", choices=RATINGS, default="everyone")
    p.set_defaults(func=_cmd_new)

    p = sub.add_parser("add", help="add every Pixel This Art image found under the given folders")
    p.add_argument("pack")
    p.add_argument("art", nargs="+")
    p.set_defaults(func=_cmd_add)

    p = sub.add_parser("validate", help="check a pack for problems")
    p.add_argument("pack")
    p.set_defaults(func=_cmd_validate)

    p = sub.add_parser("export", help="zip a pack for the store and print its catalog entry")
    p.add_argument("pack")
    p.add_argument("--out", default="dist")
    p.add_argument("--url", help="download URL to put in the catalog entry")
    p.set_defaults(func=_cmd_export)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command is None:
        args.pack = None
        return _cmd_gui(args)
    return args.func(args)
