# tiktok-dl

**English** · [Tiếng Việt](README.vi.md)

> [!WARNING]
> **This project is for educational purposes only.** Do not use it to re-upload or sell other people's content.

Download every video from a TikTok profile, or a single Instagram or Facebook reel, with full metadata.

- Fast downloads with **aria2c**
- **ffmpeg** adds title, author, date, description, stats, cover and subtitles to each MP4
- Photo posts are turned into MP4 slideshows
- Saves all info to `videos.csv` and `metadata/*.json`
- Stop anytime, run again to continue

## Install

1. Install [Python 3.10+](https://www.python.org/downloads/)
2. Run:
   ```bash
   pip install -r requirements.txt
   ```
   On Linux/macOS use `python3 -m pip install -r requirements.txt`.
   If pip says the environment is "externally managed", create a virtual environment first:
   `python3 -m venv .venv && . .venv/bin/activate`
3. Install aria2c and ffmpeg (both optional, see below):
   - **Windows:** put `aria2c.exe` and `ffmpeg.exe` next to `tiktok-dl.py`
     - aria2c: https://github.com/aria2/aria2/releases
     - ffmpeg: https://www.gyan.dev/ffmpeg/builds/
   - **Debian/Ubuntu:** `sudo apt install aria2 ffmpeg`
   - **macOS** (with [Homebrew](https://brew.sh)): `brew install aria2 ffmpeg`

The script looks for each tool next to `tiktok-dl.py` first, then in `PATH`.

- **ffmpeg:** if none is found, it uses the ffmpeg that comes with the `imageio-ffmpeg` package
  (installed in step 2), so a separate ffmpeg install is optional.
- **aria2c:** if it is not found, files are downloaded with Python `requests` instead (slower).

### Or install it as a command

Instead of steps 2 and 3 above, you can install `tiktok-dl` as a command with
[pipx](https://pipx.pypa.io) (or `uv tool install`), without cloning the repo:

```bash
pipx install git+https://github.com/Aluminat/tiktok-dl
tiktok-dl @username
```

Then use `tiktok-dl` wherever the commands below say `python tiktok-dl.py`.
Update with `pipx upgrade tiktok-dl`, remove with `pipx uninstall tiktok-dl`.
An installed copy finds aria2c and ffmpeg through `PATH` (on Windows, put the `.exe` files in a
folder that is in `PATH`), and still falls back to the ffmpeg from `imageio-ffmpeg`.

On Linux/macOS, use `python3` instead of `python` in the commands below.

## Use

```bash
python tiktok-dl.py @username
```

| Command | What it does |
|---|---|
| `python tiktok-dl.py @username` | Download the whole profile |
| `python tiktok-dl.py @username -n 20` | Only the 20 newest posts |
| `python tiktok-dl.py @username -o D:\TikTok` | Save to another folder |
| `python tiktok-dl.py @username --metadata-only` | Only save info, no videos |
| `python tiktok-dl.py <video link>` | Download one video |
| `python tiktok-dl.py https://vt.tiktok.com/XXXXXXXXX/` | Short links from the TikTok app work too |
| `python tiktok-dl.py @username --lang vi` | Show messages in Vietnamese |
| `python tiktok-dl.py @username --retry-all` | Also retry deleted and ad / TikTok Shop posts that re-runs skip |
| `python tiktok-dl.py https://www.instagram.com/reel/XXXXXXXXXXX/` | Download one Instagram reel (needs a login, see [Instagram](#instagram)) |
| `python tiktok-dl.py https://www.facebook.com/reel/1234567890123456` | Download one Facebook reel (see [Facebook](#facebook)) |

Files are saved in `downloads/<username>/`.

### Sites and folders

The tool works out the site from each link:

| Link | Site |
|---|---|
| `tiktok.com/@username`, `.../video/...`, `.../photo/...`, `vt.tiktok.com/...`, `vm.tiktok.com/...` | TikTok |
| `instagram.com/<username>`, `.../reel/...`, `.../reels/...`, `.../p/...`, `.../tv/...` (also `www.` and `m.`) | Instagram |
| `facebook.com/reel/...`, `.../watch/?v=...`, `.../<page>/videos/...`, `.../<page>/reels/...`, `.../share/r/...`, `.../share/v/...` (also `www.`, `m.` and `web.`), `fb.watch/...` | Facebook |

**Instagram:** single reel and video post links work (see [Instagram](#instagram)). Instagram
profiles, photo posts and carousels are not supported yet. **Facebook:** single reel links work
(`facebook.com/reel/...`, `.../watch/?v=...` and `fb.watch/...`, see [Facebook](#facebook)). The
other Facebook links (pages, `.../videos/...`, `.../share/...`) fail with an error naming the
supported forms, and the tool goes on with the next target.

Without `--site`, a bare `username` or `@username` means TikTok and links from any site are
accepted. With `--site instagram`, `--site facebook` or `--site tiktok`, usernames belong to that
site and links must be from it: a link from a different site fails with an error, and the tool goes
on with the next target.

Each account gets its own folder:

| Site | Folder |
|---|---|
| TikTok | `downloads/<username>/` |
| Instagram | `downloads/instagram/<username>/` |
| Facebook | `downloads/facebook/<page id>/` (the page's number, or its `pfbid...` id) |

A link to a single post is saved in its author's folder, so each account has one `videos.csv`,
`failed.txt` and progress file. TikTok has no `tiktok/` folder because it was the only site
before: folders made by earlier versions stay where they are, and re-runs keep skipping the posts
already downloaded there.

Messages are in **English** by default (Vietnamese if your system language is Vietnamese).
Use `--lang en` or `--lang vi` to choose.

Output is colored (green = done, red = failed, yellow = reason / warning) with a live progress bar.
Use `--no-color` (or set `NO_COLOR=1`) for plain text.

Run `python tiktok-dl.py -h` to see all options.

## Speed and rate limits

The defaults are kept low so the site is less likely to block you or flag your account
(more care matters when you are logged in). Each site has its own; the options override them:

| Option | TikTok | Instagram | Facebook | What it sets |
|---|---|---|---|---|
| `-j`, `--jobs` | 16 | 16 | 16 | files aria2c downloads at once |
| `-x`, `--connections` | 4 | 4 | 4 | aria2c connections per file (max 16) |
| `-w`, `--workers` | 8 | 1 | 1 | threads reading post pages |
| `--wait` | 0.3 | 3 | 2 | seconds between page requests |

The Instagram and Facebook defaults are a cautious guess, not measured.

You can raise them, for example `-x 8 --wait 0.1`, but going faster makes blocks more likely.

If TikTok answers with HTTP 429 (too many requests) or 403 while reading pages, the tool shows one
warning, pauses and retries with a longer wait each time (5, 10, 20 s), or waits as long as
TikTok asks in its `Retry-After` header (up to 2 minutes). After 8 such answers in a row the tool
stops instead of retrying every post (TikTok may have blocked your IP for a while); finished posts
are kept and `failed.txt` says why it stopped. Wait a bit, then
run the same command again to continue; a larger `--wait` or fewer `--workers` helps.

## Failed downloads log

If a post can't be downloaded, the tool tells you:

- On screen, each failed post is shown with its **link** and the **reason**.
- All failed posts are also saved to **`failed.txt`** in the output folder, so you can open each link and check it yourself.

```
https://www.tiktok.com/@username/video/7705012484368780552
    ad / TikTok Shop video: TikTok does not provide a download link on the web (even when logged in)
```

`failed.txt` is updated on every run and lists every post of the profile that is still failing, including ones from earlier runs (a single-video run does not wipe it). It is removed when nothing fails.

Posts that can never be downloaded (deleted posts, and ad / TikTok Shop posts that TikTok gives no download link for) fail once and are then marked `(skipped)` in `failed.txt`. Re-runs skip them without loading their page; everything else that failed (network errors, rate limits, ffmpeg errors) is retried on every run. Add `--retry-all` to try the skipped posts again, for example if TikTok changes how it serves them.

## Get your session ID (optional)

You only need this for **private accounts you follow** or **age-restricted posts**.

1. Open https://www.tiktok.com in **Chrome** or **Edge** and log in.
2. Press **F12** to open DevTools.
3. Go to the **Application** tab → **Storage** → **Cookies** → `https://www.tiktok.com`.
   (Firefox: **Storage** tab → **Cookies**.)
4. Type `sessionid` in the filter box.
5. Double-click the **Value** of the row named exactly `sessionid` and copy it (a long string of letters and numbers).
6. Put it in the `TIKTOK_SESSIONID` environment variable (recommended). The commands below ask
   for the value and you paste it in, so it is not saved in your shell history.
   It lasts until you close the terminal.

   PowerShell (Windows):
   ```powershell
   $env:TIKTOK_SESSIONID = Read-Host "sessionid"
   ```
   bash / zsh (Linux, macOS, Git Bash):
   ```bash
   read -rs -p "sessionid: " TIKTOK_SESSIONID && export TIKTOK_SESSIONID
   ```
   (zsh: `read -rs "TIKTOK_SESSIONID?sessionid: " && export TIKTOK_SESSIONID`)
7. Check that it works:
   ```bash
   python tiktok-dl.py --check-session
   ```
   - `✓ Session OK: logged in as @your_account` → it works.
   - `✗ Session ID is invalid or expired` → copy a new one.
8. Download with it:
   ```bash
   python tiktok-dl.py @username
   ```
   The tool checks the session first and stops if it is not valid.

You can also pass it with `--session-id PASTE_YOUR_SESSIONID_HERE`, but then it is saved in your
shell history. If both are set, `--session-id` wins.

> [!CAUTION]
> Your `sessionid` works like your password. **Never share it** or post it online.
> If you log out of TikTok, the session ID stops working and you need to copy a new one.
> Using a **secondary account** is safer.

## Instagram

Single **reel** and **video post** links work: `instagram.com/reel/...`, `.../reels/...`,
`.../p/...` (when the post is one video) and `.../tv/...`, also with the username in front
(`instagram.com/<username>/reel/...`). Whole profiles, photo posts and carousels are not supported
yet. Each reel goes to `downloads/instagram/<username>/`, with the same MP4 tags, `videos.csv`,
`failed.txt` and resume as TikTok.

> [!WARNING]
> Instagram only works **logged in**, with your account's `sessionid` cookie, and **Meta may ban
> accounts that scrape**. Use a **secondary account**, not your main one.

1. Open https://www.instagram.com in **Chrome** or **Edge** and log in (a secondary account).
2. Press **F12**, go to **Application** → **Storage** → **Cookies** → `https://www.instagram.com`
   (Firefox: **Storage** tab → **Cookies**).
3. Type `sessionid` in the filter box and copy the **Value** of the row named exactly `sessionid`.
4. Put it in the `INSTAGRAM_SESSIONID` environment variable. The commands ask for the value, so it
   is not saved in your shell history:

   PowerShell (Windows):
   ```powershell
   $env:INSTAGRAM_SESSIONID = Read-Host "sessionid"
   ```
   bash / zsh (Linux, macOS, Git Bash):
   ```bash
   read -rs -p "sessionid: " INSTAGRAM_SESSIONID && export INSTAGRAM_SESSIONID
   ```
   (zsh: `read -rs "INSTAGRAM_SESSIONID?sessionid: " && export INSTAGRAM_SESSIONID`)
5. Download:
   ```bash
   python tiktok-dl.py https://www.instagram.com/reel/XXXXXXXXXXX/
   ```

`--ig-session-id PASTE_IT_HERE` works too, but is saved in your shell history; it wins over the
environment variable. The cookie is only sent to instagram.com: it is never printed, written to a
file, or given to aria2c. Without a cookie, the tool says so and makes no request.

The tool asks Instagram for each post the same way its website does, slowly (3 s between requests,
one at a time). When Instagram answers:

- **too many requests (HTTP 429):** the tool slows down and retries, like TikTok.
- **a check (`checkpoint_required` / `challenge_required`):** the run stops at once. Open Instagram
  in a browser with the same account and clear the check yourself, then run the same command again.
  The tool never tries to solve or skip it.
- **`login_required`:** the run stops; the cookie expired or is wrong, copy a new one.
- **`feedback_required`:** the run stops; Instagram is limiting the account. Wait hours or a day.

Deleted posts and posts your account cannot see are marked `(skipped)` in `failed.txt` (when the
link has the username in it, so the tool knows the folder) and re-runs skip them; `--retry-all`
tries them again. A `/reel/...` link without the username is read once on every run to find its
author's folder.

## Facebook

Single **reel** links work: `facebook.com/reel/<id>`, `facebook.com/watch/?v=<id>` (also `m.` and
`web.`) and `fb.watch/...` short links (the tool follows the short link to the reel first). Pages,
stories and highlights are not supported. Each reel goes to `downloads/facebook/<page id>/`, with
the same MP4 tags, `videos.csv`, `failed.txt` and resume as TikTok. For reels Facebook does not give
a clean page name, so the folder uses the page's id; the name is in the `nickname` column of
`videos.csv` and in the MP4 tags.

```bash
python tiktok-dl.py https://www.facebook.com/reel/1234567890123456
```

How it works: [yt-dlp](https://github.com/yt-dlp/yt-dlp) is used **only to find** the reel's video
link, audio link and metadata (its Facebook extractor, as a library). aria2c downloads the video
and audio (or Python `requests` without aria2c), and ffmpeg muxes them into one MP4 with the tags.
yt-dlp's own downloader and ffmpeg steps are never used. Without ffmpeg, the video and its sound
are saved as two files (`.mp4` and `.m4a`).

The tool reads each reel slowly (2 s between requests, one at a time). When Facebook answers:

- **too many requests (HTTP 429):** the tool slows down and retries, like TikTok.
- **a login wall:** that link fails with a message asking for `--cookies` (see below), and the
  tool goes on with the next target.
- **the reel does not exist or was deleted:** the link fails with that reason. As a reel link
  does not name its page, the tool has no folder to mark it `(skipped)` in, so it is read again
  on the next run.

The tool does not get around anything: it uses no fake browser or crawler identity, and never
tries to pass a check.

### `--cookies`: reels behind a login wall

Most public reels download without a login. For one that shows a login wall, give the tool your
Facebook cookies as a **Netscape cookie file** (`cookies.txt`):

> [!WARNING]
> **Meta may ban accounts that scrape.** Use a **secondary account**, not your main one.
> The cookie file works like your password: never share it, and delete it when you are done.

1. In **Chrome**, **Edge** or **Firefox**, log in to https://www.facebook.com (a secondary account).
2. Install a browser extension that exports cookies in the **Netscape** format (`cookies.txt`),
   and export the cookies of `facebook.com` to a file, for example `fb-cookies.txt`.
3. Download with it:
   ```bash
   python tiktok-dl.py https://www.facebook.com/reel/1234567890123456 --cookies fb-cookies.txt
   ```

Facebook's login uses several cookies (`c_user`, `xs`, ...), which is why it takes a file and not
a single value. The file is only read, in memory: its contents are never printed, copied to disk,
or put on a command line, and only its `facebook.com` cookies are used, for facebook.com only (never
for the video files, never given to aria2c). If the wall is still there, the cookies are logged out
or expired: export them again.

`--cookies` works for **Instagram** too: when `--ig-session-id` / `INSTAGRAM_SESSIONID` is not set,
the tool takes the `sessionid` cookie of `instagram.com` from the file. So one file exported while
logged in to both sites serves both.

## Problems?

- **Can't get the post list:** run `pip install -U yt-dlp`
- **Some posts failed:** see `failed.txt` in the output folder (link + reason for each post), then run the same command again
- **Ad / TikTok Shop videos fail:** TikTok does not give a download link for these on the web, even when you are logged in. They are listed in `failed.txt` so you can open them yourself, and re-runs skip them (use `--retry-all` to try again)
- **Private account:** set `TIKTOK_SESSIONID` (or add `--session-id`), see [Get your session ID](#get-your-session-id-optional)
- **Instagram stops with "pass a check", "logged out" or "limiting this account":** see [Instagram](#instagram)
- **"Facebook shows a login wall for this reel":** add `--cookies <file>`, see [Facebook](#facebook). **"Facebook gave no reel data":** run `pip install -U yt-dlp`
- **"TikTok's bot protection blocked the request (challenge page)":** TikTok answered with its "Please wait..." check page instead of the real page. The tool does not retry it: if the profile page is blocked, that profile is skipped; if 3 post pages in a row are blocked, the run stops (finished posts are kept and `failed.txt` says why). Try again later or from another network (VPN off, a different connection). `--session-id` does not help with this

## Contributing

Bug reports and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Disclaimer

**This is for educational purposes only.** You are responsible for how you use it.
Respect the creators' copyright and TikTok's Terms of Service.

This project is not affiliated with TikTok or ByteDance. The author is not responsible for any misuse.

## License

MIT © doumamay
