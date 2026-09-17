"""The retrieval kernel the Research Mode engine (ADR-0008) rides: the
pinned searchers over the second Cognee service, the parallel fan-out,
and the bounded two-round loop with the starvation measure — the pieces
of the old Deep dive (ADR-0006, issues #25 and #27) that survived the
rewrite into a multi-turn research conversation. The one-shot dive job
machinery itself is gone; ui.research's wayfinder turns call in here.
The kernel talks to the second service's recall behind this module's
ONE `urlopen` attribute, so the scripted-upstream tests keep a single
patch point (ui.dive.urlopen)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
import re
from urllib.request import Request, urlopen

# Research Mode's searchers ride the second Cognee service
# (cognee-next-tier, port 8001) so a multi-minute graph search never
# blocks the first-answer path on 8000 (ADR-0002). Both read the same
# Postgres memory.
NEXT_TIER_URL = os.environ.get("NEXT_TIER_URL", "http://127.0.0.1:8001").rstrip("/")
# The Book set the sheet answers over; the search shape is pinned here
# — never in the browser (README, Next-tier search).
BOOK_DATASETS = ["tarhe-kolli", "70143-336"]

# A graph-backed search can sit minutes on the second service; each
# searcher gets its own leash (the COT probe's four rounds fit inside
# ten minutes on live logs, and a decomposition pass is lighter).
DIVE_SEARCH_TIMEOUT = 600
# The search fan-out's breadth: at most this many parallel searchers
# over one turn's sub-questions. The planning prompts' count wording is
# derived from this constant — the two cannot drift.
DIVE_MAX_SUB_QUESTIONS = 6
# Starvation (issue #27): a sub-question is quote-starved when its own
# searcher parsed FEWER than this many passages — too few to ground a
# claim on. The count is the searcher's own parsed Evidence pool, and a
# fed sub-question is never re-searched.
DIVE_STARVED_PASSAGES = 2
# The topology's round lock (issue #27): round 1 plus at most one gap
# round over the starved sub-questions. This is the bound that keeps a
# research turn from growing an unbounded agentic loop — a pathological
# upstream that starves every sub-question gets exactly two rounds,
# never a third.
DIVE_MAX_RETRIEVAL_ROUNDS = 2


def parse_evidence_sources(text) -> list:
    """Pull (reference, passage) pairs out of one recall reply's text.

    The server-side twin of the sheet's Evidence parsing: the reply's
    answer is followed by an `Evidence:` block of bullets, each bullet
    `locator: "quoted passage"`. Malformed bullets drop; a reply with no
    Evidence block yields [] — never an invented passage.
    """
    if not isinstance(text, str):
        return []
    at = text.find("Evidence:")
    if at == -1:
        return []
    rest = text[at + len("Evidence:"):]
    sources = []
    for bullet in re.split(r"\n-\s+", rest):
        item = bullet.strip()
        at_quote = item.find(': "')
        if at_quote == -1:
            continue
        reference = item[:at_quote].strip()
        passage = item[at_quote + 3:]
        if passage.endswith('"'):
            passage = passage[:-1]
        passage = passage.strip()
        if reference and passage:
            sources.append({"reference": reference, "passage": passage})
    return sources


def dive_recall(sub_question: str, datasets=None) -> list:
    """One searcher: the pinned recall on the second Cognee service.

    The search shape is pinned HERE, never in the browser:
    HYBRID_COMPLETION over the Book set with references on — not
    FEELING_LUCKY, not AGENTIC_COMPLETION (it requires exactly one
    dataset; the Book set is two), and not GRAPH_COMPLETION, which
    renders no Evidence block on this service (the 2026-09-12 live
    smoke, ticket #25; ADR 0006 — the negative fixture
    tests/fixtures/recall-graph-completion-8001.json locks it). The
    dataset subset narrows the search to the ask's selected Books
    (ADR-0010); None or an empty list means the whole Book set. On any
    failure the searcher contributes nothing; the turn continues on its
    siblings' pools.
    """
    request = Request(
        f"{NEXT_TIER_URL}/api/v1/recall",
        data=json.dumps(
            {
                "searchType": "HYBRID_COMPLETION",
                "query": sub_question,
                "datasets": list(datasets) if datasets else list(BOOK_DATASETS),
                "includeReferences": True,
            },
            ensure_ascii=False,
        ).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=DIVE_SEARCH_TIMEOUT) as response:
            payload = json.load(response)
    except (OSError, ValueError):
        return []
    if not isinstance(payload, list):
        return []
    sources = []
    for item in payload:
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            sources.extend(parse_evidence_sources(item["text"]))
    return sources


def run_dive_round(sub_questions, sources, seen, datasets=None) -> list:
    """One retrieval round: as many searchers as sub-questions run at
    once (stdlib threads), each on its own DIVE_SEARCH_TIMEOUT leash.

    Every searcher's parsed pool merges into `sources` in sub-question
    order — APPEND only, never re-ordering, so the passage indices a
    writer prompt shows stay stable across rounds — and identical
    passages deduplicate against everything already pooled. Returns one
    parsed count per sub-question, in order, taken from each searcher's
    own Evidence pool before dedupe: the starvation measure (issue #27).
    """
    workers = max(1, len(sub_questions))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pools = list(
            pool.map(lambda question: dive_recall(question, datasets), sub_questions)
        )
    for result in pools:
        for source in result:
            passage = source["passage"]
            if passage in seen:
                continue
            seen.add(passage)
            sources.append(source)
    return [len(result) for result in pools]


def dive_retrieve(
    sub_questions, cancel=None, on_gap=None, datasets=None, budget=None
) -> tuple:
    """The bounded retrieval (issue #27): round 1 searches every
    sub-question; sub-questions that come back quote-starved — fewer
    than DIVE_STARVED_PASSAGES parsed passages — re-search AT MOST
    ONCE, in parallel, only the starved ones. DIVE_MAX_RETRIEVAL_ROUNDS
    is the lock that keeps a research turn from growing an unbounded
    agentic loop: an always-starved upstream gets exactly two rounds,
    never a third. The gap round is announced through `on_gap` before it
    runs — the caller turns the starved count into a Farsi event — and
    the `cancel` flag is checked between rounds so an abort lands
    promptly. `datasets` narrows every searcher to the ask's selected
    Books (ADR-0010). Returns (sources, rounds, starved): the merged
    pool, the rounds it took, and the sub-questions still starved at
    exit — the research state's honest gap entries.

    `budget` (the research turn's TurnBudget, T3) pre-pays each round's
    searcher calls: a round starts only when its full cost fits, and the
    round's calls are charged to the budget when it lands — so the
    turn's call cap can never be exceeded mid-round. A refused round
    leaves the loop with the pool gathered so far and the refusal
    recorded on the budget — the worker's boundary check stops the turn
    right after."""
    sources = []
    seen = set()
    pending = list(sub_questions)
    rounds = 0
    while pending and rounds < DIVE_MAX_RETRIEVAL_ROUNDS:
        if cancel is not None and cancel.is_set():
            return sources, rounds, pending
        if budget is not None and not budget.afford(len(pending)):
            break
        if rounds > 0 and on_gap is not None:
            on_gap(len(pending))
        rounds += 1
        counts = run_dive_round(pending, sources, seen, datasets)
        if budget is not None:
            budget.charge(len(counts))
        pending = [
            question
            for question, count in zip(pending, counts)
            if count < DIVE_STARVED_PASSAGES
        ]
    return sources, rounds, pending
