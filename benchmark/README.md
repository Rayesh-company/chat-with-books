# Benchmark — fast chat mode (first answer)

A fixed 18-question set (TKI-01 … TKI-18) over the Book طرح کلی اندیشۀ اسلامی در قرآن,
run against the **fast chat mode only** — Cognee `recall()` with `HYBRID_COMPLETION` on
port 8000 (`glm-5.3-flash`). The next-tier service (8001) and the UI quoted-answer
composer are deliberately out of scope, so every number here is attributable to the
first-answer pipeline alone.

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

Cognee must be up (`curl http://localhost:8000/health`) with `tarhe-kolli` ingested (the Book set also carries `70143-336` since 2026-09-10; TKI questions stay on the `tarhe-kolli` dataset alone).

```powershell
python benchmark/run_benchmark.py                     # all 18, variant "baseline"
python benchmark/run_benchmark.py --variant my-tweak  # after changing exactly one setup knob
python benchmark/run_benchmark.py --ids TKI-06,TKI-12 --probe-chunks
```

Wall time is submit-to-full-answer of the non-streaming call; the UI streams the same
call and the `final` frame carries the identical JSON, so totals are comparable. TTFT is
a UI-side metric and is not benchmarked here. The first search after a cold start can sit
several minutes on Cognee's pre-search step — expect it only on the first question.

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
