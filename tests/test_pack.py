import json
import zipfile
from pathlib import Path

import pytest

from pixel_this_pack.cli import main
from pixel_this_pack.core import builder, sources
from pixel_this_pack.core.model import (
    REQUIRED_IMAGE_FILES,
    ImageEntry,
    Pack,
    Schedule,
    load_pack,
    make_image_id,
    split_list,
    unique_id,
)

REPO = Path(__file__).resolve().parents[2]


def make_art(root: Path, name: str, skip: tuple[str, ...] = ()) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    for f in REQUIRED_IMAGE_FILES + ("settings.json",):
        if f not in skip:
            (folder / f).write_bytes(b"x")
    return folder


def test_ids_and_lists():
    assert make_image_id("Attic Wedding (2)") == "attic_wedding_2"
    assert make_image_id("  ") == "image"
    assert unique_id("a", {"a", "a_2"}) == "a_3"
    assert split_list("cute, spooky ,, cute") == ["cute", "spooky"]


def test_round_trip_keeps_unknown_fields(tmp_path):
    raw = {
        "title": "Halloween", "packId": "halloween_daily_2025", "type": "daily",
        "guid": "g-1", "author": {"name": "Medusa", "links": {"twitter": {"username": "m", "directlink": "u"}}},
        "schedule": {"startDate": "2025-10-30", "frequency": "daily"},
        "images": [{"id": "b", "guid": "2", "unlockDay": 9, "folder": "images/b", "custom": 1},
                   {"id": "a", "guid": "1"}],
    }
    (tmp_path / "pack.json").write_text(json.dumps(raw))
    pack = load_pack(tmp_path)
    pack.images.reverse()
    out = pack.to_dict()
    assert out["packId"] == "halloween_daily_2025"
    assert out["author"]["links"]["twitter"]["username"] == "m"
    assert [(i["id"], i["unlockDay"]) for i in out["images"]] == [("a", 1), ("b", 2)]
    assert out["images"][1]["custom"] == 1
    assert out["images"][1]["folder"] == "images/b"


def test_non_daily_omits_daily_fields():
    pack = Pack(title="t", images=[ImageEntry(id="a")])
    img = pack.to_dict()["images"][0]
    assert "unlockDay" not in img and "folder" not in img
    assert "schedule" not in pack.to_dict()


def test_missing_guid_is_not_invented(tmp_path):
    (tmp_path / "pack.json").write_text(json.dumps({"title": "x", "images": []}))
    pack = load_pack(tmp_path)
    assert pack.guid == ""
    assert any("GUID" in i.message for i in builder.validate(pack, None))


def test_scan_finds_art_and_stops_at_image(tmp_path):
    make_art(tmp_path / "output", "Cat")
    make_art(tmp_path / "output", "Dog", skip=("thumbnail.png",))
    make_art(tmp_path / "output" / "Cat", "nested")  # should not be found separately
    found = sources.scan(tmp_path)
    assert [a.name for a in found] == ["Cat", "Dog"]
    assert found[1].missing == ("thumbnail.png",)


def test_save_copies_art_renames_and_never_deletes(tmp_path):
    art = make_art(tmp_path / "art", "Cat")
    pack_dir = tmp_path / "My Pack"
    pack = Pack(title="My Pack", images=[ImageEntry(id="cat", source=art)])
    builder.save_pack(pack, pack_dir)
    assert all((pack_dir / "images" / "cat" / f).is_file() for f in REQUIRED_IMAGE_FILES)
    assert not (pack_dir / "images" / "cat" / "settings.json").exists()

    pack.images[0].id = "kitty"
    builder.save_pack(pack, pack_dir)
    assert (pack_dir / "images" / "kitty" / "pixel_art.png").is_file()
    assert not (pack_dir / "images" / "cat").exists()
    assert (pack_dir / "pack.json.bak").is_file()

    pack.images.clear()
    builder.save_pack(pack, pack_dir)
    assert (pack_dir / "images" / "kitty").is_dir()
    assert any("orphaned" in i.message for i in builder.validate(pack, pack_dir))


def test_story_art_goes_in_base(tmp_path):
    art = make_art(tmp_path / "art", "Scene")
    pack = Pack(title="s", type="story", images=[ImageEntry(id="scene1", source=art)])
    builder.save_pack(pack, tmp_path / "p")
    assert (tmp_path / "p" / "images" / "scene1" / "base" / "mask_index.png").is_file()


def test_duplicate_ids_block_save(tmp_path):
    pack = Pack(title="t", images=[ImageEntry(id="a"), ImageEntry(id="a")])
    with pytest.raises(builder.BuildError):
        builder.save_pack(pack, tmp_path)


def test_validate_catches_common_mistakes(tmp_path):
    pack = Pack(title="", type="daily", rating="mature",
                images=[ImageEntry(id="a", alternative="zzz"), ImageEntry(id="b")])
    pack.images[1].guid = pack.images[0].guid
    messages = " | ".join(i.message for i in builder.validate(pack, tmp_path))
    for expected in ("no title", "start date", "used more than once", "alternative 'zzz'",
                     "content warnings", "folder images/a is missing"):
        assert expected in messages
    pack.schedule = Schedule(start_date="10/30/2025")
    assert "not YYYY-MM-DD" in " ".join(i.message for i in builder.validate(pack, None))


def test_export_zip_layout_and_catalog_entry(tmp_path):
    art = make_art(tmp_path / "art", "Cat")
    pack_dir = tmp_path / "Cat Pack"
    pack = Pack(title="Cat Pack", tagline="t", tags=["cats"], images=[ImageEntry(id="cat", source=art)])
    pack.author.name = "Me"
    with pytest.raises(builder.BuildError):
        builder.export_zip(pack, pack_dir, tmp_path / "dist")  # unsaved art

    builder.save_pack(pack, pack_dir)
    builder.save_pack(pack, pack_dir)  # creates pack.json.bak, which must not ship
    result = builder.export_zip(pack, pack_dir, tmp_path / "dist", "https://x/cat.zip")
    names = zipfile.ZipFile(result.zip_path).namelist()
    assert "Cat Pack/pack.json" in names
    assert "Cat Pack/images/cat/thumbnail.png" in names
    assert not any(n.endswith(".bak") for n in names)

    entry = result.catalog_entry
    assert entry["guid"] == pack.guid and entry["installFolder"] == "Cat Pack"
    assert entry["download"] == {"url": "https://x/cat.zip", "sizeBytes": result.size_bytes,
                                 "sha256": result.sha256, "format": "zip"}
    assert entry["imageCount"] == 1


def test_export_entry_parses_in_launcher(tmp_path):
    """The catalog entry must be accepted by the launcher's own parser, when it is checked out next door."""
    launcher_src = REPO / "Pixel-This-Launcher" / "src"
    if not launcher_src.is_dir():
        pytest.skip("Pixel-This-Launcher not checked out alongside")
    import sys
    sys.path.insert(0, str(launcher_src))
    try:
        from pmt_launcher.models.catalog import PackEntry
    except ImportError as exc:
        pytest.skip(f"launcher not importable: {exc}")
    art = make_art(tmp_path / "art", "Cat")
    pack = Pack(title="Cat Pack", images=[ImageEntry(id="cat", source=art)])
    builder.save_pack(pack, tmp_path / "Cat Pack")
    result = builder.export_zip(pack, tmp_path / "Cat Pack", tmp_path / "dist", "https://x/c.zip")
    parsed = PackEntry.from_dict(result.catalog_entry)
    assert parsed.guid == pack.guid and parsed.is_installable


def test_cli_new_add_validate_export(tmp_path, capsys):
    make_art(tmp_path / "output", "Cat")
    make_art(tmp_path / "output", "Dog")
    pack_dir = tmp_path / "Pets"
    assert main(["new", str(pack_dir), "--title", "Pets", "--author", "Me"]) == 0
    assert main(["add", str(pack_dir), str(tmp_path / "output")]) == 0
    assert [i.id for i in load_pack(pack_dir).images] == ["cat", "dog"]
    (pack_dir / "cover.png").write_bytes(b"x")
    assert main(["validate", str(pack_dir)]) == 0
    assert main(["export", str(pack_dir), "--out", str(tmp_path / "dist")]) == 0
    assert '"installFolder": "Pets"' in capsys.readouterr().out
