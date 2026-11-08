"""Downloads: which files a post needs (Download), aria2c (one URL per file per attempt) or plain
requests, and checking the files."""
from __future__ import annotations

import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import requests

from .stop import STOP, wait_result
from .util import UA, sanitize

if TYPE_CHECKING:
    from .site import Post, Site


def base_name(post: Post) -> str:
    desc = sanitize(post.caption)
    return f"{post.created:%Y-%m-%d}_{post.id}" + (f"_{desc}" if desc else "")


class Download:
    """Which files one post needs and where they go: video, cover and subtitles, or the images and
    music of a photo post. The URLs come from the site (Post.media())."""

    def __init__(self, post: Post, work: Path, opts, site: Optional[Site] = None):
        self.post = post
        self.site = site                     # gives the MP4 tags (tags); None in tests that only plan
        self.id = post.id
        self.base = base_name(post)
        self.created = post.created          # file times (finalize)
        self.work = work / self.id
        self.tasks: list[tuple[list[str], Path, dict[str, int]]] = []   # (mirrors, dest, {url: expected size})
        self.hidden: Optional[str] = None    # reason when the site gives no download link
        self.permanent = False               # True when no re-run can ever download it
        self.incomplete = False              # every video download came out truncated
        self.video: Optional[Path] = None    # the post's video (a video post only)
        media = post.media()
        self.is_slide = bool(media.images)
        self.url = post.link
        # A post saved item by item (Media.items): (item file, its cover or None) per item, in order.
        self.items: list[tuple[Path, Optional[Path]]] = []

        if media.items:
            for i, item in enumerate(media.items, 1):
                dest = self.work / f"item_{i:02d}{'.mp4' if item.video else '.jpg'}"
                self.tasks.append((item.urls, dest, {}))
                cover = self.work / f"item_{i:02d}_cover.img" if item.video and item.cover and opts.cover else None
                if cover:
                    self.tasks.append(([item.cover], cover, {}))
                self.items.append((dest, cover))
            return

        if self.is_slide:
            self.images = [self.work / f"img_{i:02d}.jpg" for i in range(1, len(media.images) + 1)]
            for urls, dest in zip(media.images, self.images):
                self.tasks.append((urls, dest, {}))
            self.audio = self.work / "audio.mp3" if media.audio else None
            if media.audio:
                self.tasks.append(([media.audio], self.audio, {}))
            return

        if not media.video:
            self.permanent = media.permanent
            self.hidden = media.unavailable
            return
        self.video = self.work / "video.mp4"
        self.tasks.append((media.video, self.video, media.sizes))

        self.cover = self.work / "cover.img" if media.cover and opts.cover else None
        if self.cover:
            self.tasks.append(([media.cover], self.cover, {}))

        self.subs: list[tuple[Path, str]] = []
        seen = set()
        for sub in media.subtitles if opts.subs != "none" else []:
            if not sub.url or sub.lang in seen:
                continue
            if opts.subs == "original" and sub.machine:
                continue
            seen.add(sub.lang)
            dest = self.work / f"sub_{len(self.subs)}.vtt"
            self.subs.append((dest, sub.lang))
            self.tasks.append(([sub.url], dest, {}))

    @property
    def tags(self) -> dict:
        """The MP4 tags (computed when muxing)."""
        return self.site.mux_tags(self.post)


def is_valid_file(dest: Path, expected: int = 0) -> bool:
    """Reject "finished" downloads that are really HTML/JSON error pages, or that are smaller than
    the size the site reports for them (expected, 0 = unknown)."""
    try:
        if not dest.is_file() or dest.stat().st_size < max(64, expected) or Path(f"{dest}.aria2").exists():
            return False
        with open(dest, "rb") as fh:
            head = fh.read(16)
    except OSError:
        return False
    if dest.suffix == ".vtt":
        return head.lstrip(b"\xef\xbb\xbf").startswith(b"WEBVTT")
    if head.lstrip()[:1] in (b"<", b"{"):
        return False
    if dest.suffix == ".mp4":
        return head[4:8] == b"ftyp"
    return True


def is_truncated(dest: Path, expected: int) -> bool:
    """A download with valid content that stops short of the size the site reports."""
    try:
        return bool(expected) and is_valid_file(dest) and dest.stat().st_size < expected
    except OSError:
        return False


def download_aria2(aria2: str, tasks, cookie: str, jobs: int, conns: int, attempt: int,
                   referer: str = "") -> set[Path]:
    """Run one aria2c pass with each task's mirror number `attempt`. Files that fail validation are
    deleted, so the next pass retries them. Returns the files that came down truncated. `referer` is
    sent as the Referer header (the site's, see Site.referer; empty = none)."""
    # The download list goes to aria2c on stdin, with the cookie as a per-download option, so the
    # session ID is neither in the command line (visible to other processes) nor in a file on disk.
    lines = []
    queued = []
    for mirrors, dest, sizes in tasks:
        sizes = sizes or {}  # None/{}: no size reported for any mirror
        # A file from an earlier pass was checked against its own URL then; a leftover from an
        # earlier run is held to the reported size.
        if attempt >= len(mirrors) or is_valid_file(dest, max(sizes.values(), default=0) if attempt == 0 else 0):
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        for leftover in (dest, Path(f"{dest}.aria2")):
            leftover.unlink(missing_ok=True)
        lines += [mirrors[attempt], f"  dir={dest.parent}", f"  out={dest.name}"]
        if cookie:
            lines.append(f"  header=Cookie: {cookie}")
        queued.append((dest, sizes.get(mirrors[attempt], 0)))
    if not lines:
        return set()
    cmd = [
        aria2, "--input-file=-", f"--max-concurrent-downloads={jobs}",
        f"--max-connection-per-server={conns}", f"--split={conns}", "--min-split-size=1M",
        "--continue=true", "--allow-overwrite=true", "--auto-file-renaming=false",
        "--file-allocation=none", "--max-tries=5", "--retry-wait=2", "--timeout=30",
        "--connect-timeout=15", "--quiet=true", f"--user-agent={UA}",
        *([f"--referer={referer}"] if referer else []), "--header=Accept: */*",
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        # Wait in short steps instead of one blocking communicate(): on Windows Ctrl+C cannot
        # interrupt a blocking wait, so STOP would only be set once aria2c exits, while ffmpeg jobs
        # keep running and starting meanwhile.
        data = ("\n".join(lines) + "\n").encode("utf-8")
        while True:
            try:
                proc.communicate(data, timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                data = None  # already handed over: later calls only wait for aria2c to exit
    except KeyboardInterrupt:
        STOP.set()
        proc.terminate()
        raise
    truncated = {dest for dest, expected in queued if is_truncated(dest, expected)}
    for dest, expected in queued:
        if not is_valid_file(dest, expected):
            dest.unlink(missing_ok=True)
    return truncated


def download_requests(session: requests.Session, tasks, jobs: int) -> set[Path]:
    """Download with plain requests. Returns the files that came down truncated at least once."""
    truncated: set[Path] = set()

    def one(task):
        mirrors, dest, sizes = task
        sizes = sizes or {}  # None/{}: no size reported for any mirror
        if STOP.is_set() or is_valid_file(dest, max(sizes.values(), default=0)):
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        for url in mirrors:
            expected = sizes.get(url, 0)  # e.g. the site's watermarked copy has no reported size
            for _ in range(3):
                if STOP.is_set():
                    break
                try:
                    with session.get(url, stream=True, timeout=30) as r:
                        r.raise_for_status()
                        with open(dest, "wb") as fh:
                            for chunk in r.iter_content(1 << 16):
                                if STOP.is_set():
                                    break
                                fh.write(chunk)
                    if STOP.is_set():
                        break  # Ctrl+C mid-download: the partial file is deleted below
                    if is_valid_file(dest, expected):
                        return
                    if not is_truncated(dest, expected):
                        break  # got an error page instead of media: try the next URL
                    truncated.add(dest)  # cut short: delete it and retry
                    dest.unlink(missing_ok=True)
                except requests.RequestException:
                    STOP.wait(1)
        dest.unlink(missing_ok=True)

    pool = ThreadPoolExecutor(jobs)
    try:
        for fut in [pool.submit(one, task) for task in tasks]:
            wait_result(fut)
    except KeyboardInterrupt:
        STOP.set()
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=STOP.is_set())
    return truncated
