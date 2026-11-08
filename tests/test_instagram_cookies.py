"""--cookies for Instagram (issue #34): the instagram.com "sessionid" of the cookie file logs in when
--ig-session-id / INSTAGRAM_SESSIONID is not set. No test touches the network."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import requests

from tiktok_dl import cli
from tiktok_dl.core import pacing
from tiktok_dl.core.errors import ScrapeError
from tiktok_dl.sites import instagram

FROM_FILE = "ig-from-cookie-file"
COOKIE_FILE = (
    "# Netscape HTTP Cookie File\n"
    f"#HttpOnly_.instagram.com\tTRUE\t/\tTRUE\t1893456000\tsessionid\t{FROM_FILE}\n"
    ".instagram.com\tTRUE\t/\tTRUE\t1893456000\tcsrftoken\tabc\n"
    ".facebook.com\tTRUE\t/\tTRUE\t1893456000\txs\tfacebook-only\n"
)
CODE = "C1a2B3c4D5e"
USER = "some.user"


@pytest.fixture
def cookie_file(tmp_path):
    path = tmp_path / "cookies.txt"
    path.write_text(COOKIE_FILE, encoding="utf-8")
    return path


def opts(**values):
    return SimpleNamespace(**{"ig_session_id": None, "cookies": None, **values})


def test_the_sessionid_of_the_cookie_file(cookie_file):
    assert instagram.InstagramSite.session(opts(cookies=str(cookie_file))) == FROM_FILE


def test_the_flag_or_env_var_wins_over_the_cookie_file(cookie_file):
    assert instagram.InstagramSite.session(opts(ig_session_id="from-flag", cookies=str(cookie_file))) == "from-flag"


def test_no_instagram_sessionid_in_the_file_is_no_login(tmp_path):
    path = tmp_path / "fb.txt"
    path.write_text(".facebook.com\tTRUE\t/\tTRUE\t1893456000\txs\tx\n", encoding="utf-8")
    assert instagram.InstagramSite.session(opts(cookies=str(path))) is None
    with pytest.raises(ScrapeError, match="^Instagram needs a login"):
        instagram.InstagramSite(None, instagram.InstagramSite.session(opts(cookies=str(path))), 0, None)


def test_without_cookies_option_nothing_changes():
    assert instagram.InstagramSite.session(opts()) is None
    assert instagram.InstagramSite.session(SimpleNamespace(ig_session_id="x")) == "x"   # opts built by older tests


def test_main_logs_in_to_instagram_with_the_cookie_file(cookie_file, tmp_path, monkeypatch):
    sent = []

    def get(session, url, **kwargs):
        sent.append(session.prepare_request(requests.Request("GET", url)).headers.get("Cookie"))
        r = requests.Response()
        r.status_code, r.url, r.encoding = 200, url, "utf-8"
        r._content = json.dumps({"items": [{"pk": int(instagram.media_id(CODE)), "code": CODE, "taken_at": 1,
                                            "media_type": 2, "user": {"username": USER}}]}).encode()
        return r

    monkeypatch.setattr(requests.sessions.Session, "get", get)
    monkeypatch.setattr(pacing.time, "sleep", lambda s: None)
    monkeypatch.setattr(cli.tools, "find_tool", lambda name: None)
    monkeypatch.setattr(cli.tools, "find_ffmpeg", lambda: None)
    monkeypatch.setattr(cli.console, "log", lambda msg="", err=False: None)
    monkeypatch.setattr(cli.console, "LANG", cli.console.LANG)
    monkeypatch.setattr(cli.console, "_COLOR", cli.console._COLOR)
    monkeypatch.delenv("INSTAGRAM_SESSIONID", raising=False)
    monkeypatch.delenv("TIKTOK_SESSIONID", raising=False)
    monkeypatch.setattr(cli.sys, "argv", ["tiktok-dl", f"https://www.instagram.com/reel/{CODE}/", "--cookies",
                                          str(cookie_file), "--metadata-only", "-o", str(tmp_path / "out"),
                                          "--lang", "en", "--no-color"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 0
    assert sent == [f"sessionid={FROM_FILE}"]   # the sessionid only: no other cookie of the file
