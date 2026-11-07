"""Errors shared by the core and the site modules."""
from __future__ import annotations


class ScrapeError(Exception):
    """A post or profile could not be read. `code` is the site's own error code (0 = none)."""

    def __init__(self, message: str, code: int = 0):
        super().__init__(message)
        self.code = code


class RateLimited(ScrapeError):
    """The site answered too many page reads in a row with a rate limit (HTTP 429/403): stop the whole run."""


class Challenged(ScrapeError):
    """The site answered a page read with its bot-protection challenge page instead of the page."""


class ChallengeStop(RateLimited):
    """Too many page reads in a row got the challenge page: stop the whole run like RateLimited."""
