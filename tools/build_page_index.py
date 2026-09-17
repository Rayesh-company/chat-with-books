#!/usr/bin/env python3
"""Build one Book's per-page text index (books/<dataset>.pages.json)
from Cognee's extracted text layer (text_<hash>.txt, the file the
ingest pipeline left in /cognee-storage/data/).

The extraction stamps every PDF page it saw with a standalone
``Page N:`` line — the same markers the Evidence locators' page
suffixes come from (enable_farsi_evidence.py). Splitting on those
markers yields a {page number: page text} map: the provenance bridge
between a citation's ``pages A-B`` and the Book's actual pages, and
the verification surface for the reader's click-to-highlight matches.
Pages with no extractable text (image plates) simply have no entry.

Run once per Book at provisioning; the JSON is committed. No PDF
library — the server stack stays stdlib-only.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# The extraction's page stamp: its own line, nothing else on it. The
# fullwidth colon rides along (CJK PDFs produce it) — the Evidence
# patch's marker regex (enable_farsi_evidence.py) allows the same two.
PAGE_MARKER = re.compile(r"^Page\s+(\d+)\s*[:：]\s*$", re.MULTILINE)


def split_pages(text: str) -> dict[int, str]:
    """Split an extracted text layer into {page number: page text}.

    Each page's text is everything between its ``Page N:`` stamp line
    and the next stamp, stamp line dropped, trailing blank lines
    trimmed. A marker seen twice keeps the first reading (never
    happens in a real extraction, but a merge artifact should not
    silently double a page's text).
    """
    pages: dict[int, str] = {}
    matches = list(PAGE_MARKER.finditer(text))
    for i, match in enumerate(matches):
        page = int(match.group(1))
        if page in pages:
            continue
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        pages[page] = text[start:end].strip("\n")
    return pages


def build_page_index(source: Path, destination: Path) -> int:
    """Write one Book's pages index JSON; return the page count."""
    pages = split_pages(source.read_text(encoding="utf-8"))
    payload = {
        "pages": {str(page): text for page, text in sorted(pages.items())},
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    return len(pages)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(
            "usage: python tools/build_page_index.py "
            "<text_<hash>.txt> <dataset>",
            file=sys.stderr,
        )
        return 2
    source, dataset = Path(argv[1]), argv[2]
    destination = REPO_ROOT / "books" / f"{dataset}.pages.json"
    count = build_page_index(source, destination)
    print(f"{destination} — {count} pages with text")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
