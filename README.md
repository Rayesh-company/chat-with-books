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

### Phone gate

The sheet identifies a Customer by a phone number (`#phone` on the form; `inputmode="tel"`, remembered in `localStorage`). The number rides to `ui/serve.py` in an `X-Session-Phone` header on both `/api/v1/recall` and `/quoted-answer` — the proxy never forwards it to Cognee. Persian/Arabic-Indic digits are normalized; anything that is not 10–13 digits is rejected with 400. Each number gets **five chats a day** (server-local day, counted in `ui/usage.sqlite3`): a chat is one ask — phase 1 records it and answers 429 when the day's five are spent; phase 2 (`/quoted-answer`) belongs to that chat, so it needs a phone with at least one chat today and neither counts nor checks the limit itself (the fifth chat's own swap must pass). Honor-system — no SMS verification; the gate stops casual credit-burn, not a determined caller.

The sheet asks recall() with `stream: true` and renders the answer as it arrives. `delta` frames paint a live preview; `final` is the only authoritative render and the preview is discarded. `reset` clears the preview after an LLM retry. An `error` frame is not terminal: the sheet keeps reading and prefers `final`. The streamed answer renders as markdown — headings, bold, bullets — during the preview and at `final`; literal `#`/`**` characters never show. `ui/serve.py` relays `text/event-stream` unbuffered (HTTP/1.0, connection-close delimited). «نخستین توکن» is TTFT: submit to first token rendered; with no deltas it falls back to the first render from `final`.

After the streamed answer renders, the sheet can swap it for the **Quoted answer**: a document the composer writes as paragraphs that each interleave AI-written Farsi with verbatim Book sentences — one unit of AI text, embedded quote, and the paragraph's cited pages at its end (`docs/example.txt` is the visual parity target; PM format call, 2026-09-10) — the quotes taken **verbatim from the Book's Evidence passages** for the same question (ADR-0003). The sheet parses each Evidence bullet into locator + quoted passage and POSTs them with the question and the streamed answer to `ui/serve.py`'s `/quoted-answer`, which makes two sequential `glm-5.3-flash` calls on the Z.AI coding endpoint (`LLM_API_KEY` from the host env; never shipped to the browser), each with its own 240 s timeout, an explicit output ceiling (`max_tokens` 16384, pinned in source — the unpinned endpoint default ran a long document out of room mid-write, 2026-09-10, and reasoning tokens count inside the ceiling), and the endpoint's default temperature: a **planner** call with thinking enabled outlines the document — section headings, each paragraph's point, which passages to weave, where one paragraph weaves several passages — and a **writer** call with thinking disabled follows that plan to write the blocks: `heading`, `paragraph` (a `parts` list of AI text runs and quote parts). The planner plans from the question and passages alone — the draft answer is held back from it — and keeps the plan under 150 words (PM call, 2026-09-10, after live phase 2 measured ~295 s against the estimate: reasoning time scales with the brief). Reasoning is on only for planning (document structure, cross-passage weaving) and off for writing, so copied sentences survive the verbatim guard; measured 2026-09-10, reasoning cost ~84 s and writing ~17 s, so phase 2 was estimated at roughly 100 s. If the planner fails, times out, or returns nothing usable, the writer still runs without the plan — the single-call shape — so planning never breaks the fallback. A verbatim guard drops every quote sentence whose normalized letter stream does not occur in its claimed passage (PDF text-layer `\b` noise, ZWNJ, kashida, and Arabic ي/ك variants collapse before comparing), so nothing paraphrased is shown as quoted; a failed sentence drops alone and its paragraph survives. Every paragraph must hold at least one surviving quote and some AI text (no pure-AI, no bare-quote paragraphs), and a paragraph may weave quotes from several passages. Each quote sentence carries a resting clay highlight and is focusable — hover or keyboard focus shows a Farsi tooltip (Book title + that passage's first page; a passage without page markers cites the Book alone, never an invented page) — and the paragraph ends with the exact chunk-page range of every passage it quoted, in order («صفحات 740 تا 745»; the first-page-only end label lasted one smoke before the PM reversed it, 2026-09-10 — the tooltip keeps the first page). After the swap the whole Evidence section — «استناد» heading and list — is hidden: the answer itself carries the citations, and a new question brings the section back. While the composer runs, a pulsing «در حال نوشتن پاسخ استنادی…» status marks phase 2 under the streamed answer, and the elapsed clock keeps counting until the swap or fallback settles — the timer times the whole pipeline, not just the stream (PM call, 2026-09-10). The writer is asked for at least eight quote paragraphs when the passages support them (PM call, 2026-09-10, raised from five the same night) — never inventing a quote to reach the count. The swap happens only when the guarded document holds at least two quoting paragraphs, or one plus a heading; if the composer fails, times out, or misses that threshold, the streamed answer and the Evidence citations stay — the sheet is never left empty. This runs strictly after the streamed answer: TTFT, streaming, the `Evidence:` contract, and the model pins are untouched (AC contract of #22).

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

## VPS deploy

The public deployment is one Ubuntu VPS running the full stack behind **https://booksai.rayesh-team.ir**: the compose services (Postgres, Cognee `8000`, next-tier `8001`, all loopback-bound) plus the Session sheet under a systemd unit `cwb-session.service` (`User=ubuntu`, `EnvironmentFile` pointing at the repo's `.env` for `LLM_API_KEY`, `SESSION_UI_HOST=127.0.0.1`, `SESSION_UI_PORT=8765`). Nothing binds a public port anymore (2026-09-10): nginx fronts the domain — `/etc/nginx/sites-available/booksai.rayesh-team.ir`, proxying to `127.0.0.1:8765` with `proxy_buffering off` and 600 s read/send timeouts, because the SSE stream and the ~140 s `/quoted-answer` POST both ride it — and the Let's Encrypt cert (`certbot --nginx`, auto-renews) terminates TLS on the VPS. The Cloudflare DNS record is **DNS-only (grey cloud)**, pointing straight at the VPS: TLS is the origin's job and no Cloudflare proxy sits in the path. If that record is ever flipped to proxied (orange cloud), Cloudflare's ~100 s origin timeout will cut the silent `/quoted-answer` POST with a 524 — phase 2 would need to stream heartbeat bytes before its JSON, a seam that does not exist today. The VPS shares the host with unrelated projects (`mitgrat-*`, `mcdc-web`), so before binding anything check `ss -tlnp` — every service keeps its unique port and never assumes the repo defaults are free.

Deploy is tarball-shaped (the VPS copy is not a git checkout): tar the tree locally excluding `.git`, `.env`, the Book PDF and caches, `pscp`/`scp` it over, swap the tree (keep the remote `.env`), then `sudo systemctl restart cwb-session`. If `docker/enable_farsi_evidence.py` changed, restart the Cognee container too (`docker restart chat-with-books-cognee`) — the snippet window applies at search time, so no re-ingest is needed; the bind mount re-reads the file on container start.

The Book dataset is ingested once (`DATASET_PROCESSING_COMPLETED` in `/api/v1/datasets/status`); re-ingest is not part of a code deploy.

## Tests

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = "1"
python -m pytest
```
