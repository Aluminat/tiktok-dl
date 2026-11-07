# Contributing to tiktok-dl

Thanks for helping! Bug reports, fixes and small features are all welcome.

> This project is for educational purposes only. Contributions that bypass TikTok's protections
> (forging request signatures, solving captchas, spoofing devices) will not be accepted.

## Report a bug

Open an [issue](https://github.com/Aluminat/tiktok-dl/issues) with:

- the command you ran (you can hide the username)
- what you expected and what happened
- the error text, or the lines from `failed.txt`
- your OS and Python version (`python --version`, or `python3 --version` on Linux/macOS)

## Set up

Fork [Aluminat/tiktok-dl](https://github.com/Aluminat/tiktok-dl) on GitHub, then clone your fork:

```bash
git clone https://github.com/<your-name>/tiktok-dl.git
cd tiktok-dl
git remote add upstream https://github.com/Aluminat/tiktok-dl.git
pip install -r requirements.txt
```

On Linux/macOS use `python3` / `python3 -m pip` instead of `python` / `pip`.

Install aria2c and ffmpeg as described in [README.md](README.md#install): on Windows put
`aria2c.exe` and `ffmpeg.exe` next to `tiktok-dl.py`; on Linux/macOS run
`sudo apt install aria2 ffmpeg` or `brew install aria2 ffmpeg`. The script looks next to
`tiktok-dl.py` first, then in `PATH`. Both are optional: ffmpeg falls back to `imageio-ffmpeg`,
and without aria2c files are downloaded with `requests` (slower).

## Where things are

The app is the **`tiktok_dl/`** package; `tiktok-dl.py` only launches it. The docstring in
`tiktok_dl/__init__.py` explains how a download works and has a code map. In short:

| File | What it does |
|---|---|
| `cli.py` | Arguments and `main()`; `scrape_profile()` picks the site for a target |
| `core/console.py` | `t()`, colours, `log()`, `status()` and the progress bar |
| `core/site.py` | The site interface: `Site`, `Post`, `Verdict` |
| `core/pacing.py` | Page reads: `--wait` gap, backoff on rate limits, streak stops |
| `core/download.py` | `Download` (which files a post needs), aria2c / requests downloads |
| `core/mux.py` | ffmpeg muxing and photo slideshows |
| `core/records.py` | Progress database, `videos.csv`, `failed.txt` |
| `core/flow.py` | Main loop: read → download → mux, in overlapping batches |
| `sites/tiktok.py` | Everything TikTok: page reads, post list (yt-dlp), CSV row, MP4 tags |

## Rules for changes

- **Every message is bilingual.** Write user-facing text as `t("English", "Tiếng Việt")`.
  If you can't write Vietnamese, put the English text in both and say so in the pull request.
- **Use the color helpers** (`green()`, `red()`, `yellow()`, `gray()`...) instead of raw ANSI codes,
  so `--no-color` and `NO_COLOR` keep working.
- **Keep the core site-neutral.** Site URLs, page JSON keys and bot-protection markers belong in
  `tiktok_dl/sites/<site>.py`; the core reaches a site only through `core/site.py`. Don't add
  code to the launcher `tiktok-dl.py`. The version is `__version__` in `tiktok_dl/__init__.py`
  and nowhere else (`pyproject.toml` reads it). See
  [docs/adr/0002](docs/adr/0002-package-with-shared-core-and-site-modules.md).
- **Call helpers through their module.** Use `console.log(...)`, `console.status(...)`,
  `download.download_requests(...)` and the like instead of `from ... import log`, so tests can
  replace them (`tests/test_layout.py` checks this).
- **Keep the dependencies small.** They are listed in both `requirements.txt` and
  `pyproject.toml`; change both (a test checks that they match).
- **Comments and commit messages are in English.**

## Test your change

### Automated checks

Install the dev tools once (inside a virtual environment if you like):

```bash
pip install -r requirements.txt -r requirements-dev.txt
```

Then, from the repo root:

```bash
ruff check .      # lint (settings in ruff.toml)
pytest            # unit tests in tests/, no network needed
```

The tests import the `tiktok_dl` package from the clone (`tests/conftest.py` puts it first on
`sys.path`), so there is nothing to install. Patch a function in the module that defines it. To
run them against an installed copy instead, as CI does, install
the package (`pip install .`) and run `pytest` with `TIKTOK_DL_TEST_INSTALLED=1` set. They never call TikTok: anything that would use the network is
mocked, and a test that tries a real HTTP request fails. When you fix a bug, add a test for it.

GitHub Actions runs the same two commands on every push and pull request, on Windows and Linux
with Python 3.10 and 3.13 (`.github/workflows/ci.yml`). It then builds the package, checks that
the pipx-installed `tiktok-dl --version` matches `__version__`, and runs the tests again against
the installed wheel.

### Manual checks

The tests don't cover downloading and muxing. Before opening a pull request, run at least:

```bash
python tiktok-dl.py @tiktok -n 5                 # normal videos
python tiktok-dl.py <link to a photo post>       # photo slideshow
python tiktok-dl.py @tiktok -n 5 --lang vi       # Vietnamese messages
python tiktok-dl.py @tiktok -n 5 --no-color      # plain output
```

Then run the first command again: it should skip the posts it already downloaded.

## Send a pull request

1. **Fork** [Aluminat/tiktok-dl](https://github.com/Aluminat/tiktok-dl) on GitHub (button in the top right).
2. Create a branch for your change:
   ```bash
   git checkout -b fix-short-links
   ```
3. Commit with a clear message: a short title, a blank line, then what changed and why.
   ```
   Fix short links that redirect twice

   - Follow up to 5 redirects instead of 1 for vt.tiktok.com links.
   ```
4. Push the branch to your fork:
   ```bash
   git push -u origin fix-short-links
   ```
5. On GitHub, click **Compare & pull request** (base repository: `Aluminat/tiktok-dl`, branch `main`)
   and fill in the template.

Keep one topic per pull request. Small pull requests get reviewed faster.
