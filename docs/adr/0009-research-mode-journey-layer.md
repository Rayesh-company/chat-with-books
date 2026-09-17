# ADR 0009 — Research Mode's journey layer: stages, guided questions, and the visible map

Date: 2026-09-15
Status: accepted
Amends: ADR-0008 (Research Mode — the Wayfinder chat)

## Context

The live V0.1 smoke (the operator's captured session, `OUTPUT/` screenshots,
2026-09-15) read as generic: two or three templated notes and an output. The
walkthrough found why — the assistant had no voice (research-stage replies were
server-composed counters and checkpoint announcements), the chips were the same
four fixed buttons every turn, the agent never asked the user a substantive
question (the only "question" was the yes/no RQ checkpoint), and the state strip
showed counts, not a route. The user asked for what the Wayfinder skill
(`WAYFINDER.md`) gives an engineer before a project: a guided journey that
maintains the goal against the knowledge base, stage by stage, with the user's
opinions folded in along the way.

## Decision

Add a **journey layer** between the conversation layer and the research-state
layer of ADR-0008. It is adapted from the Wayfinder skill's map/ticket/fog
architecture:

- **The stage machine.** Five stages — `orientation` (name the destination),
  `mapping` (survey the ground breadth-first), `investigating` (work the open
  questions), `synthesizing`, `drafting` — moved ONLY by code on observable
  state (a destination recorded, two open questions pending, evidence pooled,
  claims recorded, the Brief written). The model proposes content; it never
  moves a stage. `phase` stays as a mirror for the V0.1 readers.
- **Guided questions (grilling, HITL).** In orientation and mapping the reply
  can be ONE question authored by a thinking-ON `glm-5.3-flash` call — one
  sharp question, two to four short option chips, and always a skip chip
  («فعلاً همین کافی است؛ ادامه بده»). The agent NEVER answers its own
  question (the Wayfinder rule); the user's answer lands as a `decisions`
  entry, and in orientation it names the **destination** — what the user wants
  to walk away with. Book grounding comes only from the concepts the
  investigation has actually seen; a guided question states no Book fact, so
  there is nothing for the verbatim guard to check. Bounds: at most three
  guided rounds per stage; a failed authoring falls through to the stage's
  ordinary move, never a dead end.
- **The visible map.** The state strip becomes «نقشۀ پژوهش»: destination,
  research question with version count, the frontier («در حال پرداختن»), the
  NAMED open questions with statuses, the decisions index, the fog
  («هنوز نامشخص»), out of scope, and the counts. A journey strip above it
  names the five stages. `decisions` existed in V0.1 but was never surfaced.
- **Named open questions.** Sub-questions carry `id` + `name` (short Farsi
  label) and are the journey's unit of work; replies and chips refer to them
  by name («شواهدِ «شهود و ساحت» را پیدا کن»), never by id.
- **Fog of war.** `map.fog` holds questions the investigation can see coming
  but cannot state sharply enough to ask yet; when a classify pass proposes a
  matching open question, the fog note **graduates** (leaves the fog, lives
  only as its question). Out-of-scope items never graduate.
- **The journey narrator.** Every material operation's reply opens with one
  short LLM-written note (thinking OFF) explaining what changed on the map and
  what is next — qualitative only: no digits, no counts, no Book claims (the
  server's own fact line carries the numbers). A junk or failed narration
  narrates nothing; the operation's reply stands.
- **Contextual chips.** `research_suggestions` renders the journey's moves —
  guided-answer chips + skip in the question stages, per-question targeted
  gather chips plus the gather-all in investigating, audit/brief in the late
  stages — and checkpoint chips carry SHORT labels («می‌پذیرم» / «رد می‌کنم»),
  the proposal text living in the note above, never inside the chip. The
  four fixed commands of ADR-0008 stay deterministic; the new family
  («شواهدِ «نام» را پیدا کن», the gather-all, the skip) resolves
  server-side by exact text and pattern — no classification luck.

## Considered options

- **Just better copy on the V0.1 flow** (nicer notes, same spine) — rejected:
  the genericness was structural (no stages, no HITL, no visible route), not
  wording.
- **Model-driven stages** (the classifier also moves the stage) — rejected:
  stage transitions are consequential; a hallucinated jump would strand the
  user. Code moves stages on observable state only, exactly like the
  checkpoint rule keeps RQ changes user-approved.
- **Free-form agent chatter** (unrestricted narration/questions) — rejected:
  the platform's no-hallucination contract bounds every LLM output. Guided
  questions and narrations are process speech over state-derived facts; Book
  content still flows only through guarded writers over real passages.
- **Re-fetching the transcript on refresh** — deferred unchanged from V0.1;
  the journey layer changes the turn engine, not the session transport.

## Consequences

- The first two turns of a research conversation are now a real dialogue
  (destination question → answer → grounded landscape with a breadth-first
  question), not a counter log.
- Every LLM call added (question author, narrator) is bounded — at most one
  each per turn, never retried in a loop — and both degrade to silence on any
  failure, so the turn's safety shape is unchanged.
- A V0.1 session in the store loads under `ensure_state_shape`: the old phase
  word derives the stage, unnamed sub-questions gain names, the map defaults
  fill in — a pure upgrade, never a rewrite.
- The map projection rides the existing summary (turn results, `/research/state`,
  `/research/decide`) — no new endpoints, no store migration.
- Tests: seventeen new cases lock the stage machine, the grilling bounds, the
  fog graduation, the targeted gather, the narration guards, the compat
  loader, and the map projection (204 total green).
