# dl_remove_metadata

<img src="assets/icon.png" width="96" align="right" alt="dl_remove_metadata icon">

Removes metadata from videos, audio and images, including AI / C2PA "Content Credentials" tags, and checks files for AI tags.

- Makes a clean **copy** next to each original: `clip.mp4` → `clip_camo.mp4`. Your originals are never changed.
- Video and audio are copied without re-encoding: no quality loss, and it's fast.
- Windows 10 and 11 (64-bit). Everything it needs is included — no Python, ffmpeg or ExifTool install required.

## Download & install

1. Download **dl_remove_metadata-windows.zip** from the [latest release](../../releases/latest).
2. Right-click the zip → **Extract All…** and put the `dl_remove_metadata` folder somewhere permanent, e.g. `C:\Users\<you>\Apps\dl_remove_metadata`.
3. Double-click **dl_remove_metadata.exe**.
   Windows SmartScreen may say *"Windows protected your PC"* because the app isn't code-signed. Click **More info** → **Run anyway**. You only do this once.
4. The app adds **Remove Metadata** and **Check for AI Tags** to Explorer's right-click menu (for your user only; no admin needed).

If you move the folder later, just open the app once more and the right-click menu follows it.

## Use

- In Explorer, right-click files or folders → **Remove Metadata**
  (Windows 11: it's under **Show more options**, or hold **Shift** while right-clicking)
- or drop files and folders onto the window, or onto `dl_remove_metadata.exe` itself
- or open the app and click **Choose Files…** / **Choose Folder…**

**Save to…** puts clean copies in another folder. **Reset** goes back to saving next to the originals.

### Check for AI tags

Switch to **Check for AI Tags** at the top of the window and drop files (or right-click → **Check for AI Tags**). Checking only reads files; nothing is changed.

| Status | Meaning |
|---|---|
| 🔺 red | AI tags found: Content Credentials, "made by AI" labels, AI tool names, AI prompts |
| ⓘ orange | No AI tags, but other details such as camera, dates, location or software |
| ✅ green | No AI tags or personal details found |

Double-click a row (or **Details…**) to see exactly which tags were found. Files with AI tags are ticked (☑) automatically; click the ✓ column to change that. **Remove Metadata from Selected** makes clean copies.

## Updates

Once a week, or when you click **Check for Updates**, the app updates ffmpeg, ExifTool and its list of AI tags (into `%LOCALAPPDATA%\dl_remove_metadata`; the program folder stays as it is). When a new version of dl_remove_metadata is released here, it shows **Update Now**: one click downloads it and restarts the app.

To remove the right-click menu, untick **Right-click menu** at the bottom of the window. To uninstall, untick it, then delete the program folder and `%LOCALAPPDATA%\dl_remove_metadata`.

## Limits

Removes file metadata only. It can't detect or remove invisible watermarks embedded in the pixels themselves (e.g. SynthID). Services that recognise images by how they look can still match a cleaned copy.

## Build from source

Requires Python 3.10+ (64-bit) on Windows.

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python build.py
```

This downloads ffmpeg and ExifTool into `tools\`, builds `dist\dl_remove_metadata\dl_remove_metadata.exe`, and zips it as `dist\dl_remove_metadata-windows.zip` (the file to attach to a GitHub release, tagged `v<version>` matching `APP_VERSION` in `dlrm/common.py`).

Run from source without building: `.venv\Scripts\python run.py` (after `build.py` has fetched `tools\` once).
Command line: `.venv\Scripts\python -m dlrm --cli --check <files>` or `--cli <files>` to clean.

| File | Purpose |
|---|---|
| `dlrm/engine.py` | removing metadata (ffmpeg / ExifTool) and checking for AI tags |
| `dlrm/gui.py` | the window and the right-click progress window |
| `dlrm/shell.py` | Explorer right-click menu, batching multi-file selections |
| `dlrm/updater.py` | weekly tool / AI-tag-list updates and app self-update |
| `detect_rules.txt` | which metadata counts as an AI tag |

## Credits

dl_remove_metadata is released under the MIT License ([LICENSE](LICENSE)).

Includes these separate open-source tools, each under its own licence (details: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)):

- **FFmpeg**: [ffmpeg.org](https://ffmpeg.org), Windows builds by Gyan Doshi ([gyan.dev](https://www.gyan.dev/ffmpeg/builds/)). GPL v3.
- **ExifTool** by Phil Harvey: [exiftool.org](https://exiftool.org). Perl Artistic License / GPL.
