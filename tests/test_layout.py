"""Layout guards (docs/adr/0002): the core is site-neutral, and helpers that tests replace are called
through their module, so a patch reaches every caller."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from conftest import LAUNCHER

import tiktok_dl

PACKAGE = Path(tiktok_dl.__file__).resolve().parent
CORE = sorted((PACKAGE / "core").glob("*.py"))
ALL_SOURCES = sorted(PACKAGE.rglob("*.py")) + [LAUNCHER]

# Site URLs, page JSON keys and bot-protection markers belong in sites/<site>.py.
SITE_SPECIFIC = [
    "tiktok.com", "__UNIVERSAL_DATA_FOR_REHYDRATION__", "__DEFAULT_SCOPE__", "_wafchallengeid", "waf-aiso",
    "itemStruct", "itemInfo", "userInfo", "playAddr", "PlayAddrStruct", "downloadAddr", "imagePost", "createTime",
    "uniqueId", "statusCode", "subtitleInfos", "LanguageCodeName", "webapp.", "aweme", "isECVideo",
    "10204",   # TikTok's deleted-post code
    "instagram.com", "facebook.com", "fb.watch",
    "X-IG-App-ID", "checkpoint_required", "challenge_required", "login_required", "feedback_required",
    "video_versions", "image_versions2", "taken_at", "product_type",
]

# Names tests replace with monkeypatch.setattr on their defining module (or module state they set).
# Importing one by name into another module would make a stale copy that a patch does not reach.
PATCHED = {
    "log", "status", "LANG", "_COLOR", "find_tool", "find_ffmpeg", "SCRIPT_DIR", "download_requests", "finalize",
    "run_ffmpeg", "write_failed_file", "Progress", "scrape_profile", "list_posts", "save_profile", "TikTok",
    "resolve_short_link",
}


def package_module(node: ast.ImportFrom) -> bool:
    """Is this `from X import ...` about a module of the tiktok_dl package?"""
    return node.level > 0 or (node.module or "").split(".")[0] == "tiktok_dl"


def imported_names(node: ast.ImportFrom, package: str) -> list[str]:
    """Absolute dotted names an ImportFrom in `package` may bring in: the module and module.<name>."""
    if node.level:
        parts = package.split(".")[:len(package.split(".")) - (node.level - 1)]
        base = ".".join(parts + ([node.module] if node.module else []))
    else:
        base = node.module or ""
    return [base] + [f"{base}.{alias.name}" for alias in node.names]


def is_sites_or_cli(name: str) -> bool:
    return any(name == m or name.startswith(m + ".") for m in ("tiktok_dl.sites", "tiktok_dl.cli"))


@pytest.mark.parametrize("source", CORE, ids=lambda p: p.name)
def test_core_does_not_import_sites_or_cli(source):
    tree = ast.parse(source.read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            offenders += [alias.name for alias in node.names if is_sites_or_cli(alias.name)]
        elif isinstance(node, ast.ImportFrom):
            offenders += [name for name in imported_names(node, "tiktok_dl.core") if is_sites_or_cli(name)]
    assert offenders == []


def test_import_check_resolves_relative_imports():
    def names(code):
        return imported_names(ast.parse(code).body[0], "tiktok_dl.core")

    assert any(is_sites_or_cli(n) for n in names("from ..sites import tiktok"))
    assert any(is_sites_or_cli(n) for n in names("from .. import cli"))
    assert any(is_sites_or_cli(n) for n in names("from tiktok_dl.sites.tiktok import TikTok"))
    assert not any(is_sites_or_cli(n) for n in names("from . import console"))
    assert not any(is_sites_or_cli(n) for n in names("from .. import __version__"))


@pytest.mark.parametrize("source", CORE, ids=lambda p: p.name)
def test_core_has_no_site_urls_keys_or_markers(source):
    text = source.read_text(encoding="utf-8")
    assert [word for word in SITE_SPECIFIC if word in text] == []


@pytest.mark.parametrize("source", ALL_SOURCES, ids=lambda p: p.relative_to(p.parents[1]).as_posix())
def test_patched_names_are_called_through_their_module(source):
    tree = ast.parse(source.read_text(encoding="utf-8"))
    offenders = [(node.lineno, alias.name) for node in ast.walk(tree)
                 if isinstance(node, ast.ImportFrom) and package_module(node)
                 for alias in node.names if alias.name in PATCHED]
    assert offenders == []


def test_guards_see_the_whole_package():
    names = {p.relative_to(PACKAGE).as_posix() for p in PACKAGE.rglob("*.py")}
    assert {"cli.py", "core/site.py", "core/flow.py", "sites/tiktok.py",
            "sites/instagram.py", "sites/facebook.py"} <= names
