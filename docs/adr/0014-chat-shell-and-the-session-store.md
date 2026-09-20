# ADR 0014 — The chat shell: chat interaction patterns, نشست language, one Session per sitting

Date: 2026-09-20
Status: accepted (stage 1 landed; the Session store is stage 3)
Amends: ADR-0005 (the three-phase tabbed sheet — its presentation is superseded; the pipeline is not)
Context for: the 2026-09-20 grilling session that planned T27 (the "chat shell" redesign)

## Context

The sheet looked like a demo to the people paying for it: a document-sheet layout where one ask owned the whole page, a tab strip for the three phases, no conversation history, a form-like ask with a legacy phone field, and English `chunk N of document …` locator lines leaking into the RTL UI. The PM's brief of 2026-09-11 (pinned in tests) chose tabs when the answer was one ask at a time; real Session operators expect what every chat product has taught them — a thread, a composer, a history, and streaming that shows life.

Two facts constrained the redesign:

1. **The glossary outruns the metaphor.** CONTEXT.md deliberately bans «گفتگو»/chat and says **Session (نشست)**, and the product's differentiator is the three-phase pipeline (ADR-0005/0006): the Quote selection is the first answer and the streamed prose is never displayed. A "real chat UI" must not drag in a rebrand or flatten the pipeline into plain chat bubbles.
2. **The deploy model is stdlib-only.** One `index.html` and one `serve.py`, zero npm, tarball deploys (the container ships nothing installed). A framework rewrite would trade a working operations story for familiarity.

## Decision

- **Chat interaction patterns, نشست language.** The shell behaves like a modern chat app — sidebar, thread, composer, streaming, history — while every visible name comes from CONTEXT.md's roster. New Farsi strings are collected in ONE draft table in the sheet's source (and CONTEXT.md's 2026-09-20 draft roster) for PM sign-off; nothing ad-hoc ships visible text.
- **The thread supersedes the tabs.** An ask becomes a user bubble plus one assistant article; the phases render in place under quiet phase markers — Quote selection with its collapsed Evidence pool, the Quoted answer extending beneath its marker, Research Mode as the Session's own section. ADR-0005's tabbed presentation is retired; the pipeline itself (phases, parallel picker, guards, timers) is untouched.
- **One Session = one sitting.** A Session is created from the empty state by picking exactly one Book (the Book pick glossary row; the per-ask toggles of ADR-0010's UI retire — the server contract never changed), it endures under the Account, and it is resumable. Research Mode runs one journey per Session; a new investigation opens a new Session. Stage 3 adds the server-side Session store (stdlib SQLite) behind «فهرست نشست‌ها», with multi-device refetch-on-focus and last-write-wins.
- **The stop button is honest.** The composer locks while the pipeline runs; stopping keeps the charge for what already streamed (the ledger has no refund path) and the UI says so in plain Farsi.
- **The phone field is finished off.** ADR-0013 already stripped the header's authority; the shell removes the field, the quota hint, and the `X-Session-Phone` header entirely.
- **Zero-dependency holds.** The shell is the same single self-contained file: hand CSS on a two-theme token layer (paper identity, light and dark, system preference plus a manual toggle), vanilla JS with per-ask element bindings instead of a framework.

## Considered options

- **Framework rewrite (React/Vite + Tailwind)** — fastest path to a familiar chat shell, rejected: a build toolchain and a node toolchain break the stdlib-only deploy story that ADR-0001's stack and the container image are built on.
- **Rebrand as a chat app («گفتگو»)** — rejected for now: the naming roster is PM-approved and the Session vocabulary is load-bearing (reports, metering, console all say نشست). The glossary gains a draft row instead; the PM's approval renames it in one line.
- **Paint the streamed prose during phase 1** — rejected: ADR-0006/issue #28 deliberately made the Quote selection the first answer with no AI-written text; live streaming feedback comes from the status surface, TTFT, and the staged reveal of the guarded result instead.
- **Keep tabs inside one message** — rejected: it preserves the demo feel the redesign exists to kill, and ADR-0005's own rationale (separate switchable sections) is met better by an in-place thread.

## Consequences

- `test_session_ui.py`, `test_phone_gate.py`, `test_first_answer_docs.py`, and `test_quoted_answer.py`'s sheet tests re-pin to the thread shape; the 2026-09-11 tabbed PM brief is explicitly superseded and the README's UI chapters were rewritten with it.
- Stage 1 keeps the Session browser-local (honest: a reload returns to the empty state with the last Book pre-highlighted; a stored research conversation still reconnects). The stage-3 store replaces that local list with the Account's real history — the sidebar render is already written against that swap.
- Per-ask DOM is built through `newAskSurface()` with rebindable element slots, so the pipeline functions keep their names and contracts while old asks stay visible in the thread.
- The draft string table (sidebar and button names, stop, theme, hero, starter questions, stop's charge note) waits on the PM like every draft row before it; the research composer joins the shell's composer in stage 4.
