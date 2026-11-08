"""Instagram reels (issue #32): links, login, the API reader, stop rules, defaults, CSV and tags.

The API answers are hand-written, in the shape yt-dlp's Instagram extractor reads from
/api/v1/media/<id>/info/. No test touches the network: page reads go to a fake server, aria2c and
ffmpeg are fakes too.
"""
from __future__ import annotations

import copy
import csv
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from tiktok_dl import cli
from tiktok_dl.core import console, download, flow, mux, pacing, records
from tiktok_dl.core.errors import RateLimited, ScrapeError, SiteStop
from tiktok_dl.core.site import Verdict
from tiktok_dl.sites import instagram, tiktok

SECRET = "ig-s3cr3t-session"
CODE = "C1a2B3c4D5e"
PK = instagram.media_id(CODE)
USER = "some.user"
API_URL = f"https://www.instagram.com/api/v1/media/{PK}/info/"
REEL = f"https://www.instagram.com/reel/{CODE}/?igsh=abc"
USER_REEL = f"https://www.instagram.com/{USER}/reel/{CODE}/"

ITEM = {
    "pk": int(PK),
    "id": f"{PK}_123",
    "code": CODE,
    "taken_at": 1700000000,
    "media_type": 2,
    "product_type": "clips",
    "user": {"pk": 123, "username": USER, "full_name": "Some User", "is_verified": True},
    "caption": {"text": "Sunset #beach with @friend.one\nsecond line"},
    "like_count": 10,
    "comment_count": 2,
    "play_count": 1000,
    "view_count": 900,
    "video_duration": 12.4,
    "original_width": 1080,
    "original_height": 1920,
    "has_audio": True,
    "video_versions": [
        {"type": 101, "width": 480, "height": 854, "url": "https://cdn.example/small.mp4"},
        {"type": 102, "width": 1080, "height": 1920, "url": "https://cdn.example/big.mp4"},
        {"type": 103, "width": 480, "height": 854, "url": "https://cdn.example/small.mp4"},
    ],
    "image_versions2": {"candidates": [
        {"width": 150, "height": 266, "url": "https://cdn.example/thumb.jpg"},
        {"width": 1080, "height": 1920, "url": "https://cdn.example/cover.jpg"},
    ]},
    "clips_metadata": {"music_info": {"music_asset_info": {"title": "Song", "display_artist": "Band"}}},
}


def answer(status: int = 200, body=None, url: str = API_URL, headers: dict | None = None,
           text: str | None = None) -> requests.Response:
    """A real requests.Response, as the website's API would send it."""
    r = requests.Response()
    r.status_code = status
    r._content = (text if text is not None else json.dumps(body if body is not None else {})).encode("utf-8")
    r.url = url
    r.encoding = "utf-8"
    r.headers.update(headers or {})
    return r


def ok(item=None) -> requests.Response:
    return answer(200, {"items": [item or ITEM], "num_results": 1, "status": "ok"})


@pytest.fixture
def server(monkeypatch):
    """The Instagram API: answers page reads in order and records each request (URL, headers, the
    Cookie header requests would send)."""
    srv = SimpleNamespace(answers=[], calls=[])

    def get(session, url, **kwargs):
        sent = session.prepare_request(requests.Request("GET", url))
        srv.calls.append(SimpleNamespace(url=url, headers=dict(sent.headers)))
        if not srv.answers:
            raise AssertionError(f"unexpected request: {url}")
        r = srv.answers.pop(0)
        return r() if callable(r) else r

    monkeypatch.setattr(requests.sessions.Session, "get", get)
    monkeypatch.setattr(pacing.time, "sleep", lambda s: None)
    monkeypatch.setattr(pacing.STOP, "wait", lambda s=None: False)   # no real waits between attempts
    return srv


@pytest.fixture
def logged(monkeypatch):
    lines: list[str] = []
    monkeypatch.setattr(console, "log", lambda msg="", err=False: lines.append(msg))
    return lines


def make_opts(tmp_path, **overrides) -> SimpleNamespace:
    opts = SimpleNamespace(output=str(tmp_path), session_id=None, ig_session_id=SECRET, wait=0, proxy=None,
                           limit=None, metadata_only=False, workers=None, batch=10, mux_jobs=1, keep_parts=False,
                           jobs=1, connections=None, cover=True, subs="original", retry_all=False,
                           slideshow=None, keep_images=False, slide_seconds=3.0)
    for key, value in overrides.items():
        setattr(opts, key, value)
    return opts


class FakeAria2:
    """aria2c: records argv and stdin, and "downloads" each URL to its dir/out. A URL in `broken` comes
    down as an error page ("html") or cut short, with aria2c's control file left behind ("cut")."""
    calls: list[SimpleNamespace] = []
    broken: dict[str, str] = {}

    def __init__(self, args, stdin=None, stdout=None, stderr=None):
        self.args = args
        self.input = b""

    def communicate(self, input=None, timeout=None):
        if input:
            self.input = input
            FakeAria2.calls.append(SimpleNamespace(args=self.args, input=input.decode("utf-8")))
            url = folder = None
            for line in input.decode("utf-8").splitlines():
                if not line.startswith("  "):
                    url = line
                elif line.startswith("  dir="):
                    folder = Path(line[6:])
                elif line.startswith("  out="):
                    body = b"\x00\x00\x00\x18ftypmp42" if url.endswith(".mp4") else b"\xff\xd8\xff\xe0"
                    if FakeAria2.broken.get(url) == "html":
                        body = b"<!DOCTYPE html><html>URL signature expired</html>"
                    (folder / line[6:]).write_bytes(body + b"\x00" * 4096)
                    if FakeAria2.broken.get(url) == "cut":
                        (folder / f"{line[6:]}.aria2").write_bytes(b"\x00" * 64)
        return None, None

    def terminate(self):
        pass


@pytest.fixture
def tools(monkeypatch):
    """Fake aria2c (download.subprocess.Popen) and ffmpeg (mux.run_ffmpeg): ffmpeg writes its output."""
    FakeAria2.calls = []
    FakeAria2.broken = {}
    ffmpeg_calls: list[list[str]] = []
    monkeypatch.setattr(download.subprocess, "Popen", FakeAria2)

    def run_ffmpeg(cmd):
        ffmpeg_calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 4096)
        return True, ""

    monkeypatch.setattr(mux, "run_ffmpeg", run_ffmpeg)
    return SimpleNamespace(aria2=FakeAria2.calls, ffmpeg=ffmpeg_calls)


def run(tmp_path, target=REEL, **overrides) -> int:
    return cli.scrape_profile(target, make_opts(tmp_path, **overrides), "aria2c", "ffmpeg")


def out_dir(tmp_path) -> Path:
    return tmp_path / "instagram" / USER


def statuses(tmp_path) -> dict:
    progress = records.Progress(out_dir(tmp_path) / ".progress.db")
    try:
        return dict(progress.db.execute("SELECT id, status FROM posts").fetchall())
    finally:
        progress.close()


# --------------------------------------------------------------------------- #
# Links
# --------------------------------------------------------------------------- #

# Shortcode -> media id, as yt-dlp's _id_to_pk gives them (yt-dlp 2026.10.26).
YT_DLP_IDS = {"C1a2B3c4D5e": "3268162102382050910", "B": "1", "CzWb3-Hr_Q9": "3230892376212894781",
              "DAbCdEfGhIj": "3466375131966345763"}


@pytest.mark.parametrize(("code", "pk"), YT_DLP_IDS.items())
def test_shortcode_and_media_id_match_yt_dlp(code, pk):
    assert instagram.media_id(code) == pk
    assert instagram.shortcode(pk) == code


def test_long_shortcodes_and_full_ids():
    private = CODE + "x" * 28   # links to posts of private accounts add 28 characters
    assert instagram.media_id(private) == PK == YT_DLP_IDS[CODE]
    assert instagram.shortcode(f"{PK}_123") == CODE   # the "id" field: <media id>_<user id>


@pytest.mark.parametrize(("link", "profile", "kind"), [
    (f"https://www.instagram.com/reel/{CODE}/", None, "reel"),
    (f"https://instagram.com/reel/{CODE}/?igsh=MWQ1ZGUxMzBkMA==", None, "reel"),
    (f"https://www.instagram.com/reels/{CODE}/", None, "reel"),
    (f"https://www.instagram.com/p/{CODE}/?img_index=2", None, "p"),
    (f"https://m.instagram.com/tv/{CODE}", None, "tv"),
    (f"www.instagram.com/p/{CODE}", None, "p"),
    (f" https://www.instagram.com/{USER}/reel/{CODE}/ \n", USER, "reel"),
    (f"https://www.instagram.com/{USER}/p/{CODE}/", USER, "p"),
])
def test_post_links(link, profile, kind):
    got_profile, entry = instagram.InstagramSite.parse(link)
    assert got_profile == profile
    assert entry.id == PK
    assert entry.link == f"https://www.instagram.com/{kind}/{CODE}/"


@pytest.mark.parametrize("target", ["https://www.instagram.com/some.user/", "https://www.instagram.com/some.user/reels/",
                                    "instagram.com/some.user", "some.user", "@some.user",
                                    "https://www.instagram.com/stories/some.user/123/"])
def test_profiles_are_not_supported_yet(target):
    with pytest.raises(ScrapeError, match="^Instagram profiles are not supported yet"):
        instagram.InstagramSite.parse(target)


@pytest.mark.parametrize("target", [f"https://www.instagram.com/../reel/{CODE}/", f"https://www.instagram.com/.x/p/{CODE}/",
                                    "https://www.instagram.com/reel/abc$def/"])
def test_odd_post_links_are_rejected(target):
    with pytest.raises(ScrapeError):
        instagram.InstagramSite.parse(target)


def test_link_of_an_old_failed_row():
    assert instagram.InstagramSite.link(None, PK) == f"https://www.instagram.com/p/{CODE}/"


# --------------------------------------------------------------------------- #
# Login
# --------------------------------------------------------------------------- #

class TestResolveSessionId:
    def test_flag_wins_over_env(self):
        assert instagram.resolve_session_id("from-flag", {"INSTAGRAM_SESSIONID": "from-env"}) == "from-flag"

    def test_env_only(self):
        assert instagram.resolve_session_id(None, {"INSTAGRAM_SESSIONID": f" {SECRET}\n"}) == SECRET

    def test_neither_or_blank(self):
        assert instagram.resolve_session_id(None, {}) is None
        assert instagram.resolve_session_id("  ", {"INSTAGRAM_SESSIONID": ""}) is None

    def test_tiktok_cookie_is_not_used(self):
        assert instagram.resolve_session_id(None, {"TIKTOK_SESSIONID": SECRET}) is None


def test_no_cookie_makes_no_request_and_writes_nothing(tmp_path, server):
    with pytest.raises(ScrapeError) as e:
        run(tmp_path, ig_session_id=None, session_id="a-tiktok-cookie")
    assert str(e.value).startswith("Instagram needs a login: set the INSTAGRAM_SESSIONID environment variable")
    assert "secondary account" in str(e.value)
    assert server.calls == []
    assert list(tmp_path.iterdir()) == []


def test_no_cookie_message_in_vietnamese(tmp_path, server, monkeypatch):
    monkeypatch.setattr(console, "LANG", "vi")
    with pytest.raises(ScrapeError, match="^Instagram cần đăng nhập"):
        run(tmp_path, USER_REEL, ig_session_id=None)


@pytest.fixture
def run_main(monkeypatch, logged):
    monkeypatch.setattr(console, "LANG", console.LANG)        # main() sets these globals: restore them
    monkeypatch.setattr(console, "_COLOR", console._COLOR)
    monkeypatch.setattr(cli.tools, "find_tool", lambda name: None)
    monkeypatch.setattr(cli.tools, "find_ffmpeg", lambda: None)
    monkeypatch.delenv("TIKTOK_SESSIONID", raising=False)
    monkeypatch.delenv("INSTAGRAM_SESSIONID", raising=False)

    def go(*args):
        monkeypatch.setattr(cli.sys, "argv", ["tiktok-dl", *args, "--lang", "en", "--no-color"])
        with pytest.raises(SystemExit) as e:
            cli.main()
        return e.value.code

    return go


def test_main_takes_the_flag_over_the_env_var(run_main, server, monkeypatch, tmp_path):
    monkeypatch.setenv("INSTAGRAM_SESSIONID", "from-env")
    server.answers = [ok()]
    assert run_main(REEL, "--ig-session-id", "from-flag", "--metadata-only", "-o", str(tmp_path)) == 0
    assert server.calls[0].headers["Cookie"] == "sessionid=from-flag"


def test_main_reads_the_env_var(run_main, server, monkeypatch, tmp_path):
    monkeypatch.setenv("INSTAGRAM_SESSIONID", SECRET)
    server.answers = [ok()]
    assert run_main(REEL, "--metadata-only", "-o", str(tmp_path)) == 0
    assert server.calls[0].headers["Cookie"] == f"sessionid={SECRET}"


def test_main_without_cookie(run_main, server, logged, tmp_path):
    assert run_main(REEL, "-o", str(tmp_path)) == 1
    assert any(line.startswith("Error: Instagram needs a login") for line in logged)
    assert server.calls == [] and list(tmp_path.iterdir()) == []


def test_cookie_stays_on_instagram(tmp_path):
    client = instagram.Instagram(SECRET, 0)
    assert client.cookie_header() == ""   # aria2c gets no cookie at all

    def cookie_for(url):
        return client.s.prepare_request(requests.Request("GET", url)).headers.get("Cookie")

    assert cookie_for(API_URL) == f"sessionid={SECRET}"
    assert cookie_for("https://scontent.cdninstagram.com/v/t50/x.mp4") is None   # the requests fallback
    assert cookie_for("https://cdn.example/big.mp4") is None
    site = instagram.InstagramSite(None, SECRET, 0, None)
    assert SECRET not in repr(site) and SECRET not in repr(site.client)


# --------------------------------------------------------------------------- #
# A reel, start to finish
# --------------------------------------------------------------------------- #

def test_reel_downloads_with_tags_and_rerun_skips(tmp_path, server, tools, logged, capsys):
    server.answers = [ok()]
    assert run(tmp_path) == 0

    # One read: owner() learnt the author from it, and read_post() reused it.
    assert [c.url for c in server.calls] == [API_URL]
    sent = server.calls[0].headers
    assert sent["X-IG-App-ID"] == instagram.APP_ID and sent["Cookie"] == f"sessionid={SECRET}"
    assert "Origin" not in sent

    folder = out_dir(tmp_path)
    name = f"2023-11-14_{PK}_Sunset beach with @friend.one second line.mp4"
    assert (folder / name).is_file()
    assert statuses(tmp_path) == {PK: "done"}
    assert not (folder / "failed.txt").exists() and not (folder / "profile.json").exists()

    # aria2c: the largest version first, the cover, Instagram's referer, no cookie.
    first = tools.aria2[0].input.splitlines()
    assert first[0] == "https://cdn.example/big.mp4"
    assert "https://cdn.example/cover.jpg" in first
    assert "header=Cookie" not in tools.aria2[0].input
    assert "--referer=https://www.instagram.com/" in tools.aria2[0].args

    tags = tools.ffmpeg[0]
    for tag in ("album=Instagram @some.user", "artist=Some User (@some.user)", "genre=Instagram",
                f"episode_id={CODE}", "date=2023-11-14", "title=Sunset #beach with @friend.one"):
        assert tag in tags

    with open(folder / "videos.csv", encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["id"] == PK and rows[0]["url"] == f"https://www.instagram.com/reel/{CODE}/"
    assert rows[0]["file"] == name and rows[0]["plays"] == "1000" and rows[0]["type"] == "reel"

    # The cookie never shows up: aria2c argv or stdin, files, output.
    for call in tools.aria2:
        assert SECRET not in " ".join(call.args) and SECRET not in call.input
    assert all(SECRET not in str(cmd) for cmd in tools.ffmpeg)
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert SECRET.encode() not in path.read_bytes(), path
    out = capsys.readouterr()
    assert SECRET not in out.out + out.err and SECRET not in "\n".join(map(str, logged))

    # A re-run reads the post once to find its folder, then skips it.
    server.calls.clear()
    tools.aria2.clear()
    server.answers = [ok()]
    assert run(tmp_path) == 0
    assert len(server.calls) == 1 and tools.aria2 == []
    assert any("Nothing left to download" in line for line in logged)


def test_link_with_the_username_needs_no_read_on_rerun(tmp_path, server, tools):
    server.answers = [ok()]
    assert run(tmp_path, USER_REEL) == 0
    assert (out_dir(tmp_path) / ".progress.db").is_file()
    server.calls.clear()
    assert run(tmp_path, USER_REEL) == 0
    assert server.calls == []


def test_unexpected_author_is_not_a_folder(tmp_path, server):
    item = copy.deepcopy(ITEM)
    item["user"]["username"] = ".."
    server.answers = [ok(item)]
    with pytest.raises(ScrapeError, match="no valid user.username"):
        run(tmp_path, metadata_only=True)
    assert list(tmp_path.iterdir()) == []


# --------------------------------------------------------------------------- #
# Stop rules and backoff
# --------------------------------------------------------------------------- #

STOP_ANSWERS = {
    "checkpoint": (lambda: answer(400, {"message": "checkpoint_required", "checkpoint_url": "/challenge/x/",
                                        "status": "fail"}), "pass a check (checkpoint_required)"),
    "challenge": (lambda: answer(400, {"message": "challenge_required", "status": "fail"}),
                  "pass a check (challenge_required)"),
    "challenge redirect": (lambda: answer(200, text="<html>", url="https://www.instagram.com/challenge/?next=/x"),
                           "pass a check (challenge_required)"),
    "login": (lambda: answer(403, {"message": "login_required", "status": "fail"}), "logged out (login_required)"),
    "login redirect": (lambda: answer(200, text="<html>", url="https://www.instagram.com/accounts/login/?next=/api"),
                       "logged out (login_required)"),
    "401": (lambda: answer(401, text=""), "logged out (login_required)"),
    "feedback": (lambda: answer(400, {"message": "feedback_required", "spam": True, "status": "fail"}),
                 "limiting this account (feedback_required)"),
}


@pytest.mark.parametrize("kind", STOP_ANSWERS)
def test_stop_answers_end_the_run_at_once(tmp_path, server, tools, kind):
    make, text = STOP_ANSWERS[kind]
    assert instagram.classify(make()) is Verdict.STOP
    server.answers = [make, make, make, make]
    with pytest.raises(SiteStop) as e:
        run(tmp_path, USER_REEL)
    assert text in str(e.value)
    assert len(server.calls) == 1   # not retried
    failed = (out_dir(tmp_path) / "failed.txt").read_text(encoding="utf-8")
    assert "stopped early" in failed and text in failed
    assert statuses(tmp_path) == {}   # not marked: the next run tries it again


def test_check_message_says_to_clear_it_in_a_browser():
    message = instagram.stop_message(STOP_ANSWERS["checkpoint"][0]())
    assert "Open Instagram in a browser" in message and "never tries to solve or skip it" in message


def test_stop_during_the_owner_read(tmp_path, server):
    server.answers = [STOP_ANSWERS["login"][0]()]
    with pytest.raises(SiteStop, match="login_required"):
        run(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_stop_stops_the_other_reads(server):
    client = instagram.Instagram(SECRET, 0)
    server.answers = [STOP_ANSWERS["feedback"][0]()]
    with pytest.raises(SiteStop):
        client.media(PK)
    with pytest.raises(SiteStop, match="feedback_required"):   # queued reads give up with no request
        client.media(PK)
    assert len(server.calls) == 1


def test_main_prints_the_stop_message(run_main, server, logged, monkeypatch, tmp_path):
    monkeypatch.setenv("INSTAGRAM_SESSIONID", SECRET)
    server.answers = [STOP_ANSWERS["checkpoint"][0]()]
    assert run_main(USER_REEL, "-o", str(tmp_path)) == 1
    text = "\n".join(logged)
    assert "✗ Instagram wants this account to pass a check (checkpoint_required)" in text
    assert "run the same command again to continue" in text
    assert "TikTok" not in text


@pytest.fixture
def clock(monkeypatch):
    c = SimpleNamespace(now=1000.0, sleeps=[])

    def sleep(seconds):
        c.sleeps.append(round(seconds, 3))
        c.now += seconds

    monkeypatch.setattr(pacing.time, "monotonic", lambda: c.now)
    monkeypatch.setattr(pacing.time, "sleep", sleep)
    return c


def test_429_backs_off_and_honours_retry_after(server, clock, logged):
    client = instagram.Instagram(SECRET, 0)
    server.answers = [answer(429, {"message": "Please wait a few minutes before you try again.", "status": "fail"},
                             headers={"Retry-After": "7"}), ok()]
    assert client.media(PK)["code"] == CODE
    assert round(sum(clock.sleeps), 3) == 7.0
    assert len(logged) == 1 and "Instagram is limiting requests (HTTP 429)" in logged[0]


def test_429_streak_stops_the_run_naming_instagram(server, clock, logged):
    client = instagram.Instagram(SECRET, 0)
    server.answers = [answer(429) for _ in range(pacing.RATE_LIMIT_STREAK)]
    with pytest.raises(ScrapeError, match="HTTP 429, Instagram is limiting requests") as first:
        client.media(PK)                      # 4 attempts, all rate-limited: this post fails
    assert not isinstance(first.value, RateLimited)
    with pytest.raises(RateLimited) as e:     # 4 more make the streak: the run stops
        client.media(PK)
    assert not isinstance(e.value, SiteStop)
    assert len(server.calls) == pacing.RATE_LIMIT_STREAK
    assert (e.value.site, e.value.codes) == ("Instagram", "429")
    assert str(e.value).startswith("Instagram keeps refusing requests (HTTP 429)")


def test_403_without_a_message_is_not_a_rate_limit():
    assert instagram.classify(answer(403, text="")) is Verdict.OK


# --------------------------------------------------------------------------- #
# Posts that can never be read
# --------------------------------------------------------------------------- #

NOT_FOUND = {
    "404": lambda: answer(404, {"message": "Media not found or unavailable", "status": "fail"}),
    "400": lambda: answer(400, {"message": "Media not found or unavailable", "status": "fail"}),
    "no items": lambda: answer(200, {"items": [], "num_results": 0, "status": "ok"}),
}


@pytest.mark.parametrize("kind", NOT_FOUND)
def test_missing_posts_are_skipped_until_retry_all(tmp_path, server, tools, kind):
    server.answers = [NOT_FOUND[kind]]
    assert run(tmp_path, USER_REEL) == 1
    assert len(server.calls) == 1   # not retried
    assert statuses(tmp_path) == {PK: "skipped"}
    assert "(skipped)" in (out_dir(tmp_path) / "failed.txt").read_text(encoding="utf-8")

    server.calls.clear()
    assert run(tmp_path, USER_REEL) == 0   # skipped: no read
    assert server.calls == []

    server.answers = [ok()]
    assert run(tmp_path, USER_REEL, retry_all=True) == 0
    assert len(server.calls) == 1 and statuses(tmp_path) == {PK: "done"}


def test_missing_post_from_a_link_without_username(tmp_path, server):
    # The author is unknown, so there is no folder to record it in: an error, nothing written.
    server.answers = [NOT_FOUND["404"]()]
    with pytest.raises(ScrapeError, match=f"^https://www.instagram.com/reel/{CODE}/: post does not exist") as e:
        run(tmp_path)
    assert instagram.classify(e.value) is Verdict.PERMANENT
    assert list(tmp_path.iterdir()) == []


# --------------------------------------------------------------------------- #
# Defaults per site
# --------------------------------------------------------------------------- #

def test_instagram_defaults():
    opts = SimpleNamespace(wait=None, workers=None, connections=None)
    got = flow.run_settings(instagram.InstagramSite, opts)
    assert (got.wait, got.workers, got.connections) == (3.0, 1, 4)
    assert (opts.wait, opts.workers, opts.connections) == (None, None, None)   # shared opts left alone


def test_tiktok_defaults_are_unchanged():
    got = flow.run_settings(tiktok.TikTokSite, SimpleNamespace(wait=None, workers=None, connections=None))
    assert (got.wait, got.workers, got.connections) == (0.3, 8, 4)


def test_flags_override_the_defaults_and_are_kept_in_range():
    got = flow.run_settings(instagram.InstagramSite, SimpleNamespace(wait=0.5, workers=40, connections=0))
    assert (got.wait, got.workers, got.connections) == (0.5, 16, 1)


def test_the_run_uses_the_instagram_defaults(tmp_path, server, monkeypatch):
    seen = []
    real = instagram.Instagram

    def recording(session_id, wait, proxy):
        seen.append(wait)
        return real(session_id, 0, proxy)

    monkeypatch.setattr(instagram, "Instagram", recording)
    server.answers = [ok()]
    assert run(tmp_path, metadata_only=True, wait=None) == 0
    assert seen == [3.0]


def test_slideshow_is_on_for_tiktok_and_off_for_instagram():
    unset = SimpleNamespace(wait=None, workers=None, connections=None, slideshow=None)
    assert flow.run_settings(tiktok.TikTokSite, unset).slideshow is True
    assert flow.run_settings(instagram.InstagramSite, unset).slideshow is False
    for flag in (True, False):
        given = SimpleNamespace(wait=None, workers=None, connections=None, slideshow=flag)
        assert flow.run_settings(tiktok.TikTokSite, given).slideshow is flag
        assert flow.run_settings(instagram.InstagramSite, given).slideshow is flag


@pytest.mark.parametrize(("flags", "slideshow"), [((), None), (("--slideshow",), True), (("--no-slideshow",), False)])
def test_cli_leaves_the_slideshow_to_the_site(run_main, monkeypatch, flags, slideshow):
    got = []
    monkeypatch.setattr(cli, "scrape_profile", lambda target, opts, *a: got.append(opts) or 0)
    assert run_main("@some.user", *flags) == 0
    assert got[0].slideshow is slideshow


def test_cli_leaves_the_pacing_flags_unset(run_main, monkeypatch):
    got = []
    monkeypatch.setattr(cli, "scrape_profile", lambda target, opts, *a: got.append(opts) or 0)
    assert run_main("@some.user") == 0
    assert (got[0].wait, got[0].workers, got[0].connections) == (None, None, None)
    assert run_main("@some.user", "--wait", "1", "-w", "2", "-x", "3") == 0
    assert (got[1].wait, got[1].workers, got[1].connections) == (1.0, 2, 3)


# --------------------------------------------------------------------------- #
# CSV row and tags
# --------------------------------------------------------------------------- #

def test_csv_shares_tiktoks_column_names_and_order():
    ig = list(instagram.flatten(ITEM))
    tt = list(tiktok.flatten({"id": "1", "author": {"uniqueId": "x"}}))
    shared = [key for key in ig if key in tt]
    assert shared == [key for key in tt if key in shared]   # same order as TikTok
    assert ig[:len(shared)] == shared                         # Instagram's own columns come after
    for key in ("id", "url", "username", "created", "description", "plays", "likes", "comments", "duration",
                "type"):
        assert key in shared


def test_csv_row_values():
    row = instagram.flatten(ITEM)
    assert row["hashtags"] == "#beach" and row["mentions"] == "@friend.one"
    assert (row["duration"], row["width"], row["height"]) == (12, 1080, 1920)
    assert (row["shortcode"], row["music_title"], row["music_author"]) == (CODE, "Song", "Band")
    assert row["author_verified"] is True and row["nickname"] == "Some User"


def test_file_is_the_last_csv_column_for_every_site(tmp_path):
    (tmp_path / "metadata").mkdir()
    row = tiktok.flatten({"id": "7", "author": {"uniqueId": "x"}, "createTime": 1})
    (tmp_path / "metadata" / "7.json").write_text(json.dumps({"metadata": row, "raw": {}}), encoding="utf-8")
    assert records.write_csv(tmp_path, {"7": "a.mp4"}) == 1
    with open(tmp_path / "videos.csv", encoding="utf-8-sig", newline="") as fh:
        header, values = list(csv.reader(fh))
    assert header == [*row, "file"] and values[-1] == "a.mp4"


# --------------------------------------------------------------------------- #
# Photo posts and carousels (#33)
# --------------------------------------------------------------------------- #

POST = f"https://www.instagram.com/p/{CODE}/"
BASE_NAME = f"2023-11-14_{PK}_Sunset beach with @friend.one second line"


def photo(name: str, pk: int = 1) -> dict:
    """A photo as the API lists it: several sizes of the same image, not sorted."""
    return {"pk": pk, "media_type": 1, "image_versions2": {"candidates": [
        {"width": 640, "height": 800, "url": f"https://cdn.example/{name}_640.jpg"},
        {"width": 1440, "height": 1800, "url": f"https://cdn.example/{name}_1440.jpg"},
        {"width": 150, "height": 150, "url": f"https://cdn.example/{name}_150.jpg"},
    ]}}


def video(name: str, pk: int = 2) -> dict:
    """A video item of a carousel: its versions and its cover."""
    return {"pk": pk, "media_type": 2, "video_duration": 7.5,
            "video_versions": [{"width": 480, "height": 600, "url": f"https://cdn.example/{name}_480.mp4"},
                               {"width": 1080, "height": 1350, "url": f"https://cdn.example/{name}_1080.mp4"}],
            "image_versions2": {"candidates": [
                {"width": 1080, "height": 1350, "url": f"https://cdn.example/{name}_cover.jpg"}]}}


def post_item(media_type: int, **fields) -> dict:
    """A feed post: ITEM without the reel's fields."""
    item = {key: value for key, value in copy.deepcopy(ITEM).items()
            if key not in ("video_versions", "image_versions2", "clips_metadata", "video_duration", "play_count",
                           "has_audio")}
    return {**item, "media_type": media_type, "product_type": "feed", **fields}


SINGLE_PHOTO = post_item(1, image_versions2=photo("single")["image_versions2"])


def downloaded(tmp_path) -> list[str]:
    """Files in the author's folder, metadata and work files left out."""
    return sorted(p.name for p in out_dir(tmp_path).iterdir()
                  if p.is_file() and p.suffix in (".jpg", ".mp4"))


def csv_rows(tmp_path) -> list[dict]:
    with open(out_dir(tmp_path) / "videos.csv", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def aria2_urls(tools) -> list[str]:
    """Every URL handed to aria2c, in order."""
    return [line for call in tools.aria2 for line in call.input.splitlines() if not line.startswith("  ")]


def test_single_photo_is_saved_at_the_largest_size(tmp_path, server, tools):
    server.answers = [ok(SINGLE_PHOTO)]
    assert run(tmp_path, POST) == 0
    assert downloaded(tmp_path) == [f"{BASE_NAME}.jpg"]
    # Only the largest size is asked for: a smaller one is never a fallback.
    assert aria2_urls(tools) == ["https://cdn.example/single_1440.jpg"]
    assert statuses(tmp_path) == {PK: "done"}
    row = csv_rows(tmp_path)[0]
    assert (row["type"], row["file"], row["url"]) == ("photo", f"{BASE_NAME}.jpg", POST)


ALL_PHOTOS = post_item(8, carousel_media_count=3, carousel_media=[photo("a", 11), photo("b", 12), photo("c", 13)])
MIXED = post_item(8, carousel_media_count=3, carousel_media=[photo("a", 11), video("b", 12), photo("c", 13)])


def test_all_photo_carousel_is_saved_item_by_item(tmp_path, server, tools):
    server.answers = [ok(ALL_PHOTOS)]
    assert run(tmp_path, POST) == 0
    names = [f"{BASE_NAME}_01.jpg", f"{BASE_NAME}_02.jpg", f"{BASE_NAME}_03.jpg"]
    assert downloaded(tmp_path) == names
    assert aria2_urls(tools) == ["https://cdn.example/a_1440.jpg", "https://cdn.example/b_1440.jpg",
                                 "https://cdn.example/c_1440.jpg"]
    assert tools.ffmpeg == []   # originals: no slideshow without --slideshow
    row = csv_rows(tmp_path)[0]
    assert (row["type"], row["file"]) == ("carousel", " | ".join(names))


def test_mixed_carousel_keeps_the_order_and_tags_its_videos(tmp_path, server, tools):
    server.answers = [ok(MIXED)]
    assert run(tmp_path, POST) == 0
    names = [f"{BASE_NAME}_01.jpg", f"{BASE_NAME}_02.mp4", f"{BASE_NAME}_03.jpg"]
    assert downloaded(tmp_path) == names
    # The video item: its largest version and its cover, in the item's place.
    assert aria2_urls(tools) == ["https://cdn.example/a_1440.jpg", "https://cdn.example/b_1080.mp4",
                                 "https://cdn.example/b_cover.jpg", "https://cdn.example/c_1440.jpg"]
    # The video is muxed with the post's tags and its cover, like a reel.
    [cmd] = tools.ffmpeg
    assert any(arg.endswith("item_02.mp4") for arg in cmd)
    assert any(arg.endswith("item_02_cover.img") for arg in cmd)
    for tag in ("album=Instagram @some.user", "genre=Instagram", f"episode_id={CODE}", "date=2023-11-14"):
        assert tag in cmd
    assert (out_dir(tmp_path) / names[1]).read_bytes()[4:8] == b"ftyp"
    assert statuses(tmp_path) == {PK: "done"}
    row = csv_rows(tmp_path)[0]
    assert (row["type"], row["file"]) == ("carousel", " | ".join(names))

    # Every file on disk: a re-run skips the post.
    tools.aria2.clear()
    server.answers = [ok(MIXED)]
    assert run(tmp_path, POST) == 0
    assert tools.aria2 == []

    # One file gone: the post is not done any more and is downloaded again.
    (out_dir(tmp_path) / names[2]).unlink()
    server.answers = [ok(MIXED)]
    assert run(tmp_path, POST) == 0
    assert tools.aria2 != [] and downloaded(tmp_path) == names


@pytest.mark.parametrize("how", ["html", "cut"])
def test_a_missing_item_leaves_the_post_unfinished_and_only_it_is_downloaded_again(tmp_path, server, tools, how):
    # Every version of the video item (a video falls back to a smaller version, as a reel does).
    FakeAria2.broken = {"https://cdn.example/b_1080.mp4": how, "https://cdn.example/b_480.mp4": how}
    server.answers = [ok(MIXED)]
    assert run(tmp_path, POST) == 1
    assert statuses(tmp_path) == {PK: "error"}
    assert downloaded(tmp_path) == []   # nothing half done in the author's folder
    assert "item 2 of 3" in (out_dir(tmp_path) / "failed.txt").read_text(encoding="utf-8")

    # The next run asks only for the missing item: the others wait in the work folder.
    FakeAria2.broken = {}
    tools.aria2.clear()
    server.answers = [ok(MIXED)]
    assert run(tmp_path, POST) == 0
    assert aria2_urls(tools) == ["https://cdn.example/b_1080.mp4"]
    assert statuses(tmp_path) == {PK: "done"}
    assert downloaded(tmp_path) == [f"{BASE_NAME}_01.jpg", f"{BASE_NAME}_02.mp4", f"{BASE_NAME}_03.jpg"]
    assert not (out_dir(tmp_path) / ".parts").exists() and not (out_dir(tmp_path) / "failed.txt").exists()


def slideshow_runs(tools) -> list[list[str]]:
    return [cmd for cmd in tools.ffmpeg if "-loop" in cmd]


def test_slideshow_flag_builds_an_mp4_of_an_all_photo_carousel(tmp_path, server, tools):
    server.answers = [ok(ALL_PHOTOS)]
    assert run(tmp_path, POST, slideshow=True) == 0
    [cmd] = slideshow_runs(tools)
    assert sum(arg.endswith(".jpg") for arg in cmd) == 3 and "genre=Instagram" in cmd
    assert downloaded(tmp_path) == [f"{BASE_NAME}.mp4"]
    assert csv_rows(tmp_path)[0]["file"] == f"{BASE_NAME}.mp4"


def test_slideshow_with_keep_images_keeps_the_originals_too(tmp_path, server, tools):
    server.answers = [ok(ALL_PHOTOS)]
    assert run(tmp_path, POST, slideshow=True, keep_images=True) == 0
    names = [f"{BASE_NAME}.mp4", f"{BASE_NAME}_01.jpg", f"{BASE_NAME}_02.jpg", f"{BASE_NAME}_03.jpg"]
    assert downloaded(tmp_path) == names
    assert csv_rows(tmp_path)[0]["file"] == " | ".join(names)


@pytest.mark.parametrize("item", [MIXED, SINGLE_PHOTO], ids=["mixed carousel", "single photo"])
def test_slideshow_flag_leaves_other_posts_as_originals(tmp_path, server, tools, item):
    server.answers = [ok(item)]
    assert run(tmp_path, POST, slideshow=True) == 0
    assert slideshow_runs(tools) == []
    assert f"{BASE_NAME}.mp4" not in downloaded(tmp_path)
    assert len(downloaded(tmp_path)) == (3 if item is MIXED else 1)


def test_a_carousel_item_without_a_link_fails_the_post_for_now(tmp_path, server, tools):
    broken = copy.deepcopy(MIXED)
    broken["carousel_media"][1].pop("video_versions")
    server.answers = [ok(broken)]
    assert run(tmp_path, POST) == 1
    assert tools.aria2 == [] and downloaded(tmp_path) == []
    assert statuses(tmp_path) == {PK: "error"}   # not "skipped": a later run may get the link
    assert "no download link for carousel item 2" in (out_dir(tmp_path) / "failed.txt").read_text(encoding="utf-8")
