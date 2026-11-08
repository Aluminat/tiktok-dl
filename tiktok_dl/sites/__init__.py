"""One module per website. The core reaches them only through core.site.Site.

Instagram downloads single post links (reels, videos, photos, carousels); Facebook only recognises its
links so far (they fail with "not supported yet")."""
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
    ScrapeError when the link belongs to another site than `name`, or when `name` is not TikTok and
    the target is a link of no known site."""
    for site in SITES:
        if site.matches(target):
            if name and site.name != name:
                label, text = BY_NAME[name].label, target.strip()
                raise ScrapeError(t(f"this link is for {site.label}, not {label} (--site {name}): {text}",
                                    f"link này của {site.label}, không phải {label} (--site {name}): {text}"))
            return site
    if not name:
        return SITES[0]
    site, text = BY_NAME[name], target.strip()
    # A link no site claims (another website, a look-alike host) is not a username of the --site site.
    # TikTok already rejects these in its own username check, with its existing message.
    if site is not SITES[0] and ("://" in text or "/" in text):
        raise ScrapeError(t(f"not a link or username for {site.label} (--site {name}): {text}",
                            f"không phải link hay username {site.label} (--site {name}): {text}"))
    return site
