# chat-with-books

Farsi Q&A over a fixed Book set through Cognee. The whole memory layer stays on Postgres (ADR-0001). Chat and embeddings go to AvalAI at `https://api.avalai.ir/v1`.

The Book set is one PDF: `tarhe-kolli.pdf` (طرح کلی اندیشۀ اسلامی در قرآن). Keep it at the repo root. It is not committed.

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

Retrieval smoke. `CHUNKS` should return hits whose `document_name` is `tarhe-kolli`, not an empty index. First search can sit several minutes on session analysis before chunks return.

```powershell
curl.exe -sS -X POST http://localhost:8000/api/v1/search -H "Content-Type: application/json" --data-raw '{"searchType":"CHUNKS","query":"اندیشه اسلامی","datasets":["tarhe-kolli"],"topK":5}'
```

## Tests

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = "1"
python -m pytest
```
