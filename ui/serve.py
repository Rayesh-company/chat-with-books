#!/usr/bin/env python3
"""Serve the Farsi Session sheet, proxy first-answer recall to Cognee,
pick the Quote selection — the first answer rendered on the sheet
(ADR-0006, issue #28) — relay the recorded Next-tier COT probe to
cognee-next-tier (the sheet no longer calls it — the operator probe
remains), compose the Quoted answer for phase 2, and orchestrate the
Deep dive (ADR-0006): Planner, the bounded retrieval (round 1 plus at
most one gap round over the quote-starved sections, issue #27) on the
second Cognee service, Synthesizer."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

try:
    # The deep modules behind this facade; the re-exports below keep the
    # tests' single `from ui import serve` import seam.
    from ui.guard import (
        _citation_labels,
        _count_word,
        _json_object,
        _numbered_passages,
        _strip_code_fence,
        book_label,
        first_page_label,
        guard_blocks,
        guard_sentences,
        normalize_for_match,
        pages_label,
        parse_quoted_reply,
    )
    from ui.quotas import (
        DAILY_CHAT_LIMIT,
        chats_today,
        normalize_phone,
        record_chat,
    )
    from ui.composer import (
        COMPOSER_MAX_TOKENS,
        COMPOSER_TIMEOUT,
        _composer_content,
        _composer_reply,
        build_continuation_prompt,
        build_planner_prompt,
        build_quoted_prompt,
        compose_quoted_answer,
    )
except ImportError:  # the container runs this file as a script beside the modules
    from guard import (
        _citation_labels,
        _count_word,
        _json_object,
        _numbered_passages,
        _strip_code_fence,
        book_label,
        first_page_label,
        guard_blocks,
        guard_sentences,
        normalize_for_match,
        pages_label,
        parse_quoted_reply,
    )
    from quotas import (
        DAILY_CHAT_LIMIT,
        chats_today,
        normalize_phone,
        record_chat,
    )
    from composer import (
        COMPOSER_MAX_TOKENS,
        COMPOSER_TIMEOUT,
        _composer_content,
        _composer_reply,
        build_continuation_prompt,
        build_planner_prompt,
        build_quoted_prompt,
        compose_quoted_answer,
    )

UI_DIR = Path(__file__).resolve().parent
COGNEE_URL = os.environ.get("COGNEE_URL", "http://127.0.0.1:8000").rstrip("/")
# Phase 3 rides the second Cognee service (cognee-next-tier, port 8001)
# so a multi-minute graph search never blocks the first-answer path on
# 8000 (ADR-0002). Both read the same Postgres memory.
NEXT_TIER_URL = os.environ.get("NEXT_TIER_URL", "http://127.0.0.1:8001").rstrip("/")
HOST = os.environ.get("SESSION_UI_HOST", "127.0.0.1")
PORT = int(os.environ.get("SESSION_UI_PORT", "8765"))
PROXY_TIMEOUT = 600
# A Next-tier search runs four chain-of-thought rounds and can pass ten
# minutes (live logs, 2026-09-11) — past the shared 600s leash the relay
# once gave up on a search the second service had already finished.
# Phase 3 waits on its own leash; "unreachable: timed out" only after it.
NEXT_TIER_TIMEOUT = int(os.environ.get("NEXT_TIER_TIMEOUT", "1200"))
ALLOWED_PROXY = {"/health", "/api/v1/recall"}
# The Book set the sheet answers over; phase 3 pins the search shape
# here — never in the browser (README, Next-tier search).
BOOK_DATASETS = ["tarhe-kolli", "70143-336"]
NEXT_TIER_SEARCH_TYPE = "GRAPH_COMPLETION_COT"

# Deep dive (ADR-0006, tracer bullet issue #25): the study orchestration
# runs here, server-side — the browser names no Cognee search type. Each
# searcher pins its recall shape below (HYBRID_COMPLETION over the Book
# set, references on — not FEELING_LUCKY, not AGENTIC_COMPLETION, which
# needs exactly one dataset), riding the second Cognee service so a
# multi-minute dive never blocks the first-answer path on 8000. The
# Planner and Synthesizer run glm-5.3 (the next-tier model, ADR-0002),
# pinned in source like every model pin — never via env.
DIVE_MODEL = "glm-5.3"
# The 2026-09-12 live smoke (ticket #25) failed the dive: the pinned
# GRAPH_COMPLETION renders no Evidence block on the second service —
# its references arrive as graph-node metadata with no verbatim passage
# text — so every section's quote pool was empty by construction and all
# six sections starved through both retrieval rounds. The recorded real
# reply is the negative fixture tests/fixtures/
# recall-graph-completion-8001.json (locked to parse to zero passages).
# The same question over HYBRID_COMPLETION on the same service renders
# the Evidence contract the pool parser consumes — locators with Book
# identity and pages, verbatim passages (positive fixture
# recall-hybrid-completion-8001.json) — exactly the pool the
# synthesizer's verbatim guard needs. ADR 0006 records the switch;
# GRAPH_COMPLETION_DECOMPOSITION stays the recorded fallback for
# planner disappointment, which this was not. The model pin stays
# per-service, so the tier separation holds.
DIVE_SEARCH_TYPE = "HYBRID_COMPLETION"
# A graph-backed search can sit minutes on the second service; each
# searcher gets its own leash (the COT probe's four rounds fit inside
# ten minutes on live logs, and a decomposition pass is lighter).
DIVE_SEARCH_TIMEOUT = 600
# The Planner decomposes the question into at most this many Farsi
# sub-questions; as many searchers run in parallel. The planner prompt's
# count wording is derived from this constant — the two cannot drift.
DIVE_MAX_SUB_QUESTIONS = 6
# Starvation (issue #27): a section is quote-starved when round 1's
# searcher for it parsed FEWER than this many passages. Fewer than two
# passages cannot weave the quoted paragraph pair the study's body
# paragraphs are made of. The count is each searcher's own parsed
# Evidence pool — not the guard, which runs post-synthesizer — and a
# fed section is never re-searched.
DIVE_STARVED_PASSAGES = 2
# The topology's round lock (issue #27): round 1 plus at most one gap
# round over the starved sections. This is the bound that keeps the
# dive from growing an unbounded agentic loop — a pathological upstream
# that starves every section gets exactly two rounds, never a third.
DIVE_MAX_RETRIEVAL_ROUNDS = 2

# Quote selection (ADR-0006, issue #28): the first answer rendered on
# the sheet is the Evidence pool's Quote selection — verbatim Book
# sentences, no AI prose — picked by EXACTLY ONE composer call. The
# model is the existing COMPOSER_MODEL pin (glm-5.3-flash, ADR-0002);
# thinking disabled for the phase-2 writer's measured reason:
# copy-matching, not reasoning. The pool itself is untouched — the same
# (reference, passage) pairs stay phase 2's exact input.
QUOTE_SELECTION_AIM = 10
QUOTE_SELECTION_CEILING = 12
QUOTE_SELECTION_FLOOR = 4


def build_picker_prompt(question: str, sources) -> str:
    """The picker's brief: select, don't write.

    The aim wording is derived from QUOTE_SELECTION_AIM — the two cannot
    drift — and the reply shape is the selection list guard_sentences
    already consumes.
    """
    passages = _numbered_passages(sources)
    return (
        "You are selecting the Quote selection for a Farsi Q&A sheet "
        "over the Books.\n\n"
        f"Question: {question}\n\n"
        "Passages (numbered, from the Books' retrieved Evidence; "
        "text-layer noise like \\b backspaces may appear between "
        "words):\n"
        f"{passages}\n\n"
        "Task: select the Farsi sentences that together best answer "
        f"the question — aim for {_count_word(QUOTE_SELECTION_AIM)}; "
        "fewer only when the passages hold fewer. Each selection is a "
        "complete Farsi sentence copied VERBATIM from exactly ONE "
        "passage (ignore the \\b noise; write proper Farsi). Do not "
        "paraphrase, do not merge, do not shorten. Never invent a "
        "sentence.\n\n"
        "Reply with ONLY a JSON object, no prose, no code fence:\n"
        '{"selections": [{"text": "<verbatim sentence>", '
        '"source": <passage index>}]}'
    )


def parse_picker_reply(content):
    """Pull the selections list out of the picker's reply; [] when
    malformed.

    The parse conventions of parse_quoted_reply — code fence stripped,
    json.loads with one brace-scoped retry for prose-wrapped JSON, the
    shared _json_object — without the salvage: a picker reply is tiny,
    and a truncated one would not survive the floor anyway.
    """
    parsed = _json_object(content)
    selections = parsed.get("selections") if isinstance(parsed, dict) else None
    return selections if isinstance(selections, list) else []


def pick_quote_selection(question: str, sources):
    """One picker call over the pool; the guarded selections, or [].

    Exactly ONE composer call (glm-5.3-flash, thinking disabled,
    COMPOSER_MAX_TOKENS, endpoint-default temperature). The reply runs
    the existing verbatim letter-stream guard — a paraphrase, or a
    verbatim sentence claiming the wrong index, drops; the survivors cap
    at QUOTE_SELECTION_CEILING, and fewer than QUOTE_SELECTION_FLOOR of
    them means the picker missed the floor: [] — the same empty shape a
    call failure returns, so the sheet's fallback is one uniform shape.
    Every kept sentence carries the labels of exactly the passage it
    claims, attached server-side like guard_blocks does.
    """
    try:
        reply = _composer_reply(build_picker_prompt(question, sources), "disabled")
        content = _composer_content(reply)
    except (KeyError, ValueError, OSError):
        return []
    kept = guard_sentences(parse_picker_reply(content), sources)
    kept = kept[:QUOTE_SELECTION_CEILING]
    if len(kept) < QUOTE_SELECTION_FLOOR:
        return []
    return [
        {
            "text": item["text"],
            "reference": item["reference"],
            **_citation_labels(item["reference"]),
        }
        for item in kept
    ]


# Deep dive (ADR-0006, tracer bullet issue #25): the study orchestration.
# Module-level functions over the injectable urlopen, the same seam the
# Quoted answer tests script — one dive is Planner -> at most two
# retrieval rounds (the second only for quote-starved sections, issue
# #27) -> Synthesizer, and every upstream payload is pinned here,
# server-side; the sheet sends only the question.


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


def build_dive_planner_prompt(question: str) -> str:
    """The dive Planner's brief: decompose the question, not answer it."""
    return (
        "You are planning a Farsi Deep dive study over a fixed set of "
        "Books.\n\n"
        f"Question: {question}\n\n"
        "Task: decompose the question into distinct Farsi sub-questions "
        "whose answers together cover it — the facets, sub-themes, and "
        "cross-checks a scholarly study of the Books would need. Up to "
        f"{_count_word(DIVE_MAX_SUB_QUESTIONS)} sub-questions; fewer when "
        "the question is narrow. Each "
        "sub-question must be answerable from the Books on its own. "
        "Never answer them yourself.\n\n"
        "Reply with ONLY a JSON array of Farsi strings, no prose, no "
        "code fence:\n"
        '["زیرپرسش اول؟", "زیرپرسش دوم؟"]'
    )


def dive_subquestions_from_reply(content, question: str) -> list:
    """Guard the Planner's reply into sub-questions; [question] if unusable.

    Parse the JSON list (a code fence is stripped first), keep the
    non-empty string items, cap at DIVE_MAX_SUB_QUESTIONS. A reply that
    is not a usable list — or one with nothing left after the guard —
    falls back to the raw question, so a dive never dies at planning.
    """
    stripped = _strip_code_fence(content) if isinstance(content, str) else ""
    parsed = None
    try:
        parsed = json.loads(stripped)
    except ValueError:
        match = re.search(r"\[.*\]", stripped, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(0))
            except ValueError:
                parsed = None
    if not isinstance(parsed, list):
        return [question]
    sub_questions = [
        item.strip()
        for item in parsed
        if isinstance(item, str) and item.strip()
    ]
    return sub_questions[:DIVE_MAX_SUB_QUESTIONS] or [question]


def plan_dive_subquestions(question: str) -> list:
    """One Planner call (glm-5.3, thinking on); the sub-questions.

    On any planner failure the fallback is the raw question alone —
    the same never-empty shape as the phase-2 planner (AC-4): a dive
    degrades to a single search, it never dies at planning.
    """
    try:
        reply = _composer_reply(
            build_dive_planner_prompt(question), "enabled", DIVE_MODEL
        )
        content = _composer_content(reply)
    except (KeyError, ValueError, OSError):
        return [question]
    return dive_subquestions_from_reply(content, question)


def dive_recall(sub_question: str) -> list:
    """One searcher: the pinned recall on the second Cognee service.

    The search shape is pinned HERE, never in the browser:
    HYBRID_COMPLETION over the Book set with references on — not
    FEELING_LUCKY, not AGENTIC_COMPLETION (it requires exactly one
    dataset; the Book set is two), and no longer GRAPH_COMPLETION,
    which renders no Evidence block on this service (the 2026-09-12
    live smoke, ticket #25; ADR 0006). On any failure the searcher
    contributes nothing; the dive continues on its siblings' pools.
    """
    request = Request(
        f"{NEXT_TIER_URL}/api/v1/recall",
        data=json.dumps(
            {
                "searchType": DIVE_SEARCH_TYPE,
                "query": sub_question,
                "datasets": list(BOOK_DATASETS),
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


def run_dive_round(sub_questions, sources, seen) -> list:
    """One retrieval round: as many searchers as sub-questions run at
    once (stdlib threads), each on its own DIVE_SEARCH_TIMEOUT leash.

    Every searcher's parsed pool merges into `sources` in sub-question
    order — APPEND only, never re-ordering, so the passage indices the
    synthesizer prompt shows stay stable across rounds — and identical
    passages deduplicate against everything already pooled. Returns one
    parsed count per sub-question, in order, taken from each searcher's
    own Evidence pool before dedupe: the starvation measure (issue #27).
    """
    workers = max(1, len(sub_questions))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pools = list(pool.map(dive_recall, sub_questions))
    for result in pools:
        for source in result:
            passage = source["passage"]
            if passage in seen:
                continue
            seen.add(passage)
            sources.append(source)
    return [len(result) for result in pools]


def build_dive_prompt(question: str, sources) -> str:
    """The Synthesizer's brief: the headed, quoted Farsi study."""
    passages = _numbered_passages(sources)
    return (
        "You are writing a Farsi Deep dive study for a Q&A sheet over a "
        "fixed set of Books.\n\n"
        f"Question: {question}\n\n"
        "Passages (numbered, retrieved from the Books' Evidence per "
        "sub-question; text-layer noise like \\b backspaces may appear "
        "between words):\n"
        f"{passages}\n\n"
        "Task: write the study as a multi-section document of 2,000 to "
        "4,000 Farsi words in five to eight headed sections.\n"
        "Every body paragraph is one unit: your own Farsi text with "
        "quoted sentences embedded inside it — your text, then a quoted "
        "sentence, then more of your text, as the argument needs. "
        "Introduce each section, connect the quotes, and summarize what "
        "they establish; never state a Book claim the passages do not "
        "support. A paragraph may quote from more than one passage. Each "
        "quoted sentence is a complete Farsi sentence copied VERBATIM "
        "from exactly ONE passage (ignore the \\b noise; write proper "
        "Farsi). Do not paraphrase, do not merge, do not shorten. Never "
        "write a paragraph without at least one quoted sentence, and "
        "never a paragraph of bare quotes without your connective text. "
        "Never invent a quote.\n"
        "Do NOT write a references list or a references section — the "
        "sheet appends the references itself from the real passages.\n\n"
        "Reply with ONLY a JSON object, no prose, no code fence:\n"
        '{"blocks": [{"type": "heading", "text": "..."}, '
        '{"type": "paragraph", "parts": [{"text": "..."}, '
        '{"quote": "<verbatim sentence>", "source": <passage index>}, '
        '{"text": "..."}]}]}'
    )


def build_dive_continuation_prompt(question: str, sources, blocks: list) -> str:
    """The dive's resume brief after a length-cut study: the same brief
    plus the blocks that completed before the cut; the writer writes
    only what follows them."""
    passages = _numbered_passages(sources)
    written = json.dumps({"blocks": blocks}, ensure_ascii=False)
    return (
        "You are continuing a Farsi Deep dive study for a Q&A sheet over "
        "a fixed set of Books.\n\n"
        f"Question: {question}\n\n"
        "Passages (numbered, retrieved from the Books' Evidence per "
        "sub-question; text-layer noise like \\b backspaces may appear "
        "between words):\n"
        f"{passages}\n\n"
        "A previous write of this study was cut by a reply length "
        "limit. The blocks that completed before the cut (JSON):\n"
        f"{written}\n\n"
        "Task: continue that SAME document. Write ONLY the blocks that "
        "come AFTER the last block above — the remaining headings and "
        "interleaved paragraphs, in the same JSON object shape. Every "
        "paragraph follows the same rule as before: your own Farsi text "
        "with quoted sentences embedded inside it, each quoted sentence "
        "a complete Farsi sentence copied VERBATIM from exactly ONE "
        "passage (ignore the \\b noise; write proper Farsi). Do not "
        "paraphrase, do not merge. Never repeat a block that is already "
        "written; never invent or paraphrase a quote. Do NOT write a "
        'references list. If nothing is missing, reply with an empty '
        'list: {"blocks": []}.\n\n'
        "Reply with ONLY a JSON object, no prose, no code fence:\n"
        '{"blocks": [{"type": "heading", "text": "..."}, '
        '{"type": "paragraph", "parts": [{"text": "..."}, '
        '{"quote": "<verbatim sentence>", "source": <passage index>}, '
        '{"text": "..."}]}]}'
    )


def continue_dive_study(question: str, sources, blocks: list):
    """One continuation call past a length-cut study; (blocks, truncated).

    The same single-continuation shape as phase 2: a continuation cut
    again (or failed) rides on as the salvaged prefix, truncated True.
    """
    try:
        reply = _composer_reply(
            build_dive_continuation_prompt(question, sources, blocks),
            "disabled",
            DIVE_MODEL,
        )
        content = _composer_content(reply)
    except (KeyError, ValueError, OSError):
        return blocks, True
    extra = parse_quoted_reply(content)
    return blocks + extra, reply["choices"][0].get("finish_reason") == "length"


def compose_dive_study(question: str, sources):
    """One Synthesizer call (glm-5.3, thinking disabled) writes the
    study; (guarded blocks, truncated).

    Thinking stays off for the same reason as the phase-2 writer:
    copied sentences must survive the verbatim guard. A reply cut by
    the output ceiling is salvaged and continued ONCE — a doubly-cut
    study lands the guarded prefix, flagged. The guard is phase 2's,
    as-is.
    """
    try:
        reply = _composer_reply(
            build_dive_prompt(question, sources), "disabled", DIVE_MODEL
        )
        content = _composer_content(reply)
    except (KeyError, ValueError, OSError):
        return [], False
    blocks = parse_quoted_reply(content)
    if reply["choices"][0].get("finish_reason") != "length":
        return guard_blocks(blocks, sources), False
    blocks, truncated = continue_dive_study(question, sources, blocks)
    return guard_blocks(blocks, sources), truncated


def with_dive_references(blocks: list, sources) -> list:
    """Append the closing references block, built server-side.

    The items are the references of the passages the guarded study
    actually quoted, in order of first use — guaranteed real, never
    model-invented. An empty study (the guard kept nothing) appends
    nothing: there is no study to close.
    """
    used = []
    for block in blocks:
        if block.get("type") != "paragraph":
            continue
        for part in block.get("parts", []):
            index = part.get("source")
            if (
                isinstance(index, int)
                and not isinstance(index, bool)
                and 0 <= index < len(sources)
            ):
                reference = sources[index]["reference"]
                if reference not in used:
                    used.append(reference)
    if not used:
        return blocks
    return blocks + [{"type": "references", "items": used}]


# The dive is a job in an in-process registry (ADR-0006, issue #26), not
# a held request: the start answers a job identity immediately, the sheet
# polls the status endpoint, and multiple users get fair, independent
# dives. Stdlib only — a dict, a lock, threads; no broker, no new
# dependencies. A server restart empties the registry, so an unknown job
# id after a restart IS the recorded failure surface (the sheet reports
# the dive as unfinished, never a hang).

DIVE_TERMINAL_STATES = {"done", "failed", "aborted"}
DIVE_MAX_CONCURRENT = 3
DIVE_BUSY_PHONE_DETAIL = (
    "یک مطالعۀ عمیق برای این شماره هم‌اکنون در جریان است؛ لطفاً صبور باشید."
)
DIVE_BUSY_GLOBAL_DETAIL = (
    "هم‌اکنون چند مطالعۀ عمیق در جریان است؛ کمی بعد دوباره تلاش کنید."
)
DIVE_NOT_FOUND_DETAIL = "چنین مطالعه‌ای پیدا نشد."
DIVE_FAILED_DETAIL = "مطالعۀ عمیق ناتمام ماند؛ خطای غیرمنتظره."
DIVE_NO_EVIDENCE_DETAIL = "مطالعۀ عمیق ناتمام ماند؛ نقل‌قولی از کتاب‌ها پیدا نشد."

# Farsi progress events, one appended at each state transition — the
# status surface's observable timeline of the dive.
DIVE_EVENT_PLANNING = "برنامه‌ریزی مطالعه…"
DIVE_EVENT_SEARCHING = "جست‌وجوی کتاب‌ها…"
DIVE_EVENT_WRITING = "نوشتن مطالعۀ عمیق…"
DIVE_EVENT_DONE = "مطالعۀ عمیق آماده شد."
DIVE_EVENT_FAILED = "مطالعۀ عمیق ناتمام ماند."
DIVE_EVENT_ABORTED = "مطالعۀ عمیق لغو شد."

# The gap round's progress event (issue #27), announced BEFORE the
# re-search runs while the state remains "searching" — the sheet renders
# the latest event, so the operator sees the second round start. The
# count lands in Persian digits: «۱ بخشِ کم‌نقل دوباره جست‌وجو می‌شود…» /
# «۲ بخشِ کم‌نقل دوباره جست‌وجو می‌شوند…».
_FARSI_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def _dive_gap_event(starved: int) -> str:
    digits = str(starved).translate(_FARSI_DIGITS)
    verb = "می‌شود" if starved == 1 else "می‌شوند"
    return f"{digits} بخشِ کم‌نقل دوباره جست‌وجو {verb}…"


# Recorded limit (YAGNI): terminal jobs are never reaped — they linger
# in this dict by design. The caps scan non-terminal jobs only, and a
# server restart empties the registry, so growth is bounded by one
# restart cycle. Revisit pruning with the durable-execution upgrade.
DIVE_REGISTRY = {}
DIVE_REGISTRY_LOCK = threading.Lock()


class DiveJob:
    """One dive in the registry: identity, ownership, the observable
    timeline (state + Farsi events), the outcome, and the cooperative
    cancel flag. `done` is set when the worker thread has fully exited —
    the tests' leash on dive threads leaking across tests."""

    def __init__(self, phone: str, query: str):
        self.id = uuid.uuid4().hex
        self.phone = phone
        self.query = query
        self.state = "planning"
        self.events = [DIVE_EVENT_PLANNING]
        self.result = None
        self.error = None
        self.started_at = time.monotonic()
        self.cancel = threading.Event()
        self.done = threading.Event()


def dive_retrieve(job: DiveJob, sub_questions) -> list:
    """The bounded retrieval (issue #27): round 1 searches every
    sub-question; sections that come back quote-starved — fewer than
    DIVE_STARVED_PASSAGES parsed passages, too few to weave a quoted
    paragraph pair — re-search AT MOST ONCE, in parallel, only the
    starved ones. DIVE_MAX_RETRIEVAL_ROUNDS is the lock that keeps the
    dive from growing an unbounded agentic loop: an always-starved
    upstream gets exactly two rounds, never a third, and the dive
    proceeds on whatever pooled (an empty pool lands the no-evidence
    failure in the worker). The gap round is announced before it runs —
    a Farsi event naming the starved count, visible in the status
    events while the state remains "searching" — and the cancel flag is
    checked between rounds so an abort lands promptly."""
    sources = []
    seen = set()
    pending = list(sub_questions)
    rounds = 0
    while pending and rounds < DIVE_MAX_RETRIEVAL_ROUNDS:
        if job.cancel.is_set():
            return sources
        rounds += 1
        counts = run_dive_round(pending, sources, seen)
        pending = [
            question
            for question, count in zip(pending, counts)
            if count < DIVE_STARVED_PASSAGES
        ]
        if pending and rounds < DIVE_MAX_RETRIEVAL_ROUNDS:
            _dive_advance(job, event=_dive_gap_event(len(pending)))
    return sources


def run_dive_job(job: DiveJob) -> None:
    """The dive worker: the orchestration stages above with the job's
    state transitions and cancel flag interleaved at the stage
    boundaries. Cancellation is cooperative — an in-flight urlopen is
    never interrupted mid-call; it resolves within its own leash and the
    worker stops at the next boundary without writing a result. Any
    worker failure marks the job `failed` with a short Farsi detail; the
    endpoint never 500s from this thread."""
    try:
        sub_questions = plan_dive_subquestions(job.query)
        if job.cancel.is_set():
            return
        _dive_advance(job, "searching", DIVE_EVENT_SEARCHING)
        sources = dive_retrieve(job, sub_questions)
        if job.cancel.is_set():
            return
        if not sources:
            _dive_fail(job, DIVE_NO_EVIDENCE_DETAIL)
            return
        _dive_advance(job, "synthesizing", DIVE_EVENT_WRITING)
        blocks, truncated = compose_dive_study(job.query, sources)
        with DIVE_REGISTRY_LOCK:
            # The abort may have landed while the synthesizer wrote —
            # every writer refuses a settled job, so a finished result
            # is discarded, never resurrecting the dive to `done`.
            if job.state in DIVE_TERMINAL_STATES:
                return
            job.result = (with_dive_references(blocks, sources), truncated)
            job.state = "done"
            job.events.append(DIVE_EVENT_DONE)
    except Exception:
        _dive_fail(job, DIVE_FAILED_DETAIL)
    finally:
        job.done.set()


def _dive_write(
    job: DiveJob,
    state: str | None = None,
    event: str | None = None,
    error: str | None = None,
) -> bool:
    """One atomic non-terminal write under the registry lock: the state,
    the error detail, and the timeline event land together or not at
    all. A settled job is never overwritten — the worker's cancel checks
    run outside the lock, so an abort can land between a check and this
    write — clobbering `aborted` here would leave a zombie job holding
    the caps until restart. Returns whether the write landed."""
    with DIVE_REGISTRY_LOCK:
        if job.state in DIVE_TERMINAL_STATES:
            return False
        if state is not None:
            job.state = state
        if error is not None:
            job.error = error
        if event is not None:
            job.events.append(event)
        return True


def _dive_advance(
    job: DiveJob, state: str | None = None, event: str | None = None
) -> None:
    """One non-terminal stage write, under the lock. With a state it
    marks the stage transition; with only an event it is a mid-stage
    note — the state stays at the live stage (the gap round is announced
    from inside "searching")."""
    _dive_write(job, state, event)


def _dive_fail(job: DiveJob, detail: str) -> None:
    _dive_write(job, "failed", DIVE_EVENT_FAILED, error=detail)


def abort_dive_job(job: DiveJob) -> bool:
    """Abort one dive: the cancel flag and the `aborted` state land
    together under the lock, so the status surface reads the abort right
    away while the worker exits at its next boundary. A settled job is
    never re-marked."""
    with DIVE_REGISTRY_LOCK:
        if job.state in DIVE_TERMINAL_STATES:
            return False
        job.cancel.set()
        job.state = "aborted"
        job.events.append(DIVE_EVENT_ABORTED)
        return True


def _start_dive_job(phone: str, query: str):
    """Create the registry job under the caps — at most one non-terminal
    dive per phone and DIVE_MAX_CONCURRENT globally — or return the
    Farsi busy detail. The check and the creation are atomic under the
    lock, so racing starts can never exceed a cap, and a rejected start
    is never queued: the caller replies the busy rejection as-is."""
    with DIVE_REGISTRY_LOCK:
        non_terminal = [
            job
            for job in DIVE_REGISTRY.values()
            if job.state not in DIVE_TERMINAL_STATES
        ]
        if any(job.phone == phone for job in non_terminal):
            return None, DIVE_BUSY_PHONE_DETAIL
        if len(non_terminal) >= DIVE_MAX_CONCURRENT:
            return None, DIVE_BUSY_GLOBAL_DETAIL
        job = DiveJob(phone, query)
        DIVE_REGISTRY[job.id] = job
    threading.Thread(target=run_dive_job, args=(job,), daemon=True).start()
    return job, None


class SessionHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(UI_DIR), **kwargs)

    def log_message(self, format, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))

    def end_headers(self) -> None:
        # The sheet is an evolving single page: every load must revalidate,
        # never render a stale cached copy after a deploy (a plain refresh
        # kept showing the pre-tab page after the 2026-09-11 switchover).
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/livez":
            # Local liveness for the container healthcheck — the sheet's
            # /health proxies to Cognee and would couple this container's
            # health to another service's.
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/health":
            self._proxy("GET")
            return
        if path == "/deep-dive/status":
            self._deep_dive_status()
            return
        super().do_GET()

    def _drain_request_body(self) -> None:
        """Read the body Content-Length promised before answering and
        closing. Closing with bytes unread makes the kernel answer RST,
        not FIN, and the response we just wrote can be lost to the reset
        (WinError 10054 flaking the gate tests; through nginx the same
        reset can surface as a 502 instead of the gate's 429)."""
        length = int(self.headers.get("Content-Length", "0") or "0")
        while length > 0:
            chunk = self.rfile.read(min(length, 65536))
            if not chunk:
                break
            length -= len(chunk)

    def _json_error(self, status: int, detail: str) -> None:
        self._drain_request_body()
        self._send_json(status, {"detail": detail})

    def _send_json(self, status: int, payload: dict) -> None:
        """One JSON reply. The caller must have consumed the request body
        already (or never had one) — unlike _json_error this does not
        drain, so a second read cannot block on bytes already taken."""
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _gate_phone(self):
        """The ask gate: a valid phone with chats left today; records the
        chat. Answers 400/429 itself and returns None when rejected."""
        phone = normalize_phone(self.headers.get("X-Session-Phone", ""))
        if not phone:
            self._json_error(400, "شمارهٔ تلفن همراه را وارد کنید.")
            return None
        if chats_today(phone) >= DAILY_CHAT_LIMIT:
            self._json_error(
                429, "شمار گفتگوهای امروز این شماره پر شده است؛ فردا بیایید."
            )
            return None
        record_chat(phone)
        return phone

    def _quoted_phone(self):
        """The phase-2 gate: a valid phone with at least one chat today
        (the quoted answer belongs to a chat that already started)."""
        phone = normalize_phone(self.headers.get("X-Session-Phone", ""))
        if not phone:
            self._json_error(400, "شمارهٔ تلفن همراه را وارد کنید.")
            return None
        if chats_today(phone) < 1:
            self._json_error(
                429, "پاسخ استنادی بخشی از همان گفتگو است؛ اول یک پرسش بپرسید."
            )
            return None
        return phone

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/v1/recall":
            phone = self._gate_phone()
            if phone is None:
                return
            # A new ask owns the sheet (issue #26): after the gate has
            # recorded the chat, the phone's own in-flight dive is
            # aborted — cooperatively; the worker exits at its next
            # boundary. Another phone's dive is never touched.
            with DIVE_REGISTRY_LOCK:
                own_dives = [
                    job
                    for job in DIVE_REGISTRY.values()
                    if job.phone == phone
                    and job.state not in DIVE_TERMINAL_STATES
                ]
            for job in own_dives:
                abort_dive_job(job)
            self._proxy("POST")
            return
        if path == "/quoted-answer":
            if self._quoted_phone() is None:
                return
            self._quoted_answer()
            return
        if path == "/quote-selection":
            if self._quoted_phone() is None:
                return
            self._quote_selection()
            return
        if path == "/deep-dive":
            phone = self._quoted_phone()
            if phone is None:
                return
            self._deep_dive(phone)
            return
        if path == "/next-tier-recall":
            if self._quoted_phone() is None:
                return
            self._next_tier_recall()
            return
        self._drain_request_body()
        self.send_error(404, "Not found")

    def _quoted_answer(self) -> None:
        """Compose the Quoted answer; empty blocks = fallback."""
        length = int(self.headers.get("Content-Length", "0") or "0")
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            question = payload["question"]
            answer = payload.get("answer")
            sources = [
                {
                    "reference": source["reference"],
                    "passage": source["passage"],
                }
                for source in payload["sources"]
                if isinstance(source, dict)
                and isinstance(source.get("reference"), str)
                and isinstance(source.get("passage"), str)
            ]
            if not isinstance(question, str) or not question.strip() or not sources:
                raise ValueError("question and sources are required")
            if not isinstance(answer, str):
                answer = ""
        except (ValueError, KeyError, TypeError):
            self.send_error(400, "Bad request")
            return
        blocks, truncated = compose_quoted_answer(question, answer, sources)
        body = json.dumps(
            {"blocks": blocks, "truncated": truncated}, ensure_ascii=False
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _quote_selection(self) -> None:
        """Pick the Quote selection (ADR-0006, issue #28): the pool
        exactly as the sheet parsed it, one picker call, the guarded
        selections back. The gate is phase 2's shape — the picker
        belongs to the chat phase 1 recorded — so it needs a phone with
        at least one chat today and never records or counts one. A
        malformed body or an empty pool answers 400 (a JSON detail, the
        gate's shape) before any upstream call; a picker failure or a
        below-floor selection answers 200 {"selections": []} — the one
        uniform empty shape the sheet's prose fallback consumes, never
        a 5xx."""
        length = int(self.headers.get("Content-Length", "0") or "0")
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            question = payload["question"]
            sources = [
                {
                    "reference": source["reference"],
                    "passage": source["passage"],
                }
                for source in payload["sources"]
                if isinstance(source, dict)
                and isinstance(source.get("reference"), str)
                and isinstance(source.get("passage"), str)
            ]
            if not isinstance(question, str) or not question.strip() or not sources:
                raise ValueError("question and sources are required")
        except (ValueError, KeyError, TypeError):
            # The body is already read above, so _send_json is safe —
            # _json_error would drain a second time and block.
            self._send_json(
                400, {"detail": "پرسش و استنادهای بازیابی‌شده را بفرستید."}
            )
            return
        selections = pick_quote_selection(question, sources)
        self._send_json(200, {"selections": selections, "pool_size": len(sources)})

    def _deep_dive_status(self) -> None:
        """The dive's poll surface (issue #26): the sheet asks for a
        job's state, Farsi events, and elapsed seconds — and, when the
        job settled, its outcome. `done` carries the study payload
        ({"blocks", "truncated"}), `failed` a short Farsi detail, and an
        unknown id (a restart emptied the registry, or a foreign phone)
        answers 404 — the recorded failure surface, never a hang. The
        phone must match the job's: one user's poll never reads another
        user's dive."""
        phone = normalize_phone(self.headers.get("X-Session-Phone", ""))
        query = parse_qs(urlparse(self.path).query)
        job_id = (query.get("job") or [""])[0]
        payload = None
        with DIVE_REGISTRY_LOCK:
            job = DIVE_REGISTRY.get(job_id)
            if job is not None and job.phone == phone:
                payload = {
                    "state": job.state,
                    "events": list(job.events),
                    "elapsed": round(time.monotonic() - job.started_at, 1),
                }
                if job.state == "done":
                    blocks, truncated = job.result
                    payload["blocks"] = blocks
                    payload["truncated"] = truncated
                elif job.state == "failed":
                    payload["detail"] = job.error
        if payload is None:
            self._json_error(404, DIVE_NOT_FOUND_DETAIL)
            return
        self._send_json(200, payload)

    def _deep_dive(self, phone: str) -> None:
        """The Deep dive start (ADR-0006, issue #26): the gate is the
        phase-2 shape exactly — the dive belongs to the chat phase 1
        recorded, so it needs a phone with at least one chat today and
        never counts or checks the limit. The body carries only the
        query; every upstream payload is pinned server-side. The dive
        runs on its own registry job and this handler answers the job
        identity immediately: 202 {"job_id": ...} — the sheet polls
        /deep-dive/status for state, events, and the study. Nothing is
        ever queued: a busy phone (or a full registry) is rejected, not
        deferred."""
        length = int(self.headers.get("Content-Length", "0") or "0")
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            query = payload["query"]
            if not isinstance(query, str) or not query.strip():
                raise ValueError("query is required")
        except (ValueError, KeyError, TypeError):
            # The body is already read above, so _send_json is safe —
            # _json_error would drain a second time and block.
            self._send_json(400, {"detail": "پرسش را بنویسید."})
            return
        job, busy_detail = _start_dive_job(phone, query.strip())
        if job is None:
            self._send_json(429, {"detail": busy_detail})
            return
        self._send_json(202, {"job_id": job.id})

    def _next_tier_recall(self) -> None:
        """The recorded Next-tier COT relay (ADR-0005), kept live as the
        Session operator's probe of the second service — the sheet no
        longer calls it (the dive replaced the auto-start). The search
        shape is pinned here, never chosen in the browser:
        GRAPH_COMPLETION_COT over the Book set, references on, not
        streamed (the operator waits for the JSON). Same chat as phase 1
        — the gate only checks a chat happened today and never counts."""
        length = int(self.headers.get("Content-Length", "0") or "0")
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            query = payload["query"]
            if not isinstance(query, str) or not query.strip():
                raise ValueError("query is required")
        except (ValueError, KeyError, TypeError):
            # The body is already read above, so _send_json is safe —
            # _json_error would drain a second time and block.
            self._send_json(400, {"detail": "پرسش را بنویسید."})
            return
        body = json.dumps(
            {
                "searchType": NEXT_TIER_SEARCH_TYPE,
                "query": query.strip(),
                "datasets": list(BOOK_DATASETS),
                "includeReferences": True,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = Request(
            f"{NEXT_TIER_URL}/api/v1/recall",
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        self._relay(request, "next-tier", timeout=NEXT_TIER_TIMEOUT)

    def _relay(self, request: Request, service: str, timeout: int = PROXY_TIMEOUT) -> None:
        """Relay one upstream reply — JSON as-is, text/event-stream
        unbuffered — with the unreachable contract (504 + detail)."""
        try:
            with urlopen(request, timeout=timeout) as resp:
                content_type = resp.headers.get("Content-Type", "application/json")
                if "text/event-stream" in content_type:
                    # Close-delimited relay (HTTP/1.0): no Content-Length, lines
                    # hit the socket as they arrive. readline() because read(n)
                    # would block until n bytes collect.
                    self.send_response(resp.status)
                    self.send_header("Content-Type", content_type)
                    self.end_headers()
                    try:
                        while True:
                            line = resp.readline()
                            if not line:
                                break
                            self.wfile.write(line)
                    except OSError:
                        sys.stderr.write(
                            "%s - stream client went away\n" % self.address_string()
                        )
                    return
                payload = resp.read()
                self.send_response(resp.status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
        except HTTPError as exc:
            payload = exc.read()
            self.send_response(exc.code)
            self.send_header("Content-Type", exc.headers.get("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except (URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            message = json.dumps(
                {"detail": f"{service} unreachable: {reason}"},
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_response(504)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(message)))
            self.end_headers()
            self.wfile.write(message)

    def _proxy(self, method: str) -> None:
        path = self.path.split("?", 1)[0]
        sys.stderr.write("%s - proxy %s %s\n" % (self.address_string(), method, path))
        sys.stderr.flush()
        if path not in ALLOWED_PROXY:
            self._drain_request_body()
            self.send_error(404, "Not found")
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length) if length else None
        headers = {}
        content_type = self.headers.get("Content-Type")
        if content_type:
            headers["Content-Type"] = content_type
        request = Request(
            f"{COGNEE_URL}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        self._relay(request, "cognee")


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), SessionHandler)
    print(f"Session sheet http://{HOST}:{PORT}", flush=True)
    print(f"Proxying /api/v1/recall and /health to {COGNEE_URL}", flush=True)
    print(f"Relaying /next-tier-recall to {NEXT_TIER_URL}", flush=True)
    print(f"Orchestrating /deep-dive against {NEXT_TIER_URL}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
