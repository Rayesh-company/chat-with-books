"""Widen Cognee Evidence tokens past Latin [a-z0-9] so Farsi answers can ground."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ASCII_TOKEN = 'r"[a-z0-9]+"'
UNICODE_TOKEN = 'r"[^\\W_]+"'
EXPECTED_SITES = 2
REFERENCES_PATH = Path("/app/cognee/modules/retrieval/utils/references.py")
ORIGINAL_ENTRYPOINT = "/app/entrypoint.sh"


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


def apply_patch(path: Path) -> None:
    path.write_text(patch_references(path.read_text(encoding="utf-8")), encoding="utf-8")


def main() -> None:
    apply_patch(REFERENCES_PATH)
    os.execv(ORIGINAL_ENTRYPOINT, [ORIGINAL_ENTRYPOINT, *sys.argv[1:]])


if __name__ == "__main__":
    main()
