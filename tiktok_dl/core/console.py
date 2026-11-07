"""Console output: the message language (t()), colours, log lines, the status line and the progress bar.

Messages are bilingual: t("English", "Tiếng Việt"). Other modules call log() and status() through
this module (console.log(...)), so tests can replace them, and read LANG as console.LANG.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import threading
import time
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


# Message language: "en" (default) or "vi". Set with --lang or the TIKTOK_DL_LANG environment variable.
LANG = "en"


def t(en: str, vi: str) -> str:
    """Pick the message for the current language."""
    return vi if LANG == "vi" else en


def detect_lang(argv: list[str]) -> str:
    for i, arg in enumerate(argv):
        if arg.startswith("--lang="):
            return arg.split("=", 1)[1].lower()
        if arg == "--lang" and i + 1 < len(argv):
            return argv[i + 1].lower()
    env = os.environ.get("TIKTOK_DL_LANG", "").lower()
    if env in ("en", "vi"):
        return env
    try:
        import locale
        name = (locale.getlocale()[0] or "").lower()
    except Exception:
        name = ""
    return "vi" if name.startswith(("vi", "vietnam")) else "en"


_print_lock = threading.RLock()
_status_line = ""   # status line kept at the bottom (progress bar)
_COLOR = False      # turned on in main() when running in a terminal
_TTY = sys.stdout.isatty()
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _style(code: str):
    return lambda s: f"\x1b[{code}m{s}\x1b[0m" if _COLOR else str(s)


bold = _style("1")
dim = _style("2")
gray = _style("90")
red = _style("31")
green = _style("32")
yellow = _style("33")
blue = _style("34")
magenta = _style("35")
cyan = _style("36")
bold_red = _style("1;31")
bold_green = _style("1;32")
bold_cyan = _style("1;36")
link = _style("4;34")


def init_colors(enabled: bool) -> None:
    """Enable ANSI colors when running in a terminal (Windows 10+ needs VT mode switched on)."""
    global _COLOR
    forced = bool(os.environ.get("FORCE_COLOR"))   # e.g. keep colors when output is redirected to a file
    if not enabled or os.environ.get("NO_COLOR") or not (_TTY or forced):
        _COLOR = False
        return
    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            for handle_id in (-11, -12):   # stdout, stderr
                handle = kernel32.GetStdHandle(handle_id)
                mode = ctypes.c_uint32()
                if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                    kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        except Exception:
            _COLOR = False
            return
    _COLOR = True


def visible_len(text: str) -> int:
    return len(_ANSI_RE.sub("", text))


def _width() -> int:
    return max(40, shutil.get_terminal_size((100, 20)).columns - 1)


def _clear_status() -> None:
    if _status_line and _TTY:
        sys.stdout.write("\r" + " " * _width() + "\r")


def _draw_status() -> None:
    if not (_status_line and _TTY):
        return
    w = _width()
    line = _status_line
    if visible_len(line) > w:   # too long: drop colors and cut to fit
        line = _ANSI_RE.sub("", line)[:w]
    sys.stdout.write("\r" + line + " " * max(0, w - visible_len(line)))
    sys.stdout.flush()


def log(msg: str = "", err: bool = False) -> None:
    """Print a log line above the progress bar, then redraw the bar."""
    with _print_lock:
        _clear_status()
        sys.stdout.flush()
        print(msg, file=sys.stderr if err else sys.stdout, flush=True)
        _draw_status()


def status(msg: str) -> None:
    """Set the bottom status line (empty string clears it). Only shown in a terminal."""
    global _status_line
    with _print_lock:
        _clear_status()
        _status_line = msg
        _draw_status()


def warn(msg: str) -> str:
    return f"  {yellow('!')} {yellow(msg)}"


def fmt_eta(seconds: Optional[float]) -> str:
    if seconds is None or seconds < 0 or seconds > 99 * 3600:
        return "--:--"
    s = int(seconds)
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


class ProgressBar:
    """Overall progress bar for one profile, redrawn 8 times per second.

    Percentage and ETA are based on finished posts (so they do not jump around);
    speed is a 5-second moving average of downloaded bytes.
    """

    SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, total: int):
        self.total = max(1, total)
        self.ok = self.failed = 0
        self.base_bytes = 0
        self.active: list = []
        self.samples: list[tuple[float, int]] = []
        self.started = time.monotonic()
        self.frame = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join()
        status("")

    def post_done(self, ok: bool) -> None:
        with self._lock:
            if ok:
                self.ok += 1
            else:
                self.failed += 1

    def downloading(self, tasks) -> None:
        with self._lock:
            self.active = tasks

    def downloaded(self) -> None:
        with self._lock:
            self.base_bytes += sum(d.stat().st_size for _, d, _ in self.active if d.exists())
            self.active = []

    def _bytes(self) -> int:
        with self._lock:
            active = list(self.active)
            base = self.base_bytes
        total = base
        for _, d, _ in active:
            try:
                total += d.stat().st_size
            except OSError:
                pass
        return total

    def _loop(self) -> None:
        while not self._stop.wait(0.125):
            self.render()

    @staticmethod
    def _bar(frac: float, width: int) -> str:
        if _COLOR:
            # "━━━━━╸━━━━" style: the done part is colored, with half-cell precision.
            halves = int(frac * width * 2)
            full, half = divmod(halves, 2)
            done = "━" * full + ("╸" if half else "")
            rest = "━" * (width - full - (1 if half else 0))
            color = green if frac >= 1 else cyan
            return color(done) + gray(rest)
        # No colors: use solid / shaded blocks so done and remaining still look different.
        eighths = int(frac * width * 8)
        full, part = divmod(eighths, 8)
        partial = " ▏▎▍▌▋▊▉"[part] if part else ""
        return "█" * full + partial + "░" * (width - full - (1 if partial else 0))

    def render(self) -> None:
        now = time.monotonic()
        got = self._bytes()
        self.samples = [s for s in self.samples if now - s[0] <= 5] + [(now, got)]
        t0, b0 = self.samples[0]
        speed = (got - b0) / (now - t0) if now - t0 >= 0.5 else 0.0
        finished = self.ok + self.failed
        frac = min(1.0, finished / self.total)
        elapsed = now - self.started
        eta = elapsed / finished * (self.total - finished) if finished else None
        self.frame = (self.frame + 1) % len(self.SPINNER)
        digits = len(str(self.total))
        sep = gray(" │ ")

        spinner = cyan(self.SPINNER[self.frame]) if _COLOR else ""
        parts = [
            bold(f"{frac * 100:5.1f}%"),
            f"{finished:>{digits}}/{self.total} {t('posts', 'bài')}",
            f"{fmt_bytes(got):>8}",
            cyan(f"{fmt_bytes(speed) + '/s':>10}"),
            f"ETA {yellow(f'{fmt_eta(eta):>7}')}",
        ]
        # The failure column is always shown with a fixed width so the bar does not shrink when failures start.
        fails = f"✗ {self.failed:<{digits}}"
        parts.append(red(fails) if self.failed else gray(fails))
        tail = sep.join(parts)
        prefix = f"  {spinner} " if spinner else "  "
        bar_w = max(10, min(40, _width() - visible_len(prefix) - visible_len(tail) - 3))
        status(f"{prefix}{self._bar(frac, bar_w)}  {tail}")


def fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}GB"


def fmt_int(v) -> str:
    try:
        return f"{int(v):,}".replace(",", ".")
    except (TypeError, ValueError):
        return "?"
