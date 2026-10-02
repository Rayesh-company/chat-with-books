# ADR 0017 — The Book reader's cold path: warm caches, lazy bytes, an anchor-ordered queue

Date: 2026-09-27
Status: accepted
Amends: ADR-0007 (the Book reader and its rendering addendum — the visual pipeline is untouched)
Context for: the 2026-09-27 operator feedback round (the reader's slow load)

## Context

The reader's visual pipeline was already the right shape — the server-side PDFium raster is the pixel truth, pdf.js parses, the transparent text layer selects — and `openBook` already jumped to the raster of the target page before pdf.js finished. The slowness the operators reported lived in everything *around* that pipeline:

1. **Every raster miss re-parsed the whole Book.** `_render_book_page` opened a fresh `PdfDocument` per miss — a full parse of the 16 MB / 807-page file — inside the global `RENDER_LOCK`, so every waiting client waited with it.
2. **The render cache lived in `/tmp`.** A container rebuild (every deploy) wiped it; the first visitor after each deploy re-rendered from zero.
3. **The Book refetched whole, every open.** The PDF answers carried no validator and the pages index rode `no-store` — reopening a Book paid the 16 MB and the 1.95 MB again.
4. **pdf.js pulled the whole file in the background.** With auto-fetch on, the browser streamed the complete Book for a text layer only the visible pages use.
5. **A far jump queued FIFO.** The render chain was first-come: the jump target (page 456) waited behind every neighbor the IntersectionObserver had already queued, each a full round-trip.

The constraint the operator set: no change to the Persian rendering — the subset-font rasters, the cMaps, the text layer — nothing visual may move.

## Decision

- **The open document is reused.** An LRU of parsed `PdfDocument` handles (capacity 2, `PDF_DOCS_CACHE`) lives under `RENDER_LOCK` — the reuse never widens PDFium's thread exposure; the books are read-only mounts, so a handle cannot go stale. Evicted handles close.
- **The raster cache survives deploys.** `RENDER_CACHE_DIR`'s default moved from `/tmp` to a `render-cache/` beside the books dir; compose bind-mounts `./render-cache:/render-cache` (the mount point the default resolves to), and the deploy keeps the old tree's cache across the swap. A ceiling (`SESSION_RENDER_CACHE_CAP`, 3 GB) prunes oldest-first, on the write path only when the cap is crossed.
- **Answers carry an ETag, and 304 is the re-open path.** `/books/<d>/book` and `.pages.json` send a strong ETag (size + mtime) with `Cache-Control: public, max-age=0, must-revalidate`; a matching `If-None-Match` answers 304 for one stat. The pages index also gzips when the client accepts (~6× on the wire), its compressed bytes cached in memory keyed by mtime.
- **pdf.js fetches only the bytes the parsed pages need.** `disableAutoFetch: true` on `getDocument` — the server's Range path (now ETag-stamped) serves the lazy page fetches; the cMaps and standard fonts stay exactly as they were.
- **The queue drains nearest-anchor-first.** The FIFO chain became a scheduler: pending page numbers in a set, one worker always rendering the page nearest the anchor (the jump's `stick`, else the page in view), `ensureRendered` resolved by a per-page waiter. Serialization is unchanged — only the order moved, and the jump target now goes first.
- **A far jump paints small-first.** `gotoPage` past three pages sets a fast-paint hint: the placeholder fills from the cheap `w=480` bucket and swaps to the full-width raster on load — both `immutable`-cached, so the pre-warm costs its bytes once. The page arrives blurry in a fraction of a second instead of sharp in seconds.
- **Every render has a witness.** The raster route logs `render <dataset> p<n> w<width> hit|miss <seconds>` — the first measurement the reader has ever had.

## Consequences

- The PNG bytes are bit-for-bit what they were; the text layer, cMaps, and highlight geometry are untouched. Nothing the reader draws can change.
- The first visit still pays the cold render of a page; every visit after (same deploy or the next) pays a disk read — and the re-open path pays one 304 for the Book instead of 16 MB.
- The gzip cache holds one Book's index at a time (the files are few); a re-indexed Book re-compresses for free through the mtime key.
- The stale docstring claiming "No HTTP Range" is gone — the route has honored Range since the T10 commit that shipped it.
