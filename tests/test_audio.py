"""A video post whose sound comes as a separate stream (Media.audio, e.g. Facebook's DASH audio): the core
downloads it next to the video and ffmpeg muxes it in. Posts without one keep the same ffmpeg command."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from tiktok_dl.core import console, download, mux
from tiktok_dl.core.errors import ScrapeError
from tiktok_dl.core.site import Media, Post

VIDEO, AUDIO = "https://cdn.example/v.mp4", "https://cdn.example/a.mp4"


class DemoPost(Post):
    id = "42"
    link = "https://demo.example/p/42"
    author = "someone"
    created = datetime(2024, 1, 2, tzinfo=timezone.utc)
    caption = "hi"
    counts = {}

    def __init__(self, media: Media):
        super().__init__({})
        self._media = media

    def media(self) -> Media:
        return self._media


def opts(**overrides):
    return SimpleNamespace(**{"cover": True, "subs": "none", "metadata_only": False, "slideshow": True,
                              "keep_images": False, "keep_parts": False, **overrides})


def plan(tmp_path, media: Media) -> download.Download:
    return download.Download(DemoPost(media), tmp_path / ".parts", opts(),
                             SimpleNamespace(mux_tags=lambda post: {"title": "x"}, label="Demo"))


def test_the_audio_stream_is_a_download_task(tmp_path):
    p = plan(tmp_path, Media(video=[VIDEO], audio=AUDIO))
    assert [(urls, dest.name) for urls, dest, _ in p.tasks] == [([VIDEO], "video.mp4"), ([AUDIO], "audio.m4a")]


def test_a_video_without_audio_stream_has_no_audio_task(tmp_path):
    p = plan(tmp_path, Media(video=[VIDEO]))
    assert [dest.name for _, dest, _ in p.tasks] == ["video.mp4"]
    assert p.audio is None


def test_a_post_without_a_download_link_has_no_audio(tmp_path):
    p = plan(tmp_path, Media(audio=AUDIO, unavailable="no link"))
    assert p.tasks == [] and p.hidden == "no link"
    assert p.audio is None


def test_a_photo_post_downloads_its_music_after_the_images(tmp_path):
    p = plan(tmp_path, Media(images=[["https://cdn.example/1.jpg"], ["https://cdn.example/2.jpg"]], audio=AUDIO))
    assert [dest.name for _, dest, _ in p.tasks] == ["img_01.jpg", "img_02.jpg", "audio.mp3"]
    assert p.audio.name == "audio.mp3"


@pytest.fixture
def ffmpeg(monkeypatch):
    calls = []

    def run(cmd):
        calls.append(cmd)
        return True, ""

    monkeypatch.setattr(mux, "run_ffmpeg", run)
    return calls


def write_files(p: download.Download, audio: bool = True) -> None:
    p.work.mkdir(parents=True, exist_ok=True)
    p.video.write_bytes(b"\x00\x00\x00\x18ftypdash" + b"\x00" * 2048)
    if audio:
        p.audio.write_bytes(b"\x00\x00\x00\x18ftypdash" + b"\x00" * 2048)


def test_ffmpeg_takes_the_video_from_the_first_input_and_the_sound_from_the_second(tmp_path, ffmpeg):
    p = plan(tmp_path, Media(video=[VIDEO], audio=AUDIO))
    write_files(p)
    assert mux.mux_video("ffmpeg", p, tmp_path / "out.mp4") == ""
    cmd = ffmpeg[0]
    assert cmd[cmd.index("-i") + 1] == str(p.video)
    assert cmd[cmd.index("-i", cmd.index("-i") + 1) + 1] == str(p.audio)
    maps = [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "-map"]
    assert maps == ["0:v:0", "1:a:0"]
    assert cmd[cmd.index("-c") + 1] == "copy"


def test_cover_and_subtitles_come_after_the_audio_input(tmp_path, ffmpeg):
    p = plan(tmp_path, Media(video=[VIDEO], audio=AUDIO, cover="https://cdn.example/c.jpg"))
    write_files(p)
    p.cover.write_bytes(b"\xff\xd8 cover")
    p.subs = [(tmp_path / "sub_0.vtt", "en")]
    p.subs[0][0].write_text("WEBVTT\n", encoding="utf-8")
    mux.mux_video("ffmpeg", p, tmp_path / "out.mp4")
    maps = [ffmpeg[0][i + 1] for i, arg in enumerate(ffmpeg[0]) if arg == "-map"]
    assert maps == ["0:v:0", "1:a:0", "2:0", "3:0"]


def test_fallback_attempts_never_drop_the_audio(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(mux, "run_ffmpeg", lambda cmd: (calls.append(cmd), (False, "boom"))[1])
    p = plan(tmp_path, Media(video=[VIDEO], audio=AUDIO, cover="https://cdn.example/c.jpg"))
    write_files(p)
    p.cover.write_bytes(b"\xff\xd8 cover")
    with pytest.raises(ScrapeError):
        mux.mux_video("ffmpeg", p, tmp_path / "out.mp4")
    assert len(calls) == 2 and all(str(p.audio) in cmd for cmd in calls)


def test_the_command_without_audio_stream_is_unchanged(tmp_path, ffmpeg):
    p = plan(tmp_path, Media(video=[VIDEO]))
    write_files(p, audio=False)
    out = tmp_path / "out.mp4"
    mux.mux_video("ffmpeg", p, out)
    assert ffmpeg[0] == ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(p.video),
                         "-map", "0:v:0", "-map", "0:a?", "-c", "copy", "-c:s", "mov_text",
                         "-metadata", "title=x", "-movflags", "+faststart", str(out)]


def test_finalize_fails_when_the_audio_did_not_download(tmp_path, ffmpeg, monkeypatch):
    monkeypatch.setattr(console, "LANG", "en")
    p = plan(tmp_path, Media(video=[VIDEO], audio=AUDIO))
    write_files(p, audio=False)
    tmp_path.joinpath("out").mkdir()
    with pytest.raises(ScrapeError, match="^audio could not be downloaded$"):
        mux.finalize(p, tmp_path / "out", "ffmpeg", opts())
    assert ffmpeg == [] and list((tmp_path / "out").iterdir()) == []


def test_without_ffmpeg_the_sound_is_kept_next_to_the_video(tmp_path, monkeypatch):
    monkeypatch.setattr(console, "LANG", "en")
    p = plan(tmp_path, Media(video=[VIDEO], audio=AUDIO))
    write_files(p)
    out = tmp_path / "out"
    out.mkdir()
    name, size, note = mux.finalize(p, out, None, opts())
    assert name == "2024-01-02_42_hi.mp4"
    assert sorted(f.name for f in out.iterdir()) == ["2024-01-02_42_hi.m4a", "2024-01-02_42_hi.mp4"]
    assert size == 2 * (2048 + 12)
    assert note == "no ffmpeg: video and audio saved as separate files, no metadata added"
