"""Facebook: single reel links (issue #34), read without login, or with the user's --cookies file.

Everything specific to Facebook lives here: its links, how its answers are read and what they mean.
Pages, stories and highlights are out of scope.

yt-dlp is used only to find a reel's video link, audio link and metadata: its Facebook extractor, as a
library (extract_info with download=False and process=False). The core downloads (aria2c, or requests)
and muxes (ffmpeg). Never used from yt-dlp:
- its downloader and its ffmpeg steps;
- its format processing (process=True), which writes the cookies yt-dlp would send into the answer;
- the "facebookexternalhit" crawler User-Agent its extractor puts on every format to dodge Facebook's
  rate limit: downloads use the core's own browser User-Agent. No impersonation, nothing that works
  around a check.
The --cookies file is read in memory (core.cookies) and its facebook.com cookies are put into yt-dlp's
cookie jar after the YoutubeDL is made. yt-dlp is never given the file (`cookiefile`), because it writes
that file back when it closes.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urljoin

import requests

from ..core import cookies
from ..core.console import fmt_int, t
from ..core.errors import RateLimited, ScrapeError
from ..core.pacing import PageReader
from ..core.site import Entry, Media, Post, Site, Verdict
from ..core.util import UA, to_int, uniq

# facebook.com with any subdomain (www., m., web.) and fb.watch short links.
# Without a scheme something must follow the host: a bare "fb.watch" is a valid TikTok username.
_HOST = r"(?:[\w-]+\.)*(?:facebook\.com|fb\.watch)"
FACEBOOK_LINK_RE = re.compile(rf"^(?:https?://{_HOST}(?:[/?#]|$)|{_HOST}[/?#])", re.I)
_FB = r"^(?:https?://)?(?:[\w-]+\.)*facebook\.com"
# The links of a reel: /reel/<id> and /watch/?v=<id>.
REEL_LINK_RES = (re.compile(rf"{_FB}/reel/(\d+)(?:[/?#]|$)", re.I),
                 re.compile(rf"{_FB}/watch/?\?(?:[^#]*&)?v=(\d+)(?:[&#]|$)", re.I))
# Where a short link may lead besides those: a page's video link.
VIDEOS_LINK_RE = re.compile(rf"{_FB}/[^/?#]+/videos/(?:[^/?#]+/)?(\d+)(?:[/?#]|$)", re.I)
SHORT_LINK_RE = re.compile(r"^(?:https?://)?(?:www\.)?fb\.watch/[\w-]+/?(?:[?#]|$)", re.I)
# The page's id names the output folder: a number, or a "pfbid..." id. The page's name is not used: for
# reels yt-dlp gives the page title ("9.7K views · 352 reactions | <caption> | <page>") as the uploader.
PAGE_ID_RE = re.compile(r"\d{1,20}|pfbid[A-Za-z0-9]{10,100}")

BASE = "https://www.facebook.com"


def reel_link(video_id: str) -> str:
    return f"{BASE}/reel/{video_id}/"


def watch_url(video_id: str) -> str:
    """The page yt-dlp's Facebook extractor reads for a reel (its reel extractor only points there)."""
    return f"{BASE}/watch/?v={video_id}"


def _reel_id(url: str, *, short_link_target: bool = False) -> Optional[str]:
    for pattern in REEL_LINK_RES + ((VIDEOS_LINK_RE,) if short_link_target else ()):
        m = pattern.match(url)
        if m:
            return m.group(1)
    return None


def resolve_short_link(text: str) -> str:
    """The reel id an fb.watch link leads to. Follows the redirects one by one (no cookies: a short link
    needs none), at most 5."""
    url = text if text.lower().startswith("http") else f"https://{text}"
    try:
        for _ in range(5):
            r = requests.get(url, headers={"User-Agent": UA}, allow_redirects=False, timeout=20)
            location = r.headers.get("Location")
            if not location:
                break
            url = urljoin(url, location)
            found = _reel_id(url, short_link_target=True)
            if found:
                return found
    except requests.RequestException as e:
        raise ScrapeError(t(f"could not open short link {text} ({e})", f"không mở được link rút gọn {text} ({e})"))
    raise ScrapeError(t(f"short link does not lead to a Facebook reel: {text}",
                        f"link rút gọn không dẫn tới reel Facebook: {text}"))


# --------------------------------------------------------------------------- #
# Answers: what each one means (classify)
# --------------------------------------------------------------------------- #

@dataclass
class Answer:
    """One yt-dlp extraction, shaped like the HTTP answer PageReader classifies: the HTTP status of the
    request that failed (200 when it did not), its headers (Retry-After), and yt-dlp's info or message."""
    status_code: int = 200
    headers: dict = field(default_factory=dict)
    info: Optional[dict] = None
    message: str = ""


# yt-dlp's text for Facebook's login wall (InfoExtractor.raise_login_required).
LOGIN_WALL = "only available for registered users"
# yt-dlp's text when Facebook says the video is not available (deleted, or not shown to this viewer).
NOT_AVAILABLE = "The video is not available"
NOT_FOUND = 404   # ScrapeError.code of a missing reel: Verdict.PERMANENT, skipped on re-runs


def classify(answer) -> Verdict:
    """What one Facebook answer means for the run: an extraction (Answer), or the ScrapeError a reel read
    failed with. A rate limit backs off; the login wall is BLOCKED: that link fails at once with the
    --cookies message (the run goes on with the next link); a missing reel is permanent."""
    if isinstance(answer, ScrapeError):
        return Verdict.PERMANENT if answer.code == NOT_FOUND else Verdict.OK
    if answer.status_code == 429:
        return Verdict.RATE_LIMITED
    if LOGIN_WALL in answer.message:
        return Verdict.BLOCKED
    return Verdict.OK


def login_message(with_cookies: bool) -> str:
    if with_cookies:
        return t("Facebook still shows a login wall with your --cookies file: its cookies are logged out or "
                 "expired. Export them again from a browser where you are logged in to Facebook "
                 "(see README, 'Facebook').",
                 "Facebook vẫn yêu cầu đăng nhập dù đã có file --cookies: cookie trong file đã đăng xuất hoặc "
                 "hết hạn. Hãy xuất lại từ trình duyệt đang đăng nhập Facebook (xem README, mục Facebook).")
    return t("Facebook shows a login wall for this reel. Export your facebook.com cookies from a browser where "
             "you are logged in, as a Netscape cookie file (cookies.txt), and add --cookies <file>. See README, "
             "'Facebook'. Meta may ban accounts that scrape: a secondary account is safer.",
             "Facebook yêu cầu đăng nhập để xem reel này. Hãy xuất cookie facebook.com từ trình duyệt đang "
             "đăng nhập, dạng file cookie Netscape (cookies.txt), rồi thêm --cookies <file>. Xem README, mục "
             "Facebook. Meta có thể khoá tài khoản tải tự động: nên dùng tài khoản phụ.")


def _entry_of(info: dict, video_id: str) -> Optional[dict]:
    """The reel in yt-dlp's answer: the answer itself, or the playlist entry with its id."""
    if info.get("_type") != "playlist":
        return info
    entries = [e for e in info.get("entries") or [] if isinstance(e, dict)]
    return next((e for e in entries if str(e.get("id")) == video_id), None) or next(
        (e for e in entries if e.get("formats")), None)


def _clean(info: dict) -> dict:
    """The answer as saved in metadata/<id>.json: without yt-dlp's per-request headers (the crawler
    User-Agent) and without any cookie field."""
    info = {k: v for k, v in info.items() if k not in ("http_headers", "cookies")}
    info["formats"] = [{k: v for k, v in f.items() if k not in ("http_headers", "cookies")}
                       for f in info.get("formats") or [] if isinstance(f, dict)]
    return info


def reel_answer(answer: Answer, video_id: str) -> Optional[dict]:
    """The reel from an extraction. None = no data, try again. Raises ScrapeError at once for a reel
    that cannot be read: NOT_FOUND for a missing one."""
    if answer.info is not None:
        entry = _entry_of(answer.info, video_id)
        if entry is None:
            raise ScrapeError(t("Facebook's answer has no video", "câu trả lời của Facebook không có video"))
        return _clean(entry)
    if answer.status_code in (404, 410) or answer.message.startswith(NOT_AVAILABLE):
        raise ScrapeError(t("reel does not exist, was deleted, or is not public (if you can see it while logged "
                            "in, add --cookies <file>)",
                            "reel không tồn tại, đã bị xoá, hoặc không công khai (nếu xem được khi đăng nhập, "
                            "hãy thêm --cookies <file>)"), NOT_FOUND)
    if answer.status_code >= 500:
        return None
    raise ScrapeError(t(f"Facebook gave no reel data (yt-dlp: {answer.message}). If the reel needs a login, add "
                        "--cookies <file>; if not, update yt-dlp: pip install -U yt-dlp",
                        f"Facebook không trả dữ liệu reel (yt-dlp: {answer.message}). Nếu reel cần đăng nhập, "
                        "hãy thêm --cookies <file>; nếu không, hãy cập nhật yt-dlp: pip install -U yt-dlp"))


# --------------------------------------------------------------------------- #
# Facebook client (yt-dlp, information only)
# --------------------------------------------------------------------------- #

class _Quiet:
    """yt-dlp's logger: say nothing. Our own messages report what went wrong."""

    def debug(self, msg):
        pass

    info = warning = error = debug


def extract(url: str, jar: list, proxy: Optional[str]) -> Answer:
    """yt-dlp's Facebook extractor on one page, information only. A network error raises
    requests.ConnectionError, so PageReader retries it like one."""
    import yt_dlp
    from yt_dlp.networking.exceptions import HTTPError, TransportError

    params = {"quiet": True, "no_warnings": True, "noprogress": True, "logger": _Quiet(), "skip_download": True,
              "socket_timeout": 25}
    if proxy:
        params["proxy"] = proxy
    with yt_dlp.YoutubeDL(params) as ydl:
        for cookie in jar:   # in memory only: no cookiefile, so nothing is written back
            ydl.cookiejar.set_cookie(cookie)
        try:
            return Answer(info=ydl.extract_info(url, download=False, process=False, ie_key="Facebook"))
        except yt_dlp.utils.DownloadError as e:
            error = e.exc_info[1] if e.exc_info and e.exc_info[1] else e
    message = str(getattr(error, "orig_msg", None) or error)
    cause = getattr(error, "cause", None)
    if isinstance(cause, HTTPError):
        return Answer(cause.status, dict(cause.response.headers), message=message)
    if isinstance(cause, TransportError) or isinstance(error, TransportError):
        raise requests.ConnectionError(message)
    return Answer(message=message)


class Facebook(PageReader):
    """Reel reads through yt-dlp, with the facebook.com cookies of --cookies if given. The cookies go to
    yt-dlp in memory only, for facebook.com: never to aria2c, never to the CDN that serves the files."""

    label = "Facebook"
    limit_codes = "429"

    def __init__(self, jar: list, wait: float = 2.0, proxy: Optional[str] = None):
        super().__init__(wait)
        self.s.headers.update({"User-Agent": UA, "Accept": "*/*"})   # the requests fallback for downloads
        if proxy:
            self.s.proxies = {"http": proxy, "https": proxy}
        self._jar = jar
        self._proxy = proxy

    classify = staticmethod(classify)

    def blocked_message(self) -> str:   # BLOCKED is Facebook's login wall
        return login_message(bool(self._jar))

    def cookie_header(self) -> str:
        """No cookie for downloads: the file links are signed, and the cookies must stay on facebook.com."""
        return ""

    def fetch(self, url: str) -> Answer:
        return extract(url, self._jar, self._proxy)

    def reel(self, video_id: str) -> dict:
        return self.read(watch_url(video_id), lambda answer: reel_answer(answer, video_id))


# --------------------------------------------------------------------------- #
# Posts
# --------------------------------------------------------------------------- #

def _usable(info: dict) -> list[dict]:
    """Formats aria2c can download as one file: a plain link, not HLS and not a segmented DASH stream."""
    return [f for f in info.get("formats") or [] if isinstance(f, dict) and f.get("url") and not f.get("fragments")
            and not str(f.get("protocol") or "").startswith(("m3u8", "http_dash_segments", "f4m", "rtmp"))]


def _format_rank(f: dict) -> tuple:
    """How good a video format is, as a sort key: its pixels, then its height, bitrate and quality."""
    return ((to_int(f.get("width")) or 0) * (to_int(f.get("height")) or 0), to_int(f.get("height")) or 0,
            f.get("tbr") or 0, f.get("quality") or 0)


def streams(info: dict) -> tuple[list[dict], Optional[dict]]:
    """(video formats, largest first; the audio format or None). The DASH video-only formats with the best
    DASH audio when Facebook gives both, else the formats with sound in them (sd/hd)."""
    usable = _usable(info)
    video_only = [f for f in usable if f.get("vcodec") not in (None, "none") and f.get("acodec") == "none"]
    audio_only = [f for f in usable if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none")]
    if video_only and audio_only:
        return (sorted(video_only, key=_format_rank, reverse=True),
                max(audio_only, key=lambda f: (f.get("abr") or f.get("tbr") or 0)))
    muxed = [f for f in usable if f.get("vcodec") != "none" and f.get("acodec") != "none"]
    return sorted(muxed, key=_format_rank, reverse=True), None


def media(info: dict) -> Media:
    """What a reel offers for download: the video (largest first), its separate sound stream, the cover."""
    videos, audio = streams(info)
    if not videos:
        return Media(unavailable=t("Facebook returned no download link for this reel",
                                   "Facebook không trả link tải cho reel này"))
    urls = uniq(f["url"] for f in videos)
    sizes = {f["url"]: to_int(f.get("filesize")) for f in videos if to_int(f.get("filesize"))}
    return Media(video=urls, sizes=sizes, audio=audio["url"] if audio else None, cover=info.get("thumbnail"))


def created_at(info: dict) -> datetime:
    return datetime.fromtimestamp(to_int(info.get("timestamp")) or 0, tz=timezone.utc)


def page_name(info: dict) -> Optional[str]:
    """The page's name. For reels yt-dlp's uploader is the page title, "<counts> | <caption> | <page>":
    the name is its last part."""
    uploader = html.unescape(str(info.get("uploader") or "")).strip()
    return uploader.rsplit(" | ", 1)[-1].strip() or None


def flatten(info: dict) -> dict:
    """The videos.csv row: TikTok's column names for what both sites have, in TikTok's order, then
    Facebook's own columns."""
    caption = info.get("description") or ""
    videos, audio = streams(info)
    best = videos[0] if videos else {}
    duration = info.get("duration")
    return {
        "id": str(info.get("id")),
        "url": reel_link(info.get("id")),
        "type": "reel",
        "created": created_at(info).isoformat(),
        "username": str(info.get("uploader_id") or ""),
        "nickname": page_name(info),
        "author_id": str(info.get("uploader_id") or "") or None,
        "description": caption,
        "hashtags": " ".join(re.findall(r"#\w+", caption)) or None,
        "plays": to_int(info.get("view_count")),
        "likes": to_int(info.get("like_count")),
        "comments": to_int(info.get("comment_count")),
        "shares": to_int(info.get("repost_count")),
        "duration": round(duration) if isinstance(duration, (int, float)) else None,
        "width": to_int(best.get("width")),
        "height": to_int(best.get("height")),
        # Facebook's own columns
        "title": html.unescape(str(info.get("title") or "")) or None,
        "video_format": best.get("format_id"),
        "audio_format": audio.get("format_id") if audio else None,
    }


def mux_tags(info: dict) -> dict:
    """Tags written into the MP4 (readable by Explorer / VLC / MediaInfo)."""
    f = flatten(info)
    when = created_at(info)
    caption = f["description"]
    page = f["nickname"] or f["username"]
    title = caption.splitlines()[0][:120] if caption.strip() else f"Facebook {f['id']}"
    comment = [f["url"], f"Views {fmt_int(f['plays'])} · Likes {fmt_int(f['likes'])} · "
                         f"Comments {fmt_int(f['comments'])} · Shares {fmt_int(f['shares'])}"]
    if f["hashtags"]:
        comment.append(f"Hashtags: {f['hashtags']}")
    return {
        "title": title,
        "artist": page,
        "album_artist": page,
        "album": f"Facebook {page}",
        "date": f"{when:%Y-%m-%d}",
        "creation_time": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "description": caption,
        "synopsis": caption,
        "comment": "\n".join(comment),
        "genre": "Facebook",
        "episode_id": f["id"],
        "copyright": f"© {page}",
    }


class FacebookPost(Post):
    """A Facebook reel: `raw` is yt-dlp's answer for it (cleaned). Fields are read when used."""

    @property
    def id(self) -> str:
        return str(self.raw["id"])

    @property
    def link(self) -> str:
        return reel_link(self.id)

    @property
    def author(self) -> str:
        return str(self.raw.get("uploader_id") or "")

    @property
    def created(self) -> datetime:
        return created_at(self.raw)

    @property
    def caption(self):
        return self.raw.get("description") or ""

    @property
    def counts(self) -> dict:
        row = flatten(self.raw)
        return {key: row[key] for key in ("plays", "likes", "comments", "shares")}

    def media(self) -> Media:
        return media(self.raw)


# --------------------------------------------------------------------------- #
# The site interface (core/site.py)
# --------------------------------------------------------------------------- #

class FacebookSite(Site):
    """Facebook behind the core's site interface: single reel links, without login or with --cookies."""

    name = "facebook"
    label = "Facebook"
    referer = f"{BASE}/"
    # Unmeasured: slow on purpose, as Meta bans accounts that scrape. The flags override them. The other
    # defaults (e.g. slideshow, unused: reels have no photos) are the base ones.
    defaults = {**Site.defaults, "wait": 2.0, "workers": 1, "connections": 4}

    def __init__(self, profile: Optional[str], cookie_file: Optional[str], wait: float, proxy: Optional[str]):
        self.profile = profile
        self.client = Facebook(self._cookies(cookie_file), wait, proxy)
        self._read: dict[str, FacebookPost] = {}   # the reel owner() read, kept for read_post()

    @staticmethod
    def _cookies(cookie_file: Optional[str]) -> list:
        if not cookie_file:
            return []
        jar = cookies.for_site(cookie_file, "facebook.com")
        if not jar:
            raise ScrapeError(t(f"the --cookies file has no facebook.com cookies: {cookie_file}. Export it from a "
                                "browser where you are logged in to Facebook.",
                                f"file --cookies không có cookie facebook.com: {cookie_file}. Hãy xuất file từ "
                                "trình duyệt đang đăng nhập Facebook."))
        return jar

    @staticmethod
    def matches(url: str) -> bool:
        return bool(FACEBOOK_LINK_RE.match(url.strip()))

    @classmethod
    def session(cls, opts) -> Optional[str]:
        """The --cookies file (a path): read in the constructor, in memory."""
        return getattr(opts, "cookies", None)

    @staticmethod
    def parse(target: str) -> tuple[Optional[str], Optional[Entry]]:
        """A reel link -> (None: the link does not name the page, the reel). Only a short link is opened."""
        text = target.strip()
        video_id = resolve_short_link(text) if SHORT_LINK_RE.match(text) else _reel_id(text)
        if not video_id:
            raise ScrapeError(t("only Facebook reel links are supported (facebook.com/reel/<id>, fb.watch/..., "
                                f"facebook.com/watch/?v=<id>): {text}",
                                "chỉ hỗ trợ link reel Facebook (facebook.com/reel/<id>, fb.watch/..., "
                                f"facebook.com/watch/?v=<id>): {text}"))
        return None, Entry(video_id, reel_link(video_id))

    def owner(self, entry: Entry) -> str:
        """Read the reel to learn its page (no reel link names it). The reel is kept, so the run does not
        read it twice."""
        try:
            post = FacebookPost(self.client.reel(entry.id))
            page = post.author
        except RateLimited:
            raise
        except ScrapeError as e:
            raise ScrapeError(f"{entry.link}: {e}", e.code) from None
        if not PAGE_ID_RE.fullmatch(page):   # it names the output folder
            raise ScrapeError(t(f"{entry.link}: unexpected answer (no valid uploader_id)",
                                f"{entry.link}: câu trả lời không như dự kiến (thiếu uploader_id hợp lệ)"))
        self._read[entry.id] = post
        self.profile = page
        return page

    def save_profile(self, out_dir) -> None:
        return None   # pages are out of scope: no profile.json, no header line

    def list_posts(self, limit, work) -> list[Entry]:
        raise ScrapeError(t("Facebook pages are not supported, only reel links",
                            "chưa hỗ trợ tải cả trang Facebook, chỉ link reel"))

    def read_post(self, post_id: str) -> FacebookPost:
        return self._read.pop(post_id, None) or FacebookPost(self.client.reel(post_id))

    def link(self, post_id: str) -> str:
        return reel_link(post_id)

    def csv_row(self, post: FacebookPost) -> dict:
        return flatten(post.raw)

    def mux_tags(self, post: FacebookPost) -> dict:
        return mux_tags(post.raw)

    classify = staticmethod(classify)
