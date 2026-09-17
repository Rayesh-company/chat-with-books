# Deep Research — exactly how it works

A precise, as-implemented description of **Research Mode** («حالت پژوهش», phase 3 of the sheet) and the pipeline it lives in: the philosophy it commits to, the architecture that carries it, and the exact scenario a user walks. Everything below is what the code does today — ADR-0008/0009/0010/0011 are the decision records behind it.

---

## 1. Core philosophy — the five commitments

1. **Citation-first, never hallucinated.** The platform's first job is to find what the Books actually say, not to reason fluently. Every sentence shown as a quote is verified letter-for-letter against a real retrieved passage (the *verbatim guard*); anything the model writes on its own is "filler" and never carries a page. A question the Books cannot answer produces an honest gap — never an invented fill.
2. **The Books are the only witnesses.** Answers come from exactly two ingested works — `tarhe-kolli` (طرح کلی اندیشۀ اسلامی در قرآن) and `70143-336` (انسان ۲۵۰ ساله). External knowledge is never a citable source.
3. **Checkpointed autonomy (chart mode vs work mode).** The engine may freely fold in *working* material — concepts, open questions, evidence — but consequential changes to the *map* (the research question, the scope) never apply themselves: they land as proposals the user accepts or rejects. Conversely, once the user is working (an investigation command), the engine **executes** instead of editing the map — the "planning loop" (rewrite → approve → rewrite) is structurally prevented: only exploration turns may propose, explicit commands outrank pending checkpoints, and a decided proposal starts a two-turn cooldown of its kind.
4. **A guide, not a form.** The engine is a Wayfinder-style guide: it names the destination with the user first, surveys the ground the Books actually hold, cuts the question into **named** open questions, and works them one at a time — with a visible map the whole way. It asks one sharp guided question per turn in the early stages and never answers its own question.
5. **One bounded operation per turn.** No unbounded agentic loops: a research turn runs at most one bounded operation (one fan-out gather, one synthesis pass, one Brief), with hard caps everywhere. Slow is allowed; runaway is not.

---

## 2. Where it sits — the three-phase pipeline

One ask (a phone + one question, five chats/phone/day) runs three phases, each in its own tab:

| Phase | Tab | What it produces |
|---|---|---|
| 1 | «پاسخ اول» | The **Quote selection**: 4–12 verbatim Book sentences picked by exactly one picker call over the retrieved Evidence pool. The streamed prose is only TTFT + phase-2 framing, never displayed. |
| 2 | «پاسخ استنادی» | The **Quoted answer**: a planner (thinking ON) outlines the document, a writer (thinking OFF) writes interleaved paragraphs of Farsi filler + verbatim quotes; the guard strips every non-verbatim quote; a dead section's heading is pruned; a below-threshold document triggers ONE writer retry. |
| 3 | «حالت پژوهش» | **Research Mode** — the subject of this document. |

Phase-1 safety nets: if the stream carries no `Evidence:` block, ONE pinned reference-on search over the picked Book fetches the pool (`/evidence-fallback`) before the sheet concedes; «جست‌وجوی بیشتر» widens the pool with up to two adjacent facet queries (`/recall-more`); every citation label shows the passage's **actual page** resolved against the Books' per-page text index (`ui/page_resolver.py`), not the locator's estimate.

---

## 3. Architecture

### 3.1 Services

```
Browser (ui/index.html, one page, Farsi RTL)
   │  phone header X-Session-Phone on every call
   ▼
chat-with-books-session  (ui/serve.py, stdlib Python, :8765)
   ├── proxy ──▶ cognee :8000            (first-answer recall, HYBRID_COMPLETION, SSE)
   ├── searchers ─▶ cognee-next-tier :8001 (research gathers & fallbacks; same Postgres memory)
   ├── composer ──▶ glm-5.3-flash         (classify / plan / write / narrate / broaden)
   └── sqlite ──── usage.sqlite3 (quota) · research.sqlite3 (sessions + transcript)
                    books/<dataset>.pdf + .pages.json (reader + page resolution)
```

Postgres is the single memory layer (pgvector + graph) for both Cognee services; the books are **separate datasets** (`tarhe-kolli`, `70143-336`) searched together or singly — the ask's Book pick (one Book, persisted in `localStorage.selectedBook`) flows into the recall proxy, the widen, and the research session.

### 3.2 The three layers of a research turn

Every message POSTs to `/research/message` and gets `202 {turn_id, session_id}`; the sheet polls `/research/turn` (2 s chained timeout). The worker (`ui/research.py: run_research_turn`) routes through three layers:

1. **Conversation layer** — `classify` reads the message + a compact state projection + the last 6 turns in ONE thinking-ON `glm-5.3-flash` call and returns one of 7 intents: `casual_question`, `concept_learning`, `source_lookup` (→ conversational path: one searcher, one guarded writer, references cite exactly that pool, **no research state change**), or `research_exploration`, `active_research`, `drafting`, `evidence_audit` (→ the research side). A malformed classify reply degrades to the conversational path — never crashes.
2. **Journey layer** (the guide) — the stage machine, guided questions, narration, contextual chips. Code moves stages; the model only proposes content.
3. **Research state layer** — the persistent investigation document (below) and at most ONE bounded operation per turn.

### 3.3 The research state (one JSON document per session, in sqlite)

| Field | Meaning |
|---|---|
| `research_question` | `{current, versions[]}` — v1 is the ask; every accepted change appends a version with its reason. Append-only provenance. |
| `scope` | `{in[], out[]}` — the investigation's boundary. |
| `subquestions` | **Named open questions**: `{id, name (≤5 words), text, status: pending→searched|gap}` — the journey's units of work. |
| `evidence` | The ledger: `{id, reference, passage, found_for}`, deduped on the guard's normalized letter stream. |
| `claims` | One per kept quoting paragraph: `{text, status, evidence_ids}` — status is **code-derived** (1 distinct passage → `direct_support`, ≥2 → `supported_synthesis`). |
| `gaps` | Honest «cannot be established from these Books» entries. |
| `decisions` | The index of the route walked (gists, accepts, rejects). |
| `map` | `{destination, fog[] (coming-but-not-sharp questions, ≤8), out_of_scope[], landscape_done}`. |
| `grilling` | The live guided question + options + per-stage count. |
| `stage` / `phase` | The journey stage (code-owned) and its legacy mirror. |
| `datasets` | The ask's resolved Book selection — every searcher of this session obeys it. |
| `proposal_cooldowns` | Per-kind turn countdowns set by decisions (the loop damper). |
| `pending_proposals` | Checkpoints awaiting `/research/decide`. |

### 3.4 Endpoints

| Route | Gate | Does |
|---|---|---|
| `POST /research/message` | ≥1 chat today, never counts | creates/continues the session, starts the turn (202 + poll) |
| `GET /research/turn?turn=` | phone-matched | `{state, events[], elapsed}` → done carries `{reply, suggestions, research_state}` |
| `GET /research/state?session=` | phone-matched | the map projection |
| `GET /research/messages?session=` | phone-matched | the ordered transcript (refresh survival) |
| `POST /research/decide` | phone-matched | resolves ONE checkpoint (accept/reject), synchronous, no LLM |
| `POST /recall-more`, `/evidence-fallback`, `/quote-selection`, `/quoted-answer` | same family | the ask-side widen / fallback / picker / composer |
| `POST /api/v1/recall` | records a chat | proxy to Cognee, body validated (`query` required; `datasets` ∩ Book set) |

### 3.5 Models and pins

All calls run `glm-5.3-flash`, pinned in source. **Thinking ON** for reasoning passes (classify, sub-question planning, guided-question authoring, broaden, the phase-2 planner); **thinking OFF** (or `reasoning_effort: low` on non-Z.AI endpoints) for every writer whose output must survive the verbatim guard, and for the narrator. `max_tokens` 16384 pinned; per-call timeout 240 s (composer), 600 s per searcher.

---

## 4. The journey — the stage machine (code-owned)

Stages advance **only by code, only forward**, on observable state:

```
orientation ──▶ mapping ──▶ investigating ──▶ synthesizing ──▶ drafting
 نام‌گذاری مقصد   نقشه‌برداری   گردآوری شواهد    تحلیل و جمع‌بندی   نوشتن خلاصه
```

- **orientation → mapping**: the destination is recorded — the user answers the guided destination question (the answer's gist lands as a decision and names `map.destination`), or presses the skip chip (the current question stands in).
- **mapping → investigating**: the landscape turn ran once (one searcher over the research question, one guarded writer surveying what the Books hold, closing with the writer's own breadth-first question), and ≥2 open questions are pending → the engine reads the questions back **by name**.
- **investigating → synthesizing**: a synthesis records claims.
- **synthesizing → drafting**: the Brief is written.

**Guided questions (grilling).** In orientation/mapping only, when the destination is unset and the stage has asked < 3 rounds, and never on a work intent (`active_research/drafting/evidence_audit` — the classifier decides in any wording): one thinking-ON authoring call produces ONE Farsi question + 2–4 short option chips + always the skip («فعلاً همین کافی است؛ ادامه بده»). The agent never answers its own question; questions are process speech — no Book facts, so nothing for the guard to check. A failed authoring falls through to the stage's ordinary move.

**The map on screen.** Under a five-dot journey strip, «نقشۀ پژوهش» shows: مقصد (destination) · پرسش پژوهش (vN) · در حال پرداختن (the frontier: the oldest pending question, highlighted) · پرسش‌های باز (named, with status dots) · تصمیم‌ها (last decisions) · هنوز نامشخص (fog — questions the investigation sees coming but can't state sharply yet; they graduate into open questions when specifiable) · خارج از دامنه · the counts row.

**Chips are the journey's controls** — all deterministic, no classification luck:

| Chip text | Resolves to |
|---|---|
| «شواهد بیشتری از کتاب‌ها پیدا کن» | gather (all pending) |
| «شواهدِ «نام» را پیدا کن» | gather targeted at that named question |
| «همهٔ پرسش‌های باز را جست‌وجو کن» | gather (all pending) |
| «شواهد را تحلیل و جمع‌بندی کن» | synthesize |
| «خلاصۀ پژوهش را بنویس» | the Brief |
| «ادعاها و استنادها را بازبینی کن» | audit (pure code) |
| «ادامهٔ سفر پژوهش» | re-asks the guided question after a detour |
| «فعلاً همین کافی است؛ ادامه بده» | skip the questioning, move on |
| option chips | sent verbatim as the user's answer |

**Narration.** Every material operation's reply opens with one short thinking-OFF note — what changed on the map and what's next. Qualitative only: no digits (the server's own fact line carries counts), no Book claims, no quotes. A junk or failed narration narrates nothing.

---

## 5. The scenario, end to end (actual implemented behavior)

1. **Entry.** The user picks ONE Book («طرح کلی اندیشۀ اسلامی در قرآن») — the ask stays disabled until they do; the pick persists across refreshes. Types: «رابطه پیامبر با حضرت علی چه ویژگی متمایزی داشته و این ویژگی‌ها چه دلالتی درباره جانشینی دارند؟»
2. **Phase 1** — Cognee streams an answer; the `Evidence:` pool is parsed server-side, the picker picks the verbatim sentences, the guard keeps 4–12 with true-page labels. The Quote selection is the displayed first answer. (No Evidence in the stream? The fallback search fetches the pool first.)
3. **Phase 2** runs in parallel — planner + writer over the same pool, the guarded Quoted answer lands in its tab.
4. **Research turn 1** (the operator opens «حالت پژوهش», seeded with the ask + pool). The classify reads an exploration intent; the journey asks its destination question («در پایان این پژوهش مایلید دقیقاً به چه چیزی رسیده باشید؟») with option chips. The user taps one or types; the gist lands as a decision, names the destination, and the journey advances to **mapping** — which surveys the Books' actual ground (quoted landscape) and asks which facets matter. The user's answer cuts **named open questions** (e.g. «غدیر», «منزلت», «مقایسه با سایر اصحاب»); the classify may also park an RQ/scope proposal.
5. **Checkpoint** — a proposal shows as a compact diff («پرسش پژوهش از «…» به «…»») with SHORT chips «می‌پذیرم / رد می‌کنم». Accept appends RQ v2; reject records the decision. Either way the kind cools down for two turns — no loop.
6. **«شواهد را گردآوری کن دربارهٔ غدیر و منزلت»** — free text classified as a work intent: the journey **gathers** (no re-approve). The fan-out decomposes into ≤6 parallel searchers on the next tier (HYBRID_COMPLETION, references on, the session's Book only), pools passages into the ledger (deduped), starved questions get one gap round (hard cap: 2 rounds), the reply opens with the narrator's note, then the server's fact line («۲۹ نقل‌قول تازه…»). Per-question chips («شواهدِ «توثیق اهل سنت» را پیدا کن») let the user steer the frontier.
7. **«شواهد را تحلیل و جمع‌بندی کن»** — one guarded writer pass over the whole ledger; each kept quoting paragraph records a claim with its code-derived status. The narrator explains what the evidence now covers — and what it doesn't.
8. **«ادعاها و استنادها را بازبینی کن»** — pure code: the claim ledger + statuses + gaps.
9. **«خلاصۀ پژوهش را بنویس»** — the **Research Brief**, written FROM the state (question history, scope, claims, gaps — never the chat transcript), in sections with verbatim quotes and server-built «منابع». Refuses without recorded claims. At 40 turns the engine only *suggests* writing it — a soft cap, never a refusal.
10. **Throughout**: a casual side question («این مفهوم را توضیح بده») is answered normally with no research formalism; a browser refresh re-fetches the transcript and the map; a brand-new ask aborts this session's in-flight turn and closes it.

---

## 6. The safety contract (what actually enforces honesty)

- **Verbatim guard**: every quoted sentence's normalized Farsi letter stream must occur in its claimed passage; paraphrases and wrong-passage claims drop (the paragraph drops if nothing survives; a heading with no surviving paragraph is pruned; below-threshold documents trigger one writer retry then fall back).
- **References are built server-side** from the passages actually quoted — the model never writes «منابع», never invents a page; labels resolve the true page from the page index.
- **Book-only claims**: out-of-corpus content stays commentary, never a claim.
- **Gaps are first-class**: a starved question becomes «کتاب‌ها برای «…» شواهد کافی ندارند؛ این بخش را نمی‌توان از همین کتاب‌ها اثبات کرد» — with narrow/accept/stop options, never filled.
- **Checkpoints**: RQ/scope changes land only through `/research/decide`; versions are append-only.
- **Bounded work**: ≤6 searchers × ≤2 rounds per gather; one operation per turn; 1 in-flight turn/phone, 3 globally (busy → Farsi 429, never queued); cooperative abort at stage boundaries.
- **Persistence**: sessions + transcript in `research.sqlite3` (restart keeps everything but the in-flight turn, whose poll 404s by contract); research is quota-free once the day's chat gate is open.

---

## 7. File map

| Piece | File |
|---|---|
| Turn worker, journey, state, chips | `ui/research.py` |
| Session + transcript store | `ui/research_store.py` |
| Searcher kernel (fan-out, starvation) | `ui/dive.py` |
| Widen («جست‌وجوی بیشتر») | `ui/recall_more.py` |
| True-page resolver | `ui/page_resolver.py` |
| Verbatim guard + labels | `ui/guard.py` |
| Composer client (retry, salvage, continuation) | `ui/composer.py` |
| Quote-selection picker | `ui/picker.py` |
| Routes, gates, proxy, reader assets | `ui/serve.py` |
| The sheet (chips, map, reader) | `ui/index.html` |
| Decision records | `docs/adr/0008…0011` |
| Contract locks | `tests/` (247 tests) |
