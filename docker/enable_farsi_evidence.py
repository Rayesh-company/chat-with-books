"""Widen Cognee Evidence tokens past Latin [a-z0-9] so Farsi answers can ground;
show the Book's PDF page range and drop id provenance on each Evidence bullet;
widen the snippet window so quoted passages hold complete sentences."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ASCII_TOKEN = 'r"[a-z0-9]+"'
UNICODE_TOKEN = 'r"[^\\W_]+"'
EXPECTED_SITES = 2
SNIPPET_CAP_PRISTINE = "_SNIPPET_MAX_CHARS = 160"
# 160 chars cut passages mid-sentence, leaving the sheet's Quoted answer
# composer almost no complete sentence its verbatim guard could keep.
SNIPPET_CAP_WIDENED = "_SNIPPET_MAX_CHARS = 600"
REFERENCES_PATH = Path("/app/cognee/modules/retrieval/utils/references.py")
ORIGINAL_ENTRYPOINT = "/app/entrypoint.sh"

BULLET_ANCHOR = 'f\'{_provenance_suffix(data_id, chunk_id)}: "{_snippet(text)}"\''
BULLET_IDS_AND_PAGES = (
    'f\'{_provenance_suffix(data_id, chunk_id)}{_pages_suffix(text)}: "{_snippet(text)}"\''
)
BULLET_FINAL = 'f\'{_pages_suffix(text)}: "{_snippet(text)}"\''
HELPER_ANCHOR = "def _chunk_id(obj: Any, payload: dict) -> Optional[str]:"
PAGES_HELPER = r'''_PAGE_MARKER = re.compile(r"Page\s+(\d+)\s*[:：]", re.IGNORECASE)


def _pages_suffix(text: str) -> str:
    """Page range '(pages 263-265)' from the Book text layer's Page N: markers."""
    pages = sorted({int(match.group(1)) for match in _PAGE_MARKER.finditer(text)})
    if not pages:
        return ""
    if pages[0] == pages[-1]:
        return f" (page {pages[0]})"
    return f" (pages {pages[0]}-{pages[-1]})"


'''


def patch_references(source: str) -> str:
    ascii_count = source.count(ASCII_TOKEN)
    unicode_count = source.count(UNICODE_TOKEN)
    if ascii_count == 0 and unicode_count == EXPECTED_SITES:
        return source
    if ascii_count != EXPECTED_SITES:
        raise ValueError(
            f"expected {EXPECTED_SITES} ASCII Evidence token regexes, found {ascii_count}"
        )
    return source.replace(ASCII_TOKEN, UNICODE_TOKEN)


def patch_bullets(source: str) -> str:
    if BULLET_FINAL in source:
        return source
    if source.count(HELPER_ANCHOR) != 1:
        raise ValueError(
            f"expected 1 Evidence helper anchor, found {source.count(HELPER_ANCHOR)}"
        )
    if "def _pages_suffix" not in source:
        source = source.replace(HELPER_ANCHOR, PAGES_HELPER + HELPER_ANCHOR, 1)
    for anchor in (BULLET_IDS_AND_PAGES, BULLET_ANCHOR):
        if source.count(anchor) == 1:
            return source.replace(anchor, BULLET_FINAL, 1)
    raise ValueError("Evidence bullet anchor not found exactly once")


def patch_snippet_cap(source: str) -> str:
    if SNIPPET_CAP_WIDENED in source:
        return source
    found = source.count(SNIPPET_CAP_PRISTINE)
    if found != 1:
        raise ValueError(f"expected 1 snippet cap anchor, found {found}")
    return source.replace(SNIPPET_CAP_PRISTINE, SNIPPET_CAP_WIDENED, 1)


def main() -> None:
    source = REFERENCES_PATH.read_text(encoding="utf-8")
    patched = patch_snippet_cap(patch_bullets(patch_references(source)))
    REFERENCES_PATH.write_text(patched, encoding="utf-8")
    os.execv(ORIGINAL_ENTRYPOINT, [ORIGINAL_ENTRYPOINT, *sys.argv[1:]])


if __name__ == "__main__":
    main()
