"""Finding aria2c and ffmpeg."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

# Folder searched first for aria2c / ffmpeg. tiktok-dl.py sets it to its own folder. Otherwise
# (installed `tiktok-dl` command, `python -m tiktok_dl`, tests) it is the folder that holds the
# package: the clone root, or site-packages for an installed copy, where nobody puts tools, so
# PATH (and imageio-ffmpeg) is used, as before.
SCRIPT_DIR = Path(__file__).resolve().parents[2]


def find_tool(name: str) -> Optional[str]:
    """Prefer an executable next to tiktok-dl.py (e.g. aria2c.exe, ffmpeg.exe), then PATH."""
    for cand in (SCRIPT_DIR / f"{name}.exe", SCRIPT_DIR / name):
        if cand.is_file():
            return str(cand)
    return shutil.which(name)


def find_ffmpeg() -> Optional[str]:
    path = find_tool("ffmpeg")
    if path:
        return path
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None
