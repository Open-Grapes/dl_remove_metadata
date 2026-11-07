"""Keeps ffmpeg, exiftool and the AI tag list current, and checks GitHub for a newer dl_remove_metadata.

Updated copies go to %LOCALAPPDATA%\\dl_remove_metadata, so the program folder itself is never modified
(and the bundled versions remain as a fallback). Also used by build.py to fetch the bundled tools.
"""

import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

from .common import (APP_DIR, APP_NAME, APP_VERSION, FROZEN, GITHUB_REPO, RELEASE_ASSET, RULES_URL, SUP, TOOLS,
                     log, run)

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


def fetch(url, timeout=30):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def fetch_text(url, timeout=20):
    return fetch(url, timeout).decode("utf-8", "replace")


def final_url(url, timeout=20):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA, method="HEAD"), timeout=timeout) as r:
        return r.geturl()


def _vtuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or ""))


def newer(a, b):
    return _vtuple(a) > _vtuple(b)


def _read(p):
    try:
        return Path(p).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


# ---------------------------------------------------------------- ffmpeg

def latest_ffmpeg():
    return fetch_text(FFMPEG_VERSION_URL).strip()


def install_ffmpeg(dest, version):
    """Download the gyan.dev 'essentials' build of `version` into dest/ffmpeg.exe. Returns True on success."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
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
    try:
        return run([str(exe), "-ver"], timeout=60).stdout.decode().strip()
    except Exception:
        return ""


def install_exiftool(dest, version):
    """Download the Windows exiftool package into dest/ (exiftool.exe + exiftool_files). Returns version or ''."""
    dest = Path(dest)
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


# ---------------------------------------------------------------- the update check

def update_available():
    """Newer app version recorded by the last check, or ''."""
    v = _read(SUP / "app_update_available")
    return v if v and newer(v, APP_VERSION) else ""


def run_update(auto=False, force=False):
    """Check everything. auto: only if the last check was over a week ago. Returns a summary string."""
    from . import engine
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
        try:
            fetch(EXIFTOOL_VERSION_URL, timeout=10)
        except Exception:
            return "Couldn't reach the update servers. Check your internet connection and try again."
        STAMP.write_text(str(int(time.time())), encoding="utf-8")
        summary = []

        # ---------- ffmpeg
        try:
            ff_latest = latest_ffmpeg()
        except Exception:
            ff_latest = ""
        ff_current = _read(SUP / "bin" / "ffmpeg.version") or _read(TOOLS / "ffmpeg.version")
        engine.reset_tool_cache()
        engine._tried_update = True  # don't recurse into another update from find_ffmpeg
        ff_works = bool(engine.find_ffmpeg())
        if not ff_latest:
            summary.append("ffmpeg: couldn't check for a new version.")
        elif newer(ff_latest, ff_current) or not ff_works or force:
            if install_ffmpeg(SUP / "bin", ff_latest):
                summary.append(f"ffmpeg: updated to {ff_latest}.")
            else:
                summary.append("ffmpeg: download failed, kept the current version.")
        else:
            summary.append(f"ffmpeg: up to date ({ff_current}).")

        # ---------- exiftool
        try:
            ex_latest = latest_exiftool()
        except Exception:
            ex_latest = ""
        ex_exe = engine.find_exiftool()
        ex_current = exiftool_version(ex_exe) if ex_exe else ""
        if not ex_latest:
            summary.append("exiftool: couldn't check for a new version.")
        elif not ex_current or newer(ex_latest, ex_current):
            got = install_exiftool(SUP / "exiftool", ex_latest)
            summary.append(f"exiftool: updated to {got}." if got else
                           "exiftool: download failed, kept the current version.")
        else:
            summary.append(f"exiftool: up to date ({ex_current}).")
        engine.reset_tool_cache()

        # ---------- AI tag list
        cur_path = engine.find_rules()
        rv_current = engine.rules_version(cur_path) if cur_path else ""
        try:
            text = fetch_text(RULES_URL, timeout=30)
            if not re.search(r"^#\s*version:", text, re.M) or not re.search(r"^ai\s", text, re.M):
                raise ValueError("not a rules file")
            m = re.search(r"^#\s*version:\s*(\S+)", text, re.M)
            rv_new = m.group(1)
            if rv_new > rv_current:
                (SUP / "detect_rules.txt").write_text(text, encoding="utf-8")
                summary.append(f"AI tag list: updated to version {rv_new}.")
            else:
                summary.append(f"AI tag list: up to date (version {rv_current}).")
        except Exception:
            summary.append(f"AI tag list: couldn't download a new list, kept version {rv_current or 'missing'}.")

        # ---------- dl_remove_metadata itself
        flag = SUP / "app_update_available"
        latest = latest_app_version()
        if not latest:
            flag.unlink(missing_ok=True)
            hint = "" if github_token() else " — sign in with GitHub CLI (gh auth login) to check the private repo"
            summary.append(f"{APP_NAME}: version {APP_VERSION} (couldn't check for a newer one{hint}).")
        elif newer(latest, APP_VERSION):
            flag.write_text(latest, encoding="utf-8")
            summary.append(f"{APP_NAME}: version {latest} is available (you have {APP_VERSION}).")
        else:
            flag.unlink(missing_ok=True)
            summary.append(f"{APP_NAME}: up to date ({APP_VERSION}).")

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
        subprocess.Popen(args, cwd=str(APP_DIR), creationflags=0x08000000 | 0x00000008,  # no window, detached
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


# ---------------------------------------------------------------- own releases (private repo aware)

def github_token():
    """A GitHub token for the private repo: GH_TOKEN / GITHUB_TOKEN, else the GitHub CLI's saved login."""
    for var in ("GH_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(var):
            return os.environ[var].strip()
    candidates = [shutil.which("gh"), r"C:\Program Files\GitHub CLI\gh.exe",
                  os.path.expandvars(r"%LOCALAPPDATA%\Programs\GitHub CLI\gh.exe")]
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


def download_release_asset(version, timeout=900):
    import json
    try:
        rel = json.loads(github_api(f"repos/{GITHUB_REPO}/releases/tags/v{version}"))
        asset = next(a for a in rel.get("assets", []) if a.get("name") == RELEASE_ASSET)
        return github_api(f"repos/{GITHUB_REPO}/releases/assets/{asset['id']}",
                          accept="application/octet-stream", timeout=timeout)
    except Exception:
        return fetch(f"https://github.com/{GITHUB_REPO}/releases/download/v{version}/{RELEASE_ASSET}", timeout)


# ---------------------------------------------------------------- self-update

def install_app_update(version):
    """Download the release zip, then swap it in with a helper script once this process exits.
    Returns an error message, or '' if the app should now quit."""
    if not FROZEN:
        return "Self-update only works in the built app. Use git pull when running from source."
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
    script.write_text(
        "@echo off\r\n"
        f":wait\r\ntasklist /FI \"PID eq {pid}\" | find \"{pid}\" >nul && (timeout /t 1 /nobreak >nul & goto wait)\r\n"
        f"robocopy \"{src}\" \"{dst}\" /E /R:5 /W:1 /NFL /NDL /NJH /NJS >nul\r\n"
        f"start \"\" \"{dst / (APP_NAME + '.exe')}\"\r\n"
        f"rmdir /s /q \"{work}\"\r\n", encoding="mbcs" if os.name == "nt" else "utf-8")
    subprocess.Popen(["cmd", "/c", str(script)], creationflags=0x08000000 | 0x00000008, close_fds=True)
    (SUP / "app_update_available").unlink(missing_ok=True)
    log(f"update: installing {APP_NAME} {version}")
    return ""
