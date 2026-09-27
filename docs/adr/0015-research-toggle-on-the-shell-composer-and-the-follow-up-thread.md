# ADR 0015 — The research toggle on the shell composer, one door, and the follow-up thread

Date: 2026-09-27
Status: accepted
Amends: ADR-0014 (the research section's arm-after-the-answer entry), ADR-0008 (the operator-start rule's entry point)
Context for: the 2026-09-27 operator feedback round (four user-reported issues)

## Context

Four user reports came back from the people using the deployed shell:

1. **The single-use answer.** After a reload or a session switch and back, the ask's first answer was gone — only the quote sentences and page labels remained. Root cause: the two phase endpoints settle the same `ask_key` from two concurrent handlers (`/quote-selection`'s snapshot, `/quoted-answer`'s article) and `settle_ask`'s UPDATE was last-write-wins. When the slow picker's snapshot settled after the article, it overwrote the answer body; when phase 2 failed, its empty-blocks settle erased the row entirely.
2. **Research was arm-after-the-answer.** The research section appeared at the bottom of the thread only after an answer landed, with a second composer inside it. The operators' mental model is the reverse: chat is the default (ask, answer, follow-ups), and research is a mode you arm from the start — a toggle on the composer, ON meaning the typed question goes straight into the research process.
3. **No scroll freedom while generating.** Every programmatic scroll (`scrollThreadEnd` on phase landings, `selectPhase`'s `scrollIntoView`, the research poll's `scrollResearch` every 2 seconds) was an unconditional smooth yank with no near-bottom check — the page dragged the reader back down during the ~100 s phase-2 wait.
4. **The composer kept the sent text.** The submit handler removed the `composerDraft` storage key but never cleared the textarea — the message stayed in the box as a draft after sending.

The same round asked for plain multi-turn chat: a second, third, fourth question that relates to the first. `/api/v1/recall` is stateless (no history field, Cognee contract untouched) and phase 2 saw only the current ask.

## Decision

- **The store judges shapes, not arrival.** `settle_ask`'s UPDATE is upgrade-only (`_settled_over`): a `selection_snapshot` never overwrites a settled article, an empty-blocks write never erases content, and the article always upgrades the snapshot. `_quoted_answer` additionally refuses to settle empty blocks at the source. Equal shapes keep latest-wins.
- **One door, two modes (ADR-0014's stage-4 consequence, now landed).** The shell composer is the only composer. A toggle («حالت پژوهش», the approved glossary row) arms it: default OFF is the ask exactly as before; ON routes the typed question to `POST /research/message` — the founding message and goal of a new research session, the next message of a live one. The research section appears when the toggle arms it (or a refresh reconnects a live conversation), never because an answer landed; `renderFinal` only seeds the question and pool. The section's own composer is retired (element and wiring kept, hidden).
- **The research gate is the Balance, and only the Balance.** The old minimum-of-one-chat precondition existed because research was seeded from a prior ask's pool; the toggle starts conversations from the typed question alone (the pool seed is optional — the engine's own gathers supply evidence). `/research/message` and `/research/decide` answer to `resolve_identity` + `_balance_gate`; research turns record no chats and never burn the daily ask quota.
- **The follow-up thread.** The client sends the sitting's id in the recall body; the server pops it (it never reaches Cognee), reads the sitting's recent turns from the Session store — the same rows the resume renders — and, when the question is short (≤ 8 words), spends ONE fast glm-5.3-flash call (15 s timeout, 512 tokens) rewriting it into a self-contained query; every failure answers the raw question, and the displayed question is always the user's own words. The rewrite is metered like every composer call. Phase 2's writer gains the same tail as framing (`conversation_tail`, text parts only — quotes are the passages' job).
- **Scroll freedom.** Every async landing scroll (quote selection, phase-2 mark, research settle, `scrollResearch` on every poll tick) is gated on a near-bottom check — the reader may scroll anywhere while the pipeline generates, and following resumes when they return. The submit's own jump stays unconditional but instant.
- **The box clears on send.** Capture, then clear (`value = ""`, autosize, char note, draft key) before either branch; the deliberate refills (starter chips, «پرسیدن دوباره») are not sends and keep their text.
- **Restore fidelity.** The resume renders the stored citations as the same collapsed Evidence pool the live ask showed («استناد», existing strings only) beneath the settled article.

## Consequences

- The transient race is closed at both layers; the store's precedence also protects future callers that settle the same ask.
- A research conversation can be the sitting's (and the day's) first act; its spend is bounded by the Balance alone, which is the design's own prepaid stop (T23).
- Research conversations are still not rows in the Session store (the research store + same-tab reconnect remains their memory); a cross-tab resume of a research-only sitting is unchanged, known behavior.
- One NEW draft string (the section's empty note) joined the shell's draft table and CONTEXT.md's draft roster for PM sign-off; every other visible string reuses approved roster text.
- The rewriter adds one upstream call only on short follow-ups over an existing sitting — a better retrieval hint, never a gate.
