"""Instagram: for now only its links are recognised, so they fail with "not supported yet" instead of
being read as a TikTok username. Downloading reels and posts comes in later issues (#32, #33)."""
from __future__ import annotations

import re
from typing import Optional

from ..core.console import t
from ..core.errors import ScrapeError
from ..core.site import Entry, Site

# instagram.com with any subdomain (www., m.): profiles, /p/, /reel/, /reels/, /tv/, with or without a query.
# Without a scheme something must follow the host: a bare "a.instagram.com" is a valid TikTok username.
_HOST = r"(?:[\w-]+\.)*instagram\.com"
INSTAGRAM_LINK_RE = re.compile(rf"^(?:https?://{_HOST}(?:[/?#]|$)|{_HOST}[/?#])", re.I)


class InstagramSite(Site):
    """Only matches() and parse() so far: parse() always fails, so the class is never constructed and
    the other members of the interface stay abstract."""

    name = "instagram"
    label = "Instagram"

    @staticmethod
    def matches(url: str) -> bool:
        return bool(INSTAGRAM_LINK_RE.match(url.strip()))

    @staticmethod
    def parse(target: str) -> tuple[str, Optional[Entry]]:
        raise ScrapeError(t(f"Instagram is not supported yet: {target.strip()}",
                            f"chưa hỗ trợ tải từ Instagram: {target.strip()}"))
