# Benchmark — fast chat mode (first answer)

A fixed 18-question set (TKI-01 … TKI-18) over the Book طرح کلی اندیشۀ اسلامی در قرآن,
run against the **fast chat mode only** — Cognee `recall()` with `HYBRID_COMPLETION` on
port 8000 (`glm-5.3-flash`). The next-tier service (8001) and the UI quoted-answer
composer are deliberately out of scope, so every number here is attributable to the
first-answer pipeline alone.

Since the Quote selection went live (ADR 0006, issue #28), the **sold first answer** is
the recall plus exactly one `glm-5.3-flash` picker call on the sheet's `/quote-selection`
(guarded 4–12 verbatim sentences). `--quote-selection` records that product-as-sold
contract: per question, the recall wall, the picker wall, the pool size, and the
selection texts. Plain runs (no flag) record the recall alone — the superseded contract,
kept for pipeline-only comparisons. The old baseline runs recorded under the superseded
contract are archived at `runs/archive/baseline/`, not deleted.

The purpose is **variant comparison**: change exactly one thing in the setup (a model
pin, the Farsi-evidence patch, a search type), run again under a new variant label, and
diff the runs.

## Files

| File | Role |
|---|---|
| `questions.json` | The 18 questions with category, difficulty, level (1–4), gold points, traps, and evidence hints. |
| `setup.json` | Metadata of the current chat-with-books setup (models, endpoints, memory stack, patches). The runner snapshots it into every run. |
| `scoring.json` | Component weights (out of 100), final-score formula, hallucination penalties, retrieval metrics, judge policy. |
| `run_benchmark.py` | Executes the questions against the live fast chat service and records answers, evidence, and wall time. |
| `judge.py` | Optional: scores a run with a fixed LLM judge (`glm-5.3` on the Z.AI coding endpoint). |
| `compare.py` | Tabulates all runs side by side. |
| `runs/<variant>/<UTC stamp>/` | One JSON per question (`answer_text`, `evidence_text`, cited pages, wall seconds), `meta.json` (setup snapshot), `summary.json`/`summary.md`, and `scores.json` after judging. |

## Run

Cognee must be up (`curl http://localhost:8000/health`) with `tarhe-kolli` ingested (the Book set also carries `70143-336` since 2026-09-10; TKI questions stay on the `tarhe-kolli` dataset alone). For `--quote-selection`, the sheet must be up on 8765 (`curl http://localhost:8765/livez`) and running code that has the picker (`/quote-selection` answers 400 on an empty pool; a 404 means old code).

**Benchmark phone bootstrap.** The picker's gate is phase 2's shape: the `X-Session-Phone` needs at least one chat today (the ask records it; the picker never counts). `09120000000` is the reserved benchmark phone; before a `--quote-selection` run, give it one real ask through the sheet:

```bash
curl -s -X POST http://localhost:8765/api/v1/recall \
  -H "Content-Type: application/json" -H "X-Session-Phone: 09120000000" \
  -d '{"searchType":"HYBRID_COMPLETION","query":"<one real question>","datasets":["tarhe-kolli"],"includeReferences":true}'
```

If it answers 429, the phone already spent its chats today — use `--phone 09120000001` for the run (and bootstrap that one instead). The bootstrap ask is part of the sanctioned spend, exactly one per benchmark day.

```powershell
python benchmark/run_benchmark.py --quote-selection                     # all 18, product-as-sold first answer
python benchmark/run_benchmark.py                                       # recall only (superseded contract)
python benchmark/run_benchmark.py --variant my-tweak --quote-selection  # after changing exactly one setup knob
python benchmark/run_benchmark.py --ids TKI-06,TKI-12 --probe-chunks
```

Wall time is submit-to-full-answer of the non-streaming call; the UI streams the same
call and the `final` frame carries the identical JSON, so totals are comparable. TTFT is
a UI-side metric and is not benchmarked here. The first search after a cold start can sit
several minutes on Cognee's pre-search step — expect it only on the first question. In
`--quote-selection` mode each question records `recall_wall_seconds`, `picker_wall_seconds`,
and `total_wall_seconds` beside the recall fields, and summary.json/summary.md carry the
stage timings (avg/total); `wall_seconds` keeps meaning the recall wall so `compare.py`
stays comparable across the contract boundary. An empty or failed picker is recorded as
the prose fallback (`selection_fallback`, `fallback_ids`) — the recorded fallback shape
the sheet itself renders, never a crash.

## Score

```powershell
python benchmark/judge.py benchmark/runs/baseline/<stamp>
python benchmark/compare.py
```

The judge scores the eight weighted components in `scoring.json`, applies hallucination
penalties (invented verse/page −25, fabricated quote −40, …), and computes the final
score in Python from the weights — the model never does the arithmetic. The judge model
is held fixed (`glm-5.3`) across variants; note the same-model caveat in `scoring.json`
and lean on the retrieval, groundedness, and citation components plus the penalties when
in doubt. Retrieval-only scoring (`Recall@k`, MRR) needs `--probe-chunks` plus a human or
LLM labeling of the returned chunks against each question's `evidence_hint`.

## Defining a variant

1. Copy `setup.json`, change `variant_id` and the one knob under test (e.g. `chat_model.model`, `patches`).
2. `python benchmark/run_benchmark.py --variant <new-id>` — the runner snapshots whatever
   `setup.json` currently holds into `runs/<new-id>/<stamp>/meta.json`, alongside the
   live git commit and service version.
3. Judge both runs and `python benchmark/compare.py`.

`runs/` is not gitignored: committing runs is the easiest way to diff variants on another
machine. Answers quote the Book, so treat committed runs like any other Book-derived
fixture.
