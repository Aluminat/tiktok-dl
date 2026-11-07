"""Page-read retries, no shadowed t(), photo links in failures, closing the progress DB."""
from __future__ import annotations

import ast
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from conftest import LAUNCHER

import tiktok_dl
from tiktok_dl.core import download, records
from tiktok_dl.core.errors import ScrapeError
from tiktok_dl.core.stop import STOP
from tiktok_dl.sites import tiktok

SOURCES = sorted(Path(tiktok_dl.__file__).parent.rglob("*.py")) + [LAUNCHER]

# --------------------------------------------------------------------------- #
# TikTok._scope retries
# --------------------------------------------------------------------------- #


def test_page_read_does_not_sleep_after_last_attempt(tdl, monkeypatch):
    sleeps = []
    monkeypatch.setattr(STOP, "wait", sleeps.append)   # the wait between attempts; Ctrl+C ends it
    tt = tiktok.TikTok(wait=0)
    gets = []

    def broken(url, **kwargs):
        gets.append(url)
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(tt.s, "get", broken)
    with pytest.raises(ScrapeError, match="offline"):
        tt.item("tiktok", "123")
    assert len(gets) == 4
    assert sleeps == [1, 3, 5]   # waits between attempts only


def test_page_read_success_after_retry(tdl, monkeypatch):
    sleeps = []
    monkeypatch.setattr(STOP, "wait", sleeps.append)   # the wait between attempts; Ctrl+C ends it
    tt = tiktok.TikTok(wait=0)
    page = ('<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">'
            '{"__DEFAULT_SCOPE__": {"webapp.video-detail": {"statusCode": 0, '
            '"itemInfo": {"itemStruct": {"id": "123"}}}}}</script>')
    replies = iter([SimpleNamespace(text="", status_code=500), SimpleNamespace(text=page, status_code=200)])
    monkeypatch.setattr(tt.s, "get", lambda url, **kwargs: next(replies))
    assert tt.item("tiktok", "123") == {"id": "123"}
    assert sleeps == [1]


# --------------------------------------------------------------------------- #
# No local variable shadows the t() translation helper
# --------------------------------------------------------------------------- #


def is_t_import(node: ast.ImportFrom, alias: ast.alias) -> bool:
    """The one import of t() the package needs: `from <...>console import t`, not renamed."""
    return (node.module or "").endswith("console") and alias.name == "t" and alias.asname is None


def test_no_variable_named_t():
    offenders = []
    for source in SOURCES:
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "t" and not isinstance(node.ctx, ast.Load):
                offenders.append((source.name, node.lineno))
            elif isinstance(node, ast.arg) and node.arg == "t":
                offenders.append((source.name, node.lineno))
            elif isinstance(node, ast.ExceptHandler) and node.name == "t":
                offenders.append((source.name, node.lineno))
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    if (alias.asname or alias.name) != "t":
                        continue
                    if isinstance(node, ast.ImportFrom) and is_t_import(node, alias):
                        continue
                    offenders.append((source.name, node.lineno))
    assert offenders == []


# --------------------------------------------------------------------------- #
# scrape_profile(): failure links and the progress DB
# --------------------------------------------------------------------------- #


def make_opts(tmp_path, **overrides):
    opts = dict(
        output=str(tmp_path), session_id=None, wait=0, proxy=None, limit=None, metadata_only=False,
        batch=50, workers=1, mux_jobs=1, jobs=1, connections=1, keep_parts=False, cover=False,
        subs="none", slideshow=True, keep_images=False, slide_seconds=3.0, retry_all=False,
    )
    opts.update(overrides)
    return SimpleNamespace(**opts)


@pytest.fixture
def opened_dbs(tdl, monkeypatch):
    """Every Progress opened during the test, and no real profile page reads."""
    opened = []

    class SpyProgress(records.Progress):
        def __init__(self, path):
            super().__init__(path)
            opened.append(self)

    monkeypatch.setattr(records, "Progress", SpyProgress)
    monkeypatch.setattr(tiktok, "save_profile", lambda tt, username, out_dir: None)
    return opened


def is_closed(progress) -> bool:
    try:
        progress.db.execute("SELECT 1")
    except sqlite3.ProgrammingError:
        return True
    return False


@pytest.fixture
def page_read_fails(tdl, monkeypatch):
    def item(self, username, video_id):
        raise ScrapeError("HTTP 403, page has no data")

    monkeypatch.setattr(tiktok.TikTok, "item", item)


@pytest.mark.parametrize("kind", ["photo", "video"])
def test_failure_link_keeps_post_type(tdl, tmp_path, opened_dbs, page_read_fails, kind):
    url = f"https://www.tiktok.com/@some.user/{kind}/7300000000000000002"
    assert tdl.scrape_profile(url, make_opts(tmp_path), None, None) == 1
    failed = (tmp_path / "some.user" / "failed.txt").read_text(encoding="utf-8")
    assert url in failed


def test_failure_link_of_short_link_to_photo(tdl, tmp_path, monkeypatch, opened_dbs, page_read_fails):
    class Redirect:
        headers = {"Location": "/@some.user/photo/7300000000000000002?_r=1"}

    gets = []

    def get(url, **kwargs):
        gets.append(url)
        return Redirect()

    monkeypatch.setattr(tiktok.requests, "get", get)
    assert tdl.scrape_profile("https://vt.tiktok.com/ZSabc/", make_opts(tmp_path), None, None) == 1
    assert gets == ["https://vt.tiktok.com/ZSabc/"]   # the short link is resolved only once
    failed = (tmp_path / "some.user" / "failed.txt").read_text(encoding="utf-8")
    assert "https://www.tiktok.com/@some.user/photo/7300000000000000002" in failed


def test_failure_link_of_listed_post_defaults_to_video(tdl, tmp_path, monkeypatch, opened_dbs, page_read_fails):
    monkeypatch.setattr(tiktok, "list_posts", lambda username, limit, cookiefile: [{"id": "42"}])
    assert tdl.scrape_profile("@some.user", make_opts(tmp_path), None, None) == 1
    failed = (tmp_path / "some.user" / "failed.txt").read_text(encoding="utf-8")
    assert "https://www.tiktok.com/@some.user/video/42" in failed


def test_progress_db_closed_when_profile_finishes(tdl, tmp_path, opened_dbs, page_read_fails):
    tdl.scrape_profile("https://www.tiktok.com/@some.user/video/1", make_opts(tmp_path), None, None)
    assert len(opened_dbs) == 1
    assert is_closed(opened_dbs[0])


def test_progress_db_closed_when_nothing_left(tdl, tmp_path, opened_dbs):
    meta = tmp_path / "some.user" / "metadata"
    meta.mkdir(parents=True)
    (meta / "1.json").write_text("{}", encoding="utf-8")
    opts = make_opts(tmp_path, metadata_only=True)
    assert tdl.scrape_profile("https://www.tiktok.com/@some.user/video/1", opts, None, None) == 0
    assert len(opened_dbs) == 1
    assert is_closed(opened_dbs[0])


def test_progress_db_closed_when_profile_fails(tdl, tmp_path, monkeypatch, opened_dbs):
    item = {"id": "1", "createTime": "1700000000", "author": {"uniqueId": "some.user"},
            "video": {"playAddr": "https://example.invalid/v.mp4"}}
    monkeypatch.setattr(tiktok.TikTok, "item", lambda self, username, video_id: item)

    def crash(session, tasks, jobs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(download, "download_requests", crash)
    with pytest.raises(RuntimeError, match="disk on fire"):
        tdl.scrape_profile("https://www.tiktok.com/@some.user/video/1", make_opts(tmp_path), None, None)
    assert len(opened_dbs) == 1
    assert is_closed(opened_dbs[0])


def test_progress_close(tdl, tmp_path):
    progress = records.Progress(tmp_path / ".progress.db")
    progress.mark("1", "done", file="a.mp4")
    progress.close()
    assert is_closed(progress)
    again = records.Progress(tmp_path / ".progress.db")
    try:
        assert again.db.execute("SELECT status FROM posts WHERE id = '1'").fetchone() == ("done",)
    finally:
        again.close()
