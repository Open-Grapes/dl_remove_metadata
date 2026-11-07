"""Light and dark looks for the window (ttk). Light uses the native Windows style; dark uses 'clam' recoloured."""

import ctypes
import os
import sys
import tkinter as tk
from tkinter import ttk

IS_WIN, IS_MAC = os.name == "nt", sys.platform == "darwin"
UI = "Segoe UI" if IS_WIN else "Helvetica Neue" if IS_MAC else "DejaVu Sans"
MONO = "Consolas" if IS_WIN else "Menlo" if IS_MAC else "DejaVu Sans Mono"
_S = 0 if IS_WIN else 3   # macOS text needs to be a few points larger to look the same

LIGHT = {
    "dark": False, "bg": "SystemButtonFace" if IS_WIN else "systemWindowBackgroundColor" if IS_MAC else "#ececec", "panel": "#f3f4f6", "field": "#ffffff", "text": "#1d2330",
    "muted": "#555b66", "faint": "#777d88", "line": "#d0d4da", "accent": "#205cd6",
    "tags": {"AI": "#c62828", "META": "#b45309", "CLEAN": "#2e7d32", "OK": "#2e7d32", "FAIL": "#c62828",
             "SKIP": "#6b7280"},
}
DARK = {
    "dark": True, "bg": "#1c1d22", "panel": "#26282f", "field": "#202127", "text": "#e6e7ea",
    "muted": "#a3a8b3", "faint": "#7f8590", "line": "#3a3d46", "accent": "#3b82f6", "hover": "#30333b",
    "pressed": "#383c46", "select": "#2c4a7c",
    "tags": {"AI": "#ff7070", "META": "#f5a524", "CLEAN": "#5cc46a", "OK": "#5cc46a", "FAIL": "#ff7070",
             "SKIP": "#9aa0aa"},
}


def palette(dark):
    return DARK if dark else LIGHT


def apply(root, dark):
    """Restyle every ttk widget under root. Returns the palette used."""
    p = palette(dark)
    style = ttk.Style(root)
    if dark:
        style.theme_use("clam")
        bg, panel, field, text, line = p["bg"], p["panel"], p["field"], p["text"], p["line"]
        style.configure(".", background=bg, foreground=text, fieldbackground=field, bordercolor=line,
                        darkcolor=bg, lightcolor=bg, troughcolor=panel, focuscolor=p["accent"],
                        selectbackground=p["select"], selectforeground=text, insertcolor=text,
                        font=(UI, 9 + _S))
        style.map(".", foreground=[("disabled", p["faint"])])
        style.configure("TButton", background=panel, bordercolor=line, padding=(10, 4), relief="flat")
        style.map("TButton", background=[("pressed", p["pressed"]), ("active", p["hover"]), ("disabled", bg)],
                  bordercolor=[("focus", p["accent"])])
        style.configure("Toolbutton", background=bg, padding=(10, 4), relief="flat")
        style.map("Toolbutton", background=[("selected", p["accent"]), ("active", p["hover"])],
                  foreground=[("selected", "#ffffff")])
        style.configure("TCheckbutton", background=bg, indicatorbackground=field, indicatorforeground=text,
                        upperbordercolor=line, lowerbordercolor=line)
        style.map("TCheckbutton", background=[("active", bg)],
                  indicatorbackground=[("selected", p["accent"]), ("active", p["hover"])],
                  indicatorforeground=[("selected", "#ffffff")])
        style.configure("Treeview", background=field, fieldbackground=field, foreground=text, bordercolor=line)
        style.map("Treeview", background=[("selected", p["select"])], foreground=[("selected", text)])
        style.configure("Treeview.Heading", background=panel, foreground=text, bordercolor=line, relief="flat")
        style.map("Treeview.Heading", background=[("active", p["hover"])])
        style.configure("Vertical.TScrollbar", background=panel, troughcolor=bg, bordercolor=bg,
                        arrowcolor=p["muted"], relief="flat")
        style.map("Vertical.TScrollbar", background=[("active", p["hover"])])
        style.configure("TProgressbar", background=p["accent"], troughcolor=panel, bordercolor=line)
        style.configure("Drop.TLabel", background=panel, bordercolor=line)
    else:
        # ttk keeps settings per theme, so the dark colours above don't leak into the native look.
        names = style.theme_names()
        style.theme_use(next(t for t in ("vista", "aqua", "winnative", "clam") if t in names))
    style.configure("Drop.TLabel", font=(UI, 13 + _S), foreground=p["muted"], anchor="center")
    style.configure("Muted.TLabel", foreground=p["muted"])
    style.configure("Small.TLabel", foreground=p["faint"], font=(UI, 8 + _S))
    style.configure("Title.TLabel", font=(UI, 11 + _S, "bold"))
    style.configure("Treeview", rowheight=26)
    root.configure(bg=p["bg"])
    for w in _toplevels(root):
        try:
            w.configure(bg=p["bg"])
        except tk.TclError:
            pass
        title_bar(w, dark)
    return p


def _toplevels(root):
    out = [root]
    for w in root.winfo_children():
        if isinstance(w, tk.Toplevel):
            out.append(w)
    return out


def title_bar(win, dark):
    """Ask Windows 10/11 to draw this window's title bar dark (or light). (macOS: follows the system.)"""
    if not IS_WIN:
        return
    try:
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        value = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):   # DWMWA_USE_IMMERSIVE_DARK_MODE (newer, older builds)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value),
                                                          ctypes.sizeof(value)) == 0:
                break
        # Redraw the frame so the change shows immediately.
        SWP = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020   # NOSIZE|NOMOVE|NOZORDER|NOACTIVATE|FRAMECHANGED
        ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP)
    except Exception:
        pass
