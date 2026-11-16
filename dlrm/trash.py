"""Moving originals to the Recycle Bin (Windows) / Trash (macOS), and bringing them back.

Every original this app moves is recorded in trashed.jsonl (in the per-user folder) together with where it
landed in the bin, so "Restore originals" puts back exactly those files and nothing else from the bin.

Drives without a bin (network drives, USB sticks...) would delete files permanently, so originals there
are never removed.
"""

import json
import os
import struct
import threading
import time
from pathlib import Path

from .common import IS_MAC, IS_WIN, SUP, log, run

RECORDS = SUP / "trashed.jsonl"
BIN = "Trash" if IS_MAC else "Recycle Bin"
_lock = threading.Lock()


# ---------------------------------------------------------------- does this drive have a bin?

def has_bin(path):
    """'' if files on this drive can go to the bin, else the reason they can't."""
    p = Path(path).resolve()
    if IS_WIN:
        import ctypes
        anchor = p.anchor
        if anchor.startswith("\\\\"):
            return f"it's on a network location, which has no {BIN}"
        kind = ctypes.windll.kernel32.GetDriveTypeW(anchor)
        if kind != 3:   # DRIVE_FIXED; removable (2) and network (4) drives delete permanently
            return f"drive {anchor} has no {BIN} (network or removable drive)"
        return ""
    if IS_MAC:
        try:
            if p.stat().st_dev == Path.home().stat().st_dev:
                return ""
            mount = p
            while mount.parent != mount and mount.parent.stat().st_dev == p.stat().st_dev:
                mount = mount.parent
            if (mount / ".Trashes").exists():
                return ""
        except OSError:
            pass
        return f"this drive has no {BIN}"
    return f"moving to the {BIN} isn't supported on this system"


# ---------------------------------------------------------------- Windows Recycle Bin internals

def _win_bin_items(drive_anchor):
    """(original path, $R path, $I path, deletion time) for this user's bin on one drive."""
    root = Path(drive_anchor) / "$Recycle.Bin"
    items = []
    try:
        sids = [d for d in root.iterdir() if d.is_dir()]
    except OSError:
        return items
    for sid in sids:
        try:
            names = list(sid.iterdir())
        except OSError:            # another user's bin
            continue
        for info in names:
            if not info.name.startswith("$I"):
                continue
            try:
                data = info.read_bytes()
                version, _size, filetime = struct.unpack_from("<qqq", data, 0)
                if version == 2:
                    (n,) = struct.unpack_from("<i", data, 24)
                    orig = data[28:28 + 2 * n].decode("utf-16-le").rstrip("\0")
                else:
                    orig = data[24:24 + 520].decode("utf-16-le").split("\0", 1)[0]
            except (OSError, struct.error, UnicodeDecodeError):
                continue
            items.append((orig, info.with_name("$R" + info.name[2:]), info, filetime))
    return items


def _win_find(original):
    """Newest bin entry for this original path, or None."""
    want = os.path.normcase(str(original))
    hits = [it for it in _win_bin_items(Path(original).anchor) if os.path.normcase(it[0]) == want]
    return max(hits, key=lambda it: it[3]) if hits else None


def _win_trash(path):
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_ushort),
                    ("fAnyOperationsAborted", wintypes.BOOL), ("hNameMappings", ctypes.c_void_p),
                    ("lpszProgressTitle", wintypes.LPCWSTR)]
    FO_DELETE, FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 3, 0x4, 0x10, 0x40, 0x400
    op = SHFILEOPSTRUCTW(None, FO_DELETE, path + "\0", None,
                         FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI, False, None, None)
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if rc != 0 or op.fAnyOperationsAborted or os.path.exists(path):
        return None, f"couldn't move it to the {BIN} (error {rc})"
    hit = _win_find(path)
    return (str(hit[1]) if hit else None), ""


def _mac_trash(path):
    p = run(["/usr/bin/osascript", "-e", "on run argv",
             "-e", 'tell application "Finder" to set t to delete (POSIX file (item 1 of argv) as alias)',
             "-e", "return POSIX path of (t as alias)", "-e", "end run", path], timeout=60)
    if p.returncode == 0 and not os.path.exists(path):
        return p.stdout.decode("utf-8", "replace").strip() or None, ""
    msg = p.stderr.decode("utf-8", "replace").strip().splitlines()
    return None, f"couldn't move it to the {BIN}" + (f" ({msg[-1]})" if msg else "")


# ---------------------------------------------------------------- public

def move_to_trash(path):
    """Move a file to the bin and remember it. Returns '' on success, else why the file was kept."""
    path = str(Path(path).resolve())
    why = has_bin(path)
    if why:
        return why
    where, err = _win_trash(path) if IS_WIN else _mac_trash(path) if IS_MAC else (None, has_bin(path))
    if err:
        return err
    with _lock:
        SUP.mkdir(parents=True, exist_ok=True)
        with open(RECORDS, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"original": path, "trash": where, "time": time.time()}) + "\n")
    return ""


def _load():
    try:
        lines = RECORDS.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _locate(rec):
    """Where this original is in the bin now, or None if it's no longer there."""
    t = rec.get("trash")
    if t and os.path.exists(t):
        return t
    if IS_WIN:                      # fall back to looking it up by its original path
        hit = _win_find(rec["original"])
        if hit and hit[1].exists():
            return str(hit[1])
    return None


def pending():
    """Originals this app moved to the bin that are still there (newest record per file)."""
    latest = {}
    for rec in _load():
        latest[os.path.normcase(rec.get("original", ""))] = rec
    return [r for r in latest.values() if r.get("original") and _locate(r)]


def restore_all():
    """Put every recorded original back in its folder. Returns (restored paths, [(path, reason)])."""
    restored, problems = [], []
    with _lock:
        for rec in pending():
            orig, src = rec["original"], _locate(rec)
            if os.path.exists(orig):
                problems.append((orig, "a file with that name is already in the folder"))
                continue
            try:
                Path(orig).parent.mkdir(parents=True, exist_ok=True)
                os.replace(src, orig)
                if IS_WIN:   # drop the bin's bookkeeping file for it
                    info = Path(src).with_name("$I" + Path(src).name[2:])
                    try:
                        info.unlink()
                    except OSError:
                        pass
                restored.append(orig)
                log(f"RESTORED {orig}")
            except OSError as e:
                problems.append((orig, str(e)))
        # Keep only records whose file is still in the bin (and not already restored).
        keep = [r for r in _load() if r.get("original") not in restored and _locate(r)]
        try:
            RECORDS.write_text("".join(json.dumps(r) + "\n" for r in keep), encoding="utf-8")
        except OSError:
            pass
    return restored, problems
