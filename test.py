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
from tkinter import filedialog, messagebox
import pygame
from PIL import Image
from mutagen import File as MutagenFile  # lire la durée exacte

# CairoSVG optionnel : on gère un fallback si absent
try:
    import cairosvg  # pour rasteriser les SVG en PNG en mémoire
    _HAS_CAIROSVG = True
except Exception:
    cairosvg = None
    _HAS_CAIROSVG = False


APP_TITLE = "🎧 MP3 Player by Zunochikirin"
SUPPORTED_EXT = {".mp3", ".ogg", ".wav", ".flac", ".m4a"}
PLAYLISTS_FILE = "playlists.json"

# ---------- Layout constants ----------
BTN_W = 56
BTN_H = 44
BTN_CORNER = 12
BTN_GAP = 8

ICON_BTN_PX = 22
ICON_LIST_PX = 18
ICON_NOW_PX  = 20
ICON_VOL_PX  = 18


# ---------- Utilitaires log ----------
def log(msg: str):
    print(f"[MP3Player] {msg}")


# ---------- Helpers thème/couleurs ----------
def theme_ink() -> str:
    # texte d'icône (noir en light, blanc en dark)
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


# ---------- Rasterize + tint SVG → CTkImage ----------
def _tint_svg_text(svg: str, color: str, size_px: int | None) -> str:
    # Ajoute color (currentColor) + remplace fill/stroke sauf fill="none"
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

    # Si CairoSVG indisponible → fallback : pas d’icône mais on ne crash pas
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


# ---------- Custom SVG Button ----------
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


# ---------- Application ----------
class MP3Player(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("820x650")
        self.minsize(720, 540)

        pygame.mixer.init()

        # State
        self.playlist: list[Path] = []
        self.current_index: int | None = None
        self.paused = False
        self.shuffle = False
        self.user_stopped = False

        # Progress state
        self.track_duration_s: float | None = None
        self.elapsed_base_ms: int = 0

        # Playlists persistence
        self.playlists: dict[str, list[str]] = self._load_playlists_file()

        # Temp WAV (fallback m4a)
        self._temp_files: list[Path] = []

        # Appearance
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.icons: dict[str, ctk.CTkImage | None] = {}

        self._build_ui()
        self._load_icons()
        self._apply_static_icons()
        self._poll_playback()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------- UI ----------
    def _build_ui(self):
        # ---------- HEADER ----------
        self.header = ctk.CTkFrame(self, corner_radius=12)
        self.header.pack(fill="x", padx=16, pady=16)

        # Sous-frame interne pour préserver les coins arrondis
        self.header_content = ctk.CTkFrame(self.header, fg_color="transparent")
        self.header_content.pack(fill="x", padx=10, pady=10)

        # Boutons Add / Folder / Clear
        self.add_btn = SvgButton(self.header_content, image=None, command=self.add_files,
                                 fg_color=accent_color())
        self.add_btn.pack(side="left", padx=BTN_GAP)

        self.folder_btn = SvgButton(self.header_content, image=None, command=self.add_folder,
                                    fg_color=accent_color())
        self.folder_btn.pack(side="left", padx=BTN_GAP)

        self.clear_btn = SvgButton(self.header_content, image=None, command=self.clear_playlist,
                                   fg_color=dim_color())
        self.clear_btn.pack(side="left", padx=BTN_GAP)

        # Sélecteur de playlist
        self.playlist_combo = ctk.CTkComboBox(self.header_content,
                                              values=sorted(self.playlists.keys()) if self.playlists else [],
                                              state="readonly",
                                              width=220)
        self.playlist_combo.set("Choose a playlist")
        self.playlist_combo.pack(side="left", padx=(BTN_GAP*2, BTN_GAP))

        self.load_pl_btn = ctk.CTkButton(self.header_content, text="Load", width=70,
                                         command=self._on_load_selected_playlist)
        self.load_pl_btn.pack(side="left", padx=(0, BTN_GAP))

        # Switch de thème
        self.theme_switch = ctk.CTkSwitch(self.header_content, text="Dark Mode", command=self._toggle_theme)
        self.theme_switch.select()
        self.theme_switch.pack(side="right", padx=10)

        # ---------- PLAYLIST ----------
        self.list_container = ctk.CTkScrollableFrame(self, corner_radius=12)
        self.list_container.pack(fill="both", expand=True, padx=16, pady=(8, 6))
        self.row_widgets: list[ctk.CTkFrame] = []

        # ---------- NOW PLAYING ----------
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

        # ---------- FOOTER ----------
        self.footer = ctk.CTkFrame(self, corner_radius=12)
        self.footer.pack(fill="x", padx=16, pady=16)

        # Sous-frame interne (fix coins arrondis)
        self.footer_content = ctk.CTkFrame(self.footer, fg_color="transparent")
        self.footer_content.pack(fill="x", padx=10, pady=10)

        # Left controls
        self.left_controls = ctk.CTkFrame(self.footer_content, fg_color="transparent")
        self.left_controls.pack(side="left", padx=10, pady=6)

        self.prev_btn = SvgButton(self.left_controls, image=None, command=self.prev_track,
                                  fg_color=dim_color())
        self.prev_btn.pack(side="left", padx=BTN_GAP)

        self.play_btn = SvgButton(self.left_controls, image=None, command=self.play_pause,
                                  fg_color=accent_color())
        self.play_btn.pack(side="left", padx=BTN_GAP)

        self.stop_btn = SvgButton(self.left_controls, image=None, command=self.stop,
                                  fg_color=dim_color())
        self.stop_btn.pack(side="left", padx=BTN_GAP)

        self.next_btn = SvgButton(self.left_controls, image=None, command=self.next_track,
                                  fg_color=dim_color())
        self.next_btn.pack(side="left", padx=BTN_GAP)

        self.shuffle_var = ctk.BooleanVar(value=False)
        self.shuffle_check = ctk.CTkCheckBox(self.left_controls, text="Shuffle",
                                             variable=self.shuffle_var, command=self.toggle_shuffle)
        self.shuffle_check.pack(side="left", padx=(BTN_GAP * 2, 0))

        # Right: volume
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

    # ---------- ICONS ----------
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

        # Header / Footer buttons (uniform size)
        self.icons["add"]    = load("plus.svg",          ICON_BTN_PX)
        self.icons["folder"] = load("add-folder.svg",    ICON_BTN_PX)
        self.icons["clear"]  = load("close.svg",         ICON_BTN_PX)

        self.icons["play"]   = load("start.svg",         ICON_BTN_PX)
        self.icons["pause"]  = load("pause-button.svg",  ICON_BTN_PX)
        self.icons["stop"]   = load("stop.svg",          ICON_BTN_PX)
        self.icons["prev"]   = load("back.svg",          ICON_BTN_PX)
        self.icons["next"]   = load("next.svg",          ICON_BTN_PX)

        # Other icons
        self.icons["music_tune"] = load("music-tune.svg",   ICON_LIST_PX)
        self.icons["music_sign"] = load("music-sign.svg",   ICON_NOW_PX)
        self.icons["volume"]     = load("medium-volume.svg", ICON_VOL_PX)

        self._apply_static_icons()
        self._refresh_playlist()

    def _apply_static_icons(self):
        if not self.icons:
            return
        # Header
        self.add_btn.set_image(self.icons.get("add"))
        self.folder_btn.set_image(self.icons.get("folder"))
        self.clear_btn.set_image(self.icons.get("clear"))
        # Footer
        self.prev_btn.set_image(self.icons.get("prev"))
        self.stop_btn.set_image(self.icons.get("stop"))
        self.next_btn.set_image(self.icons.get("next"))
        self._apply_play_icon()
        # Now playing + volume
        self.now_icon.configure(image=self.icons.get("music_sign"), text="")
        self.volume_icon.configure(image=self.icons.get("volume"), text="")

    def _apply_play_icon(self):
        is_playing = (self.current_index is not None) and pygame.mixer.music.get_busy() and not self.paused
        self.play_btn.set_image(self.icons.get("pause") if is_playing else self.icons.get("play"))

    # ---------- Theme toggle ----------
    def _toggle_theme(self):
        current = ctk.get_appearance_mode()
        ctk.set_appearance_mode("light" if current == "Dark" else "dark")
        self._load_icons()
        self.now_frame.configure(fg_color=accent_color())
        self._refresh_playlist()

    # ---------- Playlists persistence ----------
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
        self.stop()
        self.playlist.clear()
        for p in paths:
            pp = Path(p)
            if pp.exists() and pp.suffix.lower() in SUPPORTED_EXT:
                self.playlist.append(pp)
        self._refresh_playlist()
        if self.playlist:
            self.now_label.configure(text=f"Loaded playlist: {display_name(Path(self.playlist[0]), 50)}")

    # ---------- Playlist (UI) ----------
    def _refresh_playlist(self):
        # Nettoyage complet
        for child in self.list_container.winfo_children():
            child.destroy()
        self.row_widgets = []

        # Palette de référence
        light_dim, dark_dim = dim_color()                # gris moyens
        light_sep, dark_sep = list_separator_color()     # gris clairs/foncés

        # Couleurs de fond alternées selon le thème
        if ctk.get_appearance_mode().lower() == "dark":
            # 🎨 Mode sombre : deux gris proches, subtils
            bg_even = "#2d2d36"   # gris foncé principal (lignes paires)
            bg_odd  = "#34343f"   # gris légèrement plus clair (lignes impaires)
        else:
            # 🎨 Mode clair : on garde la palette cohérente d’origine
            bg_even = light_sep   # ex: #dcdcdc
            bg_odd  = light_dim   # ex: #d0d0d0

        # Couleur d'accent (pour icône active)
        accent = accent_color()[1] if ctk.get_appearance_mode().lower() == "dark" else accent_color()[0]

        for idx, path in enumerate(self.playlist):
            # Couleur alternée
            bg = bg_even if idx % 2 == 0 else bg_odd

            # Ligne
            row = ctk.CTkFrame(self.list_container, corner_radius=8, fg_color=bg)
            row.pack(fill="x", padx=12, pady=(2, 0))
            self.row_widgets.append(row)

            # Couleur de l’icône
            ink_color = accent if idx == self.current_index else theme_ink()

            # Chargement icône “music-tune” recolorée
            try:
                icon_path = Path(__file__).parent / "icons" / "music-tune.svg"
                icon_img = ctk_image_from_svg_file(icon_path, ink_color, size_px=ICON_LIST_PX)
            except Exception as e:
                print(f"⚠️ Icon tint failed: {e}")
                icon_img = None

            icon = ctk.CTkLabel(row, image=icon_img, text="")
            icon.pack(side="left", padx=(10, 8), pady=8)

            lbl = ctk.CTkLabel(row, text=display_name(path, 80), anchor="w")
            lbl.pack(side="left", fill="x", expand=True, padx=(0, 10), pady=8)

            # Clics
            for w in (row, icon, lbl):
                w.bind("<Button-1>", lambda e, i=idx: self._on_select(i))
                w.bind("<Double-Button-1>", lambda e, i=idx: self._start_play(i))

    def _on_select(self, index: int):
        for i, row in enumerate(self.row_widgets):
            row.configure(fg_color=("#ececff", "#2f2b47") if i == index else "transparent")

    # ---------- File/Folder ----------
    def add_files(self):
        paths = filedialog.askopenfilenames(
            title="Add audio files",
            filetypes=[("Audio", "*.mp3 *.ogg *.wav *.flac *.m4a"), ("All files", "*.*")]
        )
        for p in paths:
            if Path(p).suffix.lower() in SUPPORTED_EXT:
                self.playlist.append(Path(p))
        self._refresh_playlist()

    def add_folder(self):
        folder = filedialog.askdirectory(title="Add folder")
        if not folder:
            return

        folder = str(folder)
        base_name = Path(folder).name or "Playlist"
        files_in = []

        for root, _, files in os.walk(folder):
            for name in files:
                if Path(name).suffix.lower() in SUPPORTED_EXT:
                    full = str(Path(root) / name)
                    self.playlist.append(Path(full))
                    files_in.append(full)

        self._refresh_playlist()
        playlist_name = self._unique_playlist_name(base_name)
        self.playlists[playlist_name] = files_in
        self._save_playlists_file()
        self.playlist_combo.configure(values=sorted(self.playlists.keys()))
        self.playlist_combo.set(playlist_name)
        self.now_label.configure(text=f"Playlist saved: {playlist_name}")

    def clear_playlist(self):
        self.stop()
        self.playlist.clear()
        self._refresh_playlist()
        self.now_label.configure(text="Playlist cleared")
        self.time_label.configure(text="00:00 / 00:00")
        self.progress_var.set(0)

    # ---------- Playback ----------
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

    def stop(self):
        self.user_stopped = True
        pygame.mixer.music.stop()
        self.paused = False
        self.elapsed_base_ms = 0
        self.progress_var.set(0)
        self.time_label.configure(text="00:00 / " + (fmt_time(self.track_duration_s) if self.track_duration_s else "00:00"))
        self._apply_play_icon()
        self.now_label.configure(text="Stopped")

    def prev_track(self):
        if not self.playlist:
            return
        idx = (self.current_index - 1) % len(self.playlist) if self.current_index is not None else 0
        self._start_play(idx)

    def next_track(self):
        if not self.playlist:
            return
        idx = random.choice(range(len(self.playlist))) if self.shuffle else (
            (self.current_index + 1) % len(self.playlist) if self.current_index is not None else 0
        )
        self._start_play(idx)

    def toggle_shuffle(self):
        self.shuffle = bool(self.shuffle_var.get())

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

    # ---------- Fallback .m4a → WAV ----------
    def _ffmpeg_path(self) -> str | None:
        return shutil.which("ffmpeg")

    def _transcode_to_wav(self, src: Path) -> Path | None:
        """Transcode src (.m4a) en .wav temporaire via ffmpeg. Retourne le chemin WAV ou None."""
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
        """
        Pour .m4a : on tente d'abord un chargement direct; si pygame échoue,
        on transcode en WAV temporaire et on renvoie ce chemin.
        Pour autres formats : renvoie le chemin d'origine.
        """
        if path.suffix.lower() != ".m4a":
            return path

        # 1) Essai direct (selon SDL_mixer, ça peut fonctionner)
        try:
            pygame.mixer.music.load(str(path))
            return path
        except Exception:
            pass

        # 2) Fallback FFmpeg → WAV
        wav = self._transcode_to_wav(path)
        if wav:
            return wav

        # 3) Sans ffmpeg, on renverra l'erreur standard plus bas
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

            # Durée : on lit la durée du fichier d'origine (mutagen gère bien .m4a)
            self.track_duration_s = self._read_duration_seconds(orig_path)

            self.now_label.configure(text=f"Now playing: {display_name(orig_path, 60)}")
            right = fmt_time(0)
            left = fmt_time(self.track_duration_s) if self.track_duration_s else "00:00"
            self.time_label.configure(text=f"{right} / {left}")
            self.progress_var.set(0)

            self._refresh_playlist()
        except Exception as e:
            if orig_path.suffix.lower() == ".m4a" and not self._ffmpeg_path():
                message = ("Impossible de lire ce .m4a.\n\n"
                           "Installez FFmpeg (dans le PATH) pour activer le fallback, "
                           "ou convertissez le fichier en .mp3/.wav.")
            else:
                message = str(e)
            messagebox.showerror("Error", f"Cannot play:\n{orig_path}\n\n{message}")

    def _poll_playback(self):
        """Update progress bar + time label and chain next track."""
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

    # ---------- Clean close ----------
    def on_close(self):
        try:
            pygame.mixer.music.stop()
            pygame.mixer.quit()
        except Exception:
            pass
        # nettoyage des fichiers temporaires
        for p in set(self._temp_files):
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass
        self.destroy()


if __name__ == "__main__":
    app = MP3Player()
    app.mainloop()
