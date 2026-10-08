# ADR 0020 — The early pool and the streamed writer: the ask's clock stops waiting for the completion

Date: 2026-10-08
Status: accepted (implemented on feat/early-pool; not yet deployed)
Amends: the ask flow of ADR-0014-retrieval-only (the /ask door returns to the sheet's front page), ADR-0006/issue #28's settle contract (unchanged — the settle paths still own the answer), and the daily-chat quota's counting shape (T21)

## Context

The operator's 2026-10-08 timing report against the live sheet, backed by a
local harness on the same routing (Z.AI direct, live keys):

| stage | old sheet | measured |
|---|---|---|
| citation pool visible | at the streamed completion's `final` | 44.8 s |
| first stable answer (Quote selection) | ~94 s | |
| Quoted answer settled | ~227 s | |

The structural cause: the pool the picker and phase 2 read rode the streamed
completion's `final` event — every composer phase waited ~45 s for a
completion whose text the sheet then throws away (the picker replaces it).
The completion's own TTFT (~39–45 s) gated work that never needed it: the
picker and the planner read the question and the pool passages only.
Secondarily, phase 2's ~100–180 s compose (planner → writer) rendered as one
silent pulsing status — the operator reads it as a hang («گیر کرده»).

The building blocks already existed: ADR-0014-retrieval-only built the
`/ask` door (`only_context` retrieval, ~2–6 s measured, the same hybrid
lanes the completion reads — measured equivalent, recorded live 2026-09-23)
and then the impeccable-critique fix (2026-09-28) re-introduced the stream
as the subordinate draft. Neither door served the other.

## Decision

- **The early pool.** The sheet's ask fires `/ask` and `/api/v1/recall`
  TOGETHER, both carrying the ask's `ask_key`. Whichever lane delivers a
  pool first starts the picker + phase-2 pair (`startPoolPhases`); the
  stream's own Evidence block becomes a redundant second copy of the same
  retrieval lanes and never restarts the phases. The draft stream keeps its
  UX role unchanged (subordinate, retired at the settle). The picker now
  lands ~60 s earlier and phase 2 ~100 s earlier (measured: first answer
  94→34 s, settle 227→121 s — part of the delta is Z.AI queue variance; the
  structural win is the ~45 s completion wait off both phases' front).
- **One ask, one chat record.** The `/ask` leg records the ask's daily
  chat; the recall leg with an `ask_key` body CHECKS the cap but skips the
  increment (`_gate_account_pre` → `_peek_ask_key` → `_quota_check` — auth
  answers before the body is ever read). A body without the key records
  exactly as before. A cap-refused ask cannot smuggle its stream through:
  the cap is checked on both legs.
- **The /ask leg rides the shared rewrite.** `session_id` on the `/ask`
  body runs the same `_contextual_query` the recall relay runs
  (ADR-0015/0019), so a follow-up's early pool retrieves on the same
  self-contained query the stream leg sees. `ask_key` and `session_id`
  never relay upstream.
- **The streamed writer.** `/quoted-answer` with `"stream": true` replies
  `text/event-stream`: the planner runs as before, then the writer call
  streams, and each paragraph block that CLOSES and survives a per-block
  guard (`guard_blocks(..., threshold=False)` — a lone paragraph must not
  vanish because its document has not streamed yet) rides a `block` event.
  `final` carries the one authority — the full-document guard, the swap
  threshold, the settle (session store + chat store exactly as the JSON
  path). Without the flag the JSON contract is byte-for-byte unchanged
  (the reference flow and the existing tests ride it). The sheet paints
  `block` events subordinate (`.quoted-doc.draft-stream`) and re-renders
  at the settle; the incremental scanner
  (`guard.iter_complete_array_objects`) is a string-state-aware container
  stack — braces inside quoted Farsi never count, nested `parts` objects
  never match.
- **The first answer owns the section.** `firstAnswerSettled` stands the
  draft's delta paints down the moment the picker (or its prose fallback)
  lands — the early pool can settle phase 1 while the stream still runs.
  The picker's «انتخاب نقل‌قول‌ها» status rides the citations section while
  the draft streams, so the status never tears down the life-sign the
  draft exists for. A stream failure beside a live pool retires the draft
  only — the running phases keep their pool, and the clock belongs to the
  settle that is still to come.

## Considered options

- **Drop the streamed completion entirely** (ADR-0014-retrieval-only's
  original world) — rejected again: the 2026-09-28 critique stands, the
  sheet needs the life-sign while the picker runs.
- **Stream the planner** — pointless: its output is a ≤150-word plan the
  sheet never displays; the writer is where the paintable document forms.
- **Parallelize the planner with the writer** — rejected: the writer
  frames from the plan (PM call, 2026-09-10); splitting the document
  across writers multiplies verbatim-guard risk for a structural quality
  change the PM never asked for.
- **Count the ask on the recall leg and skip on /ask** — rejected: the
  reference flow and operator probes call the doors independently; the
  ask_key companion marker is the only honest signal of "these two legs
  are one ask".

## Consequences

- First-answer latency drops by the completion's full wait; the Quoted
  answer's settle drops by the same, and its write paints progressively
  instead of as one ~40 s lump after a silent planner window (the planner
  window itself stays silent — its output is never displayed).
- The daily-chat quota still counts one per ask; the harness numbers
  above double-checked both legs' accounting (test_early_pool.py).
- `guard_blocks` gained a `threshold` keyword (default unchanged); the
  composer's streamed path and the tests are its only `False` callers.
- The sheet's visible strings are unchanged — the naming gate is not
  touched.
- Measured 2026-10-08, local (Z.AI direct routing, live embedder):
  pool 5.9 s, first answer 33.7 s, settle 121.0 s — against the same-day
  baseline 44.8 / ~94 / 227 s. Suite 626 green (11 new locks in
  tests/test_early_pool.py).
