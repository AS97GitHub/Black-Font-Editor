"""BLACK (Xbox) bitmap-font editor for Big.bin and Small.bin.

The editor preserves all unrecognised bytes.  It edits only the documented
glyph record fields, decodes the embedded DXT3 atlas for inspection/export,
and can re-import a PNG or DDS atlas (re-encoded as DXT3).
Usage: py black_font_editor.py [Big.bin | Small.bin]

Modules: black_font_format.py (the .bin layout), black_texture.py (DXT codecs, DDS).
"""

from __future__ import annotations

import os
import struct
import sys
import math
import re
import traceback
from pathlib import Path
import tkinter as tk
from tkinter import ttk
from tkinter import filedialog as _native_filedialog
from tkinter import messagebox as _native_messagebox
import tkinter.font as tkfont

from PIL import Image, ImageTk

from black_font_format import BlackFont, Glyph, FORMAT_NAMES
from black_texture import load_dds_image


PALETTE = {
    "bg":       "#24292e",   # window / frames
    "fg":       "#d9dcde",   # text
    "field":    "#1d2125",   # entries, listboxes, help text, scrollbar troughs
    "canvas":   "#1d2125",   # texture canvas
    "button":   "#2f363d",   # buttons, inactive tabs, scrollbar thumbs
    "active":   "#333b42",   # hover / pressed
    "border":   "#41484f",
    "select":   "#444d56",   # selection background
    "disabled": "#959da5",
    "accent":   "#58a6ff",
    "handle":   "#00e0ff",
    "tab_active": "#1d2125",   # active tab background
    "button_border_hover": "#58a6ff",   # button border on hover
}
HANDLE_SIZE = 6
DEFAULT_ZOOM = 4
MIN_ZOOM, MAX_ZOOM = 1, 16
MAX_DISPLAY_PIXELS = 32_000_000

# Errors that mean "this file / image is unreadable" (expected, user-facing):
# ValueError - not a BLACK font, bad texture size; OSError - missing file or a
# picture PIL cannot identify; struct.error - truncated header;
# DecompressionBombError - PIL refusing an absurdly large image.
EXPECTED_LOAD_ERRORS = (ValueError, OSError, struct.error, Image.DecompressionBombError)

# Font candidates
UI_FONT_CANDIDATES = (
    "Segoe UI",
    "Ubuntu",
    "sans-serif",
)

MONO_FONT_CANDIDATES = (
    "Consolas",
    "Ubuntu Mono",
    "monospace",
)

BOLD_FONT_CANDIDATES = (
    ("Segoe UI Black", "normal"),
    ("Ubuntu", "bold"),
    ("sans-serif", "bold"),
)

UI_FONT_SIZE = 9
MONO_FONT_SIZE = 10

TAB_WIDTHS = {
    "Segoe UI": 16,
    "Ubuntu": 14,
    "sans-serif": 13,
}


def _pick_family(root, candidates):
    """Return the first installed font family from candidates (case-insensitive), or None."""
    installed = {f.lower(): f for f in tkfont.families(root)}
    for name in candidates:
        if name.lower() in installed:
            return installed[name.lower()]
    return None


def setup_fonts(root):
    """Resolve UI/monospace families, apply them to Tk's named fonts, and
    return (ui_font, mono_font) tuples for widgets that set a font explicitly.

    Configuring the named fonts (TkDefaultFont etc.) also covers every widget
    that has no explicit font: ttk buttons/labels/entries/tabs, menus, dialogs.
    """
    ui_family = _pick_family(root, UI_FONT_CANDIDATES)
    mono_family = _pick_family(root, MONO_FONT_CANDIDATES)

    if ui_family:
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont",
                     "TkCaptionFont", "TkSmallCaptionFont", "TkIconFont", "TkTooltipFont"):
            try:
                tkfont.nametofont(name, root).configure(family=ui_family, size=UI_FONT_SIZE)
            except tk.TclError:
                pass
    if mono_family:
        try:
            tkfont.nametofont("TkFixedFont", root).configure(family=mono_family, size=MONO_FONT_SIZE)
        except tk.TclError:
            pass

    ui_font = (ui_family, UI_FONT_SIZE) if ui_family else "TkDefaultFont"
    mono_font = (mono_family, MONO_FONT_SIZE) if mono_family else "TkFixedFont"
    return ui_font, mono_font


def setup_bold_font(root, ui_font):
    """Return a tkfont.Font for emphasized text: the first installed family from
    BOLD_FONT_CANDIDATES, otherwise the UI font made bold."""
    found = _pick_family(root, [family for family, _ in BOLD_FONT_CANDIDATES])
    if found:
        weight = dict((f.lower(), w) for f, w in BOLD_FONT_CANDIDATES)[found.lower()]
        return tkfont.Font(root=root, family=found, size=UI_FONT_SIZE, weight=weight)
    fallback = tkfont.Font(root=root, font=ui_font)
    fallback.configure(weight="bold")
    return fallback


def setup_theme(root: tk.Tk) -> dict:
    """Apply the dark, compact layout used by the reference editor."""
    style = ttk.Style(root)
    style.theme_use("clam")
    c = PALETTE
    root.configure(background=c["bg"])
    style.configure(".", background=c["bg"], foreground=c["fg"], fieldbackground=c["field"],
                    bordercolor=c["border"], darkcolor=c["bg"], lightcolor=c["bg"],
                    troughcolor=c["field"], focuscolor=c["select"], insertcolor=c["fg"],
                    selectbackground=c["select"], selectforeground=c["fg"])
    style.map(".", foreground=[("disabled", c["disabled"])])

    style.configure("TButton", background=c["button"], bordercolor=c["border"],
                    darkcolor=c["button"], lightcolor=c["button"], padding=(0, 2))
    style.map("TButton",
              background=[("pressed", c["active"]), ("active", c["active"]), ("disabled", c["bg"])],
              lightcolor=[("pressed", c["active"]), ("active", c["button"]), ("focus", c["button"])],
              darkcolor=[("pressed", c["active"]), ("active", c["button"]), ("focus", c["button"])],
              bordercolor=[("disabled", c["border"]), ("pressed", c["border"]),
                          ("active", c["button_border_hover"]), ("focus", c["border"]),
                          ("alternate", c["border"])])

    style.configure("TMenubutton", background=c["button"], foreground=c["fg"],
                    bordercolor=c["border"], darkcolor=c["button"], lightcolor=c["button"],
                    arrowcolor=c["fg"], padding=2)
    style.map("TMenubutton",
              background=[("pressed", c["active"]), ("active", c["active"]),
                          ("disabled", c["bg"])],
              lightcolor=[("pressed", c["active"]), ("active", c["active"])],
              darkcolor=[("pressed", c["active"]), ("active", c["active"])],
              foreground=[("disabled", c["disabled"])],
              arrowcolor=[("disabled", c["disabled"])],
              bordercolor=[("active", c["button_border_hover"])])

    for name in ("TEntry", "TCombobox", "TSpinbox"):
        style.configure(name, fieldbackground=c["field"], foreground=c["fg"],
                        bordercolor=c["border"], darkcolor=c["field"], lightcolor=c["field"],
                        insertcolor=c["fg"], arrowcolor=c["fg"])
        style.map(name,
                  fieldbackground=[("disabled", c["bg"]), ("readonly", c["field"])],
                  foreground=[("disabled", c["disabled"])],
                  selectbackground=[("!focus", c["select"])],
                  selectforeground=[("!focus", c["fg"])])
    for name in ("TEntry", "TCombobox", "TSpinbox"):
        style.map(name, background=[("readonly", c["field"]), ("disabled", c["bg"])])
    style.configure("TCombobox", background=c["button"])
    style.configure("TSpinbox", background=c["button"])
    style.map("TCombobox", background=[("active", c["active"])])
    style.map("TSpinbox", background=[("active", c["active"])])

    style.configure("TNotebook", background=c["bg"], bordercolor=c["border"])
    style.configure("TNotebook.Tab", background=c["button"], foreground=c["fg"],
                    bordercolor=c["border"])
    style.map("TNotebook.Tab", background=[("selected", c["tab_active"]), ("active", c["active"])],
              lightcolor=[("selected", c["tab_active"]), ("!selected", c["button"])])
    style.configure("TNotebook.Tab", lightcolor=c["button"], darkcolor=c["button"])

    style.configure("TScrollbar", background=c["button"], bordercolor=c["border"],
                    troughcolor=c["field"], arrowcolor=c["fg"],
                    darkcolor=c["button"], lightcolor=c["button"])
    style.map("TScrollbar", background=[("active", c["active"])])

    style.configure("TPanedwindow", background=c["bg"])
    style.configure("Sash", sashthickness=4, gripcount=0, background=c["bg"],
                    bordercolor=c["border"], lightcolor=c["bg"], darkcolor=c["bg"])

    style.configure("TLabelframe", background=c["bg"], bordercolor=c["border"])
    style.configure("TLabelframe.Label", background=c["bg"], foreground=c["fg"])

    style.configure("TCheckbutton", background=c["bg"], foreground=c["fg"],
                    bordercolor=c["border"], darkcolor=c["bg"], lightcolor=c["bg"],
                    focuscolor=c["select"])
    style.map("TCheckbutton",
              background=[("active", c["active"]), ("pressed", c["active"])],
              foreground=[("disabled", c["disabled"])] + [("active", c["fg"])],
              bordercolor=[("active", c["button_border_hover"])])

    for pattern, value in (
        ("*Listbox.background", c["field"]), ("*Listbox.foreground", c["fg"]),
        ("*Listbox.selectBackground", c["select"]), ("*Listbox.selectForeground", c["fg"]),
        ("*Listbox.highlightThickness", 0), ("*Listbox.borderWidth", 1),
        ("*Listbox.relief", "solid"),
        ("*TCombobox*Listbox.background", c["field"]), ("*TCombobox*Listbox.foreground", c["fg"]),
        ("*TCombobox*Listbox.selectBackground", c["select"]),
        ("*TCombobox*Listbox.selectForeground", c["fg"]),
        ("*Menu.background", c["bg"]), ("*Menu.foreground", c["fg"]),
        ("*Menu.activeBackground", c["select"]), ("*Menu.activeForeground", c["fg"]),
        ("*Dialog.background", c["bg"]), ("*Dialog.foreground", c["fg"]),
    ):
        root.option_add(pattern, value)
    _theme_tk_file_dialog(root, c["field"], c["fg"])
    return c


def _theme_tk_file_dialog(root, bg, fg):
    """Tk's built-in file dialog (used on Linux, and on Windows via
    USE_TK_FILE_DIALOG) hardcodes a white canvas and black text for the file
    list. Patch its IconList constructor so it uses our colors."""
    if sys.platform == "darwin":
        return
    tcl = root.tk
    try:
        # The classes/procs are autoloaded on first dialog use; load them now.
        tcl.call("auto_load", "::tk::IconList")
        tcl.call("auto_load", "::tk::IconList_Create")
        if tcl.call("info", "commands", "::tk::IconList"):                # newer Tk 8.6 (TclOO)
            arglist, body = tcl.call("info", "class", "definition", "::tk::IconList", "Create")
            kind = "oo"
        elif tcl.call("info", "commands", "::tk::IconList_Create"):       # older Tk 8.6 (procs)
            arglist = tcl.call("info", "args", "::tk::IconList_Create")
            body = tcl.call("info", "body", "::tk::IconList_Create")
            kind = "proc"
        else:
            return
        body = str(body).replace("-background white", f"-background {bg}")
        body = body.replace("set fill black", f"set fill {fg}")
        if kind == "oo":
            tcl.call("oo::define", "::tk::IconList", "method", "Create", arglist, body)
        else:
            tcl.call("proc", "::tk::IconList_Create", arglist, body)
    except tk.TclError:
        pass                                    # cosmetic only: never block startup


# --- Themed popups -----------------------------------------------------
# On Windows, tkinter's messagebox/filedialog are native OS dialogs: always light,
# cannot be recolored. There we use our own message boxes and Tk's built-in
# (themeable) file dialog. On Linux Tk already draws its own dialogs; on macOS the
# native ones follow the system appearance.
USE_THEMED_DIALOGS = sys.platform.startswith("win")
USE_TK_FILE_DIALOG = sys.platform.startswith("win")

_APP_ROOT = None     # the Tk root (set by BlackFontEditor); dialog helpers need it
_ICON_PATH = None    # window icon, also applied to our dialogs


def set_app_root(root):
    global _APP_ROOT
    _APP_ROOT = root


def set_app_icon(icon_path):
    """Set the icon path to be used for all dialog windows."""
    global _ICON_PATH
    _ICON_PATH = icon_path


def apply_window_icon(window, icon_path):
    """Give a window the app icon: .ico via iconbitmap on Windows (the only place
    it works), otherwise iconphoto via PIL. A missing/broken icon never raises.
    Returns the PhotoImage (the caller must keep a reference) or None."""
    if not icon_path or not os.path.exists(icon_path):
        return None
    if sys.platform.startswith("win"):
        try:
            window.iconbitmap(icon_path)
            return None
        except tk.TclError:
            pass
    try:
        with Image.open(icon_path) as ico:
            ico.load()
            photo = ImageTk.PhotoImage(ico.convert("RGBA"))
        window.iconphoto(True, photo)
        return photo
    except Exception:
        return None


def set_dark_titlebar(window):
    """Windows 10 (1809+) / 11: ask DWM for a dark title bar. Silently does nothing elsewhere.
    `window` is a Tk widget or a Tk window path (str)."""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        tcl = (window if isinstance(window, tk.Misc) else _APP_ROOT).tk
        path = str(window)
        tcl.call("update", "idletasks")
        user32 = ctypes.windll.user32
        # `winfo id` returns a hex string such as '0x00200003' - plain int() would fail on it.
        hwnd = user32.GetParent(int(str(tcl.call("winfo", "id", path)), 0))
        value = ctypes.c_int(1)
        for attr in (20, 19):    # DWMWA_USE_IMMERSIVE_DARK_MODE (20; 19 on early builds)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
        # Force the non-client area (title bar) to repaint if the window is already visible.
        # SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED
        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020)
    except Exception:
        pass


def _darken_tk_dialog_titlebars(root):
    """Tk's built-in file dialog is a Tcl-created toplevel (class TkFDialog): give it a
    dark title bar the moment it is shown."""
    if not USE_TK_FILE_DIALOG or not sys.platform.startswith("win"):
        return
    try:
        root.tk.createcommand("black_dark_titlebar", set_dark_titlebar)
        root.tk.call("bind", "TkFDialog", "<Map>", "black_dark_titlebar %W")
    except tk.TclError:
        pass


_ICON_COLORS = {"info": "#4aa3df", "question": "#4aa3df", "warning": "#e5b53b", "error": "#e05252"}


def _themed_dialog(title, message, icon, buttons, default, cancel, parent=None):
    """Modal message box in the app palette. buttons: [(key, label), ...];
    returns the key of the pressed button (cancel key on Esc / window close)."""
    c = PALETTE
    parent = parent or _APP_ROOT
    dlg = tk.Toplevel(parent)
    dlg.withdraw()
    dlg.title(title)
    dlg.configure(background=c["bg"])
    dlg.resizable(False, False)
    if parent is not None:
        dlg.transient(parent.winfo_toplevel())
    dlg._icon_photo = apply_window_icon(dlg, _ICON_PATH)   # keep a reference
    result = {"key": cancel}

    def finish(key):
        result["key"] = key
        dlg.destroy()

    body = ttk.Frame(dlg, padding=(16, 14, 16, 8))
    body.pack(fill=tk.BOTH, expand=True)
    tk.Label(body, bitmap=icon, background=c["bg"], foreground=_ICON_COLORS.get(icon, c["fg"])
             ).grid(row=0, column=0, sticky="n", padx=(0, 14))
    ttk.Label(body, text=message, wraplength=420, justify=tk.LEFT
              ).grid(row=0, column=1, sticky="w")

    row = ttk.Frame(dlg, padding=(16, 4, 16, 14))
    row.pack(fill=tk.X)
    buttons_widgets = {}
    for key, label in reversed(buttons):
        b = ttk.Button(row, text=label, width=10, command=lambda k=key: finish(k))
        b.pack(side=tk.RIGHT, padx=(6, 0))
        buttons_widgets[key] = b

    dlg.bind("<Return>", lambda e: finish(default))
    dlg.bind("<Escape>", lambda e: finish(cancel))
    dlg.protocol("WM_DELETE_WINDOW", lambda: finish(cancel))

    # Center over the parent window, then show modally.
    dlg.update_idletasks()
    w, h = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
    if parent is not None and parent.winfo_viewable():
        top = parent.winfo_toplevel()
        x = top.winfo_rootx() + (top.winfo_width() - w) // 2
        y = top.winfo_rooty() + (top.winfo_height() - h) // 2
    else:
        x = (dlg.winfo_screenwidth() - w) // 2
        y = (dlg.winfo_screenheight() - h) // 2
    dlg.geometry(f"+{max(x, 0)}+{max(y, 0)}")
    set_dark_titlebar(dlg)
    dlg.deiconify()
    dlg.wait_visibility()
    dlg.grab_set()
    buttons_widgets[default].focus_set()
    dlg.wait_window()
    return result["key"]


class _MessageBoxes:
    """Drop-in for tkinter.messagebox (the subset this app uses)."""

    def _native(self, name, title, message, kw):
        return getattr(_native_messagebox, name)(title, message, **kw)

    def showinfo(self, title, message, **kw):
        if not USE_THEMED_DIALOGS:
            return self._native("showinfo", title, message, kw)
        _themed_dialog(title, message, "info", [("ok", "OK")], "ok", "ok", kw.get("parent"))
        return "ok"

    def showwarning(self, title, message, **kw):
        if not USE_THEMED_DIALOGS:
            return self._native("showwarning", title, message, kw)
        _themed_dialog(title, message, "warning", [("ok", "OK")], "ok", "ok", kw.get("parent"))
        return "ok"

    def showerror(self, title, message, **kw):
        if not USE_THEMED_DIALOGS:
            return self._native("showerror", title, message, kw)
        _themed_dialog(title, message, "error", [("ok", "OK")], "ok", "ok", kw.get("parent"))
        return "ok"

    def askyesno(self, title, message, **kw):
        if not USE_THEMED_DIALOGS:
            return self._native("askyesno", title, message, kw)
        return _themed_dialog(title, message, "question", [("yes", "Yes"), ("no", "No")],
                              "yes", "no", kw.get("parent")) == "yes"

    def askyesnocancel(self, title, message, **kw):
        if not USE_THEMED_DIALOGS:
            return self._native("askyesnocancel", title, message, kw)
        key = _themed_dialog(title, message, "question",
                             [("yes", "Yes"), ("no", "No"), ("cancel", "Cancel")],
                             "yes", "cancel", kw.get("parent"))
        return {"yes": True, "no": False}.get(key)


def _tk_file_dialog(kind, options):
    """Call Tk's own (Tcl-implemented, themeable) file dialog: kind is 'open' or 'save'."""
    root = options.get("parent") or _APP_ROOT
    args = []
    for key, value in options.items():
        if key == "parent":
            value = str(value)
        if value:
            args += ["-" + key, value]
    if "parent" not in options:
        args += ["-parent", str(root)]
    root.tk.call("auto_load", "::tk::dialog::file::")

    # Apply dark titlebar and icon immediately using after_idle
    def apply_dialog_settings():
        try:
            root.tk.call("update", "idletasks")
            for w in root.tk.call("winfo", "children", "."):
                try:
                    if root.tk.call("winfo", "class", w) == "TkFDialog":
                        set_dark_titlebar(w)
                        root.tk.call("wm", "geometry", w, "480x320")

                        # Set icon for file dialog
                        if _ICON_PATH and os.path.exists(_ICON_PATH):
                            try:
                                if sys.platform.startswith("win"):
                                    root.tk.call("wm", "iconbitmap", w, _ICON_PATH)
                            except tk.TclError:
                                pass
                except tk.TclError:
                    pass
        except Exception:
            pass

    root.after_idle(apply_dialog_settings)

    result = root.tk.call("::tk::dialog::file::", kind, *args)
    # Cancel comes back as an empty Tcl value (tkinter shows it as an empty tuple).
    if not result:
        return ""
    if isinstance(result, (tuple, list)):
        result = result[0]
    return str(result)


class _FileDialogs:
    """Drop-in for tkinter.filedialog (askopenfilename / asksaveasfilename)."""

    def askopenfilename(self, **options):
        if USE_TK_FILE_DIALOG:
            try:
                return _tk_file_dialog("open", options)
            except tk.TclError:
                pass                              # fall back to the native dialog
        return _native_filedialog.askopenfilename(**options)

    def asksaveasfilename(self, **options):
        if USE_TK_FILE_DIALOG:
            try:
                return _tk_file_dialog("save", options)
            except tk.TclError:
                pass
        return _native_filedialog.asksaveasfilename(**options)


messagebox = _MessageBoxes()
filedialog = _FileDialogs()


class BlackFontEditor:
    def __init__(self, root: tk.Tk):
        self.root = root
        set_app_root(root)
        self.colors = setup_theme(root)
        self.ui_font, self.mono_font = setup_fonts(root)
        self.bold_font = setup_bold_font(root, self.ui_font)
        set_dark_titlebar(root)
        _darken_tk_dialog_titlebars(root)
        self.root.title("BLACK Bitmap Font Editor")
        self.root.geometry("1200x760")

        # Set window icon (optional, won't fail if missing)
        if getattr(sys, 'frozen', False):
            base_path = sys._MEIPASS
        else:
            base_path = os.path.dirname(__file__)

        icon_path = os.path.join(base_path, "resources", "icon.ico")
        if os.path.exists(icon_path):
            set_app_icon(icon_path)                                       # dialogs get it too
            self._icon_photo = apply_window_icon(self.root, icon_path)    # keep a reference

        self.font: BlackFont | None = None
        self.image: Image.Image | None = None
        self.photo: ImageTk.PhotoImage | None = None
        self._tex_photo_zoom = None
        self.zoom = DEFAULT_ZOOM
        self.selected_index: int | None = None
        self.drag_mode: str | None = None
        self.drag_origin: tuple[float, float, tuple[float, float, float, float]] | None = None
        self.dirty = False
        self._pane_min_widths = []
        self._build_ui()
        set_dark_titlebar(root)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def _add_pane(self, frame, min_width, weight):
        """Add a pane to the main PanedWindow and remember its minimum width."""
        self.panes.add(frame, weight=weight)
        self._pane_min_widths.append(min_width)

    def _enable_pane_clamp(self, paned):
        """Keep every pane at or above its minimum width while a sash is dragged."""
        tags = paned.bindtags()
        paned.bindtags((tags[1], tags[0]) + tags[2:])
        paned.bind("<B1-Motion>", self._clamp_sashes, add="+")
        paned.bind("<ButtonRelease-1>", self._clamp_sashes, add="+")

    def _clamp_sashes(self, _event=None):
        paned = self.panes
        mins = self._pane_min_widths
        n = len(mins) - 1
        if n < 1 or len(paned.panes()) != len(mins):
            return
        pos = [paned.sashpos(i) for i in range(n)]
        total = paned.winfo_width()
        for i in range(n):
            prev = pos[i - 1] if i else 0
            pos[i] = max(pos[i], prev + mins[i])
        for i in reversed(range(n)):
            nxt = pos[i + 1] if i + 1 < n else total
            pos[i] = min(pos[i], nxt - mins[i + 1])
        for i in range(n):
            if pos[i] != paned.sashpos(i):
                paned.sashpos(i, pos[i])

    def _build_ui(self) -> None:
        toolbar = ttk.Frame(self.root)
        toolbar.pack(side=tk.TOP, fill=tk.X, padx=4, pady=(6, 2))

        ttk.Button(toolbar, text="Open .bin...", command=self.open_file, padding=(8, 2)).pack(side=tk.LEFT, padx=(0, 3))
        ttk.Button(toolbar, text="Import texture...", command=self.import_texture, padding=(8, 2)).pack(side=tk.LEFT, padx=3)
        ttk.Button(toolbar, text="Export texture...", command=self.export_texture, padding=(8, 2)).pack(side=tk.LEFT, padx=(3, 0))

        ttk.Label(toolbar, text="Zoom:").pack(side=tk.LEFT, padx=(16, 2))
        self.zoom_var = tk.IntVar(value=self.zoom)
        zoom = ttk.Spinbox(toolbar, from_=MIN_ZOOM, to=MAX_ZOOM, width=2, textvariable=self.zoom_var, command=self.change_zoom)
        zoom.pack(side=tk.LEFT, padx=(3, 0))
        zoom.bind("<Return>", lambda _e: self.change_zoom())
        zoom.bind("<FocusOut>", lambda _e: self.change_zoom())

        ttk.Button(toolbar, text="Save As...", command=self.save_as, padding=(8, 2)).pack(side=tk.RIGHT, padx=(3, 0))
        ttk.Button(toolbar, text="Save (overwrite)", command=self.save, padding=(8, 2)).pack(side=tk.RIGHT, padx=(0, 3))

        self.panes = ttk.Panedwindow(self.root, orient=tk.HORIZONTAL)
        self.panes.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self._enable_pane_clamp(self.panes)

        left = ttk.Frame(self.panes, width=224)
        centre = ttk.Frame(self.panes)
        right = ttk.Frame(self.panes, width=265)
        left.pack_propagate(False)
        right.pack_propagate(False)
        self._add_pane(left, min_width=150, weight=0)
        self._add_pane(centre, min_width=250, weight=1)
        self._add_pane(right, min_width=200, weight=0)
        self._build_left(left)
        self._build_canvas(centre)
        self._build_right(right)

        self.status_var = tk.StringVar(value="Open Big.bin or Small.bin to begin.")
        ttk.Label(self.root, textvariable=self.status_var, anchor="w", relief=tk.SUNKEN).pack(side=tk.BOTTOM, fill=tk.X)

    def _build_left(self, parent: ttk.Frame) -> None:
        tabs = ttk.Notebook(parent)
        tabs.pack(fill=tk.BOTH, expand=True, padx=(4, 0), pady=4)
        glyph_tab = ttk.Frame(tabs)
        pointer_tab = ttk.Frame(tabs)
        tabs.add(glyph_tab, text="Glyphs")
        tabs.add(pointer_tab, text="Pointers")

        # Set tab width based on font family
        if isinstance(self.ui_font, tuple):
            font_name = self.ui_font[0]
        else:
            font_name = tkfont.nametofont(self.ui_font, self.root).actual("family")
        tab_width = TAB_WIDTHS.get(font_name, 13)

        style = ttk.Style(self.root)
        style.configure("TNotebook.Tab", width=tab_width, padding=(0, 4, 0, 4), anchor="center")
        style.map("TNotebook.Tab", padding=[("selected", (0, 7, 0, 4))], expand=[("selected", (0, 0, 0, 0))])

        frame = ttk.Frame(glyph_tab)
        frame.pack(fill=tk.BOTH, expand=True)
        self.glyph_list = tk.Listbox(frame, font=self.mono_font, exportselection=False,
                                     borderwidth=0, relief="flat")
        bar = ttk.Scrollbar(frame, command=self.glyph_list.yview)
        self.glyph_list.config(yscrollcommand=bar.set)
        self.glyph_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        bar.pack(side=tk.RIGHT, fill=tk.Y)
        self.glyph_list.bind("<<ListboxSelect>>", self.select_from_list)

        pframe = ttk.Frame(pointer_tab)
        pframe.pack(fill=tk.BOTH, expand=True)
        self.pointer_list = tk.Listbox(pframe, font=self.mono_font, exportselection=False, borderwidth=0, relief="flat")
        pbar = ttk.Scrollbar(pframe, command=self.pointer_list.yview)
        self.pointer_list.config(yscrollcommand=pbar.set)
        self.pointer_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        pbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.pointer_list.bind("<Double-Button-1>", self.select_from_pointer)

    def _build_canvas(self, parent: ttk.Frame) -> None:
        holder = ttk.Frame(parent)
        holder.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        self.canvas = tk.Canvas(holder, background=PALETTE["canvas"], highlightthickness=1, highlightbackground=PALETTE["border"], highlightcolor=PALETTE["border"])
        xbar = ttk.Scrollbar(holder, orient=tk.HORIZONTAL, command=self.canvas.xview)
        ybar = ttk.Scrollbar(holder, orient=tk.VERTICAL, command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=xbar.set, yscrollcommand=ybar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        holder.rowconfigure(0, weight=1)
        holder.columnconfigure(0, weight=1)
        self.canvas.bind("<ButtonPress-1>", self.canvas_press)
        self.canvas.bind("<B1-Motion>", self.canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self.canvas_release)

    def _build_right(self, parent: ttk.Frame) -> None:
        self.uv_mode_var = tk.BooleanVar(value=False)

        # Glyph Properties - directly in parent like reference
        props = ttk.LabelFrame(parent, text="Glyph Properties")
        props.pack(fill=tk.X, padx=(0, 4), pady=4)

        self.property_vars = {key: tk.StringVar() for key in
                              ("record", "character", "pixel_x", "pixel_y", "cell_width", "cell_height",
                               "offset_x", "offset_y", "advance")}
        self.property_labels: dict[str, ttk.Label] = {}

        fields = (
            ("Index:", "record", True),
            ("Character:", "character", False),
            ("Pixel X:", "pixel_x", False),
            ("Pixel Y:", "pixel_y", False),
            ("Cell Width:", "cell_width", False),
            ("Cell Height:", "cell_height", False),
            ("Offset X:", "offset_x", False),
            ("Offset Y:", "offset_y", False),
            ("Advance Width:", "advance", False),
        )

        for label, name, readonly in fields:
            frame = ttk.Frame(props)
            frame.pack(fill=tk.X, pady=(2, 1))

            label_widget = ttk.Label(frame, text=label, width=17, anchor=tk.E)
            label_widget.pack(side=tk.LEFT, padx=(0, 2))
            self.property_labels[name] = label_widget

            entry = ttk.Entry(frame, textvariable=self.property_vars[name], width=12)
            entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2))
    
            if readonly:
                entry.state(["readonly"])

        ttk.Checkbutton(props, text="Display normalized UV", variable=self.uv_mode_var, command=self.on_uv_display_changed).pack(fill=tk.X, padx=2, pady=2)

        ttk.Button(props, text="Apply Changes", command=self.apply_properties, padding=2).pack(fill=tk.X, padx=2, pady=2)

        # File info - separate frame
        file_info = ttk.LabelFrame(parent, text="File Info")
        file_info.pack(fill=tk.X, padx=(0, 4), pady=4)
        self.info_var = tk.StringVar(value="No file loaded")
        ttk.Label(file_info, textvariable=self.info_var, justify=tk.LEFT, wraplength=240).pack(fill=tk.X, padx=4, pady=4)

        # Texture background & border
        bg_frame = ttk.LabelFrame(parent, text="Texture Background")
        bg_frame.pack(fill=tk.X, padx=(0, 4), pady=4)

        self.bg_var = tk.StringVar(value="#000000")
        self.bg_enabled = tk.BooleanVar(value=True)
        self.show_border_var = tk.BooleanVar(value=False)

        ttk.Checkbutton(bg_frame, text="Show black background", variable=self.bg_enabled, command=self.on_bg_color_changed).pack(fill=tk.X, padx=2, pady=(2, 0))

        ttk.Checkbutton(bg_frame, text="Show texture border", variable=self.show_border_var, command=self.redraw).pack(fill=tk.X, padx=2, pady=(0, 2))

        help_frame = ttk.LabelFrame(parent, text="Help")
        help_frame.pack(fill=tk.BOTH, expand=True, padx=(0, 4), pady=4)

        help_scrollbar = ttk.Scrollbar(help_frame)
        help_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        help_text = tk.Text(help_frame, wrap=tk.WORD, width=30, height=10,
                            yscrollcommand=help_scrollbar.set,
                            font=self.ui_font, state=tk.DISABLED,
                            relief=tk.FLAT, highlightthickness=0,
                            background=self.colors["field"],
                            foreground=self.colors["fg"],
                            insertbackground=self.colors["fg"])
        help_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        help_scrollbar.config(command=help_text.yview)

        help_content = (
            "**LMB** on glyph on texture - select.\n\n"
            "**Drag corner handle** - resize rectangle.\n"
            "**Drag glyph** - move rectangle.\n\n"
            "Character is the glyph's 16-bit character code; type one character, U+XXXX, 0xXXXX or a decimal value.\n\n"
            "Pixel X/Y, Cell Width/Height, Offset X/Y and Advance Width are shown in atlas pixels. "
            "Enable **Display normalized UV** to view and edit the original normalized float values instead.\n\n"
            "**Import texture** replaces the DXT3 atlas from a PNG or DDS. "
            "Small.bin expects 128x256, Big.bin expects 256x256; other sizes can be nearest-neighbour resized. "
            "DDS: DXT1/3/5 and 32-bit RGBA. Glyph UV metrics stay valid because dimensions are not changed.\n\n"
            "**Export texture** saves the atlas as a PNG.\n\n"
            "The editor preserves the raw file, pointer table, texture header and compressed atlas. Save changes only after testing in-game.\n\n"
            "Code updates change the documented U16 glyph-code field. The 128-entry pointer table is shown for inspection and is deliberately not rebuilt automatically."
        )
        help_text.tag_configure("bold", font=self.bold_font)
        help_text.config(state=tk.NORMAL)
        self._insert_marked_text(help_text, help_content)
        help_text.config(state=tk.DISABLED)

    def on_bg_color_changed(self, _event=None):
        self.redraw()

    def on_uv_display_changed(self) -> None:
        """Switch property fields between atlas pixels and stored UV floats."""
        uv_labels = {
            "pixel_x": "Atlas U:", "pixel_y": "Atlas V:",
            "cell_width": "Cell Width:", "cell_height": "Cell Height:",
            "offset_x": "Offset X:", "offset_y": "Offset Y:",
            "advance": "Advance Width:",
        }
        pixel_labels = {
            "pixel_x": "Pixel X:", "pixel_y": "Pixel Y:",
            "cell_width": "Cell Width:", "cell_height": "Cell Height:",
            "offset_x": "Offset X:", "offset_y": "Offset Y:",
            "advance": "Advance Width:",
        }
        for name, text in (uv_labels if self.uv_mode_var.get() else pixel_labels).items():
            self.property_labels[name].configure(text=text)
        if self.selected_index is not None:
            self.select_glyph(self.selected_index)

    @staticmethod
    def _insert_marked_text(text_widget, content):
        """Insert content into a Text widget; fragments wrapped in **...** get the
        "bold" tag (the markers themselves are not shown)."""
        for i, chunk in enumerate(re.split(r"\*\*(.+?)\*\*", content, flags=re.S)):
            if chunk:
                text_widget.insert(tk.END, chunk, ("bold",) if i % 2 else ())

    def _report_unexpected_error(self, title: str, error: BaseException) -> None:
        """Show an unexpected exception (most likely a bug) and print its traceback
        to the console so it is not hidden behind a message box."""
        traceback.print_exc()
        messagebox.showerror(
            title,
            f"Unexpected error ({type(error).__name__}):\n{error}\n\n"
            "Details were printed to the console.",
        )

    def _mark_modified(self, message: str) -> None:
        self.dirty = True
        self.status_var.set(f"{message} (not saved)")

    def _confirm_discard_or_save(self) -> bool:
        """Ask what to do with unsaved edits. True = safe to continue (nothing to
        save, saved, or discarded); False = cancelled or the save did not happen."""
        if not self.dirty or self.font is None:
            return True
        answer = messagebox.askyesnocancel("Unsaved changes",
                                           "You have unsaved changes.\n\nSave before continuing?")
        if answer is None:          # Cancel
            return False
        if answer:                  # Yes: continue only if the save really succeeded
            return self.save()
        return True                 # No: discard

    def open_file(self) -> None:
        path = filedialog.askopenfilename(title="Open BLACK bitmap font", filetypes=[("BIN files", "*.bin *.BIN"), ("All files", "*")])
        if path and self._confirm_discard_or_save():
            self.load_file(path)

    def load_paths(self, path: str) -> None:
        """Load the file given on the command line (a missing path is reported)."""
        if os.path.exists(path):
            self.load_file(path)
        else:
            self.status_var.set(f"File not found: {path}")

    def load_file(self, path: os.PathLike[str] | str) -> bool:
        """Load a font. True on success; a failure is reported and leaves the current font untouched."""
        try:
            font = BlackFont(path)
            image = font.texture_image()
        except EXPECTED_LOAD_ERRORS as exc:
            messagebox.showerror("Open error", str(exc))
            return False
        except Exception as exc:
            self._report_unexpected_error("Critical open error", exc)
            return False
        # Re-opening the same file keeps the selected glyph.
        keep_selection = (self.font is not None
                          and os.path.abspath(font.path) == os.path.abspath(self.font.path)
                          and self.selected_index is not None
                          and self.selected_index < font.glyph_count)
        self.font, self.image, self.dirty = font, image, False
        if not keep_selection:
            self.selected_index = None
        self._tex_photo_zoom = None
        if self.zoom > self._max_zoom():
            self.zoom = self._max_zoom()
            self.zoom_var.set(self.zoom)
        self.root.title(f"BLACK Bitmap Font Editor — {font.path.name}")
        self.info_var.set(
            f"File: {font.path.name}\nAtlas: {font.texture_name} ({font.texture_width}×{font.texture_height})\n"
            f"Format: {FORMAT_NAMES.get(font.texture_format, 'unknown')} (0x{font.texture_format:02X})\nNominal size: {font.nominal_size:.2f}px\n"
            f"Glyph record slots: {font.glyph_count}\nRecord alignment tail: 0x{font.glyph_tail_bytes:X}\n"
            f"Texture header: 0x{font.texture_offset:X}\n"
            f"Launcher check: {'OK' if not font.launcher_issues() else 'would reject — ' + '; '.join(font.launcher_issues())}")
        self.glyph_list.delete(0, tk.END)
        for glyph in font.glyphs():
            self.glyph_list.insert(tk.END, glyph.label)
        self.pointer_list.delete(0, tk.END)
        for i in range(128):
            raw, target = font.pointer(i), font.pointer_target(i)
            suffix = f"glyph {target:03d}" if target is not None else "outside glyph records"
            self.pointer_list.insert(tk.END, f"{i:03d}: 0x{raw:04X}  → {suffix}")
        if keep_selection:
            self.select_glyph(self.selected_index)      # also redraws the canvas
        else:
            self.clear_properties()
            self.redraw()
        self.status_var.set(f"Loaded {font.path} — {font.glyph_count} physical glyph records")
        return True

    def select_from_list(self, _event=None) -> None:
        selection = self.glyph_list.curselection()
        if selection:
            self.select_glyph(selection[0])

    def select_from_pointer(self, _event=None) -> None:
        selection = self.pointer_list.curselection()
        if selection and self.font:
            target = self.font.pointer_target(selection[0])
            if target is not None:
                self.select_glyph(target)

    def select_glyph(self, index: int) -> None:
        if not self.font:
            return
        self.selected_index = index
        glyph = self.font.glyph(index)
        values = self.property_vars
        values["record"].set(f"#{index} at 0x{glyph.offset:X}")
        # Keep the editor field unambiguous: it is the on-disk U16 character
        # code.  Only the navigation list uses the rendered character.
        values["character"].set(f"U+{glyph.code:04X}")
        if self.uv_mode_var.get():
            display_values = (glyph.u, glyph.v, glyph.width, glyph.height,
                              glyph.bearing_x, glyph.bearing_y, glyph.advance)
        else:
            display_values = (
                glyph.u * self.font.texture_width,
                glyph.v * self.font.texture_height,
                glyph.width * self.font.texture_width,
                glyph.height * self.font.texture_height,
                glyph.bearing_x * self.font.texture_width,
                glyph.bearing_y * self.font.texture_height,
                glyph.advance * self.font.texture_width,
            )
        for name, value in zip(("pixel_x", "pixel_y", "cell_width", "cell_height", "offset_x", "offset_y", "advance"), display_values):
            values[name].set(f"{value:.8g}")
        self.glyph_list.selection_clear(0, tk.END)
        self.glyph_list.selection_set(index)
        self.glyph_list.see(index)
        self.redraw()

    def clear_properties(self) -> None:
        for var in self.property_vars.values():
            var.set("")

    def glyph_pixels(self, glyph: Glyph) -> tuple[float, float, float, float]:
        assert self.font is not None
        x0, y0 = glyph.u * self.font.texture_width, glyph.v * self.font.texture_height
        return x0, y0, x0 + glyph.width * self.font.texture_width, y0 + glyph.height * self.font.texture_height

    def drawable_pixels(self, glyph: Glyph) -> tuple[float, float, float, float] | None:
        """Return a visible atlas rectangle, excluding sentinel/empty records."""
        assert self.font is not None
        if glyph.u < 0 or glyph.v < 0:  # launcher: negative U/V = record without a cell (the space)
            return None
        rect = self.glyph_pixels(glyph)
        x0, y0, x1, y1 = rect
        if not all(math.isfinite(value) for value in rect):
            return None
        if x1 <= x0 or y1 <= y0:
            return None
        # Allow glyphs to extend beyond texture bounds (don't clip them)
        return rect

    @staticmethod
    def parse_code(value: str) -> int:
        text = value.strip()
        if text.upper().startswith("U+"):
            code = int(text[2:], 16)
        elif text.lower().startswith("0x"):
            code = int(text, 16)
        elif len(text) == 1:
            code = ord(text)
        else:
            code = int(text, 10)
        if not 0 <= code <= 0xFFFF:
            raise ValueError("Code must fit in U+0000…U+FFFF")
        return code

    def apply_properties(self) -> None:
        if not self.font or self.selected_index is None:
            return
        try:
            old = self.font.glyph(self.selected_index)
            pixel_x, pixel_y, cell_width, cell_height, offset_x, offset_y, advance = (
                float(self.property_vars[name].get()) for name in
                ("pixel_x", "pixel_y", "cell_width", "cell_height", "offset_x", "offset_y", "advance"))
            if not all(math.isfinite(value) for value in
                       (pixel_x, pixel_y, cell_width, cell_height, offset_x, offset_y, advance)):
                raise ValueError("Metric values must be finite, plausible floats")
            if cell_width < 0 or cell_height < 0:
                raise ValueError("Cell Width and Cell Height must not be negative")
            if not all(abs(value) < 1000 for value in
                       (pixel_x, pixel_y, cell_width, cell_height, offset_x, offset_y, advance)):
                raise ValueError("Metric values must be finite, plausible floats")
            if self.uv_mode_var.get():
                updated = Glyph(old.index, old.offset, pixel_x, pixel_y, cell_width, cell_height,
                                offset_x, offset_y, advance,
                                self.parse_code(self.property_vars["character"].get()))
            else:
                updated = Glyph(old.index, old.offset,
                    pixel_x / self.font.texture_width, pixel_y / self.font.texture_height,
                    cell_width / self.font.texture_width, cell_height / self.font.texture_height,
                    offset_x / self.font.texture_width, offset_y / self.font.texture_height,
                    advance / self.font.texture_width, self.parse_code(self.property_vars["character"].get()))
        except ValueError as exc:
            messagebox.showerror("Invalid glyph values", str(exc))
            return
        self.font.write_glyph(updated)
        self.glyph_list.delete(self.selected_index)
        self.glyph_list.insert(self.selected_index, updated.label)
        self.select_glyph(updated.index)
        self._mark_modified(f"Glyph #{updated.index} changed")

    def _max_zoom(self) -> int:
        """Highest zoom whose scaled texture stays within MAX_DISPLAY_PIXELS."""
        if self.image is None:
            return MAX_ZOOM
        w, h = self.image.size
        return max(MIN_ZOOM, min(MAX_ZOOM, int((MAX_DISPLAY_PIXELS / (w * h)) ** 0.5)))

    def change_zoom(self) -> None:
        try:
            value = int(self.zoom_var.get())
        except (tk.TclError, ValueError):       # empty / non-numeric text in the box
            value = self.zoom
        new_zoom = max(MIN_ZOOM, min(self._max_zoom(), value))
        self.zoom_var.set(new_zoom)
        if new_zoom != self.zoom:
            self.zoom = new_zoom
            self.redraw()

    def redraw(self, preview: tuple[float, float, float, float] | None = None) -> None:
        self.canvas.delete("all")
        if not self.font or not self.image:
            return
        z = self.zoom
        w, h = self.image.size
        if self._tex_photo_zoom != z:
            scaled = self.image.resize((w * z, h * z), Image.Resampling.NEAREST)
            self.photo = ImageTk.PhotoImage(scaled)
            self._tex_photo_zoom = z

        # Draw background if enabled
        if self.bg_enabled.get():
            bg_color = self.bg_var.get()
            self.canvas.create_rectangle(0, 0, w * z, h * z, fill=bg_color, outline="", tags="background")

        self.canvas.create_image(0, 0, image=self.photo, anchor=tk.NW, tags="atlas")
        self.canvas.configure(scrollregion=(0, 0, w * z, h * z))

        # Draw texture border if enabled
        if self.show_border_var.get():
            self.canvas.create_rectangle(0, 0, w * z, h * z, outline="#41484f", width=1, tags="border")

        # The reference editor displays the complete glyph map: red outlines
        # for every valid glyph and cyan for the selected record.
        selected_rect = None
        for glyph in self.font.glyphs():
            rect = preview if glyph.index == self.selected_index and preview is not None else self.drawable_pixels(glyph)
            if rect is None:
                continue
            x0, y0, x1, y1 = rect
            selected = glyph.index == self.selected_index
            self.canvas.create_rectangle(x0 * z, y0 * z, x1 * z, y1 * z,
                                         outline=PALETTE["handle"] if selected else "#ff3030",
                                         width=2 if selected else 1, tags=(f"glyph_{glyph.index}",))
            if selected:
                selected_rect = rect
        if selected_rect is None:
            return
        x0, y0, x1, y1 = selected_rect
        for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
            self.canvas.create_rectangle(x * z - HANDLE_SIZE, y * z - HANDLE_SIZE,
                                        x * z + HANDLE_SIZE, y * z + HANDLE_SIZE,
                                        fill=PALETTE["handle"], outline="black", tags="handle")

    def _redraw_selected_glyph(self, x0, y0, x1, y1):
        """Optimized redraw for only the selected glyph during drag operations."""
        z = self.zoom
        self.canvas.delete(f"glyph_{self.selected_index}")
        self.canvas.delete("handle")

        # Draw selected glyph rectangle
        self.canvas.create_rectangle(
            x0 * z, y0 * z, x1 * z, y1 * z,
            outline=PALETTE["handle"], width=2, tags=(f"glyph_{self.selected_index}",)
        )

        # Draw handles
        for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
            self.canvas.create_rectangle(x * z - HANDLE_SIZE, y * z - HANDLE_SIZE,
                                        x * z + HANDLE_SIZE, y * z + HANDLE_SIZE,
                                        fill=PALETTE["handle"], outline="black", tags="handle")

    def canvas_point(self, event: tk.Event) -> tuple[float, float]:
        return self.canvas.canvasx(event.x) / self.zoom, self.canvas.canvasy(event.y) / self.zoom

    def canvas_press(self, event: tk.Event) -> None:
        self.canvas.focus_set()
        if not self.font:
            return
        x, y = self.canvas_point(event)
        if self.selected_index is not None:
            glyph = self.font.glyph(self.selected_index)
            rect = self.drawable_pixels(glyph)
            if rect is not None:
                x0, y0, x1, y1 = rect
                tolerance = HANDLE_SIZE / self.zoom + 1
                corners = (("x0y0", x0, y0), ("x1y0", x1, y0), ("x0y1", x0, y1), ("x1y1", x1, y1))
                for mode, cx, cy in corners:
                    if abs(x - cx) <= tolerance and abs(y - cy) <= tolerance:
                        self.drag_mode, self.drag_origin = mode, (x, y, rect)
                        return
                if x0 <= x <= x1 and y0 <= y <= y1:
                    self.drag_mode, self.drag_origin = "move", (x, y, rect)
                    return
        candidates: list[tuple[float, int]] = []
        for glyph in self.font.glyphs():
            rect = self.drawable_pixels(glyph)
            if rect is None:
                continue
            x0, y0, x1, y1 = rect
            if x0 <= x <= x1 and y0 <= y <= y1:
                candidates.append(((x1 - x0) * (y1 - y0), glyph.index))
        if candidates:
            self.select_glyph(min(candidates)[1])
        self.drag_mode = None

    def dragged_rect(self, event: tk.Event) -> tuple[float, float, float, float] | None:
        if not self.font or not self.drag_mode or not self.drag_origin:
            return None
        x, y = self.canvas_point(event)
        ox, oy, (x0, y0, x1, y1) = self.drag_origin
        dx, dy = x - ox, y - oy

        # Store original rectangle and dimensions for move mode
        orig_x0, orig_y0 = x0, y0
        orig_width = x1 - x0
        orig_height = y1 - y0

        if self.drag_mode == "move":
            x0, x1, y0, y1 = x0 + dx, x1 + dx, y0 + dy, y1 + dy
        elif self.drag_mode == "x0y0": x0, y0 = x, y
        elif self.drag_mode == "x1y0": x1, y0 = x, y
        elif self.drag_mode == "x0y1": x0, y1 = x, y
        elif self.drag_mode == "x1y1": x1, y1 = x, y

        # Clamp to the texture
        tex_w, tex_h = self.font.texture_width, self.font.texture_height
        x0 = max(0, min(x0, tex_w))
        x1 = max(0, min(x1, tex_w))
        y0 = max(0, min(y0, tex_h))
        y1 = max(0, min(y1, tex_h))

        # In move mode, preserve original dimensions after clamping
        if self.drag_mode == "move":
            # Slide the whole rectangle and stop it at the texture edges, keeping its size
            # (clamping each edge separately - and then patching one - lets it escape when
            # the cursor is dragged far outside the atlas).
            x0 = min(max(orig_x0 + dx, 0), max(tex_w - orig_width, 0))
            y0 = min(max(orig_y0 + dy, 0), max(tex_h - orig_height, 0))
            x1, y1 = x0 + orig_width, y0 + orig_height

        # Truncate coordinates to integers to avoid decimal values
        x0_rounded = int(min(x0, x1))
        y0_rounded = int(min(y0, y1))
        x1_rounded = int(max(x0, x1))
        y1_rounded = int(max(y0, y1))
        return x0_rounded, y0_rounded, x1_rounded, y1_rounded

    def canvas_drag(self, event: tk.Event) -> None:
        rect = self.dragged_rect(event)
        if rect:
            x0, y0, x1, y1 = rect
            # Update UI fields without saving to data during drag
            if self.uv_mode_var.get():
                self.property_vars["pixel_x"].set(f"{x0 / self.font.texture_width:.8g}")
                self.property_vars["pixel_y"].set(f"{y0 / self.font.texture_height:.8g}")
                self.property_vars["cell_width"].set(f"{(x1 - x0) / self.font.texture_width:.8g}")
                self.property_vars["cell_height"].set(f"{(y1 - y0) / self.font.texture_height:.8g}")
            else:
                self.property_vars["pixel_x"].set(f"{x0:.0f}")
                self.property_vars["pixel_y"].set(f"{y0:.0f}")
                self.property_vars["cell_width"].set(f"{x1 - x0:.0f}")
                self.property_vars["cell_height"].set(f"{y1 - y0:.0f}")
            # Optimized redraw - only update selected glyph
            self._redraw_selected_glyph(x0, y0, x1, y1)

    def canvas_release(self, event: tk.Event) -> None:
        rect = self.dragged_rect(event)
        if self.drag_origin:
            _, _, orig_rect = self.drag_origin
        self.drag_mode = None
        self.drag_origin = None
        if not rect or not self.font or self.selected_index is None:
            return
        if rect != orig_rect:
            glyph = self.font.glyph(self.selected_index)
            x0, y0, x1, y1 = rect
            glyph.u, glyph.v = x0 / self.font.texture_width, y0 / self.font.texture_height
            glyph.width, glyph.height = (x1 - x0) / self.font.texture_width, (y1 - y0) / self.font.texture_height
            self.font.write_glyph(glyph)
            self.select_glyph(glyph.index)
            self._mark_modified(f"Glyph #{glyph.index} rectangle changed")
        else:
            # Plain click, drag back to the start: restore the fields/canvas
            self.select_glyph(self.selected_index)

    def export_texture(self) -> None:
        if not self.image:
            messagebox.showinfo("No font", "Open a BLACK bitmap font first.")
            return
        path = filedialog.asksaveasfilename(title="Export atlas", defaultextension=".png",
                                            filetypes=[("PNG image", "*.png"), ("All files", "*")])
        if not path:
            return
        try:
            self.image.save(path, "PNG")
        except OSError as exc:
            messagebox.showerror("Export error", str(exc))
            return
        self.status_var.set(f"Atlas exported to {path}")

    def import_texture(self) -> None:
        """Replace the DXT3 atlas from a PNG or DDS of matching dimensions.

        Stock fonts (Small.bin 128x256, Big.bin 256x256) store a single DXT3
        mip with no lower levels. The texture header and all glyph metrics are
        preserved so UV coordinates remain valid.
        """
        if not self.font:
            messagebox.showinfo("No font", "Open a BLACK bitmap font first.")
            return
        path = filedialog.askopenfilename(
            title="Import texture (PNG or DDS)",
            filetypes=[
                ("Texture images", "*.png *.PNG *.dds *.DDS"),
                ("PNG image", "*.png *.PNG"),
                ("DDS texture", "*.dds *.DDS"),
                ("All files", "*"),
            ],
        )
        if not path:
            return
        try:
            if Path(path).suffix.lower() == ".dds":
                image = load_dds_image(path)
            else:
                with Image.open(path) as img:
                    image = img.convert("RGBA")
        except EXPECTED_LOAD_ERRORS as exc:
            messagebox.showerror("Import error", str(exc))
            return
        except Exception as exc:
            self._report_unexpected_error("Critical import error", exc)
            return
        self._apply_texture(image, Path(path).name)

    def _apply_texture(self, image: Image.Image, source: str, note: str = "") -> bool:
        """Write *image* into the atlas (asking about a size mismatch). True on success."""
        tw, th = self.font.texture_width, self.font.texture_height
        if image.size != (tw, th):
            if not messagebox.askyesno(
                "Size mismatch",
                f"Image is {image.size[0]}x{image.size[1]}, atlas is {tw}x{th}.\n\n"
                "Resize with nearest-neighbour and import?",
            ):
                return False
            image = image.resize((tw, th), Image.Resampling.NEAREST)
        try:
            self.font.replace_texture(image)
            self.image = self.font.texture_image()
        except EXPECTED_LOAD_ERRORS as exc:
            messagebox.showerror("Import error", str(exc))
            return False
        except Exception as exc:
            self._report_unexpected_error("Critical import error", exc)
            return False
        self._tex_photo_zoom = None
        self.redraw()
        self._mark_modified(f"Imported {source} -> DXT3 {tw}x{th}{note}")
        return True

    # ---- saving ------------------------------------------------------------

    def save(self) -> bool:
        if not self.font:
            messagebox.showinfo("No font", "Open a BLACK bitmap font first.")
            return False
        if not self.dirty:
            self.status_var.set("No changes to save")
            return True
        if not messagebox.askyesno(
                "Overwrite font",
                f"Overwrite original file?\n{self.font.path}\n\n"
                "Making a backup copy first is recommended."):
            return False
        try:
            self.font.save(self.font.path)
        except OSError as exc:
            messagebox.showerror("Save error", str(exc))
            return False
        self.dirty = False
        self.status_var.set(f"Saved {self.font.path}")
        return True

    def save_as(self) -> bool:
        if not self.font:
            messagebox.showinfo("No font", "Open a BLACK bitmap font first.")
            return False
        path = filedialog.asksaveasfilename(title="Save BLACK bitmap font", defaultextension=".bin",
                                            filetypes=[("BIN files", "*.bin *.BIN"), ("All files", "*")])
        if not path:
            return False
        try:
            self.font.save(path)
        except OSError as exc:
            messagebox.showerror("Save error", str(exc))
            return False
        self.font.path = Path(path)             # later "Save (overwrite)" targets this file
        self.dirty = False
        self.root.title(f"BLACK Bitmap Font Editor — {self.font.path.name}")
        self.status_var.set(f"Saved {path}")
        return True

    def close(self) -> None:
        if self._confirm_discard_or_save():
            self.root.destroy()


def main() -> None:
    root = tk.Tk()
    app = BlackFontEditor(root)
    if len(sys.argv) > 1:
        root.after_idle(app.load_paths, sys.argv[1])
    root.mainloop()


if __name__ == "__main__":
    main()
