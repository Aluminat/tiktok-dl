"""Truncated downloads: a video smaller than the size TikTok reports is a failed download."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tiktok_dl.core import console
from tiktok_dl.sites import tiktok

MP4 = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00" + b"\x00" * 2000
HTML = b"<!DOCTYPE html><html><body>Access Denied</body></html>" + b" " * 100
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 100
VTT = b"WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHello\n" + b"\n" * 100
SIZE = len(MP4)
SHORT = MP4[:1500]
WATERMARKED = MP4[:1800]  # downloadAddr: a different, smaller file with no reported size
A, B, WM = "https://cdn/a.mp4", "https://cdn/b.mp4", "https://cdn/wm.mp4"
SIZED = {A: SIZE, B: SIZE}


@pytest.fixture(autouse=True)
def english(tdl, monkeypatch):
    monkeypatch.setattr(console, "LANG", "en")


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
                keep_parts=False, retry_all=False)
    base.update(kw)
    return SimpleNamespace(**base)


def video_item(size=SIZE, download_addr=WM):
    return {
        "id": "7000000000000000001",
        "createTime": 1700000000,
        "desc": "clip",
        "author": {"uniqueId": "someone"},
        "video": {"playAddr": A, "PlayAddrStruct": {"UrlList": [A, B]}, "downloadAddr": download_addr,
                  "size": size, "cover": "https://cdn/cover.jpg"},
    }


def test_post_records_expected_size_for_play_urls_only(tdl, tmp_path):
    post = tiktok.Post(video_item(), tmp_path, opts(tmp_path))
    tasks = {dest.name: (mirrors, sizes) for mirrors, dest, sizes in post.tasks}
    assert tasks == {"video.mp4": ([A, B, WM], SIZED), "cover.img": (["https://cdn/cover.jpg"], {})}


def test_post_downloadaddr_same_as_playaddr_keeps_size(tdl, tmp_path):
    mirrors, _, sizes = tiktok.Post(video_item(download_addr=A), tmp_path, opts(tmp_path)).tasks[0]
    assert (mirrors, sizes) == ([A, B], SIZED)


def test_post_unknown_size_has_no_sizes(tdl, tmp_path):
    assert tiktok.Post(video_item(size=None), tmp_path, opts(tmp_path)).tasks[0][2] == {}


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
    session = FakeSession({A: [SHORT, MP4]})
    truncated = tdl.download_requests(session, [([A], dest, {A: SIZE})], 1)
    assert session.calls == [A] * 2
    assert dest.read_bytes() == MP4
    assert dest in truncated  # it was cut short once, but the file is complete now


def test_requests_tries_next_mirror_after_truncated(tdl, tmp_path):
    dest = tmp_path / "p" / "video.mp4"
    session = FakeSession({A: [SHORT], B: [MP4]})
    tdl.download_requests(session, [([A, B], dest, SIZED)], 1)
    assert session.calls == [A] * 3 + [B]
    assert dest.read_bytes() == MP4


def test_requests_deletes_truncated_download(tdl, tmp_path):
    dest = tmp_path / "p" / "video.mp4"
    session = FakeSession({A: [SHORT], B: [SHORT]})
    truncated = tdl.download_requests(session, [([A, B], dest, SIZED)], 1)
    assert not dest.exists()
    assert truncated == {dest}


def test_requests_unknown_size_keeps_short_file(tdl, tmp_path):
    dest = tmp_path / "p" / "video.mp4"
    session = FakeSession({A: [SHORT]})
    truncated = tdl.download_requests(session, [([A], dest, {})], 1)
    assert session.calls == [A]
    assert dest.read_bytes() == SHORT
    assert truncated == set()


def test_requests_error_page_is_not_truncation(tdl, tmp_path):
    dest = tmp_path / "p" / "video.mp4"
    session = FakeSession({A: [HTML]})
    truncated = tdl.download_requests(session, [([A], dest, {A: SIZE})], 1)
    assert session.calls == [A]  # an error page goes straight to the next URL
    assert not dest.exists()
    assert truncated == set()


def test_requests_accepts_smaller_watermarked_fallback(tdl, tmp_path):
    dest = tmp_path / "p" / "video.mp4"
    session = FakeSession({A: [HTML], WM: [WATERMARKED]})
    truncated = tdl.download_requests(session, [([A, WM], dest, {A: SIZE})], 1)
    assert session.calls == [A, WM]
    assert dest.read_bytes() == WATERMARKED
    assert truncated == set()


def test_requests_leftover_is_held_to_reported_size(tdl, tmp_path):
    dest = tmp_path / "p" / "video.mp4"
    dest.parent.mkdir()
    dest.write_bytes(SHORT)  # cut short in an earlier run
    session = FakeSession({A: [MP4]})
    tdl.download_requests(session, [([A, WM], dest, {A: SIZE})], 1)
    assert session.calls == [A]
    assert dest.read_bytes() == MP4


# --------------------------------------------------------------------------- #
# aria2c
# --------------------------------------------------------------------------- #

class FakeAria2:
    """Stands in for aria2c: reads the download list from stdin and writes the body queued for each URL."""

    def __init__(self, bodies: dict[str, list[bytes]]):
        self.bodies = bodies
        self.calls: list[str] = []

    def __call__(self, cmd, **kwargs):
        assert "--input-file=-" in cmd
        return SimpleNamespace(communicate=self.run, terminate=lambda: None)

    def run(self, data: bytes, timeout=None):
        downloads = []  # [url, {option: value}] per download, in the aria2c input-file format
        for line in data.decode("utf-8").splitlines():
            if line.startswith(" "):
                key, value = line.strip().split("=", 1)
                downloads[-1][1][key] = value
            elif line:
                downloads.append([line, {}])
        for url, options in downloads:
            self.calls.append(url)
            queue = self.bodies[url]
            body = queue.pop(0) if len(queue) > 1 else queue[0]
            (Path(options["dir"]) / options["out"]).write_bytes(body)
        return b"", b""


def aria2_passes(tdl, tmp_path, monkeypatch, bodies, mirrors, sizes):
    """Run every aria2c pass the way scrape_profile does; return (dest, truncated, URLs fetched)."""
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    dest = work / "p" / "video.mp4"
    fake = FakeAria2(bodies)
    monkeypatch.setattr(tdl.subprocess, "Popen", fake)
    truncated = set()
    for attempt in range(len(mirrors)):
        truncated |= tdl.download_aria2("aria2c", [(mirrors, dest, sizes)], "", 1, 1, attempt)
    return dest, truncated, fake.calls


def test_aria2_retries_truncated_file_with_next_mirror(tdl, tmp_path, monkeypatch):
    dest, truncated, calls = aria2_passes(tdl, tmp_path, monkeypatch, {A: [SHORT], B: [MP4]}, [A, B], SIZED)
    assert calls == [A, B]
    assert dest.read_bytes() == MP4
    assert truncated == {dest}


def test_aria2_deletes_file_truncated_on_every_mirror(tdl, tmp_path, monkeypatch):
    dest, truncated, calls = aria2_passes(tdl, tmp_path, monkeypatch, {A: [SHORT], B: [SHORT]}, [A, B], SIZED)
    assert calls == [A, B]
    assert not dest.exists()
    assert truncated == {dest}


def test_aria2_accepts_smaller_watermarked_fallback(tdl, tmp_path, monkeypatch):
    dest, truncated, calls = aria2_passes(tdl, tmp_path, monkeypatch, {A: [HTML], WM: [WATERMARKED]},
                                          [A, WM], {A: SIZE})
    assert calls == [A, WM]
    assert dest.read_bytes() == WATERMARKED
    assert truncated == set()


def test_aria2_skips_complete_file(tdl, tmp_path, monkeypatch):
    dest = tmp_path / "video.mp4"
    dest.write_bytes(MP4)
    monkeypatch.setattr(tdl.subprocess, "Popen", lambda *a, **k: pytest.fail("aria2c should not run"))
    tdl.download_aria2("aria2c", [([A], dest, {A: SIZE})], "", 1, 1, 0)
    assert dest.read_bytes() == MP4


def test_aria2_leftover_is_held_to_reported_size(tdl, tmp_path, monkeypatch):
    work = tmp_path / "work"
    dest = work / "p" / "video.mp4"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(SHORT)  # cut short in an earlier run
    fake = FakeAria2({A: [MP4]})
    monkeypatch.setattr(tdl.subprocess, "Popen", fake)
    tdl.download_aria2("aria2c", [([A, WM], dest, {A: SIZE})], "", 1, 1, 0)
    assert fake.calls == [A]
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

        def stop(self):
            pass

    monkeypatch.setattr(tiktok, "TikTok", FakeTikTok)
    monkeypatch.setattr(tiktok, "save_profile", lambda *a: None)
    o = opts(tmp_path, cover=False)
    failed = tdl.scrape_profile(f"https://www.tiktok.com/@someone/video/{item['id']}", o, aria2, None)
    return failed, tmp_path / "out" / "someone"


def test_every_mirror_truncated_aria2_is_reported(tdl, tmp_path, monkeypatch):
    fake = FakeAria2({A: [SHORT], B: [SHORT], WM: [HTML]})
    monkeypatch.setattr(tdl.subprocess, "Popen", fake)
    failed, out = run_profile(tdl, tmp_path, monkeypatch, video_item(), aria2="aria2c")
    assert failed == 1
    assert fake.calls == [A, B, WM]  # every URL was tried
    report = (out / "failed.txt").read_text(encoding="utf-8")
    assert "video download incomplete" in report
    assert not list(out.glob("*.mp4"))


def test_second_mirror_complete_aria2_succeeds(tdl, tmp_path, monkeypatch):
    monkeypatch.setattr(tdl.subprocess, "Popen", FakeAria2({A: [SHORT], B: [MP4], WM: [HTML]}))
    failed, out = run_profile(tdl, tmp_path, monkeypatch, video_item(), aria2="aria2c")
    assert failed == 0
    assert not (out / "failed.txt").exists()
    assert [p.read_bytes() for p in out.glob("*.mp4")] == [MP4]


def test_every_mirror_truncated_requests_is_reported(tdl, tmp_path, monkeypatch):
    session = FakeSession({A: [SHORT], B: [SHORT], WM: [HTML]})
    failed, out = run_profile(tdl, tmp_path, monkeypatch, video_item(), session=session)
    assert failed == 1
    assert "video download incomplete" in (out / "failed.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize("aria2", [None, "aria2c"])
def test_watermarked_fallback_is_accepted(tdl, tmp_path, monkeypatch, aria2):
    """playAddr gives an error page; the smaller watermarked downloadAddr file is complete."""
    bodies = {A: [HTML], B: [HTML], WM: [WATERMARKED]}
    monkeypatch.setattr(tdl.subprocess, "Popen", FakeAria2(bodies))
    failed, out = run_profile(tdl, tmp_path, monkeypatch, video_item(), session=FakeSession(bodies), aria2=aria2)
    assert failed == 0
    assert not (out / "failed.txt").exists()
    assert [p.read_bytes() for p in out.glob("*.mp4")] == [WATERMARKED]


def test_unknown_size_short_video_is_accepted(tdl, tmp_path, monkeypatch):
    session = FakeSession({A: [SHORT]})
    failed, out = run_profile(tdl, tmp_path, monkeypatch, video_item(size=None), session=session)
    assert failed == 0
    assert [p.read_bytes() for p in out.glob("*.mp4")] == [SHORT]
