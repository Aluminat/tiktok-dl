"""Shared fixtures.

tiktok-dl.py has a hyphen in its name, so it cannot be imported with a normal
`import` statement. The `tdl` fixture loads it from its file path instead.

With TIKTOK_DL_TEST_INSTALLED=1 (used by CI), `tdl` is the installed package
instead (`import tiktok_dl`), so the same tests run against the built wheel.
"""
from __future__ import annotations

import importlib
import importlib.util
import io
import os
import sys
from pathlib import Path

import pytest
import requests

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "tiktok-dl.py"
TEST_INSTALLED = os.environ.get("TIKTOK_DL_TEST_INSTALLED") == "1"


def _load_script():
    # At import time the script reconfigures sys.stdout/sys.stderr to UTF-8 and
    # checks isatty(). Give it plain StringIO streams (no reconfigure()) so it
    # does not touch pytest's capture streams or the real console.
    real_out, real_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
    try:
        if TEST_INSTALLED:
            return _import_installed()
        spec = importlib.util.spec_from_file_location("tiktok_dl", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        sys.modules["tiktok_dl"] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.stdout, sys.stderr = real_out, real_err


def _import_installed():
    module = importlib.import_module("tiktok_dl")
    if Path(module.__file__).resolve().parent == REPO:
        raise RuntimeError("TIKTOK_DL_TEST_INSTALLED=1 but tiktok_dl was imported from the clone, "
                           "not the installed package. Run the `pytest` command, not `python -m pytest`.")
    return module


@pytest.fixture(scope="session")
def tdl():
    """The tiktok-dl.py module."""
    return _load_script()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Fail any test that tries to make a real HTTP request."""
    def blocked(*args, **kwargs):
        raise AssertionError("tests must not use the network")
    monkeypatch.setattr(requests.sessions.Session, "request", blocked)
