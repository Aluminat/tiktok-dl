"""The TikTok session ID must not leak: aria2c command line, cookie file on disk, shell history."""
from __future__ import annotations

import sys

import pytest

SECRET = "s3cr3t-session-id"


class TestResolveSessionId:
    def test_flag_only(self, tdl):
        assert tdl.resolve_session_id(SECRET, {}) == SECRET

    def test_env_only(self, tdl):
        assert tdl.resolve_session_id(None, {"TIKTOK_SESSIONID": SECRET}) == SECRET

    def test_flag_wins_over_env(self, tdl):
        assert tdl.resolve_session_id("from-flag", {"TIKTOK_SESSIONID": "from-env"}) == "from-flag"

    def test_neither(self, tdl):
        assert tdl.resolve_session_id(None, {}) is None

    def test_blank_values_count_as_unset(self, tdl):
        assert tdl.resolve_session_id("", {"TIKTOK_SESSIONID": "  "}) is None
        assert tdl.resolve_session_id("  ", {"TIKTOK_SESSIONID": SECRET}) == SECRET

    def test_strips_whitespace(self, tdl):
        assert tdl.resolve_session_id(None, {"TIKTOK_SESSIONID": f" {SECRET}\n"}) == SECRET

    def test_reads_os_environ_by_default(self, tdl, monkeypatch):
        monkeypatch.setenv("TIKTOK_SESSIONID", SECRET)
        assert tdl.resolve_session_id(None) == SECRET


class TestMainSessionSource:
    """--check-session works with the env var alone, and --session-id overrides it."""

    @pytest.fixture
    def run_main(self, tdl, monkeypatch):
        # main() sets these module globals; restore them after the test.
        monkeypatch.setattr(tdl, "LANG", tdl.LANG)
        monkeypatch.setattr(tdl, "_COLOR", tdl._COLOR)
        seen = []

        def fake_whoami(self):
            seen.append(self.s.cookies.get("sessionid"))
            return {"uniqueId": "me", "nickName": "Me"}

        monkeypatch.setattr(tdl.TikTok, "whoami", fake_whoami)

        def run(*args):
            monkeypatch.setattr(sys, "argv", ["tiktok-dl", "--lang", "en", "--no-color", *args])
            with pytest.raises(SystemExit) as exc:
                tdl.main()
            return exc.value.code, seen

        return run

    def test_check_session_from_env(self, run_main, monkeypatch):
        monkeypatch.setenv("TIKTOK_SESSIONID", SECRET)
        code, seen = run_main("--check-session")
        assert code == 0
        assert seen == [SECRET]

    def test_flag_overrides_env(self, run_main, monkeypatch):
        monkeypatch.setenv("TIKTOK_SESSIONID", "from-env")
        code, seen = run_main("--check-session", "--session-id", "from-flag")
        assert code == 0
        assert seen == ["from-flag"]

    def test_check_session_without_any_session_id(self, run_main, monkeypatch, capsys):
        monkeypatch.delenv("TIKTOK_SESSIONID", raising=False)
        code, seen = run_main("--check-session")
        assert code == 2  # argparse error
        assert seen == []
        assert "TIKTOK_SESSIONID" in capsys.readouterr().err


class FakePopen:
    """Records the aria2c command line and what is written to its stdin."""
    calls: list[FakePopen] = []

    def __init__(self, args, stdin=None, stdout=None, stderr=None):
        self.args = args
        self.stdin_pipe = stdin
        self.input = None
        FakePopen.calls.append(self)

    def communicate(self, input=None, timeout=None):
        self.input = input
        return None, None

    def wait(self, timeout=None):
        return 0

    def terminate(self):
        pass


class TestDownloadAria2:
    @pytest.fixture
    def popen(self, tdl, monkeypatch):
        FakePopen.calls = []
        monkeypatch.setattr(tdl.subprocess, "Popen", FakePopen)
        return FakePopen.calls

    def test_cookie_goes_to_stdin_not_argv(self, tdl, tmp_path, popen):
        dest = tmp_path / "Thư mục" / "123.mp4"
        cookie = f"sessionid={SECRET}; sid_tt={SECRET}"
        tdl.download_aria2("aria2c", [(["https://example.com/a.mp4"], dest, None)], cookie, 4, 4, 0)

        assert len(popen) == 1
        call = popen[0]
        assert not any(SECRET in arg for arg in call.args)
        assert "--input-file=-" in call.args
        assert call.stdin_pipe == tdl.subprocess.PIPE

        text = call.input.decode("utf-8")
        lines = text.splitlines()
        assert lines[0] == "https://example.com/a.mp4"
        assert f"  dir={dest.parent}" in lines
        assert "  out=123.mp4" in lines
        assert f"  header=Cookie: {cookie}" in lines

    def test_no_input_file_on_disk(self, tdl, tmp_path, popen):
        dest = tmp_path / "123.mp4"
        tdl.download_aria2("aria2c", [(["https://example.com/a.mp4"], dest, None)], f"sessionid={SECRET}", 1, 1, 0)
        files = [p for p in tmp_path.rglob("*") if p.is_file()]
        assert all(SECRET not in p.read_text(encoding="utf-8", errors="replace") for p in files)
        assert not (tmp_path / "aria2-input.txt").exists()

    def test_every_download_gets_the_cookie(self, tdl, tmp_path, popen):
        tasks = [([f"https://example.com/{i}.mp4"], tmp_path / f"{i}.mp4", None) for i in range(3)]
        tdl.download_aria2("aria2c", tasks, f"sessionid={SECRET}", 1, 1, 0)
        assert popen[0].input.decode("utf-8").count(f"  header=Cookie: sessionid={SECRET}") == 3

    def test_no_cookie_header_when_cookie_empty(self, tdl, tmp_path, popen):
        tdl.download_aria2("aria2c", [(["https://example.com/a.mp4"], tmp_path / "a.mp4", None)], "", 1, 1, 0)
        assert "header=Cookie" not in popen[0].input.decode("utf-8")

    def test_nothing_to_do_does_not_start_aria2c(self, tdl, tmp_path, popen):
        # attempt 1 but only one mirror: nothing left to try
        tdl.download_aria2("aria2c", [(["https://example.com/a.mp4"], tmp_path / "a.mp4", None)], "c=1", 1, 1, 1)
        assert popen == []


class TestCookieFileCleanup:
    def test_deleted_after_success(self, tdl, tmp_path, monkeypatch):
        seen = {}

        def fake_list_posts(username, limit, cookiefile):
            seen["path"] = cookiefile
            with open(cookiefile, encoding="utf-8") as fh:
                seen["text"] = fh.read()
            return [{"id": "1"}]

        monkeypatch.setattr(tdl, "list_posts", fake_list_posts)
        assert tdl.list_posts_with_session("user", None, SECRET, tmp_path) == [{"id": "1"}]
        assert SECRET in seen["text"]  # yt-dlp did get the session
        assert not list(tmp_path.iterdir())

    @pytest.mark.parametrize("error", ["scrape", "interrupt"])
    def test_deleted_after_failure(self, tdl, tmp_path, monkeypatch, error):
        def fake_list_posts(username, limit, cookiefile):
            if error == "scrape":
                raise tdl.ScrapeError("boom")
            raise KeyboardInterrupt

        monkeypatch.setattr(tdl, "list_posts", fake_list_posts)
        with pytest.raises((tdl.ScrapeError, KeyboardInterrupt)):
            tdl.list_posts_with_session("user", None, SECRET, tmp_path)
        assert not list(tmp_path.iterdir())

    def test_no_cookie_file_without_session(self, tdl, tmp_path, monkeypatch):
        got = {}

        def fake_list_posts(username, limit, cookiefile):
            got["cookiefile"] = cookiefile
            return []

        monkeypatch.setattr(tdl, "list_posts", fake_list_posts)
        assert tdl.list_posts_with_session("user", 5, None, tmp_path) == []
        assert got["cookiefile"] is None
        assert not list(tmp_path.iterdir())
