"""The editor window: pack details on the left, image order in the middle,
the selected image on the right."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from .. import APP_NAME, version_string
from ..core import builder, sources
from ..core.model import (
    FREQUENCIES,
    PACK_TYPES,
    RATINGS,
    REQUIRED_IMAGE_FILES,
    ImageEntry,
    Pack,
    Schedule,
    SocialLink,
    load_pack,
    make_image_id,
    new_guid,
    split_list,
    unique_id,
)

PREVIEW_SIZE = 256
MAX_RECENT = 8


# --- preferences -------------------------------------------------------------


def _prefs_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "PixelMeThis" / "PixelThisPack" / "settings.json"


def _load_prefs() -> dict:
    try:
        return json.loads(_prefs_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_prefs(prefs: dict) -> None:
    path = _prefs_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(prefs, indent=2), encoding="utf-8")
    except OSError:
        pass


def _open_in_file_manager(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


# --- main window -------------------------------------------------------------


class PackEditor(ttk.Frame):
    def __init__(self, root: tk.Tk) -> None:
        super().__init__(root, padding=8)
        self.root = root
        self.prefs = _load_prefs()
        self.pack = Pack()
        self.pack_dir: Path | None = None
        self.dirty = False
        self._loading = False
        self._iids: dict[str, ImageEntry] = {}
        self._selected: ImageEntry | None = None
        self._preview_img: tk.PhotoImage | None = None
        self._drag_iid: str | None = None

        self._build_menu()
        self._build_layout()
        self.grid(sticky="nsew")
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        root.protocol("WM_DELETE_WINDOW", self.quit_app)
        self._load_into_form()

    # --- construction --------------------------------------------------------

    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="New Pack", accelerator="Ctrl+N", command=self.new_pack)
        file_menu.add_command(label="Open Pack Folder…", accelerator="Ctrl+O", command=self.open_pack)
        self.recent_menu = tk.Menu(file_menu, tearoff=False)
        file_menu.add_cascade(label="Open Recent", menu=self.recent_menu)
        file_menu.add_separator()
        file_menu.add_command(label="Save", accelerator="Ctrl+S", command=self.save)
        file_menu.add_command(label="Save As…", command=self.save_as)
        file_menu.add_separator()
        file_menu.add_command(label="Quit", accelerator="Ctrl+Q", command=self.quit_app)
        menubar.add_cascade(label="File", menu=file_menu)

        pack_menu = tk.Menu(menubar, tearoff=False)
        pack_menu.add_command(label="Validate", accelerator="F5", command=self.show_validation)
        pack_menu.add_command(label="Export for Store…", command=self.export)
        pack_menu.add_separator()
        pack_menu.add_command(label="Set Cover Image…", command=self.set_cover)
        pack_menu.add_command(label="Open Pack Folder", command=self.open_folder)
        menubar.add_cascade(label="Pack", menu=pack_menu)

        img_menu = tk.Menu(menubar, tearoff=False)
        img_menu.add_command(label="Add from Pixel This Art…", accelerator="Ctrl+I", command=self.add_images)
        img_menu.add_command(label="Remove Selected", accelerator="Del", command=self.remove_images)
        img_menu.add_separator()
        img_menu.add_command(label="Move Up", accelerator="Alt+Up", command=lambda: self.move_selected(-1))
        img_menu.add_command(label="Move Down", accelerator="Alt+Down", command=lambda: self.move_selected(1))
        img_menu.add_command(label="Sort by Title", command=self.sort_images)
        menubar.add_cascade(label="Images", menu=img_menu)

        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="About", command=lambda: messagebox.showinfo(APP_NAME, version_string()))
        menubar.add_cascade(label="Help", menu=help_menu)
        self.root.config(menu=menubar)

        for seq, fn in (("<Control-n>", self.new_pack), ("<Control-o>", self.open_pack),
                        ("<Control-s>", self.save), ("<Control-q>", self.quit_app),
                        ("<Control-i>", self.add_images), ("<F5>", self.show_validation)):
            self.root.bind_all(seq, lambda _e, f=fn: f())
        self._refresh_recent()

    def _build_layout(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        panes = ttk.PanedWindow(self, orient="horizontal")
        panes.grid(row=0, column=0, sticky="nsew")

        left = ttk.Notebook(panes)
        panes.add(left, weight=2)
        left.add(self._build_details_tab(left), text="Pack")
        left.add(self._build_author_tab(left), text="Author / Owner")
        left.add(self._build_schedule_tab(left), text="Schedule")

        panes.add(self._build_images_panel(panes), weight=3)
        panes.add(self._build_image_detail(panes), weight=2)

        self.status = tk.StringVar()
        ttk.Label(self, textvariable=self.status, anchor="w").grid(row=1, column=0, sticky="ew", pady=(6, 0))

    def _field(self, parent, row: int, label: str, var: tk.Variable, setter, widget=None) -> ttk.Widget:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="nw", padx=(0, 8), pady=3)
        widget = widget or ttk.Entry(parent, textvariable=var)
        widget.grid(row=row, column=1, sticky="ew", pady=3)
        var.trace_add("write", lambda *_: self._on_field(setter, var.get()))
        return widget

    def _text(self, parent, row: int, label: str, height: int, setter) -> tk.Text:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="nw", padx=(0, 8), pady=3)
        text = tk.Text(parent, height=height, wrap="word", undo=True, font="TkDefaultFont")
        text.grid(row=row, column=1, sticky="nsew", pady=3)
        text.bind("<<Modified>>", lambda _e: self._on_text(text, setter))
        return text

    def _build_details_tab(self, parent) -> ttk.Frame:
        f = ttk.Frame(parent, padding=8)
        f.columnconfigure(1, weight=1)
        self.v_title, self.v_version, self.v_tagline = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.v_type, self.v_rating = tk.StringVar(), tk.StringVar()
        self.v_tags, self.v_triggers, self.v_guid = tk.StringVar(), tk.StringVar(), tk.StringVar()

        self._field(f, 0, "Title", self.v_title, lambda v: setattr(self.pack, "title", v))
        self._field(f, 1, "Type", self.v_type, self._set_type,
                    ttk.Combobox(f, textvariable=self.v_type, values=PACK_TYPES, state="readonly"))
        self._field(f, 2, "Rating", self.v_rating, lambda v: setattr(self.pack, "rating", v),
                    ttk.Combobox(f, textvariable=self.v_rating, values=RATINGS, state="readonly"))
        self._field(f, 3, "Version", self.v_version, lambda v: setattr(self.pack, "version", v))
        self._field(f, 4, "Tagline", self.v_tagline, lambda v: setattr(self.pack, "tagline", v))
        self.t_description = self._text(f, 5, "Description", 6, lambda v: setattr(self.pack, "description", v))
        f.rowconfigure(5, weight=1)
        self._field(f, 6, "Tags", self.v_tags, lambda v: setattr(self.pack, "tags", split_list(v)))
        self._field(f, 7, "Content warnings", self.v_triggers, lambda v: setattr(self.pack, "triggers", split_list(v)))
        ttk.Label(f, text="Separate tags and warnings with commas.", style="Hint.TLabel").grid(
            row=8, column=1, sticky="w")

        ttk.Label(f, text="GUID").grid(row=9, column=0, sticky="w", pady=(12, 3))
        guid_row = ttk.Frame(f)
        guid_row.grid(row=9, column=1, sticky="ew", pady=(12, 3))
        guid_row.columnconfigure(0, weight=1)
        ttk.Entry(guid_row, textvariable=self.v_guid, state="readonly").grid(row=0, column=0, sticky="ew")
        ttk.Button(guid_row, text="New", width=5, command=self.regen_pack_guid).grid(row=0, column=1, padx=(4, 0))

        self.cover_label = ttk.Label(f, text="", style="Hint.TLabel")
        self.cover_label.grid(row=10, column=1, sticky="w", pady=(8, 0))
        ttk.Button(f, text="Set Cover…", command=self.set_cover).grid(row=10, column=0, sticky="w", pady=(8, 0))
        return f

    def _build_author_tab(self, parent) -> ttk.Frame:
        f = ttk.Frame(parent, padding=8)
        f.columnconfigure(1, weight=1)
        self.v_author, self.v_store, self.v_site = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self._field(f, 0, "Name", self.v_author, lambda v: setattr(self.pack.author, "name", v))
        self._field(f, 1, "Online store", self.v_store, lambda v: setattr(self.pack.author, "onlinestore", v))
        self._field(f, 2, "Website", self.v_site, lambda v: setattr(self.pack.author, "website", v))

        ttk.Label(f, text="Social links").grid(row=3, column=0, columnspan=2, sticky="w", pady=(12, 3))
        self.links_tree = ttk.Treeview(f, columns=("username", "link"), height=6, selectmode="browse")
        self.links_tree.heading("#0", text="Platform")
        self.links_tree.heading("username", text="Username")
        self.links_tree.heading("link", text="Link")
        self.links_tree.column("#0", width=90)
        self.links_tree.column("username", width=110)
        self.links_tree.grid(row=4, column=0, columnspan=2, sticky="nsew")
        f.rowconfigure(4, weight=1)
        self.links_tree.bind("<<TreeviewSelect>>", self._on_link_select)

        edit = ttk.Frame(f)
        edit.grid(row=5, column=0, columnspan=2, sticky="ew", pady=4)
        self.v_link_platform, self.v_link_user, self.v_link_url = tk.StringVar(), tk.StringVar(), tk.StringVar()
        for col, (label, var, width) in enumerate((("Platform", self.v_link_platform, 10),
                                                   ("Username", self.v_link_user, 14),
                                                   ("Link", self.v_link_url, 24))):
            ttk.Label(edit, text=label).grid(row=0, column=col, sticky="w")
            ttk.Entry(edit, textvariable=var, width=width).grid(row=1, column=col, sticky="ew", padx=(0, 4))
        edit.columnconfigure(2, weight=1)
        buttons = ttk.Frame(f)
        buttons.grid(row=6, column=0, columnspan=2, sticky="w")
        ttk.Button(buttons, text="Add / Update", command=self._save_link).pack(side="left")
        ttk.Button(buttons, text="Remove", command=self._remove_link).pack(side="left", padx=4)
        return f

    def _build_schedule_tab(self, parent) -> ttk.Frame:
        f = ttk.Frame(parent, padding=8)
        f.columnconfigure(1, weight=1)
        self.v_start, self.v_end, self.v_freq = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self._field(f, 0, "Start date", self.v_start, lambda v: setattr(self._schedule(), "start_date", v.strip()))
        self._field(f, 1, "End date", self.v_end, lambda v: setattr(self._schedule(), "end_date", v.strip()))
        self._field(f, 2, "Frequency", self.v_freq, lambda v: setattr(self._schedule(), "frequency", v),
                    ttk.Combobox(f, textvariable=self.v_freq, values=FREQUENCIES))
        ttk.Label(f, style="Hint.TLabel", wraplength=280, justify="left",
                  text="Dates are YYYY-MM-DD. Used by daily packs: the image order is the unlock "
                       "order, so image #1 unlocks on day 1.").grid(row=3, column=1, sticky="w", pady=8)
        return f

    def _build_images_panel(self, parent) -> ttk.Frame:
        f = ttk.Frame(parent, padding=(8, 0))
        f.columnconfigure(0, weight=1)
        f.rowconfigure(1, weight=1)

        bar = ttk.Frame(f)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        ttk.Button(bar, text="Add from Art…", command=self.add_images, style="Accent.TButton").pack(side="left")
        ttk.Button(bar, text="Remove", command=self.remove_images).pack(side="left", padx=4)
        for text, fn in (("⤒", lambda: self.move_selected("top")), ("↑", lambda: self.move_selected(-1)),
                         ("↓", lambda: self.move_selected(1)), ("⤓", lambda: self.move_selected("bottom"))):
            ttk.Button(bar, text=text, width=3, command=fn).pack(side="left", padx=1)
        self.count_label = ttk.Label(bar, text="")
        self.count_label.pack(side="right")

        self.tree = ttk.Treeview(f, columns=("pos", "id", "title", "tags", "status"), show="headings",
                                 selectmode="extended")
        for col, text, width, stretch in (("pos", "#", 40, False), ("id", "Id", 130, False),
                                          ("title", "Title", 160, True), ("tags", "Tags", 140, True),
                                          ("status", "Status", 90, False)):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, stretch=stretch, anchor="center" if col == "pos" else "w")
        self.tree.tag_configure("problem", foreground="#e8615a")
        self.tree.tag_configure("pending", foreground="#5aa9e8")
        self.tree.grid(row=1, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(f, orient="vertical", command=self.tree.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)

        self.tree.bind("<<TreeviewSelect>>", self._on_image_select)
        self.tree.bind("<ButtonPress-1>", self._drag_start)
        self.tree.bind("<B1-Motion>", self._drag_motion)
        self.tree.bind("<ButtonRelease-1>", self._drag_end)
        self.tree.bind("<Delete>", lambda _e: self.remove_images())
        self.tree.bind("<Alt-Up>", lambda _e: (self.move_selected(-1), "break")[1])
        self.tree.bind("<Alt-Down>", lambda _e: (self.move_selected(1), "break")[1])
        ttk.Label(f, text="Drag rows or use the arrows to set the order players see.",
                  style="Hint.TLabel").grid(row=2, column=0, sticky="w", pady=(4, 0))
        return f

    def _build_image_detail(self, parent) -> ttk.Frame:
        f = ttk.Frame(parent, padding=(8, 0))
        f.columnconfigure(1, weight=1)
        self.preview = tk.Canvas(f, width=PREVIEW_SIZE, height=PREVIEW_SIZE, highlightthickness=0, bg="#1c1c1c")
        self.preview.grid(row=0, column=0, columnspan=2, pady=(0, 8))

        self.v_img_id, self.v_img_title, self.v_img_tags = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.v_img_alt, self.v_img_guid, self.v_img_info = tk.StringVar(), tk.StringVar(), tk.StringVar()

        ttk.Label(f, text="Id").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=3)
        self.e_img_id = ttk.Entry(f, textvariable=self.v_img_id)
        self.e_img_id.grid(row=1, column=1, sticky="ew", pady=3)
        self.e_img_id.bind("<Return>", lambda _e: self._apply_image_id())
        self.e_img_id.bind("<FocusOut>", lambda _e: self._apply_image_id())

        self._field(f, 2, "Title", self.v_img_title, lambda v: self._set_image("title", v))
        self.t_img_desc = self._text(f, 3, "Description", 4, lambda v: self._set_image("description", v))
        self._field(f, 4, "Tags", self.v_img_tags, lambda v: self._set_image("tags", split_list(v)))
        self.c_img_alt = ttk.Combobox(f, textvariable=self.v_img_alt, state="readonly")
        self._field(f, 5, "Alternative", self.v_img_alt, lambda v: self._set_image("alternative", v), self.c_img_alt)

        ttk.Label(f, text="GUID").grid(row=6, column=0, sticky="w", pady=3)
        guid_row = ttk.Frame(f)
        guid_row.grid(row=6, column=1, sticky="ew", pady=3)
        guid_row.columnconfigure(0, weight=1)
        ttk.Entry(guid_row, textvariable=self.v_img_guid, state="readonly").grid(row=0, column=0, sticky="ew")
        ttk.Button(guid_row, text="New", width=5, command=self.regen_image_guid).grid(row=0, column=1, padx=(4, 0))

        ttk.Label(f, textvariable=self.v_img_info, style="Hint.TLabel", wraplength=300, justify="left").grid(
            row=7, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.detail_frame = f
        return f

    # --- form <-> model ------------------------------------------------------

    def _on_field(self, setter, value) -> None:
        if self._loading:
            return
        setter(value)
        self._mark_dirty()

    def _on_text(self, text: tk.Text, setter) -> None:
        if not text.edit_modified():
            return
        text.edit_modified(False)
        if not self._loading:
            setter(text.get("1.0", "end-1c"))
            self._mark_dirty()

    def _set_text(self, text: tk.Text, value: str) -> None:
        text.delete("1.0", "end")
        text.insert("1.0", value)
        text.edit_reset()
        text.edit_modified(False)

    def _schedule(self) -> Schedule:
        if self.pack.schedule is None:
            self.pack.schedule = Schedule()
        return self.pack.schedule

    def _set_type(self, value: str) -> None:
        self.pack.type = value
        self._refresh_images()

    def _load_into_form(self) -> None:
        self._loading = True
        try:
            p = self.pack
            self.v_title.set(p.title)
            self.v_type.set(p.type)
            self.v_rating.set(p.rating)
            self.v_version.set(p.version)
            self.v_tagline.set(p.tagline)
            self._set_text(self.t_description, p.description)
            self.v_tags.set(", ".join(p.tags))
            self.v_triggers.set(", ".join(p.triggers))
            self.v_guid.set(p.guid)
            self.v_author.set(p.author.name)
            self.v_store.set(p.author.onlinestore)
            self.v_site.set(p.author.website)
            sched = p.schedule or Schedule()
            self.v_start.set(sched.start_date)
            self.v_end.set(sched.end_date)
            self.v_freq.set(sched.frequency)
        finally:
            self._loading = False
        self._refresh_links()
        self._selected = None
        self._refresh_images()
        self._show_image(None)
        self._refresh_cover()
        self._update_title()

    def _mark_dirty(self) -> None:
        if not self.dirty:
            self.dirty = True
            self._update_title()

    def _update_title(self) -> None:
        name = self.pack.title or "Untitled pack"
        where = f" — {self.pack_dir}" if self.pack_dir else " — not saved yet"
        self.root.title(f"{'* ' if self.dirty else ''}{name}{where} · {APP_NAME}")

    def _set_status(self, text: str) -> None:
        self.status.set(text)

    # --- author links --------------------------------------------------------

    def _refresh_links(self) -> None:
        self.links_tree.delete(*self.links_tree.get_children())
        for platform, link in self.pack.author.links.items():
            self.links_tree.insert("", "end", iid=platform, text=platform, values=(link.username, link.directlink))

    def _on_link_select(self, _event=None) -> None:
        sel = self.links_tree.selection()
        if not sel:
            return
        link = self.pack.author.links.get(sel[0])
        if link:
            self.v_link_platform.set(sel[0])
            self.v_link_user.set(link.username)
            self.v_link_url.set(link.directlink)

    def _save_link(self) -> None:
        platform = self.v_link_platform.get().strip().lower()
        if not platform:
            messagebox.showwarning(APP_NAME, "Enter a platform name (e.g. twitter, bluesky, patreon).")
            return
        self.pack.author.links[platform] = SocialLink(self.v_link_user.get().strip(), self.v_link_url.get().strip())
        self._refresh_links()
        self._mark_dirty()

    def _remove_link(self) -> None:
        for platform in self.links_tree.selection():
            self.pack.author.links.pop(platform, None)
        self._refresh_links()
        self._mark_dirty()

    # --- image list ----------------------------------------------------------

    def _iid(self, image: ImageEntry) -> str:
        return f"img{id(image)}"

    def _image_state(self, image: ImageEntry) -> tuple[str, str]:
        if image.source is not None:
            return "new", "pending"
        if self.pack_dir is None:
            return "", ""
        folder = self.pack.image_dir(self.pack_dir, image.saved_id or image.id)
        missing = [n for n in REQUIRED_IMAGE_FILES if not (folder / n).is_file()]
        if missing:
            return f"missing {len(missing)}", "problem"
        return "ok", ""

    def _refresh_images(self) -> None:
        selected = [self._iids[i] for i in self.tree.selection() if i in self._iids]
        self.tree.delete(*self.tree.get_children())
        self._iids = {}
        for pos, image in enumerate(self.pack.images, start=1):
            iid = self._iid(image)
            self._iids[iid] = image
            status, tag = self._image_state(image)
            self.tree.insert("", "end", iid=iid, tags=(tag,) if tag else (),
                             values=(pos, image.id, image.title, ", ".join(image.tags), status))
        keep = [self._iid(img) for img in selected if self._iid(img) in self._iids]
        if keep:
            self.tree.selection_set(keep)
        n = len(self.pack.images)
        self.count_label.config(text=f"{n} image{'s' if n != 1 else ''}")
        self.c_img_alt.config(values=[""] + [img.id for img in self.pack.images])
        if self._selected is not None:
            self._show_image(self._selected)

    def _selection(self) -> list[ImageEntry]:
        return [self._iids[i] for i in self.tree.selection() if i in self._iids]

    def _on_image_select(self, _event=None) -> None:
        self._apply_image_id()
        sel = self._selection()
        self._show_image(sel[0] if len(sel) == 1 else None)

    def add_images(self) -> None:
        start = self.prefs.get("art_dir") or str(Path.home())
        folder = filedialog.askdirectory(title="Pick a Pixel This Art output folder", initialdir=start)
        if not folder:
            return
        self.prefs["art_dir"] = folder
        _save_prefs(self.prefs)
        found = sources.scan(Path(folder))
        if not found:
            messagebox.showinfo(APP_NAME, "No Pixel This Art images found there.\n\n"
                                          "Look for folders containing pixel_art.png (usually the art tool's output/ folder).")
            return
        already = {img.source.resolve() for img in self.pack.images if img.source is not None}
        chosen = ImagePicker(self.root, found, already).result
        if not chosen:
            return
        taken = self.pack.image_ids()
        added = []
        for art in chosen:
            image_id = unique_id(make_image_id(art.name), taken)
            taken.add(image_id)
            image = ImageEntry(id=image_id, title=art.name, source=art.folder)
            self.pack.images.append(image)
            added.append(image)
        self._refresh_images()
        self.tree.selection_set([self._iid(img) for img in added])
        self.tree.see(self._iid(added[-1]))
        self._mark_dirty()
        self._set_status(f"Added {len(added)} image(s). They are copied into the pack when you save.")

    def remove_images(self) -> None:
        sel = self._selection()
        if not sel:
            return
        names = ", ".join(img.id for img in sel[:5]) + ("…" if len(sel) > 5 else "")
        if not messagebox.askyesno(APP_NAME, f"Remove {len(sel)} image(s) from the pack?\n\n{names}\n\n"
                                             "Their files stay on disk; only pack.json changes."):
            return
        for image in sel:
            self.pack.images.remove(image)
            for other in self.pack.images:
                if other.alternative == image.id:
                    other.alternative = ""
        self._selected = None
        self._show_image(None)
        self._refresh_images()
        self._mark_dirty()

    def move_selected(self, step) -> None:
        sel = self._selection()
        if not sel:
            return
        images = self.pack.images
        if step in ("top", "bottom"):
            rest = [img for img in images if img not in sel]
            ordered = [img for img in images if img in sel]
            images[:] = ordered + rest if step == "top" else rest + ordered
        else:
            indexes = sorted(images.index(img) for img in sel)
            if step < 0 and indexes[0] == 0 or step > 0 and indexes[-1] == len(images) - 1:
                return
            for i in indexes if step < 0 else reversed(indexes):
                images[i], images[i + step] = images[i + step], images[i]
        self._refresh_images()
        self.tree.see(self._iid(sel[0]))
        self._mark_dirty()

    def sort_images(self) -> None:
        if messagebox.askyesno(APP_NAME, "Sort all images by title? This replaces the current order."):
            self.pack.images.sort(key=lambda img: (img.title or img.id).lower())
            self._refresh_images()
            self._mark_dirty()

    def _drag_start(self, event) -> None:
        row = self.tree.identify_row(event.y)
        self._drag_iid = row if row and self.tree.identify_region(event.x, event.y) == "cell" else None

    def _drag_motion(self, event) -> None:
        if not self._drag_iid or len(self.tree.selection()) > 1:
            return
        target = self.tree.identify_row(event.y)
        if target and target != self._drag_iid:
            self.tree.move(self._drag_iid, "", self.tree.index(target))

    def _drag_end(self, _event) -> None:
        if not self._drag_iid:
            return
        self._drag_iid = None
        order = [self._iids[i] for i in self.tree.get_children()]
        if order != self.pack.images:
            self.pack.images[:] = order
            self._refresh_images()
            self._mark_dirty()

    # --- selected image ------------------------------------------------------

    def _set_image(self, attr: str, value) -> None:
        if self._selected is None:
            return
        setattr(self._selected, attr, value)
        iid = self._iid(self._selected)
        if self.tree.exists(iid):
            self.tree.set(iid, "title", self._selected.title)
            self.tree.set(iid, "tags", ", ".join(self._selected.tags))

    def _apply_image_id(self) -> None:
        image = self._selected
        if image is None or self._loading:
            return
        new_id = make_image_id(self.v_img_id.get())
        if new_id == image.id:
            self.v_img_id.set(image.id)
            return
        if new_id in self.pack.image_ids():
            messagebox.showwarning(APP_NAME, f"Another image already uses the id {new_id!r}.")
            self.v_img_id.set(image.id)
            return
        for other in self.pack.images:
            if other.alternative == image.id:
                other.alternative = new_id
        image.id = new_id
        self._refresh_images()
        self._mark_dirty()

    def regen_image_guid(self) -> None:
        if self._selected is None:
            return
        if messagebox.askyesno(APP_NAME, "Give this image a new GUID?\n\n"
                                         "Players' saved progress on it will no longer match."):
            self._selected.guid = new_guid()
            self.v_img_guid.set(self._selected.guid)
            self._mark_dirty()

    def _show_image(self, image: ImageEntry | None) -> None:
        self._selected = image
        self._loading = True
        try:
            self.v_img_id.set(image.id if image else "")
            self.v_img_title.set(image.title if image else "")
            self._set_text(self.t_img_desc, image.description if image else "")
            self.v_img_tags.set(", ".join(image.tags) if image else "")
            self.v_img_alt.set(image.alternative if image else "")
            self.v_img_guid.set(image.guid if image else "")
        finally:
            self._loading = False

        state = "normal" if image else "disabled"
        for child in self.detail_frame.winfo_children():
            if isinstance(child, (ttk.Entry, ttk.Combobox)) and child is not self.c_img_alt:
                child.state(["!disabled"] if image else ["disabled"])
        self.c_img_alt.config(state="readonly" if image else "disabled")
        self.t_img_desc.config(state=state)

        self.preview.delete("all")
        self._preview_img = None
        if image is None:
            n = len(self._selection())
            self.v_img_info.set(f"{n} images selected" if n > 1 else "Select an image to edit it.")
            return

        folder = image.source or (self.pack.image_dir(self.pack_dir, image.saved_id or image.id)
                                  if self.pack_dir else None)
        info = []
        if self.pack.type == "daily":
            info.append(f"Unlocks on day {self.pack.images.index(image) + 1}")
        if image.source is not None:
            info.append(f"From: {image.source} (copied on save)")
        elif folder is not None:
            info.append(f"Folder: {folder}")
        if folder is not None:
            self._draw_preview(folder / "pixel_art.png", info)
        self.v_img_info.set("\n".join(info))

    def _draw_preview(self, path: Path, info: list[str]) -> None:
        if not path.is_file():
            self.preview.create_text(PREVIEW_SIZE // 2, PREVIEW_SIZE // 2, text="no pixel_art.png", fill="#888")
            return
        try:
            img = tk.PhotoImage(file=str(path))
        except tk.TclError:
            self.preview.create_text(PREVIEW_SIZE // 2, PREVIEW_SIZE // 2, text="cannot read image", fill="#888")
            return
        w, h = img.width(), img.height()
        info.insert(0, f"{w}×{h}px")
        biggest = max(w, h, 1)
        # Whole-number scaling keeps pixel edges crisp.
        if biggest <= PREVIEW_SIZE:
            img = img.zoom(max(1, PREVIEW_SIZE // biggest))
        else:
            img = img.subsample(-(-biggest // PREVIEW_SIZE))
        self._preview_img = img
        self.preview.create_image(PREVIEW_SIZE // 2, PREVIEW_SIZE // 2, image=img)

    # --- pack-level actions --------------------------------------------------

    def regen_pack_guid(self) -> None:
        if messagebox.askyesno(APP_NAME, "Give the pack a new GUID?\n\nOnly do this for a brand-new pack "
                                         "or a copy. The game and launcher identify packs by GUID, so saves "
                                         "and installs of the old one will stop matching."):
            self.pack.guid = new_guid()
            self.v_guid.set(self.pack.guid)
            self._mark_dirty()

    def _refresh_cover(self) -> None:
        if self.pack_dir and (self.pack_dir / "cover.png").is_file():
            self.cover_label.config(text="cover.png set")
        else:
            self.cover_label.config(text="no cover.png")

    def set_cover(self) -> None:
        if not self._require_saved_location("setting a cover"):
            return
        path = filedialog.askopenfilename(title="Pick a cover image",
                                          filetypes=[("PNG image", "*.png")],
                                          initialdir=self.prefs.get("art_dir") or str(Path.home()))
        if not path:
            return
        shutil.copy2(path, self.pack_dir / "cover.png")
        self._refresh_cover()
        self._set_status("Cover set.")

    def open_folder(self) -> None:
        if self._require_saved_location("opening its folder"):
            _open_in_file_manager(self.pack_dir)

    def _require_saved_location(self, what: str) -> bool:
        if self.pack_dir is None:
            messagebox.showinfo(APP_NAME, f"Save the pack first before {what}.")
            return False
        return True

    # --- file actions --------------------------------------------------------

    def _confirm_discard(self) -> bool:
        if not self.dirty:
            return True
        answer = messagebox.askyesnocancel(APP_NAME, "Save changes to this pack first?")
        if answer is None:
            return False
        return self.save() if answer else True

    def new_pack(self) -> None:
        if not self._confirm_discard():
            return
        self.pack = Pack()
        self.pack_dir = None
        self.dirty = False
        self._load_into_form()
        self._set_status("New pack. Fill in the details, add images, then save.")

    def open_pack(self, path: Path | None = None) -> None:
        if not self._confirm_discard():
            return
        if path is None:
            chosen = filedialog.askdirectory(title="Open a pack folder (contains pack.json)",
                                             initialdir=self.prefs.get("pack_parent") or str(Path.home()))
            if not chosen:
                return
            path = Path(chosen)
        if not (path / "pack.json").is_file():
            messagebox.showerror(APP_NAME, f"No pack.json in {path}")
            return
        try:
            self.pack = load_pack(path)
        except (OSError, ValueError) as exc:
            messagebox.showerror(APP_NAME, f"Could not read pack.json:\n\n{exc}")
            return
        self.pack_dir = path
        self.dirty = False
        self._remember(path)
        self._load_into_form()
        self._set_status(f"Opened {path}")

    def save(self) -> bool:
        if self.pack_dir is None:
            return self.save_as()
        return self._save_to(self.pack_dir)

    def save_as(self) -> bool:
        parent = filedialog.askdirectory(title="Choose where to put the pack folder",
                                         initialdir=self.prefs.get("pack_parent") or str(Path.home()))
        if not parent:
            return False
        default = self.pack.title.strip() or "New Pack"
        name = simpledialog.askstring(APP_NAME, "Pack folder name:", initialvalue=default, parent=self.root)
        if not name or not name.strip():
            return False
        target = Path(parent) / name.strip()
        if (target / "pack.json").exists():
            try:
                existing = load_pack(target)
            except (OSError, ValueError):
                existing = None
            different = existing is None or existing.guid != self.pack.guid
            if different and not messagebox.askyesno(
                    APP_NAME, f"{target} already holds a different pack.\n\n"
                              "Overwrite its pack.json? (Its old pack.json is kept as pack.json.bak.)"):
                return False
        if self.pack_dir is not None and target.resolve() != self.pack_dir.resolve():
            # Bring everything across (story files, clues, cover), not just what pack.json lists.
            try:
                shutil.copytree(self.pack_dir, target, dirs_exist_ok=True)
            except OSError as exc:
                messagebox.showerror(APP_NAME, f"Could not copy the pack folder:\n\n{exc}")
                return False
        return self._save_to(target)

    def _save_to(self, target: Path) -> bool:
        self._apply_image_id()
        try:
            self.root.config(cursor="watch")
            self.root.update_idletasks()
            builder.save_pack(self.pack, target)
        except (OSError, builder.BuildError) as exc:
            messagebox.showerror(APP_NAME, f"Save failed:\n\n{exc}")
            return False
        finally:
            self.root.config(cursor="")
        self.pack_dir = target
        self.dirty = False
        self._remember(target)
        self._refresh_images()
        self._refresh_cover()
        self._update_title()
        errors = sum(1 for i in builder.validate(self.pack, target) if i.level == builder.ERROR)
        self._set_status(f"Saved {target / 'pack.json'}" +
                         (f" — {errors} problem(s), press F5 to see them" if errors else ""))
        return True

    def _remember(self, path: Path) -> None:
        recent = [str(path)] + [p for p in self.prefs.get("recent", []) if p != str(path)]
        self.prefs["recent"] = recent[:MAX_RECENT]
        self.prefs["pack_parent"] = str(path.parent)
        _save_prefs(self.prefs)
        self._refresh_recent()

    def _refresh_recent(self) -> None:
        self.recent_menu.delete(0, "end")
        recent = self.prefs.get("recent", [])
        if not recent:
            self.recent_menu.add_command(label="(none)", state="disabled")
        for p in recent:
            self.recent_menu.add_command(label=p, command=lambda p=p: self.open_pack(Path(p)))

    def show_validation(self) -> None:
        self._apply_image_id()
        issues = builder.validate(self.pack, self.pack_dir)
        if not issues:
            messagebox.showinfo(APP_NAME, "No problems found.")
            return
        errors = sum(1 for i in issues if i.level == builder.ERROR)
        TextDialog(self.root, "Validation",
                   f"{errors} error(s), {len(issues) - errors} warning(s)",
                   "\n".join(str(i) for i in issues))

    def export(self) -> None:
        if self.dirty or self.pack_dir is None:
            if not messagebox.askyesno(APP_NAME, "The pack must be saved before exporting. Save now?"):
                return
            if not self.save():
                return
        out = filedialog.askdirectory(title="Where should the export zip go?",
                                      initialdir=self.prefs.get("export_dir") or str(self.pack_dir.parent))
        if not out:
            return
        self.prefs["export_dir"] = out
        _save_prefs(self.prefs)
        url = simpledialog.askstring(APP_NAME, "Download URL for the store catalog (optional — you can fill "
                                               "it in later):", parent=self.root) or ""
        try:
            result = builder.export_zip(self.pack, self.pack_dir, Path(out), url.strip())
        except (OSError, builder.BuildError) as exc:
            messagebox.showerror(APP_NAME, f"Export failed:\n\n{exc}")
            return
        TextDialog(self.root, "Exported",
                   f"{result.zip_path}\n{result.size_bytes:,} bytes · sha256 {result.sha256[:16]}…\n\n"
                   "Add this to packs[] in PMT_Store/catalog.json:",
                   json.dumps(result.catalog_entry, indent=2), copy=True)
        self._set_status(f"Exported {result.zip_path}")

    def quit_app(self) -> None:
        if self._confirm_discard():
            self.root.destroy()


# --- dialogs -----------------------------------------------------------------


class ImagePicker(tk.Toplevel):
    """Choose which of the found art folders to add. Everything complete starts selected."""

    def __init__(self, parent: tk.Tk, found: list[sources.ArtImage], already: set[Path]) -> None:
        super().__init__(parent)
        self.title("Add images")
        self.transient(parent)
        self.result: list[sources.ArtImage] = []
        self._found = found

        frame = ttk.Frame(self, padding=10)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=f"Found {len(found)} image folder(s). Pick the ones to add:").pack(anchor="w")
        box_frame = ttk.Frame(frame)
        box_frame.pack(fill="both", expand=True, pady=6)
        self.listbox = tk.Listbox(box_frame, selectmode="extended", width=70, height=min(20, max(6, len(found))),
                                  activestyle="none", exportselection=False)
        scroll = ttk.Scrollbar(box_frame, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scroll.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        for i, art in enumerate(found):
            note = ""
            if not art.complete:
                note = f"   (missing {', '.join(art.missing)})"
            elif art.folder.resolve() in already:
                note = "   (already added)"
            self.listbox.insert("end", f"{art.name}{note}")
            if art.complete and not note:
                self.listbox.selection_set(i)

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="All", command=lambda: self.listbox.selection_set(0, "end")).pack(side="left")
        ttk.Button(buttons, text="None", command=lambda: self.listbox.selection_clear(0, "end")).pack(side="left", padx=4)
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Add", style="Accent.TButton", command=self._ok).pack(side="right", padx=4)
        self.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self.destroy())
        self.grab_set()
        self.wait_window()

    def _ok(self) -> None:
        picked = [self._found[i] for i in self.listbox.curselection()]
        incomplete = [a for a in picked if not a.complete]
        if incomplete and not messagebox.askyesno(
                APP_NAME, f"{len(incomplete)} selected folder(s) are missing files the game needs. Add them anyway?",
                parent=self):
            return
        self.result = picked
        self.destroy()


class TextDialog(tk.Toplevel):
    def __init__(self, parent: tk.Tk, title: str, header: str, body: str, copy: bool = False) -> None:
        super().__init__(parent)
        self.title(title)
        self.transient(parent)
        frame = ttk.Frame(self, padding=10)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=header, justify="left").pack(anchor="w", pady=(0, 6))
        text = tk.Text(frame, width=90, height=min(30, body.count("\n") + 2), wrap="word")
        text.insert("1.0", body)
        text.config(state="disabled")
        text.pack(fill="both", expand=True)
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(6, 0))
        if copy:
            ttk.Button(buttons, text="Copy", command=lambda: (self.clipboard_clear(), self.clipboard_append(body))
                       ).pack(side="left")
        ttk.Button(buttons, text="Close", command=self.destroy).pack(side="right")
        self.bind("<Escape>", lambda _e: self.destroy())


# --- entry -------------------------------------------------------------------


def _apply_theme(root: tk.Tk) -> None:
    try:
        import sv_ttk
        sv_ttk.set_theme("dark")
    except ImportError:
        ttk.Style(root).theme_use("clam")
    style = ttk.Style(root)
    style.configure("Hint.TLabel", foreground="#8a8a8a")


def run(pack_dir: Path | None = None) -> int:
    root = tk.Tk()
    root.geometry("1400x780")
    root.minsize(1000, 600)
    _apply_theme(root)
    editor = PackEditor(root)
    if pack_dir is not None:
        editor.open_pack(pack_dir)
    root.mainloop()
    return 0
