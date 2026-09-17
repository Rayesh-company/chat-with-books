"""The true-page resolver (ADR-0011): a kept quote's labels name the
passage's ACTUAL page, not the locator's estimate. The Evidence
locators' ``pages A-B`` labels drift against the Book's real pagination
(recorded live 2026-09-16: every sampled passage sat at label-1), so
``guard.py``'s regex-only labels put tooltips and the reader's landing
one page off at the source. This module loads the Book's per-page text
index (``books/<dataset>.pages.json``, built by
tools/build_page_index.py) lazily, normalizes each touched page once
with the guard's own letter stream, and finds the page a passage
actually starts on — the reader's recorded algorithm, server-side.

``install()`` wires the resolver into ui.guard's labels seam; the
default (no install) keeps the estimate behavior, so the pure-text
tests are untouched."""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

try:
    from ui import guard as guard_module
    from ui.guard import normalize_for_match
except ImportError:  # the container runs serve.py as a script beside the modules
    import guard as guard_module
    from guard import normalize_for_match

# The locator shapes, the guard's twins.
_PAGE_RANGE = re.compile(r"\(pages (\d+)-(\d+)\)")
_PAGE_SINGLE = re.compile(r"\(page (\d+)\)")
_DOCUMENT = re.compile(r"\bdocument ([A-Za-z0-9._-]+)")
# The recorded drift is ±2 pages; the resolver searches the labeled
# range first, then the widened band around it.
_RESOLVE_WIDEN = 2
# A passage needle shorter than this matches too easily — no resolution
# beats a wrong one.
_MIN_NEEDLE = 12

_CACHE: dict = {}
_CACHE_LOCK = threading.Lock()


def bookshelf_dir() -> Path:
    """The Books' directory — serve.py's own rule (compose mounts the
    Books at /books; dev default: the repo's books/ beside ui/)."""
    return Path(
        os.environ.get("SESSION_BOOKS_DIR", str(Path(__file__).resolve().parent.parent / "books"))
    )


def _dataset_pages(dataset: str):
    """The dataset's {page number: normalized page text}, loaded and
    normalized once per process; None when the index is unreadable (the
    estimate behavior stands)."""
    with _CACHE_LOCK:
        if dataset in _CACHE:
            return _CACHE[dataset]
        try:
            data = json.loads(
                (bookshelf_dir() / f"{dataset}.pages.json").read_text(
                    encoding="utf-8"
                )
            )
            pages = {
                str(number): normalize_for_match(text)
                for number, text in (data.get("pages") or {}).items()
            }
        except (OSError, ValueError):
            pages = None
        _CACHE[dataset] = pages
        return pages


def reset_cache() -> None:
    """Drop the loaded indexes (tests repoint the bookshelf)."""
    with _CACHE_LOCK:
        _CACHE.clear()


def resolve_first_page(reference: str, passage: str):
    """The page the passage actually starts on, or None — the honest
    unknown. Searches the locator's labeled range first (a chunk spans
    at most a few pages), then the ±2 widened band for the recorded
    drift; the needle is the passage's normalized opening, the same
    letter-stream comparison the verbatim guard runs."""
    if not isinstance(reference, str) or not isinstance(passage, str):
        return None
    document = _DOCUMENT.search(reference)
    if not document:
        return None
    pages = _dataset_pages(document.group(1))
    if not pages:
        return None
    needle = normalize_for_match(passage)[:400]
    if len(needle) < _MIN_NEEDLE:
        return None
    range_match = _PAGE_RANGE.search(reference)
    if range_match:
        first, last = int(range_match.group(1)), int(range_match.group(2))
    else:
        single = _PAGE_SINGLE.search(reference)
        if not single:
            return None
        first = last = int(single.group(1))
    for number in range(first, last + 1):
        if needle in pages.get(str(number), ""):
            return number
    for number in range(max(1, first - _RESOLVE_WIDEN), last + _RESOLVE_WIDEN + 1):
        if needle in pages.get(str(number), ""):
            return number
    return None


def install() -> None:
    """Wire the resolver into guard's citation labels — called once at
    serve startup; without it the labels keep the estimate behavior."""
    guard_module.PAGE_RESOLVER = resolve_first_page
