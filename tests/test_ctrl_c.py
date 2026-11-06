"""Ctrl+C stops promptly: queued work is dropped, finished posts stay recorded (issue #5).

Ctrl+C is simulated either by raising KeyboardInterrupt from a mocked step that runs in the main
thread, or with _thread.interrupt_main() from a worker thread (what a real Ctrl+C does).
"""
from __future__ import annotations

import _thread
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

N_POSTS = 12
BATCH = 4


@pytest.fixture(autouse=True)
def clean_stop(tdl):
    """STOP is module state: never let one test's Ctrl+C leak into another test."""
    tdl.STOP.clear()
    # interrupt_main() only raises KeyboardInterrupt when Python's own SIGINT handler is installed.
    old = signal.signal(signal.SIGINT, signal.default_int_handler)
    yield
    signal.signal(signal.SIGINT, old)
    tdl.STOP.clear()


def make_opts(tmp_path: Path, **overrides) -> SimpleNamespace:
    opts = SimpleNamespace(
        output=str(tmp_path), session_id=None, wait=0, proxy=None, limit=None, metadata_only=False,
        batch=BATCH, workers=1, mux_jobs=1, jobs=1, connections=1, cover=False, subs="none",
        slideshow=False, slide_seconds=3, keep_images=False, keep_parts=False,
    )
    vars(opts).update(overrides)
    return opts


@pytest.fixture
def profile(tdl, monkeypatch):
    """A fake profile with N_POSTS videos: no TikTok, no network, no real downloads."""
    calls: list[str] = []
    on_item = {}   # call number -> hook, run inside the metadata worker thread

    class FakeTikTok:
        def __init__(self, *args, **kwargs):
            self.s = None

        def cookie_header(self) -> str:
            return ""

        def item(self, username: str, video_id: str) -> dict:
            calls.append(video_id)
            hook = on_item.get(len(calls))
            if hook:
                hook()
            time.sleep(0.02)
            return {"id": video_id, "createTime": 1700000000, "author": {"uniqueId": username},
                    "video": {"playAddr": f"https://example.invalid/{video_id}.mp4"}}

    monkeypatch.setattr(tdl, "TikTok", FakeTikTok)
    monkeypatch.setattr(tdl, "save_profile", lambda *args: None)
    monkeypatch.setattr(tdl, "list_posts", lambda *args: [{"id": str(i)} for i in range(1, N_POSTS + 1)])
    return SimpleNamespace(calls=calls, on_item=on_item)


def settle(calls: list, seconds: float = 1.0) -> None:
    """Give background metadata threads time to wind down (they must stop, not finish the batch)."""
    end = time.monotonic() + seconds
    last = -1
    while time.monotonic() < end and len(calls) != last:
        last = len(calls)
        time.sleep(0.3)


def test_ctrl_c_in_download_phase_cancels_queued_mux_jobs(tdl, profile, monkeypatch, tmp_path):
    started: list[str] = []
    downloads = []

    def fake_finalize(post, out_dir, ffmpeg, opts):
        started.append(post.id)
        if len(started) == 1:   # the first post finishes normally before Ctrl+C
            name = f"{post.id}.mp4"
            (out_dir / name).write_bytes(b"x" * 10)
            return name, 10, ""
        # The second one is muxing when Ctrl+C comes: ffmpeg gets it too and fails.
        if tdl.STOP.wait(5):
            raise tdl.ScrapeError("ffmpeg error: interrupted")
        raise AssertionError("STOP was never set")

    def fake_download(session, tasks, jobs):
        downloads.append(tasks)
        if len(downloads) == 2:
            # Ctrl+C while batch 2 downloads: batch 1 has 1 post done, 1 muxing and 2 queued.
            deadline = time.monotonic() + 5
            while len(started) < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            raise KeyboardInterrupt

    monkeypatch.setattr(tdl, "finalize", fake_finalize)
    monkeypatch.setattr(tdl, "download_requests", fake_download)
    # The first read-ahead request of batch 3 is still in flight when Ctrl+C comes.
    profile.on_item[2 * BATCH + 1] = lambda: tdl.STOP.wait(2)

    t0 = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        tdl.scrape_profile("someuser", make_opts(tmp_path), None, None)
    assert time.monotonic() - t0 < 3
    assert tdl.STOP.is_set()

    # Queued mux jobs were cancelled, not run.
    assert started == ["1", "2"]

    # Read-ahead of batch 3 stops after the request in flight; batch 4 is never read.
    settle(profile.calls)
    assert len(profile.calls) <= 2 * BATCH + 1

    # The finished post is skipped on the next run, everything else is retried.
    out_dir = tmp_path / "someuser"
    progress = tdl.Progress(out_dir / ".progress.db")
    rows = dict(progress.db.execute("SELECT id, status FROM posts").fetchall())
    progress.db.close()
    assert rows == {"1": "done"}


def test_ctrl_c_in_metadata_phase_stops_reading(tdl, profile, monkeypatch, tmp_path):
    monkeypatch.setattr(tdl, "download_requests", lambda *args: pytest.fail("download must not start"))
    def ctrl_c_during_request():
        # Ctrl+C while the main thread waits for metadata; this request is still in flight.
        _thread.interrupt_main()
        tdl.STOP.wait(2)

    profile.on_item[2] = ctrl_c_during_request

    t0 = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        tdl.scrape_profile("someuser", make_opts(tmp_path), None, None)
    assert time.monotonic() - t0 < 3
    assert tdl.STOP.is_set()

    settle(profile.calls)
    assert len(profile.calls) == 2   # posts 3 and 4 of the batch were not read


def test_main_exits_130_with_one_stopped_message(tdl, profile, monkeypatch, tmp_path, capsys):
    def fake_download(session, tasks, jobs):
        raise KeyboardInterrupt

    monkeypatch.setattr(tdl, "download_requests", fake_download)
    monkeypatch.setattr(tdl, "finalize", lambda *args: pytest.fail("no post should be muxed"))
    monkeypatch.setattr(tdl, "find_tool", lambda name: None)
    monkeypatch.setattr(tdl, "find_ffmpeg", lambda: None)
    monkeypatch.setattr(sys, "argv", ["tiktok-dl", "someuser", "-o", str(tmp_path), "--lang", "en", "--no-color"])

    with pytest.raises(SystemExit) as exc:
        tdl.main()
    assert exc.value.code == 130
    out = capsys.readouterr()
    assert (out.out + out.err).count("Stopped. Run the same command again to continue.") == 1


class FakeResponse:
    def __init__(self, chunks):
        self.chunks = chunks
        self.sent = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, size):
        for chunk in self.chunks:
            self.sent += 1
            yield chunk


def test_download_requests_stops_mid_file_and_removes_partial(tdl, tmp_path):
    def slow_chunks():
        yield b"\x00\x00\x00\x20ftypisom" + b"\x00" * 100   # looks like a valid MP4 start
        _thread.interrupt_main()                            # Ctrl+C after the first chunk
        for _ in range(200):
            time.sleep(0.01)
            yield b"\x00" * 100

    gets = []
    responses = []

    class FakeSession:
        def get(self, url, stream, timeout):
            gets.append(url)
            responses.append(FakeResponse(slow_chunks()))
            return responses[-1]

    tasks = [([f"https://example.invalid/{i}.mp4"], tmp_path / f"{i}" / "video.mp4", 0) for i in range(3)]
    t0 = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        tdl.download_requests(FakeSession(), tasks, 1)
    assert time.monotonic() - t0 < 2
    assert tdl.STOP.is_set()
    assert gets == ["https://example.invalid/0.mp4"]   # the queued files were never started
    assert responses[0].sent < 200
    # A half-written file would pass is_valid_file() and be muxed as if complete on the next run.
    assert not tasks[0][1].exists()


def test_run_ffmpeg_does_not_start_after_ctrl_c(tdl, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("ffmpeg must not start"))
    tdl.STOP.set()
    with pytest.raises(tdl.ScrapeError):
        tdl.run_ffmpeg(["ffmpeg", "-version"])


def test_mux_video_skips_fallbacks_when_ffmpeg_was_stopped(tdl, monkeypatch, tmp_path):
    runs = []

    def fake_run(cmd, **kwargs):
        runs.append(cmd)
        tdl.STOP.set()   # Ctrl+C arrives while ffmpeg runs, and ffmpeg exits with an error
        return SimpleNamespace(returncode=255, stderr=b"Exiting normally, received signal 2.")

    monkeypatch.setattr(subprocess, "run", fake_run)
    cover = tmp_path / "cover.img"
    cover.write_bytes(b"x" * 10)
    post = SimpleNamespace(item={"id": "1", "author": {"uniqueId": "u"}}, subs=[], cover=cover,
                           video=tmp_path / "video.mp4")
    with pytest.raises(tdl.ScrapeError):
        tdl.mux_video("ffmpeg", post, tmp_path / "out.mp4")
    assert len(runs) == 1   # no "cover skipped" retry that could be recorded as done


def test_wait_result_returns_value_and_raises_errors(tdl):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(1) as pool:
        assert tdl.wait_result(pool.submit(lambda: 42)) == 42
        with pytest.raises(ValueError):
            tdl.wait_result(pool.submit(lambda: int("x")))


def test_wait_result_can_be_interrupted(tdl):
    gate = threading.Event()
    from concurrent.futures import ThreadPoolExecutor

    pool = ThreadPoolExecutor(1)
    try:
        fut = pool.submit(gate.wait, 10)
        threading.Timer(0.2, _thread.interrupt_main).start()
        t0 = time.monotonic()
        with pytest.raises(KeyboardInterrupt):
            tdl.wait_result(fut)
        assert time.monotonic() - t0 < 2
    finally:
        gate.set()
        pool.shutdown(wait=True)
