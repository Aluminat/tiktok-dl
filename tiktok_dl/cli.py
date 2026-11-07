"""Command line: the arguments and main(). For now it also holds the main loop (scrape_profile)."""
from __future__ import annotations

import argparse
import functools
import json
import os
import shutil
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from . import __version__
from .core import console, download, mux, records, tools
from .core.console import (
    ProgressBar,
    blue,
    bold,
    bold_cyan,
    bold_green,
    bold_red,
    cyan,
    fmt_bytes,
    fmt_eta,
    fmt_int,
    gray,
    green,
    link,
    magenta,
    red,
    t,
    yellow,
)
from .core.errors import ChallengeStop, RateLimited, ScrapeError
from .core.stop import STOP, wait_result
from .sites import tiktok

# --------------------------------------------------------------------------- #
# Main flow
# --------------------------------------------------------------------------- #

def scrape_profile(target: str, opts, aria2: Optional[str], ffmpeg: Optional[str]) -> int:
    target = tiktok.resolve_short_link(target.strip())   # keep the full link: it tells photo posts apart
    username, single = tiktok.parse_target(target)
    out_dir = Path(opts.output).resolve() / username
    (out_dir / "metadata").mkdir(parents=True, exist_ok=True)
    work = out_dir / ".parts"
    work.mkdir(exist_ok=True)
    (work / "session-cookies.txt").unlink(missing_ok=True)  # left behind by older versions
    tt = tiktok.TikTok(opts.session_id, opts.wait, opts.proxy)
    failure_link = functools.partial(tiktok.post_link, username)   # failed.txt rows without a link

    console.log("\n" + bold_cyan(f"━━ @{username} ") + gray("━" * max(4, 40 - len(username))))
    info = tiktok.save_profile(tt, username, out_dir)
    if info:
        u, st = info.get("user") or {}, info.get("stats") or {}
        dot = gray(" · ")
        console.log(f"  {bold(u.get('nickname', username))}{cyan(' ✓') if u.get('verified') else ''}{dot}"
            f"{bold(fmt_int(st.get('followerCount')))} {gray(t('followers', 'follower'))}{dot}"
            f"{bold(fmt_int(st.get('heartCount')))} {gray(t('likes', 'tim'))}{dot}"
            f"{bold(fmt_int(st.get('videoCount')))} {gray(t('posts', 'bài đăng'))}")

    if single:
        entries = [{"id": single, "url": target}]
    else:
        try:
            entries = tiktok.list_posts_with_session(username, opts.limit, opts.session_id, work)
        finally:
            console.status("")
        found = bold(len(entries))
        console.log(gray(t("  Found ", "  Tìm thấy ")) + found + gray(t(" posts.", " bài đăng.")))

    progress = records.Progress(out_dir / ".progress.db")
    if opts.metadata_only:
        pending = [e for e in entries if not (out_dir / "metadata" / f"{e['id']}.json").exists()]
    else:
        pending = [e for e in entries if not progress.done(str(e["id"]), out_dir)]
    finished = len(entries) - len(pending)
    if not opts.retry_all:
        never = progress.skipped()
        pending = [e for e in pending if str(e["id"]) not in never]
    permanent = len(entries) - finished - len(pending)
    if finished and not permanent:
        console.log(gray(t(f"  Skipping {finished} posts already downloaded.", f"  Bỏ qua {finished} bài đã có.")))
    elif permanent:
        console.log(gray(t(f"  Skipping {finished + permanent} posts: {finished} already downloaded, {permanent} that "
                   "can never be downloaded (deleted / ad posts, see failed.txt; --retry-all to try again).",
                   f"  Bỏ qua {finished + permanent} bài: {finished} bài đã có, {permanent} bài không bao giờ tải "
                   "được (đã xoá / quảng cáo, xem failed.txt; thêm --retry-all để thử lại).")))

    n = len(pending)
    if not n:
        console.log(green(t("  ✓ Nothing left to download.", "  ✓ Không còn bài nào cần tải.")))
        records.write_csv(out_dir)
        records.write_failed_file(progress, out_dir, username, failure_link)
        progress.close()
        return 0
    console.log(gray(t(f"  Processing {n} post{'s' if n != 1 else ''} → ", f"  Bắt đầu xử lý {n} bài → "))
        + blue(str(out_dir)) + "\n")
    started = time.monotonic()
    tally = {"ok": 0, "failed": 0, "bytes": 0, "dl_bytes": 0, "dl_secs": 0.0}
    tally_lock = threading.Lock()
    bar = ProgressBar(n)

    def post_url(entry: dict) -> str:
        # Before the post page is read, only a /photo/ link (e.g. the one the user gave) marks a photo post.
        kind = "photo" if "/photo/" in (entry.get("url") or "").lower() else "video"
        return f"https://www.tiktok.com/@{username}/{kind}/{entry['id']}"

    def succeed(line: str, size: int = 0) -> None:
        with tally_lock:
            tally["ok"] += 1
            tally["bytes"] += size
        bar.post_done(True)
        console.log(f"  {bold_green('✓')} {line}")

    def fail(url: str, reason: str) -> None:
        with tally_lock:
            tally["failed"] += 1
        bar.post_done(False)
        console.log(f"  {bold_red('✗')} {link(url)}\n      {gray('└')} {yellow(reason)}")

    def fetch(entry):
        if STOP.is_set():
            return entry, None, t("stopped", "đã dừng")
        try:
            return entry, tt.item(username, str(entry["id"])), None
        except RateLimited:
            raise
        except ScrapeError as e:
            return entry, None, e
        except Exception as e:   # an odd page must fail only this post, not the whole batch
            return entry, None, t(f"unexpected page format ({type(e).__name__}: {e})",
                                  f"trang có định dạng không như dự kiến ({type(e).__name__}: {e})")

    def fetch_batch(batch):
        with ThreadPoolExecutor(opts.workers) as pool:
            return list(pool.map(fetch, batch))

    def finish(post: tiktok.Post) -> None:
        if STOP.is_set():
            return
        try:
            name, size, note = mux.finalize(post, out_dir, ffmpeg, opts)
        except (ScrapeError, OSError) as e:
            if STOP.is_set():
                return  # cut short by Ctrl+C: left unmarked so the next run retries it
            progress.mark(post.id, "error", error=str(e), url=post.url)
            fail(post.url, str(e))
            return
        except Exception as e:   # a bug or odd data in one post must not stop the run
            reason = t(f"unexpected error ({type(e).__name__}: {e})",
                       f"lỗi không lường trước ({type(e).__name__}: {e})")
            progress.mark(post.id, "error", error=reason, url=post.url)
            fail(post.url, reason)
            return
        progress.mark(post.id, "done", file=name)
        kind = magenta("[slideshow] ") if post.is_slide else ""
        extra = f"  {yellow('(' + note + ')')}" if note else ""
        succeed(f"{kind}{name}  {gray(fmt_bytes(size))}{extra}", size)

    # The 3 steps overlap: while aria2c downloads the current batch, the next batch's metadata is
    # read and finished batches are muxed by ffmpeg in the background. Only one batch is read
    # ahead because TikTok download URLs expire after a few hours.
    batches = [pending[i:i + opts.batch] for i in range(0, n, opts.batch)]
    meta_pool = ThreadPoolExecutor(1)
    mux_pool = ThreadPoolExecutor(opts.mux_jobs)
    mux_futures = []
    completed = interrupted = False
    stopped: Optional[RateLimited] = None
    with bar:
        try:
            upcoming = meta_pool.submit(fetch_batch, batches[0])
            for bi, batch in enumerate(batches):
                results = wait_result(upcoming)
                cookie = tt.cookie_header()
                upcoming = meta_pool.submit(fetch_batch, batches[bi + 1]) if bi + 1 < len(batches) else None

                posts: list[tiktok.Post] = []
                for entry, item, err in results:
                    pid = str(entry["id"])
                    if err:
                        reason = t(f"could not read the post page: {err}", f"không đọc được trang bài đăng: {err}")
                        # getattr: err may also be a plain message string, not only a ScrapeError
                        state = "skipped" if getattr(err, "code", 0) in tiktok.PERMANENT_POST_CODES else "error"
                        progress.mark(pid, state, error=reason, url=post_url(entry))
                        fail(post_url(entry), reason)
                        continue
                    try:
                        meta = tiktok.flatten(item)
                        post = None if opts.metadata_only else tiktok.Post(item, work, opts)
                    except Exception as e:   # odd field types in the post JSON: fail only this post
                        reason = t(f"unexpected page format ({type(e).__name__}: {e})",
                                   f"trang có định dạng không như dự kiến ({type(e).__name__}: {e})")
                        progress.mark(pid, "error", error=reason, url=post_url(entry))
                        fail(post_url(entry), reason)
                        continue
                    meta_path = out_dir / "metadata" / f"{pid}.json"
                    meta_path.write_text(json.dumps({"metadata": meta, "raw": item},
                                                    ensure_ascii=False, indent=2), encoding="utf-8")
                    if opts.metadata_only:
                        progress.mark(pid, "metadata")
                        succeed(f"{gray('metadata')} {pid}")
                        continue
                    if post.hidden:
                        progress.mark(pid, "skipped" if post.permanent else "error", error=post.hidden, url=post.url)
                        fail(post.url, post.hidden)
                        continue
                    posts.append(post)
                if not posts:
                    continue

                tasks = [task for p in posts for task in p.tasks]
                bar.downloading(tasks)
                # Files finished in an earlier run do not count towards the speed.
                already = sum(d.stat().st_size for _, d, _ in tasks if download.is_valid_file(d))
                t0 = time.monotonic()
                try:
                    if aria2:
                        truncated: set[Path] = set()
                        for attempt in range(max((len(task[0]) for task in tasks), default=0)):
                            truncated |= download.download_aria2(aria2, tasks, cookie, opts.jobs, opts.connections, attempt,
                                                           "https://www.tiktok.com/")
                        for _, dest, _ in tasks:
                            if not download.is_valid_file(dest):
                                dest.unlink(missing_ok=True)
                    else:
                        truncated = download.download_requests(tt.s, tasks, opts.jobs) or set()
                    for post in posts:
                        post.incomplete = not post.is_slide and post.video in truncated
                finally:
                    bar.downloaded()
                with tally_lock:
                    tally["dl_secs"] += time.monotonic() - t0
                    after = sum(d.stat().st_size for _, d, _ in tasks if d.exists())
                    tally["dl_bytes"] += max(0, after - already)

                mux_futures += [mux_pool.submit(finish, post) for post in posts]
            for fut in mux_futures:
                wait_result(fut)
            completed = True
        except KeyboardInterrupt:
            STOP.set()
            interrupted = True
            raise
        except RateLimited as e:   # rate-limit streak or challenge-page streak (ChallengeStop)
            stopped = e  # write failed.txt first, then stop the run
        finally:
            tt.stop()  # queued page reads of an aborted batch give up instead of running on
            meta_pool.shutdown(wait=False, cancel_futures=True)
            # On Ctrl+C, drop queued mux jobs; running ones were stopped by Ctrl+C too and end quickly.
            mux_pool.shutdown(wait=True, cancel_futures=STOP.is_set())
            if not completed and not stopped:
                # The run is ending with an error or Ctrl+C: nothing below runs. On Ctrl+C, still
                # rebuild failed.txt (all marks are in now); the CSV and work folder are left as they are.
                try:
                    if interrupted:
                        records.write_failed_file(progress, out_dir, username, failure_link)
                except (OSError, sqlite3.Error):
                    pass   # best effort: a locked failed.txt must not hide the Ctrl+C (exit 130)
                finally:
                    progress.close()

    ok, failed, total_bytes = tally["ok"], tally["failed"], tally["bytes"]
    rows = records.write_csv(out_dir)
    if not opts.keep_parts and work.exists() and not any(p.is_dir() for p in work.iterdir()):
        shutil.rmtree(work, ignore_errors=True)

    stop_note = None
    if stopped:
        left = n - ok - failed
        stop_note = t(f"# The run stopped early, {left} posts were not processed: {stopped}",
                      f"# Tool đã dừng sớm, còn {left} bài chưa xử lý: {stopped}")
    all_failed = records.write_failed_file(progress, out_dir, username, failure_link, stop_note)
    progress.close()
    if stopped:
        raise stopped

    secs = max(1.0, time.monotonic() - started)
    dot = gray(" · ")
    speed = (f"{dot}aria2c {cyan(fmt_bytes(tally['dl_bytes'] / tally['dl_secs']) + '/s')}"
             if tally["dl_secs"] > 0 else "")
    title = t(f"Done @{username} ", f"Xong @{username} ")
    console.log("\n" + bold(f"  ━━ {title}") + gray("━" * max(4, 36 - len(title))))
    counts = (f"  {bold_green('✓ ' + str(ok))} {t('done', 'thành công')}   "
              + (bold_red(f"✗ {failed}") if failed else gray("✗ 0")) + f" {t('failed', 'lỗi')}")
    if total_bytes:
        counts += f"{dot}{bold(fmt_bytes(total_bytes))} {t('in', 'trong')} {fmt_eta(secs)}{speed}"
    console.log(counts)
    console.log(gray(t("  Folder: ", "  Thư mục: ")) + blue(str(out_dir))
        + gray(t(f"  (videos.csv: {rows} post{'s' if rows != 1 else ''})", f"  (videos.csv: {rows} bài)")))
    if all_failed:
        # Counts every failing post of the profile, including ones from earlier runs.
        console.log(yellow(t(f"  Failed posts with links ({all_failed}): ", f"  Danh sách bài lỗi kèm link ({all_failed}): "))
            + blue(str(out_dir / "failed.txt")))
    if failed:
        console.log(gray(t("  Run the same command again to retry failed posts (finished posts are skipped).",
                   "  Chạy lại đúng lệnh cũ để thử lại các bài lỗi (bài đã xong sẽ được bỏ qua).")))
    return failed


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
                   help=t("posts per read-download-mux batch (default: 50)", "số bài mỗi lô đọc-tải-mux (mặc định: 50)"))
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
            console.log(bold_red(t("Error: ", "Lỗi: ")) + red(t(f"could not check the session ({e})", f"không kiểm tra được session ({e})")),
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
        console.log("\n" + bold_red(t("✗ TikTok's bot protection keeps blocking requests (challenge page), so the run stopped.",
                               "✗ Lớp chống bot của TikTok liên tục chặn request (trang xác minh) nên tool đã dừng."))
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

