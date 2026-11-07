"""One module per website. The core reaches them only through core.site.Site.

Instagram and Facebook only recognise their links so far (they fail with "not supported yet")."""
from __future__ import annotations

from .facebook import FacebookSite
from .instagram import InstagramSite
from .tiktok import TikTokSite

SITES = (TikTokSite, InstagramSite, FacebookSite)


def find_site(target: str):
    """The site a link belongs to; the first site (TikTok) for anything else, e.g. a bare username."""
    for site in SITES:
        if site.matches(target):
            return site
    return SITES[0]
