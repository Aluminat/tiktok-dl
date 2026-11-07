"""Shared fixtures.

The tests import the tiktok_dl package: the one in this clone (put first on sys.path), or with
TIKTOK_DL_TEST_INSTALLED=1 (used by CI) the installed package, so the same tests run against the
built wheel. The package is imported here, when conftest loads, before any test module.
"""
from __future__ import annotations

import importlib
import io
import os
import sys
from pathlib import Path

import pytest
import requests

REPO = Path(__file__).resolve().parent.parent
LAUNCHER = REPO / "tiktok-dl.py"
VERSION_FILE = REPO / "tiktok_dl" / "__init__.py"
TEST_INSTALLED = os.environ.get("TIKTOK_DL_TEST_INSTALLED") == "1"


def _import_package() -> None:
    if not TEST_INSTALLED:
        sys.path.insert(0, str(REPO))
    # Importing the package reconfigures sys.stdout/sys.stderr to UTF-8 and checks isatty()
    # (tiktok_dl/core/console.py). Give it plain StringIO streams (no reconfigure()) so it does not
    # touch pytest's capture streams or the real console.
    real_out, real_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
    try:
        importlib.import_module("tiktok_dl.cli")   # imports every module of the package
    finally:
        sys.stdout, sys.stderr = real_out, real_err
    package_dir = Path(sys.modules["tiktok_dl"].__file__).resolve().parent
    if TEST_INSTALLED and package_dir.parent == REPO:
        raise RuntimeError("TIKTOK_DL_TEST_INSTALLED=1 but tiktok_dl was imported from the clone, "
                           "not the installed package. Run the `pytest` command, not `python -m pytest`.")
    if not TEST_INSTALLED and package_dir.parent != REPO:
        raise RuntimeError(f"tiktok_dl was imported from {package_dir}, not from this clone.")


_import_package()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Fail any test that tries to make a real HTTP request."""
    def blocked(*args, **kwargs):
        raise AssertionError("tests must not use the network")
    monkeypatch.setattr(requests.sessions.Session, "request", blocked)
