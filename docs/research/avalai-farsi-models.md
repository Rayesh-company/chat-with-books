# AvalAI models for Cognee Farsi Q&A

Question: which AvalAI chat and embedding models should Cognee use for Farsi book Q&A, given `https://api.avalai.ir/v1`, citation usefulness, and a first answer that should not feel slow?

Product constraint: AvalAI stays the provider. Do not invent Farsi quality or latency numbers.

## Recommendation (inference)

Wire Cognee as an OpenAI-compatible client to AvalAI (not as native OpenAI). Split models by job:

1. **Embeddings (ingest + retrieve):** `text-embedding-3-small` to start. AvalAI documents this model on `/v1/embeddings` with default 1536 dimensions. Cheaper/faster to ingest a book set than `text-embedding-3-large`. Upgrade to `text-embedding-3-large` only if a labeled Farsi retrieval set shows the small model missing must-include passages.
2. **First-answer completion:** a **chat-completions-capable** mid-tier model, not the flagship reasoning default. AvalAI tells new apps to prefer `/v1/responses` + `gpt-5.5`, but Cognee's custom provider requires an OpenAI-compatible **`/v1/chat/completions`** route. Use a fast chat model from the catalog you confirm in `/v1/models` — candidates to **eval**, not claims: `gpt-5.4-mini` (AvalAI's own cheaper OpenAI-family option) or `qwen3.6-flash` / `gemini-3.1-flash-lite` (listed as budget-friendly). Pick the first that (a) exists on the account, (b) speaks Farsi well enough on a 10-question gold set, (c) returns structured JSON well enough for Cognee graph extraction.
3. **Next-tier / COT completion:** a stronger model on the same endpoint (`gpt-5.5` or `qwen3.7-max` / `claude-sonnet-4-6` if those IDs are on the account). Cognee COT already costs many LLM round-trips; do not put the slowest model on the first answer.

Do **not** treat any of those IDs as proven for Farsi. AvalAI does not publish a Farsi leaderboard. Their embeddings guide only says bilingual eval sets should include Persian queries.

## Facts

### AvalAI transport

Quickstart: set `AVALAI_API_KEY`, OpenAI SDK with `base_url="https://api.avalai.ir/v1"`. New apps are steered to `/v1/responses`; `/v1/chat/completions` is for existing chat integrations and models that only expose chat compatibility. Public catalog: `https://api.avalai.ir/public/models`; authenticated `/v1/models`.

Source: https://docs.avalai.ir/en/quickstart

### Embeddings

`/v1/embeddings` example model: `text-embedding-3-small`. Also listed: `text-embedding-3-large` (3072 dims), `text-embedding-ada-002`, plus Gemini/Cohere/Alibaba/etc. "Do not mix models or dimensions inside one vector index." Eval tables explicitly include Persian/English spelling variants and "Persian queries against Persian content".

Model-selection table: embeddings "top performance" `gemini-embedding-2`, `text-embedding-3-large`; balanced `embed-v4.0`, `text-embedding-3-small`; budget `qwen3-embedding`, `embed-english-v3.0`.

Sources:
- https://docs.avalai.ir/en/guides/embeddings
- https://docs.avalai.ir/en/guides/model-selection

### Chat / completion models

AvalAI lists many provider IDs (OpenAI `gpt-5.5` / `gpt-5.4-mini`, Anthropic, Gemini, Qwen, DeepSeek, etc.). Selection guide: accuracy first, then cost/latency; for latency-sensitive work, drop to mini/flash/haiku after an eval. `gpt-5.5` on Responses uses `reasoning.effort`; `low` is for latency-sensitive flows.

Source: https://docs.avalai.ir/en/guides/model-selection

No AvalAI page retrieved here states a measured Farsi win-rate for a named model.

### How Cognee must call AvalAI

Cognee LLM traffic goes through LiteLLM. Third-party OpenAI-compatible servers: `LLM_PROVIDER="custom"`, `LLM_MODEL="openai/<id-your-server-accepts>"`, `LLM_ENDPOINT` = base URL usually ending in `/v1`, `LLM_API_KEY` = bearer. The custom provider expects `/v1/chat/completions` with system/user messages. Embedding config is separate (`EMBEDDING_PROVIDER` includes `openai_compatible` and `custom`); if only the LLM is pointed at AvalAI, Cognee still defaults embeddings to OpenAI.

Sources:
- https://docs.cognee.ai/setup-configuration/llm-providers
- https://docs.cognee.ai/setup-configuration/overview

Suggested env shape (inference from those two facts, IDs to be confirmed on `/v1/models`):

```dotenv
LLM_PROVIDER=custom
LLM_MODEL=openai/gpt-5.4-mini
LLM_ENDPOINT=https://api.avalai.ir/v1
LLM_API_KEY=<AVALAI_API_KEY>

EMBEDDING_PROVIDER=openai_compatible
EMBEDDING_MODEL=openai/text-embedding-3-small
EMBEDDING_ENDPOINT=https://api.avalai.ir/v1
EMBEDDING_API_KEY=<AVALAI_API_KEY>
EMBEDDING_DIMENSIONS=1536
```

`LLM_QUERY_*` / `LLM_EXTRACTION_*` exist as per-stage overrides if first-answer and graph-extraction need different models.

Source: https://docs.cognee.ai/setup-configuration/overview

## Unknowns (do not fake)

- Which AvalAI model IDs are enabled on **this** account.
- Farsi answer quality, citation faithfulness, and JSON-extraction quality per model.
- Round-trip latency from the VPS to `api.avalai.ir` (primary domain claimed "lowest latency"; not measured here).
- Whether Cognee's `json_mode` structured-output default works on the chosen AvalAI chat model.
- Whether `gpt-5.5` is usable through Cognee at all (Cognee custom path is chat/completions; AvalAI prefers Responses for that model). Confirm `/v1/models` and chat-route support before locking a flagship ID.

## What would change this recommendation

- A 10–20 item Farsi gold set over the real book corpus (retrieval + answer + citation).
- Account model list showing Qwen/Gemini/OpenAI availability.
- Evidence that the chosen chat model cannot emit the JSON Cognee needs for `cognify` — then try `LLM_INSTRUCTOR_MODE=tool_call` (Cognee custom-provider docs) or a different model, not a different API vendor.
