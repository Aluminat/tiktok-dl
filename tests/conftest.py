"""Shared fixtures.

tiktok-dl.py has a hyphen in its name, so it cannot be imported with a normal
`import` statement. The `tdl` fixture loads it from its file path instead.
"""
from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path

import pytest
import requests

SCRIPT = Path(__file__).resolve().parent.parent / "tiktok-dl.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("tiktok_dl", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["tiktok_dl"] = module
    # At import time the script reconfigures sys.stdout/sys.stderr to UTF-8 and
    # checks isatty(). Give it plain StringIO streams (no reconfigure()) so it
    # does not touch pytest's capture streams or the real console.
    real_out, real_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
    try:
        spec.loader.exec_module(module)
    finally:
        sys.stdout, sys.stderr = real_out, real_err
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
