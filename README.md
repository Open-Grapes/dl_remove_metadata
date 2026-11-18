# dl_remove_metadata

<img src="assets/icon.png" width="96" align="right" alt="dl_remove_metadata icon">

Removes metadata from videos, audio and images, including AI / C2PA "Content Credentials" tags, and checks files for AI tags.

- Makes a clean **copy** next to each original: `clip.mp4` → `clip_camo.mp4`. Your originals are never changed.
- Video and audio are copied without re-encoding: no quality loss, and it's fast.
- Windows 10 and 11 (64-bit), and macOS 11 or newer (Apple Silicon and Intel). Everything it needs is included — no Python, ffmpeg or ExifTool install required.

## Download & install (Windows)

1. Download **dl_remove_metadata-windows.zip** from the [latest release](../../releases/latest).
2. Right-click the zip → **Extract All…** and put the `dl_remove_metadata` folder somewhere permanent, e.g. `C:\Users\<you>\Apps\dl_remove_metadata`.
3. Double-click **dl_remove_metadata.exe**.
   Windows SmartScreen may say *"Windows protected your PC"* because the app isn't code-signed. Click **More info** → **Run anyway**. You only do this once.
4. The app adds **Remove Metadata** and **Check for AI Tags** to Explorer's right-click menu (for your user only; no admin needed).

If you move the folder later, just open the app once more and the right-click menu follows it.

## Download & install (Mac)

1. From the [latest release](../../releases/latest), download the file for your Mac (Apple menu → **About This Mac**):
   - **dl_remove_metadata-mac-apple-silicon.dmg** — chip "Apple M1/M2/M3/M4…"
   - **dl_remove_metadata-mac-intel.dmg** — processor "Intel"
2. Double-click the `.dmg` and drag **dl_remove_metadata** onto **Applications**.
3. Open it from Applications. macOS says it can't verify the developer: click **Done**.
4. **Apple menu → System Settings → Privacy & Security**, scroll to **Security**, click **Open Anyway**, then **Open Anyway** again and enter your password. You only do this once.
5. The app adds **Remove Metadata** and **Check for AI Tags** to Finder (right-click → **Quick Actions**). If they don't show, switch them on in **System Settings → General → Login Items & Extensions → Finder**.

The `.dmg` also has **dl_remove_metadata (Web).command** for the browser version (right-click → **Open** the first time).

## Use

- In Explorer, right-click files or folders → **Remove Metadata**
  (Windows 11: it's under **Show more options**, or hold **Shift** while right-clicking)
- or drop files and folders onto the window, or onto `dl_remove_metadata.exe` itself
- or open the app and click **Choose Files…** / **Choose Folder…**

**Save to…** puts clean copies in another folder. **Reset** goes back to saving next to the originals.

**Originals:** choose **Keep originals** (default) or **Remove originals**. With Remove, each original is moved to the Recycle Bin (Trash on Mac) once its clean `_camo` copy has been made, so you're left with only the clean copy; if cleaning a file fails, its original is kept. The right-click action follows the same choice, and it's remembered on each computer.

- Drives without a Recycle Bin / Trash (network drives, USB sticks) would delete files permanently, so originals there are always kept.
- **Restore originals** (next to the option, available while Remove is selected) brings back every original this app moved to the Recycle Bin / Trash into its original folder. The `_camo` copies stay. Originals already emptied from the bin can't be brought back, and a file is never put back over one with the same name.

### Check for AI tags

Switch to **Check for AI Tags** at the top of the window and drop files (or right-click → **Check for AI Tags**). Checking only reads files; nothing is changed.

| Status | Meaning |
|---|---|
| 🔺 red | AI tags found: Content Credentials, "made by AI" labels, AI tool names, AI prompts |
| ⓘ orange | No AI tags, but other details such as camera, dates, location or software |
| ✅ green | No AI tags or personal details found |

Double-click a row (or **Details…**) to see exactly which tags were found. Files with AI tags are ticked (☑) automatically; click the ✓ column to change that. **Remove Metadata from Selected** makes clean copies.

### Browser version (no app window)

Double-click **dl_remove_metadata (Web).cmd** in the program folder. A page opens in your web browser with the same two modes: drop files (or folders) on it, then click **Download** next to each clean copy, or **Download all (.zip)**.

- It runs only on your own computer (`http://127.0.0.1`); files are never sent anywhere else. The page needs the program folder next to it, because it uses the bundled ffmpeg and ExifTool.
- Clean copies go to your browser's Downloads folder, named `name_camo.ext`.
- Close the browser tab when you're done; the background server stops by itself about a minute later.

### Share via Wi-Fi (phones and other computers)

Click **Share via Wi-Fi** at the top of the window. It shows a QR code and an address like `http://192.168.0.20` (and `http://dl-remove-metadata.local` where the network allows it). Phones, tablets and laptops on the same Wi-Fi scan the code or open the address, drop photos, videos or audio on the page, choose **Remove Metadata** or **Check for AI Tags**, and download the clean copies. This computer does the work; keep it on with the app open.

- There's no password: anyone on your Wi-Fi who knows the address can use it while sharing is on. **Turn off** in the same window ends it; open pages show "Wi-Fi sharing has ended".
- Each device only sees and downloads its own files.
- Uploads are kept on this computer, one folder each, until you delete them. A reminder bar shows while any are stored.
- **Admin…** shows the stored uploads (**Open folder**, **Delete all**) and an activity log (date, time, IP address, device name, MAC address, device, action) that opens in Excel. Select a row and **Block device** to end that device's access ("Access ended" on its page).
- First time: Windows asks whether to allow dl_remove_metadata on networks — allow **Private networks** (and set your Wi-Fi to Private in Settings → Network & internet). On a Mac, click **Allow** when asked about incoming connections.

## Updates

**Check for Updates** opens a window showing this version and the latest on GitHub with its release notes, plus ffmpeg, ExifTool and the AI tag list with their status. **Update all** installs everything new in one click, **Check again** re-checks, **Release page** opens the release on GitHub. Once a week the app also updates ffmpeg, ExifTool and its list of AI tags by itself (into `%LOCALAPPDATA%\dl_remove_metadata`; the program folder stays as it is). When a new version of dl_remove_metadata is released here, it shows **Update Now**: one click downloads it and restarts the app.

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

This downloads ffmpeg and ExifTool into `tools\`, builds `dist\Windows\dl_remove_metadata\dl_remove_metadata.exe`, and zips it as `dist\Windows\dl_remove_metadata-windows.zip` (the file to attach to a GitHub release, tagged `v<version>` matching `APP_VERSION` in `dlrm/common.py`).

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
