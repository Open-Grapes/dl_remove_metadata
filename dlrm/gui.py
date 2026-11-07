"""dl_remove_metadata window: remove metadata or check files for AI tags."""

import os
import queue
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import engine, shell, theme, updater
from .common import (APP_DIR, APP_NAME, APP_VERSION, FILE_MANAGER, IS_MAC, IS_WIN, LOG, load_settings,
                     save_settings)
from .theme import MONO, UI

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    BaseTk = TkinterDnD.Tk
except Exception:  # drag and drop unavailable: the window still works with the buttons
    DND_FILES, BaseTk = None, tk.Tk

BADGE = {"AI": "🔺 AI tags", "META": "ⓘ Other details", "CLEAN": "✅ Clean",
         "OK": "✅ Cleaned", "SKIP": "— Skipped", "FAIL": "❌ Failed", "WAIT": "…", "RUN": "⏳ Working"}


def set_icon(win):
    ico = APP_DIR / "assets" / "icon.ico"
    try:
        if ico.exists():
            win.iconbitmap(default=str(ico))
        else:
            import sys
            win.iconbitmap(default=sys.executable)
    except tk.TclError:
        pass


def open_in_explorer(path):
    """Show a file in Explorer / Finder (or open a folder)."""
    p = Path(path)
    try:
        if IS_MAC:
            subprocess.Popen(["open", "-R", str(p)] if p.is_file() else ["open", str(p if p.exists() else p.parent)])
        elif p.is_file():
            subprocess.Popen(["explorer", "/select,", str(p)])
        else:
            os.startfile(str(p if p.exists() else p.parent))
    except OSError:
        pass


def open_file(path):
    try:
        subprocess.Popen(["open", str(path)]) if IS_MAC else os.startfile(str(path))
    except OSError:
        pass


def sound(ok):
    try:
        if IS_WIN:
            import winsound
            winsound.MessageBeep(winsound.MB_OK if ok else winsound.MB_ICONHAND)
        elif IS_MAC:
            name = "Glass" if ok else "Basso"
            subprocess.Popen(["/usr/bin/afplay", f"/System/Library/Sounds/{name}.aiff"])
    except Exception:
        pass


class App(BaseTk):
    def __init__(self, files=(), mode="remove"):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("900x600")
        self.minsize(700, 460)
        set_icon(self)
        self.settings = load_settings()
        self.events = queue.Queue()
        self.busy = False
        self.jobs = []
        self.rows = {}        # tree item -> {"file":..., "result":..., "sel": bool}
        self.mode = tk.StringVar(value=mode)
        self.dark = self.settings.get("theme") == "dark"
        self.pal = theme.apply(self, self.dark)
        self._build()
        self._refresh_outdir()
        self.after(100, self._pump)
        self.after(300, self._startup)
        self.after(60, lambda: theme.title_bar(self, self.dark))
        if IS_MAC:   # files dropped on the Dock icon, or "Open With"
            self.createcommand("::tk::mac::OpenDocument", lambda *paths: self.add(paths))
        if files:
            self.after(400, lambda: self.add(files))

    # ------------------------------------------------------------ layout
    def _build(self):
        top = ttk.Frame(self, padding=(12, 10, 12, 0))
        top.pack(fill="x")
        self.theme_btn = ttk.Button(top, command=self.toggle_theme)
        self.theme_btn.pack(side="left", padx=(0, 14))
        for val, text in (("remove", "Remove Metadata"), ("check", "Check for AI Tags")):
            ttk.Radiobutton(top, text=text, value=val, variable=self.mode, style="Toolbutton",
                            command=self._mode_changed).pack(side="left", padx=(0, 4), ipadx=10, ipady=3)
        ttk.Button(top, text="Clear list", command=self.clear).pack(side="right")

        self.drop = ttk.Label(self, style="Drop.TLabel", relief="groove", padding=18)
        self.drop.pack(fill="x", padx=12, pady=10)

        row = ttk.Frame(self, padding=(12, 0))
        row.pack(fill="x")
        ttk.Button(row, text="Choose Files…", command=self.choose_files).pack(side="left")
        ttk.Button(row, text="Choose Folder…", command=self.choose_folder).pack(side="left", padx=6)
        self.reset_btn = ttk.Button(row, text="Reset", command=self.reset_outdir)
        self.reset_btn.pack(side="right")
        ttk.Button(row, text="Save to…", command=self.choose_outdir).pack(side="right", padx=6)
        self.outdir_lbl = ttk.Label(row, style="Muted.TLabel")
        self.outdir_lbl.pack(side="right")

        mid = ttk.Frame(self, padding=(12, 8))
        mid.pack(fill="both", expand=True)
        cols = ("sel", "badge", "file", "info", "folder")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="extended")
        for c, text, w, st in (("sel", "✓", 34, False), ("badge", "Status", 140, False),
                               ("file", "File", 220, False), ("info", "Result", 330, True),
                               ("folder", "Folder", 260, True)):
            self.tree.heading(c, text=text)
            self.tree.column(c, width=w, stretch=st, anchor="center" if c == "sel" else "w")
        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self._tree_click)
        self.tree.bind("<Double-1>", lambda e: self.details())

        act = ttk.Frame(self, padding=(12, 0))
        act.pack(fill="x")
        self.details_btn = ttk.Button(act, text="Details…", command=self.details)
        self.clean_sel_btn = ttk.Button(act, text="Remove Metadata from Selected", command=self.clean_selected)
        self.open_btn = ttk.Button(act, text=f"Show in {FILE_MANAGER}", command=self.show_selected)
        self.open_btn.pack(side="left")
        self.status = ttk.Label(act, style="Muted.TLabel")
        self.status.pack(side="right")

        bottom = ttk.Frame(self, padding=(12, 8, 12, 10))
        bottom.pack(fill="x")
        self.versions = ttk.Label(bottom, style="Small.TLabel")
        self.versions.pack(side="left")
        self.update_now_btn = ttk.Button(bottom, text="Update Now", command=self.update_now)
        ttk.Button(bottom, text="Check for Updates", command=self.check_updates).pack(side="right")
        self.menu_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bottom, text="Finder Quick Actions" if IS_MAC else "Right-click menu", variable=self.menu_var,
                        command=self.toggle_menu).pack(side="right", padx=10)

        if DND_FILES:
            for w in (self, self.drop, self.tree):
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>", self._on_drop)
        self._mode_changed()
        self._theme_changed()

    # ------------------------------------------------------------ light / dark
    def toggle_theme(self):
        self.dark = not self.dark
        self.settings["theme"] = "dark" if self.dark else "light"
        save_settings(self.settings)          # remembered on this PC
        self.pal = theme.apply(self, self.dark)
        self._theme_changed()

    def _theme_changed(self):
        self.theme_btn.configure(text="Light mode" if self.dark else "Dark mode")
        for k, color in self.pal["tags"].items():
            self.tree.tag_configure(k, foreground=color)
        theme.title_bar(self, self.dark)

    def _mode_changed(self):
        check = self.mode.get() == "check"
        self.drop.configure(text=("Drop files or folders here to check them for AI tags\n(checking only reads files; nothing is changed)"
                                  if check else "Drop videos, audio, images or folders here\nto make clean copies without metadata"))
        self.tree.configure(displaycolumns=("sel", "badge", "file", "info", "folder") if check
                                 else ("badge", "file", "info", "folder"))
        if check:
            self.details_btn.pack(side="left", padx=(6, 0))
            self.clean_sel_btn.pack(side="left", padx=6)
        else:
            self.details_btn.pack_forget()
            self.clean_sel_btn.pack_forget()

    # ------------------------------------------------------------ startup / updates
    def _startup(self):
        shell.clear_quarantine()
        if self.settings.get("menu", True):
            try:
                shell.install_menu()
            except OSError:
                pass
        try:
            self.menu_var.set(shell.menu_installed())
        except OSError:
            pass
        self._show_versions()
        updater.start_background_check()

    def _show_versions(self):
        def work():
            self.events.put(("versions", engine.tool_versions()))
        threading.Thread(target=work, daemon=True).start()
        if updater.update_available():
            self.update_now_btn.configure(text=f"Update Now (v{updater.update_available()})")
            self.update_now_btn.pack(side="right", padx=6)
        else:
            self.update_now_btn.pack_forget()

    def check_updates(self):
        self.status.configure(text="Checking for updates…")

        def work():
            msg = updater.run_update()
            engine.reset_tool_cache()
            self.events.put(("updated", msg))
        threading.Thread(target=work, daemon=True).start()

    def update_now(self):
        v = updater.update_available()
        if not v or self.busy:
            return
        self.status.configure(text=f"Downloading {APP_NAME} {v}…")

        def work():
            self.events.put(("app_update", updater.install_app_update(v)))
        threading.Thread(target=work, daemon=True).start()

    def toggle_menu(self):
        try:
            if self.menu_var.get():
                shell.install_menu()
                self.settings["menu"] = True
            else:
                shell.remove_menu()
                self.settings["menu"] = False
            save_settings(self.settings)
        except OSError as e:
            messagebox.showerror(APP_NAME, f"Couldn't change the right-click menu:\n{e}")

    # ------------------------------------------------------------ output folder
    def _refresh_outdir(self):
        d = self.settings.get("outdir")
        if d and not Path(d).is_dir():
            d = None
        self.outdir = d
        self.outdir_lbl.configure(text=f"Save to: {d}" if d else "Save to: next to the originals")
        self.reset_btn.state(["!disabled"] if d else ["disabled"])

    def choose_outdir(self):
        d = filedialog.askdirectory(title="Save clean copies to")
        if d:
            self.settings["outdir"] = str(Path(d))
            save_settings(self.settings)
            self._refresh_outdir()

    def reset_outdir(self):
        self.settings.pop("outdir", None)
        save_settings(self.settings)
        self._refresh_outdir()

    # ------------------------------------------------------------ adding files
    def choose_files(self):
        fs = filedialog.askopenfilenames(title="Choose files")
        if fs:
            self.add(fs)

    def choose_folder(self):
        d = filedialog.askdirectory(title="Choose a folder")
        if d:
            self.add([d])

    def _on_drop(self, event):
        self.add(self.tk.splitlist(event.data))
        return event.action

    def add(self, items, mode=None):
        mode = mode or self.mode.get()
        items = [str(Path(i)) for i in items]
        self.status.configure(text="Looking for media files…")

        def work():
            files = engine.expand(items, include_cleaned=(mode == "check"))
            self.events.put(("queue", (mode, files)))
        threading.Thread(target=work, daemon=True).start()

    def _enqueue(self, mode, files):
        if not files:
            self.status.configure(text="No videos, audio or images found.")
            return
        for f in files:
            iid = self.tree.insert("", "end", values=("", BADGE["WAIT"], Path(f).name, "", str(Path(f).parent)))
            self.rows[iid] = {"file": f, "result": None, "sel": False}
            self.jobs.append((mode, iid, f))
        self._run_jobs()

    def _run_jobs(self):
        if self.busy or not self.jobs:
            return
        self.busy = True
        outdir = self.outdir

        def work():
            while self.jobs:
                mode, iid, f = self.jobs.pop(0)
                self.events.put(("running", iid))
                try:
                    res = engine.check_one(f) if mode == "check" else engine.process_one(f, outdir)
                except Exception as e:
                    res = engine.Result("FAIL", f, reason=str(e))
                self.events.put(("result", (mode, iid, res)))
            self.events.put(("done", None))
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------ events from worker threads
    def _pump(self):
        try:
            while True:
                kind, data = self.events.get_nowait()
                getattr(self, "_ev_" + kind)(data)
        except queue.Empty:
            pass
        self.after(100, self._pump)

    def _ev_versions(self, text):
        self.versions.configure(text=f"{APP_NAME} {APP_VERSION} · {text}")

    def _ev_updated(self, msg):
        self.status.configure(text="")
        self._show_versions()
        messagebox.showinfo("Check for Updates", msg or "Done.")

    def _ev_app_update(self, err):
        if err and IS_MAC and err.startswith("Version "):   # macOS: the new .dmg was downloaded and opened
            self.status.configure(text="")
            messagebox.showinfo(APP_NAME, err)
        elif err:
            self.status.configure(text="")
            messagebox.showerror(APP_NAME, err)
        else:
            self.destroy()

    def _ev_queue(self, data):
        self._enqueue(*data)

    def _ev_running(self, iid):
        if self.tree.exists(iid):
            self.tree.set(iid, "badge", BADGE["RUN"])
            self.tree.see(iid)
        self.status.configure(text=f"{len(self.jobs) + 1} left…")

    def _ev_result(self, data):
        mode, iid, res = data
        if not self.tree.exists(iid):
            return
        row = self.rows[iid]
        row["result"] = res
        if mode == "check":
            if res.status in ("AI", "META"):
                ai = [t for t in res.tags if t[0] == "ai"]
                labels = sorted({t[1] for t in (ai or res.tags)})
                info = ", ".join(labels)
            else:
                info = res.reason or "No AI tags or personal details found"
            row["sel"] = res.status == "AI"       # files with AI tags are ticked automatically
            self.tree.item(iid, values=("☑" if row["sel"] else "☐", BADGE.get(res.status, res.status),
                                        Path(res.file).name, info, str(Path(res.file).parent)),
                           tags=(res.status,))
        else:
            info = Path(res.output).name if res.status == "OK" else res.reason
            self.tree.item(iid, values=("", BADGE[res.status], Path(res.file).name, info,
                                        str(Path(res.file).parent)), tags=(res.status,))

    def _ev_done(self, _):
        self.busy = False
        results = [r["result"] for r in self.rows.values() if r["result"]]
        if self.jobs:
            self._run_jobs()
            return
        if self.mode.get() == "check":
            ai = sum(1 for r in results if getattr(r, "status", "") == "AI")
            self.status.configure(text=f"{len(results)} checked · {ai} with AI tags")
        else:
            ok = sum(1 for r in results if r.status == "OK")
            fail = sum(1 for r in results if r.status == "FAIL")
            self.status.configure(text=f"{ok} cleaned" + (f" · {fail} failed (see Result column)" if fail else ""))

    # ------------------------------------------------------------ list actions
    def _tree_click(self, e):
        if self.mode.get() != "check" or self.tree.identify_region(e.x, e.y) != "cell":
            return
        if self.tree.identify_column(e.x) != "#1":
            return
        iid = self.tree.identify_row(e.y)
        row = self.rows.get(iid)
        if row and row["result"] is not None and row["result"].status not in ("SKIP", "FAIL", "OK"):
            row["sel"] = not row["sel"]
            self.tree.set(iid, "sel", "☑" if row["sel"] else "☐")
            return "break"

    def details(self):
        sel = self.tree.selection()
        if not sel:
            return
        row = self.rows.get(sel[0])
        res = row and row["result"]
        if not res:
            return
        win = tk.Toplevel(self, bg=self.pal["bg"])
        win.title(f"Details — {Path(res.file).name}")
        win.geometry("760x420")
        p = self.pal
        txt = tk.Text(win, wrap="word", font=(MONO, 9 if IS_WIN else 11), padx=8, pady=8, relief="flat",
                      bg=p["field"], fg=p["text"], insertbackground=p["text"], selectbackground=p.get("select", "#cce0ff"))
        sb = ttk.Scrollbar(win, command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        txt.pack(fill="both", expand=True)
        txt.tag_configure("ai", foreground=p["tags"]["AI"])
        txt.tag_configure("info", foreground=p["tags"]["META"])
        txt.tag_configure("h", font=(UI, 10 if IS_WIN else 13, "bold"))
        txt.insert("end", res.file + "\n\n", "h")
        tags = getattr(res, "tags", None)
        if tags:
            for kind in ("ai", "info"):
                group = [t for t in tags if t[0] == kind]
                if group:
                    txt.insert("end", ("AI tags\n" if kind == "ai" else "Other details\n"), "h")
                    for _, label, tag, val in group:
                        txt.insert("end", f"  {label}\n", kind)
                        txt.insert("end", f"      {tag} = {val}\n")
                    txt.insert("end", "\n")
        else:
            txt.insert("end", getattr(res, "reason", "") or getattr(res, "output", "") or "Nothing found.")
        txt.configure(state="disabled")
        theme.title_bar(win, self.dark)

    def clean_selected(self):
        files = [r["file"] for r in self.rows.values() if r["sel"]]
        if not files:
            messagebox.showinfo(APP_NAME, "Tick the files to clean in the ✓ column first.")
            return
        self.mode.set("remove")
        self._mode_changed()
        self.clear()
        self._enqueue("remove", [f for f in files if not engine.is_cleaned(f)] or files)

    def show_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        res = self.rows[sel[0]]["result"]
        open_in_explorer(res.output if res is not None and getattr(res, "output", "") else self.rows[sel[0]]["file"])

    def clear(self):
        if self.busy:
            self.jobs.clear()
        self.tree.delete(*self.tree.get_children())
        self.rows.clear()
        self.status.configure(text="")


# ---------------------------------------------------------------- right-click "Remove Metadata"

class QuickWindow(tk.Tk):
    """Small progress window for the right-click action (Explorer menu / Finder Quick Action)."""

    def __init__(self, items):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("460x150")
        self.resizable(False, False)
        self.attributes("-topmost", True)
        set_icon(self)
        self.dark = load_settings().get("theme") == "dark"
        theme.apply(self, self.dark)
        self.items, self.outdir = items, load_settings().get("outdir")
        if self.outdir and not Path(self.outdir).is_dir():
            self.outdir = None
        self.events = queue.Queue()
        f = ttk.Frame(self, padding=16)
        f.pack(fill="both", expand=True)
        self.title_lbl = ttk.Label(f, text="Removing metadata…", style="Title.TLabel")
        self.title_lbl.pack(anchor="w")
        self.file_lbl = ttk.Label(f, text="", style="Muted.TLabel", width=60)
        self.file_lbl.pack(anchor="w", pady=(4, 8))
        self.bar = ttk.Progressbar(f, mode="indeterminate")
        self.bar.pack(fill="x")
        self.bar.start(12)
        self.btns = ttk.Frame(f)
        self.btns.pack(fill="x", pady=(10, 0))
        threading.Thread(target=self._work, daemon=True).start()
        self.after(100, self._pump)
        self.after(50, lambda: theme.title_bar(self, self.dark))

    def _work(self):
        files = engine.expand(self.items)
        self.events.put(("total", len(files)))
        results = []
        for i, f in enumerate(files, 1):
            self.events.put(("file", (i, len(files), f)))
            results.append(engine.process_one(f, self.outdir))
        self.events.put(("done", results))

    def _pump(self):
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == "total" and data:
                    self.bar.stop()
                    self.bar.configure(mode="determinate", maximum=data, value=0)
                elif kind == "file":
                    i, n, f = data
                    self.bar.configure(value=i - 1)
                    self.file_lbl.configure(text=f"{i} of {n}: {Path(f).name}")
                elif kind == "done":
                    self._finish(data)
                    return
        except queue.Empty:
            pass
        self.after(100, self._pump)

    def _finish(self, results):
        self.bar.stop()
        self.bar.configure(mode="determinate", maximum=1, value=1)
        ok = [r for r in results if r.status == "OK"]
        fail = [r for r in results if r.status == "FAIL"]
        if fail:
            self.title_lbl.configure(text=f"{len(ok)} cleaned, {len(fail)} failed")
            self.file_lbl.configure(text=f"e.g. {Path(fail[-1].file).name}: {fail[-1].reason}"[:120])
            ttk.Button(self.btns, text="Open log", command=lambda: open_file(LOG)).pack(side="left")
            sound(False)
        elif not ok:
            self.title_lbl.configure(text="No videos, audio or images to clean.")
            self.file_lbl.configure(text="")
        else:
            word = "file" if len(ok) == 1 else "files"
            self.title_lbl.configure(text=f"Metadata removed from {len(ok)} {word}.")
            self.file_lbl.configure(text=f"Saved as {Path(ok[-1].output).name}" if len(ok) == 1 else
                                    f"Clean copies end in _camo")
            sound(True)
            self.after(8000, self.destroy)
        if ok:
            ttk.Button(self.btns, text=f"Show in {FILE_MANAGER}",
                       command=lambda: open_in_explorer(ok[-1].output)).pack(side="left", padx=(6, 0))
        ttk.Button(self.btns, text="Close", command=self.destroy).pack(side="right")
        updater.start_background_check()
