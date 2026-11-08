# 4. Posts saved item by item

Status: accepted (issue #33); extends [ADR 0003](0003-what-a-second-site-adds-to-the-site-interface.md)

## Context

Instagram photo posts and carousels (issue #33) do not fit the two shapes `Media` had: one video
(with cover and subtitles), or TikTok's photo post (images and music, turned into a slideshow by
default, marked done even with an image missing). A carousel mixes photos and videos, the user
wants the original files rather than a slideshow, and the issue asks that a post is done only when
every item is on disk, with only the missing item downloaded again on the next run.

## Decision

- **`Media.items`**, a list of `Item(urls, video, cover)`: a post saved item by item as its original
  files, in order. One item is saved as `<base>.<ext>`, several as `<base>_01.<ext>`,
  `<base>_02.<ext>`, ... (`<base>` is the shared `<date>_<id>_<caption>`). A video item is muxed with
  the post's tags and its own cover, like a single video post (`mux_video(..., item=...)`); a photo
  is copied as it is. Instagram gives a photo only at its largest size: smaller sizes are not
  mirrors, so a failed download never quietly becomes a thumbnail.
- **All or nothing.** `mux.save_items()` raises when any item is missing, before anything is put in
  the profile folder. The work folder `.parts/<id>/` is kept (as for any failed post), and the
  download step already skips valid files there, so the next run fetches only the missing item. A
  cut-short download is caught as before: aria2c's `.aria2` file or an error page makes the file
  invalid, and it is deleted. Instagram reports no file sizes, so the size check TikTok has does not
  apply.
- **Several files per post in the `file` column.** The progress database and `videos.csv` list every
  file of such a post, joined with `records.FILE_SEP` (`" | "`; `sanitize()` removes `|` from names).
  `Progress.done()` needs all of them on disk. TikTok posts keep a single name.
- **`slideshow` is a per-site default** (`Site.defaults`, filled in by `run_settings` as in ADR
  0003): TikTok `True` (unchanged), Instagram `False`. The CLI leaves it `None` unless `--slideshow`
  or `--no-slideshow` is given. A slideshow is built only for several items that are all photos;
  `--keep-images` keeps the photos next to it, as for TikTok.

## Consequences

- TikTok's photo posts keep their path (`Media.images`, `is_slide`), including the "N image(s)
  missing" note: changing that was not asked for.
- `Download.video` is `None` for posts without a video, and the flow checks that instead of
  `is_slide` before looking for a truncated video.
- A post saved item by item prints its first file and how many more there are.
