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
                           slideshow=True, keep_images=False, slide_seconds=3.0)
    for key, value in overrides.items():
        setattr(opts, key, value)
    return opts


class FakeAria2:
    """aria2c: records argv and stdin, and "downloads" each URL to its dir/out."""
    calls: list[SimpleNamespace] = []

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
                    (folder / line[6:]).write_bytes(body + b"\x00" * 4096)
        return None, None

    def terminate(self):
        pass


@pytest.fixture
def tools(monkeypatch):
    """Fake aria2c (download.subprocess.Popen) and ffmpeg (mux.run_ffmpeg): ffmpeg writes its output."""
    FakeAria2.calls = []
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

def test_shortcode_and_media_id_match_yt_dlp():
    from yt_dlp.extractor.instagram import _id_to_pk, _pk_to_id

    for code in (CODE, "B", "CzWb3-Hr_Q9", "DAbCdEfGhIj"):
        assert instagram.media_id(code) == str(_id_to_pk(code))
        assert instagram.shortcode(instagram.media_id(code)) == _pk_to_id(_id_to_pk(code))
    private = CODE + "x" * 28   # links to posts of private accounts add 28 characters
    assert instagram.media_id(private) == str(_id_to_pk(private)) == PK
    assert instagram.shortcode(f"{PK}_123") == CODE


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


def test_photo_posts_and_carousels_are_not_supported_yet(tmp_path, server, tools):
    for media_type in (1, 8):
        item = {**copy.deepcopy(ITEM), "media_type": media_type, "product_type": "feed"}
        item.pop("video_versions")
        server.answers = [ok(item)]
        assert run(tmp_path, f"https://www.instagram.com/p/{CODE}/") == 1
        # An error, not "skipped": these download once photo posts are supported.
        assert statuses(tmp_path) == {PK: "error"}
        assert "photo posts and carousels are not supported yet" in (out_dir(tmp_path) / "failed.txt").read_text(
            encoding="utf-8")


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
