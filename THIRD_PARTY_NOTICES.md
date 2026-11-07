# Third-Party Notices

dl_remove_metadata's own code is under the MIT License (see [LICENSE](LICENSE)).

The release zip includes the open-source tools below. They are **separate programs** that dl_remove_metadata runs as-is: not modified and not compiled into its own code. Each stays under its own licence. The built-in updater may later download newer versions from the same sources, under the same licences.

---

## FFmpeg

Used to remove metadata from video and audio files without re-encoding.

| | |
|---|---|
| **Bundled as** | `tools\ffmpeg.exe` |
| **Build** | "essentials" Windows build by Gyan Doshi, https://www.gyan.dev/ffmpeg/builds/ (version in `tools\ffmpeg.version`) |
| **Copyright** | © 2000–2026 the FFmpeg developers |
| **Licence** | GNU General Public License, version 3. The build is configured with `--enable-gpl --enable-version3` and includes GPL libraries, so the binary as a whole is GPL v3. Licence text: `licenses\GPL-3.0.txt`, `tools\ffmpeg-LICENSE.txt`, https://www.gnu.org/licenses/gpl-3.0.html |
| **Project** | https://ffmpeg.org |

**Source code.** The FFmpeg source for the bundled version is available at https://ffmpeg.org/releases/ (e.g. `ffmpeg-<version>.tar.xz`) and the build's configuration and library list are shown by `tools\ffmpeg.exe -version` and documented at https://www.gyan.dev/ffmpeg/builds/. Each GitHub release of dl_remove_metadata also attaches the matching FFmpeg source tarball.

FFmpeg is a trademark of Fabrice Bellard. dl_remove_metadata is not affiliated with or endorsed by the FFmpeg project.

---

## ExifTool

Used to read and remove metadata (EXIF, XMP, IPTC, C2PA and more) from images, and to check files for AI tags.

| | |
|---|---|
| **Bundled as** | `tools\exiftool\exiftool.exe` + `exiftool_files\` (the official Windows package, which includes Perl) |
| **Copyright** | © 2003–2026 Phil Harvey |
| **Licence** | Same terms as Perl itself: the Artistic License or the GNU General Public License, version 1 or later. https://dev.perl.org/licenses/ |
| **Project & source** | https://exiftool.org |

dl_remove_metadata is not affiliated with or endorsed by ExifTool or Phil Harvey.

---

## Python runtime and libraries (inside the .exe)

- **Python** — Python Software Foundation License, https://www.python.org
- **Tcl/Tk** — BSD-style licence, https://www.tcl.tk
- **tkinterdnd2 / tkdnd** — MIT License, https://github.com/Eliav2/tkinterdnd2

---

## Getting the source code

If you can't get the source code for any bundled component from the links above, open an issue on this repository and it will be provided free of charge, for at least three years after the last release that included that component.
