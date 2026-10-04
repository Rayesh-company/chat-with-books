"""The retrieval kernel the Research Mode engine (ADR-0008) rides: the
pinned searchers over the second Cognee service, the Tool registry that
names every way of searching the Book set (ADR-0012, T4), the parallel
fan-out, the bounded two-round loop with the starvation measure, and
the one bounded graph hop — the pieces of the old Deep dive (ADR-0006,
issues #25 and #27) that survived the rewrite into a multi-turn
research conversation. The one-shot dive job machinery itself is gone;
ui.research's wayfinder turns call in here. The kernel talks to the
second service's recall behind this module's ONE `urlopen` attribute,
so the scripted-upstream tests keep a single patch point
(ui.dive.urlopen)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
import threading
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
# The fast leash: the vector-only Tools (chunks, summaries) run no LLM
# round on the second service — one query embedding plus a pgvector
# lookup — so their fuse is the short one, an order under the
# graph-backed leash above.
TOOL_FAST_SEARCH_TIMEOUT = 120

# The Tool registry (ADR-0012, T4): every way of searching the Book set
# a Research skill row may declare, one entry per Tool — the second
# service's search type, the Tool's OWN leash, and the shape its reply
# parses to:
#   passages — verbatim Book passages with page locators; the only shape
#              that may enter the evidence ledger and be quoted;
#   concepts — the graph family's node labels. A graph completion's text
#              is model-written synthesis — the recorded live reply (the
#              negative fixture tests/fixtures/
#              recall-graph-completion-8001.json) carries no Evidence
#              block and no verbatim passage — so only the labels cross
#              out of a graph reply, steering citable searches instead;
#   notes    — pre-generated summaries; context for later skills, never
#              the pool (a summary is cognee's words, not the Book's).
# The unsupported modes stay out BY CONSTRUCTION — no caller can name
# what the registry does not hold: CYPHER and NATURAL_LANGUAGE have no
# support on the Postgres demo graph, AGENTIC_COMPLETION requires
# exactly one dataset (the Book set is two), FEELING_LUCKY auto-routes
# past the pin, and GRAPH_COMPLETION_COT stays the operator probe of
# serve.py's relay, minutes deep and no Tool of a turn. The sheet must
# never learn the excluded words either (the session-UI test locks
# that).
TOOL_REGISTRY = {
    "hybrid": {
        "search_type": "HYBRID_COMPLETION",
        "timeout": DIVE_SEARCH_TIMEOUT,
        "shape": "passages",
    },
    "chunks": {
        "search_type": "CHUNKS",
        "timeout": TOOL_FAST_SEARCH_TIMEOUT,
        "shape": "passages",
    },
    "graph": {
        "search_type": "GRAPH_COMPLETION",
        "timeout": DIVE_SEARCH_TIMEOUT,
        "shape": "concepts",
    },
    "decomposition": {
        "search_type": "GRAPH_COMPLETION_DECOMPOSITION",
        "timeout": DIVE_SEARCH_TIMEOUT,
        "shape": "concepts",
    },
    "context_extension": {
        "search_type": "GRAPH_COMPLETION_CONTEXT_EXTENSION",
        "timeout": DIVE_SEARCH_TIMEOUT,
        "shape": "concepts",
    },
    "summaries": {
        "search_type": "SUMMARIES",
        "timeout": TOOL_FAST_SEARCH_TIMEOUT,
        "shape": "notes",
    },
}

# The graph hop's bounds (run_tool's one chain): the node labels that
# may steer follow-ups, and the citable searches one hop may run.
GRAPH_HOP_LABEL_CAP = 3
GRAPH_HOP_FOLLOW_UP_CAP = 2


class ToolError(ValueError):
    """A Tool call the registry refuses: an unregistered name, or a
    search without the session's picked Book. The caller records the
    diagnosis — a refusal is never a silent empty pool."""


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


# The searcher-seam meter (stage C, findings-02 lever 5): thread-local
# like the composer's own tap — the research worker parks a listener
# for the turn's thread and clears it in its finally, so one turn can
# never bill another's searches.
_SEARCH_METER = threading.local()


def set_search_meter(listener) -> None:
    """Park this thread's search listener (None clears — the research
    worker's finally always clears, mirroring composer.set_meter)."""
    _SEARCH_METER.listener = listener


def _notify_search(query: str) -> None:
    listener = getattr(_SEARCH_METER, "listener", None)
    if listener is None:
        return
    try:
        listener(query)
    except Exception:
        pass  # the meter watches the search; it never breaks it


def _recall_request(search_type: str, query: str, datasets: list) -> Request:
    """The recall POST every searcher shares — the shape pinned HERE,
    never in the browser: references on, the datasets the caller picked."""
    return Request(
        f"{NEXT_TIER_URL}/api/v1/recall",
        data=json.dumps(
            {
                "searchType": search_type,
                "query": query,
                "datasets": list(datasets),
                "includeReferences": True,
            },
            ensure_ascii=False,
        ).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )


def dive_recall(sub_question: str, datasets=None) -> list:
    """One phase-1 searcher: the pinned recall on the second Cognee
    service.

    The search shape is pinned HERE, never in the browser:
    HYBRID_COMPLETION over the Book set with references on — not
    FEELING_LUCKY, not AGENTIC_COMPLETION (it requires exactly one
    dataset; the Book set is two), and not GRAPH_COMPLETION, which
    renders no Evidence block on this service (the 2026-09-12 live
    smoke, ticket #25; ADR 0006 — the negative fixture
    tests/fixtures/recall-graph-completion-8001.json locks it). The
    dataset subset narrows the search to the ask's selected Books
    (ADR-0010); None or an empty list means the whole Book set — the
    ASK-path rule. Research Mode's Tools sit one layer up: run_tool
    demands the session's picked Book and never substitutes this
    default. On any failure the searcher contributes nothing; the turn
    continues on its siblings' pools.
    """
    request = _recall_request(
        "HYBRID_COMPLETION",
        sub_question,
        list(datasets) if datasets else list(BOOK_DATASETS),
    )
    try:
        with urlopen(request, timeout=DIVE_SEARCH_TIMEOUT) as response:
            payload = json.load(response)
    except (OSError, ValueError):
        return []
    _notify_search(sub_question)
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
    # The searcher-seam meter, pool side: the fan-out's searchers run
    # on POOL threads, where the worker's thread-local listener never
    # lands — so the round itself notifies once per completed searcher,
    # on the round's own (worker) thread.
    for question in sub_questions:
        _notify_search(question)
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


# --- the Tool registry (ADR-0012, T4) ---------------------------------------

# The Book text layer's page marker, the service patch's twin
# (enable_farsi_evidence.py): a chunks reply's pages read from the same
# markers its Evidence bullets would.
_CHUNK_PAGE_MARKER = re.compile(r"Page\s+(\d+)\s*[:：]", re.IGNORECASE)


def parse_tool_concepts(payload) -> list:
    """The graph family's shape: the reply's graph-node labels in rank
    order, deduped. The completion's own text is model-written
    synthesis — the recorded live reply carries no Evidence block and
    no verbatim passage (the negative fixture) — so only the labels
    cross out of a graph reply, and only as steering for citable
    searches."""
    labels = []
    for item in payload if isinstance(payload, list) else []:
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata")
        entries = metadata.get("evidence") if isinstance(metadata, dict) else None
        for entry in entries or []:
            if not isinstance(entry, dict) or entry.get("kind") != "graph_node":
                continue
            label = entry.get("label")
            if isinstance(label, str) and label.strip() and label not in labels:
                labels.append(label.strip())
    return labels


def parse_tool_summaries(payload) -> list:
    """The summaries Tool's shape: each reply item's text is one
    pre-generated summary note — context for later skills, never the
    citable pool (a summary is cognee's words, not the Book's)."""
    return [
        item["text"].strip()
        for item in (payload if isinstance(payload, list) else [])
        if isinstance(item, dict) and isinstance(item.get("text"), str)
        and item["text"].strip()
    ]


def parse_tool_chunks(payload, datasets: list) -> list:
    """The chunks Tool's shape: each reply item's text IS the verbatim
    chunk, so the reference is built server-side — the Book identity
    from the item's dataset name, else the pick when it names exactly
    one Book, else the chunk drops (an unattributable passage is never
    quoted) — and the pages from the text layer's Page N: markers, the
    same provenance the service's Evidence bullets carry. No markers:
    the reference names the Book alone, never an invented page."""
    passages = []
    for item in payload if isinstance(payload, list) else []:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        dataset = item.get("dataset_name")
        if not isinstance(dataset, str) or not dataset.strip():
            dataset = datasets[0] if len(datasets) == 1 else None
        if not dataset:
            continue
        pages = sorted(
            {int(match.group(1)) for match in _CHUNK_PAGE_MARKER.finditer(text)}
        )
        if pages:
            span = (
                f"(page {pages[0]})"
                if pages[0] == pages[-1]
                else f"(pages {pages[0]}-{pages[-1]})"
            )
        else:
            span = ""
        passages.append(
            {"reference": f"document {dataset} {span}".strip(), "passage": text}
        )
    return passages


def run_tool(name: str, query: str, datasets, cancel=None) -> dict:
    """One Tool search through the registry (ADR-0012, T4): the entry's
    search type under the entry's OWN leash, the reply parsed by the
    entry's shape into {tool, shape, passages, concepts, notes} — only
    the shape's list fills. The registry is the only path to the second
    service's search modes: an unregistered name raises (the unsupported
    modes are unreachable by construction), and so does an empty pick —
    every Tool searches the session's picked Book, never a default. An
    upstream failure returns the empty result; the turn continues on
    what its other searches pooled, exactly as the hybrid searcher
    always has."""
    tool = TOOL_REGISTRY.get(name)
    if tool is None:
        raise ToolError(f"unregistered tool: {name}")
    if not datasets:
        raise ToolError(f"{name} refused: no picked Book")
    picked = list(datasets)
    result = {
        "tool": name,
        "shape": tool["shape"],
        "passages": [],
        "concepts": [],
        "notes": [],
    }
    if cancel is not None and cancel.is_set():
        return result
    request = _recall_request(tool["search_type"], query, picked)
    try:
        with urlopen(request, timeout=tool["timeout"]) as response:
            payload = json.load(response)
    except (OSError, ValueError):
        return result
    if tool["shape"] == "concepts":
        result["concepts"] = parse_tool_concepts(payload)
    elif tool["shape"] == "notes":
        result["notes"] = parse_tool_summaries(payload)
    elif name == "chunks":
        result["passages"] = parse_tool_chunks(payload, picked)
    else:
        for item in payload if isinstance(payload, list) else []:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                result["passages"].extend(parse_evidence_sources(item["text"]))
    return result


def graph_hop(seed, datasets, cancel=None, budget=None) -> tuple:
    """One bounded graph hop (ADR-0012, T4): a GRAPH_COMPLETION search
    over the seed — its node labels are the Book graph's own concepts —
    then at most GRAPH_HOP_FOLLOW_UP_CAP citable hybrid searches seeded
    `seed — label`, so the pool gains the angles one search mode misses.
    The hop is a bonus angle, never a spine: an unaffordable budget
    leaves before it starts (the worker's honest stop reports it), every
    follow-up is afford-checked then charged, and the labels ride back
    even when no follow-up ran — the map's concept row grows from them.
    Returns (passages, labels)."""
    if budget is not None and not budget.afford(1):
        return [], []
    hop = run_tool("graph", seed, datasets, cancel=cancel)
    if budget is not None:
        budget.charge(1)
    labels = hop["concepts"][:GRAPH_HOP_LABEL_CAP]
    passages = []
    ran = 0
    for label in labels:
        if ran >= GRAPH_HOP_FOLLOW_UP_CAP:
            break
        if cancel is not None and cancel.is_set():
            break
        if budget is not None and not budget.afford(1):
            break
        if budget is not None:
            budget.charge(1)
        fed = run_tool("hybrid", f"{seed} — {label}", datasets, cancel=cancel)
        ran += 1
        passages.extend(fed["passages"])
    return passages, labels
