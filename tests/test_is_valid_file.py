"""is_valid_file(): reject downloads that are really error pages or unfinished."""
from __future__ import annotations

import pytest

from tiktok_dl.core import download

PAD = b"\x00" * 100
MP4 = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00" + PAD
HTML = b"<!DOCTYPE html><html><body>Access Denied</body></html>" + b" " * 100
JSON = b'{"statusCode": 10204, "statusMsg": "item doesn\'t exist"}' + b" " * 100
VTT = b"WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHello\n" + b"\n" * 100
BOM = b"\xef\xbb\xbf"
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF" + PAD


@pytest.mark.parametrize(
    ("name", "data", "expected"),
    [
        ("video.mp4", MP4, True),
        ("video.mp4", HTML, False),
        ("video.mp4", JSON, False),
        ("video.mp4", b"\n  " + HTML, False),
        ("video.mp4", b"\x00" * 200, False),  # no ftyp box
        ("video.mp4", MP4[:63], False),  # too small
        ("subs.vtt", VTT, True),
        ("subs.vtt", BOM + VTT, True),
        ("subs.vtt", HTML, False),
        ("subs.vtt", JSON, False),
        ("cover.jpg", JPEG, True),
        ("cover.jpg", HTML, False),
        ("music.mp3", b"ID3\x04\x00" + PAD, True),
    ],
)
def test_is_valid_file(tmp_path, name, data, expected):
    dest = tmp_path / name
    dest.write_bytes(data)
    assert download.is_valid_file(dest) is expected


def test_minimum_size(tmp_path):
    dest = tmp_path / "cover.jpg"
    dest.write_bytes(JPEG[:63])
    assert download.is_valid_file(dest) is False
    dest.write_bytes(JPEG[:64])
    assert download.is_valid_file(dest) is True


def test_missing_file(tmp_path):
    assert download.is_valid_file(tmp_path / "missing.mp4") is False


def test_directory(tmp_path):
    folder = tmp_path / "video.mp4"
    folder.mkdir()
    assert download.is_valid_file(folder) is False


def test_leftover_aria2_control_file(tmp_path):
    # aria2c keeps "<file>.aria2" next to a download until it is complete.
    dest = tmp_path / "video.mp4"
    dest.write_bytes(MP4)
    (tmp_path / "video.mp4.aria2").write_bytes(b"\x00" * 10)
    assert download.is_valid_file(dest) is False
    (tmp_path / "video.mp4.aria2").unlink()
    assert download.is_valid_file(dest) is True
