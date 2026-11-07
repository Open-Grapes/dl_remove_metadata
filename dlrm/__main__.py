"""Entry point.

  dl_remove_metadata.exe                        open the window
  dl_remove_metadata.exe FILE|FOLDER ...        open the window and clean these (drag onto the .exe)
  dl_remove_metadata.exe --queue remove|check PATH   used by the Explorer right-click menu
  dl_remove_metadata.exe --auto-update          weekly background update check (silent)
  dl_remove_metadata.exe --install-menu | --remove-menu

Command-line mode (prints results; run with python -m dlrm, or the console build):
  --cli [--outdir DIR] FILE|FOLDER ...   clean items, one status line each (OK / SKIP / FAIL)
  --cli --check FILE|FOLDER ...          report metadata / AI tags only (never changes files)
  --cli --versions                       print tool versions
  --cli --update                         check for updates now
"""

import sys


def _print(*a):
    try:
        print(*a, flush=True)
    except (AttributeError, OSError):  # no console (windowed build)
        pass


def cli(args):
    from . import engine, updater
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    if args[:1] == ["--versions"]:
        _print(engine.tool_versions())
        return 0
    if args[:1] == ["--update"]:
        _print(updater.run_update())
        return 0
    if args[:1] == ["--check"]:
        for f in engine.expand(args[1:], include_cleaned=True):
            r = engine.check_one(f)
            for t in r.tags:
                _print("TAG\t" + "\t".join(t))
            _print(f"RESULT\t{f}\t{r.status}" if r.status not in ("SKIP", "FAIL") else f"{r.status}\t{f}\t{r.reason}")
        return 0
    outdir = None
    if args[:1] == ["--outdir"]:
        outdir, args = args[1], args[2:]
    code = 0
    for f in engine.expand(args):
        r = engine.process_one(f, outdir)
        _print(f"OK\t{r.output}" if r.status == "OK" else f"{r.status}\t{f}\t{r.reason}")
        code = code or (r.status == "FAIL")
    return code


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["--cli"]:
        return cli(args[1:])
    if args[:1] == ["--auto-update"]:
        from . import updater
        updater.run_update(auto=True)
        return 0
    if args[:1] in (["--install-menu"], ["--remove-menu"]):
        from . import shell
        shell.install_menu() if args[0] == "--install-menu" else shell.remove_menu()
        return 0
    if args[:1] == ["--queue"] and len(args) >= 3:
        from . import shell
        shell.enqueue(args[1], args[2])
        batch = shell.collect_batch()
        from . import gui
        if batch.get("remove"):
            gui.QuickWindow(batch["remove"]).mainloop()
        if batch.get("check"):
            gui.App(batch["check"], mode="check").mainloop()
        return 0
    from . import gui
    gui.App([a for a in args if not a.startswith("--")]).mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
