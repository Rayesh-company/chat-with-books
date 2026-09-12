# Three-phase sheet: one section per phase, per-phase timers, and the length-cut continuation

Amended 2026-09-12: the phase-3 auto-start is overturned by ADR 0006 — phase 3 becomes the operator-started Deep dive. The sections, timers, and truncation-continuation machinery stand.

The Session sheet becomes a three-phase pipeline view (PM brief, 2026-09-11). One ask runs the whole chain — phase 1 streams the first answer, phase 2 writes the Quoted answer, phase 3 answers from the graph retrieval (the Next-tier search) — and each phase generates into its own switchable section: tabs with a pulsing dot on the phase being generated, a «در حال تولید: …» line naming it, and a timer chip on phases 2 and 3 that ticks while the phase runs and freezes at its completion time. Phase 3 starts automatically when phase 2 settles; `ui/serve.py` gains `/next-tier-recall`, which pins the recorded Next-tier payload server-side (`GRAPH_COMPLETION_COT`, the two datasets, `includeReferences: true`, not streamed) and relays to `NEXT_TIER_URL` (default port 8001) behind phase 2's exact gate shape.

Phase 2's cut-off replies get a repair at the same pass: a reply stopped by the output ceiling (`finish_reason` `"length"`) dies mid-JSON, so the complete block prefix is salvaged and ONE continuation call writes only the remaining blocks; if the continuation is cut too, the guarded prefix still lands and the response says `"truncated": true`.

## Considered options

- Phase 3 behind a button, per the original operator-started Next-tier definition. Rejected: the brief describes one three-phase answer per ask, with completion timers for phases 2 and 3 — a manual third phase has nothing to time. The recorded boundaries still hold: same question as phase 1, its own service on 8001, so the first-answer path is never stolen, and a new ask aborts an in-flight search.
- Keep the PM call that hides the Evidence section after the swap. Superseded by the sections themselves: the Evidence list stays in phase 1's own tab, where it is the verbatim source pool, and the swap no longer shares a section with anything it could clobber.
- Raise `COMPOSER_MAX_TOKENS` past 16384. Rejected: it is the endpoint's reply ceiling, already pinned after the live 2026-09-10 cut; the repair belongs at the truncation seam, not the knob.
- Continue a cut document by stitching raw reply text. Rejected: the cut can land mid-string; asking for the remaining blocks as a fresh `{"blocks": [...]}` object and concatenating keeps every block parseable and guardable.

## Consequences

- Every ask now burns a Next-tier search on 8001 after the quoted answer settles. The phone gate is unchanged — phase 3 belongs to the recorded chat, never counts — but the glm-5.3 budget on the coding endpoint carries every chat's COT search, not only operator-triggered ones.
- The sheet's total wait can now reach several minutes per ask (stream + ~100 s composer + multi-minute COT). The per-phase timers and the running-phase line carry that wait; slow is allowed in phase 3 by definition.
- `/next-tier-recall` never names the search type in the browser: `index.html` must stay free of the `GRAPH_COMPLETION_COT` string (locked by the first-answer recall contract test).
- The tests that locked the shared-section shape were rewritten with the restructure: the swap now renders into phase 2's section, citations stay visible in phase 1's, and the phase-2 settle callback starts phase 3 instead of stopping the pipeline clock.
