"""tiktok-dl - download every video from a TikTok profile.

How it works
------------
1. List posts      list_posts()      yt-dlp's TikTok user extractor (flat list of post ids).
2. Read metadata   TikTok.item()     each post page embeds its data in
                                     <script id="__UNIVERSAL_DATA_FOR_REHYDRATION__">.
3. Plan downloads  Post              video / cover / subtitles, or images + music for photo posts.
4. Download        download_aria2()  aria2c, one URL per file per attempt (falls back to requests).
5. Mux             finalize()        ffmpeg adds metadata, cover and subtitles; photo posts become
                                     MP4 slideshows (mux_slideshow).

scrape_profile() runs steps 2-5 in batches and overlaps them: while aria2c downloads one batch,
the next batch's metadata is read and the previous batch is muxed in the background.

Code map
--------
Console output   log(), status(), colors, ProgressBar   (all user-facing text goes through t())
Helpers          parse_target(), resolve_short_link(), sanitize(), ...
TikTok client    class TikTok
Metadata         flatten() -> videos.csv row, mux_tags() -> MP4 tags
Progress         class Progress (SQLite, lets a re-run skip finished posts)
Download / mux   class Post, download_aria2(), download_requests(), mux_video(), mux_slideshow()
Main flow        scrape_profile(), main()

Messages are bilingual: t("English", "Tiếng Việt"). Please add both when you add a message.
"""

__version__ = "2.0.0"
