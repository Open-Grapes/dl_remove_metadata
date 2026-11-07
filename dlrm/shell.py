"""Right-click menu ("Remove Metadata", "Check for AI Tags"): Explorer verbs on Windows, Finder Quick Actions
on macOS. Also batching of multi-file selections on Windows.

Windows Explorer starts the program once per selected file. Each start drops its file into a queue folder;
whichever start holds a named mutex collects the whole batch and handles it in one go.
"""

import ctypes
import os
import sys
import time
import uuid
from pathlib import Path

from .common import APP_BUNDLE, APP_DIR, APP_NAME, FROZEN, IS_MAC, SUP

QUEUE = SUP / "queue"
MUTEX_NAME = f"Local\\{APP_NAME}-queue"
VERBS = {"remove": "Remove Metadata", "check": "Check for AI Tags"}
KEYS = [r"Software\Classes\*\shell", r"Software\Classes\Directory\shell"]


def launch_command(mode):
    exe = Path(sys.executable)
    if FROZEN:
        return f'"{exe}" --queue {mode} "%1"'
    pyw = exe.with_name("pythonw.exe")
    return f'"{pyw if pyw.exists() else exe}" "{APP_DIR / "run.py"}" --queue {mode} "%1"'


def icon_path():
    if FROZEN:
        return f'"{sys.executable}",0'
    ico = APP_DIR / "assets" / "icon.ico"
    return str(ico) if ico.exists() else ""


def install_menu():
    """Create or refresh the right-click entries (current user only, no admin needed)."""
    if IS_MAC:
        return _mac_install()
    import winreg
    for base in KEYS:
        for mode, label in VERBS.items():
            key = f"{base}\\{APP_NAME}.{mode}"
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as k:
                winreg.SetValueEx(k, "MUIVerb", 0, winreg.REG_SZ, label)
                winreg.SetValueEx(k, "MultiSelectModel", 0, winreg.REG_SZ, "Player")
                ico = icon_path()
                if ico:
                    winreg.SetValueEx(k, "Icon", 0, winreg.REG_SZ, ico)
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key + r"\command") as k:
                winreg.SetValueEx(k, "", 0, winreg.REG_SZ, launch_command(mode))


def remove_menu():
    if IS_MAC:
        return _mac_remove()
    import winreg
    for base in KEYS:
        for mode in VERBS:
            key = f"{base}\\{APP_NAME}.{mode}"
            for sub in (key + r"\command", key):
                try:
                    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
                except OSError:
                    pass


def menu_installed():
    if IS_MAC:
        return _mac_installed()
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{KEYS[0]}\\{APP_NAME}.remove\\command") as k:
            return winreg.QueryValue(k, None) == launch_command("remove")
    except OSError:
        return False


# ---------------------------------------------------------------- batching

def enqueue(mode, path):
    QUEUE.mkdir(parents=True, exist_ok=True)
    name = f"{time.time_ns()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    tmp = QUEUE / (name + ".tmp")
    tmp.write_text(f"{mode}\t{path}", encoding="utf-8")
    os.replace(tmp, QUEUE / (name + ".txt"))


def _pending():
    try:
        return sorted(QUEUE.glob("*.txt"))
    except OSError:
        return []


def collect_batch(settle=1.0, max_wait=10.0):
    """Wait for the queue to stop growing, then take every queued item. Returns {mode: [paths]}.
    Only the process holding the mutex collects, so each item is handled exactly once."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = ctypes.c_void_p
    k32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    k32.ReleaseMutex.argtypes = [ctypes.c_void_p]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    h = k32.CreateMutexW(None, False, MUTEX_NAME)
    k32.WaitForSingleObject(h, 0xFFFFFFFF)
    try:
        start, last_n, last_change = time.time(), -1, time.time()
        while time.time() - start < max_wait:
            n = len(_pending())
            if n != last_n:
                last_n, last_change = n, time.time()
            elif time.time() - last_change >= settle:
                break
            time.sleep(0.15)
        batch = {}
        for f in _pending():
            try:
                mode, _, path = f.read_text(encoding="utf-8").partition("\t")
                f.unlink()
            except OSError:
                continue
            if path and path not in batch.setdefault(mode, []):
                batch[mode].append(path)
        return batch
    finally:
        k32.ReleaseMutex(h)
        k32.CloseHandle(h)


# ---------------------------------------------------------------- macOS Finder Quick Actions

SERVICES = Path.home() / "Library" / "Services"
MAC_SCRIPT = """# dl_remove_metadata — {menu} Quick Action
CONF="$HOME/Library/Application Support/dl_remove_metadata/app_path"
APP=""
[ -f "$CONF" ] &amp;&amp; APP="$(cat "$CONF")"
BIN="$APP/Contents/MacOS/dl_remove_metadata"
[ -x "$BIN" ] || BIN="/Applications/dl_remove_metadata.app/Contents/MacOS/dl_remove_metadata"
if [ ! -x "$BIN" ]; then
  /usr/bin/osascript -e 'display alert "dl_remove_metadata not found" message "Open the dl_remove_metadata app once so this menu can find it again."'
  exit 0
fi
nohup "$BIN" --quick {mode} "$@" &lt;/dev/null &gt;/dev/null 2&gt;&amp;1 &amp;
"""


def _workflow_dir(mode):
    return SERVICES / f"{VERBS[mode]}.workflow"


def _mac_templates():
    base = APP_DIR / "assets" / "mac"
    return ((base / "document.wflow.template").read_text(encoding="utf-8"),
            (base / "Info.plist.template").read_text(encoding="utf-8"))


def _mac_install():
    """Install both Quick Actions into ~/Library/Services and remember where the app is."""
    import subprocess
    SUP.mkdir(parents=True, exist_ok=True)
    if APP_BUNDLE:
        (SUP / "app_path").write_text(str(APP_BUNDLE), encoding="utf-8")
    doc_t, info_t = _mac_templates()
    changed = False
    for mode, menu in VERBS.items():
        d = _workflow_dir(mode) / "Contents"
        doc = doc_t.replace("@@SCRIPT@@", MAC_SCRIPT.format(menu=menu, mode=mode))
        info = info_t.replace("@@MENU@@", menu)
        cur = (d / "document.wflow").read_text(encoding="utf-8") if (d / "document.wflow").exists() else ""
        if cur != doc:
            d.mkdir(parents=True, exist_ok=True)
            (d / "document.wflow").write_text(doc, encoding="utf-8")
            (d / "Info.plist").write_text(info, encoding="utf-8")
            changed = True
    if changed:   # make Finder notice the new menu items
        subprocess.run(["/System/Library/CoreServices/pbs", "-flush"], capture_output=True)


def _mac_remove():
    import shutil
    import subprocess
    for mode in VERBS:
        shutil.rmtree(_workflow_dir(mode), ignore_errors=True)
    subprocess.run(["/System/Library/CoreServices/pbs", "-flush"], capture_output=True)


def _mac_installed():
    return all((_workflow_dir(m) / "Contents" / "document.wflow").exists() for m in VERBS)


def clear_quarantine():
    """macOS: let the bundled ffmpeg / exiftool run after the app was downloaded (like 'Open Anyway')."""
    if IS_MAC and APP_BUNDLE:
        import subprocess
        subprocess.run(["/usr/bin/xattr", "-dr", "com.apple.quarantine", str(APP_BUNDLE)], capture_output=True)
