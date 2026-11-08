"""Instagram: single reel / video post links (issue #32), read logged in with the user's sessionid cookie.

Everything specific to Instagram lives here: its URLs, the API answer keys and its stop answers.
Photo posts and carousels come in #33, whole profiles in v2.

A post is read with the same request Instagram's website makes (and yt-dlp's Instagram extractor):
GET /api/v1/media/<media id>/info/, where the media id is the link's shortcode read as a base-64
number. Only headers the website itself sends are used: no browser impersonation, no fake device.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

from ..core.console import fmt_int, t
from ..core.errors import RateLimited, ScrapeError
from ..core.pacing import PageReader
from ..core.site import Entry, Item, Media, Post, Site, Verdict
from ..core.util import UA, to_int, uniq

# instagram.com with any subdomain (www., m.): profiles, /p/, /reel/, /reels/, /tv/, with or without a query.
# Without a scheme something must follow the host: a bare "a.instagram.com" is a valid TikTok username.
_HOST = r"(?:[\w-]+\.)*instagram\.com"
INSTAGRAM_LINK_RE = re.compile(rf"^(?:https?://{_HOST}(?:[/?#]|$)|{_HOST}[/?#])", re.I)
# A post link: /reel/<code>, /reels/<code>, /p/<code>, /tv/<code>, optionally after /<username>.
POST_LINK_RE = re.compile(rf"^(?:https?://)?{_HOST}/(?:([\w.]+)/)?(reels?|p|tv)/([\w-]+)(?:[/?#]|$)", re.I)
# A username names the output folder: letters, digits, _ and ., not starting or ending with a dot.
USERNAME_RE = re.compile(r"\w(?:[\w.]{0,28}\w)?")

BASE = "https://www.instagram.com"
API = f"{BASE}/api/v1"
APP_ID = "936619743392459"   # the website's app id (X-IG-App-ID), as yt-dlp sends it

# Shortcode <-> media id: the shortcode is the media id written in this base-64 alphabet. Links to posts
# of private accounts add 28 characters after the shortcode.
ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def media_id(shortcode: str) -> str:
    """The numeric media id of a shortcode (yt-dlp's _id_to_pk)."""
    if len(shortcode) > 28:
        shortcode = shortcode[:-28]
    n = 0
    for ch in shortcode:
        n = n * 64 + ALPHABET.index(ch)
    return str(n)


def shortcode(post_id) -> str:
    """The shortcode of a numeric media id ("<id>_<user id>" also works)."""
    n = int(str(post_id).split("_")[0])
    code = ""
    while True:
        n, digit = divmod(n, 64)
        code = ALPHABET[digit] + code
        if not n:
            return code


def post_link(code: str, kind: str = "p") -> str:
    """Link of a post: kind is "reel", "p" or "tv"."""
    return f"{BASE}/{kind}/{code}/"


# --------------------------------------------------------------------------- #
# Login (sessionid cookie)
# --------------------------------------------------------------------------- #

def resolve_session_id(flag: Optional[str], environ=None) -> Optional[str]:
    """The sessionid cookie from --ig-session-id, else from the INSTAGRAM_SESSIONID environment variable
    (which keeps it out of shell history). The flag wins when both are set."""
    environ = os.environ if environ is None else environ
    return (flag or "").strip() or (environ.get("INSTAGRAM_SESSIONID") or "").strip() or None


def no_session_message() -> str:
    return t("Instagram needs a login: set the INSTAGRAM_SESSIONID environment variable (or add --ig-session-id) "
             "to the \"sessionid\" cookie of instagram.com, copied from your browser while logged in. "
             "See README, 'Instagram'. Meta may ban accounts that scrape: a secondary account is safer.",
             "Instagram cần đăng nhập: đặt biến môi trường INSTAGRAM_SESSIONID (hoặc thêm --ig-session-id) "
             "bằng cookie \"sessionid\" của instagram.com, chép từ trình duyệt khi đang đăng nhập. "
             "Xem README, mục Instagram. Meta có thể khoá tài khoản tải tự động: nên dùng tài khoản phụ.")


# --------------------------------------------------------------------------- #
# Answers: what each one means (classify)
# --------------------------------------------------------------------------- #

# The "message" of an error answer that ends the run at once. The tool never tries to clear a check.
STOP_MESSAGES = ("checkpoint_required", "challenge_required", "login_required", "feedback_required")
# Error messages for a post that does not exist, was deleted, or that this account cannot see.
NOT_FOUND_MESSAGES = ("Media not found or unavailable", "Invalid media_id")
NOT_FOUND = 404   # ScrapeError.code of such a post: Verdict.PERMANENT, skipped on re-runs


def _message(r) -> str:
    """The "message" of an Instagram JSON answer ("" for anything else)."""
    try:
        data = r.json()
    except ValueError:
        return ""
    return str(data.get("message") or "") if isinstance(data, dict) else ""


def stop_reason(r) -> Optional[str]:
    """Which STOP_MESSAGES answer this is, or None. A request bounced to the login page (or to the home
    page) means the cookie is not logged in; one bounced to /challenge/ is a check to clear."""
    path = urlparse(getattr(r, "url", "") or "").path
    if path.startswith("/accounts/login") or path == "/":
        return "login_required"
    if path.startswith("/challenge"):
        return "challenge_required"
    message = _message(r)
    if message in STOP_MESSAGES:
        return message
    return "login_required" if r.status_code == 401 else None


def classify(answer) -> Verdict:
    """What one Instagram answer means for the run: the HTTP response to a post read, or the
    ScrapeError a post read failed with. The JSON message is checked before the HTTP code: checks and
    expired logins come with 400, 401 or 403. Unlike TikTok, 403 is not a rate limit."""
    if isinstance(answer, ScrapeError):
        return Verdict.PERMANENT if answer.code == NOT_FOUND else Verdict.OK
    if stop_reason(answer):
        return Verdict.STOP
    if answer.status_code == 429:
        return Verdict.RATE_LIMITED
    return Verdict.OK


def stop_message(r) -> str:
    reason = stop_reason(r)
    if reason in ("checkpoint_required", "challenge_required"):
        return t(f"Instagram wants this account to pass a check ({reason}). Open Instagram in a browser, "
                 "logged in with the same account, and clear the check yourself, then run the same command "
                 "again. The tool never tries to solve or skip it.",
                 f"Instagram yêu cầu tài khoản này qua bước kiểm tra ({reason}). Hãy mở Instagram trên trình "
                 "duyệt, đăng nhập đúng tài khoản đó và tự hoàn tất bước kiểm tra, rồi chạy lại đúng lệnh cũ. "
                 "Tool không bao giờ tự giải hay bỏ qua bước này.")
    if reason == "feedback_required":
        return t("Instagram is limiting this account (feedback_required). Stop for a while (hours, or a day) "
                 "before you try again, and use a larger --wait.",
                 "Instagram đang hạn chế tài khoản này (feedback_required). Hãy ngừng một thời gian (vài giờ "
                 "hoặc một ngày) rồi mới thử lại, và tăng --wait.")
    return t("Instagram treats you as logged out (login_required): the sessionid cookie expired or is wrong. "
             "Copy a new one from your browser (see README, 'Instagram').",
             "Instagram coi như bạn chưa đăng nhập (login_required): cookie sessionid đã hết hạn hoặc sai. "
             "Hãy chép cookie mới từ trình duyệt (xem README, mục Instagram).")


def media_answer(r) -> Optional[dict]:
    """The post (items[0]) from an answer to /media/<id>/info/. None = no data, try again. Raises
    ScrapeError at once for a post that cannot be read: NOT_FOUND for a missing or hidden one."""
    message = _message(r)
    if r.status_code == 404 or message in NOT_FOUND_MESSAGES:
        raise ScrapeError(t("post does not exist, was deleted, or this account cannot see it (private)",
                            "bài đăng không tồn tại, đã bị xoá, hoặc tài khoản này không xem được (riêng tư)"),
                          NOT_FOUND)
    if r.status_code >= 500:
        return None
    if r.status_code >= 400:
        raise ScrapeError(t(f"Instagram refused the request (HTTP {r.status_code}) {message}",
                            f"Instagram từ chối request (HTTP {r.status_code}) {message}").strip())
    data = r.json()
    items = data.get("items") if isinstance(data, dict) else None
    if isinstance(items, list) and not items:
        raise ScrapeError(t("post does not exist, was deleted, or this account cannot see it (private)",
                            "bài đăng không tồn tại, đã bị xoá, hoặc tài khoản này không xem được (riêng tư)"),
                          NOT_FOUND)
    if isinstance(items, list) and isinstance(items[0], dict):
        return items[0]
    return None


# --------------------------------------------------------------------------- #
# Instagram web client
# --------------------------------------------------------------------------- #

class Instagram(PageReader):
    """Post reads with the user's sessionid cookie. The cookie is only in the session's cookie jar,
    for instagram.com: it is never sent to the CDN that serves the files, and never to aria2c."""

    label = "Instagram"
    limit_codes = "429"

    def __init__(self, session_id: str, wait: float = 3.0, proxy: Optional[str] = None):
        super().__init__(wait)
        self.s.headers.update({
            "User-Agent": UA,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": f"{BASE}/",
            "X-IG-App-ID": APP_ID,
            "X-ASBD-ID": "359341",
            "X-IG-WWW-Claim": "0",
            "X-Requested-With": "XMLHttpRequest",
        })
        if proxy:
            self.s.proxies = {"http": proxy, "https": proxy}
        self.s.cookies.set("sessionid", session_id, domain=".instagram.com")

    classify = staticmethod(classify)

    def blocked_message(self) -> str:   # classify() never answers BLOCKED
        return t("Instagram blocked the request", "Instagram đã chặn request")

    def stop_message(self, response) -> str:
        return stop_message(response)

    def cookie_header(self) -> str:
        """No cookie for downloads: the file links are signed, and the session must stay on instagram.com."""
        return ""

    def media(self, post_id: str) -> dict:
        return self.read(f"{API}/media/{post_id}/info/", media_answer)


# --------------------------------------------------------------------------- #
# Posts
# --------------------------------------------------------------------------- #

MEDIA_TYPES = {1: "photo", 2: "video", 8: "carousel"}


def _area(version: dict) -> int:
    return (to_int(version.get("width")) or 0) * (to_int(version.get("height")) or 0)


def _largest(versions) -> list[dict]:
    """Versions with a URL, largest first."""
    versions = [v for v in versions or [] if isinstance(v, dict) and v.get("url")]
    return sorted(versions, key=_area, reverse=True)


def caption_text(item: dict) -> str:
    return (item.get("caption") or {}).get("text") or ""


def created_at(item: dict) -> datetime:
    return datetime.fromtimestamp(to_int(item.get("taken_at")) or 0, tz=timezone.utc)


def kind_of(item: dict) -> str:
    """The link kind of a post: "reel" for reels (product_type clips), else "p"."""
    return "reel" if item.get("product_type") == "clips" else "p"


def flatten(item: dict) -> dict:
    """The videos.csv row: TikTok's column names for what both sites have, in TikTok's order, then
    Instagram's own columns."""
    user = item.get("user") or {}
    caption = caption_text(item)
    code = item.get("code") or shortcode(item["pk"])
    clips = item.get("clips_metadata") or {}
    music = (clips.get("music_info") or {}).get("music_asset_info") or {}
    sound = clips.get("original_sound_info") or {}
    duration = item.get("video_duration")
    return {
        "id": str(item.get("pk")),
        "url": post_link(code, kind_of(item)),
        "type": "reel" if item.get("product_type") == "clips" else MEDIA_TYPES.get(item.get("media_type"), "post"),
        "created": created_at(item).isoformat(),
        "username": user.get("username", ""),
        "nickname": user.get("full_name") or None,
        "author_id": str(user["pk"]) if user.get("pk") else None,
        "author_verified": user.get("is_verified"),
        "description": caption,
        "hashtags": " ".join(re.findall(r"#\w+", caption)) or None,
        "mentions": " ".join(re.findall(r"@[\w.]+", caption)) or None,
        "plays": to_int(item.get("play_count") or item.get("ig_play_count") or item.get("view_count")),
        "likes": to_int(item.get("like_count")),
        "comments": to_int(item.get("comment_count")),
        "duration": round(duration) if isinstance(duration, (int, float)) else None,
        "width": to_int(item.get("original_width")),
        "height": to_int(item.get("original_height")),
        "music_title": music.get("title") or sound.get("original_audio_title"),
        "music_author": music.get("display_artist") or (sound.get("ig_artist") or {}).get("username"),
        # Instagram's own columns
        "shortcode": code,
        "product_type": item.get("product_type"),
        "view_count": to_int(item.get("view_count")),   # older count; "plays" prefers play_count
        "has_audio": item.get("has_audio"),
        "location": (item.get("location") or {}).get("name"),
        "coauthors": " ".join(f"@{u['username']}" for u in item.get("coauthor_producers") or []
                              if isinstance(u, dict) and u.get("username")) or None,
        "paid_partnership": item.get("is_paid_partnership"),
        "comments_disabled": item.get("comments_disabled"),
    }


def mux_tags(item: dict) -> dict:
    """Tags written into the MP4 (readable by Explorer / VLC / MediaInfo)."""
    f = flatten(item)
    when = created_at(item)
    caption = caption_text(item)
    title = caption.splitlines()[0][:120] if caption.strip() else f"Instagram {f['shortcode']}"
    comment = [f["url"], f"Views {fmt_int(f['plays'])} · Likes {fmt_int(f['likes'])} · "
                         f"Comments {fmt_int(f['comments'])}"]
    if f["music_title"]:
        comment.append(f"Music: {f['music_title']}" + (f" - {f['music_author']}" if f["music_author"] else ""))
    if f["hashtags"]:
        comment.append(f"Hashtags: {f['hashtags']}")
    return {
        "title": title,
        "artist": f"{f['nickname']} (@{f['username']})" if f["nickname"] else f"@{f['username']}",
        "album_artist": f["nickname"] or f["username"],
        "album": f"Instagram @{f['username']}",
        "date": f"{when:%Y-%m-%d}",
        "creation_time": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "description": caption,
        "synopsis": caption,
        "comment": "\n".join(comment),
        "genre": "Instagram",
        "episode_id": f["shortcode"],
        "copyright": f"© {f['nickname'] or f['username']}",
    }


def photo(node: dict) -> Optional[Item]:
    """A photo at the largest size offered. Smaller sizes are never a fallback."""
    versions = _largest((node.get("image_versions2") or {}).get("candidates"))
    if not versions:
        return None
    return Item(uniq(v["url"] for v in versions if _area(v) == _area(versions[0])))


def carousel_item(node: dict) -> Optional[Item]:
    """One item of a carousel: a photo, or a video (its versions, largest first, and its cover)."""
    if node.get("media_type") != 2:
        return photo(node)
    videos = uniq(v["url"] for v in _largest(node.get("video_versions")))
    cover = photo(node)
    return Item(videos, video=True, cover=cover.urls[0] if cover else None) if videos else None


def media(item: dict) -> Media:
    """What an Instagram post offers for download: the video versions (largest first) and the cover, or
    the photo."""
    if item.get("media_type") == 1:
        single = photo(item)
        if not single:
            return Media(unavailable=t("Instagram returned no download link for this photo",
                                       "Instagram không trả link tải cho ảnh này"))
        return Media(items=[single])
    if item.get("media_type") == 8:
        nodes = [node for node in item.get("carousel_media") or [] if isinstance(node, dict)]
        items = [carousel_item(node) for node in nodes]
        if not items or None in items:
            missing = ", ".join(str(i) for i, got in enumerate(items, 1) if got is None) or "1"
            return Media(unavailable=t(f"Instagram returned no download link for carousel item {missing}",
                                       f"Instagram không trả link tải cho mục {missing} của bài nhiều ảnh/video"))
        return Media(items=items)
    videos = uniq(v["url"] for v in _largest(item.get("video_versions")))
    if not videos:
        return Media(unavailable=t("Instagram returned no download link for this video",
                                   "Instagram không trả link tải cho video này"))
    covers = _largest((item.get("image_versions2") or {}).get("candidates"))
    return Media(video=videos, cover=covers[0]["url"] if covers else None)


class InstagramPost(Post):
    """An Instagram post: `raw` is items[0] of its /media/<id>/info/ answer. Fields are read when used."""

    @property
    def id(self) -> str:
        return str(self.raw["pk"])

    @property
    def link(self) -> str:
        return post_link(self.raw.get("code") or shortcode(self.raw["pk"]), kind_of(self.raw))

    @property
    def author(self) -> str:
        return (self.raw.get("user") or {}).get("username", "")

    @property
    def created(self) -> datetime:
        return created_at(self.raw)

    @property
    def caption(self):
        return caption_text(self.raw)

    @property
    def counts(self) -> dict:
        row = flatten(self.raw)
        return {key: row[key] for key in ("plays", "likes", "comments")}

    def media(self) -> Media:
        return media(self.raw)


# --------------------------------------------------------------------------- #
# The site interface (core/site.py)
# --------------------------------------------------------------------------- #

class InstagramSite(Site):
    """Instagram behind the core's site interface: single reel / video post links, logged in. A plain
    class, not a dataclass: it holds the session cookie, which must never show up in a repr."""

    name = "instagram"
    label = "Instagram"
    referer = f"{BASE}/"
    # Unmeasured: slow on purpose, as Meta bans accounts that scrape. The flags override them.
    defaults = {"wait": 3.0, "workers": 1, "connections": 4}

    def __init__(self, profile: Optional[str], session_id: Optional[str], wait: float, proxy: Optional[str]):
        if not session_id:
            raise ScrapeError(no_session_message())
        self.profile = profile
        self.client = Instagram(session_id, wait, proxy)
        self._read: dict[str, InstagramPost] = {}   # the post owner() read, kept for read_post()

    @staticmethod
    def matches(url: str) -> bool:
        return bool(INSTAGRAM_LINK_RE.match(url.strip()))

    @classmethod
    def session(cls, opts) -> Optional[str]:
        return getattr(opts, "ig_session_id", None)

    @staticmethod
    def parse(target: str) -> tuple[Optional[str], Optional[Entry]]:
        """A reel / post link -> (the username in the link or None, the post). Profiles fail: they come
        in v2. Makes no request."""
        text = target.strip()
        m = POST_LINK_RE.match(text)
        if not m:
            raise ScrapeError(t(f"Instagram profiles are not supported yet, only links to a reel or video post "
                                f"(instagram.com/reel/...): {text}",
                                f"chưa hỗ trợ tải cả profile Instagram, chỉ link reel hoặc bài video "
                                f"(instagram.com/reel/...): {text}"))
        user, kind, code = m.group(1), m.group(2).lower(), m.group(3)
        if any(ch not in ALPHABET for ch in code) or (user and not USERNAME_RE.fullmatch(user)):
            raise ScrapeError(t(f"not a valid Instagram post link: {text}", f"link bài Instagram không hợp lệ: {text}"))
        kind = "reel" if kind.startswith("reel") else kind
        return user, Entry(media_id(code), post_link(code, kind))

    def owner(self, entry: Entry) -> str:
        """Read the post to learn its author (a /reel/<code> link does not name it). The post is kept,
        so the run does not read it twice."""
        try:
            post = InstagramPost(self.client.media(entry.id))
            author = post.author
        except RateLimited:
            raise
        except ScrapeError as e:
            raise ScrapeError(f"{entry.link}: {e}", e.code) from None
        if not USERNAME_RE.fullmatch(author or ""):   # it names the output folder
            raise ScrapeError(t(f"{entry.link}: unexpected answer (no valid user.username)",
                                f"{entry.link}: câu trả lời không như dự kiến (thiếu user.username hợp lệ)"))
        self._read[entry.id] = post
        self.profile = author
        return author

    def save_profile(self, out_dir) -> None:
        return None   # profiles come in v2: no profile.json, no header line

    def list_posts(self, limit, work) -> list[Entry]:
        raise ScrapeError(t("Instagram profiles are not supported yet", "chưa hỗ trợ tải cả profile Instagram"))

    def read_post(self, post_id: str) -> InstagramPost:
        return self._read.pop(post_id, None) or InstagramPost(self.client.media(post_id))

    def link(self, post_id: str) -> str:
        return post_link(shortcode(post_id))

    def csv_row(self, post: InstagramPost) -> dict:
        return flatten(post.raw)

    def mux_tags(self, post: InstagramPost) -> dict:
        return mux_tags(post.raw)

    classify = staticmethod(classify)
