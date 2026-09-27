# ADR 0016 — The research session joins the Session store, and the dead accept button

Date: 2026-09-27
Status: accepted
Amends: ADR-0015 (the known limitation its consequences named), ADR-0014 (the Session store's scope)
Context for: the 2026-09-27 operator feedback round (research persistence + the accept chips)

## Context

Two more reports from the deployed shell, both landing in the same place:

1. **The research trail dies at the first sidebar click.** After a research-mode conversation, a reload — or a switch to another Session and back — restored the chat but left nothing of the research: no transcript, no map. The root cause was never storage: `research.sqlite3` keeps the whole session (state, messages) server-side, and `/research/state` + `/research/messages` would happily re-render both. What died was the *pointer*: the research session id lived only in `sessionStorage["sessionResearch"]`, and the two restore paths were mutually exclusive — `openStoreSession` (the chat's resume) unconditionally called `resetResearch()`, erasing the pointer, while `reconnectResearch` (the reload path) skipped the chat restore entirely. Whichever path won destroyed the other; ADR-0015's consequences had named exactly this as the outstanding limitation ("research conversations are still not rows in the Session store").
2. **The «می‌پذیرم» chips did nothing on a reconnected sitting.** `decideResearch`'s first line was `if (!researchSessionId || !researchQuery) return;` — a leftover precondition from the pre-ADR-0015 ask-seeded flow. `researchQuery` was set only when a research session was *created*; after any reconnect it stayed null forever, and every proposal chip (accept, reject, diagnoser adjustment, brief plan) silently returned. The server itself needs only `session_id`, `proposal_id`, and `accept`. The reconnected input also arrived disabled (`reconnectResearch` returned before `armResearchInput()` when no turn was in flight), so the chips' own `researchInputEl.disabled` guard ate the click before the first guard could.

## Decision

- **The linkage lives in the research store.** `research_sessions` gains a nullable `chat_session_id` (topped up in place, the `version`-column pattern), written by `attach_chat_session` only from the session's own Account. `POST /research/message` accepts an optional `chat_session_id` on the creating call, and links it only after the Session store confirms the id belongs to the caller — a foreign or malformed id never fails the ask; the research is the user's act, the linkage is bookkeeping.
- **The resume read carries the pointer.** `GET /sessions/{id}` returns `research_session_id` (the Account's newest research session tied to that sitting), and `/research/state` returns `chat_session_id` — the reverse lookup, so the refresh reconnect can open the WHOLE sitting instead of the bare research thread it used to hang in a fresh page.
- **One hydrator, both bodies.** `hydrateResearch` is the single resume path for every return: it re-fetches the transcript and the state, re-renders the map and the chips, re-arms the founding question (from `research_state.research_question` — the accept fix's third leg), polls a stored in-flight turn if the tab holds one, and arms the input. `openStoreSession` raises the research conversation beside the restored chat whenever the sitting has a linked research session; `reconnectResearch` first tries the reverse lookup and falls back to the bare reconnect only for a pre-linkage conversation. A closed conversation (the state read answers 409) renders read-only with the closed note and never re-arms the composer that could no longer feed it.
- **A failed turn drops only the turn handle.** `settleResearchTurn` keeps `{session_id}` in sessionStorage instead of clearing it — the session itself stays alive in the store, so the chips, the next message, and the sidebar's reopen all keep working.
- **The decide guard loses its leftover.** `decideResearch` checks `researchSessionId` alone, and the chips no longer test `researchInputEl.disabled` — `decideResearch` locks the input itself for the call's duration, and `hydrateResearch` always arms it.

## Consequences

- A research conversation survives reload, session switch and back, and (via the linkage) another tab opening the same sitting. The old same-tab `sessionStorage` reconnect remains as the fast path, not the only one.
- ADR-0015's known limitation is resolved for sessions created from now on; pre-linkage conversations (rows with a NULL `chat_session_id`) stay reachable exactly as before.
- A closed research conversation is now visible in its sitting's history — read-only, honestly closed — instead of vanishing.
- The mirrored client string `RESEARCH_CLOSED_NOTE` reuses the server's `RESEARCH_SESSION_CLOSED_DETAIL` text verbatim; no new name entered the roster through this ADR.
