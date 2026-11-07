"""A malformed post or profile page fails one post, not the whole run (issue #4).

TikTok pages are mocked at the HTTP level (requests.Session.get returns HTML with the
embedded rehydration JSON), downloads are stubbed, and ffmpeg / aria2c are not used.
"""
from __future__ import annotations

import json
import sqlite3
import types

import pytest
import requests

USER = "someone"
GOOD_IDS = ["7300000000000000001", "7300000000000000003"]
BAD_ID = "7300000000000000002"
FAKE_MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 2048


def page(scope: str, data: dict) -> str:
    blob = json.dumps({"__DEFAULT_SCOPE__": {scope: data}})
    return f'<html><script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">{blob}</script></html>'


def good_item(pid: str) -> dict:
    return {
        "id": pid,
        "desc": f"post {pid}",
        "createTime": 1700000000,
        "author": {"uniqueId": USER, "nickname": "Someone"},
        "video": {"playAddr": f"https://v16.example.invalid/{pid}.mp4"},
    }


def video_page(item_info) -> str:
    data = {"statusCode": 0}
    if item_info is not None:
        data["itemInfo"] = item_info
    return page("webapp.video-detail", data)


def user_page(with_user_info: bool = True) -> str:
    data = {"statusCode": 0}
    if with_user_info:
        data["userInfo"] = {"user": {"uniqueId": USER, "nickname": "Someone"}, "stats": {}}
    return page("webapp.user-detail", data)


class FakeResponse:
    def __init__(self, text: str = "", status_code: int = 200):
        self.text = text
        self.status_code = status_code
        self.ok = status_code == 200
        self.content = text.encode()


def mock_pages(monkeypatch, pages: dict) -> None:
    """Serve `pages[url]` from every requests.Session; unknown URLs return a 404."""
    def get(self, url, **kwargs):
        return FakeResponse(pages[url]) if url in pages else FakeResponse(status_code=404)

    monkeypatch.setattr(requests.Session, "get", get)


def post_url(pid: str) -> str:
    return f"https://www.tiktok.com/@{USER}/video/{pid}"


@pytest.fixture
def run(tdl, tmp_path, monkeypatch):
    """Run scrape_profile on a 3-post profile; `pages` overrides the mocked page per URL."""
    def fake_download(session, tasks, jobs):
        for _, dest, _ in tasks:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(FAKE_MP4)

    entries = [{"id": pid} for pid in (GOOD_IDS[0], BAD_ID, GOOD_IDS[1])]
    monkeypatch.setattr(tdl, "list_posts", lambda username, limit, cookiefile: entries)
    monkeypatch.setattr(tdl, "download_requests", fake_download)
    opts = types.SimpleNamespace(
        output=str(tmp_path), session_id=None, wait=0, proxy=None, limit=None, metadata_only=False,
        workers=2, batch=50, mux_jobs=1, jobs=2, connections=1, cover=False, subs="none",
        slideshow=True, keep_images=False, slide_seconds=3.0, keep_parts=False,
    )

    def go(bad_page: str | None = None, profile_page: str | None = None) -> int:
        pages = {f"https://www.tiktok.com/@{USER}": profile_page or user_page()}
        for pid in GOOD_IDS:
            pages[post_url(pid)] = video_page({"itemStruct": good_item(pid)})
        pages[post_url(BAD_ID)] = bad_page if bad_page is not None else video_page({"itemStruct": good_item(BAD_ID)})
        mock_pages(monkeypatch, pages)
        return tdl.scrape_profile(USER, opts, None, None)

    return go, tmp_path / USER


def db_row(out_dir, pid):
    db = sqlite3.connect(out_dir / ".progress.db")
    try:
        return db.execute("SELECT status, error FROM posts WHERE id = ?", (pid,)).fetchone()
    finally:
        db.close()


def assert_good_posts_downloaded(out_dir):
    for pid in GOOD_IDS:
        assert list(out_dir.glob(f"*_{pid}_*.mp4")), f"post {pid} was not downloaded"
        assert db_row(out_dir, pid)[0] == "done"


@pytest.mark.parametrize(
    "item_info",
    [None, {}, {"itemStruct": None}, {"itemStruct": {}}, {"itemStruct": "oops"}, ["not", "a", "dict"]],
    ids=["no-itemInfo", "no-itemStruct", "null-itemStruct", "empty-itemStruct", "string-itemStruct", "list"],
)
def test_item_missing_keys_raise_scrape_error(tdl, monkeypatch, item_info):
    mock_pages(monkeypatch, {post_url(BAD_ID): video_page(item_info)})
    with pytest.raises(tdl.ScrapeError, match="unexpected page format"):
        tdl.TikTok(wait=0).item(USER, BAD_ID)


def test_user_missing_user_info_raises_scrape_error(tdl, monkeypatch):
    mock_pages(monkeypatch, {f"https://www.tiktok.com/@{USER}": user_page(with_user_info=False)})
    with pytest.raises(tdl.ScrapeError, match="unexpected page format"):
        tdl.TikTok(wait=0).user(USER)


def test_post_page_without_item_struct_fails_one_post(run, capsys):
    go, out_dir = run
    failed = go(bad_page=video_page({}))
    out, err = capsys.readouterr()

    assert failed == 1
    assert_good_posts_downloaded(out_dir)
    status, error = db_row(out_dir, BAD_ID)
    assert status == "error" and "unexpected page format" in error
    failed_txt = (out_dir / "failed.txt").read_text(encoding="utf-8")
    assert post_url(BAD_ID) in failed_txt and "unexpected page format" in failed_txt
    assert post_url(BAD_ID) in out and "unexpected page format" in out
    assert "Traceback" not in out + err


def test_post_with_odd_field_types_fails_one_post(run, capsys):
    go, out_dir = run
    weird = good_item(BAD_ID) | {"video": "not a dict"}
    failed = go(bad_page=video_page({"itemStruct": weird}))
    out, err = capsys.readouterr()

    assert failed == 1
    assert_good_posts_downloaded(out_dir)
    assert "unexpected page format" in db_row(out_dir, BAD_ID)[1]
    assert post_url(BAD_ID) in (out_dir / "failed.txt").read_text(encoding="utf-8")
    assert "Traceback" not in out + err


def test_profile_page_without_user_info_warns_and_continues(run, capsys):
    go, out_dir = run
    failed = go(profile_page=user_page(with_user_info=False))
    out, err = capsys.readouterr()

    assert failed == 0
    assert "Could not read profile info" in out and "continuing" in out
    assert not (out_dir / "profile.json").exists()
    assert_good_posts_downloaded(out_dir)
    assert db_row(out_dir, BAD_ID)[0] == "done"
    assert "Traceback" not in out + err


def test_unexpected_error_while_finalizing_fails_one_post(tdl, run, monkeypatch, capsys):
    go, out_dir = run
    real_finalize = tdl.finalize

    def flaky_finalize(post, *args, **kwargs):
        if post.id == BAD_ID:
            raise RuntimeError("boom")
        return real_finalize(post, *args, **kwargs)

    monkeypatch.setattr(tdl, "finalize", flaky_finalize)
    failed = go()
    out, err = capsys.readouterr()

    assert failed == 1
    assert_good_posts_downloaded(out_dir)
    status, error = db_row(out_dir, BAD_ID)
    assert status == "error" and "RuntimeError: boom" in error
    failed_txt = (out_dir / "failed.txt").read_text(encoding="utf-8")
    assert post_url(BAD_ID) in failed_txt and "RuntimeError: boom" in failed_txt
    assert "Traceback" not in out + err
