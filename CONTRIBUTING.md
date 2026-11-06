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

Everything lives in **`tiktok-dl.py`**. The docstring at the top of the file explains how a
download works and has a code map. In short:

| Part | What it does |
|---|---|
| `list_posts()` | Gets the post list of a profile with yt-dlp |
| `class TikTok` | Reads post / profile pages and their embedded JSON |
| `flatten()`, `mux_tags()` | Build the CSV row and the MP4 tags |
| `class Post` | Decides which files a post needs (video, cover, subtitles, images, music) |
| `download_aria2()` | Downloads files with aria2c |
| `mux_video()`, `mux_slideshow()` | ffmpeg muxing and photo slideshows |
| `scrape_profile()` | Main loop: read → download → mux, in overlapping batches |
| `ProgressBar`, `log()` | Console output and the progress bar |

## Rules for changes

- **Every message is bilingual.** Write user-facing text as `t("English", "Tiếng Việt")`.
  If you can't write Vietnamese, put the English text in both and say so in the pull request.
- **Use the color helpers** (`green()`, `red()`, `yellow()`, `gray()`...) instead of raw ANSI codes,
  so `--no-color` and `NO_COLOR` keep working.
- **Keep it one file** and keep the dependencies in `requirements.txt` small.
- **Comments and commit messages are in English.**

## Test your change

There are no automated tests yet. Before opening a pull request, run at least:

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
