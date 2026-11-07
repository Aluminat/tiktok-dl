# 1. Keep the app in tiktok-dl.py; install it through a thin wrapper

Status: superseded by [ADR 0002](0002-package-with-shared-core-and-site-modules.md) (issue #30; was accepted in issue #12)

## Context

Users want to install tiktok-dl with `pipx install git+https://github.com/Aluminat/tiktok-dl` and
run it as a `tiktok-dl` command. The project rule is "keep it one file": the whole app is
`tiktok-dl.py`, people download or clone it and run `python tiktok-dl.py ...`, and Windows users
drop `aria2c.exe` and `ffmpeg.exe` next to it.

A console-script entry point needs an importable module, and `tiktok-dl.py` cannot be imported
because of the hyphen. The two options were:

- (a) keep `tiktok-dl.py` as the single source of truth and add a thin importable module and a
  `pyproject.toml` entry point around it, or
- (b) move the code into a package and keep `tiktok-dl.py` as a small launcher.

## Decision

Option (a).

- `tiktok-dl.py` keeps all app code and stays runnable on its own.
- `tiktok_dl.py` (repo root) is a wrapper of a few lines: it loads `tiktok-dl.py` from the same
  folder and puts it in its own place in `sys.modules`. `import tiktok_dl` therefore gives the
  real app module, and `python -m tiktok_dl` works too. No app code goes in the wrapper.
- `pyproject.toml` (hatchling) ships both files side by side in the wheel and declares the
  `tiktok-dl = "tiktok_dl:main"` console script.
- The version has one source, `__version__` in `tiktok-dl.py`; hatchling reads it from there.
- The runtime dependencies are listed in `requirements.txt` (for clones) and in `pyproject.toml`;
  a test keeps the two lists equal.

## Consequences

- `python tiktok-dl.py ...` from a clone is unchanged, and contributors still edit one file.
- `find_tool()` keeps looking next to the script first, then in `PATH`. In a clone that is the
  clone folder, so `aria2c.exe` / `ffmpeg.exe` placed there are still found. In an installed copy
  the script sits in `site-packages`, where nobody puts tools, so in practice `PATH` is used, and
  ffmpeg still falls back to `imageio-ffmpeg`.
- An extra top-level `tiktok-dl.py` file is installed in `site-packages` next to the wrapper. It is
  not importable by name, so it cannot clash with other packages.
- CI builds the sdist and wheel, checks the pipx-installed `tiktok-dl --version`, and runs the test
  suite against the installed wheel (`TIKTOK_DL_TEST_INSTALLED=1`, see `tests/conftest.py`).
- If the app ever outgrows one file, option (b) is still open; the wrapper and entry point would
  then point at the new package instead.
