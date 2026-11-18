"""Keeps ffmpeg, exiftool and the AI tag list current, and checks GitHub for a newer dl_remove_metadata.

Updated copies go to the per-user folder (%LOCALAPPDATA%\\dl_remove_metadata on Windows,
~/Library/Application Support/dl_remove_metadata on macOS), so the program itself is never modified
(and the bundled versions remain as a fallback). Also used by build.py / build_mac.py to fetch the bundled tools.
"""

import io
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import tarfile
import time
import urllib.request
import zipfile
from pathlib import Path

from .common import (APP_BUNDLE, APP_DIR, APP_NAME, APP_VERSION, EXE, FROZEN, GITHUB_REPO, IS_MAC, MAC_ASSETS,
                     RELEASE_ASSET, RULES_URL, SUP, TOOLS, log, run)

UA = {"User-Agent": f"{APP_NAME}-updater"}
STAMP = SUP / "last_update_check"
LOCK = SUP / ".updating"
WEEK = 7 * 24 * 3600

FFMPEG_VERSION_URL = "https://www.gyan.dev/ffmpeg/builds/release-version"
FFMPEG_ZIPS = ["https://github.com/GyanD/codexffmpeg/releases/download/{v}/ffmpeg-{v}-essentials_build.zip",
               "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"]
EXIFTOOL_VERSION_URL = "https://exiftool.org/ver.txt"
EXIFTOOL_ZIPS = ["https://exiftool.org/exiftool-{v}_64.zip",
                 "https://sourceforge.net/projects/exiftool/files/exiftool-{v}_64.zip/download"]
# macOS: static, signed ffmpeg builds by Martin Riedl; ExifTool's Perl package (macOS ships perl).
MAC_FFMPEG = "https://ffmpeg.martin-riedl.de/redirect/latest/macos/{arch}/release/ffmpeg.zip"
EXIFTOOL_TARS = ["https://exiftool.org/Image-ExifTool-{v}.tar.gz",
                 "https://sourceforge.net/projects/exiftool/files/Image-ExifTool-{v}.tar.gz/download"]


def mac_arch():
    return "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x86_64"


def fetch(url, timeout=30):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def fetch_text(url, timeout=20):
    return fetch(url, timeout).decode("utf-8", "replace")


def final_url(url, timeout=20):
    """Where a link redirects to. Asks for just the first byte: some servers reject HEAD requests."""
    req = urllib.request.Request(url, headers={**UA, "Range": "bytes=0-0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.geturl()


def _vtuple(v):
    """'1.1' -> (1, 1); trailing zeros don't count, so 1.1 and 1.1.0 are the same version."""
    t = [int(x) for x in re.findall(r"\d+", v or "")]
    while t and t[-1] == 0:
        t.pop()
    return tuple(t)


def newer(a, b):
    return _vtuple(a) > _vtuple(b)


def _read(p):
    try:
        return Path(p).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


# ---------------------------------------------------------------- ffmpeg

_mac_ff_url = ""


def latest_ffmpeg():
    """Newest ffmpeg release version number."""
    global _mac_ff_url
    if IS_MAC:
        u = final_url(MAC_FFMPEG.format(arch="arm64" if mac_arch() == "arm64" else "amd64"), timeout=30)
        m = re.search(r"_(\d+(?:\.\d+)+)/", u)
        _mac_ff_url = u
        return m.group(1) if m else ""
    return fetch_text(FFMPEG_VERSION_URL).strip()


def _install_ffmpeg_mac(dest, version):
    url = _mac_ff_url or MAC_FFMPEG.format(arch="arm64" if mac_arch() == "arm64" else "amd64")
    try:
        with zipfile.ZipFile(io.BytesIO(fetch(url, timeout=900))) as z:
            name = next(n for n in z.namelist() if n.rstrip("/").split("/")[-1] == "ffmpeg")
            tmp = dest / "ffmpeg.new"
            tmp.write_bytes(z.read(name))
        tmp.chmod(0o755)
        if run([str(tmp), "-hide_banner", "-version"], timeout=30).returncode != 0:
            tmp.unlink(missing_ok=True)
            return False
        os.replace(tmp, dest / "ffmpeg")
        (dest / "ffmpeg.version").write_text(version, encoding="utf-8")
        return True
    except Exception as e:
        log(f"update: ffmpeg download from {url} failed: {e}")
        return False


def install_ffmpeg(dest, version):
    """Download ffmpeg `version` into dest/ffmpeg(.exe): gyan.dev 'essentials' on Windows, Martin Riedl's
    build on macOS. Returns True on success."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    if IS_MAC:
        return _install_ffmpeg_mac(dest, version)
    for url in FFMPEG_ZIPS:
        try:
            data = fetch(url.format(v=version), timeout=900)
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = [n for n in z.namelist() if n.endswith("/bin/ffmpeg.exe")]
                lic = [n for n in z.namelist() if n.endswith("/LICENSE")]
                if not names:
                    continue
                tmp = dest / "ffmpeg.exe.new"
                tmp.write_bytes(z.read(names[0]))
                if lic:
                    (dest / "ffmpeg-LICENSE.txt").write_bytes(z.read(lic[0]))
            if run([str(tmp), "-hide_banner", "-version"], timeout=30).returncode != 0:
                tmp.unlink(missing_ok=True)
                continue
            os.replace(tmp, dest / "ffmpeg.exe")
            (dest / "ffmpeg.version").write_text(version, encoding="utf-8")
            return True
        except Exception as e:
            log(f"update: ffmpeg download from {url} failed: {e}")
    return False


# ---------------------------------------------------------------- exiftool

def latest_exiftool():
    return fetch_text(EXIFTOOL_VERSION_URL).strip()


def exiftool_version(exe):
    from .engine import _exiftool_cmd
    try:
        return run(_exiftool_cmd(exe) + ["-ver"], timeout=60).stdout.decode().strip()
    except Exception:
        return ""


def install_exiftool(dest, version):
    """Download exiftool into dest/: the Windows package (exiftool.exe + exiftool_files), or on macOS the
    Perl package (exiftool + lib). Returns the version, or ''."""
    dest = Path(dest)
    if IS_MAC:
        return _install_exiftool_mac(dest, version)
    for url in EXIFTOOL_ZIPS:
        work = Path(tempfile.mkdtemp(prefix="dlrm-et-"))
        try:
            data = fetch(url.format(v=version), timeout=600)
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                z.extractall(work)
            exe = next((p for p in work.rglob("exiftool*.exe")), None)
            if not exe or not (exe.parent / "exiftool_files").is_dir():
                continue
            out = work / "out"
            out.mkdir()
            shutil.copy2(exe, out / "exiftool.exe")
            shutil.copytree(exe.parent / "exiftool_files", out / "exiftool_files")
            v = exiftool_version(out / "exiftool.exe")
            if not v:
                continue
            old = dest.with_name(dest.name + ".old")
            shutil.rmtree(old, ignore_errors=True)
            if dest.exists():
                dest.rename(old)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(out), str(dest))
            shutil.rmtree(old, ignore_errors=True)
            return v
        except Exception as e:
            log(f"update: exiftool download from {url} failed: {e}")
        finally:
            shutil.rmtree(work, ignore_errors=True)
    return ""


def _swap_in(out, dest):
    old = dest.with_name(dest.name + ".old")
    shutil.rmtree(old, ignore_errors=True)
    if dest.exists():
        dest.rename(old)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(out), str(dest))
    shutil.rmtree(old, ignore_errors=True)


def _install_exiftool_mac(dest, version):
    for url in EXIFTOOL_TARS:
        work = Path(tempfile.mkdtemp(prefix="dlrm-et-"))
        try:
            with tarfile.open(fileobj=io.BytesIO(fetch(url.format(v=version), timeout=600)), mode="r:gz") as t:
                t.extractall(work, filter="data")
            script = next((p for p in work.rglob("exiftool") if p.is_file() and (p.parent / "lib").is_dir()), None)
            if not script:
                continue
            out = work / "out"
            out.mkdir()
            shutil.copy2(script, out / "exiftool")
            (out / "exiftool").chmod(0o755)
            shutil.copytree(script.parent / "lib", out / "lib")
            v = exiftool_version(out / "exiftool")
            if not v:
                continue
            _swap_in(out, dest)
            return v
        except Exception as e:
            log(f"update: exiftool download from {url} failed: {e}")
        finally:
            shutil.rmtree(work, ignore_errors=True)
    return ""


# ---------------------------------------------------------------- the update check

def update_available():
    """Newer app version recorded by the last check, or ''."""
    v = _read(SUP / "app_update_available")
    return v if v and newer(v, APP_VERSION) else ""


def _reachable():
    try:
        fetch(EXIFTOOL_VERSION_URL, timeout=10)
        return True
    except Exception:
        return False


def app_release_info():
    """Latest release of this app on GitHub: dict(latest, date, notes, url, via, error)."""
    import json
    info = {"latest": "", "date": "", "notes": "", "url": f"https://github.com/{GITHUB_REPO}/releases/latest",
            "via": "", "error": ""}
    tok = github_token()
    try:
        rel = json.loads(github_api(f"repos/{GITHUB_REPO}/releases/latest"))
        info.update(latest=re.sub(r"^v", "", rel.get("tag_name", "")), date=(rel.get("published_at") or "")[:10],
                    notes=(rel.get("body") or "").strip(), url=rel.get("html_url") or info["url"],
                    via="checked with this computer's GitHub login" if tok else "checked on GitHub")
    except Exception as e:
        latest = latest_app_version()      # public repo without a login
        if latest:
            info.update(latest=latest, via="checked on GitHub")
        elif not tok:
            info["error"] = ("Couldn't see the private GitHub repo: sign in with GitHub CLI on this computer "
                             "(gh auth login), then click Check again.")
        else:
            info["error"] = f"Couldn't reach GitHub ({e})."
    return info


def check_all():
    """Check the app and every tool for updates without installing anything.

    Returns dict(online, app={current, latest, date, notes, url, via, error, update}, tools=[dict(key, name,
    current, latest, update, error)]).
    """
    from . import engine
    online = _reachable()
    app = {"current": APP_VERSION, **(app_release_info() if online else
                                      {"latest": "", "date": "", "notes": "", "via": "",
                                       "url": f"https://github.com/{GITHUB_REPO}/releases/latest",
                                       "error": "No internet connection."})}
    app["update"] = bool(app["latest"]) and newer(app["latest"], APP_VERSION)

    engine.reset_tool_cache()
    engine._tried_update = True          # don't start an install from inside the check
    tools = []

    # ffmpeg
    ff_ok = bool(engine.find_ffmpeg())
    ff_cur = ""
    if (SUP / "bin" / f"ffmpeg{EXE}").exists():
        ff_cur = _read(SUP / "bin" / "ffmpeg.version")
    ff_cur = ff_cur or _read(TOOLS / "ffmpeg.version")
    try:
        ff_new, ff_err = (latest_ffmpeg() if online else ""), ""
    except Exception as e:
        ff_new, ff_err = "", f"couldn't check ({e})"
    tools.append({"key": "ffmpeg", "name": "ffmpeg (video & audio)", "current": ff_cur if ff_ok else "not working",
                  "latest": ff_new, "error": ff_err,
                  "update": bool(ff_new) and (not ff_ok or newer(ff_new, ff_cur))})

    # exiftool
    ex_ok = bool(engine.find_exiftool())
    ex_cur = engine.exiftool(["-ver"])[1].strip() if ex_ok else ""
    try:
        ex_new, ex_err = (latest_exiftool() if online else ""), ""
    except Exception as e:
        ex_new, ex_err = "", f"couldn't check ({e})"
    tools.append({"key": "exiftool", "name": "ExifTool (images & tags)", "current": ex_cur or "missing",
                  "latest": ex_new, "error": ex_err, "update": bool(ex_new) and (not ex_cur or newer(ex_new, ex_cur))})

    # AI tag list
    rp = engine.find_rules()
    rv_cur = engine.rules_version(rp) if rp else ""
    rv_new, rv_err, text = "", "", ""
    if online:
        try:
            text = fetch_text(RULES_URL, timeout=30)
            m = re.search(r"^#\s*version:\s*(\S+)", text, re.M)
            if not m or not re.search(r"^ai\s", text, re.M):
                raise ValueError("not a rules file")
            rv_new = m.group(1)
        except Exception as e:
            rv_err = f"couldn't check ({e})"
    tools.append({"key": "rules", "name": "AI tag list", "current": rv_cur or "missing", "latest": rv_new,
                  "error": rv_err, "update": bool(rv_new) and rv_new > rv_cur, "_text": text})

    engine.reset_tool_cache()
    flag = SUP / "app_update_available"
    if app["update"]:
        flag.write_text(app["latest"], encoding="utf-8")
    elif app["latest"]:
        flag.unlink(missing_ok=True)
    return {"online": online, "app": app, "tools": tools}


def install_tools(status, progress=None, force_ffmpeg=False, only=None):
    """Install the tools marked for update in a check_all() result (only: just these keys). Returns summary lines."""
    from . import engine
    say = progress or (lambda msg: None)
    lines = []
    for t in status["tools"]:
        if only is not None and t["key"] not in only:
            continue
        if not (t["update"] or (force_ffmpeg and t["key"] == "ffmpeg" and t["latest"])):
            continue
        say(f"Updating {t['name']} to {t['latest']}…")
        if t["key"] == "ffmpeg":
            ok = install_ffmpeg(SUP / "bin", t["latest"])
        elif t["key"] == "exiftool":
            ok = bool(install_exiftool(SUP / "exiftool", t["latest"]))
        else:
            try:
                (SUP / "detect_rules.txt").write_text(t["_text"], encoding="utf-8")
                ok = True
            except OSError:
                ok = False
        lines.append(f"{t['name']}: updated to {t['latest']}." if ok else
                     f"{t['name']}: download failed, kept {t['current']}.")
        if ok:
            t.update(current=t["latest"], update=False)
    engine.reset_tool_cache()
    if lines:
        log("update: " + " ".join(lines))
    return lines


def run_update(auto=False, force=False):
    """Check everything and install tool updates. auto: only if the last check was over a week ago.
    Returns a summary string (the app itself is only installed from the Check for updates window)."""
    SUP.mkdir(parents=True, exist_ok=True)
    if auto:
        try:
            if time.time() - int(_read(STAMP) or 0) < WEEK:
                return ""
        except ValueError:
            pass
    # Only one update at a time; clear a stale lock older than an hour.
    try:
        if LOCK.exists() and time.time() - LOCK.stat().st_mtime > 3600:
            LOCK.rmdir()
        LOCK.mkdir()
    except OSError:
        return "An update check is already running."
    try:
        if not _reachable():
            return "Couldn't reach the update servers. Check your internet connection and try again."
        STAMP.write_text(str(int(time.time())), encoding="utf-8")
        status = check_all()
        done = {l.split(":")[0] for l in install_tools(status, force_ffmpeg=force)}
        summary = []
        for t in status["tools"]:
            if t["error"]:
                summary.append(f"{t['name']}: {t['error']}.")
            elif t["name"] in done:
                summary.append(f"{t['name']}: updated to {t['current']}." if not t["update"] else
                               f"{t['name']}: download failed.")
            else:
                summary.append(f"{t['name']}: up to date ({t['current']}).")
        a = status["app"]
        summary.append(a["error"] or (f"{APP_NAME}: version {a['latest']} is available (you have {APP_VERSION})."
                                      if a["update"] else f"{APP_NAME}: up to date ({APP_VERSION})."))
        text = "\n".join(summary)
        log("update: " + " ".join(summary))
        return text
    finally:
        try:
            LOCK.rmdir()
        except OSError:
            pass


def start_background_check():
    """Weekly background check, in a separate process so it outlives the caller."""
    try:
        args = [sys.executable] + ([] if FROZEN else ["-m", "dlrm"]) + ["--auto-update"]
        extra = ({"creationflags": 0x08000000 | 0x00000008} if os.name == "nt"   # no window, detached
                 else {"start_new_session": True})
        subprocess.Popen(args, cwd=str(APP_DIR), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, **extra)
    except OSError:
        pass


# ---------------------------------------------------------------- own releases (private repo aware)

def github_token():
    """A GitHub token for the private repo: GH_TOKEN / GITHUB_TOKEN, else the GitHub CLI's saved login."""
    for var in ("GH_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(var):
            return os.environ[var].strip()
    candidates = [shutil.which("gh"), r"C:\Program Files\GitHub CLI\gh.exe",
                  os.path.expandvars(r"%LOCALAPPDATA%\Programs\GitHub CLI\gh.exe"),
                  "/opt/homebrew/bin/gh", "/usr/local/bin/gh"]
    for gh in candidates:
        if gh and os.path.isfile(gh):
            try:
                p = run([gh, "auth", "token", "--hostname", "github.com"], timeout=20)
                tok = p.stdout.decode().strip()
                if p.returncode == 0 and tok:
                    return tok
            except Exception:
                pass
    return ""


def github_api(path, accept="application/vnd.github+json", timeout=30):
    req = urllib.request.Request(f"https://api.github.com/{path.lstrip('/')}",
                                 headers={**UA, "Accept": accept, "X-GitHub-Api-Version": "2022-11-28"})
    tok = github_token()
    if tok:
        # Unredirected: the token must not follow the asset download's redirect to the storage server.
        req.add_unredirected_header("Authorization", f"Bearer {tok}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def latest_app_version():
    """Newest release tag of GITHUB_REPO (without the leading v), or ''."""
    import json
    try:
        data = json.loads(github_api(f"repos/{GITHUB_REPO}/releases/latest"))
        return re.sub(r"^v", "", data.get("tag_name", ""))
    except Exception:
        pass
    try:  # public repo without a login
        u = final_url(f"https://github.com/{GITHUB_REPO}/releases/latest")
        m = re.search(r"/releases/tag/v?([^/?#]+)", u)
        return m.group(1) if m else ""
    except Exception:
        return ""


def download_release_asset(version, timeout=900, asset=RELEASE_ASSET):
    import json
    try:
        rel = json.loads(github_api(f"repos/{GITHUB_REPO}/releases/tags/v{version}"))
        a = next(x for x in rel.get("assets", []) if x.get("name") == asset)
        return github_api(f"repos/{GITHUB_REPO}/releases/assets/{a['id']}",
                          accept="application/octet-stream", timeout=timeout)
    except Exception:
        return fetch(f"https://github.com/{GITHUB_REPO}/releases/download/v{version}/{asset}", timeout)


# ---------------------------------------------------------------- self-update

def install_app_update(version):
    """Download the release zip, then swap it in with a helper script once this process exits.
    Returns an error message, or '' if the app should now quit."""
    if not FROZEN:
        return "Self-update only works in the built app. Use git pull when running from source."
    if IS_MAC:
        return _mac_update(version)
    work = Path(tempfile.mkdtemp(prefix="dlrm-upd-"))
    try:
        with zipfile.ZipFile(io.BytesIO(download_release_asset(version))) as z:
            z.extractall(work / "new")
    except Exception as e:
        shutil.rmtree(work, ignore_errors=True)
        return f"Download failed: {e}"
    exe = next((work / "new").rglob(f"{APP_NAME}.exe"), None)
    if not exe:
        shutil.rmtree(work, ignore_errors=True)
        return "The downloaded update didn't contain the program."
    src, dst, pid = exe.parent, APP_DIR, os.getpid()
    script = work / "apply.cmd"
    # Windows' own tools by full path: with Git's Unix tools on PATH, plain "find" is a different program.
    sysdir = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    helper_log = SUP / "update_helper.log"
    script.write_text(
        "@echo off\r\n"
        f"echo %date% %time% waiting for {pid} >> \"{helper_log}\"\r\n"
        ":wait\r\n"
        # No pipes: in a hidden background process "find" waits forever on a pipe, so go through a file.
        f"\"{sysdir / 'tasklist.exe'}\" /FI \"PID eq {pid}\" /NH > \"{work / 'pid.txt'}\" 2>&1\r\n"
        f"\"{sysdir / 'findstr.exe'}\" /C:\" {pid} \" \"{work / 'pid.txt'}\" >nul "
        f"&& (\"{sysdir / 'PING.EXE'}\" -n 2 127.0.0.1 >nul & goto wait)\r\n"
        f"\"{sysdir / 'Robocopy.exe'}\" \"{src}\" \"{dst}\" /E /R:5 /W:1 /NFL /NDL /NJH /NJS >nul\r\n"
        f"echo %date% %time% copied, robocopy code %errorlevel% >> \"{helper_log}\"\r\n"
        f"start \"\" \"{dst / (APP_NAME + '.exe')}\"\r\n"
        f"rmdir /s /q \"{work}\"\r\n", encoding="mbcs" if os.name == "nt" else "utf-8")
    flags = 0x08000000 | 0x00000008                     # no window, detached
    try:   # also leave any job object we were started in, so the helper outlives this process
        subprocess.Popen(["cmd", "/c", str(script)], creationflags=flags | 0x01000000, close_fds=True)
    except OSError:
        subprocess.Popen(["cmd", "/c", str(script)], creationflags=flags, close_fds=True)
    (SUP / "app_update_available").unlink(missing_ok=True)
    log(f"update: installing {APP_NAME} {version}")
    return ""


def _mac_update(version):
    """macOS: save the new .dmg in Downloads and open it; the user drags the app into Applications."""
    asset = MAC_ASSETS[mac_arch()]
    try:
        data = download_release_asset(version, asset=asset)
    except Exception as e:
        return f"Download failed: {e}"
    dmg = Path.home() / "Downloads" / asset
    dmg.write_bytes(data)
    subprocess.Popen(["open", str(dmg)])
    (SUP / "app_update_available").unlink(missing_ok=True)
    log(f"update: downloaded {asset} {version}")
    return (f"Version {version} was saved to Downloads and opened.\n\n"
            f"Quit {APP_NAME}, then drag the new {APP_NAME} into Applications and choose Replace.")
