"""ffmpeg mux fallbacks: which attempts run and what the note says."""
from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.mark.parametrize(
    ("has_cover", "has_subs", "expected"),
    [
        (True, True, [(True, True), (False, True), (False, False)]),
        (True, False, [(True, False), (False, False)]),
        (False, True, [(False, True), (False, False)]),
        (False, False, [(False, False)]),
    ],
)
def test_mux_attempts(tdl, has_cover, has_subs, expected):
    attempts = tdl.mux_attempts(has_cover, has_subs)
    assert attempts == expected
    assert len(set(attempts)) == len(attempts)


@pytest.fixture
def make_post(tdl, tmp_path, monkeypatch):
    """Build a fake post on disk; the cover and subtitle files exist only when asked for."""
    monkeypatch.setattr(tdl, "LANG", "en")
    monkeypatch.setattr(tdl, "mux_tags", lambda item: {"title": "x"})

    def make(has_cover: bool, has_subs: bool):
        video = tmp_path / "video.mp4"
        video.write_bytes(b"\0" * 2048)
        cover = tmp_path / "cover.img"
        if has_cover:
            cover.write_bytes(b"\xff\xd8 cover")
        subs = []
        if has_subs:
            sub = tmp_path / "sub_0.vtt"
            sub.write_text("WEBVTT\n", encoding="utf-8")
            subs.append((sub, "en"))
        return SimpleNamespace(item={}, video=video, cover=cover, subs=subs)

    return make


@pytest.fixture
def ffmpeg_calls(tdl, monkeypatch):
    """Record every ffmpeg command; succeed on the attempt number in `state["ok_on"]` (1-based)."""
    calls = []
    state = {"ok_on": 1}

    def fake_run(cmd):
        calls.append(tuple(cmd))
        if len(calls) == state["ok_on"]:
            return True, ""
        return False, f"boom {len(calls)}"

    monkeypatch.setattr(tdl, "run_ffmpeg", fake_run)
    return calls, state


COMBOS = [(True, True), (True, False), (False, True), (False, False)]


@pytest.mark.parametrize(("has_cover", "has_subs"), COMBOS)
def test_all_attempts_fail_are_distinct_and_keep_error(tdl, make_post, ffmpeg_calls, tmp_path, has_cover, has_subs):
    calls, state = ffmpeg_calls
    state["ok_on"] = 0  # never succeed
    post = make_post(has_cover, has_subs)
    with pytest.raises(tdl.ScrapeError) as exc:
        tdl.mux_video("ffmpeg", post, tmp_path / "out.mp4")
    assert len(calls) == len(tdl.mux_attempts(has_cover, has_subs))
    assert len(set(calls)) == len(calls)
    # The last attempt's ffmpeg error text reaches the user.
    assert f"boom {len(calls)}" in str(exc.value)


def test_no_cover_no_subs_single_attempt(tdl, make_post, ffmpeg_calls, tmp_path):
    calls, state = ffmpeg_calls
    state["ok_on"] = 0
    post = make_post(False, False)
    post.cover = None
    with pytest.raises(tdl.ScrapeError):
        tdl.mux_video("ffmpeg", post, tmp_path / "out.mp4")
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("has_cover", "has_subs", "ok_on", "note"),
    [
        (True, True, 1, ""),
        (True, True, 2, "cover skipped"),
        (True, True, 3, "cover skipped, subtitles skipped"),
        (True, False, 1, ""),
        (True, False, 2, "cover skipped"),
        (False, True, 1, ""),
        (False, True, 2, "subtitles skipped"),
        (False, False, 1, ""),
    ],
)
def test_note_lists_each_dropped_part_once(tdl, make_post, ffmpeg_calls, tmp_path, has_cover, has_subs, ok_on, note):
    calls, state = ffmpeg_calls
    state["ok_on"] = ok_on
    post = make_post(has_cover, has_subs)
    assert tdl.mux_video("ffmpeg", post, tmp_path / "out.mp4") == note
    assert len(calls) == ok_on


def test_note_in_vietnamese(tdl, make_post, ffmpeg_calls, tmp_path, monkeypatch):
    calls, state = ffmpeg_calls
    state["ok_on"] = 3
    post = make_post(True, True)
    monkeypatch.setattr(tdl, "LANG", "vi")
    assert tdl.mux_video("ffmpeg", post, tmp_path / "out.mp4") == "bỏ ảnh bìa, bỏ phụ đề"


def test_attempt_commands_drop_the_right_inputs(tdl, make_post, ffmpeg_calls, tmp_path):
    calls, state = ffmpeg_calls
    state["ok_on"] = 0
    post = make_post(True, True)
    with pytest.raises(tdl.ScrapeError):
        tdl.mux_video("ffmpeg", post, tmp_path / "out.mp4")
    cover, sub = str(post.cover), str(post.subs[0][0])
    assert cover in calls[0] and sub in calls[0]
    assert cover not in calls[1] and sub in calls[1]
    assert cover not in calls[2] and sub not in calls[2]
