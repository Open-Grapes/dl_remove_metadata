"""Shared paths, constants and small helpers for dl_remove_metadata."""

import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

APP_NAME = "dl_remove_metadata"
APP_VERSION = "1.0.0"

# GitHub repository this Windows port is published from (tag "v" + version,
# with dl_remove_metadata-windows.zip attached). Used for "Update Now".
GITHUB_REPO = "Open-Grapes/dl_remove_metadata"
RELEASE_ASSET = "dl_remove_metadata-windows.zip"

# Where "Check for Updates" downloads the newest AI tag list from.
RULES_URL = "https://raw.githubusercontent.com/abrahamyuen/wunderpus/main/detect_rules.txt"

SUFFIX = "_camo"

VIDEO_EXT = {"mp4", "m4v", "mov", "mkv", "avi", "webm", "wmv", "flv", "mts", "m2ts", "ts",
             "mxf", "3gp", "mpg", "mpeg", "ogv"}
AUDIO_EXT = {"mp3", "wav", "m4a", "aac", "flac", "ogg", "opus", "aif", "aiff", "wma"}
IMAGE_EXT = {"jpg", "jpeg", "png", "heic", "heif", "webp", "tif", "tiff", "gif", "avif", "dng", "jxl"}

FROZEN = getattr(sys, "frozen", False)
# Folder holding the program (the .exe when built, the repo root when run from source).
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent.parent
TOOLS = APP_DIR / "tools"
# Per-user data: updated tools, downloaded rules, settings, log.
SUP = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / APP_NAME
LOG = SUP / f"{APP_NAME}.log"

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def ensure_dirs():
    try:
        SUP.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass


def log(msg):
    ensure_dirs()
    try:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")
    except OSError:
        pass


def run(cmd, timeout=None, **kw):
    """Run a command without flashing a console window. Returns CompletedProcess (bytes)."""
    return subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          creationflags=CREATE_NO_WINDOW, timeout=timeout, **kw)


def load_settings():
    try:
        return json.loads((SUP / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(data):
    ensure_dirs()
    try:
        (SUP / "settings.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass
