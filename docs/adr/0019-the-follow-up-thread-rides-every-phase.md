# ADR 0019 — The follow-up thread rides every phase

Date: 2026-10-02
Status: accepted
Amends: ADR-0015 (the follow-up thread: the ≤ 8-word rewrite gate and the writer-only tail)
Context for: the operator's multi-turn request — clarifications, refinements, "not happy with the answer, go a different way", continuations; without re-architecting

## Context

ADR-0015 landed the follow-up thread, but its context covered only a narrow slice of the pipeline, so a second message in a sitting still behaved almost like a first one:

1. **The rewrite gate was too tight.** Only a question of ≤ 8 words was rewritten into a self-contained retrieval query. Real clarifications and refinements («نه، فصل دوم آن کتاب را می‌خواستم با مثال‌ها» — nine words) rode to Cognee raw: retrieval forgot the thread entirely, so the passages for a follow-up were picked as if the earlier turns had never happened.
2. **The picker was blind to the thread.** The Quote selection — the first answer the user sees — was picked from question + passages alone (`build_picker_prompt` had no tail parameter). A follow-up's first answer re-answered the last ask.
3. **The planner was blind to the thread.** ADR-0015 deliberately gave the tail to the writer only; the planner planned from the question and passages alone. A follow-up's plan that ignores what the sitting already covered re-plans the last answer.
4. **The tail was too thin to steer by.** Each earlier answer contributed at most 400 characters of connective text — not enough for "I'm not happy with that answer, take it differently" to say what it is unhappy with.

The constraint: minimal, rational changes — no new endpoints, no store schema change, no frontend change, no new state. The Session store already holds the thread (`settle_ask`'s rows, the same transcript the resume renders); the phases already receive `session_id`; `_conversation_tail` already reads it server-side and fails soft.

## Decision

- **The gate widens to 25 words.** `REWRITE_MAX_WORDS` 8 → 25. Real follow-ups run 5–20 words; a message longer than that almost always names its own subject and rides raw. The cost discipline holds: still ONE cheap glm-5.3-flash call (15 s timeout, 512 tokens), only on a follow-up-shaped message over an existing sitting, metered like every composer call, every failure answering the raw question. A first ask (no sitting) still never rewrites.
- **The rewriter judges self-containment.** The rewrite prompt gains one instruction: a message that already names its own subject and needs nothing from the earlier turns is returned UNCHANGED. The sitting's context must never drag a topic switch or a brand-new question back to the old subject.
- **The tail rides the picker.** `pick_quote_selection` / `build_picker_prompt` gain `conversation_tail` and reuse the writer's `conversation_context` section verbatim — one framing shape across all prompts. The first answer the user sees continues the thread.
- **The tail rides the planner.** `plan_quoted_document` / `build_planner_prompt` gain `conversation_tail`, and `compose_quoted_answer` forwards its existing tail argument. This amends ADR-0015's deliberate exclusion: the ~150-word plan cap and the planner's reasoning budget are unchanged, and the tail is framing only, never quotable. The prompt cost is trivial beside the full passages that already ride every planner call.
- **The digest deepens.** `_TAIL_ANSWER_CHARS` 400 → 800, so a follow-up can see enough of what was already answered to steer away from it. `_TAIL_TURNS` stays 3; quotes and citations still never ride the tail (quotes are the passages' job, ADR-0015 unchanged).

Unchanged invariants: Cognee stays stateless (`session_id` and any client `history` are still popped before the relay); the displayed question is always the user's own words (the rewrite is retrieval-only, never shown or settled); everything fails soft (no sitting, a silent store, a down endpoint — the raw question rides).

## Consequences

- A sitting's second, third, fourth message now carries its thread into all three generation surfaces — retrieval (rewrite), the first answer (picker), and the article (planner + writer) — through the existing session tail, with zero new state.
- One added upstream cost path: the rewrite now also fires on follow-ups of 9–25 words (the 8-word case was already paid). The picker and planner grow in prompt tokens only (≤ ~2.4 KB of framing), no new calls.
- The rewriter can now decline to rewrite; a self-contained message over an existing sitting still spends its one metered call and answers the same query it arrived with.
- ADR-0015's "the planner plans from the question and passages alone" is superseded for the tail; its other decisions (settle shape-precedence, one door, Balance-only research gate) stand.
