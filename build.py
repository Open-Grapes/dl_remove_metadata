"""Build dl_remove_metadata for Windows.

    python -m venv .venv
    .venv\\Scripts\\pip install -r requirements.txt
    .venv\\Scripts\\python build.py            # -> dist\\dl_remove_metadata\\ and dist\\dl_remove_metadata-windows.zip

Downloads ffmpeg (gyan.dev essentials build) and ExifTool (Windows package) into tools\\ the first time.
"""

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dlrm import updater  # noqa: E402
from dlrm.common import APP_NAME, RELEASE_ASSET  # noqa: E402

TOOLS = ROOT / "tools"
DIST = ROOT / "dist" / "Windows"   # the Mac build goes in dist/MacOS
ICON = ROOT / "assets" / "icon.ico"


def fetch_tools():
    TOOLS.mkdir(exist_ok=True)
    if not (TOOLS / "ffmpeg.exe").exists():
        v = updater.latest_ffmpeg()
        print(f"Downloading ffmpeg {v} …")
        if not updater.install_ffmpeg(TOOLS, v):
            sys.exit("ffmpeg download failed")
    if not (TOOLS / "exiftool" / "exiftool.exe").exists():
        v = updater.latest_exiftool()
        print(f"Downloading exiftool {v} …")
        if not updater.install_exiftool(TOOLS / "exiftool", v):
            sys.exit("exiftool download failed")


def make_icon():
    if ICON.exists():
        return
    from PIL import Image, ImageDraw
    S = 256
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((8, 8, S - 8, S - 8), radius=56, fill=(32, 92, 214))
    # a photo/film frame
    d.rounded_rectangle((52, 62, 204, 170), radius=14, outline=(255, 255, 255), width=12)
    d.polygon([(70, 155), (110, 108), (140, 140), (160, 120), (190, 155)], fill=(255, 255, 255))
    d.ellipse((150, 78, 176, 104), fill=(255, 255, 255))
    # eraser stroke = metadata removed
    d.line((52, 206, 204, 206), fill=(255, 210, 63), width=18)
    d.line((40, 220, 216, 44), fill=(255, 210, 63), width=14)
    ICON.parent.mkdir(exist_ok=True)
    img.save(ICON, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


def pyinstaller():
    subprocess.check_call([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
                           "--name", APP_NAME, "--icon", str(ICON),
                           "--add-data", f"{ICON};assets",
                           "--add-data", f"{ROOT / 'dlrm' / 'web_page.html'};dlrm",
                           "--collect-all", "tkinterdnd2",
                           "--distpath", str(DIST), "--workpath", str(ROOT / "build"),
                           "--specpath", str(ROOT / "build"), str(ROOT / "run.py")])


def assemble():
    out = DIST / APP_NAME
    shutil.copytree(TOOLS, out / "tools", dirs_exist_ok=True)
    (out / "assets").mkdir(exist_ok=True)
    shutil.copy2(ICON, out / "assets" / "icon.ico")
    shutil.copy2(ICON.with_suffix(".png"), out / "assets" / "icon.png")
    # Double-click launcher for the browser version (starts the local server, opens the page).
    (out / f"{APP_NAME} (Web).cmd").write_text(
        f'@echo off\r\nstart "" "%~dp0{APP_NAME}.exe" --web\r\n', encoding="ascii")
    for name in ("detect_rules.txt", "LICENSE", "THIRD_PARTY_NOTICES.md", "README.md"):
        shutil.copy2(ROOT / name, out / name)
    shutil.copytree(ROOT / "licenses", out / "licenses", dirs_exist_ok=True)
    z = DIST / RELEASE_ASSET
    z.unlink(missing_ok=True)
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(out.rglob("*")):
            if f.is_file():
                zf.write(f, Path(APP_NAME) / f.relative_to(out))
    print(f"\nBuilt {out}\nZipped {z} ({z.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    fetch_tools()
    make_icon()
    pyinstaller()
    assemble()
    # build\ only holds PyInstaller's intermediate files; its .exe can't run on its own, so remove it.
    shutil.rmtree(ROOT / "build", ignore_errors=True)
    print(f"Run: {DIST / APP_NAME / (APP_NAME + '.exe')}")
