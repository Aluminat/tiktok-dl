"""Errors shared by the core and the site modules."""
from __future__ import annotations


class ScrapeError(Exception):
    """A post or profile could not be read. `code` is the site's own error code (0 = none)."""

    def __init__(self, message: str, code: int = 0):
        super().__init__(message)
        self.code = code


class RateLimited(ScrapeError):
    """The site answered too many page reads in a row with a rate limit (HTTP 429/403): stop the whole run.

    `site` is the site's name for messages (e.g. "TikTok") and `codes` the HTTP codes it rate-limits
    with (e.g. "429/403"); the page reader sets both."""

    def __init__(self, message: str, code: int = 0, site: str = "", codes: str = ""):
        super().__init__(message, code)
        self.site = site
        self.codes = codes


class Challenged(ScrapeError):
    """The site answered a page read with its bot-protection challenge page instead of the page."""


class ChallengeStop(RateLimited):
    """Too many page reads in a row got the challenge page: stop the whole run like RateLimited."""


class SiteStop(RateLimited):
    """The site answered a page read in a way that ends the run at once (Verdict.STOP), e.g. it wants
    the user to clear a check in a browser, or the login expired. The message says what to do."""
