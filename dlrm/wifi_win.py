"""The Share via Wi-Fi window: a QR code and the addresses, plus the on/off switch."""

import threading
import tkinter as tk
import webbrowser
from tkinter import ttk

from . import theme
from .common import APP_NAME, IS_MAC, log


def qr_matrix(text):
    import qrcode
    q = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
    q.add_data(text)
    q.make(fit=True)
    return q.get_matrix()


FIREWALL_TIP = (
    "Phone can't connect? The first time, macOS asks whether to accept incoming connections for "
    f"{APP_NAME}: click Allow." if IS_MAC else
    f"Phone can't connect? The first time, Windows asks whether to allow {APP_NAME} on networks: allow "
    "Private networks. Your Wi-Fi must also be set to Private in Settings → Network & internet.")


class WifiWindow:
    SIZE = 230

    def __init__(self, app):
        self.app = app
        w = self.win = tk.Toplevel(app)
        w.title("Share via Wi-Fi")
        w.resizable(False, False)
        w.transient(app)
        w.configure(bg=app.pal["bg"])
        f = ttk.Frame(w, padding=18)
        f.pack(fill="both", expand=True)
        self.head = ttk.Label(f, style="Title.TLabel")
        self.head.pack(anchor="w")
        self.canvas = tk.Canvas(f, width=self.SIZE, height=self.SIZE, highlightthickness=0, bg="#ffffff")
        self.canvas.pack(pady=(12, 10))
        self.links = ttk.Frame(f)
        self.links.pack(fill="x")
        ttk.Label(f, wraplength=440, justify="left", style="Muted.TLabel", text=(
            "Phones, tablets and laptops on the same Wi-Fi as this computer can open one of these addresses (or "
            "scan the code with the phone's camera). They upload photos, videos or audio; this computer removes "
            "the metadata or checks them for AI tags, and they download the clean copies in their browser.\n\n"
            "Keep this computer on with the app open. There's no password: anyone on this Wi-Fi who knows the "
            "address can use it while sharing is on. Their uploads are kept on this computer until you delete "
            "them (Admin…).\n\n" + FIREWALL_TIP)).pack(anchor="w", pady=(10, 0))
        self.count = ttk.Label(f, style="Muted.TLabel")
        self.count.pack(anchor="w", pady=(10, 0))
        self.store_row = ttk.Frame(f)
        self.store_lbl = ttk.Label(self.store_row, style="Warn.TLabel", wraplength=440, justify="left")
        self.store_lbl.pack(anchor="w")
        sr = ttk.Frame(self.store_row)
        sr.pack(anchor="w", pady=(4, 0))
        ttk.Button(sr, text="Open folder", command=app._open_wifi_folder).pack(side="left")
        ttk.Button(sr, text="Delete all…", command=lambda: app._wifi_delete_all(self.win)).pack(side="left", padx=6)
        row = ttk.Frame(f)
        row.pack(fill="x", pady=(12, 0))
        self.toggle = ttk.Button(row, command=self.flip, width=16)
        self.toggle.pack(side="left")
        ttk.Button(row, text="Close", command=self.close).pack(side="right")
        ttk.Button(row, text="Admin…", command=app.open_wifi_admin).pack(side="right", padx=6)
        w.protocol("WM_DELETE_WINDOW", self.close)
        w.bind("<Escape>", lambda e: self.close())
        w.update_idletasks()
        theme.title_bar(w, app.dark)
        x = app.winfo_rootx() + (app.winfo_width() - w.winfo_width()) // 2
        y = app.winfo_rooty() + 50
        w.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        if app.sharing is None:
            self.flip()
        else:
            self.show()
        self._tick()

    def alive(self):
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def flip(self):
        app = self.app
        if app.sharing is not None:
            app.stop_sharing()
            self.show()
            return
        self.head.configure(text="Starting Wi-Fi sharing …")
        self.toggle.configure(state="disabled")

        def go():
            try:
                from .web import Sharing
                s = Sharing.start(on_change=lambda: app.events.put(("call", self.show)))
                err = None
            except Exception as e:
                log(f"wifi start: {e!r}")
                s, err = None, str(e)
            app.events.put(("call", lambda: self._started(s, err)))
        threading.Thread(target=go, daemon=True).start()

    def _started(self, s, err):
        self.app.sharing = s
        self.app.set_wifi_badge()
        self.toggle.configure(state="normal")
        self.show(err)

    def show(self, error=None):
        if not self.alive():
            return
        s = self.app.sharing
        for c in self.links.winfo_children():
            c.destroy()
        self.canvas.delete("all")
        if s is None:
            self.head.configure(text=f"Couldn't start sharing: {error}" if error else "Wi-Fi sharing is off.")
            self.toggle.configure(text="Turn on", style="Accent.TButton")
            self.canvas.create_text(self.SIZE // 2, self.SIZE // 2, text="Off", fill="#888888", font=(theme.UI, 14))
            self.count.configure(text="")
            return
        self.head.configure(text="Sharing is on — open on your phone:")
        self.toggle.configure(text="Turn off", style="TButton")
        self._draw_qr(s.urls[0])
        for u in s.urls:
            r = ttk.Frame(self.links)
            r.pack(fill="x", pady=2)
            lb = ttk.Label(r, text=u, font=(theme.UI, 13 + theme._S, "bold"), foreground=self.app.pal["accent"],
                           cursor="hand2")
            lb.pack(side="left")
            lb.bind("<Button-1>", lambda e, u=u: webbrowser.open(u))
            ttk.Button(r, text="Copy", command=lambda u=u: self._copy(u)).pack(side="right")
        if s.mdns == "pending":
            ttk.Label(self.links, text="(the .local address appears in a moment)", style="Small.TLabel").pack(
                anchor="w")
        elif s.mdns == "off":
            ttk.Label(self.links, text="(this network blocks .local names — use the number address)",
                      style="Small.TLabel").pack(anchor="w")

    def _draw_qr(self, text):
        try:
            m = qr_matrix(text)
        except Exception as e:
            log(f"qr: {e!r}")
            return
        n = len(m)
        cell = self.SIZE / n
        c = self.canvas
        c.create_rectangle(0, 0, self.SIZE, self.SIZE, fill="#ffffff", outline="")
        for y, row in enumerate(m):
            for x, on in enumerate(row):
                if on:
                    c.create_rectangle(x * cell, y * cell, (x + 1) * cell, (y + 1) * cell, fill="#000000",
                                       outline="")

    def _copy(self, u):
        self.app.clipboard_clear()
        self.app.clipboard_append(u)
        self.count.configure(text=f"Copied {u}")

    def stored(self, n, size):
        """Reminder: uploads from Wi-Fi users still on this computer."""
        from .wifi_store import human
        if n:
            self.store_lbl.configure(text=f"⚠  {n} upload{'s' * (n != 1)} ({human(size)}) stored on this "
                                          "computer. Delete them when you no longer need them.")
            if not self.store_row.winfo_manager():
                self.store_row.pack(anchor="w", fill="x", pady=(8, 0), after=self.count)
        else:
            self.store_row.pack_forget()

    def _tick(self):
        if not self.alive():
            return
        s = self.app.sharing
        if s is not None:
            n, devices = s.counts()
            self.count.configure(text=(f"{n} file{'s' * (n != 1)} from {devices} device{'s' * (devices != 1)} "
                                       "since sharing started") if n else "No uploads yet.")
        self._n = getattr(self, "_n", 0) + 1
        if self._n % 4 == 1:
            self.app.refresh_wifi_note()
        self.win.after(1500, self._tick)

    def close(self):
        self.app.wifi_win = None
        self.win.destroy()
