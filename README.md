# Pixel This Pack

Builds content packs for **Pixel Me This** from **Pixel This Art** output: pick
the art, set the order players see it in, fill in title / tags / owner / rating,
and save a folder the game loads directly. It can also zip the pack for the
store and produce its `catalog.json` entry for the launcher.

## Running

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=src python -m pixel_this_pack            # editor
PYTHONPATH=src python -m pixel_this_pack gui "path/to/My Pack"
```

(Or `pip install -e .` once, then just `pixel-this-pack`.)

Needs Python 3.11+ and Tk (`sudo apt install python3-tk` on Debian/Ubuntu).
`sv-ttk` gives the same dark theme as Pixel This Art; without it the app falls
back to the built-in "clam" theme.

## Using the editor

| Area | What it does |
|---|---|
| **Pack** tab | title, type (`dlc` / `daily` / `story` / `misc`), rating (`everyone` / `mature`), version, tagline, description, tags, content warnings, GUID, cover |
| **Author / Owner** tab | name, online store, website, social links (platform, username, link) |
| **Schedule** tab | start / end date and frequency for daily packs |
| Image list | **Add from Art…** picks a Pixel This Art `output/` folder and lists every image found. Drag rows or use ⤒ ↑ ↓ ⤓ (Alt+Up/Down) to reorder. Del removes. |
| Image detail | preview, id (= folder name), title, description, tags, alternative image, GUID |

- **Order is what players see.** For daily packs it is also the unlock order:
  image #1 unlocks on day 1, and `unlockDay` / `folder` are written for you.
- **Save** copies newly added art (`pixel_art.png`, `mask_index.png`,
  `palette.json`, `thumbnail.png`) into `images/<id>/` (`images/<id>/base/` for
  story packs), renames folders when you change an id, and writes `pack.json`.
  The previous `pack.json` is kept as `pack.json.bak`.
- **Nothing is ever deleted.** Removing an image only drops it from
  `pack.json`; its folder shows up as "orphaned" in **Validate (F5)** for you to
  delete by hand.
- Fields this tool doesn't know about (e.g. `packId`) are kept as-is.
- **Export for Store…** refuses to run while there are validation errors,
  zips the pack as `<folder>/…` (the layout the launcher expects), and shows the
  `packs[]` entry with size and sha256 to paste into `PMT_Store/catalog.json`.

## Headless commands

```
pixel-this-pack new <dir> --title T [--author A] [--type dlc|daily|story|misc] [--rating everyone|mature]
pixel-this-pack add <dir> <art-output-folder>...   add every complete image found
pixel-this-pack validate <dir>                     exit code 1 on errors
pixel-this-pack export <dir> [--out dist] [--url URL]
```

## Where packs go

The game scans `data/`, `dlc/` and `mods/packs/` next to its executable for
folders containing `pack.json`. Save straight into one of those to test, or
export and install through the launcher.

## Development

```bash
pip install -r requirements-dev.txt
pytest
ruff check .
```

`core/` never imports tkinter, so everything except the window is tested
headless. When `Pixel-This-Launcher` is checked out next to this repo, a test
also feeds the exported catalog entry through the launcher's own parser.
