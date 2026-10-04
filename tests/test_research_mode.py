"""The Research Mode contract locks (ADR-0008): the Wayfinder engine's
intent reader and its never-die parse guards, the research state's
versioned question and checkpoint rule (a proposal never applies
itself), the claim ledger's code-derived statuses, the dive kernel's
bounded fan-out (two rounds max, starved sub-questions returned as
gaps), the turn worker's guarded replies over scripted upstreams, and
the endpoints' gates and caps — one in-flight turn per phone, three
globally, cooperative abort by a new ask, sessions persisted in SQLite.

The payloads are pinned server-side — the browser names no Cognee
search type — and the orchestration follows the dive's pattern:
module-level functions over injectable urlopens, so the tests script
the upstreams."""

import json
import os
import threading

from tests.conftest import REPO_ROOT
from tests.helpers import (
    account_email_for_phone,
    get,
    post,
    stop_gate,
    wait_turn_done,
    with_gate,
)

from ui import dive, research, research_store, serve  # noqa: E402

PHONE = "09120000000"
OTHER_PHONE = "09120000077"
# The quota and the research store key by the ACCOUNT's email (T21);
# the phones are only the login handles the seeded Accounts are found by.
ACCOUNT = account_email_for_phone(PHONE)
OTHER_ACCOUNT = account_email_for_phone(OTHER_PHONE)

BS = "\b"
SENTENCE = "سخن در این است؛"
OTHER_SENTENCE = "این جمله از قطعهٔ دیگری است."
# The well-fed reply: two Evidence bullets per searcher — no sub-question
# starves, so a plain gather runs a single retrieval round.
FED_RECALL_TEXT = (
    "پاسخ.\n\nEvidence:\n"
    f"- chunk 1 of document tarhe-kolli (pages 10-12): \"{SENTENCE}\"\n"
    f"- chunk 29 of document tarhe-kolli: \"{OTHER_SENTENCE}\""
)


def cognee_payload(text):
    return json.dumps([{"text": text}]).encode("utf-8")


def fed_by_query(payload):
    """The well-fed recall, query-specific: each searcher returns its own
    two passages, so parallel searchers and successive turns never
    dedupe against each other."""
    query = payload["query"]
    return cognee_payload(
        "پاسخ.\n\nEvidence:\n"
        f"- chunk 1 of document tarhe-kolli (pages 10-12): \"{query} — {SENTENCE}\"\n"
        f"- chunk 29 of document tarhe-kolli: \"{query} — {OTHER_SENTENCE}\""
    )


def composer_reply(content, finish_reason=None):
    choice = {"message": {"content": content}}
    if finish_reason:
        choice["finish_reason"] = finish_reason
    return json.dumps({"choices": [choice]}).encode("utf-8")


def classify_reply(intent="casual_question", **fields):
    payload = {
        "intent": intent,
        "rq_proposal": "",
        "reason": "",
        "concepts": [],
        "subquestions": [],
        "scope_in": [],
        "scope_out": [],
    }
    payload.update(fields)
    return composer_reply(json.dumps(payload, ensure_ascii=False))


def guarded_blocks(sources, with_heading=True):
    """A writer reply's body: one optional heading plus one good quoting
    paragraph per source — the shape the verbatim guard keeps. Each
    paragraph's own text differs, so each records as its own claim."""
    blocks = [{"type": "heading", "text": "بخش نخست"}] if with_heading else []
    for index, source in enumerate(sources):
        blocks.append(
            {
                "type": "paragraph",
                "parts": [
                    {"text": f"ادعای {index + 1}: "},
                    {"quote": source["passage"], "source": index},
                ],
            }
        )
    return {"blocks": blocks}


class ResearchUpstream:
    """Stands in for the composer endpoint and the second Cognee service,
    told apart by URL. Thread-safe — the gather's searchers call it in
    parallel — with a lock around the call log and the reply queues."""

    def __init__(self, composer_replies=(), recall_reply=None, gate=None):
        self.lock = threading.Lock()
        self.calls = []
        self.bodies = []
        self.gate = gate
        self.composer_replies = [
            reply.encode("utf-8") if isinstance(reply, str) else reply
            for reply in composer_replies
        ]
        self.recall_reply = recall_reply or (
            lambda payload: cognee_payload(FED_RECALL_TEXT)
        )

    def __call__(self, request, timeout=None):
        url = request.full_url
        body = request.data.decode("utf-8") if request.data else ""
        if "chat/completions" in url:
            with self.lock:
                self.calls.append(url)
                self.bodies.append(body)
                reply = self.composer_replies.pop(0)
            if self.gate is not None:
                # The scripted composer call is in flight — logged,
                # unanswered — until the test releases the gate.
                self.gate.wait(timeout=30)
        else:
            reply = self.recall_reply(json.loads(body))
            with self.lock:
                self.calls.append(url)
                self.bodies.append(body)
        if isinstance(reply, Exception):
            raise reply

        class Response:
            status = 200
            headers = {"Content-Type": "application/json"}

            def __init__(self, body):
                self._body = body

            def read(self):
                return self._body

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return Response(reply)


import sys  # noqa: E402

sys.path.insert(0, str(REPO_ROOT))


def with_research_upstream(upstream, tmp_path):
    """Patch the engine's and the kernel's urlopens plus the store's DB
    for one direct call; restores after — the two seams a scripted turn
    rides (the composer endpoint through the engine, the searchers
    through the kernel)."""

    def run(fn):
        research_store.RESEARCH_DB = tmp_path / "research.sqlite3"
        originals = [
            (module, module.urlopen)
            for module in (research, dive)
        ]
        for module, _ in originals:
            module.urlopen = upstream
        os.environ["LLM_API_KEY"] = "test-key"
        try:
            return fn()
        finally:
            for module, original in originals:
                module.urlopen = original
            del os.environ["LLM_API_KEY"]

    return run


def run_turn_sync(session, message, upstream, tmp_path):
    """Run one turn's worker synchronously over a registry turn — the
    orchestration seam without the HTTP layer or the thread."""
    turn = research.ResearchTurn(session["account"], session["id"], message)
    research.RESEARCH_REGISTRY[turn.id] = turn
    with_research_upstream(upstream, tmp_path)(
        lambda: research.run_research_turn(turn, session)
    )
    return turn


def make_session(tmp_path, state=None, sources=None):
    """One session in the test's own store DB — never the module's real
    default file."""
    research_store.RESEARCH_DB = tmp_path / "research.sqlite3"
    session, error = research.ensure_session(
        PHONE, None, "پیام آغازین", "پرسش پژوهش؟", sources or []
    )
    assert session is not None, error
    if state:
        session["state"] = state
    return session


# --- the pinned constants -------------------------------------------------


def test_serve_pins_the_research_constants_in_source():
    research_text = (REPO_ROOT / "ui" / "research.py").read_text(encoding="utf-8")
    dive_text = (REPO_ROOT / "ui" / "dive.py").read_text(encoding="utf-8")
    assert 'RESEARCH_MODEL = "glm-5.3-flash"' in research_text
    assert "RESEARCH_MAX_CONCURRENT = 3" in research_text
    assert "RESEARCH_SESSION_TURN_CAP = 40" in research_text
    assert "RESEARCH_EVIDENCE_FLOOR = 6" in research_text
    assert "DIVE_MAX_SUB_QUESTIONS = 6" in dive_text
    assert "DIVE_STARVED_PASSAGES = 2" in dive_text
    assert "DIVE_MAX_RETRIEVAL_ROUNDS = 2" in dive_text
    assert 'DIVE_SEARCH_TIMEOUT = 600' in dive_text
    # The fixed chip commands map deterministically — the sheet's chips
    # are exactly these texts.
    assert len(research.COMMAND_MOVES) == 4
    assert research.COMMAND_MOVES[research.COMMAND_GATHER] == "gather"
    assert research.COMMAND_MOVES[research.COMMAND_SYNTHESIZE] == "synthesize"
    assert research.COMMAND_MOVES[research.COMMAND_BRIEF] == "brief"
    assert research.COMMAND_MOVES[research.COMMAND_AUDIT] == "audit"


# --- the intent reader ------------------------------------------------------


def test_classify_prompt_names_the_intents_and_the_fixed_commands():
    state = research.new_research_state("پرسش پژوهش؟")
    prompt = research.build_classify_prompt("پیام", state, "کاربر: پیام")
    for intent in research.RESEARCH_INTENTS:
        assert intent in prompt
    assert research.COMMAND_GATHER in prompt
    assert research.COMMAND_BRIEF in prompt
    assert research.COMMAND_AUDIT in prompt
    # The state rides in as the compact JSON projection, with the
    # current research question.
    assert "پرسش پژوهش؟" in prompt
    assert "Reply with ONLY a JSON object" in prompt


def test_parse_classify_reply_degrades_to_conversational():
    # A malformed reply can never push a casual chat into research
    # machinery — the never-die shape of the dive's sub-question guard.
    for content in ("", "not json", '["a list"]', '{"intent": 5}'):
        parsed = research.parse_classify_reply(content)
        assert parsed["intent"] == "casual_question"
    parsed = research.parse_classify_reply('{"intent": "time_travel"}')
    assert parsed["intent"] == "casual_question"


def test_parse_classify_reply_caps_the_proposed_lists():
    parsed = research.parse_classify_reply(
        json.dumps(
            {
                "intent": "active_research",
                "concepts": [f"مفهوم {i}" for i in range(9)],
                "subquestions": [f"زیرپرسش {i}" for i in range(9)],
                "scope_in": ["الف"] * 6,
                "scope_out": [5, "ب"],
                "rq_proposal": None,
            },
            ensure_ascii=False,
        )
    )
    assert parsed["intent"] == "active_research"
    assert len(parsed["concepts"]) == research.DIVE_MAX_SUB_QUESTIONS
    assert len(parsed["subquestions"]) == research.DIVE_MAX_SUB_QUESTIONS
    assert len(parsed["scope_in"]) == 4
    assert parsed["scope_out"] == ["ب"]
    assert parsed["rq_proposal"] == ""


def test_subquestions_from_reply_falls_back_to_the_question():
    assert research.subquestions_from_reply("غیرمنتظره", "پرسش؟") == ["پرسش؟"]
    assert research.subquestions_from_reply(
        json.dumps(["یک؟", "دو؟", ""], ensure_ascii=False), "پرسش؟"
    ) == ["یک؟", "دو؟"]


# --- the research state -----------------------------------------------------


def test_new_state_seeds_the_question_as_version_one():
    state = research.new_research_state("پرسش آغازین؟")
    assert state["research_question"]["current"] == "پرسش آغازین؟"
    assert state["research_question"]["versions"] == [
        {"text": "پرسش آغازین؟", "reason": "پرسش آغازین", "turn": 0}
    ]


def test_seed_evidence_dedupes_on_the_normalized_letter_stream():
    state = research.new_research_state("پرسش؟")
    sources = [
        {"reference": "chunk 1 of document tarhe-kolli", "passage": SENTENCE},
        # Same letters, different noise — one evidence entry, not two.
        {"reference": "chunk 2 of document tarhe-kolli", "passage": f"سـخن{BS}در{BS}این{BS}اسـت؛"},
        {"reference": "chunk 3 of document tarhe-kolli", "passage": ""},
    ]
    assert research.seed_evidence(state, sources, "پرسش؟") == 1
    assert len(state["evidence"]) == 1


def test_next_best_move_orders_the_wayfinder():
    state = research.new_research_state("پرسش؟")
    assert research.next_best_move(state) == "gather"
    state["evidence"] = [{"id": "e1", "reference": "r", "passage": SENTENCE}]
    # Below the evidence floor (or with pending sub-questions): gather.
    assert research.next_best_move(state) == "gather"
    state["evidence"] = [
        {"id": f"e{i}", "reference": "r", "passage": f"{SENTENCE}{i}"}
        for i in range(research.RESEARCH_EVIDENCE_FLOOR)
    ]
    assert research.next_best_move(state) == "synthesize"
    state["claims"] = [{"id": "c1", "text": "ادعا", "status": "direct_support"}]
    assert research.next_best_move(state) == "brief"
    # A pending decision blocks everything else — the checkpoint rule.
    state["pending_proposals"] = [{"id": "p1", "kind": "research_question"}]
    assert research.next_best_move(state) == "checkpoint"


def test_suggestions_lead_with_proposal_chips_then_the_moves():
    state = research.new_research_state("پرسش؟")
    state["pending_proposals"] = [
        {"id": "p1", "kind": "research_question", "text": "پرسش دقیق‌تر؟"}
    ]
    chips = research.research_suggestions(state)
    # The checkpoint's accept and reject chips — SHORT labels, the
    # proposal text riding in the note above, never inside the chip —
    # LEAD the set; the journey's own moves follow them (T7 #8: the
    # work chips are never hidden while a decision waits).
    assert [chip["kind"] for chip in chips] == ["proposal", "proposal", "move"]
    assert chips[0]["accept"] is True and chips[1]["accept"] is False
    assert chips[0]["label"] == "می‌پذیرم"
    assert chips[0]["text"] == "پرسش دقیق‌تر؟"
    del state["pending_proposals"]
    state["stage"] = "investigating"
    state["evidence"] = [
        {"id": f"e{i}", "reference": "r", "passage": f"{SENTENCE}{i}"}
        for i in range(research.RESEARCH_EVIDENCE_FLOOR)
    ]
    state["claims"] = [{"id": "c1", "text": "ادعا", "status": "direct_support"}]
    chips = research.research_suggestions(state)
    # The plan-request chip (decision 03, the research-mode v2 map):
    # while no plan is accepted and none waits, the chip-only user's
    # exit from the plan gate rides beside the work moves.
    assert {chip["id"] for chip in chips} == {"brief", "audit", "plan"}
    # An accepted plan retires it.
    state["brief_plan"] = {
        "current": {"sections": [{"title": "بخش", "key": "k1", "status": "accepted"}]},
        "versions": [],
    }
    chips = research.research_suggestions(state)
    assert "plan" not in {chip["id"] for chip in chips}


def test_classify_updates_park_consequential_changes_as_proposals():
    # The checkpoint rule (Wayfinder §17): a research question or scope
    # change NEVER applies itself — it lands as a pending proposal the
    # user must approve; working concepts and sub-questions fold in
    # directly. The chart-mode gate (ADR-0011): only an EXPLORATION
    # turn may park at all.
    state = research.new_research_state("پرسش آغازین؟")
    research._apply_classify_updates(
        state,
        {
            "intent": "research_exploration",
            "concepts": ["مفهوم"],
            "subquestions": ["زیرپرسش؟"],
            "rq_proposal": "پرسش دقیق‌تر؟",
            "reason": "چون",
            "scope_in": ["فصل ۴"],
            "scope_out": [],
        },
    )
    assert state["concepts"] == ["مفهوم"]
    # Sub-questions land as NAMED open questions — id, name, text,
    # status — the journey's refer-by-name units.
    assert state["subquestions"] == [
        {"id": "q1", "name": "زیرپرسش؟", "text": "زیرپرسش؟", "status": "pending"}
    ]
    assert state["research_question"]["current"] == "پرسش آغازین؟"
    assert [p["kind"] for p in state["pending_proposals"]] == [
        "research_question",
        "scope",
    ]
    # A restatement of the current question is not a proposal.
    state = research.new_research_state("پرسش آغازین؟")
    research._apply_classify_updates(
        state, {"intent": "research_exploration", "rq_proposal": f"پرسش{BS}آغازین؟", "reason": "", "concepts": [], "subquestions": [], "scope_in": [], "scope_out": []}
    )
    assert state["pending_proposals"] == []


def test_record_claims_derives_status_from_the_evidence_count():
    state = research.new_research_state("پرسش؟")
    state["evidence"] = [
        {"id": "e1", "reference": "r1", "passage": SENTENCE},
        {"id": "e2", "reference": "r2", "passage": OTHER_SENTENCE},
    ]
    blocks = [
        {"type": "heading", "text": "h"},
        {
            "type": "paragraph",
            "parts": [
                {"text": "ادعای نخست: "},
                {"quote": SENTENCE, "source": 0},
            ],
        },
        {
            "type": "paragraph",
            "parts": [
                {"text": "ادعای دوم: "},
                {"quote": SENTENCE, "source": 0},
                {"quote": OTHER_SENTENCE, "source": 1},
            ],
        },
    ]
    assert research.record_claims(state, blocks, ["e1", "e2"]) == 2
    assert state["claims"][0]["status"] == "direct_support"
    assert state["claims"][0]["evidence_ids"] == ["e1"]
    assert state["claims"][1]["status"] == "supported_synthesis"
    # The same claim text never records twice.
    assert research.record_claims(state, blocks, ["e1", "e2"]) == 0


# --- the kernel's bounded fan-out ------------------------------------------


def test_dive_retrieve_returns_fed_after_one_round():
    upstream = ResearchUpstream(recall_reply=fed_by_query)
    original = dive.urlopen
    dive.urlopen = upstream
    try:
        sources, rounds, starved = dive.dive_retrieve(["زیرپرسش یک؟", "زیرپرسش دو؟"])
    finally:
        dive.urlopen = original
    assert rounds == 1
    assert starved == []
    assert len(sources) == 4


def test_an_always_starved_gather_stops_at_exactly_two_rounds():
    # The topology's round lock: an upstream that starves every
    # sub-question gets exactly two rounds, never a third — and the
    # still-starved sub-questions come back as the gap entries.
    calls = []

    def starving(payload):
        calls.append(payload["query"])
        return cognee_payload("پاسخ.\n\nEvidence:\n- nothing: \"یکی\"")

    upstream = ResearchUpstream(recall_reply=starving)
    original = dive.urlopen
    dive.urlopen = upstream
    try:
        sources, rounds, starved = dive.dive_retrieve(["زیرپرسش؟"])
    finally:
        dive.urlopen = original
    assert rounds == 2
    assert starved == ["زیرپرسش؟"]
    assert len(calls) == 2


def test_dive_retrieve_checks_cancel_between_rounds():
    cancel = threading.Event()
    cancel.set()
    sources, rounds, starved = dive.dive_retrieve(["زیرپرسش؟"], cancel=cancel)
    assert sources == []
    assert starved == ["زیرپرسش؟"]


# --- the store --------------------------------------------------------------


def test_the_store_round_trips_a_session_and_its_transcript(tmp_path):
    research_store.RESEARCH_DB = tmp_path / "research.sqlite3"
    session, _ = research.ensure_session(
        ACCOUNT, None, "پیام", "پرسش؟", []
    )
    # The message is not persisted by ensure_session (T11): preparing a
    # session admits nothing — the accepted turn appends the message to
    # the transcript, the same append any admission rides.
    research_store.append_message(session["id"], "user", "پیام")
    state = session["state"]
    state["evidence"] = [
        {"id": "e1", "reference": "r", "passage": SENTENCE, "found_for": "پرسش؟"}
    ]
    research_store.save_session(session["id"], state)
    research_store.append_message(session["id"], "assistant", [{"type": "note", "text": "پاسخ"}])
    loaded = research_store.load_session(session["id"])
    assert loaded["account"] == ACCOUNT
    assert loaded["state"]["evidence"][0]["passage"] == SENTENCE
    assert loaded["messages"] == [
        {"role": "user", "payload": "پیام"},
        {"role": "assistant", "payload": [{"type": "note", "text": "پاسخ"}]},
    ]
    # A save for an unknown session reports not-found, never inserts.
    assert research_store.save_session("no-such-session", state) == ""


# --- the worker over scripted upstreams --------------------------------------


def test_a_conversational_turn_answers_through_one_guarded_writer(tmp_path):
    # The conversation layer: a casual message gets a normal answer —
    # one searcher over the message, one guarded writer pass over its
    # pool, references built from exactly that pool.
    pool = [
        {"reference": "chunk 1 of document tarhe-kolli", "passage": SENTENCE},
        {"reference": "chunk 2 of document tarhe-kolli", "passage": OTHER_SENTENCE},
    ]
    upstream = ResearchUpstream(
        composer_replies=[
            classify_reply("casual_question"),
            composer_reply(json.dumps(guarded_blocks(pool), ensure_ascii=False)),
        ]
    )
    session = make_session(tmp_path)
    turn = run_turn_sync(session, "این مفهوم را توضیح بده", upstream, tmp_path)
    assert turn.state == "done"
    blocks = turn.result["reply"]
    assert [b["type"] for b in blocks] == ["heading", "paragraph", "paragraph", "references"]
    # The references cite exactly the turn's searcher pool (the FED
    # fixture), in first-use order.
    assert blocks[-1]["items"] == [
        "chunk 1 of document tarhe-kolli (pages 10-12)",
        "chunk 29 of document tarhe-kolli",
    ]
    # A conversational message never touches the research state's
    # question or evidence — the conversation layer stays free.
    assert session["state"]["research_question"]["current"] == "پرسش پژوهش؟"
    assert session["state"]["evidence"] == []


def test_a_conversational_turn_without_a_pool_lands_the_honest_note(tmp_path):
    upstream = ResearchUpstream(
        composer_replies=[classify_reply("casual_question")],
        recall_reply=lambda payload: b"[]",
    )
    session = make_session(tmp_path)
    turn = run_turn_sync(session, "سؤال بی‌ربط", upstream, tmp_path)
    assert turn.state == "done"
    assert turn.result["reply"] == [
        {"type": "note", "text": research.RESEARCH_NO_EVIDENCE_DETAIL}
    ]


def test_a_conversational_turn_the_guard_keeps_nothing_of_lands_the_note(tmp_path):
    # A writer that only paraphrases (the guard legitimately keeps
    # nothing, even after the one retry) never lands unguarded text.
    pool = [{"reference": "chunk 1 of document tarhe-kolli", "passage": SENTENCE}]
    paraphrase = json.dumps(
        {"blocks": [{"type": "paragraph", "parts": [{"text": "بازگوییِ وارونه"}]}]},
        ensure_ascii=False,
    )
    upstream = ResearchUpstream(
        composer_replies=[
            classify_reply("casual_question"),
            composer_reply(paraphrase),
            composer_reply(paraphrase),
        ]
    )
    session = make_session(tmp_path)
    turn = run_turn_sync(session, "توضیح بده", upstream, tmp_path)
    assert turn.state == "done"
    assert turn.result["reply"] == [
        {"type": "note", "text": research.RESEARCH_NO_EVIDENCE_DETAIL}
    ]


def test_a_gather_turn_merges_evidence_and_reports_counts(tmp_path):
    # The research layer's gather: classify proposes the sub-questions,
    # the fan-out pools them into the ledger, and the reply is
    # server-composed notes — no model prose to guard at all.
    upstream = ResearchUpstream(
        composer_replies=[
            composer_reply(json.dumps(["زیرپرسش یک؟", "زیرپرسش دو؟"])),
        ],
        recall_reply=fed_by_query,
    )
    session = make_session(tmp_path)
    turn = run_turn_sync(session, research.COMMAND_GATHER, upstream, tmp_path)
    assert turn.state == "done"
    assert len(session["state"]["evidence"]) == 4
    assert session["state"]["phase"] == "investigating"
    notes = [b for b in turn.result["reply"] if b["type"] == "note"]
    assert any("۴ نقل‌قول تازه" in b["text"] for b in notes)
    # The suggestions offer the next move (below the floor: more
    # evidence; the sub-questions are searched, but 4 < 6).
    moves = [s for s in turn.result["suggestions"] if s["kind"] == "move"]
    assert moves and moves[0]["id"] == "gather"


def test_a_starved_gather_records_honest_gaps(tmp_path):
    # A sub-question the Books cannot feed becomes a gap entry —
    # «cannot be established from these Books» — never a filled hole.
    def starving(payload):
        return cognee_payload("پاسخی بدون بلوک Evidence.")

    upstream = ResearchUpstream(
        composer_replies=[
            classify_reply("active_research", subquestions=["زیرپرسش؟"])
        ],
        recall_reply=starving,
    )
    session = make_session(tmp_path)
    turn = run_turn_sync(session, research.COMMAND_GATHER, upstream, tmp_path)
    assert turn.state == "done"
    assert len(session["state"]["gaps"]) == 1
    assert "نمی‌توان" in session["state"]["gaps"][0]["text"]
    notes = [b["text"] for b in turn.result["reply"] if b["type"] == "note"]
    # The starved gather names its cause (T6): the diagnosis note
    # replaces the old bare no-evidence line.
    assert any("تشخیص:" in text for text in notes)
    assert turn.result["research_state"]["gaps"]


def test_a_synthesis_turn_records_claims_and_cites_the_ledger(tmp_path):
    state = research.new_research_state("پرسش پژوهش؟")
    research.seed_evidence(
        state,
        [
            {"reference": "chunk 1 of document tarhe-kolli", "passage": SENTENCE},
            {"reference": "chunk 2 of document tarhe-kolli", "passage": OTHER_SENTENCE},
        ],
        "پرسش پژوهش؟",
    )
    pool = [
        {"reference": "chunk 1 of document tarhe-kolli", "passage": SENTENCE},
        {"reference": "chunk 2 of document tarhe-kolli", "passage": OTHER_SENTENCE},
    ]
    upstream = ResearchUpstream(
        composer_replies=[
            classify_reply("active_research"),
            composer_reply(json.dumps(guarded_blocks(pool), ensure_ascii=False)),
        ]
    )
    session = make_session(tmp_path, state=state)
    turn = run_turn_sync(session, research.COMMAND_SYNTHESIZE, upstream, tmp_path)
    assert turn.state == "done"
    assert len(session["state"]["claims"]) == 2
    assert blocks_of(turn)["paragraph"] == 2
    # The references are the ledger's real passages, in first-use order.
    assert blocks_of(turn)["references"] == [
        "chunk 1 of document tarhe-kolli",
        "chunk 2 of document tarhe-kolli",
    ]


def blocks_of(turn):
    kinds = {}
    for block in turn.result["reply"]:
        if block["type"] == "references":
            kinds["references"] = block["items"]
        else:
            kinds[block["type"]] = kinds.get(block["type"], 0) + 1
    return kinds


def test_a_brief_turn_refuses_without_claims_and_writes_from_state(tmp_path):
    state = research.new_research_state("پرسش پژوهش؟")
    research.seed_evidence(
        state,
        [
            {"reference": "chunk 1 of document tarhe-kolli", "passage": SENTENCE},
        ],
        "پرسش پژوهش؟",
    )
    upstream = ResearchUpstream(composer_replies=[classify_reply("drafting")])
    session = make_session(tmp_path, state=state)
    turn = run_turn_sync(session, research.COMMAND_BRIEF, upstream, tmp_path)
    assert turn.state == "done"
    assert any(
        b["type"] == "note" and "ادعای مستندی" in b["text"]
        for b in turn.result["reply"]
    )

    # With claims recorded and the section plan accepted, the Brief
    # writes section by section FROM the state — each section's prompt
    # carries its contract (the title, the claims by their ledger
    # text), never the chat transcript.
    state["claims"] = [
        {"id": "c1", "text": "ادعا", "status": "direct_support", "evidence_ids": ["e1"]}
    ]
    sections = [{"title": "بخش یکم", "question": "", "claims": ["c1"]}]
    state["brief_plan"] = {
        "current": {"sections": sections},
        "versions": [{"sections": sections, "turn": 1}],
    }
    state["section_contracts"] = research._section_contracts_from_plan(
        state, sections
    )
    pool = [{"reference": "chunk 1 of document tarhe-kolli", "passage": SENTENCE}]
    upstream = ResearchUpstream(
        composer_replies=[
            classify_reply("drafting"),
            composer_reply(json.dumps(guarded_blocks(pool), ensure_ascii=False)),
            composer_reply("روایت کوتاه."),
            # The Closing review's judgment (T9): the finished Brief is
            # reviewed in the same turn.
            composer_reply(
                json.dumps(
                    {"verdict": "delivers", "reason": "سند می‌رساند."},
                    ensure_ascii=False,
                )
            ),
        ]
    )
    session = make_session(tmp_path, state=state)
    turn = run_turn_sync(session, research.COMMAND_BRIEF, upstream, tmp_path)
    assert turn.state == "done"
    composer_bodies = [
        json.loads(body)
        for url, body in zip(upstream.calls, upstream.bodies)
        if "chat/completions" in url
    ]
    writer_prompt = composer_bodies[1]["messages"][0]["content"]
    assert "ادعا" in writer_prompt
    assert "بخش یکم" in writer_prompt
    assert state["phase"] == "drafting"


def test_an_audit_turn_is_pure_code(tmp_path):
    state = research.new_research_state("پرسش پژوهش؟")
    state["claims"] = [
        {"id": "c1", "text": "ادعای نخست", "status": "direct_support"}
    ]
    state["gaps"] = [{"id": "g1", "text": "شکاف", "subquestion": "زیرپرسش", "turn": 1}]
    upstream = ResearchUpstream(composer_replies=[])
    session = make_session(tmp_path, state=state)
    turn = run_turn_sync(session, research.COMMAND_AUDIT, upstream, tmp_path)
    assert turn.state == "done"
    texts = [b.get("text", "") for b in turn.result["reply"]]
    assert any("[پشتوانهٔ مستقیم] ادعای نخست" in text for text in texts)
    # The audit itself is pure code — and W4 (stage C) took the chip's
    # classify call with it: zero composer calls, zero searchers.
    assert upstream.calls == []


def test_an_rq_proposal_turn_is_a_checkpoint_not_a_change(tmp_path):
    upstream = ResearchUpstream(
        composer_replies=[
            classify_reply(
                "research_exploration",
                rq_proposal="پرسش دقیق‌تر؟",
                reason="چون دامنه روشن شود",
            )
        ]
    )
    session = make_session(tmp_path)
    turn = run_turn_sync(session, "پرسش را دقیق‌تر کنیم", upstream, tmp_path)
    assert turn.state == "done"
    # The current question did NOT change; the proposal waits.
    assert session["state"]["research_question"]["current"] == "پرسش پژوهش؟"
    assert session["state"]["pending_proposals"][0]["text"] == "پرسش دقیق‌تر؟"
    chips = turn.result["suggestions"]
    # The decision pair leads; the moves are no longer hidden behind a
    # waiting proposal (T7 #8).
    assert [chip["kind"] for chip in chips] == ["proposal", "proposal", "move"]


def test_decide_proposal_versions_the_question(tmp_path):
    research_store.RESEARCH_DB = tmp_path / "research.sqlite3"
    session = make_session(tmp_path)
    state = session["state"]
    research._apply_classify_updates(
        state,
        {
            "intent": "research_exploration",
            "rq_proposal": "پرسش دقیق‌تر؟",
            "reason": "چون",
            "concepts": [],
            "subquestions": [],
            "scope_in": [],
            "scope_out": [],
        },
    )
    research_store.save_session(session["id"], state)
    proposal_id = state["pending_proposals"][0]["id"]
    result, error = research.decide_proposal(
        PHONE, session["id"], proposal_id, True
    )
    assert error is None
    # The new current question, v1 intact underneath — versions are
    # append-only provenance.
    loaded = research_store.load_session(session["id"])
    question = loaded["state"]["research_question"]
    assert question["current"] == "پرسش دقیق‌تر؟"
    assert [v["text"] for v in question["versions"]] == [
        "پرسش پژوهش؟",
        "پرسش دقیق‌تر؟",
    ]
    assert result["research_state"]["rq_versions"] == 2
    # A foreign phone learns nothing; an unknown proposal 404s.
    result, error = research.decide_proposal(
        OTHER_PHONE, session["id"], proposal_id, True
    )
    assert result is None and error[0] == 404
    result, error = research.decide_proposal(PHONE, session["id"], "p9", False)
    assert result is None and error[0] == 404


def test_decide_proposal_reject_leaves_the_question_alone(tmp_path):
    research_store.RESEARCH_DB = tmp_path / "research.sqlite3"
    session = make_session(tmp_path)
    state = session["state"]
    research._apply_classify_updates(
        state,
        {
            "intent": "research_exploration",
            "rq_proposal": "پرسش دقیق‌تر؟",
            "reason": "",
            "concepts": [],
            "subquestions": [],
            "scope_in": [],
            "scope_out": [],
        },
    )
    research_store.save_session(session["id"], state)
    proposal_id = state["pending_proposals"][0]["id"]
    result, error = research.decide_proposal(
        PHONE, session["id"], proposal_id, False
    )
    assert error is None
    loaded = research_store.load_session(session["id"])
    assert loaded["state"]["research_question"]["current"] == "پرسش پژوهش؟"
    assert len(loaded["state"]["research_question"]["versions"]) == 1
    assert loaded["state"]["pending_proposals"] == []


def test_a_long_session_suggests_the_brief(tmp_path):
    state = research.new_research_state("پرسش پژوهش؟")
    state["turns"] = research.RESEARCH_SESSION_TURN_CAP - 1
    state["evidence"] = [
        {"id": "e1", "reference": "r", "passage": SENTENCE},
    ]
    upstream = ResearchUpstream(
        composer_replies=[classify_reply("casual_question")],
        recall_reply=lambda payload: b"[]",
    )
    session = make_session(tmp_path, state=state)
    turn = run_turn_sync(session, "سؤال", upstream, tmp_path)
    assert turn.state == "done"
    assert any(
        b.get("text") == research.RESEARCH_SESSION_CAP_DETAIL
        for b in turn.result["reply"]
    )


# --- the journey layer (ADR-0009) --------------------------------------------


def grilling_reply(question="از این پژوهش چه می‌خواهید؟", options=("گزینهٔ الف", "گزینهٔ ب")):
    return composer_reply(
        json.dumps({"question": question, "options": list(options)}, ensure_ascii=False)
    )


def test_new_state_carries_the_journey_layer():
    state = research.new_research_state("پرسش؟")
    assert state["stage"] == "orientation"
    assert state["map"]["destination"] == ""
    assert state["map"]["fog"] == []
    assert state["map"]["landscape_done"] is False
    assert state["grilling"] == {
        "asked_in_stage": 0,
        "current_question": "",
        "options": [],
    }


def test_ensure_state_shape_upgrades_a_v01_session():
    # A session persisted before the journey layer: the old phase word
    # derives the stage, the unnamed sub-questions gain their names,
    # the map and grilling defaults fill in — a pure upgrade.
    state = {
        "research_question": {"current": "پرسش؟", "versions": []},
        "scope": {"in": [], "out": [], "open_decisions": []},
        "subquestions": [{"text": "زیرپرسش کهن؟", "status": "searched"}],
        "concepts": [],
        "evidence": [],
        "claims": [],
        "gaps": [],
        "decisions": [],
        "phase": "investigating",
        "pending_proposals": [],
        "turns": 9,
        "closed": False,
    }
    research.ensure_state_shape(state)
    assert state["stage"] == "investigating"
    assert state["map"]["destination"] == ""
    assert state["grilling"]["asked_in_stage"] == 0
    assert state["subquestions"][0]["name"] == "زیرپرسش کهن؟"
    assert state["subquestions"][0]["id"]


def test_resolve_command_maps_the_deterministic_family():
    # The fixed four, the gather-all, the skip, the guide — and every
    # targeted «شواهدِ «نام» را پیدا کن» chip resolves without the
    # classifier.
    assert research.resolve_command(research.COMMAND_GATHER) == ("gather", None)
    assert research.resolve_command(research.COMMAND_SYNTHESIZE) == ("synthesize", None)
    assert research.resolve_command(research.COMMAND_BRIEF) == ("brief", None)
    assert research.resolve_command(research.COMMAND_AUDIT) == ("audit", None)
    assert research.resolve_command(research.COMMAND_GATHER_ALL) == ("gather", None)
    assert research.resolve_command(research.GRILLING_SKIP) == ("skip", None)
    assert research.resolve_command(research.COMMAND_GUIDE) == ("guide", None)
    assert research.resolve_command("شواهدِ «شهود و ساحت» را پیدا کن") == (
        "gather",
        "شهود و ساحت",
    )
    assert research.resolve_command("پیام آزاد کاربر") is None


def test_the_orientation_offers_the_guide_chip_after_a_detour():
    # A conversational detour (classified casual, answered normally)
    # leaves the destination unnamed — the suggestions never strand the
    # user: one tap asks the guided question again.
    state = research.new_research_state("پرسش؟")
    chips = research.research_suggestions(state)
    assert chips == [{"kind": "move", "id": "guide", "text": research.COMMAND_GUIDE}]


def test_the_guide_command_asks_the_guided_question(tmp_path):
    upstream = ResearchUpstream(
        composer_replies=[
            grilling_reply("از این پژوهش چه می‌خواهید؟", ("مقایسه",)),
        ]
    )
    session = make_session(tmp_path)
    turn = run_turn_sync(session, research.COMMAND_GUIDE, upstream, tmp_path)
    assert turn.state == "done"
    # The forced guide outranks the conversational classification: the
    # reply IS the guided question, with its option chips.
    assert turn.result["reply"] == [
        {"type": "question", "text": "از این پژوهش چه می‌خواهید؟"}
    ]
    kinds = [(chip["kind"], chip.get("text", "")) for chip in turn.result["suggestions"]]
    assert ("answer", "مقایسه") in kinds
    assert ("skip", research.GRILLING_SKIP) in kinds


def test_advance_stage_never_moves_backward():
    state = research.new_research_state("پرسش؟")
    research.advance_stage(state, "investigating")
    assert state["stage"] == "investigating"
    research.advance_stage(state, "mapping")
    assert state["stage"] == "investigating"
    assert state["phase"] == "investigating"


def test_parse_grilling_reply_degrades_to_empty():
    assert research.parse_grilling_reply("not json") == {"question": "", "options": []}
    assert research.parse_grilling_reply('{"question": null}') == {
        "question": "",
        "options": [],
    }
    parsed = research.parse_grilling_reply(
        json.dumps(
            {"question": "پرسش؟", "options": ["الف", 5, "  ", "ب"]},
            ensure_ascii=False,
        )
    )
    assert parsed == {"question": "پرسش؟", "options": ["الف", "ب"]}


def test_parse_narration_rejects_junk_and_clips():
    # JSON-shaped junk narrates nothing; long prose clips to three
    # sentences.
    assert research.parse_narration_reply('{"blocks": []}') == ""
    assert research.parse_narration_reply("") == ""
    long = "یکم. دوم. سوم. چهارم."
    assert research.parse_narration_reply(long) == "یکم. دوم. سوم."


def test_the_first_research_turn_asks_the_destination_question(tmp_path):
    # The orientation stage's guided question: the author asks ONE
    # question (and never answers it), the chips are the options plus
    # the skip, and the map still shows no destination.
    upstream = ResearchUpstream(
        composer_replies=[
            classify_reply("research_exploration"),
            grilling_reply("از این پژوهش چه می‌خواهید؟", ("مقایسه", "نقشهٔ مفهوم")),
        ]
    )
    session = make_session(tmp_path)
    turn = run_turn_sync(session, "می‌خواهم دربارهٔ شهود بدانم", upstream, tmp_path)
    assert turn.state == "done"
    assert turn.result["reply"] == [
        {"type": "question", "text": "از این پژوهش چه می‌خواهید؟"}
    ]
    state = session["state"]
    assert state["grilling"]["current_question"] == "از این پژوهش چه می‌خواهید؟"
    assert state["grilling"]["asked_in_stage"] == 1
    assert state["stage"] == "orientation"
    assert state["map"]["destination"] == ""
    chips = turn.result["suggestions"]
    assert [chip["kind"] for chip in chips] == ["answer", "answer", "skip"]
    assert chips[0]["text"] == "مقایسه"


def test_the_destination_answer_lands_a_decision_and_maps_the_ground(tmp_path):
    # The answer's gist lands as the decision and names the destination;
    # the journey moves to mapping THIS turn and surveys the ground —
    # one searcher over the research question, one guarded writer pass.
    pool = [
        {"reference": "chunk 1 of document tarhe-kolli", "passage": SENTENCE},
        {"reference": "chunk 29 of document tarhe-kolli", "passage": OTHER_SENTENCE},
    ]
    state = research.new_research_state("پرسش پژوهش؟")
    state["grilling"] = {
        "asked_in_stage": 1,
        "current_question": "از این پژوهش چه می‌خواهید؟",
        "options": ["مقایسه"],
    }
    upstream = ResearchUpstream(
        composer_replies=[
            classify_reply("research_exploration", answer_gist="مقایسهٔ دو دیدگاه"),
            composer_reply(json.dumps(guarded_blocks(pool), ensure_ascii=False)),
        ]
    )
    session = make_session(tmp_path, state=state)
    turn = run_turn_sync(session, "مقایسه", upstream, tmp_path)
    assert turn.state == "done"
    state = session["state"]
    assert state["map"]["destination"] == "مقایسهٔ دو دیدگاه"
    assert state["decisions"] == [{"text": "مقایسهٔ دو دیدگاه", "turn": 1}]
    assert state["grilling"]["current_question"] == ""
    assert state["stage"] == "mapping"
    assert state["map"]["landscape_done"] is True
    # The landscape is a guarded document with references from its own
    # recall pool (the searcher's FED fixtures), in first-use order.
    blocks = turn.result["reply"]
    assert any(b["type"] == "paragraph" for b in blocks)
    assert blocks[-1]["items"] == [
        "chunk 1 of document tarhe-kolli (pages 10-12)",
        "chunk 29 of document tarhe-kolli",
    ]


def test_the_skip_rides_a_live_question_in_every_stage():
    # T10 (GitLab #11): the skip is universal — wherever the engine asks,
    # in ANY stage, the chip set carries the options plus the skip. No
    # stage's suggestions may strand the user without a way past a
    # question.
    for stage in research.RESEARCH_STAGES:
        state = research.new_research_state("پرسش پژوهش؟")
        state["stage"] = stage
        state["grilling"] = {
            "asked_in_stage": 1,
            "current_question": "پرسش زندهٔ راهنما؟",
            "options": ["گزینهٔ الف", "گزینهٔ ب"],
        }
        chips = research.research_suggestions(state)
        assert [chip["kind"] for chip in chips] == [
            "answer",
            "answer",
            "skip",
        ], stage
        assert chips[-1]["text"] == research.GRILLING_SKIP, stage


def test_a_late_stage_skip_lands_a_decision_and_stands_still(tmp_path):
    # Skipping in a working stage is still an answer: the decision lands
    # in the map's index, the live question clears, and the stage never
    # regresses — the guard and the checkpoints stay untouched.
    state = research.new_research_state("پرسش پژوهش؟")
    state["stage"] = "investigating"
    state["grilling"] = {
        "asked_in_stage": 1,
        "current_question": "پرسش زندهٔ راهنما؟",
        "options": [],
    }
    upstream = ResearchUpstream(
        composer_replies=[classify_reply("research_exploration")]
    )
    session = make_session(tmp_path, state=state)
    turn = run_turn_sync(session, research.GRILLING_SKIP, upstream, tmp_path)
    assert turn.state == "done"
    state = session["state"]
    assert state["stage"] == "investigating"
    assert any(
        d["text"] == research.GRILLING_SKIP_DECISION for d in state["decisions"]
    )
    assert state["grilling"]["current_question"] == ""
    assert any(
        b["type"] == "note" and "ادامه می‌دهیم" in b["text"]
        for b in turn.result["reply"]
    )


def test_the_skip_survives_the_cap_while_a_checkpoint_waits():
    # T10 (GitLab #11), review finding: proposal chips lead the set, so
    # a live question's options trim to the room left — the skip itself
    # is never a truncation casualty, and the mapping stage's facet
    # chips ride the same reserved-slot rule.
    state = research.new_research_state("پرسش پژوهش؟")
    state["pending_proposals"] = [
        {"id": "p1", "kind": "research_question", "text": "پرسش دقیق‌تر؟"}
    ]
    state["grilling"] = {
        "asked_in_stage": 1,
        "current_question": "پرسش زندهٔ راهنما؟",
        "options": ["الف", "ب", "ج", "د"],
    }
    chips = research.research_suggestions(state)
    assert len(chips) <= 6
    assert chips[-1]["kind"] == "skip"
    assert chips[-1]["text"] == research.GRILLING_SKIP
    assert sum(1 for chip in chips if chip["kind"] == "answer") == 3
    mapping = research.new_research_state("پرسش پژوهش؟")
    mapping["stage"] = "mapping"
    mapping["pending_proposals"] = [
        {"id": "p1", "kind": "research_question", "text": "پرسش دقیق‌تر؟"},
        {"id": "p2", "kind": "scope", "text": "دامنه"},
    ]
    mapping["concepts"] = ["مفهوم الف", "مفهوم ب", "مفهوم ج"]
    chips = research.research_suggestions(mapping)
    assert len(chips) <= 6
    assert chips[-1] == {"kind": "skip", "id": "skip", "text": research.GRILLING_SKIP}
    assert sum(1 for chip in chips if chip.get("id") == "facet") == 1


def test_the_skip_ends_the_orientation_questioning(tmp_path):
    upstream = ResearchUpstream(
        composer_replies=[classify_reply("research_exploration")]
    )
    session = make_session(tmp_path)
    turn = run_turn_sync(session, research.GRILLING_SKIP, upstream, tmp_path)
    assert turn.state == "done"
    state = session["state"]
    assert state["stage"] == "mapping"
    # No destination was given: the current question stands in for it.
    assert state["map"]["destination"] == "پرسش پژوهش؟"
    assert state["grilling"]["asked_in_stage"] == research.RESEARCH_GRILLING_STAGE_CAP
    assert any(
        b["type"] == "note" and "آغاز می‌کنیم" in b["text"]
        for b in turn.result["reply"]
    )


def test_the_mapping_exit_reads_the_open_questions_by_name(tmp_path):
    state = research.new_research_state("پرسش پژوهش؟")
    state["stage"] = "mapping"
    state["map"]["landscape_done"] = True
    research._add_open_question(state, "متن پرسش الف؟", "نام الف")
    research._add_open_question(state, "متن پرسش ب؟", "نام ب")
    upstream = ResearchUpstream(
        composer_replies=[classify_reply("research_exploration")]
    )
    session = make_session(tmp_path, state=state)
    turn = run_turn_sync(session, "هر دو مهم است", upstream, tmp_path)
    assert turn.state == "done"
    state = session["state"]
    assert state["stage"] == "investigating"
    notes = " ".join(b["text"] for b in turn.result["reply"] if b["type"] == "note")
    assert "«نام الف»" in notes and "«نام ب»" in notes
    # The investigating chips: the gather-all plus one targeted chip per
    # named open question.
    texts = [chip["text"] for chip in turn.result["suggestions"]]
    assert "شواهدِ «نام الف» را پیدا کن" in texts
    assert "شواهدِ «نام ب» را پیدا کن" in texts


def test_a_targeted_gather_chip_gathers_only_that_question(tmp_path):
    state = research.new_research_state("پرسش پژوهش؟")
    state["stage"] = "investigating"
    research._add_open_question(state, "متن پرسش الف؟", "نام الف")
    research._add_open_question(state, "متن پرسش ب؟", "نام ب")
    queries = []

    def recall(payload):
        queries.append(payload["query"])
        return fed_by_query(payload)

    upstream = ResearchUpstream(
        composer_replies=[
            composer_reply("این دورِ شواهد خوب پیش رفت."),
        ],
        recall_reply=recall,
    )
    session = make_session(tmp_path, state=state)
    turn = run_turn_sync(
        session, "شواهدِ «نام الف» را پیدا کن", upstream, tmp_path
    )
    assert turn.state == "done"
    state = session["state"]
    assert queries == ["متن پرسش الف؟"]
    statuses = {item["name"]: item["status"] for item in state["subquestions"]}
    assert statuses == {"نام الف": "searched", "نام ب": "pending"}
    # The narration note opens the reply; the server's count note rides
    # beneath it.
    assert turn.result["reply"][0] == {
        "type": "note",
        "text": "این دورِ شواهد خوب پیش رفت.",
    }
    assert any("۲ نقل‌قول تازه" in b["text"] for b in turn.result["reply"][1:])


def test_a_junk_narration_narrates_nothing(tmp_path):
    # A JSON-shaped narration reply narrates nothing — the operation's
    # own server-composed notes stand alone.
    state = research.new_research_state("پرسش پژوهش؟")
    state["stage"] = "investigating"
    research._add_open_question(state, "زیرپرسش؟", "نام")
    upstream = ResearchUpstream(
        composer_replies=[
            classify_reply("active_research"),
            composer_reply('{"blocks": []}'),
        ],
        recall_reply=fed_by_query,
    )
    session = make_session(tmp_path, state=state)
    turn = run_turn_sync(session, research.COMMAND_GATHER, upstream, tmp_path)
    assert turn.state == "done"
    assert turn.result["reply"][0]["type"] == "note"
    assert "نقل‌قول تازه" in turn.result["reply"][0]["text"]


def test_a_failed_grilling_author_falls_through_to_the_move(tmp_path):
    # The author came back empty (junk reply): the turn is NOT a dead
    # end — the stage's ordinary move runs, exactly the V0.1 ladder.
    upstream = ResearchUpstream(
        composer_replies=[
            classify_reply("active_research", subquestions=["زیرپرسش؟"]),
            composer_reply("غیرمنتظره"),
        ],
        recall_reply=fed_by_query,
    )
    session = make_session(tmp_path)
    turn = run_turn_sync(session, "پیش برو", upstream, tmp_path)
    assert turn.state == "done"
    state = session["state"]
    assert state["grilling"]["current_question"] == ""
    assert len(state["evidence"]) == 2
    assert state["stage"] == "investigating"


def test_fog_graduates_into_named_open_questions():
    state = research.new_research_state("پرسش؟")
    research._add_fog(state, "نقشۀ شهود در انسان کامل هنوز روشن نیست")
    assert state["map"]["fog"]
    research._apply_classify_updates(
        state,
        {
            "intent": "research_exploration",
            "new_open_questions": [
                {"name": "شهود", "text": "نقشۀ شهود در انسان کامل چیست؟"}
            ],
            "fog": ["نقشۀ شهود در انسان کامل هنوز روشن نیست"],
            "concepts": [],
            "subquestions": [],
            "scope_in": [],
            "scope_out": [],
        },
    )
    # The sharp question landed, named; the matching fog note left.
    assert [q["name"] for q in state["subquestions"]] == ["شهود"]
    assert state["map"]["fog"] == []


def test_classify_prompt_carries_the_map_and_the_guided_question():
    state = research.new_research_state("پرسش پژوهش؟")
    state["map"]["destination"] = "مقایسهٔ دو دیدگاه"
    state["grilling"]["current_question"] = "کدام جنبه مهم‌تر است؟"
    prompt = research.build_classify_prompt("این یکی", state, "کاربر: این یکی")
    assert "destination" in prompt
    assert "کدام جنبه مهم‌تر است؟" in prompt
    assert "new_open_questions" in prompt
    assert "answer_gist" in prompt


def test_the_checkpoint_reply_shows_the_compact_diff():
    state = research.new_research_state("پرسش کهن؟")
    state["pending_proposals"] = [
        {
            "id": "p1",
            "kind": "research_question",
            "text": "پرسش تازه؟",
            "reason": "چون",
        }
    ]
    notes = [b["text"] for b in research._checkpoint_reply(state)]
    assert any("از «پرسش کهن؟» به «پرسش تازه؟»" in text for text in notes)
    assert any(text == "چرا: چون" for text in notes)


def test_the_state_projection_carries_the_map(tmp_path):
    research_store.RESEARCH_DB = tmp_path / "research.sqlite3"
    session = make_session(tmp_path)
    state = session["state"]
    research._add_open_question(state, "متن پرسش؟", "نام")
    state["map"]["destination"] = "مقصد آزمایشی"
    state["decisions"].append({"text": "تصمیم", "turn": 1})
    summary = research.research_state_summary(state)
    assert summary["stage"] == "orientation"
    assert summary["destination"] == "مقصد آزمایشی"
    assert summary["frontier"] == "نام"
    assert summary["open_questions"][0]["name"] == "نام"
    assert summary["decisions"] == ["تصمیم"]
    assert summary["grilling"] == {"question": "", "options": []}


# --- the Book selection (ADR-0010) -------------------------------------------


def test_validated_datasets_intersects_with_the_book_set():
    # The client's list, intersected with the Book set in the set's
    # stable order; anything outside never reaches an upstream; nothing
    # usable means None — the whole Book set.
    assert serve.validated_datasets(["70143-336", "bogus"]) == ["70143-336"]
    assert serve.validated_datasets(["bogus", "tarhe-kolli"]) == ["tarhe-kolli"]
    assert serve.validated_datasets(["tarhe-kolli", "70143-336"]) == [
        "tarhe-kolli",
        "70143-336",
    ]
    assert serve.validated_datasets(["nope"]) is None
    assert serve.validated_datasets("tarhe-kolli") is None
    assert serve.validated_datasets(None) is None
    assert serve.validated_datasets([]) is None


def test_the_recall_proxy_validates_the_body_and_names_no_foreign_dataset(tmp_path):
    # The proxy stops forwarding blind: the query is required, and the
    # datasets the cognee service receives are the intersection with
    # the Book set.
    upstream = ResearchUpstream()
    base, server, original = with_gate(tmp_path, upstream)
    try:
        serve.record_chat(ACCOUNT)
        status, _ = post(
            base,
            "/api/v1/recall",
            {
                "query": "پرسش؟",
                "datasets": ["tarhe-kolli", "bogus"],
                "searchType": "HYBRID_COMPLETION",
            },
            phone=PHONE,
        )
        no_query, _ = post(
            base,
            "/api/v1/recall",
            {"datasets": ["tarhe-kolli"]},
            phone=PHONE,
        )
    finally:
        stop_gate(server, original)
    assert status == 200
    recalls = [
        json.loads(body)
        for url, body in zip(upstream.calls, upstream.bodies)
        if url.count("api/v1/recall") and "next" not in url
    ]
    assert len(recalls) == 1
    assert recalls[0]["datasets"] == ["tarhe-kolli"]
    assert recalls[0]["query"] == "پرسش؟"
    assert no_query == 400


def test_a_research_session_searches_the_selected_books_only(tmp_path):
    # The creating message's datasets ride into the session state and
    # every gather's searchers search only those Books.
    searched = []

    def recall(payload):
        searched.append(payload["datasets"])
        return fed_by_query(payload)

    upstream = ResearchUpstream(
        composer_replies=[
            classify_reply("active_research", subquestions=["زیرپرسش؟"]),
            composer_reply(""),
        ],
        recall_reply=recall,
    )
    base, server, original = with_gate(tmp_path, upstream)
    try:
        serve.record_chat(ACCOUNT)
        _, body = post(
            base,
            "/research/message",
            {
                "text": research.COMMAND_GATHER,
                "question": "پرسش؟",
                "datasets": ["70143-336", "bogus"],
            },
            phone=PHONE,
        )
        wait_turn_done(body["turn_id"])
        loaded = research_store.load_session(body["session_id"])
    finally:
        stop_gate(server, original)
    assert searched and all(datasets == ["70143-336"] for datasets in searched)
    # The state records the RESOLVED selection, never the browser's raw
    # list with its bogus name.
    assert loaded["state"]["datasets"] == ["70143-336"]


def test_a_session_without_a_selection_defaults_to_the_whole_book_set(tmp_path):
    research_store.RESEARCH_DB = tmp_path / "research.sqlite3"
    session, _ = research.ensure_session(PHONE, None, "پیام", "پرسش؟", [])
    assert session["state"]["datasets"] == list(dive.BOOK_DATASETS)


# --- the planning-loop breaker (ADR-0011) ------------------------------------


def test_an_active_research_turn_parks_no_proposals(tmp_path):
    # The chart-mode gate: an investigation turn is WORK — its classify
    # suggestions fold concepts and questions but NEVER become
    # checkpoints. The recorded loop was exactly this: a gather turn
    # parking a fresh RQ proposal and answering «پرسش پژوهش به‌روز شد».
    upstream = ResearchUpstream(
        composer_replies=[
            composer_reply(json.dumps(["زیرپرسش؟"])),
        ],
        recall_reply=fed_by_query,
    )
    session = make_session(tmp_path)
    turn = run_turn_sync(session, research.COMMAND_GATHER, upstream, tmp_path)
    assert turn.state == "done"
    state = session["state"]
    assert state["pending_proposals"] == []
    # The gather ran; the question folded in.
    assert len(state["evidence"]) == 2
    assert any(item["text"] == "زیرپرسش؟" for item in state["subquestions"])


def test_a_forced_command_outranks_a_pending_checkpoint(tmp_path):
    # The work-mode rule: an explicit command EXECUTES even while a
    # proposal waits — the parked decision survives for the next free
    # turn and its chips still render.
    state = research.new_research_state("پرسش پژوهش؟")
    state["stage"] = "investigating"
    state["pending_proposals"] = [
        {"id": "p1", "kind": "research_question", "text": "پرسش دقیق‌تر؟"}
    ]
    upstream = ResearchUpstream(
        composer_replies=[
            classify_reply("active_research", subquestions=["زیرپرسش؟"]),
            composer_reply(""),
        ],
        recall_reply=fed_by_query,
    )
    session = make_session(tmp_path, state=state)
    turn = run_turn_sync(session, research.COMMAND_GATHER, upstream, tmp_path)
    assert turn.state == "done"
    state = session["state"]
    # The gather ran — evidence landed, not a checkpoint reply.
    assert len(state["evidence"]) == 2
    notes = " ".join(b.get("text", "") for b in turn.result["reply"])
    assert "تصمیم پیش روی شماست" not in notes
    assert "نقل‌قول تازه" in notes
    # The proposal still waits, and its chips still render — the
    # decision pair leads, the journey's moves follow (T7 #8: the chips
    # are never hidden behind a waiting proposal).
    assert state["pending_proposals"][0]["text"] == "پرسش دقیق‌تر؟"
    kinds = [chip["kind"] for chip in turn.result["suggestions"]]
    assert kinds[:2] == ["proposal", "proposal"]
    assert "move" in kinds


def test_a_decision_cools_the_map_for_two_turns():
    # The damper: after a decided proposal of a kind, the next
    # PROPOSAL_COOLDOWN_TURNS full turns park nothing new of that kind
    # (the worker decrements AFTER the parking check — mirror that
    # order here), and the same decided text never comes back even
    # after the cooldown.
    state = research.new_research_state("پرسش پژوهش؟")
    cooldowns = state["proposal_cooldowns"]
    cooldowns["research_question"] = research.PROPOSAL_COOLDOWN_TURNS
    for turn_number in range(1, research.PROPOSAL_COOLDOWN_TURNS + 1):
        research._apply_classify_updates(
            state,
            {
                "intent": "research_exploration",
                "rq_proposal": f"پرسش دیگری {turn_number}؟",
                "reason": "",
                "concepts": [],
                "subquestions": [],
                "scope_in": [],
                "scope_out": [],
            },
        )
        cooldowns["research_question"] -= 1
    assert state["pending_proposals"] == []
    # Cooldown spent: a materially new exploration proposal parks again
    # — the checkpoint rule stays alive.
    research._apply_classify_updates(
        state,
        {
            "intent": "research_exploration",
            "rq_proposal": "پرسش تازهٔ واقعی؟",
            "reason": "",
            "concepts": [],
            "subquestions": [],
            "scope_in": [],
            "scope_out": [],
        },
    )
    assert [p["text"] for p in state["pending_proposals"]] == ["پرسش تازهٔ واقعی؟"]


def test_a_decided_text_never_reproposes():
    # Dedupe against past decisions: the SAME decided proposal never
    # returns, whatever the cooldown.
    state = research.new_research_state("پرسش پژوهش؟")
    state["decisions"] = [{"text": "پیشنهاد رد شد: «پرسش رد‌شده؟»", "turn": 1}]
    research._apply_classify_updates(
        state,
        {
            "intent": "research_exploration",
            "rq_proposal": "پرسش رد‌شده؟",
            "reason": "",
            "concepts": [],
            "subquestions": [],
            "scope_in": [],
            "scope_out": [],
        },
    )
    assert state["pending_proposals"] == []


# --- the transcript read (ADR-0011) ------------------------------------------


def test_the_messages_endpoint_gates_the_phone_and_returns_the_transcript(tmp_path):
    upstream = ResearchUpstream(
        composer_replies=[
            classify_reply("casual_question"),
            composer_reply(
                json.dumps(
                    guarded_blocks(
                        [
                            {
                                "reference": "chunk 1 of document tarhe-kolli",
                                "passage": SENTENCE,
                            }
                        ]
                    ),
                    ensure_ascii=False,
                )
            ),
        ]
    )
    base, server, original = with_gate(tmp_path, upstream)
    try:
        serve.record_chat(ACCOUNT)
        _, body = post(
            base,
            "/research/message",
            {"text": "یک پرسش معمولی", "question": "پرسش پژوهش؟"},
            phone=PHONE,
        )
        wait_turn_done(body["turn_id"])
        status, payload = get(
            base,
            f"/research/messages?session={body['session_id']}",
            phone=PHONE,
        )
        foreign, _ = get(
            base,
            f"/research/messages?session={body['session_id']}",
            phone=OTHER_PHONE,
        )
        unknown, _ = get(base, "/research/messages", phone=PHONE)
    finally:
        stop_gate(server, original)
    assert status == 200
    roles = [m["role"] for m in payload["messages"]]
    assert roles == ["user", "assistant"]
    # The assistant payload is the reply's block list, renderable as-is.
    assert isinstance(payload["messages"][1]["payload"], list)
    assert foreign == 404
    assert unknown == 404


# --- the endpoints -----------------------------------------------------------


def test_research_message_gate_is_the_balance_only_shape(tmp_path):
    # ADR-0015 (the composer's research toggle): the old
    # minimum-of-one-chat precondition is gone — the research
    # conversation may be the day's first act, seeded by the typed
    # question alone. No login -> 401 (ADR-0013 — the phone header no
    # longer authenticates anything); a logged-in Account with a
    # positive Balance reaches the handler (202, the turn admitted);
    # the admitted turn's classify call ran upstream.
    upstream = ResearchUpstream()
    base, server, original = with_gate(tmp_path, upstream)
    try:
        no_phone, _ = post(
            base, "/research/message", {"text": "پیام", "question": "پرسش؟"}
        )
        status, payload = post(
            base,
            "/research/message",
            {"text": "پیام", "question": "پرسش؟"},
            phone=PHONE,
        )
    finally:
        stop_gate(server, original)
    assert no_phone == 401
    assert status == 202
    assert payload["turn_id"] and payload["session_id"]
    wait_turn_done(payload["turn_id"])
    assert upstream.calls, "the admitted turn ran"


def test_research_message_rejects_bad_bodies(tmp_path):
    upstream = ResearchUpstream()
    base, server, original = with_gate(tmp_path, upstream)
    try:
        serve.record_chat(ACCOUNT)
        empty, _ = post(base, "/research/message", {"text": "   "}, phone=PHONE)
        no_question, _ = post(
            base, "/research/message", {"text": "پیام"}, phone=PHONE
        )
    finally:
        stop_gate(server, original)
    assert empty == 400
    assert no_question == 400
    assert upstream.calls == []


def test_a_message_answers_the_turn_identity_and_never_counts_a_chat(tmp_path):
    upstream = ResearchUpstream(
        composer_replies=[classify_reply("active_research", subquestions=["زیرپرسش؟"])]
    )
    base, server, original = with_gate(tmp_path, upstream)
    try:
        serve.record_chat(ACCOUNT)
        status, body = post(
            base,
            "/research/message",
            {
                "text": research.COMMAND_GATHER,
                "question": "پرسش پژوهش؟",
                "sources": [
                    {
                        "reference": "chunk 7 of document tarhe-kolli",
                        "passage": "جملۀ بنیان‌گذارِ پژوهش.",
                    }
                ],
            },
            phone=PHONE,
        )
        assert status == 202
        turn = wait_turn_done(body["turn_id"])
        poll_status, payload = get(
            base, f"/research/turn?turn={body['turn_id']}", phone=PHONE
        )
    finally:
        stop_gate(server, original)
    assert poll_status == 200
    assert payload["state"] == "done"
    assert payload["reply"]
    assert payload["suggestions"]
    # The creating call's pool seeded the ledger: 1 founding + 2 pooled.
    assert payload["research_state"]["evidence_count"] == 3
    # Research belongs to a chat that already started: it neither counts
    # nor checks the five-per-day limit.
    assert serve.chats_today(ACCOUNT) == 1


def test_research_runs_even_at_the_daily_limit(tmp_path):
    upstream = ResearchUpstream(
        composer_replies=[classify_reply("active_research", subquestions=["زیرپرسش؟"])]
    )
    base, server, original = with_gate(tmp_path, upstream)
    try:
        for _ in range(serve.DAILY_CHAT_LIMIT):
            serve.record_chat(ACCOUNT)
        status, body = post(
            base,
            "/research/message",
            {"text": research.COMMAND_GATHER, "question": "پرسش؟"},
            phone=PHONE,
        )
        assert status == 202
        turn = wait_turn_done(body["turn_id"])
    finally:
        stop_gate(server, original)
    assert turn.state == "done"


def test_a_session_survives_the_server_and_continues(tmp_path):
    # The store's whole point: the session reloads from SQLite — here
    # across two separate turns (a fresh load per message), the second
    # continuing the first's ledger.
    upstream = ResearchUpstream(
        composer_replies=[
            # W4 (stage C): a chip gather plans its own sub-questions —
            # the reply is the planning call's JSON list now; classify
            # never runs for a resolved command.
            composer_reply(json.dumps(["زیرپرسش یک؟"])),
            # Each material gather ends with one narration call; an
            # empty narration reply narrates nothing.
            composer_reply(""),
            composer_reply(json.dumps(["زیرپرسش دو؟"])),
            composer_reply(""),
        ],
        recall_reply=fed_by_query,
    )
    base, server, original = with_gate(tmp_path, upstream)
    try:
        serve.record_chat(ACCOUNT)
        _, first = post(
            base,
            "/research/message",
            {"text": research.COMMAND_GATHER, "question": "پرسش؟"},
            phone=PHONE,
        )
        wait_turn_done(first["turn_id"])
        # A "restart" of the registry does not lose the session.
        research.RESEARCH_REGISTRY.clear()
        status, second = post(
            base,
            "/research/message",
            {"text": research.COMMAND_GATHER, "session_id": first["session_id"]},
            phone=PHONE,
        )
        assert status == 202
        wait_turn_done(second["turn_id"])
        _, payload = get(
            base, f"/research/turn?turn={second['turn_id']}", phone=PHONE
        )
    finally:
        stop_gate(server, original)
    assert payload["research_state"]["evidence_count"] == 4  # 2 + 2 across both turns
    assert payload["research_state"]["turns"] == 2


def test_the_turn_poll_gates_the_phone(tmp_path):
    gate = threading.Event()
    upstream = ResearchUpstream(
        composer_replies=[],
        gate=gate,
    )
    base, server, original = with_gate(tmp_path, upstream)
    try:
        serve.record_chat(ACCOUNT)
        _, body = post(
            base,
            "/research/message",
            {"text": research.COMMAND_GATHER, "question": "پرسش؟"},
            phone=PHONE,
        )
        turn_id = body["turn_id"]
        unknown_status, unknown = get(
            base, "/research/turn?turn=does-not-exist", phone=PHONE
        )
        # A running turn belongs to its Account: another Account — and
        # an anonymous request (401, ADR-0013) — learns nothing.
        other_status, _ = get(
            base, f"/research/turn?turn={turn_id}", phone=OTHER_PHONE
        )
        no_phone_status, _ = get(base, f"/research/turn?turn={turn_id}")
        gate.set()
        wait_turn_done(turn_id)
    finally:
        gate.set()
        stop_gate(server, original)
    assert unknown_status == 404
    assert other_status == 404
    assert no_phone_status == 401
    assert unknown["detail"] == research.RESEARCH_TURN_NOT_FOUND_DETAIL


def test_a_second_message_while_one_runs_is_rejected_farsi_busy(tmp_path):
    gate = threading.Event()
    upstream = ResearchUpstream(
        composer_replies=[classify_reply("active_research", subquestions=["زیرپرسش؟"])],
        gate=gate,
    )
    base, server, original = with_gate(tmp_path, upstream)
    try:
        serve.record_chat(ACCOUNT)
        _, body = post(
            base,
            "/research/message",
            {"text": research.COMMAND_GATHER, "question": "پرسش؟"},
            phone=PHONE,
        )
        turn_id = body["turn_id"]
        busy_status, busy = post(
            base,
            "/research/message",
            {"text": "پیام دیگر", "session_id": body["session_id"]},
            phone=PHONE,
        )
        gate.set()
        wait_turn_done(turn_id)
    finally:
        gate.set()
        stop_gate(server, original)
    assert busy_status == 429
    assert busy["detail"] == research.RESEARCH_BUSY_ACCOUNT_DETAIL


def test_a_new_ask_pauses_research_without_closing_it(tmp_path):
    # Decision 04 (the research-mode v2 wayfinder map): a new normal
    # ask pauses the Account's in-flight research turn — cooperatively
    # — and queues its message for the resume chip. The session itself
    # stays OPEN: no close, no 409, the investigation lives beside the
    # ask, and the chip re-runs the USER'S words, never its own text.
    gate = threading.Event()
    paused_message = "تحلیل جامع نوآوری‌های کتاب را پیش ببر"
    fed_pool = [
        {
            "reference": "chunk 1 of document tarhe-kolli (pages 10-12)",
            "passage": SENTENCE,
        },
        {"reference": "chunk 29 of document tarhe-kolli", "passage": OTHER_SENTENCE},
    ]
    upstream = ResearchUpstream(
        composer_replies=[
            classify_reply("research_exploration"),
            classify_reply("casual_question"),
            composer_reply(json.dumps(guarded_blocks(fed_pool), ensure_ascii=False)),
        ],
        gate=gate,
    )
    base, server, original = with_gate(tmp_path, upstream)
    try:
        serve.record_chat(ACCOUNT)
        _, body = post(
            base,
            "/research/message",
            {"text": paused_message, "question": "پرسش؟"},
            phone=PHONE,
        )
        turn_id, session_id = body["turn_id"], body["session_id"]
        # The new ask: the in-flight turn aborts cooperatively — a
        # pause, never a close.
        ask_status, _ = post(
            base, "/api/v1/recall", {"query": "پرسش جدید؟"}, phone=PHONE
        )
        assert ask_status == 200
        poll_status, payload = get(
            base, f"/research/turn?turn={turn_id}", phone=PHONE
        )
        assert poll_status == 200
        assert payload["state"] == "aborted"
        gate.set()
        wait_turn_done(turn_id)
        # The state endpoint answers 200 — the session never closed —
        # and its chip row leads with the resume chip.
        state_status, state = get(
            base, f"/research/state?session={session_id}", phone=PHONE
        )
        assert state_status == 200
        assert {
            "kind": "move",
            "id": "resume",
            "text": research.COMMAND_RESUME,
        } in state["suggestions"]
        # The resume chip re-runs the paused message: the classifier
        # sees the user's own words, never the chip text.
        resume_status, resumed = post(
            base,
            "/research/message",
            {"text": research.COMMAND_RESUME, "session_id": session_id},
            phone=PHONE,
        )
        assert resume_status == 202
        turn = wait_turn_done(resumed["turn_id"])
        assert turn.state == "done"
        assert research._PAUSED_MESSAGES.get(session_id) is None
        # Composer order: the paused turn's classify, the resumed
        # turn's classify, its writer — the SECOND is the proof: the
        # re-run classified the user's paused words, never the chip.
        composer_at = [
            i for i, call in enumerate(upstream.calls)
            if "chat/completions" in call
        ]
        assert len(composer_at) >= 2, "no classify call reached the upstream"
        resumed_request = json.loads(upstream.bodies[composer_at[1]])
        resumed_prompt = " ".join(
            message.get("content", "")
            for message in resumed_request.get("messages", [])
        )
        # The swap's proof: the classify prompt's LATEST message is the
        # user's paused words (the chip text may ride the transcript
        # tail as history — that is where the sheet's own record put
        # it).
        assert f"Latest message: {paused_message}" in resumed_prompt
    finally:
        gate.set()
        stop_gate(server, original)
    assert turn.result is not None


def test_stop_closes_and_the_closed_door_forks_the_state(
    tmp_path, monkeypatch
):
    # Decision 04's other half: closing is the user's word alone
    # (COMMAND_STOP keeps its behavior), and the closed door
    # (resume_closed_session) forks the stopped state into a fresh
    # open session — evidence and decisions ride along, the old row
    # stays closed, an open session never forks, and a foreign account
    # never sees the door.
    monkeypatch.setattr(
        research.research_store, "RESEARCH_DB", tmp_path / "research.sqlite3"
    )
    state = research.new_research_state("هدف پژوهش")
    state["evidence"].append(
        {
            "id": "e1",
            "reference": "chunk 1 of document tarhe-kolli (pages 1-9)",
            "passage": "متن شاهد",
            "found_for": "هدف پژوهش",
        }
    )
    state["closed"] = True
    research.research_store.create_session("stopped", ACCOUNT, state)
    research.research_store.create_session(
        "open", ACCOUNT, research.new_research_state("هدف دوم")
    )
    forked, error = research.resume_closed_session(ACCOUNT, "stopped")
    assert error is None and forked is not None
    fork = research.research_store.load_session(forked["session_id"])
    assert fork["account"] == ACCOUNT
    assert not fork["state"].get("closed")
    assert len(fork["state"]["evidence"]) == 1
    assert "ادامهٔ پژوهش از وضعیت" in fork["state"]["decisions"][-1]["text"]
    stopped = research.research_store.load_session("stopped")
    assert stopped["state"].get("closed") is True
    _, still_open = research.resume_closed_session(ACCOUNT, "open")
    assert still_open == (409, research.RESEARCH_RESUME_OPEN_DETAIL)
    _, foreign = research.resume_closed_session(OTHER_ACCOUNT, "stopped")
    assert foreign == (404, research.RESEARCH_SESSION_NOT_FOUND_DETAIL)


def test_a_new_ask_by_another_phone_never_aborts_someone_elses_turn(tmp_path):
    gate = threading.Event()
    upstream = ResearchUpstream(
        composer_replies=[composer_reply(json.dumps(["زیرپرسش؟"]))],
        gate=gate,
    )
    base, server, original = with_gate(tmp_path, upstream)
    try:
        serve.record_chat(ACCOUNT)
        serve.record_chat(OTHER_ACCOUNT)
        _, body = post(
            base,
            "/research/message",
            {"text": research.COMMAND_GATHER, "question": "پرسش؟"},
            phone=PHONE,
        )
        turn_id = body["turn_id"]
        post(base, "/api/v1/recall", {"query": "پرسش دیگر؟"}, phone=OTHER_PHONE)
        poll_status, payload = get(
            base, f"/research/turn?turn={turn_id}", phone=PHONE
        )
        assert poll_status == 200
        # W4 (stage C): a chip turn's first upstream call is its own
        # planning call now — the turn parks in "planning", not
        # "classifying", while the gate holds it.
        assert payload["state"] == "planning"
        gate.set()
        turn = wait_turn_done(turn_id)
    finally:
        gate.set()
        stop_gate(server, original)
    assert turn.state == "done"
    assert turn.result is not None


def test_the_decide_endpoint_resolves_a_checkpoint(tmp_path):
    upstream = ResearchUpstream(
        composer_replies=[
            classify_reply(
                "research_exploration",
                rq_proposal="پرسش دقیق‌تر؟",
                reason="چون",
            )
        ]
    )
    base, server, original = with_gate(tmp_path, upstream)
    try:
        serve.record_chat(ACCOUNT)
        _, body = post(
            base,
            "/research/message",
            {"text": "پرسش را دقیق‌تر کنیم", "question": "پرسش پژوهش؟"},
            phone=PHONE,
        )
        turn = wait_turn_done(body["turn_id"])
        proposal_id = turn.result["research_state"]["pending_proposals"][0]["id"]
        status, payload = post(
            base,
            "/research/decide",
            {
                "session_id": body["session_id"],
                "proposal_id": proposal_id,
                "accept": True,
            },
            phone=PHONE,
        )
        state_status, state_payload = get(
            base, f"/research/state?session={body['session_id']}", phone=PHONE
        )
    finally:
        stop_gate(server, original)
    assert status == 200
    assert payload["research_state"]["research_question"] == "پرسش دقیق‌تر؟"
    assert payload["research_state"]["rq_versions"] == 2
    assert state_status == 200
    assert state_payload["research_state"]["research_question"] == "پرسش دقیق‌تر؟"


def test_the_state_endpoint_gates_the_phone_and_session(tmp_path):
    upstream = ResearchUpstream()
    base, server, original = with_gate(tmp_path, upstream)
    try:
        serve.record_chat(ACCOUNT)
        foreign, _ = get(base, "/research/state?session=no-such", phone=PHONE)
        empty, _ = get(base, "/research/state", phone=PHONE)
    finally:
        stop_gate(server, original)
    assert foreign == 404
    assert empty == 404


def test_the_state_read_returns_the_chips_so_a_refresh_keeps_the_skip(tmp_path):
    # T10 (GitLab #11): the sheet's refresh re-fetch reads the map AND
    # the chips from /research/state — a live guided question's options
    # and its skip survive the reload instead of stranding the user.
    upstream = ResearchUpstream(
        composer_replies=[
            classify_reply("research_exploration"),
            grilling_reply("از این پژوهش چه می‌خواهید؟", ("مقایسه",)),
        ]
    )
    base, server, original = with_gate(tmp_path, upstream)
    try:
        serve.record_chat(ACCOUNT)
        _, body = post(
            base,
            "/research/message",
            {"text": "می‌خواهم دربارهٔ شهود بدانم", "question": "پرسش پژوهش؟"},
            phone=PHONE,
        )
        wait_turn_done(body["turn_id"])
        status, payload = get(
            base, f"/research/state?session={body['session_id']}", phone=PHONE
        )
    finally:
        stop_gate(server, original)
    assert status == 200
    assert payload["research_state"]["grilling"]["question"] == (
        "از این پژوهش چه می‌خواهید؟"
    )
    kinds = [chip["kind"] for chip in payload["suggestions"]]
    assert kinds == ["answer", "skip"]
    assert payload["suggestions"][-1]["text"] == research.GRILLING_SKIP


def test_a_foreign_session_id_is_rejected(tmp_path):
    upstream = ResearchUpstream()
    base, server, original = with_gate(tmp_path, upstream)
    try:
        serve.record_chat(ACCOUNT)
        status, payload = post(
            base,
            "/research/message",
            {"text": "پیام", "session_id": "someone-elses"},
            phone=PHONE,
        )
    finally:
        stop_gate(server, original)
    assert status == 404
    assert payload["detail"] == research.RESEARCH_SESSION_NOT_FOUND_DETAIL


def test_an_abort_landing_before_the_next_write_is_never_overwritten():
    # The settle-guard: an abort between the worker's cancel checks and
    # its next registry write must hold — clobbering it back to live
    # would leave a zombie turn holding the caps until restart.
    turn = research.ResearchTurn(PHONE, "session", "پیام")
    research.RESEARCH_REGISTRY[turn.id] = turn
    assert research.abort_research_turn(turn) is True
    assert research._turn_write(turn, "searching", research.RESEARCH_EVENT_SEARCHING) is False
    assert turn.state == "aborted"
    assert research.RESEARCH_EVENT_SEARCHING not in turn.events
    del research.RESEARCH_REGISTRY[turn.id]


# --- stage B (research-mode v2): the steering doors -------------------------


def test_a_corpus_change_parks_and_decides_as_its_own_checkpoint():
    # Decision 05 (option B): a cross-Book message parks a corpus
    # proposal — exploration-only, never the set the session already
    # searches — and the decide flow flips state["datasets"], which
    # every searcher reads at call time.
    state = research.new_research_state("پرسش؟")
    state["datasets"] = ["tarhe-kolli"]
    research._apply_classify_updates(
        state,
        {
            "intent": "research_exploration",
            "corpus": ["tarhe-kolli", "70143-336"],
        },
    )
    parked = [p for p in state["pending_proposals"] if p["kind"] == "corpus"]
    assert len(parked) == 1
    assert parked[0]["datasets"] == ["tarhe-kolli", "70143-336"]
    # The same set never re-parks; a working turn never parks at all.
    research._apply_classify_updates(
        state,
        {"intent": "research_exploration", "corpus": ["tarhe-kolli", "70143-336"]},
    )
    assert (
        len([p for p in state["pending_proposals"] if p["kind"] == "corpus"]) == 1
    )
    fresh = research.new_research_state("پرسش؟")
    fresh["datasets"] = ["tarhe-kolli"]
    research._apply_classify_updates(
        fresh,
        {"intent": "active_research", "corpus": ["tarhe-kolli", "70143-336"]},
    )
    assert fresh["pending_proposals"] == []
    # The decide applies the flip; the state summary carries the corpus
    # the session searches (the engine's corpus-blindness ends).
    state["pending_proposals"] = []
    decision = research._apply_decision(state, parked[0], True)
    assert state["datasets"] == ["tarhe-kolli", "70143-336"]
    assert "دامنۀ کتاب‌های پژوهش به‌روز شد" in decision
    assert research.research_state_summary(state)["datasets"] == [
        "tarhe-kolli",
        "70143-336",
    ]


def test_the_plan_request_chip_parks_a_plan_deterministically(tmp_path):
    # Decision 03's deadlock exit: the chip resolves without the
    # classifier's luck — one dedicated composer call asks ONLY for the
    # section plan, and the plan parks as the checkpoint the decide
    # flow owns.
    plan_reply = composer_reply(
        json.dumps(
            {
                "brief_plan": [
                    {"title": "تز کتاب", "question": "یکی", "claims": []},
                    {"title": "شواهد", "question": "دو", "claims": []},
                ]
            },
            ensure_ascii=False,
        )
    )
    upstream = ResearchUpstream(
        composer_replies=[plan_reply]
    )
    session = make_session(tmp_path)
    turn = run_turn_sync(
        session, research.COMMAND_SUGGEST_PLAN, upstream, tmp_path
    )
    assert turn.state == "done"
    loaded = research_store.load_session(session["id"])["state"]
    parked = [
        p for p in loaded["pending_proposals"] if p["kind"] == "brief_plan"
    ]
    assert len(parked) == 1
    assert [s["title"] for s in parked[0]["sections"]] == ["تز کتاب", "شواهد"]
    # The reply IS the checkpoint card, and the decide chips ride it.
    assert any(
        "یک تصمیم پیش روی شماست" in b.get("text", "")
        for b in turn.result["reply"]
        if isinstance(b, dict)
    )
    assert any(chip.get("id") == parked[0]["id"] for chip in turn.result["suggestions"])


def test_the_plan_item_door_steers_sections_individually(tmp_path):
    # Decision 03's per-item door: an accepted plan's sections are
    # rejected, edited, and re-accepted by their stable keys — the
    # contracts follow, and an all-rejected plan refuses the Brief as
    # honestly as no plan at all.
    session = make_session(tmp_path)
    state = session["state"]
    sections = [
        {"title": "یکی", "question": "", "claims": [], "key": "k1", "status": "accepted"},
        {"title": "دو", "question": "", "claims": [], "key": "k2", "status": "accepted"},
    ]
    state["brief_plan"] = {"current": {"sections": sections}, "versions": []}
    state["section_contracts"] = research._section_contracts_from_plan(
        state, sections
    )
    research_store.save_session(session["id"], state)
    result, error = research.decide_plan_item(
        PHONE, session["id"], "k2", "rejected"
    )
    assert error is None
    assert "رد شد" in result["reply"][0]["text"]
    loaded = research_store.load_session(session["id"])["state"]
    statuses = {
        s["key"]: s["status"]
        for s in loaded["brief_plan"]["current"]["sections"]
    }
    assert statuses == {"k1": "accepted", "k2": "rejected"}
    assert {
        c["key"]: c["status"] for c in loaded["section_contracts"]
    } == statuses
    # The edit takes a new title and lands as its own decision kind.
    result, error = research.decide_plan_item(
        PHONE, session["id"], "k1", "accepted", edited="عنوان تازه"
    )
    assert error is None
    loaded = research_store.load_session(session["id"])["state"]
    first = loaded["brief_plan"]["current"]["sections"][0]
    assert first["title"] == "عنوان تازه" and first["status"] == "edited"
    assert loaded["section_contracts"][0]["title"] == "عنوان تازه"
    # Bad status vocabulary is the door's own 400.
    _, bad = research.decide_plan_item(PHONE, session["id"], "k1", "maybe")
    assert bad == (400, research.RESEARCH_PLAN_ITEM_DETAIL)
    # An all-rejected plan refuses the Brief before any writer call.
    fresh = make_session(tmp_path)
    fresh["state"]["claims"] = [
        {"id": "c1", "text": "ادعا", "status": "direct_support"}
    ]
    rejected = [
        {"title": "یکی", "question": "", "claims": [], "key": "k1", "status": "rejected"}
    ]
    fresh["state"]["brief_plan"] = {
        "current": {"sections": rejected},
        "versions": [],
    }
    fresh["state"]["section_contracts"] = research._section_contracts_from_plan(
        fresh["state"], rejected
    )
    turn = research.ResearchTurn(PHONE, fresh["id"], "خلاصه")
    blocks = research._brief(turn, fresh["state"])
    assert blocks == [
        {"type": "note", "text": research.RESEARCH_BRIEF_NO_SECTIONS_DETAIL}
    ]


def test_capped_question_ids_never_reissue_the_same_id():
    # The pm incident's four q13s: length+1 re-mints the same id once
    # the cap trims the list — the ledger's own id mint scans the
    # survivors instead (stage C, spec §4's small-bug list).
    state = research.new_research_state("پرسش؟")
    for i in range(research.RESEARCH_MAX_SUBQUESTIONS + 3):
        research._add_open_question(state, f"پرسش شمارۀ {i}؟")
    ids = [item["id"] for item in state["subquestions"]]
    assert len(ids) == len(set(ids)), ids
    assert len(ids) == research.RESEARCH_MAX_SUBQUESTIONS


def test_a_resolved_command_never_pays_the_classifier(tmp_path):
    # W4 (findings-02, stage C): the deterministic move IS the intent —
    # the chip turn's composer calls carry no intent-reader prompt at
    # all; only the chip's own work (here: planning) runs.
    upstream = ResearchUpstream(
        composer_replies=[composer_reply(json.dumps(["زیرپرسش؟"]))],
        recall_reply=fed_by_query,
    )
    session = make_session(tmp_path)
    turn = run_turn_sync(session, research.COMMAND_GATHER, upstream, tmp_path)
    assert turn.state == "done"
    bodies = [b for b in upstream.bodies if isinstance(b, str)]
    assert bodies, "the chip's own planning call should have run"
    assert all("intent reader" not in body for body in bodies)
