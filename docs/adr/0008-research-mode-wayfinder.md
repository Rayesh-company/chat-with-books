# Research Mode — the Deep dive becomes the Wayfinder

Status: accepted, 2026-09-15. Supersedes the phase-3 half of ADR 0006 (the
Quote-selection half stands untouched).

The phase-3 Deep dive — a one-shot background job the operator fired and then
waited on, Planner → bounded retrieval → Synthesizer, zero interaction between
press and result — is replaced by **Research Mode** (حالت پژوهش): a multi-turn
chat workflow that guides the operator toward an accurate, deep understanding
of their goal and of what the Book set can and cannot establish, per the
Research Wayfinder design (the skill POC the team validated before this
implementation). Two layers run simultaneously: a **conversation layer** that
keeps normal chat freedom — every message is intent-classified first (one
`glm-5.3-flash` call, thinking on; a malformed reply degrades to the
conversational path, never crashes), and casual / concept-learning / lookup
messages are answered normally through one searcher plus one guarded writer
pass — and a **research state layer** behind the chat: a persistent state
holding the versioned research question, scope, sub-questions, an evidence
ledger, a claim ledger, gaps, decisions, and pending proposals. Research
behavior strengthens with intent; it never applies uniformly, and the
experience is never a questionnaire.

The safety architecture carries over unchanged, by name. Every reply that
states a Book fact runs the same verbatim guard as phases 2 and 3 over real
passages; the closing «منابع» references list is built server-side from the
passages actually quoted, never by the model; page labels never invent
metadata. The gather is the old dive's fan-out with its caps intact — at most
six parallel `HYBRID_COMPLETION` searchers on the second service, at most two
retrieval rounds — and a sub-question still starved after the gap round
becomes an honest **gap entry** («این بخش را نمی‌توان از همین کتاب‌ها اثبات
کرد»), never a hallucinated fill; that is a first-class result, not a failure.
Claims recorded from a synthesis reply carry **code-derived statuses** — one
distinct passage `direct_support`, more `supported_synthesis` — never
model-claimed support; the audit re-reports the ledger in pure code, no LLM.

The **checkpoint rule** is the decision's core: a research-question or scope
change proposed by the classifier never applies itself. It lands as a pending
proposal the operator resolves through `POST /research/decide` («می‌پذیرم» /
«رد می‌کنم» chips on the sheet), and an accepted question change appends a
version with its reason — v1 stays intact underneath, so the investigation
keeps its provenance (`پرسش آغازین` → refined → narrowed) rather than silently
replacing itself. A pending decision blocks the other research moves.
Routine retrieval, explanation, and comparison stay automatic — checkpoints
appear only where a wrong turn would misdirect the investigation.

The Brief — Research Mode's closing deliverable — is written **from the
research state** (question history, scope, claims with statuses, gaps), never
reconstructed from the chat transcript, and refuses to run without recorded
claims. Four fixed Farsi chip commands map to operations deterministically
(gather / synthesize / brief / audit), so a chip press needs no classification
luck; when the operator does not force one, the wayfinder picks
deterministically: checkpoint → gather (until the evidence floor or pending
sub-questions) → synthesize (the first analysis) → brief.

Operationally the dive's job shape survives per turn: `POST /research/message`
answers `202 {"turn_id", "session_id"}` immediately and the sheet polls
`/research/turn`; at most one in-flight turn per phone and three globally
(429 with the Farsi busy detail, never queued); a new ask aborts the asker's
in-flight turn cooperatively — and now also closes its session, so a stale
conversation never resumes beside a new ask; the registry is in-process
stdlib-only (dict + lock + threads). What changes is durability: **the session
itself persists in SQLite** (`ui/research.sqlite3`, the quotas.py pattern —
its own file so a patched quota DB and a patched research DB never share a
test; the session container points `SESSION_RESEARCH_DB` at the
`session_quota` volume). A server restart empties only the in-flight-turn
registry — a poll for a pre-restart turn id answers 404, the recorded failure
surface — while the session, its state, and its transcript reload and the
conversation continues. A soft session cap (forty turns) appends a
suggest-the-Brief note, never a refusal.

## Considered options

- Keeping the one-shot dive and adding the chat beside it. Rejected: two
  phase-3 surfaces answering the same question with different safety stories;
  the dive's retrieval/synthesis internals were exactly the operations the
  Wayfinder needed, so they were extracted (`ui/dive.py` is now the retrieval
  kernel: searchers, fan-out, the bounded two-round loop) rather than
  duplicated.
- Letting the classifier apply research-question changes directly. Rejected:
  a silently rewritten question destroys the provenance the Wayfinder design
  exists to keep, and a wrong auto-narrowing misdirects every later gather.
  The proposal → decide split keeps the human at the consequential turns.
- An agent loop that plans and executes multiple operations per message.
  Rejected: the platform's recorded anti-unbounded-loop stance (issue #27)
  stands — one bounded operation per turn, hard caps on the fan-out, and the
  next-best-move surfaced as chips for the human to steer.
- A transcript-faithful Brief (reconstruct from chat history). Rejected:
  reconstruction invites hallucinated provenance; the state is the auditable
  record (Wayfinder §19 — artifacts generate from the accumulated research
  record).
- Postgres as the session store. Rejected for this slice: quotas.py's sqlite
  pattern already runs in production on the same host paths and the container
  volume; a Postgres schema buys nothing the investigation needs at one
  process. Revisit with the durable-execution upgrade.

## Consequences

- ADR 0006's dive-specific endpoints (`/deep-dive`, `/deep-dive/status`) and
  job machinery are gone; its Quote-selection half, the searcher pins
  (`HYBRID_COMPLETION`, the fixtures), and the fan-out caps stand as the
  Research Mode kernel. `/next-tier-recall` stays as the operator probe.
- The phase-3 tab is a chat: transcript bubbles, the «وضعیت پژوهش» state
  strip, and the chip row (next moves + checkpoint decisions). Quote spans
  keep their click-through to the Book reader (ADR 0007).
- A browser refresh reconnects to an in-flight turn; the earlier transcript
  is not re-fetched in V0.1 — the session continues server-side and the
  refresh says so. A transcript read endpoint is the recorded follow-up.
- The claim ledger's statuses are deliberately conservative: External
  Knowledge is not a status in this platform (book-only contract —
  out-of-corpus content is labeled commentary and may never enter a claim),
  and `reasoned_inference` is reserved for a future explicit step.
- CONTEXT.md gains Research Mode, Research state, Claim ledger, and Gap;
  the Deep dive retires into that entry's supersession note. The README's
  Deep dive section is rewritten as Research Mode; `tests/test_research_mode.py`
  (40 locks) replaces `tests/test_deep_dive.py`.
