"""Facebook: for now only its links are recognised, so they fail with "not supported yet" instead of
being read as a TikTok username. Downloading reels comes in a later issue (#34)."""
from __future__ import annotations

import re
from typing import Optional

from ..core.console import t
from ..core.errors import ScrapeError
from ..core.site import Entry, Site

# facebook.com with any subdomain (www., m., web.) and fb.watch short links. Short links are only
# recognised, not opened: nothing is read from the network for a site that is not supported yet.
FACEBOOK_LINK_RE = re.compile(r"^(?:https?://)?(?:[\w-]+\.)*(?:facebook\.com|fb\.watch)(?:[/?#]|$)", re.I)


class FacebookSite(Site):
    """Only matches() and parse() so far: parse() always fails, so the class is never constructed and
    the other members of the interface stay abstract."""

    name = "facebook"
    label = "Facebook"

    @staticmethod
    def matches(url: str) -> bool:
        return bool(FACEBOOK_LINK_RE.match(url.strip()))

    @staticmethod
    def parse(target: str) -> tuple[str, Optional[Entry]]:
        raise ScrapeError(t(f"Facebook is not supported yet: {target.strip()}",
                            f"chưa hỗ trợ tải từ Facebook: {target.strip()}"))
