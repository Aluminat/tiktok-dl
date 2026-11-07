"""Importable name for tiktok-dl.py, used by the installed `tiktok-dl` command.

tiktok-dl.py has a hyphen in its name, so Python cannot `import` it. This wrapper loads the
script that sits next to it and puts it in its own place in sys.modules, so `import tiktok_dl`
gives the real app module and the console script in pyproject.toml can call `tiktok_dl:main`.

All app code stays in tiktok-dl.py; do not add code here (see docs/adr/0001).
"""
import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().with_name("tiktok-dl.py")
_spec = importlib.util.spec_from_file_location(__name__, _SCRIPT)
_module = importlib.util.module_from_spec(_spec)
sys.modules[__name__] = _module
try:
    _spec.loader.exec_module(_module)
except BaseException:
    sys.modules.pop(__name__, None)
    raise
