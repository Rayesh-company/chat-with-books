# chat-with-books

Farsi Q&A over a fixed Book set through Cognee. The whole memory layer stays on Postgres (ADR-0001). Chat goes to Z.AI's GLM Coding Plan at `https://api.z.ai/api/coding/paas/v4`; embeddings stay on AvalAI at `https://api.avalai.ir/v1` (ADR-0002). The coding-plan endpoint is documented for coding tools; other agent use is best-effort under Z.AI's usage policy.

The Book set is one Book: طرح کلی اندیشۀ اسلامی در قرآن (`tarhe-kolli.pdf`). Keep it at the repo root. It is not committed.

## Local stack

Postgres is always on. We do not use Cognee's `--profile postgres`, because this product never runs without Postgres. The optional UI is `--profile ui`.

```powershell
copy .env.example .env
# Set LLM_API_KEY to the Z.AI key and EMBEDDING_API_KEY to the AvalAI key.
docker compose up -d
curl.exe -f http://localhost:8000/health
```

API docs: `http://localhost:8000/docs`.

## Session UI

A Farsi-first sheet for first answers. It proxies `recall()` to Cognee on this machine. It is not Next-tier search, and it is not Cognee `--profile ui`.

```powershell
powershell -ExecutionPolicy Bypass -File start-ui.ps1
```

`start-ui.ps1` reads `LLM_API_KEY` from `.env` into the process env — `serve.py` takes the key from the host env, never from `.env` (that file is compose-only), and without it the Quoted answer falls back to the Evidence list — then runs `python ui/serve.py`.

Open `http://localhost:8765`. Cognee must already be up on port 8000 with `tarhe-kolli` ingested.

The sheet asks recall() with `stream: true` and renders the answer as it arrives. `delta` frames paint a live preview; `final` is the only authoritative render and the preview is discarded. `reset` clears the preview after an LLM retry. An `error` frame is not terminal: the sheet keeps reading and prefers `final`. `ui/serve.py` relays `text/event-stream` unbuffered (HTTP/1.0, connection-close delimited). «نخستین توکن» is TTFT: submit to first token rendered; with no deltas it falls back to the first render from `final`.

After the streamed answer renders, the sheet can swap it for the **Quoted answer**: a document the composer writes as AI Filler paragraphs interleaved with Quote paragraphs — sentences taken **verbatim from the Book's Evidence passages** for the same question (ADR-0003). The sheet parses each Evidence bullet into locator + quoted passage and POSTs them with the question and the streamed answer to `ui/serve.py`'s `/quoted-answer`, which asks `glm-5.3-flash` on the Z.AI coding endpoint (`LLM_API_KEY` from the host env; never shipped to the browser) to write the blocks: `heading`, `filler`, `quote`. A verbatim guard drops every quote sentence whose normalized letter stream does not occur in its claimed passage (PDF text-layer `\b` noise, ZWNJ, kashida, and Arabic ي/ك variants collapse before comparing), so nothing paraphrased is shown as quoted; a failed sentence drops alone and its paragraph survives. Each Quote paragraph draws from exactly one passage, renders highlighted, ends with its exact pages from the Evidence locator («صفحات 740 تا 745»; a passage without page markers cites the Book alone, never an invented page), and each sentence is focusable — hover or keyboard focus shows a Farsi tooltip (Book title + pages). The swap happens only when the guarded document holds at least one Quote paragraph and one Filler or heading; if the composer fails, times out, or misses that threshold, the streamed answer and the Evidence citations stay — the sheet is never left empty. This runs strictly after the streamed answer: TTFT, streaming, the `Evidence:` contract, and the model pins are untouched (AC contract of #22).

UI (optional Cognee chrome):

```powershell
docker compose --profile ui up -d
curl.exe -f http://localhost:3000
```

Chat models are pinned in `compose.yaml`: `glm-5.3-flash` on `cognee` (first answers), `glm-5.3` on `cognee-next-tier` (`environment:` there overrides `.env`, so `.env` carries keys, not models). Embeddings are `text-embedding-3-small` on AvalAI. Change an `LLM_MODEL` in `compose.yaml` only after a POST to `https://api.z.ai/api/coding/paas/v4/chat/completions` with that model ID succeeds on this key.

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

A Session operator asks a Farsi question against the ingested Book. First answer is Cognee `recall()` with `HYBRID_COMPLETION`. It is not Next-tier search. Pin `searchType`. Do not pass `null` (that auto-routes and can pick `GRAPH_COMPLETION_COT`). Cognee's HTTP `includeReferences` default is false. Still pass `true`. Citation is Cognee's `Evidence:` block (Book identity plus a quoted passage). Compose starts Cognee through `enable_farsi_evidence.py` so that overlap keeps Farsi terms, not only Latin `[a-z0-9]`; the same patch widens the snippet window to 600 chars (Cognee default: 160) so quoted passages hold complete sentences for the Quoted answer's verbatim guard. Do not invent a citation formatter. Do not treat CHUNKS as the product Citation. The snippet is the short summary. Do not add a second précis. A new question starts a new first answer, not a Next-tier search. Omit `sessionId` on this path.

This path streams: pass `stream: true`. Deltas are preview only and never contain the `Evidence:` block; the `final` frame carries the same JSON array as the non-streaming reply, and citation splitting runs on `final`'s `[0].text`. Token deltas need `LLM_ANSWER_STREAMING: "true"` in compose (Cognee env). With the flag off the stream still works with zero deltas, and TTFT measures submit to first render from `final`.

Chat model for this path is `glm-5.3-flash` on Z.AI's GLM Coding Plan (PM call, 2026-09-09: flash for first answers, `glm-5.3` reserved for Next-tier; supersedes the 2026-09-08 same-model call made when chat was on AvalAI).

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

It runs on the second Cognee service (`cognee-next-tier`, port `8001`) with the Z.AI chat model `glm-5.3` on the GLM Coding Plan endpoint (smoke-checked via `/chat/completions`). The tiers use different models since 2026-09-09 — `glm-5.3-flash` first answers, `glm-5.3` here. They stay separate services so a multi-minute Next-tier search on `8001` never blocks the first-answer path on `8000`: Cognee's per-stage `LLM_QUERY_MODEL` override cannot draw that line — it hits every search completion, first answer included — and one service cannot serve both tiers independently. Both read the same Postgres memory.

```powershell
curl.exe -sS -X POST http://localhost:8001/api/v1/recall -H "Content-Type: application/json" --data-raw '{"searchType":"GRAPH_COMPLETION_COT","query":"اندیشه اسلامی در قرآن چه طرحی دارد؟","datasets":["tarhe-kolli"],"includeReferences":true}'
```

Same question as the first answer. Do not stream this path; the operator waits for the JSON. Citations may still appear; they follow the first-answer `Evidence:` contract. Pytest locks this recorded next-tier contract; a green suite does not mean the Next-tier search returned a synthesis.

## Tests

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = "1"
python -m pytest
```
