"""Output folders per site (Site.folder): TikTok keeps <output>/<user>/, other sites <output>/<site>/<user>/."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from tiktok_dl.core import flow
from tiktok_dl.core.site import Entry, Media, Post, Site, Verdict
from tiktok_dl.sites import tiktok


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


def test_run_saves_into_the_site_folder(tmp_path):
    assert flow.run_profile(DemoSite, "https://demo.example/p/42", make_opts(tmp_path), None, None) == 0
    assert (tmp_path / "demo" / "someone" / "metadata" / "42.json").is_file()
    assert [p.name for p in tmp_path.iterdir()] == ["demo"]
