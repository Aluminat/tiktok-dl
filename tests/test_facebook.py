"""Facebook reels (issue #34): links, the yt-dlp read, login and --cookies, stop rules, defaults, CSV and tags.

yt-dlp is used only to extract information. The tests replace its Facebook extractor's answer
(FacebookIE._real_extract) with hand-written ones: DASH formats built by yt-dlp's own MPD parser from a
hand-written manifest, plus the progressive sd/hd links, as FacebookIE returns them. No test touches the
network: short links go to a fake server, aria2c and ffmpeg are fakes too.
"""
from __future__ import annotations

import pytest
import requests

from tiktok_dl.core import console
from tiktok_dl.core.errors import ScrapeError
from tiktok_dl.sites import facebook

VIDEO_ID = "1195289147628387"
REEL = f"https://www.facebook.com/reel/{VIDEO_ID}"
REEL_LINK = f"https://www.facebook.com/reel/{VIDEO_ID}/"


# --------------------------------------------------------------------------- #
# Links
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("link", [
    REEL,
    f"https://facebook.com/reel/{VIDEO_ID}/?mibextid=rS40aB7S9Ucbxw6v",
    f"https://m.facebook.com/reel/{VIDEO_ID}",
    f"https://web.facebook.com/reel/{VIDEO_ID}/",
    f"facebook.com/reel/{VIDEO_ID}",
    f"https://www.facebook.com/watch/?v={VIDEO_ID}",
    f"https://www.facebook.com/watch?v={VIDEO_ID}&ref=sharing",
    f"https://www.facebook.com/watch/?ref=x&v={VIDEO_ID}",
    f"  HTTPS://WWW.FACEBOOK.COM/REEL/{VIDEO_ID}/ \n",
])
def test_reel_links(link):
    assert facebook.FacebookSite.parse(link) == (None, facebook.Entry(VIDEO_ID, REEL_LINK))


@pytest.fixture
def redirects(monkeypatch):
    """The short-link server: {url: Location}. Records each request (URL, whether redirects were followed,
    the Cookie header)."""
    srv = {"map": {}, "calls": []}

    def request(session, method, url, **kwargs):
        srv["calls"].append((method, url, kwargs.get("allow_redirects"), (kwargs.get("headers") or {}).get("Cookie")))
        r = requests.Response()
        r.url, r._content = url, b""
        if url in srv["map"]:
            r.status_code, r.headers["Location"] = 302, srv["map"][url]
        else:
            r.status_code = 200
        return r

    monkeypatch.setattr(requests.sessions.Session, "request", request)
    return srv


@pytest.mark.parametrize("destination", [
    f"https://www.facebook.com/reel/{VIDEO_ID}/?s=fb_watch",
    f"https://www.facebook.com/watch/?v={VIDEO_ID}&_rdr",
    f"https://www.facebook.com/SomePage/videos/a-title/{VIDEO_ID}/",
    f"https://m.facebook.com/SomePage/videos/{VIDEO_ID}/",
])
def test_short_links_are_followed_to_the_reel(redirects, destination):
    redirects["map"] = {"https://fb.watch/aBcD1234_x/": "https://www.facebook.com/l/?u=x",
                        "https://www.facebook.com/l/?u=x": destination}
    assert facebook.FacebookSite.parse("fb.watch/aBcD1234_x/") == (None, facebook.Entry(VIDEO_ID, REEL_LINK))
    assert [(c[1], c[2], c[3]) for c in redirects["calls"]] == [
        ("https://fb.watch/aBcD1234_x/", False, None), ("https://www.facebook.com/l/?u=x", False, None)]


def test_short_link_that_leads_nowhere(redirects, monkeypatch):
    monkeypatch.setattr(console, "LANG", "en")
    redirects["map"] = {"https://fb.watch/abc/": "https://www.facebook.com/login/?next=x"}
    with pytest.raises(ScrapeError, match=r"^short link does not lead to a Facebook reel: https://fb.watch/abc/$"):
        facebook.FacebookSite.parse("https://fb.watch/abc/")


@pytest.mark.parametrize("link", [
    "https://www.facebook.com/SomePage",
    "https://www.facebook.com/SomePage/videos/1234567890123456/",
    "https://www.facebook.com/share/r/AbC123xyz/",
    "https://www.facebook.com/watch/",
    "https://www.facebook.com/reel/abc",
])
def test_other_facebook_links_are_not_supported(link):
    with pytest.raises(ScrapeError, match=r"^only Facebook reel links are supported \(facebook.com/reel/<id>, "
                                          r"fb.watch/\.\.\., facebook.com/watch/\?v=<id>\): "):
        facebook.FacebookSite.parse(link)


def test_link_of_an_old_failed_row():
    assert facebook.FacebookSite.link(None, VIDEO_ID) == REEL_LINK
