"""Facebook: single reel links (issue #34).

Everything specific to Facebook lives here: its links, how its answers are read and what they mean.
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urljoin

import requests

from ..core.console import t
from ..core.errors import ScrapeError
from ..core.site import Entry, Site
from ..core.util import UA

# facebook.com with any subdomain (www., m., web.) and fb.watch short links.
# Without a scheme something must follow the host: a bare "fb.watch" is a valid TikTok username.
_HOST = r"(?:[\w-]+\.)*(?:facebook\.com|fb\.watch)"
FACEBOOK_LINK_RE = re.compile(rf"^(?:https?://{_HOST}(?:[/?#]|$)|{_HOST}[/?#])", re.I)
_FB = r"^(?:https?://)?(?:[\w-]+\.)*facebook\.com"
# The links of a reel: /reel/<id> and /watch/?v=<id>.
REEL_LINK_RES = (re.compile(rf"{_FB}/reel/(\d+)(?:[/?#]|$)", re.I),
                 re.compile(rf"{_FB}/watch/?\?(?:[^#]*&)?v=(\d+)(?:[&#]|$)", re.I))
# Where a short link may lead besides those: a page's video link.
VIDEOS_LINK_RE = re.compile(rf"{_FB}/[^/?#]+/videos/(?:[^/?#]+/)?(\d+)(?:[/?#]|$)", re.I)
SHORT_LINK_RE = re.compile(r"^(?:https?://)?(?:www\.)?fb\.watch/[\w-]+/?(?:[?#]|$)", re.I)

BASE = "https://www.facebook.com"


def reel_link(video_id: str) -> str:
    return f"{BASE}/reel/{video_id}/"


def _reel_id(url: str, *, short_link_target: bool = False) -> Optional[str]:
    for pattern in REEL_LINK_RES + ((VIDEOS_LINK_RE,) if short_link_target else ()):
        m = pattern.match(url)
        if m:
            return m.group(1)
    return None


def resolve_short_link(text: str) -> str:
    """The reel id an fb.watch link leads to. Follows the redirects one by one (no cookies: a short link
    needs none), at most 5."""
    url = text if text.lower().startswith("http") else f"https://{text}"
    try:
        for _ in range(5):
            r = requests.get(url, headers={"User-Agent": UA}, allow_redirects=False, timeout=20)
            location = r.headers.get("Location")
            if not location:
                break
            url = urljoin(url, location)
            found = _reel_id(url, short_link_target=True)
            if found:
                return found
    except requests.RequestException as e:
        raise ScrapeError(t(f"could not open short link {text} ({e})", f"không mở được link rút gọn {text} ({e})"))
    raise ScrapeError(t(f"short link does not lead to a Facebook reel: {text}",
                        f"link rút gọn không dẫn tới reel Facebook: {text}"))


class FacebookSite(Site):
    """Facebook behind the core's site interface: single reel links."""

    name = "facebook"
    label = "Facebook"

    @staticmethod
    def matches(url: str) -> bool:
        return bool(FACEBOOK_LINK_RE.match(url.strip()))

    @staticmethod
    def parse(target: str) -> tuple[Optional[str], Optional[Entry]]:
        """A reel link -> (None: the link does not name the page, the reel). Only a short link is opened."""
        text = target.strip()
        video_id = resolve_short_link(text) if SHORT_LINK_RE.match(text) else _reel_id(text)
        if not video_id:
            raise ScrapeError(t("only Facebook reel links are supported (facebook.com/reel/<id>, fb.watch/..., "
                                f"facebook.com/watch/?v=<id>): {text}",
                                "chỉ hỗ trợ link reel Facebook (facebook.com/reel/<id>, fb.watch/..., "
                                f"facebook.com/watch/?v=<id>): {text}"))
        return None, Entry(video_id, reel_link(video_id))

    def link(self, post_id: str) -> str:
        return reel_link(post_id)
