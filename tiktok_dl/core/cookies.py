"""--cookies: a Netscape cookie file (cookies.txt) exported from a browser, for sites that need more than
one login cookie.

The file is read here, in memory, and never written, copied or printed: messages name the file, never a
line of it. It is parsed by hand on purpose: the standard library's MozillaCookieJar and yt-dlp's cookie
jar both put a bad line in their error text (and yt-dlp's jar writes the file back when it closes).
Each site takes only its own domain's cookies (for_domain).
"""
from __future__ import annotations

from http.cookiejar import Cookie

from .console import t
from .errors import ScrapeError

HTTP_ONLY = "#HttpOnly_"   # how exporters mark HttpOnly cookies: a comment prefix on an ordinary line


def _cookie(fields: list[str]) -> Cookie:
    domain, _subdomains, path, secure, expires, name, value = fields
    expiry = int(expires)   # ValueError: not a cookie line
    return Cookie(0, name, value, None, False, domain, bool(domain), domain.startswith("."), path or "/", True,
                  secure.upper() == "TRUE", expiry or None, not expiry, None, None, {})


def load(path: str) -> list[Cookie]:
    """The cookies in a Netscape cookie file, in file order. Lines that are not cookies are skipped; a file
    with no cookie line among other lines is not a Netscape file. Raises ScrapeError (naming the file only)."""
    try:
        with open(path, encoding="utf-8-sig") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError) as e:
        raise ScrapeError(t(f"cannot read the --cookies file {path} ({type(e).__name__})",
                            f"không đọc được file --cookies {path} ({type(e).__name__})")) from None
    jar, other = [], 0
    for line in text.splitlines():
        if line.startswith(HTTP_ONLY):
            line = line[len(HTTP_ONLY):]
        elif line.startswith("#") or not line.strip():
            continue
        try:
            jar.append(_cookie(line.split("\t")))
        except ValueError:
            other += 1
    if other and not jar:
        raise ScrapeError(t(f"the --cookies file is not a Netscape cookie file (cookies.txt): {path}. Export it "
                            "again with a browser extension that saves cookies in the Netscape format.",
                            f"file --cookies không phải file cookie dạng Netscape (cookies.txt): {path}. Hãy xuất "
                            "lại bằng tiện ích trình duyệt lưu cookie theo định dạng Netscape."))
    return jar


def for_domain(jar: list[Cookie], domain: str) -> list[Cookie]:
    """The cookies of `domain` and its subdomains (never of a look-alike such as notfacebook.com)."""
    return [c for c in jar if c.domain.lstrip(".").lower() == domain or c.domain.lower().endswith("." + domain)]
