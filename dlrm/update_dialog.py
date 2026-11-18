"""The Check for updates window: this version vs GitHub, release notes, tools, and Update all."""

import queue
import re
import threading
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

from . import engine, theme, updater
from .common import APP_NAME, APP_VERSION, IS_MAC
from .theme import UI

_S = 0 if not IS_MAC else 3


def _plain(md):
    """Release notes are Markdown; show them as readable plain text."""
    md = re.sub(r"\*\*(.+?)\*\*", r"\1", md)
    md = re.sub(r"`([^`]+)`", r"\1", md)
    md = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", md)
    return md.replace("\r\n", "\n").strip()


class UpdateDialog(tk.Toplevel):
    _open = None

    @classmethod
    def show(cls, app):
        if cls._open is not None and cls._open.winfo_exists():
            cls._open.lift()
            cls._open.focus_force()
            return cls._open
        cls._open = cls(app)
        return cls._open

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.pal = app.pal
        p = self.pal
        self.title("Check for updates")
        self.minsize(600, 520)
        # open centred over the main window (kept on screen)
        app.update_idletasks()
        w, h = 760, 640
        x = app.winfo_rootx() + (app.winfo_width() - w) // 2
        y = app.winfo_rooty() + (app.winfo_height() - h) // 2
        x = min(max(x, 0), max(self.winfo_screenwidth() - w, 0))
        y = min(max(y, 0), max(self.winfo_screenheight() - h - 40, 0))
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.configure(bg=p["bg"])
        self.transient(app)
        self.status = None
        self.busy = False
        self._q = queue.Queue()          # worker threads hand UI work to the Tk thread through this
        self.after(100, self._pump)

        box = ttk.Frame(self, padding=(18, 14, 18, 14))
        box.pack(fill="both", expand=True)
        ttk.Label(box, text=f"{APP_NAME} {APP_VERSION}  (this version)",
                  font=(UI, 12 + _S, "bold")).pack(anchor="w")
        self.headline = ttk.Label(box, text="Checking…", font=(UI, 15 + _S, "bold"))
        self.headline.pack(anchor="w", pady=(12, 2))
        self.sub = ttk.Label(box, text="", style="Muted.TLabel", wraplength=700, justify="left")
        self.sub.pack(anchor="w")

        notes_frame = ttk.Frame(box)
        notes_frame.pack(fill="both", expand=True, pady=(10, 10))
        self.notes = tk.Text(notes_frame, wrap="word", height=10, relief="flat", padx=12, pady=10,
                             font=(UI, 9 + _S), bg=p["field"], fg=p["text"], insertbackground=p["text"],
                             highlightthickness=1, highlightbackground=p["line"], highlightcolor=p["line"])
        sb = ttk.Scrollbar(notes_frame, command=self.notes.yview)
        self.notes.configure(yscrollcommand=sb.set, state="disabled")
        self.notes.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        ttk.Label(box, text="Updates", font=(UI, 12 + _S, "bold")).pack(anchor="w")
        self.tools_frame = ttk.Frame(box)
        self.tools_frame.pack(fill="x", pady=(4, 8))

        self.summary = ttk.Label(box, text="", style="Muted.TLabel")
        self.summary.pack(anchor="w", pady=(2, 6))
        self.bar = ttk.Progressbar(box, mode="indeterminate")
        self.bar.pack(fill="x")

        btns = ttk.Frame(box)
        btns.pack(fill="x", pady=(12, 0))
        self.update_btn = ttk.Button(btns, text="Update all", command=self.update_all)
        self.update_btn.pack(side="left")
        self.check_btn = ttk.Button(btns, text="Check again", command=self.check)
        self.check_btn.pack(side="left", padx=8)
        self.page_btn = ttk.Button(btns, text="Release page", command=self.open_page)
        self.page_btn.pack(side="left")
        ttk.Button(btns, text="Close", command=self.close).pack(side="right")

        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda e: self.close())
        self.after(50, lambda: theme.title_bar(self, app.dark))
        self.check()

    # ------------------------------------------------------------ helpers
    def _ui(self, fn, *a):
        """Ask the Tk thread to run fn (safe to call from a worker thread)."""
        self._q.put((fn, a))

    def _pump(self):
        try:
            while True:
                fn, a = self._q.get_nowait()
                fn(*a)
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(100, self._pump)

    def _set_busy(self, busy, msg=""):
        self.busy = busy
        for b in (self.check_btn,):
            b.state(["disabled"] if busy else ["!disabled"])
        if busy:
            self.update_btn.state(["disabled"])
            for b in getattr(self, "row_btns", []):
                b.state(["disabled"])
            self.bar.configure(mode="indeterminate")
            self.bar.start(12)
        else:
            self.bar.stop()
            self.bar.configure(mode="determinate", value=0)
        if msg:
            self.summary.configure(text=msg)

    def _set_notes(self, text):
        self.notes.configure(state="normal")
        self.notes.delete("1.0", "end")
        self.notes.insert("1.0", text)
        self.notes.configure(state="disabled")

    # ------------------------------------------------------------ checking
    def check(self):
        if self.busy:
            return
        self._set_busy(True, "Checking GitHub and the tool websites…")
        self.headline.configure(text="Checking…", foreground=self.pal["text"])

        def work():
            try:
                st = updater.check_all()
            except Exception as e:
                st = {"online": False, "app": {"current": APP_VERSION, "latest": "", "date": "", "notes": "",
                                               "url": "", "via": "", "error": str(e), "update": False},
                      "tools": []}
            self._ui(self._show, st)
        threading.Thread(target=work, daemon=True).start()

    def _show(self, st):
        self.status = st
        p, a = self.pal, st["app"]
        green, orange, red = p["tags"]["OK"], p["tags"]["META"], p["tags"]["AI"]
        if a["error"]:
            self.headline.configure(text="Couldn't check for a new version", foreground=red)
            self.sub.configure(text=a["error"])
        elif a["update"]:
            self.headline.configure(text=f"Version {a['latest']} is available", foreground=orange)
            self.sub.configure(text=f"Latest on GitHub: {a['latest']}, released {a['date'] or '?'}  ·  {a['via']}"
                                    f"  ·  you have {APP_VERSION}")
        else:
            self.headline.configure(text=f"You're on the latest version ({APP_VERSION}) ✓", foreground=green)
            self.sub.configure(text=f"Latest on GitHub: {a['latest']}, released {a['date'] or '?'}  ·  {a['via']}")
        if a["notes"]:
            self._set_notes(f"What's new in {a['latest']}:\n\n{_plain(a['notes'])}")
        else:
            self._set_notes("No release notes to show." if not a["error"] else
                            "Release notes appear here once GitHub can be reached.")

        for w in self.tools_frame.winfo_children():
            w.destroy()
        self.row_btns = []
        rows = [{"key": "app", "name": f"{APP_NAME} (this app, from GitHub)", "current": APP_VERSION,
                 "latest": a["latest"], "update": a["update"], "error": "couldn't check" if a["error"] else ""}]
        rows += st["tools"]
        for row, t in enumerate(rows):
            ttk.Label(self.tools_frame, text=t["name"]).grid(row=row, column=0, sticky="w", padx=(0, 24), pady=2)
            ttk.Label(self.tools_frame, text=t["current"], style="Muted.TLabel").grid(row=row, column=1, sticky="w",
                                                                                      padx=(0, 24))
            if t["error"]:
                text, color = t["error"], red
            elif t["update"]:
                text, color = f"update available: {t['latest']}", orange
            elif t["latest"]:
                text, color = "up to date ✓", green
            else:
                text, color = "not checked", p["muted"]
            ttk.Label(self.tools_frame, text=text, foreground=color).grid(row=row, column=2, sticky="w",
                                                                         padx=(0, 16))
            b = ttk.Button(self.tools_frame, text="Update", width=8,
                           command=lambda k=t["key"]: self.update_items([k]))
            b.grid(row=row, column=3, sticky="e")
            b.state(["!disabled"] if t["update"] else ["disabled"])
            self.row_btns.append(b)
        self.tools_frame.columnconfigure(2, weight=1)

        n_tools = sum(1 for t in st["tools"] if t["update"])
        n = n_tools + (1 if a["update"] else 0)
        if not st["online"]:
            msg = "No internet connection. Connect and click Check again."
        elif n == 0:
            msg = "Everything is up to date."
        else:
            parts = ([f"{APP_NAME} {a['latest']}"] if a["update"] else []) + \
                    [t["name"] for t in st["tools"] if t["update"]]
            msg = (f"{n} update{'s' if n != 1 else ''} available: {', '.join(parts)}. "
                   f"Click Update all, or Update next to just one.")
        self._set_busy(False, msg)
        self.update_btn.state(["!disabled"] if n else ["disabled"])
        self.page_btn.state(["!disabled"] if a.get("url") else ["disabled"])

    # ------------------------------------------------------------ updating
    def update_all(self):
        st = self.status
        if not st:
            return
        keys = (["app"] if st["app"]["update"] else []) + [t["key"] for t in st["tools"] if t["update"]]
        self.update_items(keys)

    def update_items(self, keys):
        """Update the chosen components ("app", "ffmpeg", "exiftool", "rules"). The app goes last: it restarts."""
        st = self.status
        if self.busy or not st or not keys:
            return
        app_update = "app" in keys and st["app"]["update"]
        if app_update and self.app.busy:
            messagebox.showinfo(APP_NAME, "Wait until the files in the main window are finished, then try "
                                          "again.", parent=self)
            return
        tool_keys = [k for k in keys if k != "app"]
        self._set_busy(True, "Updating…")

        def work():
            lines = updater.install_tools(st, progress=lambda m: self._ui(lambda: self.summary.configure(text=m)),
                                          only=tool_keys) if tool_keys else []
            engine.reset_tool_cache()
            err = None
            if app_update:
                v = st["app"]["latest"]
                self._ui(lambda: self.summary.configure(text=f"Downloading {APP_NAME} {v}…"))
                err = updater.install_app_update(v)
            self._ui(self._updated, lines, app_update, err)
        threading.Thread(target=work, daemon=True).start()

    def _updated(self, lines, app_update, err):
        self._set_busy(False)
        self.app._show_versions()
        if app_update and err == "":
            # Windows: the helper swaps in the new version once we quit, then starts it again.
            self.app.destroy()
            return
        if app_update and err:
            (messagebox.showinfo if (IS_MAC and err.startswith("Version ")) else messagebox.showerror)(
                APP_NAME, err, parent=self)
        failed = [l for l in lines if "failed" in l]
        self.check()
        if failed:
            messagebox.showwarning(APP_NAME, "\n".join(failed), parent=self)

    # ------------------------------------------------------------ other buttons
    def open_page(self):
        url = (self.status or {}).get("app", {}).get("url")
        if url:
            webbrowser.open(url)

    def close(self):
        UpdateDialog._open = None
        self.destroy()
