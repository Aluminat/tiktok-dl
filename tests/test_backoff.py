"""Backoff on HTTP 429/403 page reads, with a fake clock (no real sleeping, no network)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from tiktok_dl.core import console, pacing
from tiktok_dl.core.errors import RateLimited, ScrapeError
from tiktok_dl.sites import tiktok

PAGE = ('<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">'
        + json.dumps({"__DEFAULT_SCOPE__": {"webapp.app-context": {"user": {"uniqueId": "me"}}}})
        + "</script>")


class FakeResponse:
    def __init__(self, status_code: int, text: str = "", headers: dict | None = None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}


@pytest.fixture
def clock(monkeypatch):
    """Replace time.monotonic/time.sleep: sleeping only moves the fake clock forward."""
    class Clock:
        now = 1000.0
        sleeps: list[float] = []

        def monotonic(self):
            return self.now

        def sleep(self, seconds):
            self.sleeps.append(round(seconds, 3))
            self.now += seconds

    c = Clock()
    c.sleeps = []
    monkeypatch.setattr(pacing.time, "monotonic", c.monotonic)
    monkeypatch.setattr(pacing.time, "sleep", c.sleep)
    return c


@pytest.fixture
def logged(monkeypatch):
    lines: list[str] = []
    monkeypatch.setattr(console, "log", lambda msg="", err=False: lines.append(msg))
    return lines


def client(responses, wait=0.0):
    """A TikTok client whose page reads return `responses` in order; records request times."""
    tt = tiktok.TikTok(wait=wait)
    queue = list(responses)
    tt.requested_at = []

    def get(url, timeout=None):
        tt.requested_at.append(pacing.time.monotonic())
        return queue.pop(0)

    tt.s.get = get
    return tt


def gaps(tt):
    """Seconds between consecutive page requests."""
    return [round(b - a, 3) for a, b in zip(tt.requested_at, tt.requested_at[1:])]


def test_backoff_schedule_doubles_until_success(clock, logged):
    tt = client([FakeResponse(429), FakeResponse(429), FakeResponse(429), FakeResponse(200, PAGE)])
    assert tt.whoami() == {"uniqueId": "me"}
    assert gaps(tt) == [5.0, 10.0, 20.0]
    assert round(sum(clock.sleeps), 3) == 35.0
    start = tt.requested_at[0]
    assert [round(t - start, 3) for t in tt.requested_at] == [0.0, 5.0, 15.0, 35.0]


def test_retry_after_seconds_is_honoured(clock, logged):
    tt = client([FakeResponse(429, headers={"Retry-After": "42"}), FakeResponse(200, PAGE)])
    assert tt.whoami() == {"uniqueId": "me"}
    assert gaps(tt) == [42.0]


def test_retry_after_is_capped(clock, logged):
    tt = client([FakeResponse(429, headers={"Retry-After": "86400"}), FakeResponse(200, PAGE)])
    tt.whoami()
    assert gaps(tt) == [pacing.BACKOFF_MAX]


def test_403_backs_off_too(clock, logged):
    tt = client([FakeResponse(403), FakeResponse(200, PAGE)])
    assert tt.whoami() == {"uniqueId": "me"}
    assert gaps(tt) == [5.0]


def test_gives_up_with_rate_limit_reason_and_warns_once(clock, logged):
    tt = client([FakeResponse(429)] * 4)
    with pytest.raises(ScrapeError, match="HTTP 429"):
        tt.whoami()
    assert gaps(tt) == [5.0, 10.0, 20.0]
    assert len(logged) == 1
    assert "429" in logged[0] and "--wait" in logged[0]
    # The last attempt is not retried, so it leaves no pause behind for the next read.
    assert clock.now == tt.requested_at[-1]

    # Later rate-limited requests on the same client back off again but do not warn again.
    tt2_responses = [FakeResponse(429), FakeResponse(200, PAGE)]
    tt.s.get = lambda url, timeout=None: tt2_responses.pop(0)
    clock.sleeps.clear()
    assert tt.whoami() == {"uniqueId": "me"}
    assert round(sum(clock.sleeps), 3) == 5.0
    assert len(logged) == 1


def test_pause_is_shared_by_later_requests(clock, logged):
    """A 429 seen by one request also delays the next request, e.g. from another thread."""
    tt = client([FakeResponse(429, headers={"Retry-After": "30"})])
    tt._back_off(FakeResponse(429, headers={"Retry-After": "30"}), 0)
    clock.sleeps.clear()
    tt._throttle()
    assert round(sum(clock.sleeps), 3) == 30.0


def test_run_stops_after_too_many_rate_limited_answers_in_a_row(clock, logged):
    """A blocked IP must fail fast, not retry every post for hours."""
    tt = client([FakeResponse(429)] * pacing.RATE_LIMIT_STREAK)
    with pytest.raises(ScrapeError):   # first post: 4 attempts, then a normal failure
        tt.whoami()
    with pytest.raises(RateLimited):   # the streak reaches the limit on the next post
        tt.whoami()
    assert len(tt.requested_at) == pacing.RATE_LIMIT_STREAK

    # Every later read gives up at once: no request, no sleep.
    clock.sleeps.clear()
    with pytest.raises(RateLimited):
        tt.whoami()
    assert len(tt.requested_at) == pacing.RATE_LIMIT_STREAK
    assert clock.sleeps == []


def test_any_other_answer_resets_the_streak(clock, logged):
    n = pacing.RATE_LIMIT_STREAK - 1
    tt = client([FakeResponse(429)] * 3 + [FakeResponse(200, PAGE)] + [FakeResponse(429)] * n
                + [FakeResponse(200, PAGE)])
    tt._streak = n - 3   # earlier rate-limited answers from other posts
    assert tt.whoami() == {"uniqueId": "me"}
    with pytest.raises(ScrapeError) as e:   # 4 x 429: fails this post only
        tt.whoami()
    assert not isinstance(e.value, RateLimited)
    assert tt.whoami() == {"uniqueId": "me"}   # n-4 x 429, then a success
    assert tt._streak == 0


def test_stop_makes_waiting_reads_give_up(clock, logged):
    tt = client([])
    tt._pause_until = clock.now + 100
    tt.stop()
    with pytest.raises(RateLimited):
        tt._throttle()
    assert clock.sleeps == []


def test_save_profile_does_not_swallow_rate_limited(tmp_path, monkeypatch):
    tt = tiktok.TikTok()

    def user(username):
        raise RateLimited("blocked")

    monkeypatch.setattr(tt, "user", user)
    with pytest.raises(RateLimited):
        tiktok.save_profile(tt, "someone", tmp_path)


def test_normal_requests_keep_the_wait_gap(clock, logged):
    tt = client([FakeResponse(200, PAGE), FakeResponse(200, PAGE)], wait=0.3)
    tt.whoami()
    tt.whoami()
    assert clock.sleeps == [0.3]
    assert logged == []


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        ("7", 7.0),
        (" 2.5 ", 2.5),
        ("-3", 0.0),
        ("not a date", None),
        ("Sat, 07 Nov 2026 12:01:30 GMT", 90.0),
        ("Sat, 07 Nov 2026 11:59:00 GMT", 0.0),
    ],
)
def test_retry_after_seconds(value, expected):
    now = datetime(2026, 11, 7, 12, 0, 0, tzinfo=timezone.utc)
    assert pacing.retry_after_seconds(value, now) == expected


def test_backoff_delay():
    assert [pacing.backoff_delay(a) for a in range(6)] == [5.0, 10.0, 20.0, 40.0, 80.0, 120.0]
    assert pacing.backoff_delay(3, retry_after=1.0) == 1.0
    assert pacing.backoff_delay(0, retry_after=1e9) == pacing.BACKOFF_MAX
