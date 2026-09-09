# Chat on Z.AI's GLM Coding Plan, embeddings stay on AvalAI

Chat moved from AvalAI to Z.AI's GLM Coding Plan (`https://api.z.ai/api/coding/paas/v4`, OpenAI Chat Completions protocol) on 2026-09-09, with a model split: `glm-5.3-flash` on `cognee` (first answers, where latency is user-visible) and `glm-5.3` on `cognee-next-tier` (Next-tier COT, where depth beats speed). Embeddings stay `text-embedding-3-small` on AvalAI, so the existing pgvector data stays valid and no re-ingest is needed.

## Considered options

- Keep AvalAI for chat (status quo of 2026-09-08). Rejected: PM call 2026-09-09 moved chat to the GLM Coding Plan key.
- One Z.AI model on both tiers. Rejected: flash on the first-answer path is the latency win of the two-service split; `glm-5.3` is reserved for the deep background synthesis.
- Move embeddings to Z.AI too. Rejected: the coding endpoint does not serve `text-embedding-3-small`, and any embedding-model change invalidates the pgvector index before the 10 September 2026 Session.

## Consequences

- `EMBEDDING_API_KEY` is now mandatory in `.env`: Cognee falls back to `LLM_API_KEY` for embeddings, which is now the Z.AI key and would 401 on AvalAI.
- The coding endpoint has no documented model listing; model changes are verified by a POST to `/chat/completions` with the candidate model ID, not a `/models` GET.
- The coding plan is documented for coding tools; other agent use is best-effort under Z.AI's usage policy — an accepted scope risk recorded here.
