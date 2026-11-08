"""The main loop: read posts -> download -> mux, for one profile (run_profile).

The three steps overlap: while aria2c downloads one batch, the next batch's post pages are read and
the previous batch is muxed in the background. The site (core/site.py) does everything that depends
on the website; this loop is the same for every site.
"""
from __future__ import annotations

import copy
import json
import shutil
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from . import console, download, mux, records
from .console import (
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
    t,
    yellow,
)
from .errors import RateLimited, ScrapeError
from .site import Site, Verdict
from .stop import STOP, wait_result


def run_settings(site_cls: type[Site], opts):
    """A copy of opts for one run of this site: --wait, --workers and --connections the user left out
    (None) take the site's defaults (Site.defaults), then are kept in range. A copy, because one opts
    serves every target of the command line, and they may be on different sites."""
    run = copy.copy(opts)
    for key, value in site_cls.defaults.items():
        if getattr(run, key, None) is None:
            setattr(run, key, value)
    run.workers = max(1, min(16, run.workers))
    run.connections = max(1, min(16, run.connections))
    return run


def run_profile(site_cls: type[Site], target: str, opts, aria2: Optional[str], ffmpeg: Optional[str]) -> int:
    """Download one profile, or one post of it, with the given site. Returns the number of posts that
    failed in this run. Raises ScrapeError when the profile cannot be read at all, and RateLimited
    (ChallengeStop, SiteStop) when the run has to stop."""
    profile, single = site_cls.parse(target)
    opts = run_settings(site_cls, opts)
    # Constructed before any folder is made: a site that needs a login fails here, with nothing written.
    site = site_cls(profile, site_cls.session(opts), opts.wait, opts.proxy)
    if profile is None:   # the link does not name the profile (e.g. an Instagram reel link): ask the site
        profile = site.owner(single)
    out_dir = Path(opts.output).resolve() / site_cls.folder(profile)
    (out_dir / "metadata").mkdir(parents=True, exist_ok=True)
    work = out_dir / ".parts"
    work.mkdir(exist_ok=True)
    (work / "session-cookies.txt").unlink(missing_ok=True)  # left behind by older versions

    console.log("\n" + bold_cyan(f"━━ @{profile} ") + gray("━" * max(4, 40 - len(profile))))
    info = site.save_profile(out_dir)
    if info:
        dot = gray(" · ")
        console.log(f"  {bold(info.nickname)}{cyan(' ✓') if info.verified else ''}{dot}"
                    f"{bold(fmt_int(info.followers))} {gray(t('followers', 'follower'))}{dot}"
                    f"{bold(fmt_int(info.likes))} {gray(t('likes', 'tim'))}{dot}"
                    f"{bold(fmt_int(info.posts))} {gray(t('posts', 'bài đăng'))}")

    if single:
        entries = [single]
    else:
        try:
            entries = site.list_posts(opts.limit, work)
        finally:
            console.status("")
        found = bold(len(entries))
        console.log(gray(t("  Found ", "  Tìm thấy ")) + found + gray(t(" posts.", " bài đăng.")))

    progress = records.Progress(out_dir / ".progress.db")
    if opts.metadata_only:
        pending = [e for e in entries if not (out_dir / "metadata" / f"{e.id}.json").exists()]
    else:
        pending = [e for e in entries if not progress.done(e.id, out_dir)]
    finished = len(entries) - len(pending)
    if not opts.retry_all:
        never = progress.skipped()
        pending = [e for e in pending if e.id not in never]
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
        records.write_csv(out_dir, progress.files())
        records.write_failed_file(progress, out_dir, profile, site.link)
        progress.close()
        return 0
    console.log(gray(t(f"  Processing {n} post{'s' if n != 1 else ''} → ", f"  Bắt đầu xử lý {n} bài → "))
                + blue(str(out_dir)) + "\n")
    started = time.monotonic()
    tally = {"ok": 0, "failed": 0, "bytes": 0, "dl_bytes": 0, "dl_secs": 0.0}
    tally_lock = threading.Lock()
    bar = ProgressBar(n)

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
            return entry, site.read_post(entry.id), None
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

    def finish(plan: download.Download) -> None:
        if STOP.is_set():
            return
        try:
            name, size, note = mux.finalize(plan, out_dir, ffmpeg, opts)
        except (ScrapeError, OSError) as e:
            if STOP.is_set():
                return  # cut short by Ctrl+C: left unmarked so the next run retries it
            progress.mark(plan.id, "error", error=str(e), url=plan.url)
            fail(plan.url, str(e))
            return
        except Exception as e:   # a bug or odd data in one plan must not stop the run
            reason = t(f"unexpected error ({type(e).__name__}: {e})",
                       f"lỗi không lường trước ({type(e).__name__}: {e})")
            progress.mark(plan.id, "error", error=reason, url=plan.url)
            fail(plan.url, reason)
            return
        progress.mark(plan.id, "done", file=name)
        kind = magenta("[slideshow] ") if plan.is_slide else ""
        extra = f"  {yellow('(' + note + ')')}" if note else ""
        first, *more = name.split(records.FILE_SEP)   # a post saved item by item lists all its files
        more_files = gray(t(f" +{len(more)} files", f" +{len(more)} file")) if more else ""
        succeed(f"{kind}{first}{more_files}  {gray(fmt_bytes(size))}{extra}", size)

    # The 3 steps overlap: while aria2c downloads the current batch, the next batch's metadata is
    # read and finished batches are muxed by ffmpeg in the background. Only one batch is read
    # ahead because download URLs expire after a few hours.
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
                cookie = site.client.cookie_header()
                upcoming = meta_pool.submit(fetch_batch, batches[bi + 1]) if bi + 1 < len(batches) else None

                plans: list[download.Download] = []
                for entry, post, err in results:
                    pid = entry.id
                    if err:
                        reason = t(f"could not read the post page: {err}", f"không đọc được trang bài đăng: {err}")
                        # err may also be a plain message string, not only a ScrapeError
                        permanent = isinstance(err, ScrapeError) and site.classify(err) is Verdict.PERMANENT
                        state = "skipped" if permanent else "error"
                        progress.mark(pid, state, error=reason, url=entry.link)
                        fail(entry.link, reason)
                        continue
                    try:
                        # The CSV row first: it checks the post data the plan reads (same error, same step).
                        meta = site.csv_row(post)
                        plan = None if opts.metadata_only else download.Download(post, work, opts, site)
                    except Exception as e:   # odd field types in the post JSON: fail only this post
                        reason = t(f"unexpected page format ({type(e).__name__}: {e})",
                                   f"trang có định dạng không như dự kiến ({type(e).__name__}: {e})")
                        progress.mark(pid, "error", error=reason, url=entry.link)
                        fail(entry.link, reason)
                        continue
                    meta_path = out_dir / "metadata" / f"{pid}.json"
                    meta_path.write_text(json.dumps({"metadata": meta, "raw": post.raw},
                                                    ensure_ascii=False, indent=2), encoding="utf-8")
                    if opts.metadata_only:
                        progress.mark(pid, "metadata")
                        succeed(f"{gray('metadata')} {pid}")
                        continue
                    if plan.hidden:
                        progress.mark(pid, "skipped" if plan.permanent else "error", error=plan.hidden, url=plan.url)
                        fail(plan.url, plan.hidden)
                        continue
                    plans.append(plan)
                if not plans:
                    continue

                tasks = [task for p in plans for task in p.tasks]
                bar.downloading(tasks)
                # Files finished in an earlier run do not count towards the speed.
                already = sum(d.stat().st_size for _, d, _ in tasks if download.is_valid_file(d))
                t0 = time.monotonic()
                try:
                    if aria2:
                        truncated: set[Path] = set()
                        for attempt in range(max((len(task[0]) for task in tasks), default=0)):
                            truncated |= download.download_aria2(aria2, tasks, cookie, opts.jobs, opts.connections,
                                                                 attempt, site.referer)
                        for _, dest, _ in tasks:
                            if not download.is_valid_file(dest):
                                dest.unlink(missing_ok=True)
                    else:
                        truncated = download.download_requests(site.client.s, tasks, opts.jobs) or set()
                    for plan in plans:
                        plan.incomplete = plan.video is not None and plan.video in truncated
                finally:
                    bar.downloaded()
                with tally_lock:
                    tally["dl_secs"] += time.monotonic() - t0
                    after = sum(d.stat().st_size for _, d, _ in tasks if d.exists())
                    tally["dl_bytes"] += max(0, after - already)

                mux_futures += [mux_pool.submit(finish, plan) for plan in plans]
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
            site.client.stop()  # queued page reads of an aborted batch give up instead of running on
            meta_pool.shutdown(wait=False, cancel_futures=True)
            # On Ctrl+C, drop queued mux jobs; running ones were stopped by Ctrl+C too and end quickly.
            mux_pool.shutdown(wait=True, cancel_futures=STOP.is_set())
            if not completed and not stopped:
                # The run is ending with an error or Ctrl+C: nothing below runs. On Ctrl+C, still
                # rebuild failed.txt (all marks are in now); the CSV and work folder are left as they are.
                try:
                    if interrupted:
                        records.write_failed_file(progress, out_dir, profile, site.link)
                except (OSError, sqlite3.Error):
                    pass   # best effort: a locked failed.txt must not hide the Ctrl+C (exit 130)
                finally:
                    progress.close()

    ok, failed, total_bytes = tally["ok"], tally["failed"], tally["bytes"]
    rows = records.write_csv(out_dir, progress.files())
    if not opts.keep_parts and work.exists() and not any(p.is_dir() for p in work.iterdir()):
        shutil.rmtree(work, ignore_errors=True)

    stop_note = None
    if stopped:
        left = n - ok - failed
        stop_note = t(f"# The run stopped early, {left} posts were not processed: {stopped}",
                      f"# Tool đã dừng sớm, còn {left} bài chưa xử lý: {stopped}")
    all_failed = records.write_failed_file(progress, out_dir, profile, site.link, stop_note)
    progress.close()
    if stopped:
        raise stopped

    secs = max(1.0, time.monotonic() - started)
    dot = gray(" · ")
    speed = (f"{dot}aria2c {cyan(fmt_bytes(tally['dl_bytes'] / tally['dl_secs']) + '/s')}"
             if tally["dl_secs"] > 0 else "")
    title = t(f"Done @{profile} ", f"Xong @{profile} ")
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
        console.log(yellow(t(f"  Failed posts with links ({all_failed}): ",
                             f"  Danh sách bài lỗi kèm link ({all_failed}): "))
                    + blue(str(out_dir / "failed.txt")))
    if failed:
        console.log(gray(t("  Run the same command again to retry failed posts (finished posts are skipped).",
                           "  Chạy lại đúng lệnh cũ để thử lại các bài lỗi (bài đã xong sẽ được bỏ qua).")))
    return failed
