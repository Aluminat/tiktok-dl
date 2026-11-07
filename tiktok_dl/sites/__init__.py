"""One module per website (TikTok for now). The core reaches them only through core.site.Site."""
from __future__ import annotations

from .tiktok import TikTokSite

SITES = (TikTokSite,)


def find_site(target: str):
    """The site a link belongs to; the first site (TikTok) for anything else, e.g. a bare username."""
    for site in SITES:
        if site.matches(target):
            return site
    return SITES[0]
