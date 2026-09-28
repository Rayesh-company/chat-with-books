# ADR 0014 — The ask is retrieval-only: the first answer cannot be a conclusion, and the chat survives a reload

Date: 2026-09-23
Status: accepted
Amends: the first-answer chapter of the README, ADR-0006's phase-1 record, and ADR-0011's citation-first fallback ladder

## Context

The operator's 2026-09-23 smoke report, against the live sheet:

1. **The first answer was a conclusion, not retrieval.** Sometimes the sheet worked as designed — the Quote selection (verbatim Book sentences) rendered as پاسخ اول. Sometimes the first message came back as a full conclusive essay with a «جمع‌بندی» — and took ~90 seconds to arrive. The variability sat in the ask path's design: the sheet streamed a Cognee `HYBRID_COMPLETION` completion (an LLM answer over the retrieved pool, its length and shape up to the model's drift — the recorded prompt already says "Be as brief as possible" and the model ignored it), used its `Evidence:` block as the citation pool, and only then ran the picker. When the picker missed its floor — or the stream carried no Evidence block — the sheet's recorded fallback **rendered that streamed prose as the first answer**. The fallback ladder, not the picker, was the flakiness.
2. **A reload threw the chat away.** Phase 1 and phase 2 outputs lived only in the browser's page memory; the research transcript restored (its store already existed, ADR-0008) but everything else vanished — the operator reloaded into an outline with no body.
3. **Research Mode needed an explicit switch**, not a separate always-armed tab: an on/off control in the ask box, default off.

A parallel probe closed the escape hatches:

- A bare `CHUNKS` search (the fast, completion-free retrieval) pulled index pages and back-matter on the operator's own question — the hybrid entity/graph lanes are what make the pool good; raw vector chunks are not citable at product quality.
- The streamed completion cannot be made reliably short (the prompt already pins brevity; the model drifted anyway) and its Evidence block rides only at `final` — so the pool can never arrive before the prose finishes.
- Cognee's search API exposes exactly the seam needed: `only_context` on a `HYBRID_COMPLETION` call returns the hybrid retriever's own retrieval lanes with **no LLM completion**. Measured live: ~4 s against the streamed completion's 60–90 s, same hybrid passage quality.
- Cognee's built-in session manager (`cognee.infrastructure.session`) is a cache-backed completion-layer assistant for its own retrievers — Redis/FsCache history that no-ops when caching is off — not a durable, phone-keyed, phone-authorized store. The sheet keeps owning its persistence.

## Decision

- **The ask is retrieval-only.** The sheet POSTs `/ask`; `ui/ask.py` runs ONE `HYBRID_COMPLETION` search per selected Book with `only_context: true` on the main service, parses the context doc's `## Relevant passages` section (the entity/fact sections are graph labels, never citable), and builds the `{"reference", "passage"}` pool with Book identity and `Page N:` markers into the reference. No LLM completion runs anywhere on the first message — a conclusive essay is unreachable by construction, and the pool cannot arrive without its citations.
- **The streamed-prose fallback ladder is demolished.** The Quote selection is the ONLY first answer. A picker miss lands its recorded honest note with the pool's citations staying visible below; an empty pool lands «استنادی از کتاب‌ها پیدا نشد». The Evidence pool is still never rendered as a fallback. The sheet's markdown renderer, the SSE reader, and the TTFT chip retire with the stream. `renderQuoteSelection` and `renderQuotedAnswer` carry the ask's `chat_id` instead of a draft answer; the phase-2 writer frames from its plan (or the passages alone when the planner fails).
- **The chat survives a reload.** `ui/chat_store.py` — the research_store's pattern — persists one row per ask (question, Book selection, pool, selections, quoted blocks, truncated flag) in `SESSION_CHAT_DB` (default `chats.sqlite3`, the `session_quota` volume beside the other stores). Every phase write is phone-guarded by the row's owner. On load the sheet GETs `/chat/latest` — the Account's newest ask — and re-renders phases 1 and 2 and the research seed. No LLM runs on restore; nothing re-bills.
- **Research Mode is a switch in the ask box, default off.** Off: the ask runs phases 1–2 exactly as recorded. On: the ask itself opens the research conversation (the creating call rides the ask's question and pool) and phase 2 is skipped — the research turns write their own guarded replies. The choice persists in localStorage; a fresh visitor's default is off. Without the switch on, nothing auto-starts research (ADR-0008's operator-start rule, kept).

## Considered options

- **Keep the stream, paint the prose only on picker failure** (the recorded fallback) — rejected: it is precisely the flakiness being fixed; the prose's length, style, and arrival time are the model's, not the sheet's.
- **Keep the stream for phase-2 framing only, never display it** — rejected: it keeps a 60–90 s LLM completion (and its AvalAI tokens) on every ask to feed a framing block the planner-pass design had already demoted to best-effort context, and keeps the pool hostage to `final`'s arrival.
- **Swap the ask to a bare `CHUNKS` search** — rejected on measured quality (index pages, ads, off-topic lecture chunks): completion-free but not the hybrid pool the product cites.
- **Adopt cognee's session manager for chat history** — rejected: it is a completion-layer cache for cognee's own retrievers, not a durable application store; the sheet's phases (picker, writer) never pass through it. Our own SQLite store carries the authorization, phone-keying, and reload semantics the product needs.
- **Restore re-running phase 2 when its blocks are missing** — rejected: a reload must never spend the Account's Toman without an explicit ask.

## Consequences

- First-answer latency drops from the streamed completion's 60–90 s to retrieval (~4–25 s) plus the one picker call; and the first answer is verbatim Book sentences every single time.
- The `Evidence:` contract moves from the completion's appended block to the context doc's passages section — same Book identity, same page markers, parsed server-side by `ui/ask.py` (locked by the recorded live fixture `tests/fixtures/recall-hybrid-only-context.json`).
- `/api/v1/recall` stays proxied for the operator's probes; the sheet never calls it.
- The backup/restore drill carries the new store (`chats.sqlite3`) beside `usage.sqlite3` and `research.sqlite3`.
- The README's first-answer chapter, the sheet tests, and this ADR re-pin the contract; the prose-fallback sentences retire.
- A new ask still aborts the phone's in-flight research turn and closes its session (the `/ask` POST carries the abort the recall POST used to).
