"""Build dl_remove_metadata for macOS (run on a Mac; GitHub Actions does this automatically).

    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
    .venv/bin/python build_mac.py          # -> dist/MacOS/dl_remove_metadata-mac-<arch>.dmg

Builds for the Mac it runs on (Apple Silicon or Intel), downloads that Mac's ffmpeg (Martin Riedl's
static build) and ExifTool (Perl package) into the app, makes the .app and a .dmg, then smoke-tests
the built app: tool versions, checking and cleaning test files, the window, the browser version and
the Finder Quick Actions.
"""

import os
import plistlib
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dlrm import updater  # noqa: E402
from dlrm.common import APP_NAME, APP_VERSION, MAC_ASSETS  # noqa: E402

ARCH = updater.mac_arch()
TOOLS = ROOT / "tools-mac" / ARCH
DIST = ROOT / "dist" / "MacOS"
WORK = ROOT / "build-mac"
APP = WORK / "dist" / f"{APP_NAME}.app"
ICNS = ROOT / "assets" / "icon.icns"
BUNDLE_ID = "com.opengrapes.dl-remove-metadata"


def sh(*cmd, **kw):
    print("$", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run([str(c) for c in cmd], check=True, **kw)


def fetch_tools():
    TOOLS.mkdir(parents=True, exist_ok=True)
    if not (TOOLS / "ffmpeg").exists():
        v = updater.latest_ffmpeg()
        print(f"Downloading ffmpeg {v} for {ARCH} …", flush=True)
        if not updater.install_ffmpeg(TOOLS, v):
            sys.exit("ffmpeg download failed")
    if not (TOOLS / "exiftool" / "exiftool").exists():
        v = updater.latest_exiftool()
        print(f"Downloading exiftool {v} …", flush=True)
        if not updater.install_exiftool(TOOLS / "exiftool", v):
            sys.exit("exiftool download failed")


def make_icns():
    if ICNS.exists():
        return
    from PIL import Image
    img = Image.open(ROOT / "assets" / "icon.png").convert("RGBA").resize((1024, 1024), Image.LANCZOS)
    img.save(ICNS, format="ICNS")


def pyinstaller():
    shutil.rmtree(WORK, ignore_errors=True)
    sh(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
       "--name", APP_NAME, "--icon", ICNS, "--osx-bundle-identifier", BUNDLE_ID,
       "--add-data", f"{ROOT / 'dlrm' / 'web_page.html'}:dlrm",
       "--collect-all", "tkinterdnd2",
       "--distpath", WORK / "dist", "--workpath", WORK / "work", "--specpath", WORK, ROOT / "run.py")


def fill_bundle():
    res = APP / "Contents" / "Resources"
    shutil.copytree(TOOLS, res / "tools", dirs_exist_ok=True)
    (res / "assets").mkdir(exist_ok=True)
    shutil.copy2(ROOT / "assets" / "icon.png", res / "assets" / "icon.png")
    shutil.copytree(ROOT / "assets" / "mac", res / "assets" / "mac", dirs_exist_ok=True)
    for name in ("detect_rules.txt", "LICENSE", "THIRD_PARTY_NOTICES.md", "README.md"):
        shutil.copy2(ROOT / name, res / name)
    shutil.copytree(ROOT / "licenses", res / "licenses", dirs_exist_ok=True)

    plist_path = APP / "Contents" / "Info.plist"
    with open(plist_path, "rb") as fh:
        pl = plistlib.load(fh)
    pl.update({
        "CFBundleName": APP_NAME, "CFBundleDisplayName": APP_NAME,
        "CFBundleShortVersionString": APP_VERSION, "CFBundleVersion": APP_VERSION,
        "LSMinimumSystemVersion": "11.0", "NSHighResolutionCapable": True,
        # Lets files and folders be dropped on the Dock icon / opened with "Open With".
        "CFBundleDocumentTypes": [{"CFBundleTypeName": "Media and folders", "CFBundleTypeRole": "Viewer",
                                   "LSHandlerRank": "Alternate",
                                   "LSItemContentTypes": ["public.movie", "public.audio", "public.image",
                                                          "public.folder"]}],
    })
    with open(plist_path, "wb") as fh:
        plistlib.dump(pl, fh)
    # Ad-hoc signature (required on Apple Silicon). Not notarized: first launch needs "Open Anyway".
    sh("codesign", "--force", "--deep", "--sign", "-", APP)
    sh("codesign", "--verify", "--deep", "--strict", APP)


WEB_COMMAND = f"""#!/bin/bash
# Opens the browser version of {APP_NAME} (runs only on this Mac).
APP="/Applications/{APP_NAME}.app"
CONF="$HOME/Library/Application Support/{APP_NAME}/app_path"
[ -f "$CONF" ] && [ -d "$(cat "$CONF")" ] && APP="$(cat "$CONF")"
if [ ! -x "$APP/Contents/MacOS/{APP_NAME}" ]; then
  osascript -e 'display alert "{APP_NAME} not found" message "Drag {APP_NAME} into Applications and open it once first."'
  exit 1
fi
nohup "$APP/Contents/MacOS/{APP_NAME}" --web >/dev/null 2>&1 &
osascript -e 'tell application "Terminal" to close (every window whose name contains "{APP_NAME} (Web)")' >/dev/null 2>&1 &
exit 0
"""

DMG_README = f"""{APP_NAME} {APP_VERSION} for macOS

INSTALL
1. Drag {APP_NAME} onto the Applications folder.
2. Open Applications and double-click {APP_NAME}.
   macOS says it can't verify the developer: click Done.
3. Apple menu > System Settings > Privacy & Security, scroll to Security:
   "{APP_NAME}" was blocked... click Open Anyway, then Open Anyway again and enter your password.
   (Only needed once.)

USE
- Drop files or folders onto the window or its Dock icon, or click Choose Files...
- In Finder: right-click files > Quick Actions > Remove Metadata / Check for AI Tags
  (if missing: System Settings > General > Login Items & Extensions > Finder, switch them on)
- Browser version: double-click "{APP_NAME} (Web).command" (right-click > Open the first time).
- Dark mode / Light mode: button at the top left; remembered on this Mac.
"""


def make_dmg():
    DIST.mkdir(parents=True, exist_ok=True)
    stage = WORK / "dmg"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir()
    sh("ditto", APP, stage / APP.name)              # ditto keeps symlinks and signatures intact
    os.symlink("/Applications", stage / "Applications")
    cmd = stage / f"{APP_NAME} (Web).command"
    cmd.write_text(WEB_COMMAND)
    cmd.chmod(0o755)
    (stage / "READ ME.txt").write_text(DMG_README)
    out = DIST / MAC_ASSETS[ARCH]
    out.unlink(missing_ok=True)
    sh("hdiutil", "create", "-volname", APP_NAME, "-srcfolder", stage, "-ov", "-format", "UDZO", out)
    print(f"\nBuilt {out} ({out.stat().st_size / 1e6:.0f} MB)")
    return out


# ---------------------------------------------------------------- smoke tests on the built app

def smoke_test():
    binary = APP / "Contents" / "MacOS" / APP_NAME
    ffmpeg = APP / "Contents" / "Resources" / "tools" / "ffmpeg"
    et = [ "/usr/bin/perl", APP / "Contents" / "Resources" / "tools" / "exiftool" / "exiftool"]
    t = WORK / "smoke"
    shutil.rmtree(t, ignore_errors=True)
    (t / "sub").mkdir(parents=True)

    def out(*args, timeout=300):
        p = subprocess.run([str(binary), *map(str, args)], capture_output=True, text=True, timeout=timeout)
        print(p.stdout + p.stderr, flush=True)
        return p.stdout

    v = out("--cli", "--versions")
    assert "not found" not in v and "ffmpeg" in v, f"tools missing: {v!r}"

    sh(ffmpeg, "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25", "-f", "lavfi",
       "-i", "sine", "-t", "2", "-c:v", "libx264", "-c:a", "aac", "-metadata", "title=Made with Sora",
       t / "clip.mp4")
    sh(ffmpeg, "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=64x64", "-frames:v", "1", t / "photo.jpg")
    sh(*et, "-q", "-overwrite_original", "-Make=Canon", "-Software=Midjourney v7", "-GPSLatitude=40",
       "-GPSLatitudeRef=N", t / "photo.jpg")
    sh(ffmpeg, "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=64x64", "-frames:v", "1",
       t / "sub" / "gen 日本.png")
    sh(*et, "-q", "-overwrite_original", "-PNG:parameters=a cat, Steps: 20", t / "sub" / "gen 日本.png")

    chk = out("--cli", "--check", t)
    assert chk.count("\tAI\n") == 3, f"expected 3 files flagged AI:\n{chk}"
    cl = out("--cli", t)
    assert cl.count("OK\t") == 3, f"expected 3 cleaned:\n{cl}"
    chk2 = out("--cli", "--check", t / "clip_camo.mp4", t / "photo_camo.jpg", t / "sub" / "gen 日本_camo.png")
    assert chk2.count("\tCLEAN\n") == 3, f"cleaned copies should be CLEAN:\n{chk2}"

    # The window opens and stays up.
    p = subprocess.Popen([str(binary)])
    time.sleep(10)
    alive = p.poll() is None
    p.send_signal(signal.SIGTERM)
    p.wait(timeout=20)
    assert alive, "the window closed by itself (crash at start)"

    # Browser version answers on localhost.
    p = subprocess.Popen([str(binary), "--web", "--no-browser"])
    try:
        ok = False
        for _ in range(30):
            time.sleep(1)
            try:
                with urllib.request.urlopen("http://127.0.0.1:8765/ping", timeout=2) as r:
                    ok = b"dl_remove_metadata" in r.read()
                    break
            except OSError:
                continue
        assert ok, "browser version didn't start"
    finally:
        p.terminate()
        p.wait(timeout=20)

    # Finder Quick Actions install and are valid plists.
    out("--install-menu")
    for name in ("Remove Metadata", "Check for AI Tags"):
        d = Path.home() / "Library" / "Services" / f"{name}.workflow" / "Contents"
        sh("plutil", "-lint", d / "document.wflow", d / "Info.plist")
    print("\nSMOKE TEST PASSED", flush=True)


if __name__ == "__main__":
    if sys.platform != "darwin":
        sys.exit("build_mac.py must run on macOS (GitHub Actions builds it: .github/workflows/mac.yml)")
    fetch_tools()
    make_icns()
    pyinstaller()
    fill_bundle()
    smoke_test()
    make_dmg()
