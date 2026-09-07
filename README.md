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

UI (optional):

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

A Session operator asks a Farsi question against the ingested Book. First answer is Cognee `recall()` with `HYBRID_COMPLETION`. It is not Next-tier search. Pin `searchType`. Do not pass `null` (that auto-routes and can pick `GRAPH_COMPLETION_COT`). Cognee's HTTP `includeReferences` default is false. Pass `true` so Evidence is appended. The Evidence block is the Citation: Book identity plus a quoted passage. That snippet is the short summary. Do not add a second précis. Omit `sessionId` so a new question is a new first answer.

Trial chat model for this path is `gpt-5.4-mini` (already in `.env` / compose).

Pytest locks this recorded first-answer contract. A green suite does not mean recall returned an answer or Evidence.

```powershell
curl.exe -sS -X POST http://localhost:8000/api/v1/recall -H "Content-Type: application/json" --data-raw '{"searchType":"HYBRID_COMPLETION","query":"اندیشه اسلامی در قرآن چه طرحی دارد؟","datasets":["tarhe-kolli"],"includeReferences":true}'
```

## Tests

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = "1"
python -m pytest
```
