"""tiktok-dl - download every video from a TikTok profile, and Instagram and Facebook reels.

How it works
------------
1. Pick the site     sites.find_site()       the site a link belongs to, or --site for a username
                                             (TikTok without it).
2. List posts        Site.list_posts()       TikTok: yt-dlp's user extractor (flat list of post ids).
3. Read each post    Site.read_post()        TikTok: the post page embeds its data in
                                             <script id="__UNIVERSAL_DATA_FOR_REHYDRATION__">.
                                             Instagram: /api/v1/media/<id>/info/, logged in.
                                             Facebook: yt-dlp's Facebook extractor, information only.
4. Plan downloads    core.download.Download  video (+ a separate audio stream) / cover / subtitles,
                                             or images + music for photo posts, from Post.media().
5. Download          download_aria2()        aria2c, one URL per file per attempt (falls back to
                     download_requests()     requests).
6. Mux               core.mux.finalize()     ffmpeg adds metadata, cover and subtitles; photo posts
                                             become MP4 slideshows (mux_slideshow).

core.flow.run_profile() runs steps 3-6 in batches and overlaps them: while aria2c downloads one
batch, the next batch's metadata is read and the previous batch is muxed in the background.

Code map
--------
cli.py              arguments, main(), scrape_profile() (picks the site for a target)
core/console.py     message language (t()), colours, log(), status(), ProgressBar, formatters
core/errors.py      ScrapeError, RateLimited, Challenged, ChallengeStop, SiteStop
core/site.py        the site interface: Site, Post, Verdict (what an answer means), Entry, Media, ...;
                    Site.folder() is a profile's folder under --output (TikTok: no site folder)
core/pacing.py      PageReader: --wait gap, backoff after rate-limited answers, streak stops; fetch()
                    is the one request of a page read (Facebook answers it through yt-dlp)
core/cookies.py     --cookies: a Netscape cookie file, read in memory, never written
core/download.py    Download (which files a post needs), aria2c / requests downloads, file checks
core/mux.py         ffmpeg muxing, slideshows, finalize()
core/records.py     Progress (SQLite, lets a re-run skip finished posts), videos.csv, failed.txt
core/flow.py        run_profile(): the read -> download -> mux loop for one profile
core/tools.py       finding aria2c / ffmpeg (next to tiktok-dl.py first, then PATH)
core/stop.py        STOP (Ctrl+C), core/util.py: UA, sanitize(), ...
sites/tiktok.py     everything TikTok: web client, page parsing, links, CSV row (flatten), MP4 tags,
                    post list (yt-dlp), session cookie
sites/instagram.py  Instagram reels and video posts: API reader (logged in), CSV row, MP4 tags
sites/facebook.py   Facebook reels: yt-dlp extracts (information only), CSV row, MP4 tags
sites/__init__.py   SITES and find_site(): which site a target goes to (--site)

The core never contains a site's URLs, JSON keys or bot-protection markers (docs/adr/0002).
Messages are bilingual: t("English", "Tiếng Việt"). Please add both when you add a message.
"""

__version__ = "2.0.0"
