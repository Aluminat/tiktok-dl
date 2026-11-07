"""Packaging: the `tiktok-dl` command, the tiktok_dl package, the launcher and where tools are found.

The subprocess tests only run `--version` / `-h`, which exit before any network use.
"""
from __future__ import annotations

import re
import runpy
import shutil
import subprocess
import sys
import sysconfig
import types
from pathlib import Path

import pytest
from conftest import LAUNCHER, REPO, TEST_INSTALLED, VERSION_FILE

import tiktok_dl
from tiktok_dl.core import tools


def run(*args, cwd=REPO):
    return subprocess.run(list(args), cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=60)


def source_version() -> str:
    match = re.search(r'^__version__ = "([^"]+)"', VERSION_FILE.read_text(encoding="utf-8"), re.M)
    return match.group(1)


def test_version_matches_source():
    # In installed mode this checks that the installed copy is the one in this clone.
    assert tiktok_dl.__version__ == source_version()


def test_script_runs_from_clone():
    result = run(sys.executable, str(LAUNCHER), "--version")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"tiktok-dl {source_version()}"


def test_script_runs_without_its_folder_on_sys_path(tmp_path):
    # -I (like -P or PYTHONSAFEPATH=1) leaves the script's folder off sys.path; the launcher adds it.
    result = run(sys.executable, "-I", str(LAUNCHER), "--version", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"tiktok-dl {source_version()}"


def test_package_imports_from_clone():
    code = ("import tiktok_dl, tiktok_dl.cli; print(tiktok_dl.__version__); print(tiktok_dl.__file__); "
            "print(tiktok_dl.cli.main)")
    result = run(sys.executable, "-c", code)
    assert result.returncode == 0, result.stderr
    version, file, main = result.stdout.splitlines()
    assert version == source_version()
    assert Path(file).resolve() == VERSION_FILE
    assert main.startswith("<function main")


@pytest.mark.parametrize(
    "args",
    [
        # What the installed console script does: `from tiktok_dl.cli import main; main()`.
        ["-c", "import sys; sys.argv = ['tiktok-dl', '--version']; from tiktok_dl.cli import main; main()"],
        ["-m", "tiktok_dl", "--version"],
    ],
)
def test_console_script_main(args):
    result = run(sys.executable, *args)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"tiktok-dl {source_version()}"


def test_pyproject_matches_requirements():
    tomllib = pytest.importorskip("tomllib")   # Python 3.11+
    project = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    requirements = [line.strip() for line in (REPO / "requirements.txt").read_text(encoding="utf-8").splitlines()
                    if line.strip() and not line.startswith("#")]
    assert project["project"]["dependencies"] == requirements
    assert project["project"]["scripts"] == {"tiktok-dl": "tiktok_dl.cli:main"}
    assert project["project"]["dynamic"] == ["version"]
    assert project["tool"]["hatch"]["version"]["path"] == "tiktok_dl/__init__.py"
    assert project["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == ["tiktok_dl"]


# --------------------------------------------------------------------------- #
# Finding aria2c / ffmpeg
# --------------------------------------------------------------------------- #

def test_find_tool_prefers_file_next_to_script(tmp_path, monkeypatch):
    (tmp_path / "aria2c.exe").write_bytes(b"")
    monkeypatch.setattr(tools, "SCRIPT_DIR", tmp_path)
    monkeypatch.setattr(tools.shutil, "which", lambda name: "/usr/bin/" + name)
    assert tools.find_tool("aria2c") == str(tmp_path / "aria2c.exe")


def test_find_tool_finds_file_without_exe_next_to_script(tmp_path, monkeypatch):
    (tmp_path / "ffmpeg").write_bytes(b"")
    monkeypatch.setattr(tools, "SCRIPT_DIR", tmp_path)
    monkeypatch.setattr(tools.shutil, "which", lambda name: None)
    assert tools.find_tool("ffmpeg") == str(tmp_path / "ffmpeg")


def test_find_tool_falls_back_to_path(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "SCRIPT_DIR", tmp_path)   # nothing next to the script
    monkeypatch.setattr(tools.shutil, "which", lambda name: "/usr/bin/" + name)
    assert tools.find_tool("aria2c") == "/usr/bin/aria2c"


def test_find_ffmpeg_falls_back_to_imageio_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "SCRIPT_DIR", tmp_path)
    monkeypatch.setattr(tools.shutil, "which", lambda name: None)
    fake = types.SimpleNamespace(get_ffmpeg_exe=lambda: "/imageio/ffmpeg")
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", fake)
    assert tools.find_ffmpeg() == "/imageio/ffmpeg"


def test_find_ffmpeg_none_when_nothing_found(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "SCRIPT_DIR", tmp_path)
    monkeypatch.setattr(tools.shutil, "which", lambda name: None)
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", None)   # import fails
    assert tools.find_ffmpeg() is None


def test_script_dir():
    if TEST_INSTALLED:
        # An installed copy does not look in the clone; tools come from PATH or imageio-ffmpeg.
        assert tools.SCRIPT_DIR != REPO
    else:
        assert tools.SCRIPT_DIR == REPO


def test_launcher_sets_tools_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "SCRIPT_DIR", tools.SCRIPT_DIR)   # restored afterwards
    monkeypatch.setattr(sys, "path", list(sys.path))              # the launcher adds its folder
    copy = tmp_path / "tiktok-dl.py"
    shutil.copyfile(LAUNCHER, copy)
    # run_name is not "__main__", so main() is not called. tmp_path has no tiktok_dl folder, so the
    # launcher's imports find the package conftest already imported (clone or installed).
    runpy.run_path(str(copy))
    assert tools.SCRIPT_DIR == copy.resolve().parent   # resolved like the launcher does (Windows 8.3 / case)


# --------------------------------------------------------------------------- #
# The installed `tiktok-dl` command (CI sets TIKTOK_DL_TEST_INSTALLED=1)
# --------------------------------------------------------------------------- #

def installed_command() -> str:
    found = shutil.which("tiktok-dl", path=sysconfig.get_path("scripts")) or shutil.which("tiktok-dl")
    assert found, "the tiktok-dl command is not installed"
    return found


@pytest.mark.skipif(not TEST_INSTALLED, reason="needs the installed package (TIKTOK_DL_TEST_INSTALLED=1)")
def test_installed_command_version(tmp_path):
    result = run(installed_command(), "--version", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"tiktok-dl {tiktok_dl.__version__}"


@pytest.mark.skipif(not TEST_INSTALLED, reason="needs the installed package (TIKTOK_DL_TEST_INSTALLED=1)")
def test_installed_command_help(tmp_path):
    result = run(installed_command(), "-h", "--lang", "en", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("usage: tiktok-dl")
