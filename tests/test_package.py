"""Packaging: the `tiktok-dl` command, the tiktok_dl wrapper and where tools are found.

The subprocess tests only run `--version` / `-h`, which exit before any network use.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import sysconfig
import types
from pathlib import Path

import pytest
from conftest import REPO, SCRIPT, TEST_INSTALLED


def run(*args, cwd=REPO):
    return subprocess.run(list(args), cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=60)


def script_version() -> str:
    match = re.search(r'^__version__ = "([^"]+)"', SCRIPT.read_text(encoding="utf-8"), re.M)
    return match.group(1)


def test_version_matches_script(tdl):
    # In installed mode this checks that the installed copy is the one in this clone.
    assert tdl.__version__ == script_version()


def test_script_runs_from_clone():
    result = run(sys.executable, str(SCRIPT), "--version")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"tiktok-dl {script_version()}"


def test_wrapper_is_the_script():
    code = "import tiktok_dl; print(tiktok_dl.__version__); print(tiktok_dl.__file__); print(tiktok_dl.main)"
    result = run(sys.executable, "-c", code)
    assert result.returncode == 0, result.stderr
    version, file, main = result.stdout.splitlines()
    assert version == script_version()
    assert Path(file).resolve() == SCRIPT
    assert main.startswith("<function main")


@pytest.mark.parametrize(
    "args",
    [
        # What the installed console script does: `from tiktok_dl import main; main()`.
        ["-c", "import sys; sys.argv = ['tiktok-dl', '--version']; from tiktok_dl import main; main()"],
        ["-m", "tiktok_dl", "--version"],
    ],
)
def test_wrapper_main(args):
    result = run(sys.executable, *args)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"tiktok-dl {script_version()}"


def test_pyproject_matches_requirements():
    tomllib = pytest.importorskip("tomllib")   # Python 3.11+
    project = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    requirements = [line.strip() for line in (REPO / "requirements.txt").read_text(encoding="utf-8").splitlines()
                    if line.strip() and not line.startswith("#")]
    assert project["project"]["dependencies"] == requirements
    assert project["project"]["scripts"] == {"tiktok-dl": "tiktok_dl:main"}
    assert project["project"]["dynamic"] == ["version"]
    assert project["tool"]["hatch"]["version"]["path"] == "tiktok-dl.py"
    assert set(project["tool"]["hatch"]["build"]["targets"]["wheel"]["only-include"]) == {"tiktok-dl.py",
                                                                                         "tiktok_dl.py"}


# --------------------------------------------------------------------------- #
# Finding aria2c / ffmpeg
# --------------------------------------------------------------------------- #

def test_find_tool_prefers_file_next_to_script(tdl, tmp_path, monkeypatch):
    (tmp_path / "aria2c.exe").write_bytes(b"")
    monkeypatch.setattr(tdl, "SCRIPT_DIR", tmp_path)
    monkeypatch.setattr(tdl.shutil, "which", lambda name: "/usr/bin/" + name)
    assert tdl.find_tool("aria2c") == str(tmp_path / "aria2c.exe")


def test_find_tool_finds_file_without_exe_next_to_script(tdl, tmp_path, monkeypatch):
    (tmp_path / "ffmpeg").write_bytes(b"")
    monkeypatch.setattr(tdl, "SCRIPT_DIR", tmp_path)
    monkeypatch.setattr(tdl.shutil, "which", lambda name: None)
    assert tdl.find_tool("ffmpeg") == str(tmp_path / "ffmpeg")


def test_find_tool_falls_back_to_path(tdl, tmp_path, monkeypatch):
    monkeypatch.setattr(tdl, "SCRIPT_DIR", tmp_path)   # nothing next to the script
    monkeypatch.setattr(tdl.shutil, "which", lambda name: "/usr/bin/" + name)
    assert tdl.find_tool("aria2c") == "/usr/bin/aria2c"


def test_find_ffmpeg_falls_back_to_imageio_ffmpeg(tdl, tmp_path, monkeypatch):
    monkeypatch.setattr(tdl, "SCRIPT_DIR", tmp_path)
    monkeypatch.setattr(tdl.shutil, "which", lambda name: None)
    fake = types.SimpleNamespace(get_ffmpeg_exe=lambda: "/imageio/ffmpeg")
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", fake)
    assert tdl.find_ffmpeg() == "/imageio/ffmpeg"


def test_find_ffmpeg_none_when_nothing_found(tdl, tmp_path, monkeypatch):
    monkeypatch.setattr(tdl, "SCRIPT_DIR", tmp_path)
    monkeypatch.setattr(tdl.shutil, "which", lambda name: None)
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", None)   # import fails
    assert tdl.find_ffmpeg() is None


def test_script_dir(tdl):
    if TEST_INSTALLED:
        # An installed copy does not look in the clone; tools come from PATH or imageio-ffmpeg.
        assert tdl.SCRIPT_DIR != REPO
    else:
        assert tdl.SCRIPT_DIR == REPO


# --------------------------------------------------------------------------- #
# The installed `tiktok-dl` command (CI sets TIKTOK_DL_TEST_INSTALLED=1)
# --------------------------------------------------------------------------- #

def installed_command() -> str:
    found = shutil.which("tiktok-dl", path=sysconfig.get_path("scripts")) or shutil.which("tiktok-dl")
    assert found, "the tiktok-dl command is not installed"
    return found


@pytest.mark.skipif(not TEST_INSTALLED, reason="needs the installed package (TIKTOK_DL_TEST_INSTALLED=1)")
def test_installed_command_version(tdl, tmp_path):
    result = run(installed_command(), "--version", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"tiktok-dl {tdl.__version__}"


@pytest.mark.skipif(not TEST_INSTALLED, reason="needs the installed package (TIKTOK_DL_TEST_INSTALLED=1)")
def test_installed_command_help(tmp_path):
    result = run(installed_command(), "-h", "--lang", "en", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("usage: tiktok-dl")
