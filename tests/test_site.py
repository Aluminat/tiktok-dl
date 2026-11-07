"""The site interface (core/site.py) and TikTok behind it: links, parsing, classify, posts and the
download plan, and the aria2c Referer the core gets from the site."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from tiktok_dl import cli
from tiktok_dl.core import download
from tiktok_dl.core.errors import ScrapeError
from tiktok_dl.core.site import Entry, Verdict
from tiktok_dl.core.util import UA
from tiktok_dl.sites import find_site, tiktok


class RecordingPopen:
    """Stands in for aria2c: records each command line and downloads nothing."""

    def __init__(self):
        self.cmds: list[list[str]] = []

    def __call__(self, cmd, **kwargs):
        self.cmds.append(list(cmd))
        return SimpleNamespace(communicate=lambda data=None, timeout=None: (b"", b""), terminate=lambda: None)


@pytest.fixture
def popen(monkeypatch):
    fake = RecordingPopen()
    monkeypatch.setattr(download.subprocess, "Popen", fake)
    return fake


def one_task(tmp_path):
    return [(["https://cdn.example/a.mp4"], tmp_path / "a" / "video.mp4", {})]


def test_aria2c_referer_comes_right_after_the_user_agent(tmp_path, popen):
    download.download_aria2("aria2c", one_task(tmp_path), "", 1, 1, 0, "https://x/")
    cmd = popen.cmds[0]
    i = cmd.index(f"--user-agent={UA}")
    assert cmd[i + 1:i + 3] == ["--referer=https://x/", "--header=Accept: */*"]


@pytest.mark.parametrize("referer", [(), ("",)], ids=["six-arguments", "empty"])
def test_aria2c_without_referer(tmp_path, popen, referer):
    download.download_aria2("aria2c", one_task(tmp_path), "", 1, 1, 0, *referer)
    cmd = popen.cmds[0]
    assert not any(arg.startswith("--referer") for arg in cmd)
    i = cmd.index(f"--user-agent={UA}")
    assert cmd[i + 1] == "--header=Accept: */*"


def test_tiktok_run_sends_the_tiktok_referer(tmp_path, monkeypatch, popen):
    class FakeTikTok:
        def __init__(self, *args):
            self.s = None

        def item(self, username, video_id):
            return {"id": video_id, "createTime": 1700000000, "author": {"uniqueId": username},
                    "video": {"playAddr": f"https://cdn.example/{video_id}.mp4"}}

        def cookie_header(self):
            return ""

        def stop(self):
            pass

    monkeypatch.setattr(tiktok, "TikTok", FakeTikTok)
    monkeypatch.setattr(tiktok, "save_profile", lambda *a: None)
    opts = SimpleNamespace(output=str(tmp_path), session_id=None, wait=0, proxy=None, limit=None,
                           metadata_only=False, batch=50, workers=1, mux_jobs=1, jobs=1, connections=1,
                           cover=False, subs="none", slideshow=True, keep_images=False, slide_seconds=3.0,
                           keep_parts=False, retry_all=False)
    cli.scrape_profile("https://www.tiktok.com/@someone/video/1", opts, "aria2c", None)
    assert popen.cmds
    assert all(cmd.count("--referer=https://www.tiktok.com/") == 1 for cmd in popen.cmds)


# --------------------------------------------------------------------------- #
# Which site a target belongs to
# --------------------------------------------------------------------------- #

TIKTOK_LINKS = ["https://www.tiktok.com/@x", "https://vt.tiktok.com/ZSabc/", "https://vm.tiktok.com/ZMabc/",
                "www.tiktok.com/@x/video/1", "tiktok.com/@x", "HTTPS://WWW.TIKTOK.COM/@X"]
OTHER_TARGETS = ["@x", "x", "https://example.com/foo", "https://notiktok.com/@x"]


@pytest.mark.parametrize("url", TIKTOK_LINKS)
def test_tiktok_links_match(url):
    assert tiktok.TikTokSite.matches(url)


@pytest.mark.parametrize("target", OTHER_TARGETS)
def test_other_targets_do_not_match(target):
    assert not tiktok.TikTokSite.matches(target)


@pytest.mark.parametrize("target", TIKTOK_LINKS + OTHER_TARGETS)
def test_find_site_falls_back_to_tiktok(target):
    assert find_site(target) is tiktok.TikTokSite


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("@some.user", ("some.user", None)),
        ("https://www.tiktok.com/@some.user", ("some.user", None)),
        ("https://www.tiktok.com/@some.user/video/7300000000000000001?is_from_webapp=1",
         ("some.user", Entry("7300000000000000001", "https://www.tiktok.com/@some.user/video/7300000000000000001"))),
        ("https://www.tiktok.com/@some.user/photo/7300000000000000002",
         ("some.user", Entry("7300000000000000002", "https://www.tiktok.com/@some.user/photo/7300000000000000002"))),
    ],
)
def test_parse(target, expected):
    assert tiktok.TikTokSite.parse(target) == expected


def test_link_of_an_id_is_a_video_link():
    assert tiktok.TikTokSite("u", None, 0, None).link("5") == "https://www.tiktok.com/@u/video/5"


def test_repr_does_not_show_the_session_id():
    site = tiktok.TikTokSite("u", "SECRET", 0, None)
    assert "SECRET" not in repr(site)
    assert "SECRET" not in repr(site.client)


# --------------------------------------------------------------------------- #
# classify
# --------------------------------------------------------------------------- #

CHALLENGE = '<html><div class="_wafchallengeid"></div><script src="/waf-aiso/aiso.js"></script></html>'


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (SimpleNamespace(status_code=429, text=CHALLENGE), Verdict.BLOCKED),
        (SimpleNamespace(status_code=200, text=CHALLENGE), Verdict.BLOCKED),
        (SimpleNamespace(status_code=429, text=""), Verdict.RATE_LIMITED),
        (SimpleNamespace(status_code=403, text=""), Verdict.RATE_LIMITED),
        (SimpleNamespace(status_code=200, text=""), Verdict.OK),
        (SimpleNamespace(status_code=404, text=""), Verdict.OK),
        (ScrapeError("post does not exist or was deleted", 10204), Verdict.PERMANENT),
        (ScrapeError("post is private", 10216), Verdict.OK),
        (ScrapeError("connection reset"), Verdict.OK),
    ],
    ids=["challenge-429", "challenge-200", "429", "403", "200", "404", "deleted", "private", "no-code"],
)
def test_classify(answer, expected):
    assert tiktok.classify(answer) is expected
    assert tiktok.TikTokSite("u", None, 0, None).classify(answer) is expected


# --------------------------------------------------------------------------- #
# Posts, media and the download plan
# --------------------------------------------------------------------------- #

SUBTITLES = [
    {"Url": "https://cdn/en.vtt", "LanguageCodeName": "eng-US", "Source": "ASR"},
    {"Url": "https://cdn/en2.vtt", "LanguageCodeName": "eng-GB", "Source": "ASR"},
    {"Url": "https://cdn/vi.vtt", "LanguageCodeName": "vie-VN", "Source": "MT"},
    {"LanguageCodeName": "fra-FR", "Source": "ASR"},
]
VIDEO = {
    "id": "7300000000000000001", "createTime": "1700000000", "desc": "Hello #fyp",
    "author": {"uniqueId": "someone"},
    "stats": {"playCount": 100, "diggCount": 10, "commentCount": 2, "shareCount": 1, "collectCount": 3},
    "statsV2": {"repostCount": "4"},
    "video": {"playAddr": "https://cdn/a.mp4", "downloadAddr": "https://cdn/wm.mp4",
              "PlayAddrStruct": {"UrlList": ["https://cdn/a.mp4", "https://cdn/b.mp4"], "DataSize": "2048"},
              "cover": "https://cdn/cover.jpg", "originCover": "https://cdn/origin.jpg",
              "subtitleInfos": SUBTITLES},
}
PHOTO = {
    "id": "7300000000000000002", "createTime": 1700000000, "author": {"uniqueId": "someone"},
    "imagePost": {"images": [{"imageURL": {"urlList": ["https://cdn/1.jpg", "https://cdn/1b.jpg",
                                                       "https://cdn/1.jpg"]}},
                             {"imageURL": {"urlList": ["https://cdn/2.jpg"]}}]},
    "music": {"playUrl": "https://cdn/music.mp3"},
}
AD = {"id": "7300000000000000003", "author": {"uniqueId": "someone"}, "video": {}, "isAd": True}


def test_post_fields():
    post = tiktok.TikTokPost(VIDEO)
    assert post.id == "7300000000000000001"
    assert post.link == "https://www.tiktok.com/@someone/video/7300000000000000001"
    assert post.author == "someone"
    assert post.created == datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)
    assert post.caption == "Hello #fyp"
    assert post.counts == {"plays": 100, "likes": 10, "comments": 2, "shares": 1, "saves": 3, "reposts": 4}
    assert tiktok.TikTokPost(PHOTO).link == "https://www.tiktok.com/@someone/photo/7300000000000000002"
    assert tiktok.TikTokPost({"id": 5}).link == "https://www.tiktok.com/@_/video/5"


def test_video_media():
    media = tiktok.media(VIDEO)
    assert media.video == ["https://cdn/a.mp4", "https://cdn/b.mp4", "https://cdn/wm.mp4"]
    assert media.sizes == {"https://cdn/a.mp4": 2048, "https://cdn/b.mp4": 2048}
    assert media.cover == "https://cdn/origin.jpg"
    assert [(s.url, s.lang, s.machine) for s in media.subtitles] == [
        ("https://cdn/en.vtt", "eng", False), ("https://cdn/en2.vtt", "eng", False),
        ("https://cdn/vi.vtt", "vie", True), (None, "fra", False)]
    assert media.images == [] and media.unavailable is None and not media.permanent


def test_photo_media():
    media = tiktok.media(PHOTO)
    assert media.images == [["https://cdn/1.jpg", "https://cdn/1b.jpg"], ["https://cdn/2.jpg"]]
    assert media.audio == "https://cdn/music.mp3"
    assert media.video == []


def test_ad_media_has_no_link_and_is_permanent():
    media = tiktok.media(AD)
    assert media.video == [] and media.permanent
    assert "ad / TikTok Shop" in media.unavailable


def plan_opts(**kw):
    return SimpleNamespace(**{"cover": True, "subs": "original", **kw})


@pytest.mark.parametrize(
    ("subs", "expected"),
    [
        ("original", [("https://cdn/en.vtt", "sub_0.vtt", "eng")]),   # first per language, no machine translation
        ("all", [("https://cdn/en.vtt", "sub_0.vtt", "eng"), ("https://cdn/vi.vtt", "sub_1.vtt", "vie")]),
        ("none", []),
    ],
)
def test_download_subtitle_choice(tmp_path, subs, expected):
    plan = download.Download(tiktok.TikTokPost(VIDEO), tmp_path, plan_opts(subs=subs))
    tasks = [(urls, dest) for urls, dest, _ in plan.tasks if dest.suffix == ".vtt"]
    assert [(urls[0], dest.name) for urls, dest in tasks] == [(url, name) for url, name, _ in expected]
    assert [(dest.name, lang) for dest, lang in plan.subs] == [(name, lang) for _, name, lang in expected]


@pytest.mark.parametrize("cover", [True, False])
def test_download_cover_only_when_asked(tmp_path, cover):
    plan = download.Download(tiktok.TikTokPost(VIDEO), tmp_path, plan_opts(cover=cover))
    covers = [urls for urls, dest, _ in plan.tasks if dest.name == "cover.img"]
    assert covers == ([["https://cdn/origin.jpg"]] if cover else [])


def test_download_of_a_photo_post(tmp_path):
    plan = download.Download(tiktok.TikTokPost(PHOTO), tmp_path, plan_opts())
    assert plan.is_slide
    assert [(urls, dest.name) for urls, dest, _ in plan.tasks] == [
        (["https://cdn/1.jpg", "https://cdn/1b.jpg"], "img_01.jpg"), (["https://cdn/2.jpg"], "img_02.jpg"),
        (["https://cdn/music.mp3"], "audio.mp3")]


def test_download_of_a_hidden_ad_post(tmp_path):
    plan = download.Download(tiktok.TikTokPost(AD), tmp_path, plan_opts())
    assert plan.permanent and "ad / TikTok Shop" in plan.hidden
    assert plan.tasks == []
    assert plan.url == "https://www.tiktok.com/@someone/video/7300000000000000003"
