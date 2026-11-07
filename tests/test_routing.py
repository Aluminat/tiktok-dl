"""Which site a target goes to (sites.find_site), Instagram and Facebook links failing with "not
supported yet", and output folders per site (Site.folder). No test opens a link."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from tiktok_dl import cli
from tiktok_dl.core import console, flow, tools
from tiktok_dl.core.errors import ScrapeError
from tiktok_dl.core.site import Entry, Media, Post, Site, Verdict
from tiktok_dl.sites import BY_NAME, SITES, facebook, find_site, instagram, tiktok

INSTAGRAM_LINKS = [
    "https://www.instagram.com/reel/C1a2B3c4D5e/",
    "https://instagram.com/reel/C1a2B3c4D5e/?igsh=MWQ1ZGUxMzBkMA==",
    "https://www.instagram.com/reels/C1a2B3c4D5e/",
    "https://www.instagram.com/p/C1a2B3c4D5e/?img_index=2",
    "https://www.instagram.com/tv/C1a2B3c4D5e/",
    "https://m.instagram.com/p/C1a2B3c4D5e/",
    "https://www.instagram.com/some.user/",
    "https://www.instagram.com/some.user?hl=en",
    "https://www.instagram.com/some.user/reel/C1a2B3c4D5e/",
    "http://instagram.com",
    "instagram.com/some.user",
    "www.instagram.com/p/C1a2B3c4D5e",
    "  HTTPS://WWW.INSTAGRAM.COM/REEL/C1A2B3C4D5E/ \n",
]
FACEBOOK_LINKS = [
    "https://www.facebook.com/reel/1234567890123456",
    "https://facebook.com/reel/1234567890123456/?mibextid=rS40aB7S9Ucbxw6v",
    "https://m.facebook.com/reel/1234567890123456",
    "https://web.facebook.com/reel/1234567890123456",
    "https://www.facebook.com/watch/?v=1234567890123456",
    "https://www.facebook.com/watch?v=1234567890123456&ref=sharing",
    "https://www.facebook.com/SomePage/videos/1234567890123456/",
    "https://www.facebook.com/SomePage/reels/1234567890123456/",
    "https://www.facebook.com/SomePage",
    "https://www.facebook.com/share/r/AbC123xyz/",
    "https://www.facebook.com/share/v/AbC123xyz/",
    "https://fb.watch/aBcD1234_x/",
    "fb.watch/aBcD1234_x",
    "facebook.com/reel/1234567890123456",
    "HTTPS://FB.WATCH/ABCD/",
]
TIKTOK_TARGETS = ["@some.user", "some.user", "instagram", "facebook", "https://www.tiktok.com/@some.user",
                  "https://vt.tiktok.com/ZSabc/", "https://www.tiktok.com/@some.user/photo/1",
                  # a bare host with no scheme or path is a valid TikTok username, as it was before
                  "fb.watch", "a.instagram.com", "my.facebook.com"]
LOOKALIKES = ["https://notinstagram.com/p/x", "https://instagram.com.evil.example/p/x",
              "https://fakefacebook.com/reel/1", "https://facebook.com.evil.example/reel/1",
              "https://fb.watcher.example/x", "https://example.com/?u=instagram.com",
              "https://tiktok.com.example/@some.user"]


def make_opts(tmp_path, **overrides):
    opts = SimpleNamespace(output=str(tmp_path), session_id=None, wait=0, proxy=None, limit=None,
                           metadata_only=True, batch=50, workers=1, mux_jobs=1, jobs=1, connections=1,
                           cover=False, subs="none", slideshow=True, keep_images=False, slide_seconds=3.0,
                           keep_parts=False, retry_all=False)
    for key, value in overrides.items():
        setattr(opts, key, value)
    return opts


class DemoPost(Post):
    id = "42"
    link = "https://demo.example/p/42"
    author = "someone"
    created = datetime(2024, 1, 1, tzinfo=timezone.utc)
    caption = "hi"
    counts = {}

    def media(self) -> Media:
        return Media()


class DemoSite(Site):
    """A site with no folder rule of its own: the default <site name>/<profile>."""
    name = "demo"
    label = "Demo"

    def __init__(self, profile, session_id, wait, proxy):
        self.client = SimpleNamespace(cookie_header=lambda: "", stop=lambda: None)

    @staticmethod
    def matches(url):
        return url.startswith("https://demo.example/")

    @staticmethod
    def parse(target):
        return "someone", Entry("42", "https://demo.example/p/42")

    def save_profile(self, out_dir):
        return None

    def list_posts(self, limit, work):
        return []

    def read_post(self, post_id):
        return DemoPost({"id": post_id})

    def link(self, post_id):
        return f"https://demo.example/p/{post_id}"

    def csv_row(self, post):
        return {"id": post.id}

    def mux_tags(self, post):
        return {}

    def classify(self, answer):
        return Verdict.OK


def test_default_folder_is_site_then_profile():
    assert DemoSite.folder("someone") == Path("demo", "someone")


def test_tiktok_folder_has_no_site_folder():
    assert tiktok.TikTokSite.folder("some.user") == Path("some.user")


def test_instagram_and_facebook_folders():
    assert instagram.InstagramSite.folder("some.user") == Path("instagram", "some.user")
    assert facebook.FacebookSite.folder("SomePage") == Path("facebook", "SomePage")


def test_run_saves_into_the_site_folder(tmp_path):
    assert flow.run_profile(DemoSite, "https://demo.example/p/42", make_opts(tmp_path), None, None) == 0
    assert (tmp_path / "demo" / "someone" / "metadata" / "42.json").is_file()
    assert [p.name for p in tmp_path.iterdir()] == ["demo"]


# --------------------------------------------------------------------------- #
# Which site a link belongs to
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("url", INSTAGRAM_LINKS)
def test_instagram_links_go_to_instagram(url):
    assert [site for site in SITES if site.matches(url)] == [instagram.InstagramSite]
    assert find_site(url) is instagram.InstagramSite


@pytest.mark.parametrize("url", FACEBOOK_LINKS)
def test_facebook_links_go_to_facebook(url):
    assert [site for site in SITES if site.matches(url)] == [facebook.FacebookSite]
    assert find_site(url) is facebook.FacebookSite


@pytest.mark.parametrize("target", TIKTOK_TARGETS + LOOKALIKES)
def test_everything_else_still_goes_to_tiktok(target):
    assert not instagram.InstagramSite.matches(target)
    assert not facebook.FacebookSite.matches(target)
    assert find_site(target) is tiktok.TikTokSite


@pytest.mark.parametrize("target", LOOKALIKES)
def test_lookalikes_are_no_site_link(target):
    # They reach TikTok only as the fallback for a non-link, never because TikTok claims them.
    assert [site for site in SITES if site.matches(target)] == []


@pytest.fixture
def logged(monkeypatch):
    lines: list[str] = []
    monkeypatch.setattr(console, "log", lambda msg="", err=False: lines.append(msg))
    return lines


@pytest.mark.parametrize(("url", "label"), [(INSTAGRAM_LINKS[0], "Instagram"), (FACEBOOK_LINKS[0], "Facebook"),
                                            ("https://fb.watch/aBcD1234_x/", "Facebook")])
def test_unsupported_link_fails_before_anything_is_written(tmp_path, url, label):
    with pytest.raises(ScrapeError) as e:
        cli.scrape_profile(url, make_opts(tmp_path), None, None)
    assert str(e.value) == f"{label} is not supported yet: {url}"
    assert list(tmp_path.iterdir()) == []


def test_unsupported_message_in_vietnamese(tmp_path, monkeypatch):
    monkeypatch.setattr(console, "LANG", "vi")
    with pytest.raises(ScrapeError, match="^chưa hỗ trợ tải từ Instagram: https://www.instagram.com/p/x/$"):
        cli.scrape_profile(" https://www.instagram.com/p/x/ ", make_opts(tmp_path), None, None)


@pytest.fixture
def run_main(monkeypatch, logged):
    """Run main() with these arguments. Instagram/Facebook targets run for real (they fail before any
    request); TikTok targets are only recorded. Returns (exit code, (site name, target) per target)."""
    monkeypatch.setattr(console, "LANG", console.LANG)        # main() sets these globals: restore them
    monkeypatch.setattr(console, "_COLOR", console._COLOR)
    monkeypatch.setattr(tools, "find_tool", lambda name: None)
    monkeypatch.setattr(tools, "find_ffmpeg", lambda: None)
    real_run = flow.run_profile

    def run(*args):
        tried = []

        def run_profile(site_cls, target, *rest):
            tried.append((site_cls.name, target))
            return 0 if site_cls is tiktok.TikTokSite else real_run(site_cls, target, *rest)

        monkeypatch.setattr(flow, "run_profile", run_profile)
        monkeypatch.setattr(cli.sys, "argv", ["tiktok-dl", *args, "--lang", "en", "--no-color"])
        with pytest.raises(SystemExit) as e:
            cli.main()
        return e.value.code, tried

    return run


def test_main_reports_unsupported_links_and_goes_on(run_main, logged, tmp_path):
    ig, fb = INSTAGRAM_LINKS[0], FACEBOOK_LINKS[-1]
    code, tried = run_main(ig, fb, "@some.user", "-o", str(tmp_path))
    assert code == 1
    assert tried == [("instagram", ig), ("facebook", fb), ("tiktok", "@some.user")]
    errors = [line for line in logged if line.startswith("Error: ")]
    assert errors == [f"Error: Instagram is not supported yet: {ig}", f"Error: Facebook is not supported yet: {fb}"]
    assert list(tmp_path.iterdir()) == []


# --------------------------------------------------------------------------- #
# --site
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("target", "name", "expected"),
    [
        ("some.user", None, tiktok.TikTokSite),
        ("some.user", "tiktok", tiktok.TikTokSite),
        ("@some.user", "instagram", instagram.InstagramSite),
        ("SomePage", "facebook", facebook.FacebookSite),
        ("https://www.tiktok.com/@some.user/video/1", "tiktok", tiktok.TikTokSite),
        ("https://vt.tiktok.com/ZSabc/", "tiktok", tiktok.TikTokSite),
        ("https://www.instagram.com/reel/C1a2B3c4D5e/", "instagram", instagram.InstagramSite),
        ("https://fb.watch/aBcD1234_x/", "facebook", facebook.FacebookSite),
    ],
)
def test_site_option_names_the_site_of_usernames(target, name, expected):
    assert find_site(target, name) is expected


@pytest.mark.parametrize(
    ("target", "name", "message"),
    [
        ("https://www.tiktok.com/@some.user", "instagram",
         "this link is for TikTok, not Instagram (--site instagram): https://www.tiktok.com/@some.user"),
        (" https://www.instagram.com/p/x/ ", "tiktok",
         "this link is for Instagram, not TikTok (--site tiktok): https://www.instagram.com/p/x/"),
        ("https://fb.watch/abc/", "instagram",
         "this link is for Facebook, not Instagram (--site instagram): https://fb.watch/abc/"),
    ],
)
def test_link_from_another_site_than_the_site_option(target, name, message):
    with pytest.raises(ScrapeError) as e:
        find_site(target, name)
    assert str(e.value) == message


@pytest.mark.parametrize("name", ["instagram", "facebook"])
@pytest.mark.parametrize("target", LOOKALIKES + ["https://www.youtube.com/watch?v=1", "some.user/reels"])
def test_unknown_link_is_not_a_username_of_the_site_option(target, name):
    label = BY_NAME[name].label
    with pytest.raises(ScrapeError) as e:
        find_site(target, name)
    assert str(e.value) == f"not a link or username for {label} (--site {name}): {target}"


def test_unknown_link_with_site_option_in_vietnamese(monkeypatch):
    monkeypatch.setattr(console, "LANG", "vi")
    with pytest.raises(ScrapeError) as e:
        find_site("https://notinstagram.com/p/x", "instagram")
    assert str(e.value) == "không phải link hay username Instagram (--site instagram): https://notinstagram.com/p/x"


def test_site_mismatch_in_vietnamese(monkeypatch):
    monkeypatch.setattr(console, "LANG", "vi")
    with pytest.raises(ScrapeError) as e:
        find_site("https://vt.tiktok.com/ZSabc/", "facebook")
    assert str(e.value) == "link này của TikTok, không phải Facebook (--site facebook): https://vt.tiktok.com/ZSabc/"


def test_main_site_option(run_main, logged, tmp_path):
    link = "https://www.tiktok.com/@some.user"
    code, tried = run_main("--site", "instagram", "@some.user", link, "-o", str(tmp_path))
    assert code == 1
    assert tried == [("instagram", "@some.user")]   # the TikTok link fails before any site runs
    errors = [line for line in logged if line.startswith("Error: ")]
    assert errors == ["Error: Instagram is not supported yet: @some.user",
                      f"Error: this link is for TikTok, not Instagram (--site instagram): {link}"]
    assert list(tmp_path.iterdir()) == []


def test_main_site_tiktok_routes_usernames_to_tiktok(run_main, logged):
    assert run_main("--site", "tiktok", "some.user", "https://vm.tiktok.com/ZMabc/") == \
        (0, [("tiktok", "some.user"), ("tiktok", "https://vm.tiktok.com/ZMabc/")])
    assert not [line for line in logged if line.startswith("Error: ")]


def test_main_rejects_an_unknown_site(run_main, capsys):
    code, tried = run_main("--site", "youtube", "some.user")
    assert code == 2 and tried == []
    assert "invalid choice: 'youtube'" in capsys.readouterr().err


def test_without_site_links_from_any_site_are_accepted():
    # Without --site a link goes to its own site; --site tiktok turns the same link into a mismatch.
    assert find_site(INSTAGRAM_LINKS[0]) is instagram.InstagramSite
    with pytest.raises(ScrapeError, match="not TikTok"):
        find_site(INSTAGRAM_LINKS[0], "tiktok")
