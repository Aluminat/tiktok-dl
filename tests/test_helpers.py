"""Small pure helpers: sanitising, names, formatters and language detection."""
from __future__ import annotations

import locale

import pytest


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("hello world", "hello world"),
        ("a/b\\c:d*e?f\"g<h>i|j", "abcdefghij"),
        ("line one\nline two\r\n\tend", "line one line two end"),
        ("#fyp #funny video", "fyp funny video"),
        ("  lots    of   spaces  ", "lots of spaces"),
        ("ends with dots...", "ends with dots"),
        ("ends with dot and space. ", "ends with dot and space"),
        ("ctrl\x00\x1fchars", "ctrlchars"),
        ("Tiếng Việt 🎉", "Tiếng Việt 🎉"),
        ("", ""),
        (None, ""),
    ],
)
def test_sanitize(tdl, text, expected):
    assert tdl.sanitize(text) == expected


def test_sanitize_limit(tdl):
    assert tdl.sanitize("x" * 100) == "x" * 60
    assert tdl.sanitize("abcdef", limit=3) == "abc"
    # Cutting at the limit must not leave a trailing dot or space.
    assert tdl.sanitize("abc. def", limit=5) == "abc"


@pytest.mark.parametrize(
    ("value", "expected"),
    [(5, 5), ("5", 5), (" 7 ", 7), (5.9, 5), ("-3", -3), (None, None), ("", None), ("abc", None), ("5.0", None)],
)
def test_to_int(tdl, value, expected):
    assert tdl.to_int(value) == expected


def test_uniq(tdl):
    assert tdl.uniq(["b", "a", "b", None, "", "c", "a"]) == ["b", "a", "c"]
    assert tdl.uniq(x for x in (1, 0, 2, 1)) == [1, 2]
    assert tdl.uniq([]) == []


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        ({"id": "123", "createTime": 1700000000, "desc": "Hello / world #fyp"}, "2023-11-14_123_Hello world fyp"),
        ({"id": "123", "createTime": "1700000000", "desc": ""}, "2023-11-14_123"),
        ({"id": "123", "createTime": 1700000000, "desc": "###"}, "2023-11-14_123"),
        ({"id": "123"}, "1970-01-01_123"),
    ],
)
def test_base_name(tdl, item, expected):
    assert tdl.base_name(item) == expected


@pytest.mark.parametrize(
    ("n", "expected"),
    [
        (0, "0B"),
        (1023, "1023B"),
        (1024, "1.0KB"),
        (1536, "1.5KB"),
        (1024 ** 2, "1.0MB"),
        (int(2.5 * 1024 ** 2), "2.5MB"),
        (5 * 1024 ** 3, "5.0GB"),
        (1024 ** 4, "1024.0GB"),
    ],
)
def test_fmt_bytes(tdl, n, expected):
    assert tdl.fmt_bytes(n) == expected


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (None, "--:--"),
        (-1, "--:--"),
        (0, "00:00"),
        (5.9, "00:05"),
        (65, "01:05"),
        (3599, "59:59"),
        (3600, "1:00:00"),
        (3725, "1:02:05"),
        (99 * 3600, "99:00:00"),
        (99 * 3600 + 1, "--:--"),
    ],
)
def test_fmt_eta(tdl, seconds, expected):
    assert tdl.fmt_eta(seconds) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "0"),
        (999, "999"),
        (1000, "1.000"),
        (1234567, "1.234.567"),
        (-1234, "-1.234"),
        ("42000", "42.000"),
        (None, "?"),
        ("abc", "?"),
    ],
)
def test_fmt_int(tdl, value, expected):
    assert tdl.fmt_int(value) == expected


class TestDetectLang:
    @pytest.fixture(autouse=True)
    def english_env(self, monkeypatch):
        monkeypatch.delenv("TIKTOK_DL_LANG", raising=False)
        monkeypatch.setattr(locale, "getlocale", lambda *a: ("en_US", "UTF-8"))

    @pytest.mark.parametrize(
        ("argv", "expected"),
        [
            (["--lang=vi"], "vi"),
            (["--lang=VI"], "vi"),
            (["@tiktok", "--lang", "vi"], "vi"),
            (["--lang", "EN"], "en"),
            ([], "en"),
            (["@tiktok", "-n", "5"], "en"),
        ],
    )
    def test_argv(self, tdl, argv, expected):
        assert tdl.detect_lang(argv) == expected

    def test_argv_wins_over_env(self, tdl, monkeypatch):
        monkeypatch.setenv("TIKTOK_DL_LANG", "vi")
        assert tdl.detect_lang(["--lang=en"]) == "en"

    def test_lang_flag_without_value_falls_back(self, tdl, monkeypatch):
        monkeypatch.setenv("TIKTOK_DL_LANG", "vi")
        assert tdl.detect_lang(["--lang"]) == "vi"

    def test_env(self, tdl, monkeypatch):
        monkeypatch.setenv("TIKTOK_DL_LANG", "VI")
        assert tdl.detect_lang([]) == "vi"

    def test_unknown_env_falls_back_to_locale(self, tdl, monkeypatch):
        monkeypatch.setenv("TIKTOK_DL_LANG", "fr")
        monkeypatch.setattr(locale, "getlocale", lambda *a: ("vi_VN", "UTF-8"))
        assert tdl.detect_lang([]) == "vi"

    @pytest.mark.parametrize(
        ("loc", "expected"),
        [
            (("vi_VN", "UTF-8"), "vi"),
            (("Vietnamese_Vietnam", "1258"), "vi"),
            (("en_US", "UTF-8"), "en"),
            ((None, None), "en"),
        ],
    )
    def test_locale(self, tdl, monkeypatch, loc, expected):
        monkeypatch.setattr(locale, "getlocale", lambda *a: loc)
        assert tdl.detect_lang([]) == expected

    def test_locale_error(self, tdl, monkeypatch):
        def broken(*a):
            raise ValueError("bad locale")

        monkeypatch.setattr(locale, "getlocale", broken)
        assert tdl.detect_lang([]) == "en"
