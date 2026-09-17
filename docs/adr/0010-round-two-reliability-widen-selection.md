# ADR 0010 — Round two: phase-2 reliability, the widen, citation landing, and the ask's Book selection

Date: 2026-09-15
Status: accepted
Amends: ADR-0003 (Quoted answer), ADR-0007 (Book reader); companions to ADR-0008/0009 (Research Mode)

## Context

The operator's live use surfaced five recurring frictions: (1) the Quoted
answer intermittently failed with «پاسخ استنادی آماده نشد» — the composer
had exactly ONE writer attempt, so a timeout, an empty reasoning reply, or a
paraphrasing writer the verbatim guard stripped below the swap threshold
landed `[]` with no recourse; (2) the Quoted answer sometimes showed bare
headings with no body — the guard keeps headings unconditionally but drops
the paragraphs whose quotes failed; (3) the first answer gave no way to go
wider («جست‌وجوی بیشتر») when the pool felt thin; (4) clicking a «منابع»
reference line opened the chunk's FIRST page blind — the line carries a
locator, not a passage, so the reader never even attempted the highlight,
and the locate fallback tried only the first three pages of a five-page
chunk; (5) every ask searched both Books with no way to choose.

## Decision

- **One writer repair (composer.py).** `compose_quoted_answer` retries the
  writer ONCE — same plan, no planner re-run — when the guarded result
  misses the swap threshold or the call failed; the better attempt rides,
  both failing keeps the honest fallback. The research engine's
  `compose_guarded_reply` shape, brought home.
- **Section pruning (guard.py).** `prune_empty_sections` drops a heading
  whose section kept no paragraph (a heading survives only when a kept
  paragraph follows it before the next heading or the end), and the swap
  threshold reads the PRUNED document. Deterministic code, order-preserving.
- **The widen (ui/recall_more.py, `POST /recall-more`).** ONE broaden call
  (glm-5.3-flash, thinking ON — coverage reasoning over the pool's
  digests) proposes at most TWO short Farsi facet queries for adjacent,
  not-yet-covered ground; each runs once through the dive kernel's pinned
  searcher (`HYBRID_COMPLETION`, next tier); the reply carries only the
  pool's NEW passages (guard's normalized dedupe), `{"sources": []}` when
  there is nothing new. Gated like phase 2 (`_quoted_phone`) — it belongs
  to a chat that already started and never counts one. The sheet merges
  the fresh passages into its pool, re-picks the Quote selection, and
  re-runs phase 2 over the merged pool.
- **Citation landing (index.html).** Quote spans register their passage
  under the locator (`doc|first-last`); a reference-line click looks the
  passage up there and locates like any quote. The labeled-range fallback
  now tries the WHOLE range (capped at nine rendered pages) instead of the
  first three; index-narrowed candidates keep the three-try slice.
- **The ask's Book selection (validated server-side).** Two pill toggles
  above the question (both on; at least one must stay on). The recall
  proxy stops forwarding the body blind — the query is required and
  `datasets` is the intersection with `BOOK_DATASETS` (the resolved list,
  in the set's order; missing/malformed/empty means the whole set). The
  selection rides into the research session (`state["datasets"]`, the
  resolved list only) and every gather, conversational recall, mapping
  recall, and widen searches just those Books.

## Considered options

- **Retry the whole planner+writer pipeline** — rejected: the planner's
  ~84 s is the common case's cost; only the writer attempt is the flaky
  unit.
- **Ask the writer to self-report dropped sections** — rejected: the
  guard's verdict is code's, not the model's; pruning after the guard is
  deterministic and testable.
- **Let the browser name datasets freely against Cognee** — rejected: the
  recorded rule pins upstream shapes server-side; the client chooses
  WHICH Books, never names anything outside the Book set.
- **Server-side rendering of the widen's merged pool** — rejected: the
  pool already lives client-side for the ask; merging and re-running the
  existing picker/phase-2 flow reuses the exact ask path with no new
  session state.

## Consequences

- Phase 2's worst-case latency grows by one writer attempt (~17–240 s)
  ONLY in the case that previously failed outright.
- The widen costs one reasoning call plus at most two searches per click;
  it is repeatable but each click is bounded, and it never mutates the
  research state.
- A quote on any page of a multi-page chunk now lands on its own page
  with the highlight painted (or the recorded honest note when the text
  layer cannot match).
- The Book selection is enforced at four points (recall proxy, recall-more,
  research session, dive searchers) — the UI is a convenience, the server
  is the contract.
