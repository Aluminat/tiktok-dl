"""Ctrl+C handling shared by every thread."""
from __future__ import annotations

import threading
import time

# Set on Ctrl+C. Worker threads check it so queued work is dropped instead of finished.
STOP = threading.Event()


def wait_result(fut):
    """fut.result(), but Ctrl+C can interrupt the wait (a blocking result() may not, on Windows)."""
    while not fut.done():
        time.sleep(0.1)
    return fut.result()
