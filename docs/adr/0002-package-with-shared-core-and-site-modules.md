# 2. A package with a shared core and one module per site

Status: accepted (issue #30); supersedes [ADR 0001](0001-single-file-app-with-thin-install-wrapper.md)

## Context

tiktok-dl is going to support Instagram and Facebook reels next to TikTok (see issue #30 for the
agreed scope). Until now the whole app was one file, `tiktok-dl.py`, installed through a thin
`tiktok_dl.py` wrapper (ADR 0001). That file mixed TikTok's URLs, page JSON and bot-protection
rules with code that every site needs: downloads, muxing, the progress database, `videos.csv`,
`failed.txt`, backoff and resume. ADR 0001 left option (b), a package with a small launcher, open
for when the app outgrew one file.

## Decision

Option (b), with one module per site.

- **Layout.** The app is the `tiktok_dl` package:
  - `cli.py`: arguments, `main()`, and `scrape_profile()`, which picks the site for a target.
  - `core/`: the site-neutral parts (console output and `t()`, errors, the site interface, page
    pacing, downloads, muxing, progress records, the main loop, finding tools).
  - `sites/tiktok.py`: everything TikTok. `instagram.py` and `facebook.py` come later.
- **Launcher.** `tiktok-dl.py` is only a launcher. `python tiktok-dl.py ...` runs the package next
  to it, and it sets `core.tools.SCRIPT_DIR` to its own folder, so `aria2c.exe` / `ffmpeg.exe`
  placed next to it are still found first.
- **Entry points and version.** The console script is `tiktok-dl = "tiktok_dl.cli:main"`, and
  `python -m tiktok_dl` works too. The version has one source, `__version__` in
  `tiktok_dl/__init__.py`, which hatchling reads. The wheel holds the package only.
- **The site interface** (`core/site.py`) is the only way the core reaches a site. Its members are
  the ones agreed in issue #30, plus four that today's output needs:

  | Member | Why |
  |---|---|
  | `matches(url)` | is this link for this site? |
  | `parse(target)` | a post link or a profile -> (profile name, the single post or None) |
  | `read_post(id)` | a site-neutral `Post`: id, link, author, date, caption, counts, `media()` |
  | `list_posts(limit, work)` | every post of the profile (a site that cannot list raises with its message) |
  | `csv_row(post)`, `mux_tags(post)` | the `videos.csv` row and the MP4 tags |
  | `classify(answer)` | `Verdict.OK`, `RATE_LIMITED` (back off), `BLOCKED` (stop after a streak), `PERMANENT` (skip on re-runs) |
  | `save_profile(out_dir)` | `profile.json`, `avatar.jpg` and the header line |
  | `link(id)` | the link of an old `failed.txt` row that was stored without one |
  | `client` | the page reader (`core.pacing.PageReader`): the requests session and cookies for downloads |
  | `referer` | the aria2c `--referer` value |

- **Core rules.** The core never contains a site's URLs, page JSON keys or bot-protection
  markers. A site's answers to page reads and the errors of failed post reads reach the core
  only as a `Verdict`: TikTok's challenge page is `BLOCKED`, HTTP 429/403 is `RATE_LIMITED`, and
  a deleted post (status code 10204 in the page) is `PERMANENT`. The one other way a post is
  marked "skipped" is `Media.permanent`, on purpose: the post was read fine (its metadata JSON
  and CSV row are written, and `--metadata-only` records it as usual), but the site gives no
  download link and no re-run can change that (TikTok ad / TikTok Shop videos). That is a fact
  about the post's data, not an answer to classify, so `Post.media()` reports it. The core then
  runs backoff, streak stops, `failed.txt` and resume the same way for every site. `tests/test_layout.py` checks the imports and the forbidden words.
- **Tests import the package** (`tests/conftest.py` puts the clone first on `sys.path`, or uses the
  installed copy with `TIKTOK_DL_TEST_INSTALLED=1`). A helper that tests replace is called through
  its module (`console.log(...)`, `download.download_requests(...)`), never imported by name into
  another module, so a patch on its defining module reaches every caller; `tests/test_layout.py`
  checks this too.

## Consequences

- Running from a clone or a downloaded zip needs the `tiktok_dl/` folder next to `tiktok-dl.py`.
  The file alone no longer runs.
- The installed wheel no longer ships a stray top-level `tiktok-dl.py`.
- `core.tools.SCRIPT_DIR` is the launcher's folder when started through `tiktok-dl.py`. Otherwise
  (installed command, `python -m tiktok_dl`, tests) it is the folder that holds the package: the
  clone root, or `site-packages` for an installed copy, where nobody puts tools, so `PATH` (and
  `imageio-ffmpeg`) is used, as before.
- Four core messages still name TikTok, because their text must not change in this step: the
  rate-limit stop and warning in `core/pacing.py`, the `HTTP <code>, TikTok is limiting requests`
  read error, and `finalize`'s "smaller than the size TikTok reports". The issue that adds the
  second site decides their wording.
- The TikTok login flags (`--session-id`, `--check-session`, `TIKTOK_SESSIONID`) are handled by
  `cli` calling the TikTok module directly. How login works per site is decided when a second site
  needs one.
- `scrape_profile()` stays as the CLI-level entry: `cli.scrape_profile()` picks the site and calls
  `core.flow.run_profile()`.
- History of the moved code is reachable with `git log --follow -C -C` / `git blame -C -C`.
