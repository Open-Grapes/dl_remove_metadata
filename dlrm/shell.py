"""Explorer right-click menu ("Remove Metadata", "Check for AI Tags") and batching of multi-file selections.

Explorer starts the program once per selected file. Each start drops its file into a queue folder;
whichever start holds a named mutex collects the whole batch and handles it in one go.
"""

import ctypes
import os
import sys
import time
import uuid
from pathlib import Path

from .common import APP_DIR, APP_NAME, FROZEN, SUP

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
