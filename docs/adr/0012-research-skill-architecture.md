# ADR 0012 — The Research skill architecture: an agent-picked skill engine inside the unchanged safety contract

Date: 2026-09-16
Status: accepted
Amends: ADR-0008 (the one-bounded-operation rule becomes the bounded chain), ADR-0009 (journey layer kept; what executes inside a turn changes)

## Context

The grilling session of 2026-09-16 fixed the module's intent and confronted it with the audit facts:

1. **The intent.** A flexible guide-agent: non-hallucinating, step-by-step, with a visual record (the map) of why-we-are-here and scope; the Book set stays the only citable source; the agent commands **many retrieval Tools** — modular, invoked when needed, in plain Persian.
2. **The engine has ONE Tool.** Gathers, chat answers, mapping survey, and the phase-1 fallback all call the same `HYBRID_COMPLETION` (`ui/dive.py:94-107`); graph completion exists as an operator probe only; decomposition, summaries, chunks and the rest are unwired.
3. **Chat is amnesiac.** A side question is one fresh search + one writer, blind to the evidence ledger, claims, and gaps (`research.py:1846-1866`).
4. **Failures fall back silently.** A failed classify degrades to a chat answer, an empty pool to a generic note, a failed writer to the previous shape — nothing names WHY the answer was bad, so nothing improves (`research.py:759-772, 1186-1193, 1859`).
5. **Reliability debts.** No turn-level deadline; unbounded ledger→prompt growth; a decide-vs-worker lost-update race; the all-starved stall that makes the Brief unreachable; ~10 risk areas with no test.
6. **The Brief is one unreviewed writer call** — model-decided headings, no plan the user saw, no check that it answers the destination (`research.py:1801-1821`).

The declared model is the Matt Pocock skill system's *pattern*: wayfinder's map already ported (ADR-0008/0009); the missing back half (plan → contracted sections → review) and the modularity (skills invoked when necessary) ported now.

## Decision

- **The engine becomes a Research skill system.** A dispatch table holds every **Research skill** with its contract: purpose, state inputs, state outputs, caps, guard applicability, allowed stages. The roster: راهنما (guide/grilling), جست‌وجوگر (gather), نقشه‌کش (landscape), کاوشگر (fog probe), تحلیل‌گر (synthesize), نویسنده (Brief sections), بازبین (Closing review), میزبان (conversational), نقشه‌بان (map hygiene), تشخیص‌گر (failure diagnosis).
- **The model picks, the code disposes.** Each turn ONE call returns skill + arguments; code validates against stage rules, cooldowns, and the per-turn budget before anything runs. A new skill or Tool is a new contract row — the router prompt is generated from the table.
- **The safety contract is untouched.** Verbatim guard, server-built references, checkpoints via `/research/decide`, append-only question versions, Book-only claims — none of it changes. The bugs are engine-side; the contract IS the non-hallucination promise.
- **The bounded chain replaces the single operation** (amending ADR-0008). A turn may chain up to **3 skills** inside a hard per-turn budget — a wall-clock deadline and an upstream-call cap. Slow is allowed; runaway is not, now with a number.
- **Tools multiply; sources don't.** Cognee's unused modes (graph completion, decomposition, summaries, chunks) are wired as Tools of the جست‌وجوگر and میزبان; every Tool searches the session's picked Book only.
- **تشخیص‌گر — diagnose, never silently fall back.** A bad answer names its cause (starved corpus, wrong Tool, guard drops, ill-fitting question), records the diagnosis in the state, and returns an adjustment proposal — narrow the question, change Tool, or declare a Gap. The standing balance between user intent and what the Books can support.
- **میزبان — bounded cited reasoning.** Side answers may reason over retrieval + graph for at most **2 hops**; every quote passes the guard; every citation resolves a real page; reasoning with no supporting passage renders as commentary, never a claim. It reads the ledger.
- **The Brief assembly line (the ported back half).** The **Brief plan** (sections ↔ open questions ↔ claim ids) lands as a checkpoint the operator accepts/edits; each section is written against its **Section contract** (declared claims, assigned question, scope lines) with the guard and ONE retry; the finished Brief faces the **Closing review** — traceability (sections→claims→evidence, scope respected) plus the destination judgment — landing with accept/revise chips.
- **The map sticks; skip is universal.** The research map renders as a persistent side rail of the chat, always visible. A skip exists for every question the engine asks, in every stage — impatience is always a valid answer. Skipping steers the journey; it never bypasses the safety contract.
- **Strangler migration.** Characterization tests first — the audit's untested-risk list (deadline, state caps, decide race, stall escape, registry reaping, chip misroute, 429 orphan…) is the seed list — then the new engine lands beside the old one and skills flip one by one; the 247 existing tests stay green throughout.
- **Plain-Persian naming pass.** CONTEXT.md stays the single vocabulary home; display names are drafted there and approved by the PM before the sheet renames anything.

## Considered options

- Full rewrite including guard and checkpoints — rejected: it would rebuild the one part that never lies; the audit found no contract bugs.
- Literally installing the development agent-skills in the product — rejected: category error; they drive a coding agent, not a Farsi research product. We port their pattern.
- Big-bang replacement in place — rejected: it discards what the live bugs taught; the strangler's tests keep that knowledge.
- Dissolving the five stages for free skill flow — rejected: the map — the declared "visual record of why we are here" — needs a route; skills flow within stage rules instead.
- Free model chaining without a budget — rejected: "slow is allowed, runaway is not" survives only as numbers.

## Consequences

- A turn becomes: classify/skill-pick → code validation → bounded chain → guard → narration + fact line. The single classify's silent authority ends; a misroute is a diagnosable, recorded event.
- The audit's risk list becomes the first ticket batch, written as characterization tests before any engine flip.
- ADR-0008's one-operation rule and ADR-0009's dispatch specifics are superseded as stated; the checkpoint rule, stage machine, map, grilling vocabulary, and guard stand unchanged.
- Next step in the development workflow: `/to-spec` collapses this ADR and the grilling thread into the build spec, then `/to-tickets`.
