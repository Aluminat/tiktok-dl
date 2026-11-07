"""One module per website. The core reaches them only through core.site.Site.

Instagram and Facebook only recognise their links so far (they fail with "not supported yet")."""
from __future__ import annotations

from typing import Optional

from ..core.console import t
from ..core.errors import ScrapeError
from .facebook import FacebookSite
from .instagram import InstagramSite
from .tiktok import TikTokSite

SITES = (TikTokSite, InstagramSite, FacebookSite)
BY_NAME = {site.name: site for site in SITES}   # the --site choices, in this order


def find_site(target: str, name: Optional[str] = None):
    """The site of a target: the one its link belongs to. Anything else (a bare username, @username)
    belongs to the site called `name` (--site), or to the first site (TikTok) without it. Raises
    ScrapeError when the link belongs to another site than `name`."""
    for site in SITES:
        if site.matches(target):
            if name and site.name != name:
                label, text = BY_NAME[name].label, target.strip()
                raise ScrapeError(t(f"this link is for {site.label}, not {label} (--site {name}): {text}",
                                    f"link này của {site.label}, không phải {label} (--site {name}): {text}"))
            return site
    return BY_NAME[name] if name else SITES[0]
