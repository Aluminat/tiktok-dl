# 5. Facebook reels: yt-dlp extracts, the core downloads; `--cookies`

Status: accepted (issue #34); extends [ADR 0003](0003-what-a-second-site-adds-to-the-site-interface.md)

## Context

Facebook is the third site. Unlike TikTok and Instagram, the tool does not read its pages itself:
the user chose yt-dlp's Facebook extractor to find a reel's links, with aria2c and ffmpeg doing the
rest. Facebook also brings needs the core did not have:

- A reel comes as two DASH streams, video and audio, that ffmpeg must join.
- Its page reads go through a library, not through `PageReader`'s `requests` session, but must
  still get the core's `--wait` gap, backoff and stop rules.
- Login is optional and takes several cookies (`c_user`, `xs`, ...), so a single cookie value like
  `--ig-session-id` does not fit.

## Decision

- **yt-dlp only extracts.** `extract_info(watch_url, download=False, process=False,
  ie_key="Facebook")`. `process=False` because format processing writes the cookies yt-dlp would
  send into the answer (`info["cookies"]`), which would end up in `metadata/<id>.json`. The tool
  picks the formats itself: the largest DASH video-only stream with the best DASH audio stream, or
  else the largest format that has its sound in it (sd/hd). HLS and segmented DASH are not used.
  The `facebookexternalhit` User-Agent that yt-dlp's extractor puts on Facebook's formats (to
  dodge Facebook's rate limit) is never used: downloads use the core's own browser User-Agent.
- **`Media.audio` on a video post** is a separate sound stream. `Download` adds it as a task
  (`audio.m4a`); `mux_video` maps the video from the first input and the sound from the second.
  The fallback attempts drop the cover or subtitles, never the audio. A missing audio file fails
  the post. Without ffmpeg, the video and its sound are kept as two files. Posts without such a
  stream get the same ffmpeg command as before.
- **`PageReader.fetch(url)`** is the one request of a page read (by default `self.s.get`). Facebook
  answers it with yt-dlp, as an `Answer` with the same `status_code` and `headers` (`Retry-After`)
  a response has, and raises `requests.ConnectionError` for a network error. So `read()` keeps its
  retries, backoff and streak stops for every site.
- **The login wall is `Verdict.BLOCKED`.** It fails that link at once with the `--cookies` message;
  raised in `owner()`, it is a per-target error and the run goes on with the next link. (`STOP`
  would end the whole command.) A rate limit is `RATE_LIMITED`, a missing reel `PERMANENT`.
- **`--cookies FILE`** is a general option: a Netscape cookie file, read by `core.cookies` in
  memory. It is parsed by hand, because the standard library's `MozillaCookieJar` and yt-dlp's jar
  both put a bad line in their error text. Each site takes its own domain's cookies from it in
  `Site.session()` / its constructor, so `Site.session()` keeps returning a string (for Facebook,
  the file's path). Facebook puts its cookies into the `YoutubeDL`'s cookie jar after construction:
  yt-dlp is never given `cookiefile`, because `YoutubeDL.close()` writes that file back. Instagram
  takes the file's `sessionid` when `--ig-session-id` / `INSTAGRAM_SESSIONID` is not set. TikTok
  does not use it.
- **The folder is `facebook/<page id>`.** For reels yt-dlp's `uploader` is the page title
  ("9.7K views · 352 reactions | <caption> | <page>"), so the id names the folder and the name goes
  to the CSV `nickname` column and the tags.

## Consequences

- No reel link names its page, so every run extracts the reel once to find the folder (as for an
  Instagram `/reel/` link without the username), and a missing or deleted reel cannot be marked
  `skipped`: there is no folder to record it in, so the target fails with an error and is read
  again next time (ADR 0003 accepted the same for Instagram).
- `fb.watch` short links are followed with plain `requests` in `parse()`, before the site is
  constructed (as TikTok's short links are), with no cookies and no proxy.
- A change in yt-dlp's Facebook answers can break reads; the "no reel data" message tells the
  user to update yt-dlp.
