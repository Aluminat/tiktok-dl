"""failed.txt is rebuilt from the progress database (issue #2)."""
from __future__ import annotations

import functools
import sqlite3
from types import SimpleNamespace

import pytest

from tiktok_dl import cli
from tiktok_dl.core import download, mux, records
from tiktok_dl.core.errors import ScrapeError
from tiktok_dl.sites import tiktok

USER = "someone"
VIDEO_URL = f"https://www.tiktok.com/@{USER}/video/111"
PHOTO_URL = f"https://www.tiktok.com/@{USER}/photo/222"
LINK = functools.partial(tiktok.post_link, USER)   # the site's link for rows without one


@pytest.fixture
def progress(tmp_path):
    p = records.Progress(tmp_path / ".progress.db")
    yield p
    p.db.close()   # Windows keeps the file locked otherwise


def read_failed(tmp_path) -> str:
    return (tmp_path / "failed.txt").read_text(encoding="utf-8")


def test_rebuild_from_mixed_statuses(tmp_path, progress):
    progress.mark("111", "error", error="video broke", url=VIDEO_URL)
    progress.mark("222", "error", error="photo broke", url=PHOTO_URL)
    progress.mark("333", "done", file="clip.mp4")
    progress.mark("444", "metadata")

    assert records.write_failed_file(progress, tmp_path, USER, LINK) == 2
    text = read_failed(tmp_path)
    assert f"{VIDEO_URL}\n    video broke" in text
    assert f"{PHOTO_URL}\n    photo broke" in text
    assert "/video/222" not in text
    assert "333" not in text
    assert "444" not in text


def test_post_that_now_succeeds_disappears(tmp_path, progress):
    progress.mark("111", "error", error="video broke", url=VIDEO_URL)
    progress.mark("222", "error", error="photo broke", url=PHOTO_URL)
    records.write_failed_file(progress, tmp_path, USER, LINK)

    progress.mark("111", "done", file="clip.mp4")
    assert records.write_failed_file(progress, tmp_path, USER, LINK) == 1
    text = read_failed(tmp_path)
    assert "111" not in text
    assert PHOTO_URL in text


def test_file_removed_when_nothing_fails(tmp_path, progress):
    progress.mark("111", "error", error="video broke", url=VIDEO_URL)
    records.write_failed_file(progress, tmp_path, USER, LINK)
    assert (tmp_path / "failed.txt").exists()

    progress.mark("111", "done", file="clip.mp4")
    assert records.write_failed_file(progress, tmp_path, USER, LINK) == 0
    assert not (tmp_path / "failed.txt").exists()


def test_empty_database_writes_no_file(tmp_path, progress):
    assert records.write_failed_file(progress, tmp_path, USER, LINK) == 0
    assert not (tmp_path / "failed.txt").exists()


def test_old_database_without_url_column(tmp_path):
    path = tmp_path / ".progress.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE posts (id TEXT PRIMARY KEY, status TEXT, file TEXT, error TEXT, updated TEXT)")
    old.execute("INSERT INTO posts VALUES ('555', 'error', NULL, 'old reason', '2024-01-01T00:00:00')")
    old.commit()
    old.close()

    progress = records.Progress(path)
    try:
        progress.mark("222", "error", error="photo broke", url=PHOTO_URL)
        assert records.write_failed_file(progress, tmp_path, USER, LINK) == 2
        text = read_failed(tmp_path)
        assert f"https://www.tiktok.com/@{USER}/video/555\n    old reason" in text
        assert PHOTO_URL in text
    finally:
        progress.db.close()


# --------------------------------------------------------------------------- #
# Whole run: a single-video run must not wipe an earlier run's failures
# --------------------------------------------------------------------------- #

def make_item(pid: str) -> dict:
    return {"id": pid, "createTime": 1700000000, "desc": "clip",
            "author": {"uniqueId": USER}, "video": {"playAddr": f"https://cdn.example/{pid}.mp4"}}


class FakeTikTok:
    def __init__(self, *args, **kwargs):
        self.s = None

    def item(self, username, video_id):
        return make_item(video_id)

    def cookie_header(self):
        return ""

    def stop(self):
        pass


def make_opts(tmp_path) -> SimpleNamespace:
    return SimpleNamespace(output=str(tmp_path), session_id=None, wait=0, proxy=None, limit=None,
                           metadata_only=False, workers=1, batch=10, mux_jobs=1, keep_parts=False,
                           jobs=1, connections=1, cover=False, subs="none", retry_all=False)


def test_single_video_run_keeps_earlier_failures(tmp_path, monkeypatch, capsys):
    out_dir = tmp_path / USER
    out_dir.mkdir()
    earlier = records.Progress(out_dir / ".progress.db")
    earlier.mark("111", "error", error="video broke", url=VIDEO_URL)
    earlier.mark("222", "error", error="photo broke", url=PHOTO_URL)
    earlier.db.close()

    def fake_finalize(post, out_dir, ffmpeg, opts):
        (out_dir / f"{post.id}.mp4").write_bytes(b"x")
        return f"{post.id}.mp4", 1, ""

    monkeypatch.setattr(tiktok, "TikTok", FakeTikTok)
    monkeypatch.setattr(tiktok, "save_profile", lambda *a: None)
    monkeypatch.setattr(download, "download_requests", lambda *a: None)
    monkeypatch.setattr(mux, "finalize", fake_finalize)

    failed = cli.scrape_profile(f"https://www.tiktok.com/@{USER}/video/999", make_opts(tmp_path), None, None)

    assert failed == 0   # this run's own failure count
    text = (out_dir / "failed.txt").read_text(encoding="utf-8")
    assert VIDEO_URL in text
    assert PHOTO_URL in text
    assert "999" not in text
    assert "✗ 0" in capsys.readouterr().out


def test_run_failure_uses_photo_link(tmp_path, monkeypatch, capsys):
    def photo_item(self, username, video_id):
        item = make_item(video_id)
        item["imagePost"] = {"images": [{"imageURL": {"urlList": ["https://cdn.example/1.jpg"]}}]}
        return item

    def broken_finalize(post, out_dir, ffmpeg, opts):
        raise ScrapeError("mux failed")

    monkeypatch.setattr(FakeTikTok, "item", photo_item)
    monkeypatch.setattr(tiktok, "TikTok", FakeTikTok)
    monkeypatch.setattr(tiktok, "save_profile", lambda *a: None)
    monkeypatch.setattr(download, "download_requests", lambda *a: None)
    monkeypatch.setattr(mux, "finalize", broken_finalize)

    failed = cli.scrape_profile(f"https://www.tiktok.com/@{USER}/photo/777", make_opts(tmp_path), None, None)

    assert failed == 1   # this run's own failure count
    text = (tmp_path / USER / "failed.txt").read_text(encoding="utf-8")
    assert f"https://www.tiktok.com/@{USER}/photo/777\n    mux failed" in text
    assert "✗ 1" in capsys.readouterr().out


def test_run_unexpected_error_uses_photo_link(tmp_path, monkeypatch, capsys):
    def photo_item(self, username, video_id):
        item = make_item(video_id)
        item["imagePost"] = {"images": [{"imageURL": {"urlList": ["https://cdn.example/1.jpg"]}}]}
        return item

    def buggy_finalize(post, out_dir, ffmpeg, opts):
        raise ValueError("boom")

    monkeypatch.setattr(FakeTikTok, "item", photo_item)
    monkeypatch.setattr(tiktok, "TikTok", FakeTikTok)
    monkeypatch.setattr(tiktok, "save_profile", lambda *a: None)
    monkeypatch.setattr(download, "download_requests", lambda *a: None)
    monkeypatch.setattr(mux, "finalize", buggy_finalize)

    failed = cli.scrape_profile(f"https://www.tiktok.com/@{USER}/photo/777", make_opts(tmp_path), None, None)

    assert failed == 1
    text = (tmp_path / USER / "failed.txt").read_text(encoding="utf-8")
    assert f"https://www.tiktok.com/@{USER}/photo/777\n    unexpected error (ValueError: boom)" in text
    assert "/video/777" not in text
    assert "✗ 1" in capsys.readouterr().out
