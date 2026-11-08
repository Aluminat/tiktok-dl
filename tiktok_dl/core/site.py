"""The site interface: the only way the core reaches a site (TikTok, Instagram, later Facebook).

The core (download, mux, progress DB, CSV, failed.txt, backoff, resume) is the same for every site.
A site module subclasses Site and Post and supplies the URLs, page parsing and rules of its website.
The core never contains a site's URLs, JSON keys or bot-protection markers.
"""
from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from .pacing import PageReader


class Verdict(enum.Enum):
    """What one answer from a site means for the run (Site.classify)."""
    OK = "ok"                      # an ordinary answer: no backoff, no stop, no skip
    RATE_LIMITED = "rate_limited"  # back off and retry; RATE_LIMIT_STREAK in a row stop the run
    BLOCKED = "blocked"            # bot protection: fail this read at once; CHALLENGE_STREAK in a row stop the run
    PERMANENT = "permanent"        # the post can never be read: marked "skipped", not tried on re-runs
    STOP = "stop"                  # nothing more can be read (a check to clear in a browser, login expired,
                                   # account limited): stop the run at once with the site's message


@dataclass(frozen=True)
class Entry:
    """A post known before its page is read (from a link or the profile's post list)."""
    id: str
    link: str      # shown and recorded when the post fails before its page is read


@dataclass(frozen=True)
class Profile:
    """Profile info for the header line. Values are the site's own (not converted)."""
    nickname: Any
    verified: Any
    followers: Any
    likes: Any
    posts: Any


@dataclass(frozen=True)
class Subtitle:
    url: Optional[str]
    lang: str          # short language code, e.g. "en"
    machine: bool      # machine translation (skipped with --subs original)


@dataclass
class Item:
    """One file of a post that is saved as its original files (Media.items): a photo, or a video that is
    muxed with the post's tags like a single video post."""
    urls: list[str]                 # mirrors of this one file
    video: bool = False
    cover: Optional[str] = None     # a video's cover image (embedded unless --no-cover)


@dataclass
class Media:
    """What a post offers for download. Mirrors are tried one per attempt (they need not be copies of
    one file).

    `permanent` is the one way besides Verdict.PERMANENT (Site.classify) that a post is marked
    "skipped": the post was read fine, but the site gives no download link and no re-run can change
    that (e.g. TikTok ad / Shop videos). See docs/adr/0002."""
    video: list[str] = field(default_factory=list)
    sizes: dict[str, int] = field(default_factory=dict)   # reported size per video URL (missing = unknown)
    cover: Optional[str] = None
    subtitles: list[Subtitle] = field(default_factory=list)
    images: list[list[str]] = field(default_factory=list)  # photo posts: mirrors per image
    audio: Optional[str] = None
    # Posts saved item by item as their original files, in order (e.g. a photo, or a carousel of photos and
    # videos): <base>.<ext> for one item, <base>_01.<ext>, <base>_02.<ext>, ... for several. The post is
    # done only when every item is on disk. See docs/adr/0004.
    items: list[Item] = field(default_factory=list)
    unavailable: Optional[str] = None   # why a video post has no download link (message)
    permanent: bool = False             # ... and no re-run can fix it


class Post(ABC):
    """A site-neutral post (Site.read_post). `raw` is the site's own data, saved as "raw" in
    metadata/<id>.json. A site subclass reads the fields from `raw` only when they are used, so odd
    page data fails at the same step as before."""

    def __init__(self, raw: dict):
        self.raw = raw

    @property
    @abstractmethod
    def id(self) -> str: ...

    @property
    @abstractmethod
    def link(self) -> str: ...

    @property
    @abstractmethod
    def author(self) -> str: ...

    @property
    @abstractmethod
    def created(self) -> datetime: ...

    @property
    @abstractmethod
    def caption(self) -> Any:
        """Used for file names (sanitize()); not converted."""

    @property
    @abstractmethod
    def counts(self) -> dict[str, Optional[int]]: ...

    @abstractmethod
    def media(self) -> Media:
        """Read only when files are downloaded (not with --metadata-only)."""


class Site(ABC):
    """One website. Constructed once per profile run: Site(profile, session(opts), wait, proxy).

    Besides the members agreed for the interface (matches, parse, read_post, list_posts, csv_row,
    mux_tags, classify), a site gives what today's output needs: save_profile() for profile.json and
    the header line, link() for old failed.txt rows, client for page reads and download cookies,
    referer for aria2c, and name, label and folder() for --site, messages and the output folder.
    For a second site (docs/adr/0003): session() picks its login cookie, defaults its unmeasured
    pacing, and owner() finds the profile of a post link that does not name it.
    """

    name: str = ""             # the --site value, e.g. "tiktok"
    label: str = ""            # the site's name in messages, e.g. "TikTok"
    referer: str = ""          # aria2c --referer for downloads
    client: PageReader         # page reads; also the requests session (`client.s`) and cookies for downloads
    # --wait, --workers, --connections and --slideshow / --no-slideshow when the user does not give them
    # (core.flow.run_settings).
    defaults: dict = {"wait": 0.3, "workers": 8, "connections": 4, "slideshow": True}

    @classmethod
    def session(cls, opts) -> Optional[str]:
        """The login cookie this site uses from the options (TikTok: --session-id / TIKTOK_SESSIONID).
        A class method: it is read before the site is constructed."""
        return getattr(opts, "session_id", None)   # getattr: tests may build opts without it

    def owner(self, entry: Entry) -> str:
        """The profile of a post whose link does not name it (parse() gave None as the profile). Called
        once, before the output folder is made. Raises ScrapeError."""
        raise NotImplementedError(f"{self.label} links always name the profile")

    @classmethod
    def folder(cls, profile: str) -> Path:
        """Where a profile's files go, relative to --output: <site name>/<profile>. A class method: the
        folder is made before the site is constructed."""
        return Path(cls.name, profile)

    @staticmethod
    @abstractmethod
    def matches(url: str) -> bool:
        """Is this link for this site?"""

    @staticmethod
    @abstractmethod
    def parse(target: str) -> tuple[Optional[str], Optional[Entry]]:
        """A post link or a profile -> (profile name, the single post or None). Raises ScrapeError.
        The profile is None only for a post link that does not name it (see owner())."""

    @abstractmethod
    def save_profile(self, out_dir: Path) -> Optional[Profile]:
        """Save the profile's info files; None when there is nothing to show."""

    @abstractmethod
    def list_posts(self, limit: Optional[int], work: Path) -> list[Entry]:
        """Every post of the profile, newest first. A site that cannot list raises ScrapeError (with its
        message)."""

    @abstractmethod
    def read_post(self, post_id: str) -> Post:
        """Read one post page (in a metadata worker thread)."""

    @abstractmethod
    def link(self, post_id: str) -> str:
        """Link of a post known only by its id (progress rows written before links were stored)."""

    @abstractmethod
    def csv_row(self, post: Post) -> dict:
        """The videos.csv row (also saved as "metadata" in metadata/<id>.json)."""

    @abstractmethod
    def mux_tags(self, post: Post) -> dict:
        """Tags written into the MP4."""

    @abstractmethod
    def classify(self, answer) -> Verdict:
        """What one answer means: the HTTP response to a page read, or the ScrapeError a post read
        failed with."""
