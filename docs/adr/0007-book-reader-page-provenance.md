# ADR-0007: The Book reader — every quote clicks through to the Book's PDF page

Date: 2026-09-13
Status: accepted

## Context

Citations on the sheet name a Book and a page range — «صفحهٔ ۷۴۰» on hover,
«صفحات ۷۴۰ تا ۷۴۵» at paragraph end — but the Customer never sees the Book
itself. The original PDFs exist only inside Cognee's storage volume; the
session container cannot reach them, and the sheet has no PDF surface at
all. The provenance chain a Customer is promised (quote → Book → page →
exact place on the page) stops at the page number.

## Decision

**The sheet gains a Book reader (کتاب‌خوان): a split view — chat right,
Book left in RTL — where every rendered quote, Evidence line, and منابع
entry clicks through to the passage's actual PDF page with the quoted
letters highlighted.**

The pieces, in dependency order:

1. **Assets.** The two Book PDFs move into a repo-root `books/` directory
   (`.gitignore`d — customer files never enter git; provisioned from the
   Cognee storage export). `tools/build_page_index.py` (stdlib) splits the
   extracted `text_<hash>.txt` on its standalone `Page N:` stamp lines into
   `books/<dataset>.pages.json` — one entry per PDF page. The JSON ships;
   the PDFs do not.

2. **Serving.** `serve.py` gains two GET routes: `/books/<dataset>.pdf` and
   `/books/<dataset>.pages.json`. The dataset must be one of the Book set
   (`BOOK_DATASETS`) — the allowlist is the path-traversal guard. compose
   bind-mounts `./books:/books:ro` into the session container
   (`SESSION_BOOKS_DIR` overrides). The server stays **stdlib-only**: no
   PDF parsing happens in Python at request time.

3. **Rendering.** pdf.js (Apache-2.0) is vendored into `ui/vendor/` —
   no CDN, works on a LAN. Pages render lazily (canvas + text layer,
   serialized queue), the reader tracks the current page, and far pages
   drop their canvases to bound memory. On a hidden browser tab, pdf.js's
   rAF-driven painting pauses and resumes with visibility — accepted,
   standard pdf.js behavior.

4. **Highlighting.** A quote click walks the provenance chain:
   - The Evidence locator's `document <dataset> (pages A-B)` names the
     Book and the page range (the machine twin of guard.py's regexes,
     client-side).
   - **Stage 1 — which page.** The quote's normalized letter stream is
     searched in `<dataset>.pages.json` across the range. The extraction
     the index came from is the one the passages came from, so this match
     is as reliable as the verbatim guard itself.
   - **Stage 2 — where on the page.** The rendered page's pdf.js text
     layer is rebuilt into reading order from item geometry (lines grouped
     by baseline, top-down; items within a line right-to-left) — pdf.js's
     raw item order scatters RTL pages and never matches contiguously.
     The quote's normalized stream is then located in that stream; if a
     footnote separator or running head splits it, the longest common
     contiguous run (≥ min(80, 60 %) of the quote) is painted instead.
     Normalization is `guard.py`'s `normalize_for_match`, ported to JS —
     the same letter-stream contract the verbatim guard enforces. The two
     extractors disagree on the lam-alef ligature's decomposition order
     (لا vs ال), so the normalized streams canonicalize that pair.
   - **Fallbacks.** No match → stay on the first page of the range with a
     Farsi note; the page-level citation holds either way. A quote
     straddling a page break gets a split match (head/tail across the
     pair).

### The rendering pipeline: server-side PDFium rasters (2026-09-13, second addendum)

The canvas experiments above were superseded by inspection of the actual
PDFs: the body text uses **embedded** subset CID TrueType fonts
(IRANSharp-Light/Bold, Identity-H, no conventional TrueType `cmap`) whose
content streams write isolated codepoints interleaved with backspace
controls. No browser-font path — substitution, aliasing, or shims — can
faithfully reproduce what a real PDF engine draws, and pdf.js's canvas
cannot re-join those glyphs.

The visual pipeline is therefore **server-side rasterization**:

- `pypdfium2` (PDFium binary, pinned) renders each requested page at a
  clamped, bucketed pixel width — `GET
  /books/<dataset>/page/<n>.png?w=` — cached on disk under
  `SESSION_RENDER_CACHE` (default: the system temp), serialized behind a
  lock (PDFium is not provably thread-safe across documents), answered
  `immutable` for a week.
- The reader displays the raster as the page's pixel truth. pdf.js stays
  **only for parsing**: `getTextContent()` feeds the reading-order item
  parts (the quote matcher's stream) and the transparent `TextLayer`
  gives selection.
- **Simple highlighting:** a matched quote range paints clay
  rectangles over the raster — one band per intersecting text item,
  sliced horizontally by the covered raw-character fraction,
  percent-positioned against the page box, so zoom and resize keep the
  bands glued to the glyphs.
- The fillText probe, shattered-text overlay, and font substitution
  machinery were removed with the canvas path; pdf.js never draws a Book
  page anymore. Zoom re-requests rasters at a larger width bucket; far
  pages evict their images on scroll.

The separation rule stands: **the raster is the visual truth; extracted
text is for selection, search, matching, and the knowledge pipeline —
never for drawing.**

## Consequences

- No re-ingest, no embedding change, no Cognee change: the provenance
  already in every Evidence locator is finally reachable, verbatim, on the
  Book's own page.
- Position provenance is **computed at view time, not stored** — chunks
  stay page-grained, and the guard's normalization guarantee is what makes
  the computed position trustworthy. Finer-than-page chunking stays out of
  scope.
- The customer PDFs ride a bind mount and stay out of git; a fresh clone
  provisions `books/` from the Cognee storage export (two `cp` commands
  and two tool invocations).
- The session image grows by the vendored pdf.js (~1.7 MB) and requires a
  rebuild (`docker compose --profile session build session`).
