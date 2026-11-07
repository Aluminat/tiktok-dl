# 3. What a second site adds to the site interface

Status: accepted (issue #32); extends [ADR 0002](0002-package-with-shared-core-and-site-modules.md)

## Context

ADR 0002 left two questions for the issue that adds a second site: how login works per site, and
how the four core messages that still named TikTok are worded. Instagram (issue #32) is that site,
and it brings needs TikTok did not have:

- It works only logged in, with its own cookie, and must make no request without one.
- Some of its answers must end the run at once: a check the user has to clear in a browser
  (`checkpoint_required`, `challenge_required`), an expired login, a limited account. TikTok's
  `BLOCKED` stops only after a streak of 3, and `RATE_LIMITED` retries.
- It needs slower defaults (`--wait 3`, 1 worker), while TikTok keeps its own.
- A reel link (`/reel/<code>/`) does not name its author, but the output folder is
  `instagram/<author>/`.

## Decision

- **Login per site.** `Site.session(opts)` (a class method) picks the site's cookie from the
  options: TikTok `--session-id` / `TIKTOK_SESSIONID`, Instagram `--ig-session-id` /
  `INSTAGRAM_SESSIONID`. `core.flow.run_profile` constructs the site *before* it makes any folder,
  so a site that needs a login can fail in its constructor with nothing written and no request.
  `--check-session` stays TikTok-only.
- **`Verdict.STOP`.** An answer that ends the run at once. `PageReader` stops every page read of
  the run and raises `SiteStop` (a `RateLimited`, so the flow writes `failed.txt` with the stop note
  as for the other stops) with the site's message from `PageReader.stop_message(response)`. The
  CLI prints that message. A post being read when the run stops is left unmarked, so the next run
  tries it again.
- **Defaults per site.** `Site.defaults` holds `wait`, `workers` and `connections`. The CLI leaves
  those three flags unset (`None`); `core.flow.run_settings()` fills them in from the site's
  defaults and keeps them in range, on a *copy* of the options, because one command line may have
  targets on several sites.
- **Profile from the post.** `parse()` may give `None` as the profile for a post link that does not
  name it. The flow then calls `Site.owner(entry)` once, before making the folder. Instagram reads
  the post to learn its author and keeps that answer for `read_post()`, so the post is read once. A
  re-run of such a link reads it once more to find the folder; a link with the username in it
  (`instagram.com/<user>/reel/<code>/`) needs no read to be skipped. When the owner read fails,
  there is no folder to record the failure in: the target fails with an error.
- **Core messages name the site.** `PageReader.label` and `limit_codes` (`"TikTok"`, `"429/403"`;
  `"Instagram"`, `"429"`) word the rate-limit warning, the read error and the rate-limit stop;
  `RateLimited` carries them (`site`, `codes`) to the CLI. `finalize`'s truncated-download message
  uses the post's site label. TikTok's text is unchanged.
- **`videos.csv` gets a `file` column**, last, for every site: the downloaded file's name, from the
  progress database. A site's CSV row uses TikTok's column names, in TikTok's order, for what both
  sites have (id, url, type, created, username, description, plays, likes, comments, duration, ...),
  then its own columns. TikTok's columns keep their names and order.
- **Download cookies.** `core.flow` gives aria2c `site.client.cookie_header()`. Instagram returns
  an empty string: its file links are signed, and the session cookie stays in the requests
  session's cookie jar for `.instagram.com` only, so neither aria2c nor the requests fallback sends
  it to the CDN.

## Consequences

- A site that never answers `STOP` needs nothing new: `stop_message()` has a generic default, and
  `owner()` is only called when `parse()` gives no profile.
- `Site(profile, session, wait, proxy)` keeps its shape; `profile` may be `None` until `owner()`
  sets it.
- The `file` column is added to TikTok's `videos.csv` too, after the existing columns.
- Files keep the shared name `<date>_<id>_<caption>.mp4`; for Instagram the id is the numeric
  media id, which is the shortcode read as a base-64 number, so a link's id is known without a
  request.
