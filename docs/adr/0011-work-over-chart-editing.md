# ADR 0011 — Round three: work over chart editing, the citation-first first answer, true pages, and the single-Book pick

Date: 2026-09-16
Status: accepted
Amends: ADR-0008/0009 (Research Mode), ADR-0003/0006 (first answer), ADR-0007 (reader), ADR-0010 (Book selection)

## Context

The operator's live session recorded four failures with one shared theme: the platform drifted toward TALKING about research instead of DOING it.

1. **The Research Planning Loop.** After the destination and scope were set, the engine kept cycling rewrite → approve → rewrite → approve: an `active_research` gather turn parked a fresh RQ proposal and answered «پرسش پژوهش به‌روز شد» instead of gathering; the checkpoint branch blocked even the explicit «شواهد را گردآوری کن». In Wayfinder terms: stuck in map editing, never entering investigation.
2. **A generic, citation-less first answer** — when the streamed reply carried no `Evidence:` block, the sheet rendered the prose with «استناد در پاسخ نبود» and stopped; the first message's contract (find potential citations, don't reason prose) had no enforcement.
3. **Estimated pages at the source** — the citation labels read the locator's `pages A-B` by regex; the live check found every sampled passage at label−1, so tooltips and the reader landed systematically one page off before any client-side matching.
4. **Both Books searched despite the pick** — the round-two toggles defaulted both-on and persisted nothing; a refresh silently restored both.
5. **The research chat vanished on refresh** — the reconnect never re-fetched the transcript ("not re-fetched in V0.1").

## Decision

- **Chart mode vs work mode (the Wayfinder split).** Only an `research_exploration` turn may park RQ/scope proposals; `active_research`/`drafting`/`evidence_audit` turns are WORK — their classify suggestions fold concepts and questions but never become checkpoints. An explicit command (any chip: gather/synthesize/brief/audit/targeted/skip/guide) EXECUTES even while a proposal waits — the parked decision survives for the next free turn, its chips still render. A decided proposal of a kind starts a two-turn cooldown (`PROPOSAL_COOLDOWN_TURNS`) during which no new proposal of that kind parks, and a decided text can never re-propose (containment dedupe against the decisions index). The checkpoint rule itself — nothing consequential applies without the user — is untouched.
- **The phase-1 evidence fallback.** A stream without `Evidence:` triggers ONE pinned reference-on search (`POST /evidence-fallback`, the dive kernel's searcher, the ask's picked Book); the pool drives the normal Quote-selection + phase-2 path. The first answer is the Quote selection whenever the Books hold anything; nothing found keeps the honest «استنادی از کتاب‌ها پیدا نشد». The endpoint gates like the ask's family and never counts a chat.
- **True pages server-side.** `ui/page_resolver.py` indexes the Books' per-page text (normalized once per process) and `guard.py`'s label seam (`PAGE_RESOLVER`, installed at serve startup) resolves each kept quote's actual first page — labels show resolved-first…locator-last; failure keeps the estimate, never an invented page. The reader's own matching stays as the second net.
- **The single-Book pick.** The entry chips become a radio pick — exactly ONE Book, none by default, the ask disabled («اول یک کتاب انتخاب کنید») until chosen, persisted in `localStorage.selectedBook` across refreshes. The recall proxy's validation is unchanged; the research session inherits the single Book.
- **The transcript survives refresh.** `GET /research/messages?session=` (phone-matched) returns the ordered messages; the reconnect IIFE re-fetches and renders them plus the map, then polls any in-flight turn.

## Considered options

- Removing the checkpoint approval entirely (auto-apply RQ changes) — rejected: the checkpoint rule is the safety heart; the fix is WHEN proposals may park, not whether the user decides.
- Blocking gathers while a checkpoint waits (stricter than today) — rejected: it IS the recorded failure.
- Making the fallback rewrite the streamed answer — rejected: the prose stays as framing; only the citation surface changes.
- Fixing pages client-side only — rejected: tooltips and the research Brief's labels are built server-side; the source of the estimate had to move.

## Consequences

- A typical investigation now runs: pick a Book → ask → Quote selection (fallback-fed if needed) → first approve (destination/RQ) → «شواهد را گردآوری کن» gathers immediately → synthesize → Brief, with checkpoints only when the user explores.
- `+2 LLM/search calls` worst case per ask (fallback), one indexing pass per Book per process (the resolver's cache).
- The V0.1 reconnect note is retired; `/research/messages` is a read-only, phone-gated surface.
