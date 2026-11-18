"""Self-test for Share via Wi-Fi (used by the build checks): acts like a phone on the network.

    python -m dlrm --cli --wifi-selftest FILE      (FILE: a photo / video / audio file with some metadata)
"""

import json
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import engine, wifi_store
from .web import Sharing


def _req(base, path, data=None, ua="Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Safari/604.1",
         ctype="application/json"):
    headers = {"User-Agent": ua}
    if data is not None:
        headers["Content-Type"] = ctype
    req = urllib.request.Request(base + path, data=data, headers=headers, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def run(sample, say=print):
    sample = Path(sample)
    results = []

    def check(name, ok, info=""):
        ok = bool(ok)
        results.append(ok)
        say(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({info})" if info else ""))

    before = {p.name for p in wifi_store.folders()}
    s = Sharing.start()
    base = s.urls[0]
    ip = urllib.parse.urlsplit(base).hostname
    check("sharing started", base.startswith("http://"), base)
    try:
        from .wifi_win import qr_matrix
        m = qr_matrix(base)
        check("QR code", len(m) > 20, f"{len(m)}x{len(m)}")
        code, body = _req(base, "/")
        check("phone opens the page", code == 200 and b"dl_remove_metadata" in body, f"HTTP {code}")
        code, body = _req(base, "/api/info")
        info = json.loads(body)
        check("page knows it's Wi-Fi sharing", code == 200 and info.get("lan") is True, info.get("pc", ""))

        code, body = _req(base, "/api/upload?name=" + urllib.parse.quote(sample.name), sample.read_bytes(),
                          ctype="application/octet-stream")
        up = json.loads(body)
        check("upload", code == 200 and up.get("id"), up.get("name", body[:80]))
        iid = up["id"]

        code, body = _req(base, "/api/process", json.dumps({"id": iid, "mode": "check"}).encode())
        chk = json.loads(body)
        check("check for AI tags", code == 200 and chk.get("status") in ("AI", "META", "CLEAN"), chk.get("status"))

        code, body = _req(base, "/api/process", json.dumps({"id": iid, "mode": "remove"}).encode())
        res = json.loads(body)
        check("remove metadata", code == 200 and res.get("status") == "OK", res.get("output") or res.get("reason"))

        code, body = _req(base, f"/api/download/{iid}")
        tmp = Path(wifi_store.SUP) / "selftest_download" / (res.get("output") or "x")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(body)
        again = engine.check_one(str(tmp))
        check("download clean copy", code == 200 and again.status == "CLEAN", f"{len(body)} bytes, {again.status}")
        shutil.rmtree(tmp.parent, ignore_errors=True)

        # another device can't fetch this device's file (different IP: simulated through the Host check only)
        code, _ = _req(base.replace(ip, "127.0.0.1"), f"/api/download/{iid}")
        check("other devices can't download it", code == 404, f"HTTP {code}")

        req = urllib.request.Request(base + "/", headers={"Host": "evil.example"})
        try:
            urllib.request.urlopen(req, timeout=10)
            check("other websites are refused", False)
        except urllib.error.HTTPError as e:
            check("other websites are refused", e.code == 403, f"HTTP {e.code}")

        wifi_store.block(ip, "self-test")
        code, body = _req(base, "/api/info")
        check("blocked device is turned away", code == 403 and b"blocked" in body, f"HTTP {code}")
        wifi_store.unblock(ip)
        code, _ = _req(base, "/api/info")
        check("unblocked device is back", code == 200, f"HTTP {code}")

        time.sleep(2.5)       # activity log rows are written in the background
        rows = [r for r in wifi_store.read_log(200) if r[2] == ip]
        actions = {r[6] for r in rows}
        need = {"Opened the page", "Uploaded", "Checked for AI tags", "Removed metadata", "Downloaded"}
        check("activity log", need <= actions, ", ".join(sorted(actions & need)))
        dev = next((r[5] for r in rows if r[5]), "")
        check("device recognised", dev.startswith("iPhone"), dev)
    finally:
        s.stop(linger=3)
    time.sleep(0.5)
    code, body = _req(base, "/api/info")
    check("turning off tells the page", code == 503 and b"ended" in body, f"HTTP {code}")

    # clean up what the test left behind
    for d in wifi_store.folders():
        if d.name not in before:
            shutil.rmtree(d, ignore_errors=True)
    ok = all(results)
    say(f"\nWIFI SELF-TEST {'PASSED' if ok else 'FAILED'} ({sum(results)}/{len(results)})")
    return ok
