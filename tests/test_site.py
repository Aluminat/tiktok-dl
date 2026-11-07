"""The site interface: the core gets the aria2c Referer from the site (TikTok's is pinned here)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tiktok_dl import cli
from tiktok_dl.core import download
from tiktok_dl.core.util import UA
from tiktok_dl.sites import tiktok


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
