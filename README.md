# tiktok-dl

**English** · [Tiếng Việt](README.vi.md)

> [!WARNING]
> **This project is for educational purposes only.** Do not use it to re-upload or sell other people's content.

Download every video from a TikTok profile, with full metadata.

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

Files are saved in `downloads/<username>/`.

Messages are in **English** by default (Vietnamese if your system language is Vietnamese).
Use `--lang en` or `--lang vi` to choose.

Output is colored (green = done, red = failed, yellow = reason / warning) with a live progress bar.
Use `--no-color` (or set `NO_COLOR=1`) for plain text.

Run `python tiktok-dl.py -h` to see all options.

## Speed and rate limits

The defaults are kept low so TikTok is less likely to block you or flag your account
(more care matters when you use `--session-id`):

| Option | Default | What it sets |
|---|---|---|
| `-j`, `--jobs` | 16 | files aria2c downloads at once |
| `-x`, `--connections` | 4 | aria2c connections per file (max 16) |
| `-w`, `--workers` | 8 | threads reading post pages |
| `--wait` | 0.3 | seconds between TikTok page requests |

You can raise them, for example `-x 8 --wait 0.1`, but going faster makes blocks more likely.

If TikTok answers with HTTP 429 (too many requests) or 403 while reading pages, the tool shows one
warning, pauses and retries with a longer wait each time (5, 10, 20 s), or waits as long as
TikTok asks in its `Retry-After` header (up to 2 minutes). After 8 such answers in a row the tool
stops instead of retrying every post (TikTok may have blocked your IP for a while). Wait a bit, then
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

## Problems?

- **Can't get the post list:** run `pip install -U yt-dlp`
- **Some posts failed:** see `failed.txt` in the output folder (link + reason for each post), then run the same command again
- **Ad / TikTok Shop videos fail:** TikTok does not give a download link for these on the web, even when you are logged in. They are listed in `failed.txt` so you can open them yourself, and re-runs skip them (use `--retry-all` to try again)
- **Private account:** set `TIKTOK_SESSIONID` (or add `--session-id`), see [Get your session ID](#get-your-session-id-optional)

## Contributing

Bug reports and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Disclaimer

**This is for educational purposes only.** You are responsible for how you use it.
Respect the creators' copyright and TikTok's Terms of Service.

This project is not affiliated with TikTok or ByteDance. The author is not responsible for any misuse.

## License

MIT © doumamay
