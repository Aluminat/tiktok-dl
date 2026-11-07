"""Page reads of one run: the --wait gap, the backoff pause after rate-limited answers and the streak
stops. A site's web client subclasses PageReader; its classify() says what each answer means.
"""
from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional

import requests

from . import console
from .console import t, warn
from .errors import Challenged, ChallengeStop, RateLimited, ScrapeError
from .site import Verdict
from .stop import STOP

# Page reads the site rate-limits (Verdict.RATE_LIMITED, e.g. HTTP 429 or 403) back off: wait as
# long as Retry-After says when the site sends it, otherwise BACKOFF_BASE seconds, doubled on each
# retry (5, 10, 20 s). Both are capped at BACKOFF_MAX, which limits one pause, not the run. The run
# stops after RATE_LIMIT_STREAK rate-limited answers in a row (any thread, no other answer
# between), so a blocked IP fails fast instead of retrying every post for hours.
BACKOFF_BASE = 5.0
BACKOFF_MAX = 120.0
RATE_LIMIT_STREAK = 8


def retry_after_seconds(value: Optional[str], now: Optional[datetime] = None) -> Optional[float]:
    """Seconds to wait from a Retry-After header (a number of seconds or an HTTP date), or None."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - (now or datetime.now(timezone.utc))).total_seconds())


def backoff_delay(attempt: int, retry_after: Optional[float] = None) -> float:
    """Seconds to pause page reads after the attempt-th (0-based) rate-limited response."""
    delay = retry_after if retry_after is not None else BACKOFF_BASE * 2 ** attempt
    return min(BACKOFF_MAX, delay)


# A BLOCKED answer (the site's bot-protection challenge page) does not clear within seconds, so it is
# not retried; CHALLENGE_STREAK such answers in a row stop the run.
CHALLENGE_STREAK = 3


class PageReader(ABC):
    """Page reads of one run, shared by all metadata threads: the --wait gap, the backoff pause after
    RATE_LIMITED answers and the streak stops. A site's web client subclasses it, sets up self.s
    (headers, proxy, cookies) and supplies classify() and blocked_message()."""

    def __init__(self, wait: float):
        self.s = requests.Session()
        self.wait = wait
        self._lock = threading.Lock()
        self._last = 0.0
        # Backoff after RATE_LIMITED answers, shared by all metadata threads.
        self._backoff_lock = threading.Lock()
        self._pause_until = 0.0
        self._warned = False
        self._streak = 0          # rate-limited answers in a row
        self._stopped = False     # set when the streak is too long or the run is stopping
        self._challenges = 0      # BLOCKED answers in a row
        self._challenge_stop = False  # stopped because of BLOCKED answers

    @abstractmethod
    def classify(self, response) -> Verdict:
        """What one answer means (the site's Site.classify)."""

    @abstractmethod
    def blocked_message(self) -> str:
        """Why a BLOCKED answer failed the read (the site's own text)."""

    def stop(self) -> None:
        """Make queued and waiting page reads give up at once (run aborted or interrupted)."""
        self._stopped = True

    def _check_stopped(self) -> None:
        if self._stopped:
            if self._challenge_stop:
                raise ChallengeStop(self.blocked_message())
            raise RateLimited(t("TikTok keeps refusing requests (HTTP 429/403); wait a while, or use a "
                                "larger --wait",
                                "TikTok liên tục từ chối request (HTTP 429/403); hãy đợi một lúc hoặc "
                                "tăng --wait"))

    def _throttle(self) -> None:
        with self._lock:
            # Keep the --wait gap between requests and honour any backoff pause. Check again
            # after sleeping, as another thread may have extended the pause meanwhile. Sleep in
            # short slices so a stopped run does not sit out a long pause.
            while True:
                self._check_stopped()
                delay = max(self._last + self.wait, self._pause_until) - time.monotonic()
                if delay <= 0:
                    break
                time.sleep(min(delay, 1.0))
            self._last = time.monotonic()

    def _rate_limited(self, r: requests.Response, attempt: int, retry: bool) -> None:
        """Count a RATE_LIMITED answer: stop the run after too many in a row, else pause if retrying."""
        with self._backoff_lock:
            self._streak += 1
            self._challenges = 0
            if self._streak >= RATE_LIMIT_STREAK:
                self._stopped = True
        self._check_stopped()
        if retry:
            self._back_off(r, attempt)

    def _challenged(self) -> None:
        """Count a BLOCKED answer and raise at once: no retry, no backoff pause."""
        with self._backoff_lock:
            self._streak = 0
            self._challenges += 1
            if self._challenges >= CHALLENGE_STREAK and not self._stopped:
                self._stopped = self._challenge_stop = True
        self._check_stopped()
        raise Challenged(self.blocked_message())

    def _back_off(self, r: requests.Response, attempt: int) -> None:
        """Pause every page read after a rate-limited response; warn the user the first time."""
        delay = backoff_delay(attempt, retry_after_seconds(r.headers.get("Retry-After")))
        with self._backoff_lock:
            self._pause_until = max(self._pause_until, time.monotonic() + delay)
            first, self._warned = not self._warned, True
        if first:
            code = r.status_code
            console.log(warn(t(f"TikTok is limiting requests (HTTP {code}). Slowing down and retrying; "
                               "if it keeps happening, use a larger --wait or fewer --workers.",
                               f"TikTok đang giới hạn request (HTTP {code}). Tool sẽ chậm lại rồi thử lại; "
                               "nếu vẫn bị, hãy tăng --wait hoặc giảm --workers.")))

    def read(self, url: str, extract) -> dict:
        """GET a page and return extract(response), with up to 4 attempts. extract() gives None when
        the page has no data."""
        last = t("no response", "không có phản hồi")
        attempts = 4
        for attempt in range(attempts):
            self._throttle()
            try:
                r = self.s.get(url, timeout=25)
                verdict = self.classify(r)
                if verdict is Verdict.BLOCKED:
                    self._challenged()  # raises: retrying within seconds won't get past it
                if verdict is Verdict.RATE_LIMITED:
                    # No pause after the last attempt: nothing retries it.
                    self._rate_limited(r, attempt, retry=attempt + 1 < attempts)
                    last = t(f"HTTP {r.status_code}, TikTok is limiting requests",
                             f"HTTP {r.status_code}, TikTok đang giới hạn request")
                    continue  # the backoff pause replaces the fixed retry delay below
                with self._backoff_lock:
                    self._streak = 0
                    self._challenges = 0
                data = extract(r)
                if data is not None:
                    return data
                last = t(f"HTTP {r.status_code}, page has no data", f"HTTP {r.status_code}, trang không có dữ liệu")
            except (requests.RequestException, ValueError, AttributeError) as e:
                last = str(e)
            if attempt + 1 >= attempts or STOP.wait(1 + 2 * attempt):   # no wait after the last attempt
                break
        raise ScrapeError(last)

    def cookie_header(self) -> str:
        return "; ".join(f"{c.name}={c.value}" for c in self.s.cookies)
