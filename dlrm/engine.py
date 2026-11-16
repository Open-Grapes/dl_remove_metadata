"""Metadata removal and AI-tag checking (Windows and macOS).

Clean copies are written next to each original (or into an output folder): clip.mp4 -> clip_camo.mp4.
Originals are never modified.
"""

import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .common import (AUDIO_EXT, EXE, IMAGE_EXT, IS_MAC, IS_WIN, SUFFIX, SUP, TOOLS, APP_DIR, VIDEO_EXT, log, run)

# ---------------------------------------------------------------- tools

_ffmpeg = None
_exiftool = None
_tried_update = False


def _works(cmd):
    try:
        return run(cmd, timeout=30).returncode == 0
    except (OSError, ValueError, Exception):
        return False


def reset_tool_cache():
    global _ffmpeg, _exiftool
    _ffmpeg = _exiftool = None


def find_ffmpeg():
    """Newest working ffmpeg: an updated copy in the per-user folder first, then the bundled one, then PATH."""
    global _ffmpeg, _tried_update
    if _ffmpeg:
        return _ffmpeg
    for c in (SUP / "bin" / f"ffmpeg{EXE}", TOOLS / f"ffmpeg{EXE}", shutil.which("ffmpeg")):
        if c and Path(c).is_file() and _works([str(c), "-hide_banner", "-version"]):
            _ffmpeg = str(c)
            return _ffmpeg
    if not _tried_update:  # nothing works: try downloading one now
        _tried_update = True
        from . import updater
        updater.run_update(force=True)
        return find_ffmpeg()
    return None


def _exiftool_cmd(c):
    """Command list that runs exiftool at c: the Windows .exe directly, the Perl script through perl."""
    if IS_WIN or str(c).lower().endswith(".exe"):
        return [str(c)]
    perl = "/usr/bin/perl" if Path("/usr/bin/perl").exists() else (shutil.which("perl") or "perl")
    return [perl, str(c)]


def find_exiftool():
    """Command list for a working exiftool, or None."""
    global _exiftool
    if _exiftool:
        return _exiftool
    name = f"exiftool{EXE}"
    for c in (SUP / "exiftool" / name, TOOLS / "exiftool" / name, shutil.which("exiftool")):
        if c and Path(c).is_file():
            cmd = _exiftool_cmd(c) if Path(c).parent.name == "exiftool" else [str(c)]
            if _works(cmd + ["-ver"]):
                _exiftool = cmd
                return _exiftool
    return None


def exiftool(args, timeout=600):
    """Run exiftool with arguments passed through a UTF-8 argfile, so any file name works."""
    fd, argfile = tempfile.mkstemp(prefix="dlrm-", suffix=".args")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for a in args:
                fh.write(str(a).replace("\n", " ") + "\n")
        p = run(find_exiftool() + ["-charset", "filename=utf8", "-@", argfile], timeout=timeout)
        return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")
    finally:
        try:
            os.remove(argfile)
        except OSError:
            pass


def tool_versions():
    fv = ev = rv = "not found"
    if find_ffmpeg():
        out = run([_ffmpeg, "-hide_banner", "-version"]).stdout.decode("utf-8", "replace").split()
        if len(out) > 2:
            fv = re.sub(r"-(essentials|full|https?:).*$", "", out[2])
    if find_exiftool():
        rc, out, _ = exiftool(["-ver"])
        ev = out.strip() or ev
    r = find_rules()
    if r:
        rv = rules_version(r) or "?"
    return f"ffmpeg {fv} · exiftool {ev} · AI tag list {rv}"


# ---------------------------------------------------------------- files

_MAGIC = [
    (b"\xff\xd8\xff", "image"), (b"\x89PNG", "image"), (b"GIF8", "image"), (b"II*\x00", "image"),
    (b"MM\x00*", "image"), (b"ID3", "audio"), (b"fLaC", "audio"), (b"OggS", "audio"),
    (b"\x1aE\xdf\xa3", "video"), (b"FORM", "audio"),
]


def kind_of(path):
    """video | audio | image | other"""
    ext = Path(path).suffix.lower().lstrip(".")
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in IMAGE_EXT:
        return "image"
    try:
        with open(path, "rb") as fh:
            head = fh.read(16)
    except OSError:
        return "other"
    for magic, kind in _MAGIC:
        if head.startswith(magic):
            return kind
    if head[:4] == b"RIFF":
        return {b"WAVE": "audio", b"AVI ": "video", b"WEBP": "image"}.get(head[8:12], "other")
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"heic", b"heix", b"mif1", b"avif"):
            return "image"
        return "audio" if brand.startswith(b"M4A") else "video"
    return "other"


def _hidden(p):
    if p.name.startswith("."):
        return True
    try:
        return bool(p.stat().st_file_attributes & (stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM))
    except (OSError, AttributeError):
        return False


def is_cleaned(path):
    return Path(path).stem.endswith(SUFFIX)


def expand(items, include_cleaned=False):
    """Expand folders into the media files inside them (recursively), skipping hidden and already-cleaned files."""
    out = []
    for item in items:
        p = Path(item)
        if p.is_dir():
            found = []
            for root, dirs, files in os.walk(p):
                rp = Path(root)
                dirs[:] = [d for d in dirs if not _hidden(rp / d)]
                for name in files:
                    f = rp / name
                    if _hidden(f) or (not include_cleaned and is_cleaned(f)):
                        continue
                    if kind_of(f) != "other":
                        found.append(str(f))
            out.extend(sorted(found, key=str.lower))
        elif p.is_file():
            out.append(str(p))
    return out


def out_path(f, outdir=None):
    p = Path(f)
    d = Path(outdir) if outdir else p.parent
    return str(d / (p.stem + SUFFIX + p.suffix))


# ---------------------------------------------------------------- removing metadata

class CleanError(Exception):
    pass


def _rm(path):
    try:
        os.remove(path)
    except OSError:
        pass


def strip_av(src, dst):
    ext = Path(src).suffix.lower().lstrip(".")
    extra = ["-movflags", "+faststart"] if ext in ("mp4", "m4v", "mov", "m4a", "3gp") else []
    cmd = [find_ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error", "-i", src,
           "-map", "0:v?", "-map", "0:a?", "-map", "0:s?",
           "-map_metadata", "-1", "-map_metadata:s:v", "-1", "-map_metadata:s:a", "-1", "-map_chapters", "-1",
           "-c", "copy", "-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact",
           *extra, "-y", dst]
    p = run(cmd)
    if p.returncode != 0:
        _rm(dst)
        lines = [l for l in p.stderr.decode("utf-8", "replace").splitlines() if l.strip()]
        raise CleanError(lines[-1] if lines else "ffmpeg failed")


def strip_image(src, dst):
    ext = Path(src).suffix.lower().lstrip(".")
    try:
        shutil.copyfile(src, dst)
    except OSError:
        raise CleanError("could not copy file")
    # Remove every metadata block (EXIF, XMP, IPTC, C2PA/JUMBF, maker notes...),
    # then put back only the colour profile and rotation so the picture looks the same.
    rc, _, err = exiftool(["-q", "-q", "-m", "-all=", "-tagsFromFile", "@", "-ICC_Profile", "-Orientation",
                           "-overwrite_original", dst])
    if rc != 0:
        rc, _, err = exiftool(["-q", "-q", "-m", "-all=", "-overwrite_original", dst])
        if rc != 0:
            _rm(dst)
            lines = [l for l in err.splitlines() if l.strip()]
            raise CleanError(lines[-1] if lines else "exiftool failed")
    # In a TIFF, -all= leaves IFD0 alone because the picture's layout lives there,
    # so remove its identifying tags by name. (Not DNG: raw converters need Make/Model.)
    if ext in ("tif", "tiff"):
        exiftool(["-q", "-q", "-m", "-overwrite_original"] +
                 [f"-IFD0:{t}=" for t in ("Make", "Model", "Software", "ModifyDate", "Artist", "Copyright",
                                          "HostComputer", "ImageDescription", "DocumentName", "PageName",
                                          "ImageUniqueID")] + [dst])
    # Safety net: if any C2PA / XMP / IPTC data survived (e.g. an unusual PNG chunk), rebuild losslessly with ffmpeg.
    _, left, _ = exiftool(["-q", "-q", "-m", "-s3", "-JUMBF:all", "-C2PA:all", "-XMP:all", "-IPTC:all", dst])
    if left.strip() and ext == "png" and find_ffmpeg():
        tmp = dst + ".tmp.png"
        p = run([find_ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error", "-i", src, "-map_metadata", "-1",
                 "-fflags", "+bitexact", "-flags", "+bitexact", "-c:v", "png", "-y", tmp])
        if p.returncode == 0:
            os.replace(tmp, dst)
        else:
            _rm(tmp)


@dataclass
class Result:
    status: str          # OK | SKIP | FAIL
    file: str
    output: str = ""
    reason: str = ""
    removed: bool = False      # original moved to the Recycle Bin / Trash


def process_one(f, outdir=None, remove_original=False):
    if is_cleaned(f):
        return Result("SKIP", f, reason="already cleaned")
    kind = kind_of(f)
    if kind == "other":
        log(f"SKIP {f}")
        return Result("SKIP", f, reason="not a video, audio or image file")
    out = out_path(f, outdir)
    try:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return Result("FAIL", f, reason="can't write to the destination folder")
    try:
        if kind in ("video", "audio"):
            if not find_ffmpeg():
                raise CleanError("ffmpeg is not working — open dl_remove_metadata and choose Check for Updates")
            strip_av(f, out)
        else:
            if not find_exiftool():
                raise CleanError("exiftool is missing — open dl_remove_metadata and choose Check for Updates")
            strip_image(f, out)
    except CleanError as e:
        log(f"FAIL {f} :: {e}")
        return Result("FAIL", f, reason=str(e))
    except Exception as e:  # unexpected (permissions, disk full...)
        _rm(out)
        log(f"FAIL {f} :: {e}")
        return Result("FAIL", f, reason=str(e))
    log(f"OK {f}")
    res = Result("OK", f, output=out)
    if remove_original:
        # Only after a clean copy really exists; the original goes to the Recycle Bin / Trash, not gone for good.
        try:
            made = Path(out).is_file() and Path(out).stat().st_size > 0
        except OSError:
            made = False
        err = move_to_trash(f) if made else "the clean copy wasn't found"
        if err:
            res.reason = f"original kept: {err}"
            log(f"KEEP {f} :: {err}")
        else:
            res.removed = True
            log(f"TRASHED {f}")
    return res


def move_to_trash(path):
    """Move a file to the Recycle Bin (Windows) or Trash (macOS). Returns '' on success, else the reason."""
    path = str(Path(path).resolve())
    if IS_WIN:
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
        if rc == 0 and not op.fAnyOperationsAborted and not os.path.exists(path):
            return ""
        return f"couldn't move it to the Recycle Bin (error {rc})"
    if IS_MAC:
        p = run(["/usr/bin/osascript", "-e", "on run argv",
                 "-e", 'tell application "Finder" to delete (POSIX file (item 1 of argv) as alias)',
                 "-e", "end run", path], timeout=60)
        if p.returncode == 0 and not os.path.exists(path):
            return ""
        msg = p.stderr.decode("utf-8", "replace").strip().splitlines()
        return "couldn't move it to the Trash" + (f" ({msg[-1]})" if msg else "")
    return "moving to the trash isn't supported on this system"


# ---------------------------------------------------------------- checking (read-only)

def rules_version(path):
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r"^#\s*version:\s*(\S+)", line)
                if m:
                    return m.group(1)
    except OSError:
        pass
    return ""


def _has_ai_rule(path):
    try:
        return any(re.match(r"^ai\s", l) for l in open(path, encoding="utf-8"))
    except OSError:
        return False


def find_rules():
    """The downloaded AI tag list if it is newer than the bundled one."""
    b, u = APP_DIR / "detect_rules.txt", SUP / "detect_rules.txt"
    best = b if b.is_file() else None
    if u.is_file() and _has_ai_rule(u) and (best is None or rules_version(u) > rules_version(b)):
        best = u
    return best


@dataclass
class Rule:
    kind: str
    tag: re.Pattern
    value: object   # compiled pattern, or None for any value
    label: str


_rules_cache = {}


def load_rules(path):
    key = (str(path), os.path.getmtime(path))
    if key in _rules_cache:
        return _rules_cache[key]
    rules, needles = [], []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\r\n")
            if re.match(r"^\s*(#|$)", line):
                continue
            p = re.split(r"\t+| {2,}", line)
            if p[0] == "bytes" and len(p) >= 2:
                needles.append((p[1], " ".join(p[3:]) if len(p) > 3 else p[1]))
                continue
            if len(p) < 4 or p[0] not in ("ai", "info"):
                continue
            try:
                rules.append(Rule(p[0], re.compile(p[1].lower()),
                                  None if p[2] == "*" else re.compile(p[2].lower()), " ".join(p[3:])))
            except re.error:
                continue
    _rules_cache.clear()
    _rules_cache[key] = (rules, needles)
    return rules, needles


@dataclass
class CheckResult:
    status: str                  # AI | META | CLEAN | SKIP | FAIL
    file: str
    tags: list = field(default_factory=list)   # (kind, label, group:tag, value)
    reason: str = ""


CHUNK = 8 * 1024 * 1024


def _edge_bytes(f):
    with open(f, "rb") as fh:
        head = fh.read(CHUNK)
        size = fh.seek(0, os.SEEK_END)
        if size <= CHUNK:
            return head
        fh.seek(max(CHUNK, size - CHUNK))
        return head + fh.read(CHUNK)


def check_one(f):
    if kind_of(f) == "other":
        return CheckResult("SKIP", f, reason="not a video, audio or image file")
    if not find_exiftool():
        return CheckResult("FAIL", f, reason="exiftool is missing — open dl_remove_metadata and choose Check for Updates")
    rp = find_rules()
    if not rp:
        return CheckResult("FAIL", f, reason="the AI tag list is missing — choose Check for Updates")
    rules, needles = load_rules(rp)

    rc, out, _ = exiftool(["-q", "-q", "-m", "-a", "-u", "-args", "-G1", "-api", r"Filter=tr/\x00-\x1f/ /",
                           "--File:all", "--System:all", "--ExifTool:all", "--Composite:all", f])
    found = []
    for line in out.splitlines():
        line = line[1:] if line.startswith("-") else line
        tag, eq, val = line.partition("=")
        if not eq:
            continue
        lt, lv = tag.lower(), val.lower()
        for r in rules:
            if r.tag.search(lt) and (r.value is None or r.value.search(lv)):
                if len(val) > 160:
                    val = val[:157] + "..."
                found.append((r.kind, r.label, tag, val))
                break

    # Raw text search for AI data ExifTool can't read yet (first and last 8 MB only, so big videos stay fast).
    if needles:
        try:
            data = _edge_bytes(f)
        except OSError:
            data = b""
        labels = {t[1] for t in found}
        for needle, label in needles:
            if label in labels:
                continue
            if needle.encode("utf-8") in data:
                found.append(("ai", label, "(raw data)", f'found "{needle}" inside the file'))
                labels.add(label)

    if any(t[0] == "ai" for t in found):
        log(f"CHECK AI {f}")
        return CheckResult("AI", f, found)
    return CheckResult("META" if found else "CLEAN", f, found)
