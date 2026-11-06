"""Truncated downloads: a video smaller than the size TikTok reports is a failed download."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

MP4 = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00" + b"\x00" * 2000
HTML = b"<!DOCTYPE html><html><body>Access Denied</body></html>" + b" " * 100
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 100
VTT = b"WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHello\n" + b"\n" * 100
SIZE = len(MP4)
SHORT = MP4[:1500]


@pytest.fixture(autouse=True)
def english(tdl, monkeypatch):
    monkeypatch.setattr(tdl, "LANG", "en")


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("data", "expected", "valid"),
    [
        (MP4, SIZE, True),  # matches the reported size
        (MP4, SIZE - 1, True),  # bigger than reported is not a truncation
        (SHORT, SIZE, False),  # smaller than reported
        (MP4[:-1], SIZE, False),  # one byte short
        (SHORT, 0, True),  # unknown size: only the content is checked
        (HTML, 0, False),
        (HTML + b" " * SIZE, SIZE, False),  # big enough but still an error page
    ],
)
def test_is_valid_file_expected_size(tdl, tmp_path, data, expected, valid):
    dest = tmp_path / "video.mp4"
    dest.write_bytes(data)
    assert tdl.is_valid_file(dest, expected) is valid


@pytest.mark.parametrize(("name", "data"), [("cover.img", JPEG), ("sub_0.vtt", VTT), ("audio.mp3", b"ID3" + JPEG)])
def test_unknown_size_files_validate_as_before(tdl, tmp_path, name, data):
    dest = tmp_path / name
    dest.write_bytes(data)
    assert tdl.is_valid_file(dest, 0) is tdl.is_valid_file(dest) is True


def test_is_truncated(tdl, tmp_path):
    dest = tmp_path / "video.mp4"
    assert tdl.is_truncated(dest, SIZE) is False  # missing
    dest.write_bytes(SHORT)
    assert tdl.is_truncated(dest, SIZE) is True
    assert tdl.is_truncated(dest, 0) is False  # unknown size
    dest.write_bytes(MP4)
    assert tdl.is_truncated(dest, SIZE) is False
    dest.write_bytes(HTML)
    assert tdl.is_truncated(dest, SIZE) is False  # error page, not a truncation


def opts(tmp_path, **kw):
    base = dict(output=str(tmp_path / "out"), session_id=None, wait=0, proxy=None, limit=None,
                metadata_only=False, batch=50, workers=1, mux_jobs=1, jobs=2, connections=1,
                cover=True, subs="original", slideshow=True, keep_images=False, slide_seconds=3.0,
                keep_parts=False)
    base.update(kw)
    return SimpleNamespace(**base)


def video_item(size=SIZE):
    return {
        "id": "7000000000000000001",
        "createTime": 1700000000,
        "desc": "clip",
        "author": {"uniqueId": "someone"},
        "video": {"playAddr": "https://cdn/a.mp4", "downloadAddr": "https://cdn/b.mp4", "size": size,
                  "cover": "https://cdn/cover.jpg"},
    }


def test_post_records_expected_size_for_video_only(tdl, tmp_path):
    post = tdl.Post(video_item(), tmp_path, opts(tmp_path))
    sizes = {dest.name: size for _, dest, size in post.tasks}
    assert sizes == {"video.mp4": SIZE, "cover.img": 0}


# --------------------------------------------------------------------------- #
# requests fallback
# --------------------------------------------------------------------------- #

class FakeResponse:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, size):
        for i in range(0, len(self.body), size):
            yield self.body[i:i + size]


class FakeSession:
    """Serves the bodies queued for each URL in order; the last one repeats."""

    def __init__(self, bodies: dict[str, list[bytes]]):
        self.bodies = bodies
        self.calls: list[str] = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        queue = self.bodies[url]
        return FakeResponse(queue.pop(0) if len(queue) > 1 else queue[0])


def test_requests_retries_truncated_download(tdl, tmp_path):
    dest = tmp_path / "p" / "video.mp4"
    session = FakeSession({"https://cdn/a.mp4": [SHORT, MP4]})
    truncated = tdl.download_requests(session, [(["https://cdn/a.mp4"], dest, SIZE)], 1)
    assert session.calls == ["https://cdn/a.mp4"] * 2
    assert dest.read_bytes() == MP4
    assert dest in truncated  # it was cut short once, but the file is complete now


def test_requests_tries_next_mirror_after_truncated(tdl, tmp_path):
    dest = tmp_path / "p" / "video.mp4"
    session = FakeSession({"https://cdn/a.mp4": [SHORT], "https://cdn/b.mp4": [MP4]})
    tdl.download_requests(session, [(["https://cdn/a.mp4", "https://cdn/b.mp4"], dest, SIZE)], 1)
    assert session.calls == ["https://cdn/a.mp4"] * 3 + ["https://cdn/b.mp4"]
    assert dest.read_bytes() == MP4


def test_requests_deletes_truncated_download(tdl, tmp_path):
    dest = tmp_path / "p" / "video.mp4"
    session = FakeSession({"https://cdn/a.mp4": [SHORT], "https://cdn/b.mp4": [SHORT]})
    truncated = tdl.download_requests(session, [(["https://cdn/a.mp4", "https://cdn/b.mp4"], dest, SIZE)], 1)
    assert not dest.exists()
    assert truncated == {dest}


def test_requests_unknown_size_keeps_short_file(tdl, tmp_path):
    dest = tmp_path / "p" / "video.mp4"
    session = FakeSession({"https://cdn/a.mp4": [SHORT]})
    truncated = tdl.download_requests(session, [(["https://cdn/a.mp4"], dest, 0)], 1)
    assert session.calls == ["https://cdn/a.mp4"]
    assert dest.read_bytes() == SHORT
    assert truncated == set()


def test_requests_error_page_is_not_truncation(tdl, tmp_path):
    dest = tmp_path / "p" / "video.mp4"
    session = FakeSession({"https://cdn/a.mp4": [HTML]})
    truncated = tdl.download_requests(session, [(["https://cdn/a.mp4"], dest, SIZE)], 1)
    assert session.calls == ["https://cdn/a.mp4"]  # an error page goes straight to the next URL
    assert not dest.exists()
    assert truncated == set()


# --------------------------------------------------------------------------- #
# aria2c
# --------------------------------------------------------------------------- #

def test_aria2_retries_truncated_file_with_next_mirror(tdl, tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    dest = work / "p" / "video.mp4"
    dest.parent.mkdir()
    dest.write_bytes(SHORT)  # what the first mirror gave
    started = []

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            started.append(cmd)

        def wait(self):
            return 0

    monkeypatch.setattr(tdl.subprocess, "Popen", FakePopen)
    tdl.download_aria2("aria2c", [(["https://cdn/a.mp4", "https://cdn/b.mp4"], dest, SIZE)],
                       "", 1, 1, work, 1)
    assert len(started) == 1
    assert not dest.exists()  # the truncated file was removed before the retry
    assert "https://cdn/b.mp4" in (work / "aria2-input.txt").read_text(encoding="utf-8")


def test_aria2_skips_complete_file(tdl, tmp_path, monkeypatch):
    dest = tmp_path / "video.mp4"
    dest.write_bytes(MP4)
    monkeypatch.setattr(tdl.subprocess, "Popen", lambda *a, **k: pytest.fail("aria2c should not run"))
    tdl.download_aria2("aria2c", [(["https://cdn/a.mp4"], dest, SIZE)], "", 1, 1, tmp_path, 0)
    assert dest.read_bytes() == MP4


# --------------------------------------------------------------------------- #
# Whole flow: failed.txt
# --------------------------------------------------------------------------- #

def run_profile(tdl, tmp_path, monkeypatch, item, session=None, aria2=None):
    class FakeTikTok:
        def __init__(self, *args):
            self.s = session

        def item(self, username, video_id):
            return item

        def cookie_header(self):
            return ""

    monkeypatch.setattr(tdl, "TikTok", FakeTikTok)
    monkeypatch.setattr(tdl, "save_profile", lambda *a: None)
    o = opts(tmp_path, cover=False)
    failed = tdl.scrape_profile(f"https://www.tiktok.com/@someone/video/{item['id']}", o, aria2, None)
    return failed, tmp_path / "out" / "someone"


def test_every_mirror_truncated_aria2_is_reported(tdl, tmp_path, monkeypatch):
    attempts = []

    def fake_aria2(aria2, tasks, cookie, jobs, conns, work, attempt):
        attempts.append(attempt)
        for mirrors, dest, _ in tasks:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(SHORT)

    monkeypatch.setattr(tdl, "download_aria2", fake_aria2)
    failed, out = run_profile(tdl, tmp_path, monkeypatch, video_item(), aria2="aria2c")
    assert failed == 1
    assert attempts == [0, 1]  # both mirrors were tried
    report = (out / "failed.txt").read_text(encoding="utf-8")
    assert "video download incomplete" in report
    assert not list(out.glob("*.mp4"))


def test_second_mirror_complete_aria2_succeeds(tdl, tmp_path, monkeypatch):
    def fake_aria2(aria2, tasks, cookie, jobs, conns, work, attempt):
        for mirrors, dest, size in tasks:
            if not tdl.is_valid_file(dest, size):
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(SHORT if attempt == 0 else MP4)

    monkeypatch.setattr(tdl, "download_aria2", fake_aria2)
    failed, out = run_profile(tdl, tmp_path, monkeypatch, video_item(), aria2="aria2c")
    assert failed == 0
    assert not (out / "failed.txt").exists()
    assert [p.read_bytes() for p in out.glob("*.mp4")] == [MP4]


def test_every_mirror_truncated_requests_is_reported(tdl, tmp_path, monkeypatch):
    session = FakeSession({"https://cdn/a.mp4": [SHORT], "https://cdn/b.mp4": [SHORT]})
    failed, out = run_profile(tdl, tmp_path, monkeypatch, video_item(), session=session)
    assert failed == 1
    assert "video download incomplete" in (out / "failed.txt").read_text(encoding="utf-8")


def test_unknown_size_short_video_is_accepted(tdl, tmp_path, monkeypatch):
    session = FakeSession({"https://cdn/a.mp4": [SHORT]})
    failed, out = run_profile(tdl, tmp_path, monkeypatch, video_item(size=None), session=session)
    assert failed == 0
    assert [p.read_bytes() for p in out.glob("*.mp4")] == [SHORT]
