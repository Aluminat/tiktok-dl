"""The site interface: the only way the core reaches a site (TikTok, later Instagram, Facebook).

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
    """One website. Constructed once per profile run: Site(profile, session_id, wait, proxy).

    Besides the members agreed for the interface (matches, parse, read_post, list_posts, csv_row,
    mux_tags, classify), a site gives what today's output needs: save_profile() for profile.json and
    the header line, link() for old failed.txt rows, client for page reads and download cookies, and
    referer for aria2c.
    """

    referer: str = ""          # aria2c --referer for downloads
    client: PageReader         # page reads; also the requests session (`client.s`) and cookies for downloads

    @staticmethod
    @abstractmethod
    def matches(url: str) -> bool:
        """Is this link for this site?"""

    @staticmethod
    @abstractmethod
    def parse(target: str) -> tuple[str, Optional[Entry]]:
        """A post link or a profile -> (profile name, the single post or None). Raises ScrapeError."""

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
