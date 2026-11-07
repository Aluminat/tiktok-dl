"""TikTok's bot-protection challenge page: detected, not retried, and it stops the run early."""
from __future__ import annotations

import argparse
import json

import pytest
import requests

# Shape of the real challenge page (~1.4 KB): "Please wait...", a _wafchallengeid element and a
# waf-aiso script, and no embedded page data.
CHALLENGE = (
    '<!DOCTYPE html><html><head><meta charset="utf-8"><title>Please wait...</title>'
    '<script src="https://sf16-website-login.neutral.ttwstatic.com/obj/tiktok_web_login_static/'
    'waf-aiso/aiso.js"></script></head><body><p>Please wait...</p>'
    '<div class="_wafchallengeid" id="cs" data-cs="abc123"></div></body></html>'
)
NO_DATA = "<!DOCTYPE html><html><head><title>TikTok</title></head><body></body></html>"


def scope_page(scope: str, data: dict) -> str:
    return ('<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">'
            + json.dumps({"__DEFAULT_SCOPE__": {scope: data}}) + "</script>")


PAGE = scope_page("webapp.app-context", {"user": {"uniqueId": "me"}})


class FakeResponse:
    def __init__(self, status_code: int, text: str = "", headers: dict | None = None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}


@pytest.fixture
def clock(tdl, monkeypatch):
    """Replace time.monotonic/time.sleep and the STOP wait between attempts: waiting only moves the
    fake clock forward."""
    class Clock:
        now = 1000.0

        def monotonic(self):
            return self.now

        def sleep(self, seconds):
            self.sleeps.append(round(seconds, 3))
            self.now += seconds

        def wait(self, seconds):   # STOP.wait(): no Ctrl+C in these tests, so it always times out
            self.sleep(seconds)
            return False

    c = Clock()
    c.sleeps = []
    monkeypatch.setattr(tdl.time, "monotonic", c.monotonic)
    monkeypatch.setattr(tdl.time, "sleep", c.sleep)
    monkeypatch.setattr(tdl.STOP, "wait", c.wait)
    return c


@pytest.fixture
def logged(tdl, monkeypatch):
    lines: list[str] = []
    monkeypatch.setattr(tdl, "log", lambda msg="", err=False: lines.append(msg))
    return lines


def client(tdl, responses):
    """A TikTok client whose page reads return `responses` in order and are counted."""
    tt = tdl.TikTok(wait=0)
    queue = list(responses)
    tt.requests = 0

    def get(url, timeout=None):
        tt.requests += 1
        return queue.pop(0)

    tt.s.get = get
    return tt


def test_detects_the_challenge_page(tdl):
    assert tdl.is_challenge_page(CHALLENGE)
    assert tdl.is_challenge_page('<div class="_wafchallengeid"></div>')
    assert not tdl.is_challenge_page(PAGE)
    assert not tdl.is_challenge_page(NO_DATA)
    assert not tdl.is_challenge_page("<html><body>Please wait...</body></html>")
    # A real page that happens to mention a marker still has its data: not a challenge.
    assert not tdl.is_challenge_page(PAGE + "<!-- waf-aiso -->")


@pytest.mark.parametrize("status", [200, 403, 429])
def test_challenge_raises_at_once_without_retry_or_backoff(tdl, clock, logged, status):
    tt = client(tdl, [FakeResponse(status, CHALLENGE)])
    with pytest.raises(tdl.Challenged) as e:
        tt.whoami()
    assert not isinstance(e.value, tdl.RateLimited)
    assert "bot protection" in str(e.value) and "--session-id" in str(e.value)
    assert tt.requests == 1
    assert clock.sleeps == []
    assert tt._pause_until == 0.0
    assert logged == []   # no "TikTok is limiting requests" warning


def test_page_without_data_keeps_retrying(tdl, clock, logged):
    tt = client(tdl, [FakeResponse(200, NO_DATA)] * 4)
    with pytest.raises(tdl.ScrapeError, match="page has no data") as e:
        tt.whoami()
    assert not isinstance(e.value, tdl.Challenged)
    assert tt.requests == 4
    assert clock.sleeps == [1, 3, 5]   # waits between attempts only, none after the last one


def test_page_without_data_then_success(tdl, clock, logged):
    tt = client(tdl, [FakeResponse(200, NO_DATA), FakeResponse(200, PAGE)])
    assert tt.whoami() == {"uniqueId": "me"}
    assert tt.requests == 2


def test_challenge_streak_stops_the_run(tdl, clock, logged):
    tt = client(tdl, [FakeResponse(200, CHALLENGE)] * tdl.CHALLENGE_STREAK)
    for _ in range(tdl.CHALLENGE_STREAK - 1):
        with pytest.raises(tdl.Challenged):
            tt.whoami()
    with pytest.raises(tdl.ChallengeStop) as e:
        tt.whoami()
    assert "bot protection" in str(e.value)
    assert tt.requests == tdl.CHALLENGE_STREAK

    # Every later read gives up at once with the challenge reason: no request, no sleep.
    with pytest.raises(tdl.ChallengeStop):
        tt.whoami()
    with pytest.raises(tdl.ChallengeStop):
        tt._throttle()
    assert tt.requests == tdl.CHALLENGE_STREAK
    assert clock.sleeps == []


def test_any_other_answer_resets_the_challenge_streak(tdl, clock, logged):
    n = tdl.CHALLENGE_STREAK - 1
    tt = client(tdl, [FakeResponse(200, CHALLENGE)] * n + [FakeResponse(200, PAGE)]
                + [FakeResponse(200, CHALLENGE)] * n)
    for _ in range(n):
        with pytest.raises(tdl.Challenged):
            tt.whoami()
    assert tt.whoami() == {"uniqueId": "me"}
    for _ in range(n):
        with pytest.raises(tdl.Challenged) as e:
            tt.whoami()
        assert not isinstance(e.value, tdl.ChallengeStop)


# --------------------------------------------------------------------------- #
# scrape_profile()
# --------------------------------------------------------------------------- #

USER = "someone"
POST_IDS = [f"73000000000000000{i:02d}" for i in range(10)]


def opts(tmp_path, **kw):
    base = dict(output=str(tmp_path), session_id=None, wait=0.0, proxy=None, limit=None,
                metadata_only=True, workers=1, batch=1, mux_jobs=1, keep_parts=False,
                jobs=1, connections=1, retry_all=False)
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture
def site(tdl, monkeypatch, clock, logged):
    """Serve TikTok pages by URL; records every URL requested."""
    class Site:
        pages: dict[str, str] = {}
        default = NO_DATA
        requested: list[str] = []

    s = Site()
    s.pages, s.requested = {}, []

    def get(self, url, timeout=None, **kw):
        s.requested.append(url)
        return FakeResponse(200, s.pages.get(url, s.default))

    monkeypatch.setattr(requests.Session, "get", get)
    monkeypatch.setattr(tdl, "status", lambda *a, **k: None)
    return s


def profile_page():
    return scope_page("webapp.user-detail", {"statusCode": 0, "userInfo": {"user": {"uniqueId": USER},
                                                                            "stats": {}}})


def post_page(pid):
    return scope_page("webapp.video-detail", {"statusCode": 0, "itemInfo": {"itemStruct": {
        "id": pid, "createTime": "1700000000", "author": {"uniqueId": USER}}}})


def test_challenged_profile_stops_before_yt_dlp(tdl, tmp_path, site, monkeypatch):
    site.pages[f"https://www.tiktok.com/@{USER}"] = CHALLENGE

    def list_posts(*a, **k):
        raise AssertionError("yt-dlp must not be called")

    monkeypatch.setattr(tdl, "list_posts", list_posts)
    with pytest.raises(tdl.Challenged) as e:
        tdl.scrape_profile(f"@{USER}", opts(tmp_path), None, None)
    assert not isinstance(e.value, tdl.RateLimited)   # main() moves on to the next profile
    assert str(e.value).startswith(f"@{USER}: ") and "bot protection" in str(e.value)
    assert site.requested == [f"https://www.tiktok.com/@{USER}"]


def test_challenged_post_pages_stop_the_run_and_keep_finished_posts(tdl, tmp_path, site, monkeypatch):
    site.pages[f"https://www.tiktok.com/@{USER}"] = profile_page()
    good = POST_IDS[:2]
    for pid in good:
        site.pages[f"https://www.tiktok.com/@{USER}/video/{pid}"] = post_page(pid)
    site.default = CHALLENGE
    monkeypatch.setattr(tdl, "list_posts", lambda *a, **k: [{"id": pid} for pid in POST_IDS])

    with pytest.raises(tdl.ChallengeStop):
        tdl.scrape_profile(f"@{USER}", opts(tmp_path), None, None)

    # One read per post, no retries: profile + good posts + the challenge streak, then stop.
    assert len(site.requested) == 1 + len(good) + tdl.CHALLENGE_STREAK
    out = tmp_path / USER
    for pid in good:
        assert (out / "metadata" / f"{pid}.json").exists()
    failed = (out / "failed.txt").read_text(encoding="utf-8")
    assert "stopped early" in failed and "bot protection" in failed
    # The challenged posts before the stop are listed; the rest were not tried.
    challenged = POST_IDS[len(good):len(good) + tdl.CHALLENGE_STREAK - 1]
    assert all(pid in failed for pid in challenged)
    left = len(POST_IDS) - len(good) - len(challenged)
    assert f"{left} posts were not processed" in failed

    # A re-run skips the finished posts.
    site.requested.clear()
    with pytest.raises(tdl.ChallengeStop):
        tdl.scrape_profile(f"@{USER}", opts(tmp_path), None, None)
    assert not any(pid in url for url in site.requested for pid in good)


# --------------------------------------------------------------------------- #
# main()
# --------------------------------------------------------------------------- #

@pytest.fixture
def run_main(tdl, monkeypatch, logged):
    """Run main() with these targets and a fake scrape_profile; returns (exit code, targets tried)."""
    monkeypatch.setattr(tdl, "LANG", tdl.LANG)        # main() sets these globals: restore them
    monkeypatch.setattr(tdl, "_COLOR", tdl._COLOR)
    monkeypatch.setattr(tdl, "find_tool", lambda name: None)
    monkeypatch.setattr(tdl, "find_ffmpeg", lambda: None)

    def run(targets, scrape):
        tried = []

        def fake_scrape(target, *a):
            tried.append(target)
            return scrape(target)

        monkeypatch.setattr(tdl, "scrape_profile", fake_scrape)
        monkeypatch.setattr(tdl.sys, "argv", ["tiktok-dl", *targets, "--lang", "en", "--no-color"])
        with pytest.raises(SystemExit) as e:
            tdl.main()
        return e.value.code, tried

    return run


def test_main_stops_the_run_on_challenge_streak(tdl, run_main, logged):
    def scrape(target):
        raise tdl.ChallengeStop(tdl.challenge_message())

    code, tried = run_main(["first", "second"], scrape)
    assert code == 1
    assert tried == ["first"]
    out = "\n".join(logged)
    assert "bot protection" in out and "429" not in out


def test_main_skips_a_challenged_profile_and_goes_on(tdl, run_main, logged):
    def scrape(target):
        if target == "first":
            raise tdl.Challenged(f"@first: {tdl.challenge_message()}")
        return 0

    code, tried = run_main(["first", "second"], scrape)
    assert code == 1
    assert tried == ["first", "second"]
    assert any("@first: " in line and "bot protection" in line for line in logged)
