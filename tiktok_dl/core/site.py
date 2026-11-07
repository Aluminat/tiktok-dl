"""The site interface: the only way the core reaches a site (TikTok, later Instagram, Facebook)."""
from __future__ import annotations

import enum


class Verdict(enum.Enum):
    """What one answer from a site means for the run (Site.classify)."""
    OK = "ok"                      # an ordinary answer: no backoff, no stop, no skip
    RATE_LIMITED = "rate_limited"  # back off and retry; RATE_LIMIT_STREAK in a row stop the run
    BLOCKED = "blocked"            # bot protection: fail this read at once; CHALLENGE_STREAK in a row stop the run
    PERMANENT = "permanent"        # the post can never be read: marked "skipped", not tried on re-runs
