"""The Book reader's server surface: the per-page text index builder
(tools/build_page_index.py) and serve.py's /books routes — the
provenance bridge a quote click walks (ADR-0007)."""

import sys
import gzip
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import urllib.request

from tests.conftest import REPO_ROOT
from tests.helpers import with_gate, stop_gate
from tools.build_page_index import build_page_index, split_pages

import ui.serve as serve


# ---- the page-index builder ----


def test_split_pages_splits_on_standalone_markers():
    text = (
        "Page 2:\n"
        "نخستین صفحه\n"
        "Page 10:\n"
        "صفحهٔ دیگر\n"
        "این خط Page 11: در میان متن است و نشانه نیست\n"
        "Page 12:\n"
    )
    pages = split_pages(text)
    assert pages == {2: "نخستین صفحه", 10: "صفحهٔ دیگر\nاین خط Page 11: در میان متن است و نشانه نیست", 12: ""}


def test_split_pages_accepts_fullwidth_colon_and_spaces():
    text = "Page  3 ：\nمتن\nPage 4:\nبعدی\n"
    assert split_pages(text) == {3: "متن", 4: "بعدی"}


def test_split_pages_keeps_first_reading_of_a_repeated_marker():
    text = "Page 1:\nاول\nPage 1:\nدوم\n"
    assert split_pages(text) == {1: "اول"}


def test_build_page_index_writes_sorted_json(tmp_path):
    source = tmp_path / "text_hash.txt"
    source.write_text("Page 7:\nهفت\nPage 2:\nدو\n", encoding="utf-8")
    destination = tmp_path / "books" / "x.pages.json"
    count = build_page_index(source, destination)
    assert count == 2
    payload = __import__("json").loads(
        destination.read_text(encoding="utf-8")
    )["pages"]
    assert list(payload) == ["2", "7"]
    assert payload["7"] == "هفت"


# ---- the /books routes (the real sheet server) ----

PDF_MAGIC = b"%PDF"


def _get_raw(base, path):
    request = urllib.request.Request(base + path)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as exc:  # noqa: F821
        return exc.code, exc.headers, exc.read()


def _conditional_raw(base, path, headers):
    """One GET with extra request headers — a 304 answer is an HTTPError
    to urllib, so the conditional reads go through here."""
    request = urllib.request.Request(base + path, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as exc:  # noqa: F821
        return exc.code, exc.headers, exc.read()


def test_books_routes_serve_pdf_and_index(tmp_path):
    books = tmp_path / "books"
    books.mkdir()
    (books / "tarhe-kolli.pdf").write_bytes(PDF_MAGIC + b"fake")
    (books / "tarhe-kolli.pages.json").write_text(
        '{"pages": {"1": "متن"}}', encoding="utf-8"
    )
    original_dir = serve.BOOKS_DIR
    serve.BOOKS_DIR = books
    base, server, originals = with_gate(tmp_path, None)
    try:
        status, headers, body = _get_raw(base, "/books/tarhe-kolli.pdf")
        assert status == 200
        assert headers["Content-Type"].startswith("application/pdf")
        assert body.startswith(PDF_MAGIC)

        status, headers, body = _get_raw(base, "/books/tarhe-kolli.pages.json")
        assert status == 200
        assert headers["Content-Type"].startswith("application/json")
        assert "متن" in body.decode("utf-8")

        # The reader's extension-less data form — no ".pdf" in the URL
        # for download managers to sniff (the recorded IDM takeover) —
        # and the explicit ".pdf" form as an attachment download.
        status, headers, body = _get_raw(base, "/books/tarhe-kolli/book")
        assert status == 200
        assert headers["Content-Type"].startswith("application/pdf")
        assert body.startswith(PDF_MAGIC)
        assert "attachment" not in (headers.get("Content-Disposition") or "")

        status, headers, _ = _get_raw(base, "/books/tarhe-kolli.pdf")
        assert status == 200
        assert "attachment" in (headers.get("Content-Disposition") or "")

        # HTTP Range request (206 Partial Content)
        req = urllib.request.Request(base + "/books/tarhe-kolli/book", headers={"Range": "bytes=0-3"})
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 206
            assert resp.headers["Content-Range"].startswith("bytes 0-3/")
            assert resp.headers["Accept-Ranges"] == "bytes"
            assert resp.read() == PDF_MAGIC
    finally:
        stop_gate(server, originals)
        serve.BOOKS_DIR = original_dir


def test_books_routes_reject_unknown_dataset_and_traversal(tmp_path):
    books = tmp_path / "books"
    books.mkdir()
    (books / "tarhe-kolli.pdf").write_bytes(PDF_MAGIC)
    original_dir = serve.BOOKS_DIR
    serve.BOOKS_DIR = books
    base, server, originals = with_gate(tmp_path, None)
    try:
        for path in (
            "/books/unknown-dataset.pdf",
            "/books/..%2F..%2F.env",
            "/books/tarhe-kolli.docx",
            "/books/70143-336.pages.json.exe",
        ):
            status, _, _ = _get_raw(base, path)
            assert status == 404, path
    finally:
        stop_gate(server, originals)
        serve.BOOKS_DIR = original_dir


def test_books_route_missing_file_is_404(tmp_path):
    books = tmp_path / "books"
    books.mkdir()
    original_dir = serve.BOOKS_DIR
    serve.BOOKS_DIR = books
    base, server, originals = with_gate(tmp_path, None)
    try:
        status, _, _ = _get_raw(base, "/books/70143-336.pdf")
        assert status == 404
    finally:
        stop_gate(server, originals)
        serve.BOOKS_DIR = original_dir


# ---- the PDFium raster route (the visual pipeline, ADR-0007) ----

import pytest

REAL_PDF = serve.BOOKS_DIR / "tarhe-kolli.pdf"


@pytest.mark.skipif(
    not REAL_PDF.exists(), reason="books/ PDFs are provisioned, not committed"
)
def test_book_page_route_renders_and_caches(tmp_path):
    cache = tmp_path / "render-cache"
    original_dir = serve.BOOKS_DIR
    original_cache = serve.RENDER_CACHE_DIR
    serve.BOOKS_DIR = serve.BOOKS_DIR
    serve.RENDER_CACHE_DIR = cache
    base, server, originals = with_gate(tmp_path, None)
    try:
        status, headers, body = _get_raw(
            base, "/books/tarhe-kolli/page/14.png?w=640"
        )
        assert status == 200
        assert headers["Content-Type"] == "image/png"
        assert body[:4] == b"\x89PNG"
        # the width bucket: 640 survives clamping unchanged
        assert (cache / "tarhe-kolli-p14-w640.png").exists()
        # second request serves from the cache — identical bytes
        status2, _, body2 = _get_raw(base, "/books/tarhe-kolli/page/14.png?w=640")
        assert status2 == 200
        assert body2 == body
        # a clamped width lands on the same bucket as its round value
        status3, _, body3 = _get_raw(base, "/books/tarhe-kolli/page/14.png?w=650")
        assert status3 == 200
        assert body3 == body
    finally:
        stop_gate(server, originals)
        serve.BOOKS_DIR = original_dir
        serve.RENDER_CACHE_DIR = original_cache


@pytest.mark.skipif(
    serve._pdfium is None, reason="pypdfium2 not installed in this env"
)
def test_book_page_route_missing_pdf_is_404_not_500(tmp_path):
    # The 09-28 regression shape: books/ holds the tracked .pages.json
    # sidecars while the gitignored PDFs never arrived (the deploy swap
    # dropped them). Every cold page render must answer a plain 404 —
    # never a 500 — and the boot check is what says the rest loudly.
    books = tmp_path / "books"
    books.mkdir()
    (books / "tarhe-kolli.pages.json").write_text("{}", encoding="utf-8")
    cache = tmp_path / "render-cache"
    cache.mkdir()
    original_dir = serve.BOOKS_DIR
    original_cache = serve.RENDER_CACHE_DIR
    serve.BOOKS_DIR = books
    serve.RENDER_CACHE_DIR = cache
    base, server, originals = with_gate(tmp_path, None)
    try:
        status, _, _ = _get_raw(base, "/books/tarhe-kolli/page/572.png?w=640")
        assert status == 404
        status2, _, _ = _get_raw(base, "/books/tarhe-kolli/book")
        assert status2 == 404
    finally:
        stop_gate(server, originals)
        serve.BOOKS_DIR = original_dir
        serve.RENDER_CACHE_DIR = original_cache


def test_book_page_route_rejects_bad_input(tmp_path):
    books = tmp_path / "books"
    books.mkdir()
    original_dir = serve.BOOKS_DIR
    serve.BOOKS_DIR = books
    base, server, originals = with_gate(tmp_path, None)
    try:
        for path in (
            "/books/tarhe-kolli/page/0.png",
            "/books/tarhe-kolli/page/-3.png",
            "/books/nope-dataset/page/1.png",
            "/books/../secret/page/1.png",
            "/books/tarhe-kolli/page/1.gif",
        ):
            status, _, _ = _get_raw(base, path)
            assert status == 404, path
    finally:
        stop_gate(server, originals)
        serve.BOOKS_DIR = original_dir


# ---- the revalidation pair (ADR-0017): ETag + 304, gzip, Range ----


def test_book_routes_carry_etag_and_answer_304(tmp_path):
    books = tmp_path / "books"
    books.mkdir()
    (books / "tarhe-kolli.pdf").write_bytes(PDF_MAGIC + b"fake")
    original_dir = serve.BOOKS_DIR
    serve.BOOKS_DIR = books
    base, server, originals = with_gate(tmp_path, None)
    try:
        status, headers, _ = _get_raw(base, "/books/tarhe-kolli/book")
        assert status == 200
        etag = headers["ETag"]
        assert etag
        assert "must-revalidate" in (headers.get("Cache-Control") or "")

        req = urllib.request.Request(
            base + "/books/tarhe-kolli/book", headers={"If-None-Match": etag}
        )
        status304, headers304, body304 = _conditional_raw(
            base, "/books/tarhe-kolli/book", {"If-None-Match": etag}
        )
        assert status304 == 304
        assert headers304["ETag"] == etag
        assert body304 == b""

        # A changed file moves the ETag — the stat pair is the validator.
        (books / "tarhe-kolli.pdf").write_bytes(PDF_MAGIC + b"fake2")
        status_new, _, _ = _conditional_raw(
            base, "/books/tarhe-kolli/book", {"If-None-Match": etag}
        )
        assert status_new == 200

        # Range requests keep their 206 (pdf.js's lazy page fetches).
        ranged = urllib.request.Request(
            base + "/books/tarhe-kolli/book", headers={"Range": "bytes=0-3"}
        )
        with urllib.request.urlopen(ranged) as resp3:
            assert resp3.status == 206
    finally:
        stop_gate(server, originals)
        serve.BOOKS_DIR = original_dir


def test_pages_index_gzips_when_accepted(tmp_path):
    books = tmp_path / "books"
    books.mkdir()
    plain = '{"pages": {"1": "متن"}}'
    (books / "tarhe-kolli.pages.json").write_text(plain, encoding="utf-8")
    original_dir = serve.BOOKS_DIR
    serve.BOOKS_DIR = books
    base, server, originals = with_gate(tmp_path, None)
    try:
        req = urllib.request.Request(
            base + "/books/tarhe-kolli.pages.json",
            headers={"Accept-Encoding": "gzip"},
        )
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            assert resp.headers["Content-Encoding"] == "gzip"
            body = gzip.decompress(resp.read()).decode("utf-8")
        assert body == plain
    finally:
        stop_gate(server, originals)
        serve.BOOKS_DIR = original_dir


@pytest.mark.skipif(
    not REAL_PDF.exists(), reason="books/ PDFs are provisioned, not committed"
)
def test_pdf_doc_handle_reuses_and_evicts(tmp_path):
    # The open-document reuse (ADR-0017): a second call for the same
    # Book returns the SAME parsed handle, the LRU's oldest entry closes
    # when the cap is crossed, and the caller's lock is what guards it.
    class FakeDoc:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    doc_a, doc_b = FakeDoc(), FakeDoc()
    original_cache = serve.PDF_DOCS_CACHE
    serve.PDF_DOCS_CACHE = {"a": doc_a, "b": doc_b}
    try:
        with serve.RENDER_LOCK:
            first = serve.SessionHandler._pdf_doc_handle(REAL_PDF)
            again = serve.SessionHandler._pdf_doc_handle(REAL_PDF)
        assert again is first
        # The cap was full with two fakes: "a" (oldest) was closed and
        # evicted to make room, "b" and the real handle remain.
        assert doc_a.closed
        assert not doc_b.closed
        assert "b" in serve.PDF_DOCS_CACHE
        assert str(REAL_PDF) in serve.PDF_DOCS_CACHE
    finally:
        for doc in serve.PDF_DOCS_CACHE.values():
            try:
                doc.close()
            except Exception:
                pass
        serve.PDF_DOCS_CACHE = original_cache


def test_render_cache_prune_keeps_cap(tmp_path):
    # The ceiling (ADR-0017): the oldest rasters go first, only when the
    # cap is crossed; under the cap the sweep touches nothing.
    original_dir = serve.RENDER_CACHE_DIR
    original_cap = serve.RENDER_CACHE_CAP
    serve.RENDER_CACHE_DIR = tmp_path
    serve.RENDER_CACHE_CAP = 100
    try:
        old = tmp_path / "tarhe-kolli-p1-w480.png"
        new = tmp_path / "tarhe-kolli-p2-w480.png"
        old.write_bytes(b"x" * 80)
        new.write_bytes(b"x" * 30)
        os.utime(old, (1_000_000_000, 1_000_000_000))
        os.utime(new, (2_000_000_000, 2_000_000_000))
        serve.SessionHandler._prune_render_cache()
        assert not old.exists()
        assert new.exists()
    finally:
        serve.RENDER_CACHE_DIR = original_dir
        serve.RENDER_CACHE_CAP = original_cap
