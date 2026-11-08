"""Facebook reels (issue #34): links, the yt-dlp read, login and --cookies, stop rules, defaults, CSV and tags.

yt-dlp is used only to extract information. The tests replace its Facebook extractor's answer
(FacebookIE._real_extract) with hand-written ones: DASH formats built by yt-dlp's own MPD parser from a
hand-written manifest, plus the progressive sd/hd links, as FacebookIE returns them. No test touches the
network: short links go to a fake server, aria2c and ffmpeg are fakes too.
"""
from __future__ import annotations

import copy
import csv
import io
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
import yt_dlp
from yt_dlp.extractor.facebook import FacebookIE
from yt_dlp.networking import Response
from yt_dlp.networking.exceptions import HTTPError
from yt_dlp.utils import ExtractorError

from tiktok_dl import cli
from tiktok_dl.core import console, download, mux, pacing, records
from tiktok_dl.core.errors import ScrapeError
from tiktok_dl.core.util import UA
from tiktok_dl.sites import facebook

VIDEO_ID = "1195289147628387"
PAGE_ID = "100040874179269"
REEL = f"https://www.facebook.com/reel/{VIDEO_ID}"
REEL_LINK = f"https://www.facebook.com/reel/{VIDEO_ID}/"
WATCH_URL = f"https://www.facebook.com/watch/?v={VIDEO_ID}"
CDN = "https://video.xx.fbcdn.net/o1/v/t2/f2/m69"

# A reel's DASH manifest as Facebook sends it (two video sizes, one audio stream, single files).
MPD = f'''<?xml version="1.0"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" minBufferTime="PT1.500S" type="static"
     mediaPresentationDuration="PT12.4S" profiles="urn:mpeg:dash:profile:isoff-on-demand:2011">
<Period id="0">
<AdaptationSet segmentAlignment="true" maxWidth="1080" maxHeight="1920" par="9:16" lang="und"
               subsegmentAlignment="true" subsegmentStartsWithSAP="1">
<Representation id="1v" mimeType="video/mp4" codecs="avc1.64001F" width="720" height="1280" frameRate="30"
                sar="1:1" startWithSAP="1" bandwidth="1500000" FBQualityClass="hd" FBQualityLabel="720p">
<BaseURL>{CDN}/720.mp4?oh=a&amp;oe=b</BaseURL>
<SegmentBase indexRangeExact="true" indexRange="821-900"><Initialization range="0-820"/></SegmentBase>
</Representation>
<Representation id="2v" mimeType="video/mp4" codecs="avc1.640028" width="1080" height="1920" frameRate="30"
                sar="1:1" startWithSAP="1" bandwidth="3000000" FBQualityClass="hd" FBQualityLabel="1080p">
<BaseURL>{CDN}/1080.mp4?oh=a&amp;oe=b</BaseURL>
<SegmentBase indexRangeExact="true" indexRange="821-900"><Initialization range="0-820"/></SegmentBase>
</Representation>
</AdaptationSet>
<AdaptationSet segmentAlignment="true" lang="und" subsegmentAlignment="true" subsegmentStartsWithSAP="1">
<Representation id="3a" mimeType="audio/mp4" codecs="mp4a.40.5" audioSamplingRate="44100" startWithSAP="1"
                bandwidth="64000">
<BaseURL>{CDN}/audio.mp4?oh=a&amp;oe=b</BaseURL>
<SegmentBase indexRangeExact="true" indexRange="821-900"><Initialization range="0-820"/></SegmentBase>
</Representation>
</AdaptationSet></Period></MPD>'''
VIDEO_1080, VIDEO_720, AUDIO = f"{CDN}/1080.mp4?oh=a&oe=b", f"{CDN}/720.mp4?oh=a&oe=b", f"{CDN}/audio.mp4?oh=a&oe=b"
SD, HD = f"{CDN}/sd.mp4?oh=c", f"{CDN}/hd.mp4?oh=c"


def reel_info(dash: bool = True) -> dict:
    """FacebookIE's answer for a reel (yt-dlp 2026.10.26): the DASH formats from its MPD parser, the
    progressive sd/hd links, the crawler User-Agent its process_formats() sets, and the page metadata
    (for reels the "uploader" is the page title)."""
    ie = FacebookIE(yt_dlp.YoutubeDL({"quiet": True}))
    formats = ie._parse_mpd_formats(ET.fromstring(MPD)) if dash else []
    formats += [{"format_id": "sd", "quality": -3, "url": SD}, {"format_id": "hd", "quality": -2, "url": HD}]
    for f in formats:
        f.setdefault("http_headers", {})["User-Agent"] = "facebookexternalhit/1.1"
        f.setdefault("downloader_options", {})["http_chunk_size"] = 250 << 20
    title = "9.7K views &#xb7; 352 reactions | Sunset #beach | Some Page"
    return {
        "id": VIDEO_ID, "formats": formats, "subtitles": {}, "automatic_captions": {},
        "thumbnail": "https://scontent.xx.fbcdn.net/v/cover.jpg?oh=d",
        "uploader_id": PAGE_ID, "uploader": title, "title": title,
        "description": "Sunset #beach\nsecond line", "timestamp": 1700000000, "duration": 12.4,
        "view_count": 9700, "like_count": 352, "comment_count": 12, "repost_count": 5,
    }


def http_error(status: int, headers: dict | None = None) -> ExtractorError:
    """What FacebookIE raises when the page request fails (as _download_webpage wraps it)."""
    response = Response(io.BytesIO(b""), WATCH_URL, headers or {}, status=status)
    return ExtractorError(f"Unable to download webpage: HTTP Error {status}", cause=HTTPError(response),
                          expected=True)


def raise_(error):
    """An answer that raises inside the extractor (a function: each use builds a fresh error)."""
    def answer(ie, url):
        raise error() if callable(error) else error
    return answer


@pytest.fixture
def extractor(monkeypatch):
    """yt-dlp's Facebook extractor: answers in order, recording each call (URL, the YoutubeDL params, the
    cookies yt-dlp would send). yt-dlp's own download and format processing must never run."""
    ex = SimpleNamespace(answers=[], calls=[])

    def real_extract(ie, url):
        ydl = ie._downloader
        ex.calls.append(SimpleNamespace(url=url, params=dict(ydl.params),
                                        cookies=ydl.cookiejar.get_cookie_header("https://www.facebook.com/")))
        if not ex.answers:
            raise AssertionError(f"unexpected extraction: {url}")
        answer = ex.answers.pop(0)
        return answer(ie, url) if callable(answer) else copy.deepcopy(answer)

    def never(*args, **kwargs):
        raise AssertionError("yt-dlp may only extract information")

    monkeypatch.setattr(FacebookIE, "_real_extract", real_extract)
    for name in ("process_ie_result", "process_info", "dl", "download"):
        monkeypatch.setattr(yt_dlp.YoutubeDL, name, never)
    monkeypatch.setattr(pacing.time, "sleep", lambda s: None)
    monkeypatch.setattr(pacing.STOP, "wait", lambda s=None: False)   # no real waits between attempts
    return ex


@pytest.fixture
def logged(monkeypatch):
    lines: list[str] = []
    monkeypatch.setattr(console, "log", lambda msg="", err=False: lines.append(msg))
    return lines


def make_opts(tmp_path, **overrides) -> SimpleNamespace:
    opts = SimpleNamespace(output=str(tmp_path / "out"), session_id=None, ig_session_id=None, cookies=None, wait=0,
                           proxy=None, limit=None, metadata_only=False, workers=None, batch=10, mux_jobs=1,
                           keep_parts=False, jobs=1, connections=None, cover=True, subs="original",
                           retry_all=False, slideshow=True, keep_images=False, slide_seconds=3.0)
    for key, value in overrides.items():
        setattr(opts, key, value)
    return opts


class FakeAria2:
    """aria2c: records argv and stdin, and "downloads" each URL to its dir/out."""
    calls: list[SimpleNamespace] = []

    def __init__(self, args, stdin=None, stdout=None, stderr=None):
        self.args = args

    def communicate(self, input=None, timeout=None):
        if input:
            FakeAria2.calls.append(SimpleNamespace(args=self.args, input=input.decode("utf-8")))
            url = folder = None
            for line in input.decode("utf-8").splitlines():
                if not line.startswith("  "):
                    url = line
                elif line.startswith("  dir="):
                    folder = Path(line[6:])
                elif line.startswith("  out="):
                    body = b"\xff\xd8\xff\xe0" if "cover" in url else b"\x00\x00\x00\x18ftypdash"
                    (folder / line[6:]).write_bytes(body + b"\x00" * 4096)
        return None, None

    def terminate(self):
        pass


@pytest.fixture
def tools(monkeypatch):
    """Fake aria2c (download.subprocess.Popen) and ffmpeg (mux.run_ffmpeg): ffmpeg writes its output."""
    FakeAria2.calls = []
    ffmpeg_calls: list[list[str]] = []
    monkeypatch.setattr(download.subprocess, "Popen", FakeAria2)

    def run_ffmpeg(cmd):
        ffmpeg_calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 4096)
        return True, ""

    monkeypatch.setattr(mux, "run_ffmpeg", run_ffmpeg)
    return SimpleNamespace(aria2=FakeAria2.calls, ffmpeg=ffmpeg_calls)


def run(tmp_path, target=REEL, **overrides) -> int:
    return cli.scrape_profile(target, make_opts(tmp_path, **overrides), "aria2c", "ffmpeg")


def out_dir(tmp_path) -> Path:
    return tmp_path / "out" / "facebook" / PAGE_ID


def statuses(tmp_path) -> dict:
    progress = records.Progress(out_dir(tmp_path) / ".progress.db")
    try:
        return dict(progress.db.execute("SELECT id, status FROM posts").fetchall())
    finally:
        progress.close()


# --------------------------------------------------------------------------- #
# Links
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("link", [
    REEL,
    f"https://facebook.com/reel/{VIDEO_ID}/?mibextid=rS40aB7S9Ucbxw6v",
    f"https://m.facebook.com/reel/{VIDEO_ID}",
    f"https://web.facebook.com/reel/{VIDEO_ID}/",
    f"facebook.com/reel/{VIDEO_ID}",
    f"https://www.facebook.com/watch/?v={VIDEO_ID}",
    f"https://www.facebook.com/watch?v={VIDEO_ID}&ref=sharing",
    f"https://www.facebook.com/watch/?ref=x&v={VIDEO_ID}",
    f"  HTTPS://WWW.FACEBOOK.COM/REEL/{VIDEO_ID}/ \n",
])
def test_reel_links(link):
    assert facebook.FacebookSite.parse(link) == (None, facebook.Entry(VIDEO_ID, REEL_LINK))


@pytest.fixture
def redirects(monkeypatch):
    """The short-link server: {url: Location}. Records each request (URL, whether redirects were followed,
    the Cookie header)."""
    srv = {"map": {}, "calls": []}

    def request(session, method, url, **kwargs):
        srv["calls"].append((method, url, kwargs.get("allow_redirects"), (kwargs.get("headers") or {}).get("Cookie")))
        r = requests.Response()
        r.url, r._content = url, b""
        if url in srv["map"]:
            r.status_code, r.headers["Location"] = 302, srv["map"][url]
        else:
            r.status_code = 200
        return r

    monkeypatch.setattr(requests.sessions.Session, "request", request)
    return srv


@pytest.mark.parametrize("destination", [
    f"https://www.facebook.com/reel/{VIDEO_ID}/?s=fb_watch",
    f"https://www.facebook.com/watch/?v={VIDEO_ID}&_rdr",
    f"https://www.facebook.com/SomePage/videos/a-title/{VIDEO_ID}/",
    f"https://m.facebook.com/SomePage/videos/{VIDEO_ID}/",
])
def test_short_links_are_followed_to_the_reel(redirects, destination):
    redirects["map"] = {"https://fb.watch/aBcD1234_x/": "https://www.facebook.com/l/?u=x",
                        "https://www.facebook.com/l/?u=x": destination}
    assert facebook.FacebookSite.parse("fb.watch/aBcD1234_x/") == (None, facebook.Entry(VIDEO_ID, REEL_LINK))
    assert [(c[1], c[2], c[3]) for c in redirects["calls"]] == [
        ("https://fb.watch/aBcD1234_x/", False, None), ("https://www.facebook.com/l/?u=x", False, None)]


def test_short_link_that_leads_nowhere(redirects, monkeypatch):
    monkeypatch.setattr(console, "LANG", "en")
    redirects["map"] = {"https://fb.watch/abc/": "https://www.facebook.com/login/?next=x"}
    with pytest.raises(ScrapeError, match=r"^short link does not lead to a Facebook reel: https://fb.watch/abc/$"):
        facebook.FacebookSite.parse("https://fb.watch/abc/")


@pytest.mark.parametrize("link", [
    "https://www.facebook.com/SomePage",
    "https://www.facebook.com/SomePage/videos/1234567890123456/",
    "https://www.facebook.com/share/r/AbC123xyz/",
    "https://www.facebook.com/watch/",
    "https://www.facebook.com/reel/abc",
])
def test_other_facebook_links_are_not_supported(link):
    with pytest.raises(ScrapeError, match=r"^only Facebook reel links are supported \(facebook.com/reel/<id>, "
                                          r"fb.watch/\.\.\., facebook.com/watch/\?v=<id>\): "):
        facebook.FacebookSite.parse(link)


def test_link_of_an_old_failed_row():
    assert facebook.FacebookSite.link(None, VIDEO_ID) == REEL_LINK


# --------------------------------------------------------------------------- #
# A public reel, start to finish
# --------------------------------------------------------------------------- #

NAME = f"2023-11-14_{VIDEO_ID}_Sunset beach second line.mp4"


def test_public_reel_downloads_with_audio_and_tags_and_rerun_skips(tmp_path, extractor, tools, logged):
    extractor.answers = [reel_info()]
    assert run(tmp_path) == 0

    # One extraction, of the reel's watch page, with no cookies and nothing but information.
    assert [c.url for c in extractor.calls] == [WATCH_URL]
    assert extractor.calls[0].cookies is None
    assert "cookiefile" not in extractor.calls[0].params

    folder = out_dir(tmp_path)
    assert (folder / NAME).is_file()
    assert statuses(tmp_path) == {VIDEO_ID: "done"}

    # aria2c downloads the largest DASH video, the DASH audio and the cover, as the core does for every
    # site: our browser User-Agent, Facebook's referer, no cookie. Never the crawler User-Agent.
    stdin = tools.aria2[0].input.splitlines()
    urls = [line for line in stdin if not line.startswith("  ")]
    assert urls == [VIDEO_1080, AUDIO, "https://scontent.xx.fbcdn.net/v/cover.jpg?oh=d"]
    args = tools.aria2[0].args
    assert f"--user-agent={UA}" in args and "--referer=https://www.facebook.com/" in args
    assert "facebookexternalhit" not in " ".join(args) + tools.aria2[0].input
    assert "header=Cookie" not in tools.aria2[0].input

    # ffmpeg muxes video + audio and writes the tags.
    cmd = tools.ffmpeg[0]
    assert [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "-map"][:2] == ["0:v:0", "1:a:0"]
    assert cmd[cmd.index("-i", cmd.index("-i") + 1) + 1].endswith("audio.m4a")
    for tag in ("album=Facebook Some Page", "artist=Some Page", "genre=Facebook", f"episode_id={VIDEO_ID}",
                "date=2023-11-14", "title=Sunset #beach"):
        assert tag in cmd

    with open(folder / "videos.csv", encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["id"] == VIDEO_ID and rows[0]["url"] == REEL_LINK and rows[0]["file"] == NAME
    assert (rows[0]["plays"], rows[0]["likes"], rows[0]["comments"], rows[0]["shares"]) == ("9700", "352", "12", "5")
    meta = json.loads((folder / "metadata" / f"{VIDEO_ID}.json").read_text(encoding="utf-8"))
    assert meta["raw"]["id"] == VIDEO_ID and "facebookexternalhit" not in json.dumps(meta)

    # A re-run extracts once to find the page's folder, then skips the reel.
    extractor.calls.clear()
    tools.aria2.clear()
    extractor.answers = [reel_info()]
    assert run(tmp_path) == 0
    assert len(extractor.calls) == 1 and tools.aria2 == []
    assert any("Nothing left to download" in line for line in logged)


def test_without_dash_the_largest_progressive_link_is_used(tmp_path, extractor, tools):
    extractor.answers = [reel_info(dash=False)]
    assert run(tmp_path) == 0
    urls = [line for line in tools.aria2[0].input.splitlines() if not line.startswith("  ")]
    assert urls[0] == HD and AUDIO not in urls
    assert [tools.ffmpeg[0][i + 1] for i, a in enumerate(tools.ffmpeg[0]) if a == "-map"][:2] == ["0:v:0", "0:a?"]


def test_the_smaller_dash_video_is_the_second_mirror():
    media = facebook.media(reel_info())
    assert media.video == [VIDEO_1080, VIDEO_720] and media.audio == AUDIO


def test_segmented_and_hls_formats_are_not_used():
    info = reel_info(dash=False)
    info["formats"] = [{"format_id": "hls-1", "protocol": "m3u8_native", "url": f"{CDN}/x.m3u8",
                        "vcodec": "avc1", "acodec": "mp4a", "height": 1920, "width": 1080},
                       {"format_id": "d", "protocol": "http_dash_segments", "url": f"{CDN}/x.mpd",
                        "fragments": [{"url": "a"}], "vcodec": "avc1", "acodec": "none", "height": 1920}]
    media = facebook.media(info)
    assert media.video == [] and media.unavailable


def test_metadata_only(tmp_path, extractor, tools):
    extractor.answers = [reel_info()]
    assert run(tmp_path, metadata_only=True) == 0
    assert (out_dir(tmp_path) / "metadata" / f"{VIDEO_ID}.json").is_file() and tools.aria2 == []


@pytest.mark.parametrize("uploader_id", [None, "", "../x", "Some Page"])
def test_unexpected_page_id_is_not_a_folder(tmp_path, extractor, uploader_id):
    info = reel_info()
    info["uploader_id"] = uploader_id
    extractor.answers = [info]
    with pytest.raises(ScrapeError, match="no valid uploader_id"):
        run(tmp_path)
    assert not (tmp_path / "out").exists()
