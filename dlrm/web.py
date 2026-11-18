"""Browser versions of the app.

1. This computer only — `dl_remove_metadata (Web)` launcher (`--web`): a server on 127.0.0.1 with a random
   per-session token. Uploads are temporary and deleted when it stops (a minute after the last page closes).

2. Share via Wi-Fi — started from the window: phones and laptops on the same network open
   http://192.168.x.x (or http://dl-remove-metadata.local), upload files, and this computer removes their
   metadata or checks them for AI tags; they download the clean copies. No password, but the Host header is
   checked (other websites can't reach it), blocked devices are turned away, every visit is logged, and
   uploads are kept on this computer (one folder each) until you delete them in Admin.
"""

import json
import mimetypes
import os
import secrets
import shutil
import socket
import socketserver
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
IDLE_EXIT = 60          # seconds without any page open before the local server stops
STATE = SUP / "web.json"
WORK = SUP / "web"
PAGE = Path(__file__).with_name("web_page.html")
MAX_UPLOAD = 50 * 1024 ** 3

LAN_PORTS = (80, 8777, 0)          # port 80 gives short addresses like http://192.168.0.20
MDNS_HOST = "dl-remove-metadata"   # http://dl-remove-metadata.local
LINGER = 8          # seconds an address keeps saying "sharing has ended" after Turn off (pages poll every ≤4 s)
BLOCKED_MSG = f"The owner of this computer has ended your access to {APP_NAME}."
BLOCKED_PAGE = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
                f"<title>{APP_NAME}</title><body style='font:17px system-ui,sans-serif;display:grid;place-items:center;"
                "min-height:90vh;margin:0;padding:16px;text-align:center;background:#f4f5f7;color:#222'>"
                "<div><div style='font-size:42px'>&#9940;</div><h2>Access ended</h2><p>" + BLOCKED_MSG + "</p></div>")
_SEEN = {}


def lan_ip():
    """This computer's address on the local network (the adapter with the default route), or None."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))      # picks the route; nothing is sent
            ip = s.getsockname()[0]
        return None if ip.startswith(("127.", "0.")) else ip
    except OSError:
        return None


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port, token=None, lan_hosts=None):
        self.lan = lan_hosts is not None
        self.hosts = lan_hosts or {"127.0.0.1", "localhost"}
        self.token = token
        self.last_seen = time.time()
        self.lock = threading.Lock()
        self.items = {}      # id -> {"name", "src", "out", "ip", "dir"}
        self.engine_lock = threading.Lock()   # one file at a time, like the window
        self.closed = False                   # Wi-Fi sharing turned off: answer "ended", then hang up
        self.conns = set()                    # open browser connections (kept alive between requests)
        self.conns_lock = threading.Lock()
        super().__init__(("0.0.0.0" if self.lan else "127.0.0.1", port), Handler)

    # On Windows SO_REUSEADDR lets a second copy of the app grab the same port, and phones then reach
    # either one at random. Claim the port exclusively there instead.
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        # Skip HTTPServer's machine-name lookup (socket.getfqdn), which can hang on some networks.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

    def handle_error(self, request, client_address):
        """A browser that hangs up mid-answer (closed tab, phone left the Wi-Fi) is normal: don't print a
        traceback (the window app has no console to print to)."""
        import sys
        err = sys.exc_info()[1]
        if isinstance(err, (ConnectionError, TimeoutError)):
            return
        log(f"web: request from {client_address[0]} failed: {err!r}")

    def hang_up(self):
        """Turn off: cut every open browser connection, so pages notice right away (not on their next refresh)."""
        self.closed = True
        with self.conns_lock:
            conns = list(self.conns)
        for c in conns:
            try:
                c.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


_versions = {"text": "", "at": 0.0}


def _tool_versions():
    """Tool versions for the page footer (asking ffmpeg/exiftool takes a moment, so keep it for 10 minutes)."""
    if time.time() - _versions["at"] > 600:
        _versions.update(text=engine.tool_versions(), at=time.time())
    return _versions["text"]


def _safe_name(name):
    name = Path(name.replace("\\", "/")).name
    name = "".join(c for c in name if c not in '<>:"/\\|?*' and ord(c) >= 32).strip(" .")
    return name or "file"


class Handler(BaseHTTPRequestHandler):
    server: Server
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def setup(self):
        super().setup()
        with self.server.conns_lock:
            self.server.conns.add(self.connection)

    def finish(self):
        with self.server.conns_lock:
            self.server.conns.discard(self.connection)
        try:
            super().finish()
        except OSError:
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

    def _ip(self):
        return self.client_address[0]

    def _allowed(self):
        """Host header must name this computer (blocks other websites via DNS rebinding); the local version
        also needs its session token."""
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        if host not in self.server.hosts:
            return False
        if self.server.lan:
            return True
        tok = self.headers.get("X-Token") or (self._query().get("t") or [""])[0]
        return secrets.compare_digest(tok, self.server.token)

    def _gate(self, path):
        """Wi-Fi: sharing turned off, or this device blocked — say so (the page shows a notice) and stop."""
        if not self.server.lan:
            return False
        from .wifi_store import is_blocked
        if self.server.closed:
            self.close_connection = True
            self._send(503, {"error": "Wi-Fi sharing was turned off on the computer.", "ended": True},
                       headers={"Connection": "close"})
            return True
        ip = self._ip()
        if is_blocked(ip):
            self.close_connection = True
            if time.time() - _SEEN.get("blocked " + ip, 0) > 600:
                self._log("Tried to connect (blocked)")
                _SEEN["blocked " + ip] = time.time()
            if path.startswith("/api/"):
                self._send(403, {"error": BLOCKED_MSG, "blocked": True}, headers={"Connection": "close"})
            else:
                self._send(403, BLOCKED_PAGE, "text/html; charset=utf-8", {"Connection": "close"})
            return True
        return False

    def _log(self, action, details=""):
        if self.server.lan:
            from .wifi_store import log_event
            log_event(self._ip(), self.headers.get("User-Agent", ""), action, details)

    def _item(self, iid):
        with self.server.lock:
            it = self.server.items.get(iid)
        # On Wi-Fi a device only gets its own files.
        if it and self.server.lan and it.get("ip") != self._ip():
            return None
        return it

    # ---------------------------------------------------------------- GET
    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/ping":   # lets a second launch find the local server
            return self._send(200, {"app": APP_NAME, "ok": True})
        if path in ("/icon.png", "/favicon.ico"):
            ico = APP_DIR / "assets" / "icon.png"
            return self._send(200, ico.read_bytes() if ico.exists() else b"", "image/png",
                              {"Cache-Control": "max-age=86400"})
        if not self._allowed():
            return self._send(403, ("Open the address shown in the Share via Wi-Fi window." if self.server.lan else
                                    f"Open the page with the {APP_NAME} (Web) launcher."), "text/plain")
        if self._gate(path):
            return
        self.server.last_seen = time.time()
        if path in ("/", "/index.html"):
            if self.server.lan and time.time() - _SEEN.get(self._ip(), 0) > 600:   # once per 10 min per device
                self._log("Opened the page")
            _SEEN[self._ip()] = time.time()
            return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8",
                              {"Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                                                          "script-src 'self' 'unsafe-inline'; img-src 'self' data:"})
        if path == "/api/info":
            return self._send(200, {"app": APP_NAME, "version": APP_VERSION, "tools": _tool_versions(),
                                    "lan": self.server.lan, "pc": socket.gethostname()})
        if path == "/api/heartbeat":
            return self._send(200, {"ok": True})
        if path.startswith("/api/download/"):
            it = self._item(path.rsplit("/", 1)[-1])
            if not it or not it.get("out") or not Path(it["out"]).is_file():
                return self._send(404, {"error": "not found"})
            self._log("Downloaded", Path(it["out"]).name)
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
        tmpdir = SUP / "wifi_zip" if self.server.lan else WORK
        tmpdir.mkdir(parents=True, exist_ok=True)
        zpath = tmpdir / f"clean_files-{secrets.token_hex(4)}.zip"
        used, n_files = set(), 0
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
                n_files += 1
        try:
            self._log("Downloaded all (.zip)", f"{n_files} files")
            self._send_file(zpath, "clean_files.zip")
        finally:
            zpath.unlink(missing_ok=True)

    # ---------------------------------------------------------------- POST
    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        if not self._allowed():
            return self._send(403, {"error": "forbidden"})
        if self._gate(path):
            return
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
                if not self._item(iid):
                    continue
                with self.server.lock:
                    it = self.server.items.pop(iid, None)
                # On Wi-Fi the files stay in the uploads folder until you delete them (Admin).
                if it and not self.server.lan:
                    shutil.rmtree(Path(it["dir"]), ignore_errors=True)
            if self.server.lan and req.get("ids"):
                self._log("Cleared their list", f"{len(req['ids'])} files")
            return self._send(200, {"ok": True})
        return self._send(404, {"error": "not found"})

    def _upload(self):
        name = _safe_name((self._query().get("name") or ["file"])[0])
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_UPLOAD:
            return self._send(400, {"error": "empty or too large"})
        iid = secrets.token_hex(8)
        if self.server.lan:
            from .wifi_store import human, new_folder
            d = new_folder(iid[:6], self._ip(), name)
        else:
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
            self.server.items[iid] = {"name": name, "src": str(src), "out": None, "ip": self._ip(), "dir": str(d)}
        if self.server.lan:
            self._log("Uploaded", f"{name} · {human(length)}")
        return self._send(200, {"id": iid, "name": name, "kind": engine.kind_of(str(src))})

    def _process(self, iid, mode):
        it = self._item(iid)
        if not it:
            return self._send(404, {"error": "file not found — add it again"})
        with self.server.engine_lock:
            if mode == "check":
                r = engine.check_one(it["src"])
                self._log("Checked for AI tags", f"{it['name']} · {r.status}")
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
            self._log("Removed metadata", it["name"])
            return self._send(200, {"status": "OK", "output": Path(r.output).name,
                                    "size": Path(r.output).stat().st_size})
        self._log("Couldn't clean" if r.status == "FAIL" else "Skipped", f"{it['name']} · {r.reason}")
        return self._send(200, {"status": r.status, "reason": r.reason})


# ============================================================================ Share via Wi-Fi (from the window)
class Sharing:
    """The running Wi-Fi server: .urls, .mdns, .stop(). Start with Sharing.start()."""

    _ending = None          # the last server, still telling open pages "sharing has ended" for a few seconds

    def __init__(self):
        self.srv = None
        self.urls = []
        self.mdns = "pending"
        self._closers = []
        self._stopped = False

    @classmethod
    def start(cls, on_change=None):
        from .wifi_store import WORK as WIFI_WORK
        self = cls()
        if cls._ending is not None:         # turned straight back on: wait for the old one to let go of the port
            cls._ending.join(LINGER + 2)
        ip = lan_ip()
        if not ip:
            raise RuntimeError("This computer isn't connected to a network.")
        WIFI_WORK.mkdir(parents=True, exist_ok=True)       # earlier uploads are kept until you delete them
        hosts = {ip, f"{MDNS_HOST}.local", f"{socket.gethostname().lower()}.local", "127.0.0.1", "localhost"}
        for port in LAN_PORTS:
            try:
                self.srv = Server(port, lan_hosts=hosts)
                break
            except OSError:
                continue
        if self.srv is None:
            raise RuntimeError("Couldn't open a network port.")
        port = self.srv.server_address[1]
        suffix = "" if port == 80 else f":{port}"
        self.urls = [f"http://{ip}{suffix}"]
        threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.5}, daemon=True).start()
        log(f"wifi: sharing on {self.urls[0]}")

        def announce():          # http://dl-remove-metadata.local follows when mDNS is ready (can be slow or blocked)
            close = _announce(ip, port)
            if close and not self._stopped:
                self._closers.append(close)
                self.urls.append(f"http://{MDNS_HOST}.local{suffix}")
            elif close:
                close()
            self.mdns = "on" if close else "off"
            if on_change:
                on_change()
        threading.Thread(target=announce, daemon=True).start()
        return self

    def stop(self, linger=LINGER):
        """Turn off. For a few seconds the address keeps answering "sharing has ended" (pages show that
        instead of looking alive) — then the port is released."""
        self._stopped = True
        if self.srv:
            self.srv.closed = True
        for c in self._closers:
            try:
                c()
            except Exception:
                pass
        srv = self.srv
        if not srv:
            return
        log("wifi: sharing off")

        def end():
            srv.hang_up()
            time.sleep(linger)
            try:
                srv.shutdown()
                srv.server_close()
            except Exception:
                pass
        t = threading.Thread(target=end, daemon=True)
        t.start()
        Sharing._ending = t

    def purge(self):
        """Before "Delete all": forget every file (they're about to be deleted). Returns folders to keep."""
        if not self.srv:
            return set()
        with self.srv.lock:
            self.srv.items.clear()
        return set()

    def kick(self, ip):
        """Blocked: drop that device's files from the list."""
        if not self.srv:
            return
        with self.srv.lock:
            for iid in [i for i, it in self.srv.items.items() if it.get("ip") == ip]:
                self.srv.items.pop(iid, None)

    def counts(self):
        """(files uploaded since sharing started, devices that sent them)."""
        if not self.srv:
            return 0, 0
        with self.srv.lock:
            items = list(self.srv.items.values())
        return len(items), len({it.get("ip") for it in items})


def _announce(ip, port):
    """Make http://dl-remove-metadata.local work (mDNS / Bonjour). Returns a closer, or None."""
    try:
        from zeroconf import ServiceInfo, Zeroconf
        zc = Zeroconf(interfaces=[ip])
        info = ServiceInfo("_http._tcp.local.", f"{APP_NAME} on {socket.gethostname()}._http._tcp.local.",
                           port=port, addresses=[socket.inet_aton(ip)], server=f"{MDNS_HOST}.local.",
                           properties={"path": "/"})
        zc.register_service(info, allow_name_change=True)

        def close():
            try:
                zc.unregister_service(info)
            finally:
                zc.close()
        return close
    except Exception as e:
        log(f"mDNS: {e!r}")
        return None


# ============================================================================ this computer only (--web)
def _existing():
    """URL of an already-running local server, or None."""
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
