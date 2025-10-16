import os
import re
import json
import random
import subprocess
import tempfile
import shutil
import hashlib
from io import BytesIO
from pathlib import Path

import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox
import pygame
from PIL import Image
from mutagen import File as MutagenFile  # precise duration

# CairoSVG is optional; we degrade gracefully if it's missing
try:
    import cairosvg  # rasterize SVG → PNG in memory
    _HAS_CAIROSVG = True
except Exception:
    cairosvg = None
    _HAS_CAIROSVG = False


APP_TITLE = " MP3 Player by Zunochikirin"
SUPPORTED_EXT = {".mp3", ".ogg", ".wav", ".flac", ".m4a"}
PLAYLISTS_FILE = "playlists.json"
WINDOW_STATE_FILE = "window.json"

# ---------- Layout constants ----------
BTN_W = 56
BTN_H = 44
BTN_CORNER = 12
BTN_GAP = 8

ICON_BTN_PX = 22
ICON_LIST_PX = 18
ICON_NOW_PX  = 20
ICON_VOL_PX  = 18


# ---------- Logging helper ----------
def log(msg: str):
    print(f"[MP3Player] {msg}")


# ---------- Theme / color helpers ----------
def theme_ink() -> str:
    return "#111111" if ctk.get_appearance_mode().lower() == "light" else "#ffffff"

def accent_color() -> tuple[str, str]:
    return ("#6C63FF", "#8C52FF")

def dim_color() -> tuple[str, str]:
    return ("#d0d0d0", "#666666")

def list_separator_color() -> tuple[str, str]:
    return ("#dcdcdc", "#2f2f3a")

def truncate(text: str, n: int = 70) -> str:
    return text if len(text) <= n else text[: n - 3] + "..."

def display_name(p: Path, limit: int = 70) -> str:
    return truncate(p.stem, limit)

def fmt_time(sec: float | None) -> str:
    if not sec or sec < 0:
        return "00:00"
    m = int(sec) // 60
    s = int(sec) % 60
    return f"{m:02d}:{s:02d}"

def _natural_key(p: Path):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', p.stem)]

def shade_hex(col: str, factor: float) -> str:
    """Lighten/darken a #rrggbb by factor (>1 lighter, <1 darker)."""
    c = col.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    r = max(0, min(255, int(int(c[0:2], 16) * factor)))
    g = max(0, min(255, int(int(c[2:4], 16) * factor)))
    b = max(0, min(255, int(int(c[4:6], 16) * factor)))
    return f"#{r:02x}{g:02x}{b:02x}"


# ---------- SVG → CTkImage (tint + rasterize) ----------
def _tint_svg_text(svg: str, color: str, size_px: int | None) -> str:
    if "<svg" in svg:
        if re.search(r'\bstyle\s*=', svg, re.I):
            svg = re.sub(r'(<svg[^>]*\bstyle\s*=\s*["\'])', r'\1color:' + color + ';', svg, count=1, flags=re.I)
        else:
            svg = re.sub(r'<svg\b', f'<svg style="color:{color}"', svg, count=1, flags=re.I)

    svg = re.sub(r'fill\s*=\s*([\'"])(?!none)[^\'"]+\1', f'fill="{color}"', svg, flags=re.I)
    svg = re.sub(r'stroke\s*=\s*([\'"])(?!none)[^\'"]+\1', f'stroke="{color}"', svg, flags=re.I)

    def _style_replacer(m):
        style = m.group(1)
        style = re.sub(r'fill\s*:\s*(?!none)[#\w().,%-]+', f'fill:{color}', style, flags=re.I)
        style = re.sub(r'stroke\s*:\s*[#\w().,%-]+',      f'stroke:{color}', style, flags=re.I)
        return f'style="{style}"'
    svg = re.sub(r'style\s*=\s*"(.*?)"', _style_replacer, svg, flags=re.I | re.S)

    def _css_replacer(m):
        css = m.group(1)
        css = re.sub(r'fill\s*:\s*(?!none)[#\w().,%-]+',  f'fill:{color}', css, flags=re.I)
        css = re.sub(r'stroke\s*:\s*[#\w().,%-]+',        f'stroke:{color}', css, flags=re.I)
        if "color:" not in css.lower():
            css = f"svg{{color:{color};}}\n" + css
        return f"<style>{css}</style>"
    svg = re.sub(r'<style[^>]*>(.*?)</style>', _css_replacer, svg, flags=re.I | re.S)

    if not re.search(r'fill\s*=', svg, re.I) and not re.search(r'stroke\s*=', svg, re.I):
        svg = re.sub(r"<svg\b", f'<svg fill="{color}"', svg, count=1, flags=re.I)

    if size_px is not None:
        if re.search(r'\bwidth\s*=', svg, re.I):
            svg = re.sub(r'width\s*=\s*([\'"])[^\'"]+\1', f'width="{size_px}"', svg, flags=re.I)
        else:
            svg = re.sub(r'<svg\b', f'<svg width="{size_px}"', svg, count=1, flags=re.I)
        if re.search(r'\bheight\s*=', svg, re.I):
            svg = re.sub(r'height\s*=\s*([\'"])[^\'"]+\1', f'height="{size_px}"', svg, flags=re.I)
        else:
            svg = re.sub(r'<svg\b', f'<svg height="{size_px}"', svg, count=1, flags=re.I)
    return svg

def _safe_read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            return path.read_text(encoding="utf-16")
        except Exception:
            try:
                return path.read_bytes().decode("utf-8", errors="ignore")
            except Exception as e:
                log(f"⚠️ Cannot decode SVG {path}: {e}")
                return None
    except Exception as e:
        log(f"⚠️ Cannot read SVG {path}: {e}")
        return None

def ctk_image_from_svg_file(path: Path, color: str, size_px: int | None) -> "ctk.CTkImage | None":
    svg_text = _safe_read_text(path)
    if not svg_text:
        return None

    svg_tinted = _tint_svg_text(svg_text, color, size_px)

    if not _HAS_CAIROSVG:
        log(f"ℹ️ CairoSVG not available; skipping rasterization for {path.name}")
        return None

    try:
        png_bytes = cairosvg.svg2png(
            bytestring=svg_tinted.encode("utf-8"),
            output_width=size_px if size_px else None,
            output_height=size_px if size_px else None
        )
        pil = Image.open(BytesIO(png_bytes)).convert("RGBA")
        if size_px:
            pil = pil.resize((size_px, size_px), Image.LANCZOS)
        return ctk.CTkImage(light_image=pil, dark_image=pil, size=pil.size)
    except Exception as e:
        log(f"⚠️ SVG rasterize failed for {path.name}: {e}")
        return None


# ---------- Small custom button with SVG image ----------
class SvgButton(ctk.CTkFrame):
    def __init__(self, master, image, command,
                 fg_color=("gray85", "gray20"), hover_color=None,
                 width=BTN_W, height=BTN_H, corner_radius=BTN_CORNER,
                 inner_padx=10, inner_pady=6):
        super().__init__(master, corner_radius=corner_radius, fg_color=fg_color)
        self._command = command
        self._base_color = fg_color
        self._hover_color = hover_color or self._shade(fg_color, 1.08)
        self._img = image

        self.configure(width=width, height=height)
        self._holder = ctk.CTkLabel(self, text="", image=self._img)
        self._holder.pack(expand=True, fill="both", padx=inner_padx, pady=inner_pady)

        for w in (self, self._holder):
            w.bind("<Button-1>", lambda e: self._command())
            w.bind("<Enter>", self._on_enter)
            w.bind("<Leave>", self._on_leave)

    def set_image(self, img):
        self._img = img
        self._holder.configure(image=self._img)

    def _on_enter(self, _): self.configure(fg_color=self._hover_color)
    def _on_leave(self, _): self.configure(fg_color=self._base_color)

    def _shade(self, color, f):
        def _one(col):
            col = col.lstrip("#")
            if len(col) == 3: col = ''.join(ch*2 for ch in col)
            try:
                r = int(col[0:2], 16); g = int(col[2:4], 16); b = int(col[4:6], 16)
            except Exception: return col
            r = max(0, min(255, int(r*f))); g = max(0, min(255, int(g*f))); b = max(0, min(255, int(b*f)))
            return f"#{r:02x}{g:02x}{b:02x}"
        if isinstance(color, tuple):
            light, dark = color
            return (_one(light), _one(dark))
        return _one(color)


# ---------- Main Application ----------
class MP3Player(ctk.CTk):
    def __init__(self):
        super().__init__()
        self._set_app_icons()
        self.title(APP_TITLE)
        self.geometry("820x650")
        self.minsize(720, 540)

        # Restore previous window geometry
        try:
            wstate = json.loads(Path(WINDOW_STATE_FILE).read_text(encoding="utf-8"))
            geo = wstate.get("geo")
            if geo: self.geometry(geo)
        except Exception:
            pass

        pygame.mixer.init()

        # Playback state
        self.playlist: list[Path] = []
        self.current_index: int | None = None
        self.paused = False
        self.shuffle = False
        self.user_stopped = False

        # Progress state
        self.track_duration_s: float | None = None
        self.elapsed_base_ms: int = 0

        # Persisted playlists
        self.playlists: dict[str, list[str]] = self._load_playlists_file()

        # Temp WAV cache for FFmpeg M4A fallback
        self._temp_files: list[Path] = []

        # Appearance defaults
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        # Icons and cached row icons
        self.icons: dict[str, ctk.CTkImage | None] = {}
        self.row_icon_default = None
        self.row_icon_accent = None
        self.shuffle_icon_default = None
        self.shuffle_icon_accent = None

        # Zebra colors
        self._bg_even = None
        self._bg_odd = None

        self._build_ui()
        self._load_icons()
        self._apply_static_icons()
        self._apply_shuffle_icon()
        self._poll_playback()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ----- App & taskbar icons -----
    def _set_app_icons(self):
        import platform, ctypes
        base_dir = Path(__file__).resolve().parent
        ico_path = base_dir / "assets" / "app.ico"
        png_path = base_dir / "assets" / "app.png"

        if platform.system() == "Windows":
            try:
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                    "Zunochikirin.MP3Player"
                )
            except Exception:
                pass

        if ico_path.exists():
            try:
                self.iconbitmap(default=str(ico_path))
                return
            except Exception:
                pass

        if png_path.exists():
            try:
                self.iconphoto(True, tk.PhotoImage(file=str(png_path)))
            except Exception as e:
                print(f"⚠️ Cannot set PNG icon: {e}")

    # ----- UI -----
    def _build_ui(self):
        # Header shell + inner content (rounded corners preserved)
        self.header = ctk.CTkFrame(self, corner_radius=12)
        self.header.pack(fill="x", padx=16, pady=16)
        self.header_content = ctk.CTkFrame(self.header, fg_color="transparent")
        self.header_content.pack(fill="x", padx=10, pady=10)

        # Header controls
        self.add_btn = SvgButton(self.header_content, image=None, command=self.add_files,
                                 fg_color=accent_color())
        self.add_btn.pack(side="left", padx=BTN_GAP)

        self.folder_btn = SvgButton(self.header_content, image=None, command=self.add_folder,
                                    fg_color=accent_color())
        self.folder_btn.pack(side="left", padx=BTN_GAP)

        self.clear_btn = SvgButton(self.header_content, image=None, command=self.clear_playlist,
                                   fg_color=dim_color())
        self.clear_btn.pack(side="left", padx=BTN_GAP)

        # Playlist selector
        self.playlist_combo = ctk.CTkComboBox(self.header_content,
                                              values=sorted(self.playlists.keys()) if self.playlists else [],
                                              state="readonly",
                                              width=220)
        self.playlist_combo.set("Choose a playlist")
        self.playlist_combo.pack(side="left", padx=(BTN_GAP*2, BTN_GAP))

        self.load_pl_btn = ctk.CTkButton(self.header_content, text="Load", width=70,
                                         command=self._on_load_selected_playlist)
        self.load_pl_btn.pack(side="left", padx=(0, BTN_GAP))

        self.theme_switch = ctk.CTkSwitch(self.header_content, text="Dark Mode", command=self._toggle_theme)
        self.theme_switch.select()
        self.theme_switch.pack(side="right", padx=10)

        # Playlist area
        self.list_container = ctk.CTkScrollableFrame(self, corner_radius=12)
        self.list_container.pack(fill="both", expand=True, padx=16, pady=(8, 6))
        self.row_widgets: list[ctk.CTkFrame] = []

        # Now-playing bar
        self.now_frame = ctk.CTkFrame(self, corner_radius=0, height=72, fg_color=accent_color())
        self.now_frame.pack(fill="x", pady=(4, 0))
        self.now_frame.pack_propagate(False)

        top_row = ctk.CTkFrame(self.now_frame, fg_color="transparent")
        top_row.pack(fill="x", pady=(6, 0))

        self.now_icon = ctk.CTkLabel(top_row, text="")
        self.now_icon.pack(side="left", padx=(12, 6), pady=6)

        self.now_label = ctk.CTkLabel(top_row, text="No track playing", font=("Segoe UI", 14))
        self.now_label.pack(side="left", padx=6, pady=6)

        self.time_label = ctk.CTkLabel(top_row, text="00:00 / 00:00", font=("Segoe UI", 12))
        self.time_label.pack(side="right", padx=12, pady=6)

        self.progress_var = ctk.DoubleVar(value=0)
        self.progress_bar = ctk.CTkProgressBar(self.now_frame, variable=self.progress_var,
                                               progress_color="#FFFFFF", height=6)
        self.progress_bar.pack(fill="x", padx=16, pady=(2, 10))

        # Footer (transport + volume + shuffle button)
        self.footer = ctk.CTkFrame(self, corner_radius=12)
        self.footer.pack(fill="x", padx=16, pady=16)
        self.footer_content = ctk.CTkFrame(self.footer, fg_color="transparent")
        self.footer_content.pack(fill="x", padx=10, pady=10)

        self.left_controls = ctk.CTkFrame(self.footer_content, fg_color="transparent")
        self.left_controls.pack(side="left", padx=10, pady=6)

        self.prev_btn = SvgButton(self.left_controls, image=None, command=self.prev_track,
                                  fg_color=dim_color())
        self.prev_btn.pack(side="left", padx=BTN_GAP)

        self.play_btn = SvgButton(self.left_controls, image=None, command=self.play_pause,
                                  fg_color=accent_color())
        self.play_btn.pack(side="left", padx=BTN_GAP)

        self.next_btn = SvgButton(self.left_controls, image=None, command=self.next_track,
                                  fg_color=dim_color())
        self.next_btn.pack(side="left", padx=BTN_GAP)

        # NEW: Shuffle icon button (replaces checkbox)
        self.shuffle_btn = SvgButton(
            self.left_controls,
            image=None,
            command=self.toggle_shuffle,
            fg_color=dim_color()
        )
        self.shuffle_btn.pack(side="left", padx=(BTN_GAP * 2, 0))

        # Volume
        self.volume_frame = ctk.CTkFrame(self.footer_content, fg_color="transparent")
        self.volume_frame.pack(side="right", padx=10, pady=6)

        self.volume_icon = ctk.CTkLabel(self.volume_frame, text="")
        self.volume_icon.pack(side="left", padx=(0, 6))

        self.volume_slider = ctk.CTkSlider(self.volume_frame, from_=0, to=100,
                                           number_of_steps=100, width=170,
                                           command=self._on_volume)
        self.volume_slider.set(80)
        self.volume_slider.pack(side="left")

        pygame.mixer.music.set_volume(0.8)

    # ----- Icons -----
    def _load_icons(self):
        ink = theme_ink()
        try:
            base_dir = Path(__file__).resolve().parent
        except NameError:
            base_dir = Path.cwd()
        icon_dir = base_dir / "icons"

        if not icon_dir.exists():
            log(f"⚠️ Icons directory not found: {icon_dir}")
        else:
            log(f"Icons directory: {icon_dir}")

        def load(name: str, px: int | None):
            p = icon_dir / name
            if not p.exists():
                log(f"⚠️ Missing icon file: {name}")
                return None
            img = ctk_image_from_svg_file(p, ink, size_px=px)
            if img is None:
                log(f"⚠️ Failed to create image for: {name}")
            return img

        # Header / footer
        self.icons["add"]    = load("plus.svg",          ICON_BTN_PX)
        self.icons["folder"] = load("add-folder.svg",    ICON_BTN_PX)
        self.icons["clear"]  = load("close.svg",         ICON_BTN_PX)
        self.icons["play"]   = load("start.svg",         ICON_BTN_PX)
        self.icons["pause"]  = load("pause-button.svg",  ICON_BTN_PX)
        self.icons["prev"]   = load("back.svg",          ICON_BTN_PX)
        self.icons["next"]   = load("next.svg",          ICON_BTN_PX)

        # Other
        self.icons["music_tune"] = load("music-tune.svg",   ICON_LIST_PX)
        self.icons["music_sign"] = load("music-sign.svg",   ICON_NOW_PX)
        self.icons["volume"]     = load("medium-volume.svg", ICON_VOL_PX)

        # Cached row icons (default/accent)
        mt_path = icon_dir / "music-tune.svg"
        is_dark = ctk.get_appearance_mode().lower() == "dark"
        accent = accent_color()[1] if is_dark else accent_color()[0]
        self.row_icon_default = ctk_image_from_svg_file(mt_path, theme_ink(), size_px=ICON_LIST_PX)
        self.row_icon_accent  = ctk_image_from_svg_file(mt_path, accent,      size_px=ICON_LIST_PX)

        # Shuffle icons (default/accent)
        shuf_path = icon_dir / "shuffle.svg"
        self.shuffle_icon_default = ctk_image_from_svg_file(shuf_path, theme_ink(), size_px=ICON_BTN_PX)
        self.shuffle_icon_accent  = ctk_image_from_svg_file(shuf_path, accent,      size_px=ICON_BTN_PX)

        self._apply_static_icons()
        self._refresh_playlist()
        self._apply_shuffle_icon()

    def _apply_static_icons(self):
        if not self.icons:
            return
        self.add_btn.set_image(self.icons.get("add"))
        self.folder_btn.set_image(self.icons.get("folder"))
        self.clear_btn.set_image(self.icons.get("clear"))
        self.prev_btn.set_image(self.icons.get("prev"))
        self.next_btn.set_image(self.icons.get("next"))
        self._apply_play_icon()
        self.now_icon.configure(image=self.icons.get("music_sign"), text="")
        self.volume_icon.configure(image=self.icons.get("volume"), text="")
        self._apply_shuffle_icon()

    def _apply_play_icon(self):
        is_playing = (self.current_index is not None) and pygame.mixer.music.get_busy() and not self.paused
        self.play_btn.set_image(self.icons.get("pause") if is_playing else self.icons.get("play"))

    def _apply_shuffle_icon(self):
        if hasattr(self, "shuffle_btn"):
            img = self.shuffle_icon_accent if self.shuffle else self.shuffle_icon_default
            self.shuffle_btn.set_image(img)

    # ----- Theme toggle -----
    def _toggle_theme(self):
        current = ctk.get_appearance_mode()
        ctk.set_appearance_mode("light" if current == "Dark" else "dark")
        self._load_icons()
        self.now_frame.configure(fg_color=accent_color())
        self._refresh_playlist()
        self._apply_shuffle_icon()

    # ----- Playlists persistence -----
    def _load_playlists_file(self) -> dict:
        p = Path(PLAYLISTS_FILE)
        if not p.exists():
            return {}
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception as e:
            log(f"⚠️ Cannot read {PLAYLISTS_FILE}: {e}")
        return {}

    def _save_playlists_file(self):
        try:
            Path(PLAYLISTS_FILE).write_text(json.dumps(self.playlists, indent=2), encoding="utf-8")
        except Exception as e:
            log(f"⚠️ Cannot write {PLAYLISTS_FILE}: {e}")

    def _unique_playlist_name(self, base: str) -> str:
        name = base
        c = 2
        while name in self.playlists:
            name = f"{base} ({c})"
            c += 1
        return name

    def _on_load_selected_playlist(self):
        name = self.playlist_combo.get()
        if not name or name == "Choose a playlist":
            return
        paths = self.playlists.get(name, [])
        self._load_playlist_paths(paths)

    def _load_playlist_paths(self, paths: list[str]):
        self._halt_playback()
        self.current_index = None
        self.playlist.clear()
        for p in paths:
            pp = Path(p)
            if pp.exists() and pp.suffix.lower() in SUPPORTED_EXT:
                self.playlist.append(pp)
        self.playlist.sort(key=_natural_key)
        self._refresh_playlist()
        if self.playlist:
            self.now_label.configure(text=f"Loaded playlist: {display_name(Path(self.playlist[0]), 50)}")

    # ----- Playlist UI -----
    def _refresh_playlist(self):
        for child in self.list_container.winfo_children():
            child.destroy()
        self.row_widgets = []

        if ctk.get_appearance_mode().lower() == "dark":
            self._bg_even = "#2d2d36"
            self._bg_odd  = "#34343f"
            hover_factor = 1.06
        else:
            light_dim, _ = dim_color()
            light_sep, _ = list_separator_color()
            self._bg_even = light_sep
            self._bg_odd  = light_dim
            hover_factor = 0.97

        for idx, path in enumerate(self.playlist):
            base_bg = self._bg_even if idx % 2 == 0 else self._bg_odd
            hover_bg = shade_hex(base_bg, hover_factor)

            row = ctk.CTkFrame(self.list_container, corner_radius=8, fg_color=base_bg)
            row.pack(fill="x", padx=12, pady=(2, 0))
            row._base_bg = base_bg
            row._hover_bg = hover_bg
            row._is_selected = False
            self.row_widgets.append(row)

            icon_img = self.row_icon_accent if idx == self.current_index else self.row_icon_default
            icon = ctk.CTkLabel(row, image=icon_img, text="")
            icon.pack(side="left", padx=(10, 8), pady=8)

            lbl = ctk.CTkLabel(row, text=display_name(path, 80), anchor="w")
            lbl.pack(side="left", fill="x", expand=True, padx=(0, 10), pady=8)

            for w in (row, icon, lbl):
                w.bind("<Button-1>", lambda e, i=idx: self._on_select(i))
                w.bind("<Double-Button-1>", lambda e, i=idx: self._start_play(i))

            def _enter(ev, r=row):
                if not getattr(r, "_is_selected", False):
                    r.configure(fg_color=r._hover_bg)
            def _leave(ev, r=row):
                if not getattr(r, "_is_selected", False):
                    r.configure(fg_color=r._base_bg)
            row.bind("<Enter>", _enter)
            row.bind("<Leave>", _leave)

    def _on_select(self, index: int):
        sel = "#3b3b46" if ctk.get_appearance_mode().lower() == "dark" else "#e6e6e6"
        for i, row in enumerate(self.row_widgets):
            if i == index:
                row._is_selected = True
                row.configure(fg_color=sel)
            else:
                row._is_selected = False
                row.configure(fg_color=row._base_bg)

    # ----- File / folder add -----
    def add_files(self):
        paths = filedialog.askopenfilenames(
            title="Add audio files",
            filetypes=[("Audio", "*.mp3 *.ogg *.wav *.flac *.m4a"), ("All files", "*.*")]
        )
        if not paths:
            return
        seen = set(map(str, self.playlist))
        for p in paths:
            pp = Path(p)
            if pp.suffix.lower() in SUPPORTED_EXT and str(pp) not in seen:
                self.playlist.append(pp)
                seen.add(str(pp))
        self.playlist.sort(key=_natural_key)
        self._refresh_playlist()

    def add_folder(self):
        folder = filedialog.askdirectory(title="Add folder")
        if not folder:
            return

        folder = str(folder)
        base_name = Path(folder).name or "Playlist"
        files_in = []

        seen = set(map(str, self.playlist))
        for root, _, files in os.walk(folder):
            for name in files:
                pp = Path(root) / name
                if pp.suffix.lower() in SUPPORTED_EXT:
                    s = str(pp)
                    if s not in seen:
                        self.playlist.append(pp)
                        files_in.append(s)
                        seen.add(s)

        self.playlist.sort(key=_natural_key)
        self._refresh_playlist()

        if files_in:
            playlist_name = self._unique_playlist_name(base_name)
            self.playlists[playlist_name] = files_in
            self._save_playlists_file()
            self.playlist_combo.configure(values=sorted(self.playlists.keys()))
            self.playlist_combo.set(playlist_name)
            self.now_label.configure(text=f"Playlist saved: {playlist_name}")
        else:
            self.now_label.configure(text="No new audio files found")

    def clear_playlist(self):
        self._halt_playback()
        self.current_index = None
        self.playlist.clear()
        self._refresh_playlist()
        self.now_label.configure(text="Playlist cleared")
        self.time_label.configure(text="00:00 / 00:00")
        self.progress_var.set(0)

    # ----- Playback -----
    def play_pause(self):
        if self.current_index is None and self.playlist:
            self._start_play(0)
            return

        if pygame.mixer.music.get_busy() and not self.paused:
            try:
                cur = max(0, pygame.mixer.music.get_pos())
            except Exception:
                cur = 0
            self.elapsed_base_ms += cur
            pygame.mixer.music.pause()
            self.paused = True
        else:
            if self.current_index is not None:
                pygame.mixer.music.unpause()
                self.paused = False

        self._apply_play_icon()

    def prev_track(self):
        if not self.playlist:
            return
        idx = (self.current_index - 1) % len(self.playlist) if self.current_index is not None else 0
        self._start_play(idx)

    # --- Smarter shuffle picker ---
    def _pick_shuffle_index(self) -> int | None:
        n = len(self.playlist)
        if n == 0:
            return None
        if self.current_index is None:
            return random.randrange(n)

        next_seq = (self.current_index + 1) % n
        forbidden = {self.current_index, next_seq}

        if n <= len(forbidden):
            for i in range(n):
                if i != self.current_index:
                    return i
            return None

        choices = [i for i in range(n) if i not in forbidden]
        return random.choice(choices) if choices else None

    def next_track(self):
        if not self.playlist:
            return

        if self.shuffle:
            idx = self._pick_shuffle_index()
            if idx is None:
                return
        else:
            idx = (self.current_index + 1) % len(self.playlist) if self.current_index is not None else 0

        self._start_play(idx)

    def toggle_shuffle(self):
        self.shuffle = not self.shuffle
        self._apply_shuffle_icon()

    def _on_volume(self, val):
        try:
            pygame.mixer.music.set_volume(float(val) / 100)
        except Exception:
            pass

    def _read_duration_seconds(self, path: Path) -> float | None:
        try:
            mf = MutagenFile(str(path))
            if mf is None or not hasattr(mf, "info") or mf.info is None:
                return None
            if hasattr(mf.info, "length"):
                return float(mf.info.length)
        except Exception as e:
            log(f"⚠️ Duration read failed for {path}: {e}")
        return None

    # ----- M4A fallback via FFmpeg -----
    def _ffmpeg_path(self) -> str | None:
        return shutil.which("ffmpeg")

    def _transcode_to_wav(self, src: Path) -> Path | None:
        ffmpeg = self._ffmpeg_path()
        if not ffmpeg:
            return None
        h = hashlib.sha1(str(src).encode("utf-8")).hexdigest()[:12]
        tmp_dir = Path(tempfile.gettempdir()) / "mp3player_cache"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        out = tmp_dir / f"{src.stem}_{h}.wav"
        try:
            subprocess.run([ffmpeg, "-y", "-i", str(src), "-ac", "2", "-ar", "44100", "-f", "wav", str(out)],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if out.exists():
                self._temp_files.append(out)
                return out
        except Exception as e:
            log(f"⚠️ ffmpeg transcode failed: {e}")
        return None

    def _ensure_playable_path(self, path: Path) -> Path:
        if path.suffix.lower() != ".m4a":
            return path

        try:
            pygame.mixer.music.load(str(path))
            return path  # native support available
        except Exception:
            pass

        wav = self._transcode_to_wav(path)
        if wav:
            return wav
        return path

    def _start_play(self, index: int):
        if not self.playlist:
            return
        orig_path = self.playlist[index]
        try:
            path_for_play = self._ensure_playable_path(orig_path)
            pygame.mixer.music.load(str(path_for_play))
            pygame.mixer.music.play()
            self.current_index = index
            self.user_stopped = False
            self.paused = False
            self.elapsed_base_ms = 0
            self._apply_play_icon()

            self.track_duration_s = self._read_duration_seconds(orig_path)
            self.now_label.configure(text=f"Now playing: {display_name(orig_path, 60)}")
            self.time_label.configure(text=f"{fmt_time(0)} / {fmt_time(self.track_duration_s) if self.track_duration_s else '00:00'}")
            self.progress_var.set(0)

            self._refresh_playlist()
            self._on_select(index)

        except Exception as e:
            if orig_path.suffix.lower() == ".m4a" and not self._ffmpeg_path():
                message = ("Unable to play this .m4a.\n\n"
                           "Install FFmpeg (in PATH) to enable the fallback, "
                           "or convert the file to .mp3/.wav.")
            else:
                message = str(e)
            messagebox.showerror("Error", f"Cannot play:\n{orig_path}\n\n{message}")

    def _poll_playback(self):
        if self.current_index is not None:
            try:
                cur_ms = max(0, pygame.mixer.music.get_pos())
            except Exception:
                cur_ms = 0
            total_ms = self.elapsed_base_ms + (0 if self.paused else cur_ms)

            if self.track_duration_s and self.track_duration_s > 0:
                dur_ms = self.track_duration_s * 1000.0
                progress = max(0.0, min(1.0, total_ms / dur_ms))
                self.progress_var.set(progress)
                self.time_label.configure(text=f"{fmt_time(total_ms/1000.0)} / {fmt_time(self.track_duration_s)}")
            else:
                self.progress_var.set(0 if self.paused else 0.5)
                self.time_label.configure(text=f"{fmt_time(total_ms/1000.0)} / 00:00")

        if not pygame.mixer.music.get_busy() and not self.paused and not self.user_stopped and self.current_index is not None:
            self.next_track()

        self.after(200, self._poll_playback)

    # ----- Internal halt (no public Stop button) -----
    def _halt_playback(self):
        """Internal helper to stop playback & reset UI (used by clear/load)."""
        try:
            self.user_stopped = True
            pygame.mixer.music.stop()
        except Exception:
            pass
        self.paused = False
        self.elapsed_base_ms = 0
        self.progress_var.set(0)
        self._apply_play_icon()
        self.time_label.configure(text="00:00 / " + (fmt_time(self.track_duration_s) if self.track_duration_s else "00:00"))
        self.now_label.configure(text="Stopped")

    # ----- Clean close -----
    def on_close(self):
        try:
            Path(WINDOW_STATE_FILE).write_text(json.dumps({"geo": self.geometry()}), encoding="utf-8")
        except Exception:
            pass
        try:
            pygame.mixer.music.stop()
            pygame.mixer.quit()
        except Exception:
            pass
        for p in set(self._temp_files):
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass
        self.destroy()


if __name__ == "__main__":
    app = MP3Player()
    app.mainloop()