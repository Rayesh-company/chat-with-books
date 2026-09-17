"""The Host's contract locks (ticket T5, GitLab issue #6, the ADR-0012
Host row): a side answer is bounded cited reasoning with the evidence
ledger open — the gathered passages most relevant to the message lead
the pool, one fresh hybrid search follows, at most TWO graph hops chain
(a starving hop re-seeds from its own top label, never the message
again), every quote passes the verbatim guard, every citation stays
true-paged, and reasoning the pool cannot support renders as the
commentary it is — visibly distinct, never a claim. The whole chain
rides the turn's budget: an exhausted budget lands the honest stop,
never a runaway hop."""

from tests.conftest import REPO_ROOT

import json  # noqa: E402
import sys  # noqa: E402

sys.path.insert(0, str(REPO_ROOT))

from tests.upstream_fakes import (  # noqa: E402
    OTHER_SENTENCE,
    SENTENCE,
    composer_reply,
    make_session,
    run_turn_sync,
    ResearchUpstream,
)
from ui import research  # noqa: E402
from ui.guard import book_label, pages_label  # noqa: E402

PICK = ["tarhe-kolli"]

# One Farsi concept label, the shape the live graph nodes carry — the
# hop's follow-up query steers by it, and a starving hop chains from it.
HOP_LABEL = "مفهوم گرافی"

LEDGER_REF_A = "chunk 3 of document tarhe-kolli (pages 40-41)"
LEDGER_REF_B = "chunk 7 of document tarhe-kolli (pages 55-57)"


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


def recall_calls(upstream, search_type=None):
    """The recall requests the upstream saw, optionally narrowed to one
    search type — the hop-count lock reads this."""
    out = []
    for body in upstream.bodies:
        payload = json.loads(body)
        if "searchType" not in payload:
            continue
        if search_type is None or payload["searchType"] == search_type:
            out.append(payload)
    return out


def quoted_paragraph(text, quote, index):
    return {
        "type": "paragraph",
        "parts": [{"text": text}, {"quote": quote, "source": index}],
    }


def writer_blocks(*blocks):
    return composer_reply(
        json.dumps({"blocks": list(blocks)}, ensure_ascii=False)
    )


def references_of(turn):
    for block in turn.result["reply"]:
        if block["type"] == "references":
            return block["items"]
    return []


# --- the ledger is open to the side answer -----------------------------------


def test_a_side_answer_reads_the_ledger_first(tmp_path):
    # The fresh search starves and the graph is a dead end here — the
    # side answer still lands, built from the evidence the investigation
    # already gathered. The ledger feeds the pool; nothing writes it.
    def recall(_):
        return b"[]"

    upstream = ResearchUpstream(
        composer_replies=[
            composer_reply("not even consulted — the command resolves"),
            writer_blocks(
                {"type": "heading", "text": "بخش"},
                quoted_paragraph("نخست.", OTHER_SENTENCE, 0),
                quoted_paragraph("سپس.", SENTENCE, 1),
            ),
        ],
        recall_reply=recall,
    )
    session = make_session(tmp_path)
    session["state"]["datasets"] = list(PICK)
    session["state"]["evidence"] = [
        {
            "id": "e1",
            "reference": LEDGER_REF_A,
            "passage": SENTENCE,
            "found_for": "پرسش پژوهش؟",
        },
        {
            "id": "e2",
            "reference": LEDGER_REF_B,
            "passage": OTHER_SENTENCE,
            "found_for": "پرسش پژوهش؟",
        },
    ]
    message = f"{SENTENCE} {OTHER_SENTENCE}"
    turn = run_turn_sync(session, message, upstream, tmp_path)
    assert turn.state == "done"
    references = references_of(turn)
    # The reply's citations are exactly the ledger's own — the chat
    # stands on the investigation's memory, not a fresh search.
    assert set(references) == {LEDGER_REF_A, LEDGER_REF_B}
    for reference in references:
        assert pages_label(reference, SENTENCE)
        assert book_label(reference) == "طرح کلی اندیشۀ اسلامی در قرآن"
    # The Host reads the ledger, never writes it.
    assert len(session["state"]["evidence"]) == 2
    assert session["state"]["claims"] == []


# --- the hop rides the side answer -------------------------------------------


def test_a_side_answer_rides_a_graph_hop_true_paged(tmp_path):
    # The acceptance lock: the side answer combines the fresh pool with
    # at least one graph hop — the hop's label steering a citable hybrid
    # search, its passage quoted verbatim, its citation true-paged. One
    # hop feeds the pool and the chain ends there.
    def recall(payload):
        if payload["searchType"] == "GRAPH_COMPLETION":
            return json.dumps(graph_payload(), ensure_ascii=False).encode(
                "utf-8"
            )
        return json.dumps(fed_by_query(payload), ensure_ascii=False).encode(
            "utf-8"
        )

    upstream = ResearchUpstream(
        composer_replies=[
            composer_reply("ignored"),
            writer_blocks(
                quoted_paragraph("تازه.", f"{SENTENCE} — {SENTENCE}", 0),
                quoted_paragraph(
                    "از گراف.",
                    f"{SENTENCE} — {HOP_LABEL} — {SENTENCE}",
                    2,
                ),
            ),
        ],
        recall_reply=recall,
    )
    session = make_session(tmp_path)
    session["state"]["datasets"] = list(PICK)
    message = SENTENCE
    turn = run_turn_sync(session, message, upstream, tmp_path)
    assert turn.state == "done"
    # ONE graph completion, seeded by the message itself — the hop fed
    # the pool, so no second hop chained.
    graphs = recall_calls(upstream, "GRAPH_COMPLETION")
    assert [call["query"] for call in graphs] == [message]
    # The hop-sourced passage is quoted and true-paged: the guard kept
    # it verbatim against the pool, and its locator carries the Book
    # identity and the pages.
    paragraphs = [
        block for block in turn.result["reply"] if block["type"] == "paragraph"
    ]
    assert len(paragraphs) == 2
    hop_quote = f"{SENTENCE} — {HOP_LABEL} — {SENTENCE}"
    kept = [
        part
        for block in paragraphs
        for part in block["parts"]
        if part.get("quote") == hop_quote
    ]
    assert kept, "the hop's passage never reached the reply"
    hop_reference = "chunk 1 of document tarhe-kolli (pages 10-12)"
    assert hop_reference in references_of(turn)
    assert pages_label(hop_reference, hop_quote)
    assert book_label(hop_reference) == "طرح کلی اندیشۀ اسلامی در قرآن"
    assert len(session["state"]["evidence"]) == 0


def test_the_host_never_chains_a_third_hop(tmp_path):
    # The 2-hop limit's teeth: both hops starve (the graph keeps
    # yielding labels, the hybrid follow-ups keep coming back empty) and
    # the chain still stops at two — and the second hop's seed is the
    # first hop's own top label, the chain into the graph, never a
    # repeat of the message.
    def recall(payload):
        if payload["searchType"] == "GRAPH_COMPLETION":
            return json.dumps(graph_payload(), ensure_ascii=False).encode(
                "utf-8"
            )
        return b"[]"

    upstream = ResearchUpstream(
        composer_replies=[
            composer_reply("ignored"),
            writer_blocks(
                {"type": "heading", "text": "بخش"},
                quoted_paragraph("از دفتر.", SENTENCE, 0),
            ),
        ],
        recall_reply=recall,
    )
    session = make_session(tmp_path)
    session["state"]["datasets"] = list(PICK)
    session["state"]["evidence"] = [
        {
            "id": "e1",
            "reference": LEDGER_REF_A,
            "passage": SENTENCE,
            "found_for": "پرسش پژوهش؟",
        }
    ]
    turn = run_turn_sync(session, SENTENCE, upstream, tmp_path)
    assert turn.state == "done"
    graphs = recall_calls(upstream, "GRAPH_COMPLETION")
    assert len(graphs) == 2
    assert graphs[0]["query"] == SENTENCE
    assert graphs[1]["query"] == HOP_LABEL
    assert len(session["state"]["evidence"]) == 1


# --- commentary is visible, distinct, and never a claim -----------------------


def test_commentary_renders_distinct_and_never_a_claim(tmp_path):
    # A paragraph whose quotes all die under the guard keeps its own
    # text as a commentary block; an explicit commentary block passes as
    # text alone. Both render apart from the quoting paragraphs, carry
    # no quote, feed no reference, and record no claim.
    commentary_text = "نتیجه‌گیریِ خودم: پیوند این دو با هم است."
    explicit_text = "پیوند دوم، بیرون از متن کتاب."

    def recall(payload):
        if payload["searchType"] == "GRAPH_COMPLETION":
            return b"[]"
        return json.dumps(fed_by_query(payload), ensure_ascii=False).encode(
            "utf-8"
        )

    upstream = ResearchUpstream(
        composer_replies=[
            composer_reply("ignored"),
            writer_blocks(
                quoted_paragraph("الف.", f"{SENTENCE} — {SENTENCE}", 0),
                quoted_paragraph("ب.", f"{SENTENCE} — {OTHER_SENTENCE}", 1),
                {"type": "paragraph", "parts": [{"text": commentary_text}]},
                {"type": "commentary", "text": explicit_text},
            ),
        ],
        recall_reply=recall,
    )
    session = make_session(tmp_path)
    session["state"]["datasets"] = list(PICK)
    turn = run_turn_sync(session, SENTENCE, upstream, tmp_path)
    assert turn.state == "done"
    blocks = turn.result["reply"]
    kinds = [block["type"] for block in blocks]
    # The worker appends the server-built references after the blocks.
    assert kinds == [
        "paragraph",
        "paragraph",
        "commentary",
        "commentary",
        "references",
    ]
    assert blocks[2]["text"] == commentary_text
    assert blocks[3]["text"] == explicit_text
    for block in blocks[2:]:
        assert "parts" not in block and "quote" not in block
    # Commentary feeds no reference: the «منابع» list stays the quoted
    # passages' own.
    assert references_of(turn) == [
        "chunk 1 of document tarhe-kolli (pages 10-12)",
        "chunk 29 of document tarhe-kolli",
    ]
    assert session["state"]["claims"] == []


def test_the_host_vocabulary_is_pinned():
    # The vocabulary home names the two-hop bound and the commentary
    # term; the sheet carries the tag the commentary renders under.
    context = (REPO_ROOT / "CONTEXT.md").read_text(encoding="utf-8")
    assert "at most two hops" in context
    assert "«برداشت»" in context
    html = (REPO_ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    assert "برداشت" in html
    assert "research-commentary" in html


# --- the budget caps the chain ------------------------------------------------


def test_the_turn_budget_stops_the_hop_chain(tmp_path):
    # The per-turn budget's teeth on the Host path: the cap runs out
    # mid-chain, the second hop never starts, and the writer's refusal
    # lands the honest stop note — the transcript tells the truth, the
    # ledger stands untouched.
    def recall(payload):
        if payload["searchType"] == "GRAPH_COMPLETION":
            return json.dumps(graph_payload(), ensure_ascii=False).encode(
                "utf-8"
            )
        return json.dumps(fed_by_query(payload), ensure_ascii=False).encode(
            "utf-8"
        )

    upstream = ResearchUpstream(
        composer_replies=[
            composer_reply("ignored"),
        ],
        recall_reply=recall,
    )
    session = make_session(tmp_path)
    session["state"]["datasets"] = list(PICK)
    # classify 1 + fresh search 1 + the first hop's three calls = 5 —
    # the cap; the second hop's afford fails and the writer's pre-pay
    # raises with it.
    turn = run_turn_sync(session, SENTENCE, upstream, tmp_path, call_cap=5)
    assert turn.state == "done"
    assert len(recall_calls(upstream, "GRAPH_COMPLETION")) == 1
    reply = turn.result["reply"]
    assert len(reply) == 1
    assert reply[0]["type"] == "note"
    assert reply[0]["text"] == research.RESEARCH_BUDGET_STOP_DETAIL
    assert len(session["state"]["evidence"]) == 0
