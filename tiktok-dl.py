#!/usr/bin/env python3
"""tiktok-dl launcher: `python tiktok-dl.py ...` runs the tiktok_dl package next to this file.

The app lives in tiktok_dl/ (see its docstring and docs/adr/0002). aria2c / ffmpeg placed next to
this file are found first, then PATH.
"""
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
# Python normally puts this folder first on sys.path, but not with -I, -P or PYTHONSAFEPATH=1.
# Put it there so the package next to this file runs, not an installed copy (or none at all).
sys.path.insert(0, str(_HERE))

from tiktok_dl.cli import main  # noqa: E402
from tiktok_dl.core import tools  # noqa: E402

tools.SCRIPT_DIR = _HERE   # look for aria2c / ffmpeg next to this file first

if __name__ == "__main__":
    main()
