"""Command line: the arguments, main(), and scrape_profile(), which picks the site for a target
(sites.find_site(): the site of a link, or --site for a username).

The TikTok login flags (--session-id, --check-session) still call the TikTok module directly.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

from . import __version__
from .core import console, flow, tools
from .core.console import bold, bold_green, bold_red, gray, green, red, t, yellow
from .core.errors import ChallengeStop, RateLimited, ScrapeError
from .sites import BY_NAME, find_site, tiktok


def scrape_profile(target: str, opts, aria2: Optional[str], ffmpeg: Optional[str]) -> int:
    """Download one profile or post with the site its link belongs to (or --site for a username)."""
    # getattr: callers that build opts themselves (tests) may leave --site out.
    return flow.run_profile(find_site(target, getattr(opts, "site", None)), target, opts, aria2, ffmpeg)


def main() -> None:
    console.LANG = console.detect_lang(sys.argv[1:])
    if console.LANG not in ("en", "vi"):
        console.LANG = "en"

    p = argparse.ArgumentParser(
        prog="tiktok-dl",
        description=t("Download every video from a TikTok profile (aria2c + ffmpeg, with full metadata).",
                      "Tải toàn bộ video của profile TikTok (aria2c + ffmpeg, kèm đầy đủ metadata)."),
        epilog=t("Examples:", "Ví dụ:")
               + "\n  python tiktok-dl.py @tiktok\n  python tiktok-dl.py tiktok -n 20 -j 16\n"
               "  python tiktok-dl.py https://www.tiktok.com/@tiktok/video/7704834703446117662\n"
               "  python tiktok-dl.py https://vt.tiktok.com/XXXXXXXXX/\n"
               "  python tiktok-dl.py @tiktok --lang vi\n\n"
               + t("Put aria2c.exe and ffmpeg.exe next to tiktok-dl.py (or in PATH).",
                   "Đặt aria2c.exe và ffmpeg.exe cạnh tiktok-dl.py (hoặc trong PATH)."),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("targets", nargs="*", metavar="profile",
                   help=t("username, @username, profile link, video link or short link (several allowed)",
                          "username, @username, link profile, link video hoặc link rút gọn (có thể nhập nhiều)"))
    p.add_argument("--site", choices=tuple(BY_NAME),
                   help=t("the site usernames belong to (default: tiktok); links must be from this site",
                          "username thuộc trang nào (mặc định: tiktok); link phải thuộc đúng trang này"))
    p.add_argument("-o", "--output", default="downloads",
                   help=t("root folder to save into (default: downloads)", "thư mục gốc để lưu (mặc định: downloads)"))
    p.add_argument("-n", "--limit", type=int, help=t("only the N newest posts", "chỉ lấy N bài mới nhất"))
    p.add_argument("-j", "--jobs", type=int, default=16,
                   help=t("files aria2c downloads at once (default: 16)", "số file aria2c tải cùng lúc (mặc định: 16)"))
    p.add_argument("-x", "--connections", type=int, default=4,
                   help=t("aria2c connections per file, max 16 (default: 4)",
                          "số kết nối aria2c cho mỗi file, tối đa 16 (mặc định: 4)"))
    p.add_argument("-w", "--workers", type=int, default=8,
                   help=t("threads reading metadata (default: 8)", "số luồng đọc metadata (mặc định: 8)"))
    p.add_argument("--wait", type=float, default=0.3,
                   help=t("seconds between TikTok page requests (default: 0.3)",
                          "giãn cách giữa các request trang TikTok, giây (mặc định: 0.3)"))
    p.add_argument("--batch", type=int, default=50,
                   help=t("posts per read-download-mux batch (default: 50)",
                          "số bài mỗi lô đọc-tải-mux (mặc định: 50)"))
    p.add_argument("--mux-jobs", type=int, default=max(1, min(4, (os.cpu_count() or 2) // 2)),
                   help=t("files ffmpeg processes at once", "số file ffmpeg xử lý cùng lúc"))
    p.add_argument("--metadata-only", action="store_true",
                   help=t("only save metadata (JSON + CSV), no files", "chỉ lấy metadata (JSON + CSV), không tải file"))
    p.add_argument("--subs", choices=("original", "all", "none"), default="original",
                   help=t("subtitles to embed: original = original/auto captions, all = also machine translations "
                          "(default: original)",
                          "phụ đề gắn vào video: original = phụ đề gốc/tự động, all = cả bản dịch máy "
                          "(mặc định: original)"))
    p.add_argument("--no-cover", dest="cover", action="store_false",
                   help=t("do not embed the cover image", "không gắn ảnh bìa vào MP4"))
    p.add_argument("--no-slideshow", dest="slideshow", action="store_false",
                   help=t("photo posts: save images + music only, no slideshow video",
                          "bài ảnh: không dựng video slideshow, chỉ lưu ảnh + nhạc"))
    p.add_argument("--keep-images", action="store_true",
                   help=t("photo posts: also save each JPG + MP3 next to the slideshow",
                          "bài ảnh: lưu thêm từng ảnh JPG + MP3 cạnh slideshow"))
    p.add_argument("--slide-seconds", type=float, default=3.0,
                   help=t("seconds per image in slideshows (default: 3)",
                          "số giây mỗi ảnh trong slideshow (mặc định: 3)"))
    p.add_argument("--keep-parts", action="store_true",
                   help=t("keep temporary files in .parts/", "giữ lại file tạm trong .parts/"))
    p.add_argument("--retry-all", action="store_true",
                   help=t("also retry posts that can never be downloaded (deleted, ad / TikTok Shop), "
                          "which re-runs skip by default",
                          "thử lại cả những bài không bao giờ tải được (đã xoá, quảng cáo / TikTok Shop) "
                          "mà mặc định các lần chạy lại sẽ bỏ qua"))
    p.add_argument("--session-id",
                   help=t('your TikTok "sessionid" cookie (age-restricted posts, private accounts you follow); '
                          'safer: set the TIKTOK_SESSIONID environment variable instead',
                          'cookie "sessionid" tài khoản TikTok của bạn (bài giới hạn tuổi, '
                          'tài khoản riêng tư bạn theo dõi); an toàn hơn: đặt biến môi trường TIKTOK_SESSIONID'))
    p.add_argument("--check-session", action="store_true",
                   help=t("only check whether the session ID is logged in, then exit",
                          "chỉ kiểm tra session ID có đăng nhập được không rồi thoát"))
    p.add_argument("--proxy", help=t("proxy for TikTok page requests, e.g. http://127.0.0.1:8080",
                                     "proxy cho request trang TikTok, vd http://127.0.0.1:8080"))
    p.add_argument("--lang", choices=("en", "vi"), default=console.LANG,
                   help=t("message language: en = English, vi = Vietnamese (default: auto)",
                          "ngôn ngữ thông báo: en = tiếng Anh, vi = tiếng Việt (mặc định: tự nhận)"))
    p.add_argument("--no-color", dest="color", action="store_false",
                   help=t("plain text output without colors", "in chữ trơn, không màu"))
    p.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    opts = p.parse_args()
    console.LANG = opts.lang
    console.init_colors(opts.color)
    opts.jobs = max(1, min(32, opts.jobs))
    opts.connections = max(1, min(16, opts.connections))
    opts.workers = max(1, min(16, opts.workers))
    opts.batch = max(1, opts.batch)
    opts.session_id = tiktok.resolve_session_id(opts.session_id)
    if opts.check_session and not opts.session_id:
        p.error(t("--check-session needs a session ID: set TIKTOK_SESSIONID or add --session-id <sessionid>",
                  "--check-session cần session ID: đặt biến TIKTOK_SESSIONID hoặc thêm --session-id <sessionid>"))
    if not opts.targets and not opts.check_session:
        p.error(t("enter at least one profile (username, @username or link)",
                  "cần nhập ít nhất một profile (username, @username hoặc link)"))

    if opts.session_id:
        try:
            me = tiktok.TikTok(opts.session_id, 0, opts.proxy).whoami()
        except ScrapeError as e:
            console.log(bold_red(t("Error: ", "Lỗi: ")) + red(t(f"could not check the session ({e})",
                                                                 f"không kiểm tra được session ({e})")),
                err=True)
            sys.exit(1)
        if not me:
            console.log(bold_red(t("✗ Session ID is invalid or expired (TikTok treats you as logged out).",
                           "✗ Session ID không hợp lệ hoặc đã hết hạn (TikTok coi như chưa đăng nhập).")) + "\n"
                + yellow(t("  Copy a new sessionid cookie (see README, 'Get your session ID').",
                           "  Hãy lấy lại cookie sessionid mới (xem README, mục lấy session ID).")), err=True)
            sys.exit(1)
        nick = f" ({me['nickName']})" if me.get("nickName") else ""
        console.log(bold_green("✓ ") + green(t("Session OK: logged in as ", "Session hợp lệ: đang đăng nhập là "))
            + bold(f"@{me['uniqueId']}") + gray(nick))
        if opts.check_session:
            sys.exit(0)

    aria2 = tools.find_tool("aria2c")
    ffmpeg = tools.find_ffmpeg()
    if not opts.metadata_only:
        console.log(gray(t("Downloader: ", "Bộ tải:     ")) + (green("aria2c") + gray(f"  {aria2}") if aria2 else
            yellow(t("requests (aria2c not found, slower)", "requests (không tìm thấy aria2c, sẽ chậm hơn)"))))
        console.log(gray("ffmpeg:     ") + (green("ffmpeg") + gray(f"  {ffmpeg}") if ffmpeg else
            yellow(t("not found (no metadata in videos, no slideshows for photo posts)",
                     "không có (video sẽ không được gắn metadata, bài ảnh không dựng slideshow)"))))

    failed = 0
    try:
        for target in opts.targets:
            try:
                failed += scrape_profile(target, opts, aria2, ffmpeg)
            except RateLimited:
                raise
            except ScrapeError as e:
                failed += 1
                console.log(bold_red(t("Error: ", "Lỗi: ")) + red(str(e)), err=True)
    except ChallengeStop:
        console.log("\n" + bold_red(t("✗ TikTok's bot protection keeps blocking requests (challenge page), "
                                      "so the run stopped.",
                                      "✗ Lớp chống bot của TikTok liên tục chặn request (trang xác minh) "
                                      "nên tool đã dừng."))
            + "\n" + yellow(t("  Finished posts are kept. Try again later or from another network (VPN off, a "
                              "different connection) with the same command to continue; --session-id doesn't "
                              "help with this.",
                              "  Các bài đã xong vẫn được giữ. Hãy chạy lại đúng lệnh cũ sau một lúc hoặc bằng mạng "
                              "khác (tắt VPN, đổi kết nối) để tải tiếp; --session-id không giúp được trường hợp "
                              "này.")), err=True)
        sys.exit(1)
    except RateLimited:
        console.log("\n" + bold_red(t("✗ TikTok keeps refusing requests (HTTP 429/403), so the run stopped.",
                               "✗ TikTok liên tục từ chối request (HTTP 429/403) nên tool đã dừng.")) + "\n"
            + yellow(t("  Wait a while (or use a larger --wait / fewer --workers), then run the same "
                       "command again to continue.",
                       "  Hãy đợi một lúc (hoặc tăng --wait / giảm --workers), rồi chạy lại đúng lệnh cũ "
                       "để tải tiếp.")), err=True)
        sys.exit(1)
    except KeyboardInterrupt:
        console.log("\n" + yellow(t("Stopped. Run the same command again to continue.",
                             "Đã dừng. Chạy lại đúng lệnh cũ để tải tiếp.")), err=True)
        sys.exit(130)
    sys.exit(1 if failed else 0)

