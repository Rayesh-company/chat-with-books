# chat-with-books

Farsi Q&A over a fixed Book set through Cognee. The whole memory layer stays on Postgres (ADR-0001). Chat and embeddings go to AvalAI at `https://api.avalai.ir/v1`.

The Book set is one Book: طرح کلی اندیشۀ اسلامی در قرآن (`tarhe-kolli.pdf`). Keep it at the repo root. It is not committed.

## Local stack

Postgres is always on. We do not use Cognee's `--profile postgres`, because this product never runs without Postgres. The optional UI is `--profile ui`.

```powershell
copy .env.example .env
# Set LLM_API_KEY and EMBEDDING_API_KEY to the same AvalAI key.
docker compose up -d
curl.exe -f http://localhost:8000/health
```

API docs: `http://localhost:8000/docs`.

## Session UI

A Farsi-first sheet for first answers. It proxies `recall()` to Cognee on this machine. It is not Next-tier search, and it is not Cognee `--profile ui`.

```powershell
python ui/serve.py
```

Open `http://localhost:8765`. Cognee must already be up on port 8000 with `tarhe-kolli` ingested.

The sheet asks recall() with `stream: true` and renders the answer as it arrives. `delta` frames paint a live preview; `final` is the only authoritative render and the preview is discarded. `reset` clears the preview after an LLM retry. An `error` frame is not terminal: the sheet keeps reading and prefers `final`. `ui/serve.py` relays `text/event-stream` unbuffered (HTTP/1.0, connection-close delimited). «نخستین توکن» is TTFT: submit to first token rendered; with no deltas it falls back to the first render from `final`.

UI (optional Cognee chrome):

```powershell
docker compose --profile ui up -d
curl.exe -f http://localhost:3000
```

Trial chat model is `gpt-5.4-mini`. Embeddings are `text-embedding-3-small`. Change `LLM_MODEL` in `.env` only after `GET https://api.avalai.ir/v1/models` shows a different ID on this account.

## Book ingest

Dataset name: `tarhe-kolli`. Upload only this Book.

```powershell
curl.exe -f -X POST http://localhost:8000/api/v1/remember -F "data=@tarhe-kolli.pdf" -F "datasetName=tarhe-kolli" -F "run_in_background=true"
curl.exe -sS http://localhost:8000/api/v1/datasets/status
```

Poll `/api/v1/datasets/status` until the dataset is `completed` (or `DATASET_PROCESSING_COMPLETED`). Graph build on `postgres_demo` can take hours.

Pytest locks this recorded ingest contract. A green suite does not mean remember has run or that CHUNKS returned hits.

Session operator smoke on a live host: POST `CHUNKS` and check Book (`document_name`) is `tarhe-kolli`. First search can sit several minutes on Cognee's pre-search step before chunks return.

```powershell
curl.exe -sS -X POST http://localhost:8000/api/v1/search -H "Content-Type: application/json" --data-raw '{"searchType":"CHUNKS","query":"اندیشه اسلامی","datasets":["tarhe-kolli"],"topK":5}'
```

## First answer

A Session operator asks a Farsi question against the ingested Book. First answer is Cognee `recall()` with `HYBRID_COMPLETION`. It is not Next-tier search. Pin `searchType`. Do not pass `null` (that auto-routes and can pick `GRAPH_COMPLETION_COT`). Cognee's HTTP `includeReferences` default is false. Still pass `true`. Citation is Cognee's `Evidence:` block (Book identity plus a quoted passage). Compose starts Cognee through `enable_farsi_evidence.py` so that overlap keeps Farsi terms, not only Latin `[a-z0-9]`. Do not invent a citation formatter. Do not treat CHUNKS as the product Citation. The snippet is the short summary. Do not add a second précis. A new question starts a new first answer, not a Next-tier search. Omit `sessionId` on this path.

This path streams: pass `stream: true`. Deltas are preview only and never contain the `Evidence:` block; the `final` frame carries the same JSON array as the non-streaming reply, and citation splitting runs on `final`'s `[0].text`. Token deltas need `LLM_ANSWER_STREAMING: "true"` in compose (Cognee env). With the flag off the stream still works with zero deltas, and TTFT measures submit to first render from `final`.

Trial chat model for this path is `gpt-5.4-mini` (already in `.env` / compose).

Pytest locks this recorded first-answer contract. A green suite does not mean recall returned an answer or Evidence.

```powershell
curl.exe -sS -X POST http://localhost:8000/api/v1/recall -H "Content-Type: application/json" --data-raw '{"searchType":"HYBRID_COMPLETION","query":"اندیشه اسلامی در قرآن چه طرحی دارد؟","datasets":["tarhe-kolli"],"includeReferences":true}'
```

Streamed variant (`-N` so frames print live):

```powershell
curl.exe -N -sS -X POST http://localhost:8000/api/v1/recall -H "Content-Type: application/json" --data-raw '{"searchType":"HYBRID_COMPLETION","query":"اندیشه اسلامی در قرآن چه طرحی دارد؟","datasets":["tarhe-kolli"],"includeReferences":true,"stream":true}'
```

## Next-tier search

In the same Session, on the same question, the Session operator can run a Next-tier search: a second Cognee search that synthesizes across more than one stretch of the Book. A new question is a new first answer, not a Next-tier search. Slow is allowed here; it must not steal the first-answer path.

It is Cognee `SearchType.GRAPH_COMPLETION_COT` (default `max_iter=4`). Not `FEELING_LUCKY`, not `AGENTIC_COMPLETION`. If live latency is ever unusable, the fallback is `GRAPH_COMPLETION_DECOMPOSITION` — switch only after recording that choice.

It runs on the second Cognee service (`cognee-next-tier`, port `8001`) with the stronger AvalAI chat model `glm-5.3` (confirmed on `/v1/models`). The first-answer service on port `8000` keeps `gpt-5.4-mini`. Cognee's per-stage `LLM_QUERY_MODEL` override cannot separate the two paths — every search completion, first answer included, runs in the `query` pipeline stage — so the stronger model gets its own service reading the same Postgres memory.

```powershell
curl.exe -sS -X POST http://localhost:8001/api/v1/recall -H "Content-Type: application/json" --data-raw '{"searchType":"GRAPH_COMPLETION_COT","query":"اندیشه اسلامی در قرآن چه طرحی دارد؟","datasets":["tarhe-kolli"],"includeReferences":true}'
```

Same question as the first answer. Do not stream this path; the operator waits for the JSON. Citations may still appear; they follow the first-answer `Evidence:` contract. Pytest locks this recorded next-tier contract; a green suite does not mean COT returned a synthesis.

## Tests

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = "1"
python -m pytest
```
