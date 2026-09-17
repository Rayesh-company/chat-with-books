# chat-with-books — Architecture & Data Guide

*A self-study document. Read this top to bottom and you will understand what the
platform is, how every request flows, where the data lives, and why each piece
looks the way it does. Live numbers were captured 2026-09-12 from the local
stack on this machine.*

---

## Table of contents

1. [What this platform is](#1-what-this-platform-is)
2. [Bird's-eye architecture](#2-birds-eye-architecture)
3. [The five containers](#3-the-five-containers)
4. [The three external AI services](#4-the-three-external-ai-services)
5. [The memory layer — one Postgres for everything](#5-the-memory-layer--one-postgres-for-everything)
6. [Current data — what is actually stored right now](#6-current-data--what-is-actually-stored-right-now)
7. [The answer pipeline — one ask, three phases](#7-the-answer-pipeline--one-ask-three-phases)
8. [Research Mode — the Wayfinder chat, a job per turn](#8-research-mode--the-wayfinder-chat-a-job-per-turn)
9. [How the data got in — the ingest pipeline](#9-how-the-data-got-in--the-ingest-pipeline)
10. [Configuration map — who reads what](#10-configuration-map--who-reads-what)
11. [Access control — phone gate, quotas, turn registry](#11-access-control--phone-gate-quotas-turn-registry)
12. [Local quirks on this machine](#12-local-quirks-on-this-machine)
13. [Known failure modes](#13-known-failure-modes)
14. [ADR index — the decisions behind the design](#14-adr-index--the-decisions-behind-the-design)
15. [Glossary — the project's own vocabulary](#15-glossary--the-projects-own-vocabulary)

---

## 1. What this platform is

**chat-with-books** is a Farsi question-answering product for a B2B customer.
It answers questions **only from two fixed Farsi books**:

| Book (Farsi title) | Dataset name | Pages | Chunks |
|---|---|---|---|
| طرح کلی اندیشۀ اسلامی در قرآن (*The General Scheme of Islamic Thought in the Quran*) | `tarhe-kolli` | 862 | 243 |
| انسان ۲۵۰ ساله (*The 250-Year-Old Human*) | `70143-336` | 376 | 98 |

The core promise: every answer is built from **verbatim sentences quoted from
the books**, each carrying a **citation** (book identity + page numbers). The
AI never writes an uncited claim as if it came from a book — AI-written text
("filler") is always visually distinct from quoted book text, and quoted text
is machine-verified to exist word-for-word in the source passage (the
**verbatim guard**).

One user session is called a **Session**, and one question triggers a
three-phase answer pipeline:

1. **پاسخ اول** (first answer) — ~10 verbatim book sentences that answer the question.
2. **پاسخ استنادی** (quoted answer) — a flowing Farsi document weaving those quotes into AI-written prose.
3. **حالت پژوهش** (Research Mode) — an operator-started, multi-turn guided research conversation seeded by the same question and its Evidence pool.

The heavy lifting (retrieval, knowledge-graph memory, embeddings) is done by
**Cognee**, an open-source "memory layer" run as prebuilt Docker services. The
product code on top is a thin, stdlib-only Python orchestration sheet
(`ui/serve.py`) that turns Cognee's raw retrieval into the three-phase,
citation-guarded experience.

---

## 2. Bird's-eye architecture

Everything runs as Docker containers on one Docker network
(`chat-with-books`). Public images are pulled; only the session sheet is a
custom local image.

```
                        YOU (browser)
                              │
              ┌───────────────┴────────────────┐
              │                                │
              ▼                                ▼
   http://localhost:8765            http://localhost:3000
  ┌─────────────────────────┐     ┌─────────────────────────┐
  │  chat-with-books-       │     │  chat-with-books-ui     │
  │  session                │     │  (cognee/cognee-ui)     │
  │  = the Chat UI (Farsi   │     │  = Cognee's own admin   │
  │    sheet, 3 phases)     │     │    UI (optional chrome) │
  │  image: custom, stdlib  │     └────────────┬────────────┘
  └───────────┬─────────────┘                  │
              │ serve.py orchestrates:         │
              │  /quote-selection              │
              │  /quoted-answer                │
              │  /research/* (+ turn registry) │
              ▼                                ▼
   ┌──────────────────────┐        ┌──────────────────────┐
   │ chat-with-books-     │        │ chat-with-books-     │
   │ cognee               │        │ cognee-next-tier     │
   │ :18000→8000          │        │ :8001→8000           │
   │ model glm-5.3-FLASH  │        │ model glm-5.3-FLASH  │
   │ = fast first answers │        │ = research searches  │
   └──────────┬───────────┘        └──────────┬───────────┘
              │       both talk to the SAME memory   │
              └───────────────┬──────────────────────┘
                              ▼
                 ┌─────────────────────────┐
                 │ chat-with-books-postgres│
                 │ pgvector/pgvector:pg17  │
                 │ :5432                   │
                 │ relational + pgvector   │
                 │ + graph + cache         │
                 └─────────────────────────┘

        External AI (HTTPS, out to the internet):
          └── api.avalai.ir/v1                 → chat (glm-5.3-flash, both tiers — moved off
                                               Z.AI 2026-09-13, coding-plan quota exhausted)
                                               → embeddings (text-embedding-3-large, 3072d)
                                               → extraction/summarization (glm-5.3-flash)
```

**Why two Cognee services?** This is the single most important architectural
decision to internalize: the first answer must feel fast, while a research
gather is allowed to take many minutes. Cognee can't split per-request model choice or
priorities within one service, so the stack runs **two separate Cognee
containers pointed at the same Postgres memory**:

- `cognee` — pinned to `glm-5.3-flash`. Serves phase 1 (first answer). Never blocked by a long research gather.
- `cognee-next-tier` — pinned to `glm-5.3-flash` as well (both tiers moved to flash 2026-09-13, operator call — deep-study latency was the pain). Serves only Research Mode's searchers. The tier split now buys **service isolation** — a multi-minute gather here cannot slow the first-answer path — rather than a model difference.

Both read the same Postgres, so the research searchers see exactly the same memory as
the first answer — no data duplication.

---

## 3. The five containers

| Container | Image | Host port | Container port | Purpose | Health check |
|---|---|---|---|---|---|
| `chat-with-books-postgres` | `pgvector/pgvector:pg17` | 5432 | 5432 | The entire memory layer | `pg_isready` |
| `chat-with-books-cognee` | `cognee/cognee:main` | **18000** (remapped, see §12) | 8000 | Tier-1 Cognee — first answers, ingest | `curl /health` |
| `chat-with-books-cognee-next-tier` | `cognee/cognee:main` | 8001 | 8000 | Tier-2 Cognee — Research Mode's searches | `curl /health` |
| `chat-with-books-session` | `chat-with-books-session:latest` (custom, from `session-image.tar.gz`) | 8765 | 8765 | The Farsi chat sheet + all orchestration | `/livez` (local); `/health` proxies to Cognee |
| `chat-with-books-ui` | `cognee/cognee-ui:latest` | 3000 | 3000 | Cognee's own admin UI (optional, `ui` profile) | HTTP 200 |

Profiles: `session` (the chat sheet) and `ui` (Cognee chrome) are optional;
Postgres and the two Cognee services always start. On this machine all five
run under project `chat-with-books`.

Custom image details: `chat-with-books-session` is built from `ui/Dockerfile`
— a base Python interpreter plus `serve.py` and `index.html`, **nothing
installed** (stdlib only). The `LLM_API_KEY` is injected by compose
interpolation at runtime, never baked into the image. The reader's pdf.js
is vendored at `ui/vendor/` (copied into the image), and the Book PDFs ride
a `./books:/books:ro` bind mount served by `serve.py`'s allowlisted
`/books/<dataset>.pdf` and `.pages.json` routes (ADR-0007) — the PDFs
themselves stay out of git and are provisioned from the Cognee storage
export (`cognee-file-storage/<hash>/<book>.pdf` → `books/<dataset>.pdf`).

One volume each: `postgres_data` (the memory), `cognee_data`/`cognee_system`
(raw files, caches), `session_quota` (the phone-gate SQLite DB at
`/data/usage.sqlite3`).

---

## 4. The three external AI services

The stack calls out to exactly two providers, in three distinct roles:

| Role | Provider / endpoint | Model | Used by | Rate limits set in compose |
|---|---|---|---|---|
| **Chat — first answers** | AvalAI `api.avalai.ir/v1` (moved off Z.AI 2026-09-13 — coding-plan weekly quota exhausted, reset Sep 15) | `glm-5.3-flash` | `cognee` recall | 240 req/60s, min-retry 6×/1800s |
| **Chat — Research Mode** | AvalAI | `glm-5.3-flash` (both tiers since 2026-09-13) | `cognee-next-tier` | 60 req/60s |
| **Embeddings** | AvalAI `api.avalai.ir/v1` | `text-embedding-3-large`, **3072 dims** | query embedding at ask time; embedding at ingest | 120 req/60s, batch 32 |
| **Extraction & summarization (ingest only)** | AvalAI | `glm-5.3-flash` | Cognee's graph-build stages | 200 req/60s, retries 6×/1800s |

Why extraction goes to AvalAI and not Z.AI: during ingest Z.AI's coding
endpoint repeatedly 429'd (its quota is shared with coding tools). AvalAI
carries the GLM model family too, with a 250 RPM / 4M TPM budget, so the
graph-build burst traffic was rerouted there. The **query** stage (real user
questions) stays on Z.AI per the product's model pins.

Embeddings: `text-embedding-3-large` at 3072 dimensions. **This choice is
frozen into the stored vectors** — see §13 for what happens if the config
drifts.

---

## 5. The memory layer — one Postgres for everything

ADR-0001 locks this permanently: **the whole memory layer is one Postgres
database** (`cognee_db`, user `cognee`, password `cognee`). Cognee officially
recommends Kuzu/Neo4j for graphs in production, but this project rejected a
second database outright — relational metadata, vectors, graph, and caches all
live in Postgres. Cognee's `postgres_demo` graph provider is an accepted,
documented risk.

What that means concretely — the tables fall into five groups:

| Group | Tables | What they hold |
|---|---|---|
| **Vector index** | `"DocumentChunk_text"` | One row per book chunk: `id`, `payload` (JSON: text, document name, page metadata), `vector vector(3072)`. **This is the retrieval heart.** |
| **Knowledge graph** | `graph_node`, `graph_edge`, `graph_metadata`, `graph_metrics`, `graph_relationship_ledger`, `provenance_edge_evidence` | Entities extracted from the books and their typed relationships. |
| **Document registry** | `"TextDocument_name"`, `"TextSummary_text"`, `"Entity_name"`, `"EntityType_name"`, `"EdgeType_relationship_name"`, `data`, `datasets`, `dataset_database` | Cognee's per-dataset metadata and summaries. |
| **Session cache** | `cache_kv`, `cache_qa_entries`, `cache_session_context`, `cache_trace_entries`, `cache_usage_logs` | Cognee's QA cache and traces. |
| **Cognee plumbing** | `alembic_version`, `global_database_version`, `pipeline_runs`, acls/permissions tables, notebooks, … | Framework internals. |

Because everything is Postgres, **backup = one `pg_dump`** — exactly what the
`cognee_db.dump` file in this workspace is (332 MB). Restoring it onto a fresh
`pgvector/pgvector:pg17` container reproduces the entire memory, embeddings
included.

---

## 6. Current data — what is actually stored right now

Live counts captured 2026-09-12 after the handoff restore:

```
DOCUMENTS & CHUNKS (vector index)
┌──────────────────────────────┬─────────┬────────────────────────────────┐
│ Book                         │ Chunks  │ Chunking rule                  │
├──────────────────────────────┼─────────┼────────────────────────────────┤
│ tarhe-kolli  (862 pages)     │   243   │ ≤ 5 PDF pages/chunk            │
│                              │         │ → chunk_size 4100 tokens       │
│ 70143-336    (376 pages)     │    98   │ ≤ 5 PDF pages/chunk            │
│                              │         │ → chunk_size 3500 tokens       │
├──────────────────────────────┼─────────┼────────────────────────────────┤
│ Total                        │   341   │ all with 3072-dim vectors      │
└──────────────────────────────┴─────────┴────────────────────────────────┘
Chunks end at paragraph boundaries, so most are smaller than the cap.
(Calibration: Cognee's Farsi extraction ≈ 1150 / 980 chars per page
respectively — measured, not estimated.)

KNOWLEDGE GRAPH
  graph_node : 4,623 nodes
    ├─ Entity        3,794   (concepts/people/topics extracted by the LLM)
    ├─ DocumentChunk   341   (one node per chunk — links graph to text)
    ├─ TextSummary     341   (one summary per chunk)
    ├─ EntityType      145   (the type vocabulary)
    └─ TextDocument      2   (the two Books)

  graph_edge : 18,641 edges, top relationship types:
    contains     6,827   is_a        4,024   is_part_of   353
    made_from      341   part_of     145    discusses    109
    member_of      106   mentions     98    describes     81

DATASETS (Cognee registry): tarhe-kolli, 70143-336  — both completed.
RAW FILES: both extracted .txt files also restored into /cognee-storage/data/.
```

How to re-check any of this yourself:

```bash
# tables
docker exec chat-with-books-postgres psql -U cognee -d cognee_db -c '\dt'
# chunks per book
docker exec chat-with-books-postgres psql -U cognee -d cognee_db -c \
  "SELECT payload->>'document_name', count(*) FROM public.\"DocumentChunk_text\" GROUP BY 1"
# graph shape
docker exec chat-with-books-postgres psql -U cognee -d cognee_db -c \
  "SELECT type, count(*) FROM graph_node GROUP BY 1 ORDER BY 2 DESC"
```

---

## 7. The answer pipeline — one ask, three phases

A user opens `http://localhost:8765`, types a phone number (the "phone gate"),
and asks one Farsi question. That **one ask** triggers the whole pipeline; the
sheet shows each phase in its own tab.

### Phase 1 — first answer (پاسخ اول)

```
Browser ── POST /api/v1/recall ──▶ ui/serve.py ── proxy ──▶ cognee :8000
            {query, phone header}                          │
                                                           │ HYBRID_COMPLETION search:
                                                           │  1. embed query (AvalAI, 3072d)
                                                           │  2. pgvector similarity + graph context
                                                           │  3. glm-5.3-flash streams an answer
                                                           │     + "Evidence:" block
                                                           ▼
Sheet shows THREE things from the reply:
  a) streamed prose   → drives only the TTFT readout («نخستین توکن»), not displayed
  b) Evidence pool    → list of {reference (book+pages), passage} pairs,
                        rendered collapsed («همۀ نقل‌قول‌های بازیابی‌شده»)
  c) QUOTE SELECTION  → serve.py POSTs the pool to /quote-selection:
                        ONE glm-5.3-flash call (thinking OFF — this is copy-
                        matching, not reasoning) picks ~10 complete sentences
                        copied verbatim from the passages.
                        → VERBATIM GUARD: a sentence whose normalized letter
                          stream isn't inside its claimed passage is DROPPED.
                        → survivors capped at 12; < 4 ⇒ fallback to prose.
        ✅ Rendered first answer = the guarded Quote selection
           (each sentence highlighted, hover = citation tooltip)
```

Key contracts on this path:

- `searchType` is **pinned** to `HYBRID_COMPLETION` — never `null` (auto-route could pick a graph search) and never `CHUNKS` (chunks are not the product citation).
- `includeReferences: true` always — the `Evidence:` block is the citation source.
- The `Evidence:` block works in Farsi because compose starts Cognee through `docker/enable_farsi_evidence.py`, which also widens the passage snippet window from Cognee's 160 chars to 600 so whole sentences survive for phase 2.
- Streaming is on (`LLM_ANSWER_STREAMING=true`) but deltas only feed the TTFT metric; the `final` frame is the only authoritative render.

### Phase 2 — quoted answer (پاسخ استنادی)

Starts **in parallel** with the quote picker, on the same Evidence pool:

```
ui/serve.py /quoted-answer  (two sequential glm-5.3-flash calls, 240s timeout each,
                             max_tokens 16384 pinned)
  1. PLANNER  (thinking ON)  — outlines the document: headings, per-paragraph
                               points, which passages to weave where. ≤150 words.
                               Fails? → writer runs without a plan (never breaks).
  2. WRITER   (thinking OFF) — writes {heading, paragraph} blocks where each
                               paragraph = AI filler text + embedded VERBATIM
                               quotes + the paragraph's cited pages
                               («صفحات 740 تا 745»).
  → verbatim guard again on every quote sentence (letter-stream comparison,
    normalizing ZWNJ, kashida, Arabic ي/ك variants, PDF noise)
  → every paragraph must hold ≥1 surviving quote AND some AI text
  → output cut mid-JSON (finish_reason "length")? Salvage the parsed prefix +
    ONE continuation call, re-guard. `truncated: true` if still cut.
  → lands in phase 2's tab only if ≥2 quoting paragraphs (or 1 + heading);
    otherwise phase 1's answer stays. Sheet is never left empty.
```

The visual contract: quotes carry a clay highlight; hovering shows
*book title + first page*; each paragraph ends with the exact page range of
every passage it quoted. `docs/example.txt` is the parity target. Every
quote, Evidence line, and «منابع» entry is also **clickable**: it opens the
Book reader (کتاب‌خوان) on the passage's actual PDF page with the quoted
letters highlighted — the client re-runs the guard's normalized
letter-stream match, first against `books/<dataset>.pages.json` (which page
of the chunk's range), then against the rendered page's pdf.js text layer
rebuilt into reading order (which spans to paint). See ADR-0007.

### Round two (ADR-0010)

Five recorded frictions, fixed in place:

- **Phase-2 repair** — the writer gets ONE retry (same plan, no planner
  re-run) when the guarded document misses the swap threshold; the guard
  prunes headings whose section kept no surviving paragraph, and the
  threshold reads the pruned document. A flaky timeout or a paraphrasing
  writer no longer lands «پاسخ استنادی آماده نشد» without recourse, and a
  bare heading with no body never renders.
- **The widen** (`POST /recall-more`, `ui/recall_more.py`) — a
  «جست‌وجوی بیشتر» chip inside phase 1's collapsed pool: ONE broaden call
  (thinking ON, coverage reasoning over the pool's digests) proposes ≤2
  short Farsi facet queries, each searched once through the pinned
  `HYBRID_COMPLETION` searcher on the next tier; only the pool's NEW
  passages ride back (guard-normalized dedupe), `{"sources": []}` when
  nothing is new. Gated like phase 2; the sheet re-picks the Quote
  selection and re-runs phase 2 over the merged pool.
- **Citation landing** — quote spans register their passage under their
  locator; a «منابع» reference-line click looks it up and locates like any
  quote, and the labeled-range fallback tries the whole chunk range
  (capped at nine pages), not just its first three.
- **The ask's Book selection** — two pill toggles above the question; the
  recall proxy validates the body (`query` required, `datasets` =
  intersection with `BOOK_DATASETS`, resolved list in the set's order,
  missing means both) and the selection rides into the research session
  (`state["datasets"]`), whose gathers, recalls, and widens search just
  those Books. The UI is a convenience; the server is the contract.

### The user's quota

The ask (phase 1) is what records a "chat" — five per phone number per day.
Phases 2 and 3 belong to that chat and never count or check the limit
themselves. See §11.

---

## 8. Research Mode — the Wayfinder chat, a job per turn

Phase 3 is **حالت پژوهش** (Research Mode, ADR-0008): a guided, evidence-grounded
research conversation that replaced the one-shot Deep dive. The operator's first
message creates the research session — seeded with the ask's question and its
phase-1 Evidence pool — and each message is one turn:

```
Browser ── POST /research/message {text, question?, sources?, session_id?}
             ◀── 202 {"turn_id", "session_id"} at once
                  (the turn runs on its own thread in an in-process registry;
                   sessionStorage remembers the identities so a browser
                   refresh reconnects via /research/turn)

TURN LIFECYCLE (progress events in Farsi, polled every 2s):
  classifying ──▶ [route by intent] ──▶ planning/searching/writing ──▶ done
                                                                       │ or failed
  ONE classify call (glm-5.3-flash, thinking ON) reads the message +
  the compact research state + the last 6 turns → intent ∈ {casual,
  concept_learning, source_lookup, research_exploration, active_research,
  drafting, evidence_audit}. A malformed reply degrades to the
  conversational path — never crashes.

  CONVERSATION LAYER (casual/learning/lookup):
    ONE searcher over the message + ONE guarded writer pass over its pool;
    references cite exactly that pool. No research state change.

  JOURNEY LAYER (ADR-0009, exploration/active turns inside the research
  side — the spine the operations hang on):
    • Stage machine: orientation (name the destination) → mapping (survey
      the ground) → investigating → synthesizing → drafting. Moved ONLY
      by code on observable state (destination recorded, ≥2 open
      questions pending, evidence pooled, claims recorded, Brief
      written); the model never moves a stage. `phase` mirrors `stage`.
    • Guided questions (HITL grilling): in orientation/mapping the reply
      can be ONE authored question (thinking ON) + 2-4 option chips +
      always a skip chip («فعلاً همین کافی است؛ ادامه بده»); the agent
      asks and NEVER answers it. The answer lands in `decisions`; the
      orientation answer names map.destination. Process speech only —
      no Book facts, so the verbatim guard has nothing to check. Cap:
      3 rounds/stage; a failed author falls through to the stage's
      ordinary move.
    • Named open questions: sub-questions carry id + short name; chips
      work them individually («شواهدِ «نام» را پیدا کن» — resolved by
      server-side pattern, no classification luck).
    • Fog of war: map.fog holds coming-but-not-sharp questions; a
      matching open question graduates its fog note out.
    • Narration: every material operation's reply opens with one short
      LLM note (thinking OFF) — what changed on the map, what is next;
      qualitative only (no digits, no Book claims); junk/failed
      narration narrates nothing.
    • The sheet renders the route as «نقشۀ پژوهش» — the persistent side
      rail beside the chat (T10, GitLab #11): wide screens put the rail
      next to the transcript, sticky so the route stays in view without
      scrolling; narrow screens stack it above the chat. The five-stage
      journey strip leads the rail; checkpoint chips are short labels
      («می‌پذیرم» / «رد می‌کنم») with the proposal text in the note
      above.

  RESEARCH STATE LAYER (exploration/active/drafting/audit):
    a persistent state: versioned research question, scope, named open
    questions, evidence ledger, claim ledger, gaps, decisions, stage,
    map (destination/fog/out-of-scope), grilling, phase, proposals, the
    versioned Brief plan.
    • Checkpoint rule: an RQ or scope change proposed by the classifier
      NEVER applies itself — it lands as a pending proposal resolved via
      POST /research/decide («می‌پذیرم» / «رد می‌کنم» chips); an accepted
      question change APPENDS a version (v1 stays intact — provenance).
      The Brief plan (T7, GitLab #8) is a THIRD proposal kind in the
      same flow: sections tied to their named open questions and the
      supporting claim ids, parked only on an exploration turn under its
      own two-turn cooldown, accepted APPEND-ONLY into the plan ledger;
      once plans are required, the Brief refuses without an accepted
      plan. The work chips stay rendered beside the decision pair —
      deciding never blocks working (an explicit command executes even
      while a proposal waits).
    • At most ONE bounded operation per turn, deterministic when not
      forced by a fixed chip command:
        gather ── the dive's fan-out unchanged: ≤6 parallel searchers on
                   cognee-next-tier (:8001), each HYBRID_COMPLETION over
                   the picked Book, includeReferences:true, own 600s leash;
                   pools merged into the ledger, deduplicated on the
                   guard's normalized letter stream. Quote-starved
                   sub-questions (<2 passages) get ONE gap round; hard
                   caps 2 rounds × 6 searchers. Still-starved sub-questions
                   become honest GAP entries — never hallucinated fill.
                   Frontier-wide gathers then ride ONE bounded graph hop
                   (the Tool registry's `graph`): its node labels steer at
                   most two further citable hybrid searches; a targeted
                   chip gathers only its named question, no hop.
                   The reply is server-composed notes (counts and gaps).
        synthesize ── ONE guarded writer pass over the accumulated ledger
                   (thinking OFF — quotes must survive the verbatim guard);
                   each kept quoting paragraph records a CLAIM whose status
                   is code-derived: 1 passage = direct_support,
                   ≥2 = supported_synthesis.
        brief ── the Research Brief written FROM the state (question
                   history, scope, claims, gaps — never the chat
                   transcript); refuses without recorded claims.
        audit ── pure code: the claim ledger re-reported, no LLM call.
    • Every reply stating a Book fact runs the SAME verbatim guard as
      phases 2/3; the closing «منابع» references list is built
      SERVER-SIDE from the passages actually quoted — never the model's
      job; a pool the guard cannot feed lands the honest Farsi note.
```

Why this shape:

- **Separate service.** The searchers run on `cognee-next-tier` so minutes of
  gathering can never block the flash-model first-answer service.
- **Turn registry, stdlib-only** (a dict + lock + threads — no broker, no new
  dependency). Caps: **1 in-flight turn per phone, 3 globally**; beyond that →
  429 with a friendly Farsi busy message, never queued. Soft session cap: at
  40 turns every reply suggests writing the Brief — never a refusal.
- **Every turn runs under a hard budget** (ADR-0012, T3): a wall-clock deadline
  (`RESEARCH_TURN_DEADLINE_SECONDS`, default 600s) plus an upstream-call cap
  (`RESEARCH_TURN_CALL_CAP`, 24 — above every legitimate single-operation turn,
  below a runaway chain), both read through an injected clock so tests exhaust
  either instantly, no waiting on real time. Every bounded step — the classify
  call, the planner, one searcher round, one writer call — is pre-paid from
  the budget before it starts (the narrator, decoration, is charged only when
  it actually speaks), so the cap can never be exceeded mid-call; the deadline
  is checked at every chain boundary (cooperative, the
  cancel flag's shape: an in-flight call is never interrupted, so a turn
  overshoots by at most the one call already in flight). An over-budget turn
  stops at that boundary and says so: the honest Farsi note
  («بودجۀ این پیام پژوهش تمام شد…») closes the reply (after any partial
  blocks — a half-gathered pool stays in the ledger), the timeline carries
  the budget event, and the poll payload reports the budget state
  (`"budget": {calls, cap, exhausted, reason}`) — never a fabricated
  completion, never the generic failure.
- **Sessions persist in SQLite** (`ui/research.sqlite3`, the quotas.py pattern;
  container: the `session_quota` volume). A server restart empties only the
  in-flight-turn registry — a poll for a pre-restart turn answers 404 (the
  recorded failure surface) — while the session, state, and transcript reload
  and the conversation continues.
- **A new ask aborts** that phone's in-flight turn (cooperative cancel at the
  next stage boundary) and closes its session; a later message to a closed
  session answers the Farsi closed detail.
- Searchers use `HYBRID_COMPLETION`, not `GRAPH_COMPLETION` — recorded live:
  on the second service, GRAPH_COMPLETION returns graph-node metadata **with
  no verbatim passage text**, so the pool would be empty by construction and
  every sub-question would starve into a gap. The negative/positive fixtures
  are locked in `tests/fixtures/`. The mode is not banned — it is **shaped**:
  every way of searching the picked Book is a named Tool in `ui/dive.py`'s
  registry (ADR-0012, T4) with its own leash and result shape — hybrid and
  chunks parse to citable **passages**; the graph family (`graph`,
  `decomposition`, `context_extension`) parses to **concepts** (node labels
  that steer citable searches, their model text never quoted); `summaries`
  parses to **notes** (cognee's words, never the pool). Every Tool demands
  the session's picked Book (an empty pick raises, never a default) and the
  unsupported modes (`CYPHER`, `NATURAL_LANGUAGE`, `AGENTIC_COMPLETION`,
  `FEELING_LUCKY`, the COT probe) are outside the registry — unreachable by
  construction, a wrong name a recorded diagnosis.
- The old `/next-tier-recall` relay (`GRAPH_COMPLETION_COT`, up to ~10 min,
  `NEXT_TIER_TIMEOUT` 1200s) still exists server-side as the **operator's
  manual probe** of the second service; the sheet never calls it.

---

## 9. How the data got in — the ingest pipeline

You will normally never re-run this (the data ships restored), but this is how
the 341 chunks + graph were built:

```
tarhe-kolli.pdf ─┐
                 ├─ POST /api/v1/remember  (run_in_background=true)
70143-336.pdf  ──┘        │
                          ▼
                 Cognee ingest pipeline (hours, graph build on postgres_demo):
                   1. PDF → text extraction (Farsi, ~1150/980 chars per page)
                   2. chunking — token ceilings 4100 / 3500, paragraph boundaries,
                      ≤5 PDF pages per chunk
                   3. EXTRACTION   → glm-5.3-flash ON AVALAI (not Z.AI — 429 history)
                      (entities, relationships)   rate: 200 req/60s, retry 6×/1800s
                   4. SUMMARIZATION → same AvalAI routing
                   5. EMBEDDING    → text-embedding-3-large @3072 ON AVALAI
                      (batches of 32, 120 req/60s — far under 250 RPM / 4M TPM)
                   6. vectors → "DocumentChunk_text".vector(3072)
                      graph   → graph_node / graph_edge
                      summary → TextSummary_text
```

Progress was polled via `/api/v1/datasets/status` until both datasets showed
`completed`. **Re-ingest is not part of a code deploy** — datasets are ingested
once. The only thing that forces a full re-ingest is changing the embedding
model or dimensions (see §13).

---

## 10. Configuration map — who reads what

Two config layers with a strict rule: **`.env` carries keys; `compose.yaml`
carries behavior.**

| Setting | Lives in | Notes |
|---|---|---|
| `LLM_API_KEY` (Z.AI) | `.env` only | Picked up by cognee services (env_file) AND interpolated into the session container's env. Never in the image, never in git. |
| `EMBEDDING_API_KEY` (AvalAI) | `.env` only | Same. Also feeds `LLM_EXTRACTION_*` / `LLM_SUMMARIZATION_*` by compose interpolation. |
| `LLM_MODEL` per service | `compose.yaml` `environment:` | `glm-5.3-flash` on cognee, `glm-5.3` on next-tier. Overrides `.env`, so editing `.env`'s model does nothing in compose. |
| `EMBEDDING_MODEL` / `EMBEDDING_DIMENSIONS` | `.env`, compose default `text-embedding-3-large`/3072 | **Frozen into the data.** See §13. |
| Endpoints (Z.AI / AvalAI) | `compose.yaml` | Pinned; changing one requires a successful live smoke on that key first. |
| Rate limits & retries | `compose.yaml` | Per service; see the table in §4. |
| Picker/composer/research prompts, model pins, timeouts, caps | `ui/serve.py` source | Pinned in source "like every pin", never env-tunable. |
| `NEXT_TIER_TIMEOUT` | compose env, default 1200s | The relay's leash; COT searches can pass 10 minutes. |
| `enable_farsi_evidence.py` | bind-mounted into both Cognee containers | Entry point override: enables Farsi `Evidence:` blocks + 600-char snippets. Bind mount ⇒ re-read on container restart, **no re-ingest needed** (it applies at search time). |

---

## 11. Access control — phone gate, quotas, turn registry

There is no account system. The gate is deliberately lightweight
("stops casual credit-burn, not a determined caller"):

```
Phone number (10–13 digits, Persian/Arabic-Indic digits normalized)
   │  rides in X-Session-Phone header to serve.py
   │  (never forwarded to Cognee)
   ▼
ui/usage.sqlite3  (container: session_quota volume, /data/usage.sqlite3)
   ├── one ASK = one chat. 5 per phone per server-local day.
   │     phase 1 records it; 6th ask of the day → 429
   ├── phases 2 & 3: need "≥1 chat today" for that phone; never count anything
   └── research turn registry (in-memory, NOT sqlite):
         ≤1 in-flight turn per phone, ≤3 globally, 429+Farsi message beyond,
         cooperative abort + session close on new ask, 404 after restart
         (the SESSION itself persists in ui/research.sqlite3)
```

Honor-system by design: no SMS verification. The point is protecting the
Z.AI/AvalAI budgets, not real authentication.

---

## 12. Local quirks on this machine

Deviations between the pristine handoff/README and **this** machine:

1. **Cognee host port is 18000, not 8000.** Your other project
   (`chatbot-backend`) owns host port 8000, so `compose.yaml` was changed to
   publish `"18000:8000"`. Inside the Docker network nothing changed — the
   session sheet still talks to `http://cognee:8000`. Any README command using
   `localhost:8000` becomes `localhost:18000` here.
2. The handoff assets live one level up from the repo:
   `../cognee_db.dump` (restored 2026-09-12, idempotent — safe to re-run with
   `--clean --if-exists`), `../session-image.tar.gz` (loaded),
   `../cognee-file-storage/` (copied into the cognee volume).
3. Git Bash on Windows mangles Farsi in `curl --data-raw` (turns it into
   question marks → the API replies with its "unreadable message" fallback).
   Test with Python/urllib or the browser instead. `MSYS_NO_PATHCONV=1` is
   needed on `docker exec` commands with absolute container paths.

---

## 13. Known failure modes

| Symptom | Cause | Fix |
|---|---|---|
| Every search errors: `expected 1536 dimensions, not 3072` (or vice versa) | Embedding config drifted from the frozen vectors (this actually happened on the VPS 2026-09-12) | Set `EMBEDDING_MODEL=text-embedding-3-large` + `EMBEDDING_DIMENSIONS=3072` in `.env`, `docker compose up -d`. **Never** change these without planning a full re-ingest. |
| Health probes green but search fails | Probes don't exercise the embedder | Always verify with one real `/api/v1/recall`, not just `/health`. |
| First answer returns «پیام شما ناخوانا…» | The Farsi query arrived as question marks (Git Bash curl), or genuinely garbled input | Send proper UTF-8 (browser, Python). |
| A research reply lands «نقل‌قولی از کتاب‌ها پیدا نشد» | Searchers parsed zero Evidence passages | Recorded negative case of `GRAPH_COMPLETION`; confirm searchers pin `HYBRID_COMPLETION`. |
| Phase 2 shows nothing, phase 1 stands | Composer failed/timed out or guarded doc < 2 paragraphs | By design — the sheet is never left empty. |
| Z.AI 429 code 1302 during ingest bursts | Coding-plan quota shared with coding tools | Extraction is already routed to AvalAI; don't move it back. |
| Port bind failure on `up` | Something else owns the host port (this machine: 8000) | `docker ps` port check; keep the 18000 remap. |

---

## 14. ADR index — the decisions behind the design

| ADR | Decision |
|---|---|
| [0001](docs/adr/0001-postgres-for-whole-memory-layer.md) | Whole memory layer (relational + pgvector + graph + cache) on **one Postgres forever**. No Kuzu/Neo4j, ever. ⇒ no CYPHER/NL search types. |
| [0002](docs/adr/0002-chat-on-z-ai-glm-coding-plan.md) | Chat on Z.AI GLM coding plan; embeddings on AvalAI. |
| [0003](docs/adr/0003-quoted-answer-replaces-streamed-first-answer.md) | The Quoted answer (interleaved AI + verbatim quotes) replaces the streamed-prose first answer. |
| [0004](docs/adr/0004-two-books-large-embeddings-five-page-chunks.md) | Two Books; `text-embedding-3-large`/3072; ≤5-page chunks with per-Book token pins (4100/3500). |
| [0005](docs/adr/0005-three-phase-sheet-tabbed-timers-and-truncation-continuation.md) | Three-phase tabbed sheet; timers; single truncation continuation. |
| [0006](docs/adr/0006-quote-selection-deep-dive-stdlib-orchestration.md) | Quote selection as the rendered first answer; deep dive as stdlib job orchestration. |
| [0007](docs/adr/0007-book-reader-page-provenance.md) | The Book reader: every quote clicks through to the Book's PDF page, highlighted via client-side normalized letter-stream matching. |
| [0008](docs/adr/0008-research-mode-wayfinder.md) | Research Mode: the Deep dive becomes the Wayfinder — a multi-turn chat over a persistent, versioned research state with checkpoints, an evidence/claim ledger, and honest gaps. |
| [0009](docs/adr/0009-research-mode-journey-layer.md) | Research Mode's journey layer: a code-driven stage machine, guided (grilling) questions, the visible research map with fog of war, and the journey narrator — adapted from the Wayfinder skill. |
| [0010](docs/adr/0010-round-two-reliability-widen-selection.md) | Round two: the phase-2 writer repair and guard section-pruning, the «جست‌وجوی بیشتر» widen (`/recall-more`), the citation-landing fixes, and the ask's Book selection validated server-side. |
| [0011](docs/adr/0011-work-over-chart-editing.md) | Round three: chart mode vs work mode (the planning-loop breaker), the phase-1 evidence fallback, true-page resolution server-side, the single-Book pick, and the transcript that survives refresh. |

Plus the domain glossary in [CONTEXT.md](CONTEXT.md) — the project is strict
about vocabulary (Session, Book, Citation, Quote selection, Filler text…).
Use those words in docs, issues, and UI copy.

---

## 15. Glossary — the project's own vocabulary

| Term | Meaning | Never call it |
|---|---|---|
| **Customer** | The Farsi-speaking professional the product serves | client, user, account |
| **Session operator** | The human sitting with the product completing a Session | user |
| **Session** | One sitting of Farsi Q&A meeting the exit checks | chat, demo |
| **Research Mode** | Phase 3: the guided research conversation (the Wayfinder) over a persistent research state | deep study, wizard, questionnaire |
| **Journey layer** | The stage machine, guided questions, map, fog, and narrator steering a Research Mode conversation (ADR-0009) | chatbot flow, wizard steps |
| **Research map** | The visible route of a research session: destination, frontier, named open questions, decisions, fog, out of scope | status strip, dashboard |
| **Guided question** | A HITL turn where the agent asks ONE question and never answers it; the answer lands as a decision | prompt, form field |
| **Frontier** | The single open question the journey works now | queue head, current task |
| **Fog** | A coming question not yet sharp enough to ask; graduates into an open question | backlog, TODO |
| **Research state** | The session's versioned question, scope, evidence ledger, claims, gaps, and decisions | chat history, cache |
| **Claim ledger** | The recorded claims with code-derived statuses (direct support / synthesis) | summary, notes |
| **Book set** | The two named Books | corpus, library, knowledge base, documents |
| **Citation** | Book identity + exact pages of a quoted passage | footnote, evidence |
| **Quote selection** | Phase 1: ~10 verbatim book sentences, each cited | evidence list, snippet list |
| **Quoted answer** | Phase 2: paragraphs weaving filler + verbatim quotes | citation paragraph, chat |
| **Quoted paragraph** | One paragraph of the above; quotes highlighted + hoverable | evidence block, snippet |
| **Filler text** | AI-written connective text inside a Quoted paragraph; claims no pages | glue text, preamble |

---

## Quick reference — commands you'll actually use

```bash
cd D:/code/CHATBOT/v2-glm-iteration/chat-with-books

# status
docker ps --filter name=chat-with-books --format '{{.Names}}\t{{.Status}}'

# verify the full chain (UTF-8 safe)
python -c "import json,urllib.request; r=urllib.request.urlopen(urllib.request.Request(
  'http://localhost:18000/api/v1/search',
  data=json.dumps({'searchType':'CHUNKS','query':'اندیشه اسلامی',
  'datasets':['tarhe-kolli','70143-336'],'topK':3}).encode(),
  headers={'Content-Type':'application/json'}), timeout=120); print(len(json.load(r)),'hits')"

# restart after .env edits
docker compose --profile session --profile ui up -d

# next-tier COT operator probe of the second service (can take ~10 min)
curl -sS -X POST http://localhost:8001/api/v1/recall -H "Content-Type: application/json" \
  --data-raw '{"searchType":"GRAPH_COMPLETION_COT","query":"...","datasets":["tarhe-kolli","70143-336"],"includeReferences":true}'

# run the contract-locking test suite
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest
```
