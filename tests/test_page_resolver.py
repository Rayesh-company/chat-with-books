"""The true-page resolver contract locks (ADR-0011): the passage's
actual page beats the locator's drifted estimate in every citation
label, resolution failure keeps the estimate, and the pure-text guard
stays pure when no resolver is installed."""

import json
import sys
from pathlib import Path

from tests.conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT))

from ui import guard, page_resolver, serve  # noqa: E402

PASSAGE = "سخن در این است؛ این جمله آغازین همان صفحهٔ واقعی است و جایی دیگری نیست."
# The recorded drift: the locator says 482-484, the passage sits on
# page 481.
REFERENCE = "chunk 482 of document tarhe-kolli (pages 482-484)"


def make_bookshelf(tmp_path, dataset="tarhe-kolli"):
    """A tiny per-page index: the passage on page 481, noise elsewhere."""
    shelf = tmp_path / "books"
    shelf.mkdir()
    (shelf / f"{dataset}.pages.json").write_text(
        json.dumps(
            {
                "pages": {
                    "480": "صفحهٔ چهارصد و هشتاد.",
                    "481": "متنِ پیشین. " + PASSAGE,
                    "482": "متنِ دیگری که شبیه نیست.",
                    "483": "پایان بخش.",
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return shelf


def with_bookshelf(tmp_path, fn):
    """Run fn with the fixture bookshelf installed and the resolver
    wired; restores the seam and cache after."""
    import os

    original_env = os.environ.get("SESSION_BOOKS_DIR")
    original_resolver = guard.PAGE_RESOLVER
    os.environ["SESSION_BOOKS_DIR"] = str(make_bookshelf(tmp_path))
    page_resolver.reset_cache()
    page_resolver.install()
    try:
        return fn()
    finally:
        guard.PAGE_RESOLVER = original_resolver
        page_resolver.reset_cache()
        if original_env is None:
            os.environ.pop("SESSION_BOOKS_DIR", None)
        else:
            os.environ["SESSION_BOOKS_DIR"] = original_env


def test_resolve_first_page_finds_the_true_page_behind_the_label(tmp_path):
    assert with_bookshelf(
        tmp_path, lambda: page_resolver.resolve_first_page(REFERENCE, PASSAGE)
    ) == 481


def test_resolve_first_page_widens_past_the_labeled_range(tmp_path):
    # A passage two pages before its label — inside the recorded ±2
    # drift band.
    def run():
        return page_resolver.resolve_first_page(
            "chunk 1 of document tarhe-kolli (pages 482-484)", PASSAGE
        )

    assert with_bookshelf(tmp_path, run) == 481


def test_resolve_first_page_is_none_when_the_passage_is_nowhere(tmp_path):
    def run():
        return page_resolver.resolve_first_page(
            REFERENCE, "جمله‌ای که در هیچ صفحه‌ای نیست."
        )

    assert with_bookshelf(tmp_path, run) is None


def test_labels_carry_the_true_page_when_resolved(tmp_path):
    def run():
        labels = guard._citation_labels(REFERENCE, PASSAGE)
        assert labels["first_page_label"] == "صفحه 481"
        assert labels["pages_label"] == "صفحات 481 تا 484"
        # The paragraph guard attaches the same honest labels.
        sources = [{"reference": REFERENCE, "passage": PASSAGE}]
        blocks = guard.guard_blocks(
            [
                {
                    "type": "heading",
                    "text": "عنوان",
                },
                {
                    "type": "paragraph",
                    "parts": [
                        {"text": "بر پایهٔ متن:"},
                        {"quote": PASSAGE, "source": 0},
                    ],
                },
                {
                    "type": "paragraph",
                    "parts": [
                        {"text": "باز هم:"},
                        {"quote": PASSAGE, "source": 0},
                    ],
                },
            ],
            sources,
        )
        part = blocks[1]["parts"][1]
        assert part["first_page_label"] == "صفحه 481"
        assert part["pages_label"] == "صفحات 481 تا 484"

    with_bookshelf(tmp_path, run)


def test_the_estimate_stands_without_a_resolver():
    # The pure-text default: no resolver installed, the labels read the
    # locator exactly as before.
    original = guard.PAGE_RESOLVER
    guard.PAGE_RESOLVER = None
    try:
        labels = guard._citation_labels(REFERENCE, PASSAGE)
    finally:
        guard.PAGE_RESOLVER = original
    assert labels["first_page_label"] == "صفحه 482"
    assert labels["pages_label"] == "صفحات 482 تا 484"


def test_a_failing_resolver_never_fails_a_reply(tmp_path):
    def run():
        guard.PAGE_RESOLVER = lambda reference, passage: 1 / 0
        try:
            labels = guard._citation_labels(REFERENCE, PASSAGE)
        finally:
            page_resolver.install()
        # The estimate stands — a labeling lookup never breaks the reply.
        assert labels["first_page_label"] == "صفحه 482"

    with_bookshelf(tmp_path, run)
