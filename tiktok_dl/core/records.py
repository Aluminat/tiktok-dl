"""What a run leaves behind: the progress database, videos.csv and failed.txt."""
from __future__ import annotations

import csv
import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from .console import t


class Progress:
    def __init__(self, path: Path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        self.db.execute("CREATE TABLE IF NOT EXISTS posts (id TEXT PRIMARY KEY, status TEXT, "
                        "file TEXT, error TEXT, updated TEXT, url TEXT)")
        # Databases from older versions have no url column.
        if "url" not in [col[1] for col in self.db.execute("PRAGMA table_info(posts)")]:
            self.db.execute("ALTER TABLE posts ADD COLUMN url TEXT")
        self.db.commit()

    def done(self, post_id: str, folder: Path) -> bool:
        row = self.db.execute("SELECT status, file FROM posts WHERE id = ?", (post_id,)).fetchone()
        return bool(row and row[0] == "done" and row[1] and (folder / row[1]).exists())

    def mark(self, post_id: str, state: str, file: Optional[str] = None, error: Optional[str] = None,
             url: Optional[str] = None) -> None:
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO posts (id, status, file, error, updated, url) "
                            "VALUES (?, ?, ?, ?, ?, ?)",
                            (post_id, state, file, error, datetime.now().isoformat(timespec="seconds"), url))
            self.db.commit()

    def skipped(self) -> set[str]:
        """Ids of posts that can never be downloaded (e.g. deleted, or an ad without a download link)."""
        with self.lock:
            return {row[0] for row in self.db.execute("SELECT id FROM posts WHERE status = 'skipped'")}

    def files(self) -> dict[str, str]:
        """{post id: file name} of every finished post (the "file" column of videos.csv)."""
        with self.lock:
            return dict(self.db.execute("SELECT id, file FROM posts WHERE status = 'done' AND file IS NOT NULL"))

    def failures(self) -> list[tuple[str, Optional[str], Optional[str], str]]:
        """(id, link, reason, status) of every post whose latest status is an error or skipped."""
        with self.lock:
            return self.db.execute("SELECT id, url, error, status FROM posts WHERE status IN ('error', 'skipped') "
                                   "ORDER BY updated, id").fetchall()

    def close(self) -> None:
        with self.lock:
            self.db.close()


def write_csv(out_dir: Path, files: Optional[dict[str, str]] = None) -> int:
    """Rebuild videos.csv from metadata/*.json, newest first. The columns are the site's CSV row
    (Site.csv_row) and then "file", the downloaded file's name from `files` ({post id: name}), empty
    for a post not downloaded (yet). Returns the row count."""
    files = files or {}
    rows = []
    for f in sorted((out_dir / "metadata").glob("*.json")):
        try:
            row = json.loads(f.read_text(encoding="utf-8"))["metadata"]
        except (ValueError, KeyError):
            continue
        rows.append({**row, "file": files.get(str(row.get("id")))})
    rows.sort(key=lambda r: r.get("created") or "", reverse=True)
    if rows:
        with open(out_dir / "videos.csv", "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()), extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
    return len(rows)


def write_failed_file(progress: Progress, out_dir: Path, username: str, link: Callable[[str], str],
                      stop_note: Optional[str] = None) -> int:
    """Rebuild failed.txt from the progress database, so it lists every post of the profile that is
    still failing, not only this run's failures. `link(post_id)` gives the link of a row that has none.
    `stop_note` (why the run stopped early) is added to the header. Removed when nothing fails and
    there is no stop note. Returns the failing post count."""
    failed_file = out_dir / "failed.txt"
    failures = progress.failures()
    if not failures and not stop_note:
        failed_file.unlink(missing_ok=True)
        return 0
    n = len(failures)
    lines = [t(f"# @{username} - {n} posts could not be downloaded - {datetime.now():%Y-%m-%d %H:%M}",
               f"# @{username} - {n} bài không tải được - {datetime.now():%Y-%m-%d %H:%M}"),
             t("# Run the same command again to retry. Open each link to check the post yourself.",
               "# Chạy lại đúng lệnh cũ để thử lại. Mở từng link để tự kiểm tra bài đó."), ""]
    if any(state == "skipped" for *_, state in failures):
        lines.insert(2, t("# Posts marked (skipped) can never be downloaded and are not tried again; "
                          "use --retry-all to try them anyway.",
                          "# Bài có ghi (bỏ qua) không bao giờ tải được nên sẽ không thử lại; "
                          "thêm --retry-all nếu vẫn muốn thử."))
    if stop_note:
        lines.insert(2, stop_note)
    for pid, url, reason, state in failures:
        # Rows written by older versions have no link: ask the site for one.
        url = url or link(pid)
        note = t("  (skipped)", "  (bỏ qua)") if state == "skipped" else ""
        lines.append(f"{url}\n    {reason or t('unknown error', 'lỗi không rõ')}{note}")
    failed_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return n
