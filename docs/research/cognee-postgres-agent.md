# Cognee on Postgres: citations and deep retrieval

Question: what does Cognee with the whole memory layer on Postgres provide for a prebuilt agent over books (ingestion, citations, short summaries, next-tier / deeper search, Docker/Postgres)?

Product constraint: do not replace Cognee or Postgres. Deadline 10 September 2026.

## Recommendation (inference)

Use Cognee's documented Postgres stack for relational metadata, PGVector, and session cache. Keep `GRAPH_DATABASE_PROVIDER=postgres_demo` only if the PM accepts Cognee's own **demo / not production-ready** warning for the graph layer. First answers should use `cognee.recall()` (default auto-routing / `HYBRID_COMPLETION`) with `include_references` left on. Treat the appended `Evidence:` block as the citation surface. Treat `SearchType.GRAPH_COMPLETION_COT` (or decomposition / context-extension) as the candidate **next-tier** search — user-triggered, not the first-answer path, because Cognee's own latency table puts COT around ~200s vs ~4s for hybrid completion (different hardware/corpus; order of magnitude only).

A dedicated "short summary of the cited passage" is **not** a separate Cognee product feature. `SUMMARIES` returns precomputed document summaries; the Evidence block quotes retrieved chunks. If the exit criterion needs a written précis beyond that snippet, we add it.

## Facts

### Whole memory layer on Postgres

Cognee's README states that in 1.0 the entire memory layer can run on one Postgres instance: relationships via Cognee's Postgres graph backend, embeddings via pgvector, sessions via SQL cache, metadata via the same Postgres. Install extra: `pip install "cognee[postgres]"`. Env: `DB_PROVIDER=postgres`, `VECTOR_DB_PROVIDER=pgvector`, `GRAPH_DATABASE_PROVIDER=postgres_demo`, `CACHE_BACKEND=postgres`, plus `DB_*` connection vars.

Source: https://github.com/topoteretes/cognee/blob/main/README.md (section "Run the Whole Memory Layer on Postgres")

The same README **warns** that Postgres as a graph store is a **demo** feature; the production-ready Postgres graph adapter is licensed. Official graph-store docs repeat: in production use a graph-native backend (Kuzu or Neo4j) for the graph layer; Postgres remains a good default for relational, vector, and session layers. Provider name: `postgres_demo` (`postgres` is an accepted alias). Graph lives in `graph_node` / `graph_edge` tables. `SearchType.CYPHER` and `SearchType.NATURAL_LANGUAGE` **raise `SearchTypeNotSupported`** on this backend. Writes take a Postgres advisory lock.

Source: https://docs.cognee.ai/setup-configuration/graph-stores

Docker: Cognee publishes `cognee/cognee` and `cognee/cognee-mcp`. Compose from source: `docker compose --profile postgres up` adds Postgres/PGVector; `--profile ui` adds frontend on port 3000; API on 8000. `cognee-cli -ui` MCP path requires Docker.

Source: https://github.com/topoteretes/cognee/blob/main/README.md (Run with Docker)

### Ingestion

v1.0 entry point is `cognee.remember()`. Permanent mode (no `session_id`) runs ingest + graph build + default Improve pass. Accepted inputs include raw text, local file paths (including PDFs as path strings), HTTP(S) URLs, S3, and `DataItem`. Lower-level `add()` + `cognify()` still appear in search examples (e.g. `await cognee.add("path/to/policy.pdf", dataset_name="docs")`).

Sources:
- https://docs.cognee.ai/core-concepts/main-operations/remember
- https://docs.cognee.ai/guides/search-basics (CHUNKS example)

### First-answer search

`cognee.recall()` is the documented top-level retrieve API; omitting `query_type` auto-routes. Default lower-level type is `HYBRID_COMPLETION` (lexical + semantic chunks + graph/entity context + LLM). `RAG_COMPLETION` is chunks-only then LLM. `GRAPH_COMPLETION` is graph-backed completion.

Sources:
- https://docs.cognee.ai/guides/search-basics
- https://docs.cognee.ai/python-api/search-type
- https://docs.cognee.ai/python-api/search

### Citations / Evidence

On `recall()`, `include_references` defaults to **True**. Completion-style answers get a deterministic `Evidence:` block appended after generation (no extra LLM call), e.g. `- chunk 2 of document policy.pdf: "…"`. Chunk-level evidence needs `document_name`/`document_id` on the vector payload; otherwise it may omit the block silently. Search results **do not include raw source file paths**. `CHUNKS` returns `id`, `text`, `chunk_index`, `chunk_size`, `cut_type` — not page numbers.

On the lower-level `cognee.search()` API, `include_references` is documented as default **False**.

Sources:
- https://docs.cognee.ai/guides/search-basics (Citation and Source Tracking)
- https://docs.cognee.ai/python-api/search (`include_references` parameter)

### Summaries

`SearchType.SUMMARIES` returns pre-generated hierarchical summaries (`id`, `text`). Docs position it for overviews/abstracts, not as an automatic footnote on every first answer.

Source: https://docs.cognee.ai/python-api/search-type and https://docs.cognee.ai/guides/search-basics

### Deeper / next-tier search (Cognee types)

Documented deeper modes:

| Type | What it is | Cognee's measured median latency (their small corpus, gpt-5-mini, LanceDB+Kuzu — **not** our VPS) |
| --- | --- | --- |
| `HYBRID_COMPLETION` (default) | First-answer hybrid | ~3.8 s sessions off / ~20 s with default sessions |
| `GRAPH_COMPLETION_DECOMPOSITION` | Split query, retrieve per subquery, synthesize | ~60 s |
| `GRAPH_COMPLETION_CONTEXT_EXTENSION` | Iterative widen subgraph | ~60 s |
| `GRAPH_COMPLETION_COT` | Multi-hop chain-of-thought, default `max_iter=4` | ~200 s / 14 LLM calls |
| `AGENTIC_COMPLETION` | Skills/tools, up to `max_iter=6` | estimated 60–200 s |
| `FEELING_LUCKY` | LLM picks a type | ~40 s; docs say prefer a specific type for latency-sensitive production |

`recall()` auto-router can pick `GRAPH_COMPLETION_COT` when the query looks like chain-of-thought; it does **not** select `SUMMARIES`, `CHUNKS`, `RAG_COMPLETION`, or `AGENTIC_COMPLETION`. Docs: to go deeper, start with `GRAPH_COMPLETION`, escalate to decomposition or COT; lower `max_iter` via `retriever_specific_config`.

Postgres graph backend: Cypher-based types are unsupported; COT/hybrid/RAG/chunks do not require Cypher.

Sources:
- https://docs.cognee.ai/python-api/search-type
- https://docs.cognee.ai/core-concepts/main-operations/legacy-operations/search
- https://docs.cognee.ai/core-concepts/main-operations/recall

### LLM / embeddings for AvalAI

Cognee sends LLM traffic through LiteLLM. OpenAI-compatible gateways use `LLM_PROVIDER="custom"`, `LLM_MODEL="openai/<model-id>"`, `LLM_ENDPOINT` as the `/v1` base URL, `LLM_API_KEY` as the bearer token. Embedding providers include `openai_compatible` (distinct from `LLM_PROVIDER` values).

Source: https://docs.cognee.ai/setup-configuration/llm-providers

## Evidence vs inference

- **Evidence:** demo warning and CYPHER unsupported are explicit in Cognee docs/README.
- **Inference:** Evidence block + chunk text can satisfy "citation + short excerpt"; it may not satisfy "accurate book/page citation" or a separately written summary.
- **Inference:** `GRAPH_COMPLETION_COT` is the closest named match to "next-tier in-depth analysis"; it is a second `query_type`, not a product toggle named next-tier. Default COT latency is likely too slow for the first answer; a user-triggered second search with reduced `max_iter` is the plausible MVP mapping.
- **Unknown:** Farsi quality, page-level citations from typical book PDFs, whether `postgres_demo` is stable enough for a 6-day B2B session, real latency on our VPS/AvalAI, whether Cognee's UI profile is usable in Farsi.

## Supported vs must-build

**Cognee already has:** ingest files/URLs into a dataset; Postgres (+ pgvector + demo graph); `recall()` answers; default Evidence citations (document name + chunk snippet); `SUMMARIES` as a separate search; COT/decomposition/context-extension as deeper search types; Docker images and a postgres compose profile; custom OpenAI-compatible LLM endpoint.

**We must still decide/build:** accept demo graph-on-Postgres or not; first-answer vs next-tier `query_type` mapping; any extra summary copy beyond Evidence; page/book citation rules if Evidence is not enough; Farsi prompts; AvalAI model IDs; VPS compose (API ± UI ± postgres profile); turning `AUTO_FEEDBACK` off if first-answer latency matters (README performance knobs).
