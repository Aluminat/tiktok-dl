"""ffmpeg: muxing a post's video with its tags, cover and subtitles, photo slideshows, and putting the
finished files in place (finalize)."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from . import download
from .console import t
from .errors import ScrapeError
from .records import FILE_SEP
from .stop import STOP

if TYPE_CHECKING:
    from .download import Download


def run_ffmpeg(cmd: list[str]) -> tuple[bool, str]:
    # Ctrl+C reaches ffmpeg too and makes it fail. Report that as "stopped", so mux_video() does not
    # start a fallback attempt (e.g. without the cover) and record the post as done.
    if STOP.is_set():
        raise ScrapeError(t("stopped", "đã dừng"))
    p = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    # ffmpeg can exit from Ctrl+C a moment before the main thread sets STOP. For an exit code that
    # looks like a signal (255 from ffmpeg's own handler, 0xC000013A on Windows, <0 on POSIX),
    # give STOP a second to be set before treating it as a real error.
    by_signal = p.returncode < 0 or p.returncode in (255, 0xC000013A)
    if p.returncode and STOP.wait(1 if by_signal else 0):
        raise ScrapeError(t("stopped", "đã dừng"))
    return p.returncode == 0, p.stderr.decode("utf-8", "replace").strip()[-400:]


def tag_args(tags: dict) -> list[str]:
    out = []
    for k, v in tags.items():
        if v:
            out += ["-metadata", f"{k}={v}"]
    return out


def mux_attempts(has_cover: bool, has_subs: bool) -> list[tuple[bool, bool]]:
    """(with_cover, with_subs) for each ffmpeg attempt, most complete first.

    Only parts the post really has are tried, so no two attempts run the same command."""
    attempts = []
    for with_cover, with_subs in ((True, True), (False, True), (False, False)):
        attempt = (with_cover and has_cover, with_subs and has_subs)
        if attempt not in attempts:
            attempts.append(attempt)
    return attempts


def mux_video(ffmpeg: str, post: Download, out: Path,
              item: Optional[tuple[Path, Optional[Path]]] = None) -> str:
    """Mux the post's video with its tags, cover and subtitles. `item` (video, cover) muxes one video
    item of a post saved item by item instead, with the post's tags and the item's cover."""
    if item:
        (video, cover), subs = item, []
    else:
        video, cover, subs = post.video, post.cover, post.subs
    tags = tag_args(post.tags)
    subs = [(p, lang) for p, lang in subs if p.exists() and p.stat().st_size]
    cover = cover if cover and cover.exists() and cover.stat().st_size else None
    # A separate sound stream (not for an item); finalize() checked that it downloaded.
    audio = None if item else getattr(post, "audio", None)

    def build(with_cover: bool, with_subs: bool) -> list[str]:
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(video)]
        if audio:   # never dropped by the fallback attempts: a video without its sound is not a result
            cmd += ["-i", str(audio)]
            maps = ["-map", "0:v:0", "-map", "1:a:0"]
        else:
            maps = ["-map", "0:v:0", "-map", "0:a?"]
        n = 2 if audio else 1   # the next input's number
        use_subs = subs if with_subs else []
        for p, _ in use_subs:
            cmd += ["-i", str(p)]
            maps += ["-map", f"{n}:0"]
            n += 1
        if with_cover and cover:
            cmd += ["-i", str(cover)]
            maps += ["-map", f"{n}:0"]
        cmd += maps + ["-c", "copy", "-c:s", "mov_text"]
        if with_cover and cover:
            cmd += ["-c:v:1", "mjpeg", "-disposition:v:1", "attached_pic"]
        for i, (_, lang) in enumerate(use_subs):
            cmd += [f"-metadata:s:s:{i}", f"language={lang}"]
        return cmd + tags + ["-movflags", "+faststart", str(out)]

    # If the cover (sometimes an odd HEIC/WebP) or a subtitle breaks ffmpeg, drop them one at a time.
    for with_cover, with_subs in mux_attempts(bool(cover), bool(subs)):
        ok, err = run_ffmpeg(build(with_cover, with_subs))
        if ok:
            notes = []
            if cover and not with_cover:
                notes.append(t("cover skipped", "bỏ ảnh bìa"))
            if subs and not with_subs:
                notes.append(t("subtitles skipped", "bỏ phụ đề"))
            return ", ".join(notes)
    raise ScrapeError(t(f"ffmpeg error: {err}", f"ffmpeg lỗi: {err}"))


def mux_slideshow(ffmpeg: str, post: Download, out: Path, seconds: float) -> None:
    images = [p for p in post.images if p.exists() and p.stat().st_size]
    # Each image is its own input (looped for `seconds`), scaled/padded to 1080x1920, then joined.
    # The concat demuxer is not used: images in one post often differ by a few pixels, which makes
    # ffmpeg rebuild its filters mid-stream and drop the first frames (video starts seconds late).
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    for p in images:
        cmd += ["-loop", "1", "-framerate", "30", "-t", str(seconds), "-i", str(p.resolve())]
    audio = post.audio if post.audio and post.audio.exists() and post.audio.stat().st_size else None
    if audio:
        cmd += ["-stream_loop", "-1", "-i", str(audio.resolve())]
    chains = [
        f"[{i}:v]scale=1080:1920:force_original_aspect_ratio=decrease:out_range=tv,"
        f"pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,format=yuv420p,fps=30[v{i}]"
        for i in range(len(images))
    ]
    joined = "".join(f"[v{i}]" for i in range(len(images)))
    graph = ";".join(chains) + f";{joined}concat=n={len(images)}:v=1:a=0[v]"
    cmd += ["-filter_complex", graph, "-map", "[v]"]
    if audio:
        cmd += ["-map", f"{len(images)}:a", "-c:a", "aac", "-b:a", "192k"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-r", "30",
            "-t", str(seconds * len(images))]
    cmd += tag_args(post.tags) + ["-movflags", "+faststart", str(out)]
    ok, err = run_ffmpeg(cmd)
    if not ok:
        raise ScrapeError(t(f"ffmpeg could not build the slideshow: {err}", f"ffmpeg không dựng được slideshow: {err}"))


def save_items(post: Download, out_dir: Path, ffmpeg: Optional[str], opts) -> tuple[list[Path], str]:
    """Put a post saved item by item (Media.items) into out_dir: <base>.<ext> for one item, <base>_NN.<ext>
    for several, video items muxed with the post's tags; or, with --slideshow and only photos, a
    <base>.mp4 slideshow (and the photos too with --keep-images). Returns (files, note).

    All or nothing: with an item missing, nothing goes to out_dir and the work folder is kept, so the
    next run downloads only the missing items (the others are valid files there already)."""
    missing = [str(i) for i, (item, _) in enumerate(post.items, 1) if not (item.exists() and item.stat().st_size)]
    if missing:
        which, n = ", ".join(missing), len(post.items)
        raise ScrapeError(t(f"item {which} of {n} could not be downloaded (the next run downloads only what "
                            "is missing)",
                            f"không tải được mục {which} trên {n} (lần chạy sau chỉ tải lại phần còn thiếu)"))
    produced: list[Path] = []
    notes = set()
    if post.slideshow and ffmpeg:
        tmp = post.work / "slideshow.mp4"
        mux_slideshow(ffmpeg, post, tmp, opts.slide_seconds)
        produced.append(out_dir / f"{post.base}.mp4")
        os.replace(tmp, produced[0])
        if not opts.keep_images:
            return produced, ""
    numbered = len(post.items) > 1
    for i, (item, cover) in enumerate(post.items, 1):
        dest = out_dir / (f"{post.base}_{i:02d}{item.suffix}" if numbered else f"{post.base}{item.suffix}")
        if item.suffix == ".mp4" and ffmpeg:
            tmp = post.work / "muxed.mp4"
            notes.add(mux_video(ffmpeg, post, tmp, (item, cover)))
            os.replace(tmp, dest)
        else:
            shutil.copyfile(item, dest)
            if item.suffix == ".mp4":
                notes.add(t("no ffmpeg, metadata not added", "không có ffmpeg, chưa gắn metadata"))
        produced.append(dest)
    return produced, ", ".join(sorted(n for n in notes if n))


def finalize(post: Download, out_dir: Path, ffmpeg: Optional[str], opts) -> tuple[str, int, str]:
    """Mux/move files of one post into out_dir. Returns (main file name, total bytes, note); for a post
    saved item by item, the names of all its files joined with FILE_SEP."""
    when = post.created.timestamp()
    produced: list[Path] = []
    note = ""

    if post.items:
        produced, note = save_items(post, out_dir, ffmpeg, opts)
        main = produced[0]
    elif post.is_slide:
        images = [p for p in post.images if p.exists() and p.stat().st_size]
        if not images:
            raise ScrapeError(t("no image could be downloaded", "không tải được ảnh nào"))
        if len(images) < len(post.images):
            missing = len(post.images) - len(images)
            note = t(f"{missing} image(s) missing", f"thiếu {missing} ảnh")
        main = None
        if ffmpeg and opts.slideshow:
            tmp = post.work / "slideshow.mp4"
            mux_slideshow(ffmpeg, post, tmp, opts.slide_seconds)
            main = out_dir / f"{post.base}.mp4"
            os.replace(tmp, main)
            produced.append(main)
        if opts.keep_images or not main:
            for i, p in enumerate(images, 1):
                dest = out_dir / f"{post.base}_{i:02d}.jpg"
                shutil.copyfile(p, dest)
                produced.append(dest)
            if post.audio and post.audio.exists():
                dest = out_dir / f"{post.base}.mp3"
                shutil.copyfile(post.audio, dest)
                produced.append(dest)
        main = main or produced[0]
    else:
        if not post.video.exists() or post.video.stat().st_size < 1024:
            if post.incomplete:
                site = post.site.label if post.site else t("the site", "trang")
                raise ScrapeError(t(f"video download incomplete: the file is smaller than the size {site} "
                                    "reports (every link was tried)",
                                    f"tải video không trọn vẹn: file nhỏ hơn dung lượng {site} báo "
                                    "(đã thử hết các link tải)"))
            raise ScrapeError(t("video could not be downloaded", "không tải được video"))
        audio = getattr(post, "audio", None)
        if audio and (not audio.exists() or audio.stat().st_size < download.MIN_FILE_BYTES):
            raise ScrapeError(t("audio could not be downloaded", "không tải được âm thanh"))
        main = out_dir / f"{post.base}.mp4"
        if ffmpeg:
            tmp = post.work / "muxed.mp4"
            note = mux_video(ffmpeg, post, tmp)
            os.replace(tmp, main)
            produced.append(main)
        elif audio:   # nothing can join them: keep both, so the sound is not lost
            shutil.copyfile(post.video, main)
            shutil.copyfile(audio, out_dir / f"{post.base}.m4a")
            produced += [main, out_dir / f"{post.base}.m4a"]
            note = t("no ffmpeg: video and audio saved as separate files, no metadata added",
                     "không có ffmpeg: video và âm thanh được lưu thành 2 file riêng, chưa gắn metadata")
        else:
            shutil.copyfile(post.video, main)
            note = t("no ffmpeg, metadata not added", "không có ffmpeg, chưa gắn metadata")
            produced.append(main)

    for p in produced:
        os.utime(p, (when, when))
    if not opts.keep_parts:
        shutil.rmtree(post.work, ignore_errors=True)
    # A post saved item by item lists every file: it is done only when all of them are on disk.
    name = FILE_SEP.join(p.name for p in produced) if post.items else main.name
    return name, sum(p.stat().st_size for p in produced), note
