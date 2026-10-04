"""The Tool registry's contract locks (ticket T4, GitLab issue #5, the
ADR-0012 registry): every way of searching the Book set beyond hybrid
retrieval — graph completion, decomposition/context-extension,
summaries, chunks — is a registry entry with its OWN leash and result
shape; skill rows declare the names; every Tool searches only the
session's picked Book; and the unsupported modes (Cypher/NL, agentic
completion) are unreachable by construction — no caller can name what
the registry does not hold.

The recorded live graph reply (tests/fixtures/
recall-graph-completion-8001.json, the 2026-09-12 negative probe) is
the shape source here: a graph completion's text is model-written
synthesis — never a verbatim passage — so only its node labels cross
out, steering citable searches. The end-to-end lock rides the turn
seam: a gather whose ledger gains passages sourced through the graph
hop."""

from tests.conftest import REPO_ROOT

import json
import sys  # noqa: E402

sys.path.insert(0, str(REPO_ROOT))

from tests.upstream_fakes import (  # noqa: E402
    SENTENCE,
    OTHER_SENTENCE,
    classify_reply,
    composer_reply,
    make_session,
    run_turn_sync,
    ResearchUpstream,
)
from ui import dive, research  # noqa: E402
from ui.guard import book_label, pages_label  # noqa: E402

PICK = ["tarhe-kolli"]

# The recorded live reply's own labels, in rank order — the graph
# family's context shape, read straight off the negative fixture.
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "recall-graph-completion-8001.json"
FIXTURE_LABELS = ["مسئله عمل", "concept", "اَحَدٌ اَحَدٌ", "slogan"]

# One Farsi concept label, the shape the live graph nodes carry — the
# hop's follow-up query steers by it.
HOP_LABEL = "مفهوم گرافی"

EXCLUDED_SEARCH_TYPES = (
    "CYPHER",
    "NATURAL_LANGUAGE",
    "AGENTIC_COMPLETION",
    "FEELING_LUCKY",
    "GRAPH_COMPLETION_COT",
)


def graph_payload():
    """One graph-completion item in the recorded envelope: model text,
    no Evidence block, the node labels under metadata.evidence."""
    return [
        {
            "kind": "graph_completion",
            "search_type": "GRAPH_COMPLETION",
            "text": "نوشتۀ مدل، نه نقل قول.",
            "metadata": {
                "evidence": [
                    {"kind": "graph_node", "label": HOP_LABEL, "rank": 0},
                    {"kind": "graph_node", "label": "دیگر", "rank": 1},
                ]
            },
        }
    ]


def fed_by_query(payload):
    """The well-fed hybrid recall, query-specific — the searcher's own
    Evidence pool, locators carrying pages."""
    query = payload["query"]
    return [
        {
            "text": (
                "پاسخ.\n\nEvidence:\n"
                f"- chunk 1 of document tarhe-kolli (pages 10-12): "
                f"\"{query} — {SENTENCE}\"\n"
                f"- chunk 29 of document tarhe-kolli: "
                f"\"{query} — {OTHER_SENTENCE}\""
            )
        }
    ]


def call_tool(name, query, pick, payload):
    """One direct run_tool over a scripted reply — dive's ONE urlopen
    seam patched for the call, restored after."""
    def recall(_):
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    original = dive.urlopen
    dive.urlopen = ResearchUpstream(recall_reply=recall)
    try:
        return dive.run_tool(name, query, pick)
    finally:
        dive.urlopen = original


# --- the registry as data ----------------------------------------------------


def test_the_registry_declares_a_complete_entry_per_tool():
    # Six Tools: hybrid plus the five the ADR wires. Every entry names
    # its search type, its OWN leash, and the shape its reply parses to.
    assert set(dive.TOOL_REGISTRY) == {
        "hybrid",
        "chunks",
        "graph",
        "decomposition",
        "context_extension",
        "summaries",
    }
    shapes = {
        "hybrid": "passages",
        "chunks": "passages",
        "graph": "concepts",
        "decomposition": "concepts",
        "context_extension": "concepts",
        "summaries": "notes",
    }
    for name, entry in dive.TOOL_REGISTRY.items():
        assert entry["search_type"], name
        assert isinstance(entry["timeout"], int) and entry["timeout"] > 0, name
        assert entry["shape"] == shapes[name], name
    # The recorded leashes: the hybrid keeps the dive's own fuse; the
    # vector-only Tools ride the short one.
    assert dive.TOOL_REGISTRY["hybrid"]["timeout"] == dive.DIVE_SEARCH_TIMEOUT
    assert dive.TOOL_REGISTRY["chunks"]["timeout"] == dive.TOOL_FAST_SEARCH_TIMEOUT
    assert (
        dive.TOOL_REGISTRY["summaries"]["timeout"] == dive.TOOL_FAST_SEARCH_TIMEOUT
    )
    # Every search type is distinct — a registry with two names on one
    # mode would be a double-counted Tool.
    assert (
        len({entry["search_type"] for entry in dive.TOOL_REGISTRY.values()})
        == len(dive.TOOL_REGISTRY)
    )


def test_the_gather_row_declares_the_registry():
    # Skill rows declare Tools by NAME: the gather may use every
    # registered Tool; a name outside the registry can never appear.
    row = research.SKILL_TABLE_BY_NAME["active_research"]
    assert set(row["tools"]) <= set(dive.TOOL_REGISTRY)
    assert "graph" in row["tools"]


def test_unsupported_modes_stay_unreachable():
    # By construction: no registry entry names an excluded mode, and no
    # caller can run what the registry does not hold — the refusal is a
    # ToolError, never a silent empty pool.
    named = {entry["search_type"] for entry in dive.TOOL_REGISTRY.values()}
    assert named.isdisjoint(EXCLUDED_SEARCH_TYPES)
    for name in ("cypher", "natural_language", "agentic", "feeling_lucky", ""):
        try:
            dive.run_tool(name, "پرسش؟", PICK)
        except dive.ToolError:
            continue
        raise AssertionError(f"{name or '<empty>'} ran outside the registry")


def test_every_tool_refuses_without_the_picked_book():
    # The picked Book is the Tool's precondition: no pick, no search —
    # and never the phase-1 whole-set default, which stays the ask
    # path's rule alone (dive_recall's).
    for name in dive.TOOL_REGISTRY:
        for missing in (None, []):
            try:
                dive.run_tool(name, "پرسش؟", missing)
            except dive.ToolError:
                continue
            raise AssertionError(f"{name} searched without a picked Book")


def test_a_tool_call_searches_exactly_the_picked_book():
    # The recall body carries the pick verbatim — narrowed, never
    # widened — with references on and the entry's search type.
    seen = []

    def recall(payload):
        seen.append(payload)
        return b"[]"

    original = dive.urlopen
    dive.urlopen = ResearchUpstream(recall_reply=recall)
    try:
        result = dive.run_tool("graph", "پرسش؟", PICK)
    finally:
        dive.urlopen = original
    assert len(seen) == 1
    body = seen[0]
    assert body["searchType"] == "GRAPH_COMPLETION"
    assert body["datasets"] == PICK
    assert body["query"] == "پرسش؟"
    assert body["includeReferences"] is True
    assert result == {
        "tool": "graph",
        "shape": "concepts",
        "passages": [],
        "concepts": [],
        "notes": [],
    }


# --- the result shapes -------------------------------------------------------


def test_the_recorded_graph_reply_parses_to_labels_never_passages():
    # The 2026-09-12 live reply, locked: node labels in rank order, no
    # verbatim passage — a graph completion's text never crosses into
    # the citable pool.
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert dive.parse_tool_concepts(fixture) == FIXTURE_LABELS
    result = call_tool("graph", "پرسش؟", PICK, fixture)
    assert result["shape"] == "concepts"
    assert result["concepts"] == FIXTURE_LABELS
    assert result["passages"] == []
    assert result["notes"] == []


def test_a_chunks_reply_parses_to_true_paged_passages():
    # A chunks reply's text IS the verbatim chunk: the reference is
    # built server-side — pages from the text layer's Page N: markers,
    # the Book identity from the item's dataset name, else the pick
    # when it names exactly one Book.
    text = "آغازِ متن.\n\nPage 31:\n ادامهٔ متن.\n\nPage 33:\n پایان."
    payload = [{"kind": "chunk", "text": text, "dataset_name": None}]
    result = call_tool("chunks", "پرسش؟", PICK, payload)
    assert result["shape"] == "passages"
    assert result["passages"] == [
        {"reference": f"document {PICK[0]} (pages 31-33)", "passage": text}
    ]


def test_an_unattributable_chunk_drops_rather_than_invent_a_book():
    # A multi-Book pick with an unnamed chunk cannot be attributed —
    # the chunk drops, never an invented Book. A named item wins over
    # the pick, and a single-Book pick stands in for the unnamed.
    payload = [
        {"text": "متن بدون نشانهٔ صفحه.", "dataset_name": None},
        {"text": "متن نام‌دار.", "dataset_name": "70143-336"},
    ]
    both = call_tool("chunks", "پرسش؟", ["tarhe-kolli", "70143-336"], payload)
    assert [item["reference"] for item in both["passages"]] == [
        "document 70143-336",
    ]
    single = call_tool("chunks", "پرسش؟", ["tarhe-kolli"], payload)
    assert [item["reference"] for item in single["passages"]] == [
        "document tarhe-kolli",
        "document 70143-336",
    ]


def test_a_summaries_reply_parses_to_notes():
    # Summaries are cognee's words, not the Book's: they land as notes,
    # never passages — no summary can enter the citable pool.
    payload = [
        {"text": "خلاصۀ نخست.", "score": 0.9},
        {"text": "خلاصۀ دوم."},
        {"kind": "chunk"},
    ]
    result = call_tool("summaries", "پرسش؟", PICK, payload)
    assert result["shape"] == "notes"
    assert result["notes"] == ["خلاصۀ نخست.", "خلاصۀ دوم."]
    assert result["passages"] == []


def test_a_failed_upstream_returns_an_empty_result():
    # The leash or the service failing is the searcher's old contract:
    # the Tool contributes nothing, the turn continues on its siblings.

    def dead(_):
        raise OSError("downstream dead")

    original = dive.urlopen
    dive.urlopen = ResearchUpstream(recall_reply=dead)
    try:
        result = dive.run_tool("graph", "پرسش؟", PICK)
    finally:
        dive.urlopen = original
    assert result == {
        "tool": "graph",
        "shape": "concepts",
        "passages": [],
        "concepts": [],
        "notes": [],
    }


# --- the bounded graph hop ---------------------------------------------------


def test_the_hop_seeds_citable_searches_with_the_graphs_labels():
    # ONE graph completion, then at most two hybrid searches seeded
    # `seed — label`: the passages the hop sources are Evidence-block
    # verbatims with pages — graph-steered, citable.
    bodies = []

    def recall(payload):
        bodies.append(payload)
        if payload["searchType"] == "GRAPH_COMPLETION":
            return json.dumps(graph_payload(), ensure_ascii=False).encode("utf-8")
        return json.dumps(fed_by_query(payload), ensure_ascii=False).encode("utf-8")

    original = dive.urlopen
    dive.urlopen = ResearchUpstream(recall_reply=recall)
    try:
        passages, labels = dive.graph_hop("پرسش بنیادین؟", PICK)
    finally:
        dive.urlopen = original
    assert [body["searchType"] for body in bodies] == [
        "GRAPH_COMPLETION",
        "HYBRID_COMPLETION",
        "HYBRID_COMPLETION",
    ]
    assert bodies[1]["query"] == f"پرسش بنیادین؟ — {HOP_LABEL}"
    assert labels == [HOP_LABEL, "دیگر"]
    assert passages and all(
        item["reference"].startswith("chunk ") for item in passages
    )


def test_the_hop_never_starts_on_an_empty_budget():
    # The hop is a bonus angle: an unaffordable budget leaves before it
    # starts, the reason recorded on the budget for the worker's honest
    # stop — never a crash, never a call.

    def recall(_):
        raise AssertionError("no call may run on an empty budget")

    budget = research.TurnBudget(call_cap=0)
    original = dive.urlopen
    dive.urlopen = ResearchUpstream(recall_reply=recall)
    try:
        passages, labels = dive.graph_hop("پرسش؟", PICK, budget=budget)
    finally:
        dive.urlopen = original
    assert (passages, labels) == ([], [])
    assert budget.reason == "call_cap"


# --- the turn seam: the gather end to end ------------------------------------


def test_a_gather_cites_graph_sourced_passages_end_to_end(tmp_path):
    # The acceptance lock: a gather whose ledger gains passages SOURCED
    # THROUGH the graph hop — the graph's labels steering citable
    # hybrid searches, the hop's entries carrying their via, the reply
    # telling the operator what the hop added.
    def recall(payload):
        if payload["searchType"] == "GRAPH_COMPLETION":
            return json.dumps(graph_payload(), ensure_ascii=False).encode("utf-8")
        return json.dumps(fed_by_query(payload), ensure_ascii=False).encode("utf-8")

    upstream = ResearchUpstream(
        composer_replies=[
            composer_reply(json.dumps(["زیرپرسش؟"])),
            composer_reply("این دورِ شواهد خوب پیش رفت."),
        ],
        recall_reply=recall,
    )
    session = make_session(tmp_path)
    session["state"]["datasets"] = list(PICK)
    turn = run_turn_sync(session, research.COMMAND_GATHER, upstream, tmp_path)
    assert turn.state == "done"
    state = session["state"]
    hop_entries = [item for item in state["evidence"] if item.get("via") == "graph"]
    assert hop_entries, "the hop's passages never reached the ledger"
    for entry in hop_entries:
        assert entry["found_for"] == "زیرپرسش؟"
    # The graph's label grew the map's concept row from the Book graph.
    assert HOP_LABEL in state["concepts"]
    # The reply names what the hop added; the hybrid count note stands.
    notes = [block["text"] for block in turn.result["reply"] if block["type"] == "note"]
    assert any("جست‌وجوی گراف" in text for text in notes)


def test_the_hops_passages_stay_true_paged_for_the_next_writer(tmp_path):
    # Citable means the whole chain: a hop-sourced entry's reference
    # feeds the guard's labels and the true-page resolver — the same
    # locator contract the hybrid pool has always carried.
    def recall(payload):
        if payload["searchType"] == "GRAPH_COMPLETION":
            return json.dumps(graph_payload(), ensure_ascii=False).encode("utf-8")
        return json.dumps(fed_by_query(payload), ensure_ascii=False).encode("utf-8")

    upstream = ResearchUpstream(
        composer_replies=[
            classify_reply("active_research", subquestions=["زیرپرسش؟"]),
            composer_reply("خوب پیش رفت."),
        ],
        recall_reply=recall,
    )
    session = make_session(tmp_path)
    session["state"]["datasets"] = list(PICK)
    turn = run_turn_sync(session, research.COMMAND_GATHER, upstream, tmp_path)
    assert turn.state == "done"
    hop_entries = [
        item for item in session["state"]["evidence"] if item.get("via") == "graph"
    ]
    assert hop_entries
    reference = hop_entries[0]["reference"]
    assert "document tarhe-kolli" in reference
    assert pages_label(reference, hop_entries[0]["passage"])
    assert book_label(reference) == "طرح کلی اندیشۀ اسلامی در قرآن"
