"""Page reads of one run: the --wait gap, the backoff pause after rate-limited answers, the streak
stops and the stop at once (Verdict.STOP). A site's web client subclasses PageReader; its classify()
says what each answer means.
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
from .errors import Challenged, ChallengeStop, RateLimited, ScrapeError, SiteStop
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
    RATE_LIMITED answers, the streak stops and the stop at once after a STOP answer. A site's web
    client subclasses it, sets up self.s (headers, proxy, cookies), sets `label` and `limit_codes`,
    and supplies classify() and blocked_message() (and stop_message() if it ever answers STOP)."""

    label: str = ""          # the site's name in messages, e.g. "TikTok"
    limit_codes: str = ""    # the HTTP codes it rate-limits with, for messages, e.g. "429/403"

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
        self._stop_reason: Optional[str] = None   # stopped by a STOP answer: the site's message

    @abstractmethod
    def classify(self, response) -> Verdict:
        """What one answer means (the site's Site.classify)."""

    @abstractmethod
    def blocked_message(self) -> str:
        """Why a BLOCKED answer failed the read (the site's own text)."""

    def stop_message(self, response) -> str:
        """Why a STOP answer ended the run and what the user can do (the site's own text)."""
        return t(f"{self.label} refused the request (HTTP {response.status_code}), so the run stopped",
                 f"{self.label} từ chối request (HTTP {response.status_code}) nên tool đã dừng")

    def stop(self) -> None:
        """Make queued and waiting page reads give up at once (run aborted or interrupted)."""
        self._stopped = True

    def _check_stopped(self) -> None:
        if self._stopped:
            site, codes = self.label, self.limit_codes
            if self._challenge_stop:
                raise ChallengeStop(self.blocked_message(), site=site, codes=codes)
            if self._stop_reason:
                raise SiteStop(self._stop_reason, site=site, codes=codes)
            raise RateLimited(t(f"{site} keeps refusing requests (HTTP {codes}); wait a while, or use a "
                                "larger --wait",
                                f"{site} liên tục từ chối request (HTTP {codes}); hãy đợi một lúc hoặc "
                                "tăng --wait"), site=site, codes=codes)

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

    def _site_stopped(self, r: requests.Response) -> None:
        """A STOP answer: stop every page read of the run now, with the site's message, and raise."""
        message = self.stop_message(r)
        with self._backoff_lock:
            if not self._stopped:
                self._stopped, self._stop_reason = True, message
        self._check_stopped()

    def _back_off(self, r: requests.Response, attempt: int) -> None:
        """Pause every page read after a rate-limited response; warn the user the first time."""
        delay = backoff_delay(attempt, retry_after_seconds(r.headers.get("Retry-After")))
        with self._backoff_lock:
            self._pause_until = max(self._pause_until, time.monotonic() + delay)
            first, self._warned = not self._warned, True
        if first:
            code = r.status_code
            console.log(warn(t(f"{self.label} is limiting requests (HTTP {code}). Slowing down and retrying; "
                               "if it keeps happening, use a larger --wait or fewer --workers.",
                               f"{self.label} đang giới hạn request (HTTP {code}). Tool sẽ chậm lại rồi thử lại; "
                               "nếu vẫn bị, hãy tăng --wait hoặc giảm --workers.")))

    def fetch(self, url: str):
        """One request of a page read: the answer classify() and extract() look at. A site that reads its
        pages through a library (Facebook: yt-dlp) gives an answer with the same status_code and headers,
        and raises requests.RequestException for a network error, so it is retried like one."""
        return self.s.get(url, timeout=25)

    def read(self, url: str, extract) -> dict:
        """GET a page (fetch()) and return extract(response), with up to 4 attempts. extract() gives None
        when the page has no data; a ScrapeError it raises fails the read at once (no retry)."""
        last = t("no response", "không có phản hồi")
        attempts = 4
        for attempt in range(attempts):
            self._throttle()
            try:
                r = self.fetch(url)
                verdict = self.classify(r)
                if verdict is Verdict.STOP:
                    self._site_stopped(r)  # raises: the whole run stops now
                if verdict is Verdict.BLOCKED:
                    self._challenged()  # raises: retrying within seconds won't get past it
                if verdict is Verdict.RATE_LIMITED:
                    # No pause after the last attempt: nothing retries it.
                    self._rate_limited(r, attempt, retry=attempt + 1 < attempts)
                    last = t(f"HTTP {r.status_code}, {self.label} is limiting requests",
                             f"HTTP {r.status_code}, {self.label} đang giới hạn request")
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
