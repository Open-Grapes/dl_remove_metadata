"""Launcher used by PyInstaller and for running from source: python run.py"""
import sys

from dlrm.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
