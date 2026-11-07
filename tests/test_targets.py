"""Target parsing and short-link resolution (all HTTP is mocked)."""
from __future__ import annotations

import pytest
import requests

from tiktok_dl.core.errors import ScrapeError
from tiktok_dl.sites import tiktok


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
def test_parse_target(text, expected):
    assert tiktok.parse_target(text) == expected


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
def test_parse_target_invalid(text):
    with pytest.raises(ScrapeError):
        tiktok.parse_target(text)


def test_parse_target_expands_short_link(monkeypatch):
    seen = []

    def fake_resolve(text):
        seen.append(text)
        return "https://www.tiktok.com/@tiktok/video/123?_r=1"

    monkeypatch.setattr(tiktok, "resolve_short_link", fake_resolve)
    assert tiktok.parse_target(" https://vt.tiktok.com/ZSabc123/ ") == ("tiktok", "123")
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


def test_resolve_short_link_follows_redirects(monkeypatch):
    final = "https://www.tiktok.com/@tiktok/video/123?_r=1"
    responses = {
        "https://vt.tiktok.com/ZSabc123/": FakeResponse("https://www.tiktok.com/t/ZSabc123/"),
        "https://www.tiktok.com/t/ZSabc123/": FakeResponse(final),
    }
    calls = []
    monkeypatch.setattr(tiktok.requests, "get", fake_get(responses, calls))
    assert tiktok.resolve_short_link("https://vt.tiktok.com/ZSabc123/") == final
    assert calls == ["https://vt.tiktok.com/ZSabc123/", "https://www.tiktok.com/t/ZSabc123/"]


def test_resolve_short_link_adds_scheme(monkeypatch):
    final = "https://www.tiktok.com/@tiktok"
    calls = []
    monkeypatch.setattr(tiktok.requests, "get", fake_get({"https://vm.tiktok.com/ZMabc/": FakeResponse(final)}, calls))
    assert tiktok.resolve_short_link("vm.tiktok.com/ZMabc/") == final
    assert calls == ["https://vm.tiktok.com/ZMabc/"]


@pytest.mark.parametrize("text", ["tiktok", "@tiktok", "https://www.tiktok.com/@tiktok/video/1"])
def test_resolve_short_link_leaves_other_text(monkeypatch, text):
    monkeypatch.setattr(tiktok.requests, "get", fake_get({}, []))
    assert tiktok.resolve_short_link(text) == text


def test_resolve_short_link_without_redirect(monkeypatch):
    monkeypatch.setattr(tiktok.requests, "get", fake_get({"https://vt.tiktok.com/ZSabc/": FakeResponse()}, []))
    with pytest.raises(ScrapeError):
        tiktok.resolve_short_link("https://vt.tiktok.com/ZSabc/")


def test_resolve_short_link_network_error(monkeypatch):
    def broken(url, **kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(tiktok.requests, "get", broken)
    with pytest.raises(ScrapeError):
        tiktok.resolve_short_link("https://vt.tiktok.com/ZSabc/")


def test_resolve_short_link_relative_redirect_to_post(monkeypatch):
    calls = []
    responses = {"https://vt.tiktok.com/ZSabc123/": FakeResponse("/@tiktok/video/123?_r=1")}
    monkeypatch.setattr(tiktok.requests, "get", fake_get(responses, calls))
    resolved = tiktok.resolve_short_link("https://vt.tiktok.com/ZSabc123/")
    assert resolved == "https://vt.tiktok.com/@tiktok/video/123?_r=1"
    assert tiktok.parse_target(resolved) == ("tiktok", "123")


def test_resolve_short_link_relative_hops_to_profile(monkeypatch):
    calls = []
    responses = {
        "https://www.tiktok.com/t/ZTabc/": FakeResponse("/t/ZTabc/?next=1"),
        "https://www.tiktok.com/t/ZTabc/?next=1": FakeResponse("/@some.user?lang=en"),
    }
    monkeypatch.setattr(tiktok.requests, "get", fake_get(responses, calls))
    resolved = tiktok.resolve_short_link("www.tiktok.com/t/ZTabc/")
    assert resolved == "https://www.tiktok.com/@some.user?lang=en"
    assert calls == ["https://www.tiktok.com/t/ZTabc/", "https://www.tiktok.com/t/ZTabc/?next=1"]
    assert tiktok.parse_target(resolved) == ("some.user", None)


def test_resolve_short_link_scheme_relative_redirect(monkeypatch):
    responses = {"https://vm.tiktok.com/ZMabc/": FakeResponse("//www.tiktok.com/@tiktok/photo/456")}
    monkeypatch.setattr(tiktok.requests, "get", fake_get(responses, []))
    assert tiktok.resolve_short_link("https://vm.tiktok.com/ZMabc/") == "https://www.tiktok.com/@tiktok/photo/456"
