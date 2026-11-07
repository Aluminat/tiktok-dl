#!/usr/bin/env python3
"""tiktok-dl launcher: `python tiktok-dl.py ...` runs the tiktok_dl package next to this file.

The app lives in tiktok_dl/ (see its docstring). aria2c / ffmpeg placed next to this file are
found first, then PATH.
"""
from pathlib import Path

from tiktok_dl.cli import main
from tiktok_dl.core import tools

tools.SCRIPT_DIR = Path(__file__).resolve().parent   # look for aria2c / ffmpeg next to this file first

if __name__ == "__main__":
    main()
