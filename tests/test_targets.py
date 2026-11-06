"""Target parsing and short-link resolution (all HTTP is mocked)."""
from __future__ import annotations

import pytest
import requests


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("tiktok", ("tiktok", None)),
        ("@tiktok", ("tiktok", None)),
        ("  @tiktok \n", ("tiktok", None)),
        ("user.name_01", ("user.name_01", None)),
        ("https://www.tiktok.com/@tiktok", ("tiktok", None)),
        ("https://www.tiktok.com/@tiktok/", ("tiktok", None)),
        ("https://www.tiktok.com/@tiktok?lang=en", ("tiktok", None)),
        ("tiktok.com/@tiktok", ("tiktok", None)),
        ("https://www.tiktok.com/@tiktok/video/7300000000000000001", ("tiktok", "7300000000000000001")),
        ("https://www.tiktok.com/@tiktok/video/7300000000000000001?is_from_webapp=1",
         ("tiktok", "7300000000000000001")),
        ("https://www.tiktok.com/@some.user/photo/7300000000000000002", ("some.user", "7300000000000000002")),
        ("HTTPS://WWW.TIKTOK.COM/@TikTok/VIDEO/123", ("TikTok", "123")),
    ],
)
def test_parse_target(tdl, text, expected):
    assert tdl.parse_target(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "@",
        "not a user",
        "bad/name",
        "https://example.com/foo",
        "a" * 65,
    ],
)
def test_parse_target_invalid(tdl, text):
    with pytest.raises(tdl.ScrapeError):
        tdl.parse_target(text)


def test_parse_target_expands_short_link(tdl, monkeypatch):
    seen = []

    def fake_resolve(text):
        seen.append(text)
        return "https://www.tiktok.com/@tiktok/video/123?_r=1"

    monkeypatch.setattr(tdl, "resolve_short_link", fake_resolve)
    assert tdl.parse_target(" https://vt.tiktok.com/ZSabc123/ ") == ("tiktok", "123")
    assert seen == ["https://vt.tiktok.com/ZSabc123/"]


class FakeResponse:
    def __init__(self, location=None):
        self.headers = {"Location": location} if location else {}


def fake_get(responses, calls):
    """A requests.get replacement that returns `responses[url]` and records each URL."""
    def get(url, **kwargs):
        calls.append(url)
        assert kwargs.get("allow_redirects") is False
        return responses[url]
    return get


def test_resolve_short_link_follows_redirects(tdl, monkeypatch):
    final = "https://www.tiktok.com/@tiktok/video/123?_r=1"
    responses = {
        "https://vt.tiktok.com/ZSabc123/": FakeResponse("https://www.tiktok.com/t/ZSabc123/"),
        "https://www.tiktok.com/t/ZSabc123/": FakeResponse(final),
    }
    calls = []
    monkeypatch.setattr(tdl.requests, "get", fake_get(responses, calls))
    assert tdl.resolve_short_link("https://vt.tiktok.com/ZSabc123/") == final
    assert calls == ["https://vt.tiktok.com/ZSabc123/", "https://www.tiktok.com/t/ZSabc123/"]


def test_resolve_short_link_adds_scheme(tdl, monkeypatch):
    final = "https://www.tiktok.com/@tiktok"
    calls = []
    monkeypatch.setattr(tdl.requests, "get", fake_get({"https://vm.tiktok.com/ZMabc/": FakeResponse(final)}, calls))
    assert tdl.resolve_short_link("vm.tiktok.com/ZMabc/") == final
    assert calls == ["https://vm.tiktok.com/ZMabc/"]


@pytest.mark.parametrize("text", ["tiktok", "@tiktok", "https://www.tiktok.com/@tiktok/video/1"])
def test_resolve_short_link_leaves_other_text(tdl, monkeypatch, text):
    monkeypatch.setattr(tdl.requests, "get", fake_get({}, []))
    assert tdl.resolve_short_link(text) == text


def test_resolve_short_link_without_redirect(tdl, monkeypatch):
    monkeypatch.setattr(tdl.requests, "get", fake_get({"https://vt.tiktok.com/ZSabc/": FakeResponse()}, []))
    with pytest.raises(tdl.ScrapeError):
        tdl.resolve_short_link("https://vt.tiktok.com/ZSabc/")


def test_resolve_short_link_network_error(tdl, monkeypatch):
    def broken(url, **kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(tdl.requests, "get", broken)
    with pytest.raises(tdl.ScrapeError):
        tdl.resolve_short_link("https://vt.tiktok.com/ZSabc/")
