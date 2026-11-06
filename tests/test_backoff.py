"""Backoff on HTTP 429/403 page reads, with a fake clock (no real sleeping, no network)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

PAGE = ('<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">'
        + json.dumps({"__DEFAULT_SCOPE__": {"webapp.app-context": {"user": {"uniqueId": "me"}}}})
        + "</script>")


class FakeResponse:
    def __init__(self, status_code: int, text: str = "", headers: dict | None = None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}


@pytest.fixture
def clock(tdl, monkeypatch):
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
    monkeypatch.setattr(tdl.time, "monotonic", c.monotonic)
    monkeypatch.setattr(tdl.time, "sleep", c.sleep)
    return c


@pytest.fixture
def logged(tdl, monkeypatch):
    lines: list[str] = []
    monkeypatch.setattr(tdl, "log", lambda msg="", err=False: lines.append(msg))
    return lines


def client(tdl, responses, wait=0.0):
    """A TikTok client whose page reads return `responses` in order; records request times."""
    tt = tdl.TikTok(wait=wait)
    queue = list(responses)
    tt.requested_at = []

    def get(url, timeout=None):
        tt.requested_at.append(tdl.time.monotonic())
        return queue.pop(0)

    tt.s.get = get
    return tt


def test_backoff_schedule_doubles_until_success(tdl, clock, logged):
    tt = client(tdl, [FakeResponse(429), FakeResponse(429), FakeResponse(429), FakeResponse(200, PAGE)])
    assert tt.whoami() == {"uniqueId": "me"}
    assert clock.sleeps == [5.0, 10.0, 20.0]
    start = tt.requested_at[0]
    assert [round(t - start, 3) for t in tt.requested_at] == [0.0, 5.0, 15.0, 35.0]


def test_retry_after_seconds_is_honoured(tdl, clock, logged):
    tt = client(tdl, [FakeResponse(429, headers={"Retry-After": "42"}), FakeResponse(200, PAGE)])
    assert tt.whoami() == {"uniqueId": "me"}
    assert clock.sleeps == [42.0]


def test_retry_after_is_capped(tdl, clock, logged):
    tt = client(tdl, [FakeResponse(429, headers={"Retry-After": "86400"}), FakeResponse(200, PAGE)])
    tt.whoami()
    assert clock.sleeps == [tdl.BACKOFF_MAX]


def test_403_backs_off_too(tdl, clock, logged):
    tt = client(tdl, [FakeResponse(403), FakeResponse(200, PAGE)])
    assert tt.whoami() == {"uniqueId": "me"}
    assert clock.sleeps == [5.0]


def test_gives_up_with_rate_limit_reason_and_warns_once(tdl, clock, logged):
    tt = client(tdl, [FakeResponse(429)] * 4)
    with pytest.raises(tdl.ScrapeError, match="HTTP 429"):
        tt.whoami()
    assert clock.sleeps == [5.0, 10.0, 20.0]
    assert len(logged) == 1
    assert "429" in logged[0] and "--wait" in logged[0]

    # Later rate-limited requests on the same client back off again but do not warn again.
    tt2_responses = [FakeResponse(429), FakeResponse(200, PAGE)]
    tt.s.get = lambda url, timeout=None: tt2_responses.pop(0)
    assert tt.whoami() == {"uniqueId": "me"}
    assert len(logged) == 1


def test_pause_is_shared_by_later_requests(tdl, clock, logged):
    """A 429 seen by one request also delays the next request, e.g. from another thread."""
    tt = client(tdl, [FakeResponse(429, headers={"Retry-After": "30"})])
    tt._back_off(FakeResponse(429, headers={"Retry-After": "30"}), 0)
    clock.sleeps.clear()
    tt._throttle()
    assert clock.sleeps == [30.0]


def test_normal_requests_keep_the_wait_gap(tdl, clock, logged):
    tt = client(tdl, [FakeResponse(200, PAGE), FakeResponse(200, PAGE)], wait=0.3)
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
def test_retry_after_seconds(tdl, value, expected):
    now = datetime(2026, 11, 7, 12, 0, 0, tzinfo=timezone.utc)
    assert tdl.retry_after_seconds(value, now) == expected


def test_backoff_delay(tdl):
    assert [tdl.backoff_delay(a) for a in range(6)] == [5.0, 10.0, 20.0, 40.0, 80.0, 120.0]
    assert tdl.backoff_delay(3, retry_after=1.0) == 1.0
    assert tdl.backoff_delay(0, retry_after=1e9) == tdl.BACKOFF_MAX
