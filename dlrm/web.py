"""Local web version: a small server on 127.0.0.1 that serves a page in the browser.

Files dropped on the page are uploaded to this computer's own server (nothing leaves the PC), cleaned or
checked with the same engine as the window, and the clean copies are offered as downloads.

  dl_remove_metadata.exe --web     start (or reuse) the server and open the page

Only this computer can reach it (bound to 127.0.0.1), and every request needs a random per-session token,
so other web pages open in the browser can't use it. The server stops itself a minute after the last
page is closed.
"""

import json
import mimetypes
import os
import secrets
import shutil
import socket
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import engine, updater
from .common import APP_DIR, APP_NAME, APP_VERSION, SUP, log

PREFERRED_PORT = 8765
IDLE_EXIT = 60          # seconds without any page open before the server stops
STATE = SUP / "web.json"
WORK = SUP / "web"
PAGE = Path(__file__).with_name("web_page.html")
MAX_UPLOAD = 50 * 1024 ** 3


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port, token):
        super().__init__(("127.0.0.1", port), Handler)
        self.token = token
        self.last_seen = time.time()
        self.lock = threading.Lock()
        self.items = {}      # id -> {"name", "src", "out", "kind"}
        self.engine_lock = threading.Lock()   # one file at a time, like the window


def _safe_name(name):
    name = Path(name.replace("\\", "/")).name
    name = "".join(c for c in name if c not in '<>:"/\\|?*' and ord(c) >= 32).strip(" .")
    return name or "file"


class Handler(BaseHTTPRequestHandler):
    server: Server
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    # ---------------------------------------------------------------- helpers
    def _send(self, code, body=b"", ctype="application/json", headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _query(self):
        return urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)

    def _allowed(self):
        """Same-machine Host header (blocks DNS rebinding) and the session token."""
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost"):
            return False
        tok = self.headers.get("X-Token") or (self._query().get("t") or [""])[0]
        return secrets.compare_digest(tok, self.server.token)

    def _item(self, iid):
        with self.server.lock:
            return self.server.items.get(iid)

    # ---------------------------------------------------------------- GET
    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/ping":   # lets a second launch find this server
            return self._send(200, {"app": APP_NAME, "ok": True})
        if path == "/icon.png":
            ico = APP_DIR / "assets" / "icon.png"
            return self._send(200, ico.read_bytes() if ico.exists() else b"", "image/png")
        if not self._allowed():
            return self._send(403, "Open the page with the dl_remove_metadata (Web) launcher.", "text/plain")
        self.server.last_seen = time.time()
        if path in ("/", "/index.html"):
            return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8",
                              {"Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                                                          "script-src 'self' 'unsafe-inline'; img-src 'self' data:"})
        if path == "/api/info":
            return self._send(200, {"app": APP_NAME, "version": APP_VERSION, "tools": engine.tool_versions()})
        if path == "/api/heartbeat":
            return self._send(200, {"ok": True})
        if path.startswith("/api/download/"):
            it = self._item(path.rsplit("/", 1)[-1])
            if not it or not it.get("out") or not Path(it["out"]).is_file():
                return self._send(404, {"error": "not found"})
            return self._send_file(Path(it["out"]))
        if path == "/api/zip":
            ids = (self._query().get("ids") or [""])[0].split(",")
            return self._send_zip([i for i in ids if i])
        return self._send(404, {"error": "not found"})

    def _send_file(self, p, name=None):
        size = p.stat().st_size
        quoted = urllib.parse.quote(name or p.name)
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(p.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quoted}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with open(p, "rb") as fh:
            shutil.copyfileobj(fh, self.wfile, 1024 * 1024)

    def _send_zip(self, ids):
        zpath = WORK / f"clean_files-{secrets.token_hex(4)}.zip"
        used = set()
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_STORED, allowZip64=True) as z:
            for iid in ids:
                it = self._item(iid)
                if not it or not it.get("out") or not Path(it["out"]).is_file():
                    continue
                name, n = Path(it["out"]).name, 1
                while name.lower() in used:
                    p = Path(Path(it["out"]).name)
                    name, n = f"{p.stem} ({n}){p.suffix}", n + 1
                used.add(name.lower())
                z.write(it["out"], name)
        try:
            self._send_file(zpath, "clean_files.zip")
        finally:
            zpath.unlink(missing_ok=True)

    # ---------------------------------------------------------------- POST
    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        if not self._allowed():
            return self._send(403, {"error": "forbidden"})
        self.server.last_seen = time.time()
        if path == "/api/upload":
            return self._upload()
        if path == "/api/process":
            try:
                req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            except ValueError:
                return self._send(400, {"error": "bad request"})
            return self._process(req.get("id", ""), req.get("mode", "remove"))
        if path == "/api/forget":
            try:
                req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            except ValueError:
                req = {}
            for iid in req.get("ids", []):
                with self.server.lock:
                    it = self.server.items.pop(iid, None)
                if it:
                    shutil.rmtree(Path(it["src"]).parent, ignore_errors=True)
            return self._send(200, {"ok": True})
        return self._send(404, {"error": "not found"})

    def _upload(self):
        name = _safe_name((self._query().get("name") or ["file"])[0])
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_UPLOAD:
            return self._send(400, {"error": "empty or too large"})
        iid = secrets.token_hex(8)
        d = WORK / iid
        (d / "out").mkdir(parents=True, exist_ok=True)
        src = d / name
        left = length
        with open(src, "wb") as fh:
            while left > 0:
                chunk = self.rfile.read(min(left, 1024 * 1024))
                if not chunk:
                    break
                fh.write(chunk)
                left -= len(chunk)
                self.server.last_seen = time.time()
        if left:
            shutil.rmtree(d, ignore_errors=True)
            return self._send(400, {"error": "upload interrupted"})
        with self.server.lock:
            self.server.items[iid] = {"name": name, "src": str(src), "out": None}
        return self._send(200, {"id": iid, "name": name, "kind": engine.kind_of(str(src))})

    def _process(self, iid, mode):
        it = self._item(iid)
        if not it:
            return self._send(404, {"error": "file not found — add it again"})
        with self.server.engine_lock:
            if mode == "check":
                r = engine.check_one(it["src"])
                return self._send(200, {"status": r.status, "reason": r.reason,
                                        "tags": [{"kind": k, "label": l, "tag": t, "value": v}
                                                 for k, l, t, v in r.tags]})
            outdir = Path(it["src"]).parent / "out"
            # The upload keeps its own name, so a file that already ends in _camo is still cleaned here.
            r = engine.process_one(it["src"], str(outdir)) if not engine.is_cleaned(it["src"]) else None
            if r is None:
                tmp = Path(it["src"]).with_name("in" + Path(it["src"]).suffix)
                shutil.copyfile(it["src"], tmp)
                r = engine.process_one(str(tmp), str(outdir))
                if r.status == "OK":
                    final = outdir / Path(it["src"]).name
                    os.replace(r.output, final)
                    r.output = str(final)
                tmp.unlink(missing_ok=True)
        if r.status == "OK":
            with self.server.lock:
                it["out"] = r.output
            return self._send(200, {"status": "OK", "output": Path(r.output).name,
                                    "size": Path(r.output).stat().st_size})
        return self._send(200, {"status": r.status, "reason": r.reason})


# ---------------------------------------------------------------- start / reuse

def _existing():
    """URL of an already-running server, or None."""
    try:
        st = json.loads(STATE.read_text(encoding="utf-8"))
        with urllib.request.urlopen(f"http://127.0.0.1:{st['port']}/ping", timeout=2) as r:
            if json.loads(r.read()).get("app") == APP_NAME:
                return f"http://127.0.0.1:{st['port']}/?t={st['token']}"
    except Exception:
        pass
    return None


def _free_port():
    for port in (PREFERRED_PORT, 0):
        try:
            with socket.socket() as s:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
        except OSError:
            continue
    return 0


def serve(open_browser=True):
    url = _existing()
    if url:
        if open_browser:
            webbrowser.open(url)
        return 0
    shutil.rmtree(WORK, ignore_errors=True)   # leftovers from a previous run
    WORK.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(24)
    srv = Server(_free_port(), token)
    port = srv.server_address[1]
    STATE.write_text(json.dumps({"port": port, "token": token, "pid": os.getpid()}), encoding="utf-8")
    url = f"http://127.0.0.1:{port}/?t={token}"
    log(f"web: started on port {port}")

    def watchdog():
        while True:
            time.sleep(5)
            if time.time() - srv.last_seen > IDLE_EXIT:
                srv.shutdown()
                return
    threading.Thread(target=watchdog, daemon=True).start()
    if open_browser:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    updater.start_background_check()
    try:
        srv.serve_forever(poll_interval=0.5)
    finally:
        srv.server_close()
        shutil.rmtree(WORK, ignore_errors=True)
        try:
            STATE.unlink()
        except OSError:
            pass
        log("web: stopped")
    return 0
