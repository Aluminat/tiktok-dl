"""Posts that can never be downloaded are skipped on re-runs (issue #8)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

USER = "someone"


def make_item(pid: str, **extra) -> dict:
    item = {"id": pid, "createTime": 1700000000, "desc": "clip",
            "author": {"uniqueId": USER}, "video": {"playAddr": f"https://cdn.example/{pid}.mp4"}}
    item.update(extra)
    return item


def no_link_item(pid: str, **extra) -> dict:
    return make_item(pid, video={}, **extra)


def make_opts(tmp_path, **overrides) -> SimpleNamespace:
    opts = SimpleNamespace(output=str(tmp_path), session_id=None, wait=0, proxy=None, limit=None,
                           metadata_only=False, workers=1, batch=10, mux_jobs=1, keep_parts=False,
                           jobs=1, connections=1, cover=False, subs="none", retry_all=False)
    for key, value in overrides.items():
        setattr(opts, key, value)
    return opts


@pytest.fixture
def run(tdl, tmp_path, monkeypatch):
    """Run scrape_profile on a profile whose posts behave as given in `behaviours`.

    Each behaviour is an item dict (fetched fine) or a ScrapeError (raised by the page fetch).
    Returns a function that runs once and gives back the post ids whose page was fetched.
    """
    behaviours: dict = {}
    finalize_errors: dict = {}
    fetched: list[str] = []

    class FakeTikTok:
        def __init__(self, *args, **kwargs):
            self.s = None

        def item(self, username, video_id):
            fetched.append(video_id)
            b = behaviours[video_id]
            if isinstance(b, Exception):
                raise b
            return b

        def cookie_header(self):
            return ""

    def fake_finalize(post, out_dir, ffmpeg, opts):
        if post.id in finalize_errors:
            raise finalize_errors[post.id]
        (out_dir / f"{post.id}.mp4").write_bytes(b"x")
        return f"{post.id}.mp4", 1, ""

    monkeypatch.setattr(tdl, "TikTok", FakeTikTok)
    monkeypatch.setattr(tdl, "save_profile", lambda *a: None)
    monkeypatch.setattr(tdl, "list_posts", lambda *a: [{"id": pid} for pid in behaviours])
    monkeypatch.setattr(tdl, "download_requests", lambda *a: None)
    monkeypatch.setattr(tdl, "finalize", fake_finalize)

    def go(**overrides) -> list[str]:
        fetched.clear()
        tdl.scrape_profile(f"@{USER}", make_opts(tmp_path, **overrides), None, None)
        return list(fetched)

    go.behaviours = behaviours
    go.finalize_errors = finalize_errors
    return go


def statuses(tdl, tmp_path) -> dict:
    progress = tdl.Progress(tmp_path / USER / ".progress.db")
    try:
        return dict(progress.db.execute("SELECT id, status FROM posts").fetchall())
    finally:
        progress.db.close()   # Windows keeps the file locked otherwise


def failed_txt(tmp_path) -> str:
    return (tmp_path / USER / "failed.txt").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Which errors count as permanent
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(("make", "expected"), [
    (lambda tdl: tdl.ScrapeError("post does not exist or was deleted", 10204), "skipped"),
    (lambda tdl: tdl.ScrapeError("post is private", 10216), "error"),
    (lambda tdl: tdl.ScrapeError("TikTok server error", 10101), "error"),
    (lambda tdl: tdl.ScrapeError("connection reset"), "error"),           # network error, no code
    (lambda tdl: no_link_item("1", isAd=True), "skipped"),               # ad
    (lambda tdl: no_link_item("1", isECVideo=True), "skipped"),          # TikTok Shop
    (lambda tdl: no_link_item("1"), "error"),                            # restricted, --session-id may help
    (lambda tdl: make_item("1"), "done"),
], ids=["deleted", "private", "server-error", "network", "ad", "shop", "no-link", "ok"])
def test_which_errors_are_permanent(tdl, tmp_path, run, make, expected):
    run.behaviours["1"] = make(tdl)
    run()
    assert statuses(tdl, tmp_path)["1"] == expected


def test_finalize_error_is_temporary(tdl, tmp_path, run):
    run.behaviours["1"] = make_item("1")
    run.finalize_errors["1"] = tdl.ScrapeError("ffmpeg error: broken pipe")
    run()
    assert statuses(tdl, tmp_path)["1"] == "error"


def test_rate_limit_is_temporary(tdl, tmp_path, run):
    run.behaviours["1"] = tdl.ScrapeError("HTTP 429, page has no data")
    run()
    assert statuses(tdl, tmp_path)["1"] == "error"


def test_ad_post_is_permanent_but_restricted_is_not(tdl, tmp_path):
    opts = make_opts(tmp_path)
    assert tdl.Post(no_link_item("1", isAd=True), tmp_path, opts).permanent
    assert not tdl.Post(no_link_item("1"), tmp_path, opts).permanent
    assert not tdl.Post(make_item("1"), tmp_path, opts).permanent


# --------------------------------------------------------------------------- #
# Re-runs
# --------------------------------------------------------------------------- #

def test_permanent_posts_fail_once_then_are_skipped(tdl, tmp_path, run, capsys):
    run.behaviours["10"] = tdl.ScrapeError("post does not exist or was deleted", 10204)
    run.behaviours["20"] = no_link_item("20", isAd=True)
    run.behaviours["30"] = make_item("30")

    assert sorted(run()) == ["10", "20", "30"]
    first = capsys.readouterr().out
    assert "✗ 2" in first

    assert run() == []   # no page is fetched for any of them
    out = capsys.readouterr().out
    assert "Skipping 3 posts: 1 already downloaded, 2 that can never be downloaded" in out
    assert "--retry-all" in out
    assert "Nothing left to download" in out

    text = failed_txt(tmp_path)
    assert f"https://www.tiktok.com/@{USER}/video/10\n    could not read the post page: post does not exist" in text
    assert f"https://www.tiktok.com/@{USER}/video/20\n    ad / TikTok Shop video" in text
    assert "(skipped)" in text
    assert "--retry-all" in text
    assert "/video/30" not in text


def test_already_downloaded_message_unchanged_without_permanent(tdl, tmp_path, run, capsys):
    run.behaviours["30"] = make_item("30")
    run()
    capsys.readouterr()
    assert run() == []
    out = capsys.readouterr().out
    assert "Skipping 1 posts already downloaded." in out


def test_retry_all_fetches_skipped_posts_again(tdl, tmp_path, run, capsys):
    run.behaviours["10"] = tdl.ScrapeError("post does not exist or was deleted", 10204)
    run.behaviours["20"] = no_link_item("20", isECVideo=True)
    run()
    assert run() == []

    assert sorted(run(retry_all=True)) == ["10", "20"]

    # TikTok now serves the deleted post again: it is downloaded and leaves failed.txt.
    run.behaviours["10"] = make_item("10")
    assert sorted(run(retry_all=True)) == ["10", "20"]
    assert statuses(tdl, tmp_path) == {"10": "done", "20": "skipped"}
    text = failed_txt(tmp_path)
    assert "/video/10" not in text
    assert "/video/20" in text


def test_temporary_errors_are_retried_every_run(tdl, tmp_path, run):
    run.behaviours["10"] = tdl.ScrapeError("connection reset")
    run.behaviours["20"] = make_item("20")
    run.finalize_errors["20"] = tdl.ScrapeError("ffmpeg error: broken pipe")
    run.behaviours["30"] = no_link_item("30")

    for _ in range(3):
        assert sorted(run()) == ["10", "20", "30"]
    text = failed_txt(tmp_path)
    assert "(skipped)" not in text
    assert "--retry-all" not in text


def test_metadata_only_skips_deleted_posts(tdl, tmp_path, run, capsys):
    run.behaviours["10"] = tdl.ScrapeError("post does not exist or was deleted", 10204)
    run.behaviours["20"] = make_item("20")
    assert sorted(run(metadata_only=True)) == ["10", "20"]
    assert run(metadata_only=True) == []
    assert run(metadata_only=True, retry_all=True) == ["10"]
