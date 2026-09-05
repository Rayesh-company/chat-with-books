# chat-with-books

Farsi Q&A over a fixed Book set through Cognee. The whole memory layer stays on Postgres (ADR-0001). Chat and embeddings go to AvalAI at `https://api.avalai.ir/v1`.

This ticket only brings the local runtime up. Book ingest is a later step.

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
```

Then open `http://localhost:3000`.

Trial chat model is `gpt-5.4-mini`. Embeddings are `text-embedding-3-small`. Change `LLM_MODEL` in `.env` only after `GET https://api.avalai.ir/v1/models` shows a different ID on this account.

## Tests

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = "1"
python -m pytest
```
