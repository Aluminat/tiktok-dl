"""--cookies: the Netscape cookie file a browser extension exports. It is read in memory only, and its
contents never show up in a message."""
from __future__ import annotations

import pytest

from tiktok_dl.core import console, cookies
from tiktok_dl.core.errors import ScrapeError

SECRET = "xs-s3cr3t-value"

# As browser extensions export it: the header, a #HttpOnly_ line, a session cookie (expiry 0).
FILE = (
    "# Netscape HTTP Cookie File\n"
    "# https://curl.haxx.se/rfc/cookie_spec.html\n"
    "\n"
    ".facebook.com\tTRUE\t/\tTRUE\t1893456000\tc_user\t100001234567890\n"
    f"#HttpOnly_.facebook.com\tTRUE\t/\tTRUE\t1893456000\txs\t{SECRET}\n"
    "www.facebook.com\tFALSE\t/\tFALSE\t0\tlocale\ten_US\n"
    ".instagram.com\tTRUE\t/\tTRUE\t1893456000\tsessionid\tig-value\n"
    ".notfacebook.com\tTRUE\t/\tFALSE\t1893456000\tother\tx\n"
)


@pytest.fixture
def cookie_file(tmp_path):
    path = tmp_path / "cookies.txt"
    path.write_text(FILE, encoding="utf-8")
    return path


def test_reads_every_cookie(cookie_file):
    jar = cookies.load(str(cookie_file))
    assert [(c.domain, c.name, c.value) for c in jar] == [
        (".facebook.com", "c_user", "100001234567890"), (".facebook.com", "xs", SECRET),
        ("www.facebook.com", "locale", "en_US"), (".instagram.com", "sessionid", "ig-value"),
        (".notfacebook.com", "other", "x")]
    xs = jar[1]
    assert xs.secure and xs.expires == 1893456000 and xs.path == "/" and xs.domain_initial_dot
    assert jar[2].expires is None and not jar[2].secure   # a session cookie


def test_cookies_of_one_site(cookie_file):
    jar = cookies.load(str(cookie_file))
    assert [c.name for c in cookies.for_domain(jar, "facebook.com")] == ["c_user", "xs", "locale"]
    assert [c.name for c in cookies.for_domain(jar, "instagram.com")] == ["sessionid"]


def test_a_sites_cookies_straight_from_the_file(cookie_file):
    assert [c.name for c in cookies.for_site(str(cookie_file), "facebook.com")] == ["c_user", "xs", "locale"]
    assert [(c.name, c.value) for c in cookies.for_site(str(cookie_file), "instagram.com")] == [
        ("sessionid", "ig-value")]
    assert cookies.for_site(str(cookie_file), "tiktok.com") == []


def test_file_without_header_and_with_windows_line_ends(tmp_path):
    path = tmp_path / "c.txt"
    path.write_bytes(b".facebook.com\tTRUE\t/\tTRUE\t1893456000\tc_user\t1\r\n")
    assert [(c.name, c.value) for c in cookies.load(str(path))] == [("c_user", "1")]


def test_the_file_is_not_changed(cookie_file):
    before = cookie_file.read_bytes()
    cookies.load(str(cookie_file))
    assert cookie_file.read_bytes() == before


def test_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(console, "LANG", "en")
    path = tmp_path / "nope.txt"
    with pytest.raises(ScrapeError, match=r"^cannot read the --cookies file .*nope\.txt"):
        cookies.load(str(path))


def test_not_a_netscape_file_never_shows_its_contents(tmp_path, monkeypatch):
    monkeypatch.setattr(console, "LANG", "en")
    path = tmp_path / "cookies.json"
    path.write_text(f'[{{"name": "xs", "value": "{SECRET}"}}]', encoding="utf-8")
    with pytest.raises(ScrapeError) as e:
        cookies.load(str(path))
    assert str(e.value).startswith("the --cookies file is not a Netscape cookie file (cookies.txt)")
    assert SECRET not in str(e.value)


def test_bad_lines_next_to_good_ones_are_skipped(tmp_path):
    path = tmp_path / "c.txt"
    path.write_text(f"garbage {SECRET}\n.facebook.com\tTRUE\t/\tTRUE\t1893456000\tc_user\t1\n", encoding="utf-8")
    assert [c.name for c in cookies.load(str(path))] == ["c_user"]


def test_message_in_vietnamese(tmp_path, monkeypatch):
    monkeypatch.setattr(console, "LANG", "vi")
    with pytest.raises(ScrapeError, match="^không đọc được file --cookies"):
        cookies.load(str(tmp_path / "nope.txt"))
