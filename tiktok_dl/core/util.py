"""Small helpers: the browser User-Agent, file-name cleanup and lenient conversions."""
from __future__ import annotations

import re
from typing import Optional

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")


def sanitize(text: str, limit: int = 60) -> str:
    text = re.sub(r"[\r\n\t]+", " ", text or "")
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f#]', "", text)
    text = re.sub(r"\s+", " ", text).strip()[:limit]
    return text.rstrip(". ")


def to_int(v) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def uniq(seq) -> list:
    return list(dict.fromkeys(x for x in seq if x))
