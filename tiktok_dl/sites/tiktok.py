"""TikTok: the web client, page parsing, links, the CSV row, the MP4 tags and the post list (yt-dlp).

Everything specific to TikTok lives here: its URLs, the page JSON keys and its bot-protection markers.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import requests

from ..core import console
from ..core.console import fmt_int, gray, red, t, warn
from ..core.errors import Challenged, RateLimited, ScrapeError
from ..core.pacing import PageReader
from ..core.site import Entry, Media, Post, Profile, Site, Subtitle, Verdict
from ..core.util import UA, to_int, uniq

REHYDRATION_RE = re.compile(
    r'<script[^>]+id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', re.S)

STATUS_MESSAGES = {
    10101: ("TikTok server error", "lỗi máy chủ TikTok"),
    10202: ("account does not exist", "tài khoản không tồn tại"),
    10204: ("post does not exist or was deleted", "bài đăng không tồn tại hoặc đã bị xoá"),
    10216: ("post is private", "bài đăng đang để riêng tư"),
    10221: ("account is banned", "tài khoản đã bị cấm"),
    10222: ("account is private", "tài khoản đang để riêng tư"),
    10223: ("account is restricted", "tài khoản bị hạn chế"),
}
FATAL_PROFILE_CODES = {10202, 10221, 10222, 10223}
# Post errors that no re-run can fix: the post is marked "skipped" and not fetched again (see --retry-all).
PERMANENT_POST_CODES = {10204}


def challenge_message() -> str:
    return t("TikTok's bot protection blocked the request (challenge page). Try again later or from another "
             "network (VPN off, a different connection); --session-id doesn't help with this.",
             "Lớp chống bot của TikTok đã chặn request (trang xác minh). Hãy thử lại sau hoặc dùng mạng khác "
             "(tắt VPN, đổi kết nối); --session-id không giúp được trường hợp này.")


SHORT_LINK_RE = re.compile(r"^(?:https?://)?(?:(?:vt|vm)\.tiktok\.com/|(?:www\.)?tiktok\.com/t/)\w+", re.I)


def resolve_short_link(text: str) -> str:
    """Expand a short link (vt.tiktok.com/..., vm.tiktok.com/..., tiktok.com/t/...) to the full URL."""
    if not SHORT_LINK_RE.match(text):
        return text
    url = text if text.lower().startswith("http") else f"https://{text}"
    try:
        r = requests.get(url, headers={"User-Agent": UA}, allow_redirects=False, timeout=20)
        for _ in range(5):
            loc = r.headers.get("Location")
            if not loc:
                break
            url = urljoin(url, loc)   # Location may be relative to the current URL
            if re.search(r"tiktok\.com/@[^/?#]+", url):
                return url
            r = requests.get(url, headers={"User-Agent": UA}, allow_redirects=False, timeout=20)
    except requests.RequestException as e:
        raise ScrapeError(t(f"could not open short link {text} ({e})", f"không mở được link rút gọn {text} ({e})"))
    raise ScrapeError(t(f"short link does not lead to a TikTok post or profile: {text}",
                        f"link rút gọn không dẫn tới bài đăng hay profile TikTok: {text}"))


def parse_target(text: str) -> tuple[str, Optional[str]]:
    """'user', '@user', a profile link or a video link -> (username, video_id | None)."""
    raw = resolve_short_link(text.strip())
    m = re.search(r"tiktok\.com/@([^/?#\s]+)(?:/(?:video|photo)/(\d+))?", raw, re.I)
    name, vid = (m.group(1), m.group(2)) if m else (raw.lstrip("@"), None)
    if not re.fullmatch(r"[\w.]{1,64}", name):
        raise ScrapeError(t(f'not a valid TikTok username: "{text}"', f'không nhận ra username TikTok: "{text}"'))
    return name, vid


def created_at(item: dict) -> datetime:
    return datetime.fromtimestamp(to_int(item.get("createTime")) or 0, tz=timezone.utc)


def post_link(username: str, post_id: str, kind: str = "video") -> str:
    """Link of a post: kind is "video" or "photo"."""
    return f"https://www.tiktok.com/@{username}/{kind}/{post_id}"


# --------------------------------------------------------------------------- #
# TikTok web client
# --------------------------------------------------------------------------- #

# Page reads TikTok answers with these HTTP codes are rate-limited: core/pacing.py backs off.
RATE_LIMIT_CODES = (403, 429)

# TikTok's bot protection (WAF) may answer a page read with a small (~1.4 KB) "Please wait..." page
# that runs a challenge script (waf-aiso) instead of the real page. We only detect it, never solve it.
# It does not clear within seconds, so it is not retried; CHALLENGE_STREAK such answers in a row stop
# the run.
CHALLENGE_MARKERS = ("_wafchallengeid", "waf-aiso")


def is_challenge_page(html: str) -> bool:
    """True for TikTok's bot-protection challenge page (challenge markers, no embedded page data)."""
    return any(m in html for m in CHALLENGE_MARKERS) and not REHYDRATION_RE.search(html)


def page_data(html: str, scope: str) -> Optional[dict]:
    """The `scope` object from a page's embedded rehydration JSON, or None."""
    m = REHYDRATION_RE.search(html)
    if m:
        data = json.loads(m.group(1)).get("__DEFAULT_SCOPE__", {}).get(scope)
        if isinstance(data, dict):
            return data
    return None


def classify(answer) -> Verdict:
    """What one TikTok answer means for the run: the HTTP response to a page read, or the
    ScrapeError a post read failed with (TikTok reports deleted posts inside the page data)."""
    if isinstance(answer, ScrapeError):
        return Verdict.PERMANENT if answer.code in PERMANENT_POST_CODES else Verdict.OK
    if is_challenge_page(answer.text):
        return Verdict.BLOCKED
    if answer.status_code in RATE_LIMIT_CODES:
        return Verdict.RATE_LIMITED
    return Verdict.OK


class TikTok(PageReader):
    def __init__(self, session_id: Optional[str] = None, wait: float = 0.3, proxy: Optional[str] = None):
        super().__init__(wait)
        self.s.headers.update({
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.tiktok.com/",
        })
        if proxy:
            self.s.proxies = {"http": proxy, "https": proxy}
        # Use the user's own login session (sessionid cookie).
        if session_id:
            for name in ("sessionid", "sessionid_ss", "sid_tt"):
                self.s.cookies.set(name, session_id, domain=".tiktok.com")

    classify = staticmethod(classify)

    def blocked_message(self) -> str:
        return challenge_message()

    def _scope(self, url: str, scope: str) -> dict:
        return self.read(url, lambda r: page_data(r.text, scope))

    @staticmethod
    def _check(data: dict) -> None:
        code = to_int(data.get("statusCode")) or 0
        if code:
            known = STATUS_MESSAGES.get(code)
            raw = data.get("statusMsg") or ""
            msg = t(*known) if known else t(f"TikTok returned statusCode {code} {raw}",
                                            f"TikTok trả statusCode {code} {raw}")
            raise ScrapeError(msg.strip(), code)

    def user(self, username: str) -> dict:
        data = self._scope(f"https://www.tiktok.com/@{username}", "webapp.user-detail")
        self._check(data)
        info = data.get("userInfo")
        if not isinstance(info, dict):
            raise ScrapeError(t("unexpected page format (no userInfo)",
                                "trang có định dạng không như dự kiến (thiếu userInfo)"))
        return info

    def item(self, username: str, video_id: str) -> dict:
        data = self._scope(f"https://www.tiktok.com/@{username}/video/{video_id}", "webapp.video-detail")
        self._check(data)
        info = data.get("itemInfo")
        item = info.get("itemStruct") if isinstance(info, dict) else None
        if not isinstance(item, dict) or not item.get("id"):
            raise ScrapeError(t("unexpected page format (no itemInfo.itemStruct)",
                                "trang có định dạng không như dự kiến (thiếu itemInfo.itemStruct)"))
        return item

    def whoami(self) -> Optional[dict]:
        """The account logged in with the current cookies, or None when logged out.

        With a valid sessionid cookie, TikTok pages embed the user in webapp.app-context.
        """
        data = self._scope("https://www.tiktok.com/explore", "webapp.app-context")
        user = data.get("user") or {}
        return user if user.get("uniqueId") else None


def list_posts(username: str, limit: Optional[int], cookiefile: Optional[str]) -> list[dict]:
    """Every post of the profile, newest first (yt-dlp TikTokUser extractor)."""
    import yt_dlp

    class Quiet:
        def debug(self, msg):
            m = re.search(r"Downloading page (\d+)", msg)
            if m:
                console.status(gray(t(f"  Getting post list... page {m.group(1)}",
                                      f"  Đang lấy danh sách bài đăng... trang {m.group(1)}")))
        info = debug

        def warning(self, msg):
            pass

        def error(self, msg):
            console.log(f"  {red(msg)}", err=True)

    opts = {"extract_flat": "in_playlist", "skip_download": True, "logger": Quiet(), "no_warnings": True}
    if limit:
        opts["playlistend"] = limit
    if cookiefile:
        opts["cookiefile"] = cookiefile
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"https://www.tiktok.com/@{username}", download=False)
    except yt_dlp.utils.DownloadError as e:
        raise ScrapeError(t(f"yt-dlp could not get the post list ({e}). Try: pip install -U yt-dlp",
                             f"yt-dlp không lấy được danh sách bài đăng ({e}). Thử: pip install -U yt-dlp"))
    return [e for e in (info or {}).get("entries") or [] if e and e.get("id")]


def write_cookie_file(session_id: str, folder: Path) -> str:
    path = folder / "session-cookies.txt"
    exp = int(time.time()) + 30 * 86400
    rows = [f".tiktok.com\tTRUE\t/\tTRUE\t{exp}\t{n}\t{session_id}" for n in ("sessionid", "sessionid_ss", "sid_tt")]
    path.write_text("# Netscape HTTP Cookie File\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return str(path)


def list_posts_with_session(username: str, limit: Optional[int], session_id: Optional[str], work: Path) -> list[dict]:
    """list_posts() with the user's session. The cookie file holds the session ID, so it is
    deleted as soon as the list is fetched, even when listing fails or is interrupted."""
    if not session_id:
        return list_posts(username, limit, None)
    cookiefile = write_cookie_file(session_id, work)
    try:
        return list_posts(username, limit, cookiefile)
    finally:
        Path(cookiefile).unlink(missing_ok=True)


def resolve_session_id(flag: Optional[str], environ=None) -> Optional[str]:
    """The session ID from --session-id, else from the TIKTOK_SESSIONID environment variable
    (which keeps it out of shell history). The flag wins when both are set."""
    environ = os.environ if environ is None else environ
    return (flag or "").strip() or (environ.get("TIKTOK_SESSIONID") or "").strip() or None


# --------------------------------------------------------------------------- #
# Metadata
# --------------------------------------------------------------------------- #

def flatten(item: dict) -> dict:
    author = item.get("author") or {}
    music = item.get("music") or {}
    video = item.get("video") or {}
    stats = {**(item.get("stats") or {}), **(item.get("statsV2") or {})}
    extra = item.get("textExtra") or []
    images = (item.get("imagePost") or {}).get("images") or []
    subs = video.get("subtitleInfos") or []
    username = author.get("uniqueId", "")
    return {
        "id": item.get("id"),
        "url": f"https://www.tiktok.com/@{username}/video/{item.get('id')}",
        "type": "slide" if images else "video",
        "created": created_at(item).isoformat(),
        "username": username,
        "nickname": author.get("nickname"),
        "author_id": author.get("id"),
        "author_verified": author.get("verified"),
        "description": item.get("desc"),
        "hashtags": " ".join(f"#{tag['hashtagName']}" for tag in extra if tag.get("hashtagName")),
        "mentions": " ".join(f"@{tag['userUniqueId']}" for tag in extra if tag.get("userUniqueId")),
        "plays": to_int(stats.get("playCount")),
        "likes": to_int(stats.get("diggCount")),
        "comments": to_int(stats.get("commentCount")),
        "shares": to_int(stats.get("shareCount")),
        "saves": to_int(stats.get("collectCount")),
        "reposts": to_int(stats.get("repostCount")),
        "duration": to_int(video.get("duration")),
        "width": to_int(video.get("width")),
        "height": to_int(video.get("height")),
        "definition": video.get("definition") or video.get("ratio"),
        "codec": video.get("codecType"),
        "bitrate": to_int(video.get("bitrate")),
        "file_size": to_int(video.get("size")),
        "image_count": len(images) or None,
        "subtitles": " ".join(s.get("LanguageCodeName", "") for s in subs) or None,
        "music_id": music.get("id"),
        "music_title": music.get("title"),
        "music_author": music.get("authorName"),
        "music_original": music.get("original"),
        "music_duration": to_int(music.get("duration")),
        "location_created": item.get("locationCreated"),
        "text_language": item.get("textLanguage"),
        "is_ad": item.get("isAd", False),
        "is_ai_generated": item.get("IsAigc"),
        "ai_description": item.get("AIGCDescription") or None,
        "category_type": item.get("CategoryType"),
        "diversification_labels": " | ".join(item.get("diversificationLabels") or []) or None,
        "suggested_words": " | ".join(item.get("suggestedWords") or []) or None,
        "sticker_texts": " | ".join(text for s in item.get("stickersOnItem") or []
                                    for text in s.get("stickerText") or []) or None,
        "effects": " | ".join(e.get("name", "") for e in item.get("effectStickers") or []) or None,
        "duet_enabled": item.get("duetEnabled", False),
        "stitch_enabled": item.get("stitchEnabled", False),
        "share_enabled": item.get("shareEnabled"),
        "comment_status": item.get("itemCommentStatus"),
        "private_item": item.get("privateItem"),
        "pinned": item.get("isPinnedItem"),
        "schedule_time": item.get("scheduleTime"),
        "warn_info": json.dumps(item["warnInfo"], ensure_ascii=False) if item.get("warnInfo") else None,
    }


def mux_tags(item: dict) -> dict:
    """Tags written into the MP4 (readable by Explorer / VLC / MediaInfo)."""
    f = flatten(item)
    when = created_at(item)
    desc = item.get("desc") or ""
    title = desc.splitlines()[0][:120] if desc.strip() else f"TikTok {item['id']}"
    comment = [
        f["url"],
        f"Views {fmt_int(f['plays'])} · Likes {fmt_int(f['likes'])} · Comments {fmt_int(f['comments'])}"
        f" · Shares {fmt_int(f['shares'])} · Saves {fmt_int(f['saves'])}",
    ]
    if f["music_title"]:
        comment.append(f"Music: {f['music_title']}" + (f" - {f['music_author']}" if f["music_author"] else ""))
    if f["hashtags"]:
        comment.append(f"Hashtags: {f['hashtags']}")
    return {
        "title": title,
        "artist": f"{f['nickname']} (@{f['username']})" if f["nickname"] else f"@{f['username']}",
        "album_artist": f["nickname"] or f["username"],
        "album": f"TikTok @{f['username']}",
        "date": f"{when:%Y-%m-%d}",
        "creation_time": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "description": desc,
        "synopsis": desc,
        "comment": "\n".join(comment),
        "genre": "TikTok",
        "episode_id": str(item["id"]),
        "copyright": f"© {f['nickname'] or f['username']}",
    }


def save_profile(tt: TikTok, username: str, out_dir: Path) -> Optional[dict]:
    try:
        info = tt.user(username)
    except RateLimited:
        raise
    except Challenged as e:
        # Stop this profile now: the post list (yt-dlp) would only fail with a misleading error.
        raise Challenged(f"@{username}: {e}") from None
    except ScrapeError as e:
        if e.code in FATAL_PROFILE_CODES:
            raise ScrapeError(f"@{username}: {e}", e.code)
        console.log(warn(t(f"Could not read profile info ({e}), continuing.",
                   f"Không đọc được thông tin profile ({e}), vẫn tiếp tục.")))
        return None
    (out_dir / "profile.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    avatar = (info.get("user") or {}).get("avatarLarger")
    if avatar:
        try:
            r = tt.s.get(avatar, timeout=20)
            if r.ok and r.content:
                (out_dir / "avatar.jpg").write_bytes(r.content)
        except requests.RequestException:
            pass
    return info


# --------------------------------------------------------------------------- #
# Posts
# --------------------------------------------------------------------------- #

def media(item: dict) -> Media:
    """What a TikTok post offers for download: video mirrors, cover and subtitles, or the images and
    music of a photo post."""
    video = item.get("video") or {}
    images = (item.get("imagePost") or {}).get("images") or []
    if images:
        return Media(images=[uniq((img.get("imageURL") or {}).get("urlList") or []) for img in images],
                     audio=(item.get("music") or {}).get("playUrl"))

    # These URLs are NOT mirrors of the same file: www.tiktok.com/aweme/v1/play returns HTML and
    # downloadAddr is the watermarked version. aria2c uses one URL per attempt, then the next one.
    struct = video.get("PlayAddrStruct") or {}
    mirrors = uniq([video.get("playAddr"), *(struct.get("UrlList") or []), video.get("downloadAddr")])
    mirrors = [u for u in mirrors if "/aweme/v1/play" not in u]
    if not mirrors:
        # TikTok withholds the video URL on purpose, typically for ad / TikTok Shop posts.
        if item.get("isAd") or item.get("isECVideo"):
            return Media(unavailable=t("ad / TikTok Shop video: TikTok does not provide a download link on the web "
                                       "(even when logged in)",
                                       "video quảng cáo / TikTok Shop: TikTok không cung cấp link tải qua web "
                                       "(kể cả khi đã đăng nhập)"),
                         permanent=True)
        return Media(unavailable=t("TikTok returned no download link for this video (it may be restricted), "
                                   "try again with --session-id",
                                   "TikTok không trả link tải cho video này (có thể bị giới hạn), "
                                   "thử lại với --session-id"))
    # The reported size describes playAddr / PlayAddrStruct only, not the watermarked downloadAddr.
    size = to_int(struct.get("DataSize")) or to_int(video.get("size")) or 0
    sized = {video.get("playAddr"), *(struct.get("UrlList") or [])}
    return Media(video=mirrors, sizes={u: size for u in mirrors if size and u in sized},
                 cover=video.get("originCover") or video.get("cover"),
                 subtitles=[Subtitle(sub.get("Url"), (sub.get("LanguageCodeName") or "und").split("-")[0].lower(),
                                     sub.get("Source") == "MT")
                            for sub in video.get("subtitleInfos") or []])


class TikTokPost(Post):
    """A TikTok post: `raw` is the itemStruct of its page. Fields are read from it when used."""

    @property
    def id(self) -> str:
        return str(self.raw["id"])

    @property
    def link(self) -> str:
        images = (self.raw.get("imagePost") or {}).get("images")
        username = (self.raw.get("author") or {}).get("uniqueId") or "_"
        return post_link(username, self.id, "photo" if images else "video")

    @property
    def author(self) -> str:
        return (self.raw.get("author") or {}).get("uniqueId", "")

    @property
    def created(self) -> datetime:
        return created_at(self.raw)

    @property
    def caption(self):
        return self.raw.get("desc", "")   # not converted: base_name() fails on odd types as before

    @property
    def counts(self) -> dict:
        row = flatten(self.raw)
        return {key: row[key] for key in ("plays", "likes", "comments", "shares", "saves", "reposts")}

    def media(self) -> Media:
        return media(self.raw)


# --------------------------------------------------------------------------- #
# The site interface (core/site.py)
# --------------------------------------------------------------------------- #

TIKTOK_LINK_RE = re.compile(r"^(?:https?://)?(?:[\w-]+\.)*tiktok\.com(?:[/?#]|$)", re.I)


class TikTokSite(Site):
    """TikTok behind the core's site interface. A plain class, not a dataclass: it holds the session
    ID, which must never show up in a repr."""

    name = "tiktok"
    label = "TikTok"
    referer = "https://www.tiktok.com/"

    def __init__(self, profile: str, session_id: Optional[str], wait: float, proxy: Optional[str]):
        self.profile = profile
        self.session_id = session_id      # for the yt-dlp post list; never printed
        self.client = TikTok(session_id, wait, proxy)

    @staticmethod
    def matches(url: str) -> bool:
        return bool(TIKTOK_LINK_RE.match(url.strip()))

    @classmethod
    def folder(cls, profile: str) -> Path:
        """<output>/<user>, with no "tiktok" folder: TikTok was the only site before, and folders made by
        earlier versions must keep resuming."""
        return Path(profile)

    @staticmethod
    def parse(target: str) -> tuple[str, Optional[Entry]]:
        target = resolve_short_link(target.strip())   # keep the full link: it tells photo posts apart
        username, video_id = parse_target(target)      # resolves nothing again: target is already expanded
        if not video_id:
            return username, None
        kind = "photo" if "/photo/" in target.lower() else "video"
        return username, Entry(video_id, post_link(username, video_id, kind))

    # The bare names below (save_profile, list_posts_with_session, mux_tags) are the module functions.
    def save_profile(self, out_dir: Path) -> Optional[Profile]:
        info = save_profile(self.client, self.profile, out_dir)
        if not info:                    # None, or an empty userInfo: no header line
            return None
        u, st = info.get("user") or {}, info.get("stats") or {}
        return Profile(u.get("nickname", self.profile), u.get("verified"), st.get("followerCount"),
                       st.get("heartCount"), st.get("videoCount"))

    def list_posts(self, limit: Optional[int], work: Path) -> list[Entry]:
        entries = list_posts_with_session(self.profile, limit, self.session_id, work)
        # Before the post page is read, only a /photo/ link marks a photo post.
        return [Entry(str(e["id"]), post_link(self.profile, str(e["id"]),
                                              "photo" if "/photo/" in (e.get("url") or "").lower() else "video"))
                for e in entries]

    def read_post(self, post_id: str) -> TikTokPost:
        return TikTokPost(self.client.item(self.profile, post_id))

    def link(self, post_id: str) -> str:
        return post_link(self.profile, post_id)

    def csv_row(self, post: TikTokPost) -> dict:
        return flatten(post.raw)

    def mux_tags(self, post: TikTokPost) -> dict:
        return mux_tags(post.raw)

    classify = staticmethod(classify)
