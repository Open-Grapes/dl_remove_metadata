"""Wi-Fi admin: the uploads people left on this computer (open the folder / delete all), the activity log,
and blocking devices."""
import tkinter as tk
from tkinter import messagebox, ttk

from . import theme, wifi_store as store
from .common import APP_NAME, open_path


def delete_all(app, parent):
    """Ask, then delete every stored upload and its clean copy."""
    n, size = store.stats()
    if not n:
        messagebox.showinfo("Wi-Fi uploads", "There are no stored uploads.", parent=parent)
        return False
    if not messagebox.askyesno("Delete all Wi-Fi uploads?",
                               f"Delete {n} upload{'s' * (n != 1)} ({store.human(size)}) that people sent over "
                               "Wi-Fi, with their clean copies?\n\n"
                               "They're deleted for good (not to the Recycle Bin / Trash). Anyone still viewing "
                               "them can no longer download them. The activity log is kept.", parent=parent):
        return False
    skip = app.sharing.purge() if app.sharing is not None else set()
    store.delete_all(skip)
    app.refresh_wifi_note()
    return True


class AdminWindow:
    COLS = store.COLS
    WIDTHS = (88, 70, 110, 150, 130, 230, 150, 380)

    def __init__(self, app):
        self.app = app
        w = self.win = tk.Toplevel(app)
        w.title("Wi-Fi admin")
        w.transient(app)
        w.configure(bg=app.pal["bg"])
        w.geometry("1180x640")
        w.minsize(760, 420)
        f = ttk.Frame(w, padding=16)
        f.pack(fill="both", expand=True)

        ttk.Label(f, text="Stored uploads", style="Title.TLabel").pack(anchor="w")
        self.warn = ttk.Label(f, style="Warn.TLabel", wraplength=1000, justify="left")
        self.warn.pack(anchor="w", pady=(6, 0))
        r = ttk.Frame(f)
        r.pack(fill="x", pady=(8, 0))
        self.where = ttk.Label(r, style="Muted.TLabel", text=str(store.WORK))
        self.where.pack(side="left")
        ttk.Button(r, text="Delete all uploads…", command=self._delete).pack(side="right")
        ttk.Button(r, text="Open folder", command=self._open).pack(side="right", padx=6)
        ttk.Label(f, style="Small.TLabel", text=(
            "Each upload is kept in its own folder named by date, time and the sender's IP address (the clean copy "
            "is in its \"out\" folder), even after sharing is turned off or the app is closed — until you press "
            "Delete all.")).pack(anchor="w", pady=(4, 0))

        ttk.Separator(f).pack(fill="x", pady=12)
        hr = ttk.Frame(f)
        hr.pack(fill="x")
        ttk.Label(hr, text="Activity log", style="Title.TLabel").pack(side="left")
        ttk.Button(hr, text="Clear log…", command=self._clear).pack(side="right")
        self.block_btn = ttk.Button(hr, text="Block device…", command=self._block, state="disabled", width=17)
        self.block_btn.pack(side="right", padx=(0, 6))
        ttk.Button(hr, text="Open log (Excel / CSV)", command=lambda: open_path(store.LOG_CSV)
                   if store.LOG_CSV.exists() else None).pack(side="right", padx=6)
        self.filter = tk.StringVar()
        ent = ttk.Entry(hr, textvariable=self.filter, width=26)
        ent.pack(side="right", padx=(6, 12))
        ttk.Label(hr, text="Filter").pack(side="right")
        self.filter.trace_add("write", lambda *_: self._fill(force=True))

        tf = ttk.Frame(f)
        tf.pack(fill="both", expand=True, pady=(8, 0))
        self.tree = ttk.Treeview(tf, columns=self.COLS, show="headings", selectmode="extended")
        for c, wd in zip(self.COLS, self.WIDTHS):
            self.tree.heading(c, text=c, anchor="w")
            self.tree.column(c, width=wd, minwidth=50, stretch=c == "Details", anchor="w")
        sb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind(f"<{'Command' if theme.IS_MAC else 'Control'}-c>", self._copy)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._sel_changed())
        br = ttk.Frame(f)
        br.pack(fill="x", pady=(8, 0))
        ttk.Button(br, text="Unblock all", command=self._unblock_all).pack(side="right")
        self.blocked_lbl = ttk.Label(br, wraplength=950, justify="left")
        self.blocked_lbl.pack(side="left", fill="x")
        copy_key = "Cmd+C" if theme.IS_MAC else "Ctrl+C"
        ttk.Label(f, style="Small.TLabel", wraplength=1100, justify="left", text=(
            "Device name and MAC address are looked up on your network and may be blank (many phones don't share a "
            "name, and use a private MAC per Wi-Fi). Location: everyone here is on your own Wi-Fi, and local "
            f"addresses like 192.168.x.x can't be placed on a map. Select rows and press {copy_key} to copy them. "
            "Block device: select one of its rows — its page closes within a few seconds and it can't come back "
            "(by IP address; a device that gets a new address could).")).pack(anchor="w", pady=(8, 0))
        ttk.Button(f, text="Close", command=self.close).pack(anchor="e", pady=(10, 0))

        w.protocol("WM_DELETE_WINDOW", self.close)
        w.bind("<Escape>", lambda e: self.close())
        w.update_idletasks()
        theme.title_bar(w, app.dark)
        self._sig = None
        self._tick()

    def alive(self):
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def _open(self):
        store.WORK.mkdir(parents=True, exist_ok=True)
        open_path(store.WORK)

    def _delete(self):
        if delete_all(self.app, self.win):
            self._tick(once=True)

    def _clear(self):
        if messagebox.askyesno("Clear the activity log?", "Delete every line of the Wi-Fi activity log?",
                               parent=self.win):
            store.clear_log()
            self._fill(force=True)

    # ---------------------------------------------------------------- block / unblock
    def _sel_ip(self):
        sel = self.tree.selection()
        if not sel:
            return None, None
        r = self.tree.item(sel[0], "values")
        return (r[2], r) if len(r) > 2 and r[2] else (None, None)

    def _sel_changed(self):
        ip, _r = self._sel_ip()
        if not ip:
            self.block_btn.configure(text="Block device…", state="disabled")
        else:
            self.block_btn.configure(text="Unblock device" if store.is_blocked(ip) else "Block device…",
                                     state="normal")

    def _block(self):
        ip, r = self._sel_ip()
        if not ip:
            return
        if store.is_blocked(ip):
            store.unblock(ip)
        else:
            who = " · ".join(x for x in (r[3], r[5]) if x) or "this device"
            if not messagebox.askyesno("Block this device?", (
                    f"Block {ip} ({who})?\n\nIts {APP_NAME} page closes within a few seconds and shows "
                    "“Access ended”. It can't use sharing again until you unblock it. Its uploads stay in the "
                    "folder."), parent=self.win):
                return
            store.block(ip, r[5], r[3])
            if self.app.sharing is not None:
                self.app.sharing.kick(ip)
        self._sel_changed()
        self._show_blocked()
        self.win.after(1800, lambda: self._fill(force=True) if self.alive() else None)

    def _unblock_all(self):
        for ip in list(store.blocked()):
            store.unblock(ip)
        self._sel_changed()
        self._show_blocked()
        self.win.after(1800, lambda: self._fill(force=True) if self.alive() else None)

    def _show_blocked(self):
        b = store.blocked()
        txt = "Blocked devices: " + ("none" if not b else "   ".join(
            f"⛔ {ip}" + (f" ({v.get('device')})" if v.get("device") else "") + f" since {v.get('since', '')}"
            for ip, v in b.items()))
        if self.blocked_lbl.cget("text") != txt:
            self.blocked_lbl.configure(text=txt)

    def _copy(self, _e=None):
        rows = [self.tree.item(i, "values") for i in self.tree.selection()]
        if rows:
            self.app.clipboard_clear()
            self.app.clipboard_append("\n".join("\t".join(map(str, r)) for r in rows))
        return "break"

    def _fill(self, force=False):
        rows = store.read_log()
        q = self.filter.get().strip().lower()
        if q:
            rows = [r for r in rows if q in " ".join(r).lower()]
        sig = (len(rows), tuple(rows[0]) if rows else None, q)
        if sig == self._sig and not force:
            return
        self._sig = sig
        self.tree.delete(*self.tree.get_children())
        for r in rows:
            self.tree.insert("", "end", values=(r + [""] * len(self.COLS))[:len(self.COLS)])

    def _tick(self, once=False):
        if not self.alive():
            return
        n, size = store.stats()
        self.warn.configure(text=(f"⚠  {n} upload{'s' * (n != 1)} from Wi-Fi users stored on this computer "
                                  f"({store.human(size)}). Delete them when you no longer need them.") if n else
                            "No uploads stored.")
        self._fill()
        self._show_blocked()
        if not once:
            self.win.after(2000, self._tick)

    def close(self):
        self.app.wifi_admin = None
        self.win.destroy()
