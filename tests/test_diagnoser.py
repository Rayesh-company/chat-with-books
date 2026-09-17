"""The Diagnoser's contract locks (ticket T6, GitLab issue #7, the
ADR-0012 diagnoser): a fixed failure taxonomy — starved corpus, wrong
Tool, guard drop, question fit — a diagnosed failure recorded in the
state and returned as an adjustment proposal through the existing
decide flow (narrow the question, change the Tool, declare a Gap), and
the stall escape: an evidence-starved session is offered synthesize-
with-what-exists, the honest-gaps Brief, or stop — the Brief stays
reachable on a starved session, and the map's rows tell the truth
about every failed turn."""

from tests.conftest import REPO_ROOT

import json  # noqa: E402
import sys  # noqa: E402

sys.path.insert(0, str(REPO_ROOT))

from tests.upstream_fakes import (  # noqa: E402
    PHONE,
    SENTENCE,
    ResearchUpstream,
    classify_reply,
    cognee_payload,
    composer_reply,
    guarded_blocks,
    make_session,
    run_turn_sync,
)
from ui import research, research_store  # noqa: E402


def starving(payload):
    """The recall that finds nothing quotable — every searcher starves."""
    return cognee_payload("پاسخی بدون بلوک Evidence.")


def unguarded_writer():
    """A writer reply the verbatim guard keeps nothing of — no quote,
    no source index — twice, the pass and its one retry."""
    return composer_reply('{"blocks": [{"type": "paragraph", "parts": '
                         '[{"text": "بدون هیچ نقل‌قولی"}]}]}')


def guarded_writer():
    """One guarded writer pass over the single seeded passage."""
    return composer_reply(
        json.dumps(
            guarded_blocks([{"reference": "r", "passage": SENTENCE}]),
            ensure_ascii=False,
        )
    )


def adjustment_state(tmp_path):
    """A session with the starved gather's adjustment parked — one gap
    aspect named «یکی», the ledger holding one passage below the floor."""
    state = research.new_research_state("پرسش پژوهش؟")
    state["stage"] = "investigating"
    state["subquestions"] = [
        {"id": "q1", "name": "یکی", "text": "یکی؟", "status": "gap"}
    ]
    state["evidence"] = [{"id": "e1", "reference": "r", "passage": SENTENCE}]
    state["pending_proposals"] = [
        {
            "id": "p1",
            "kind": "adjustment",
            "text": "کتاب‌ها برای قالب کنونی «پرسش پژوهش؟» شواهد کافی ندارند",
            "cause": "question_fit",
            "subquestions": ["یکی"],
            "choices": [dict(c) for c in research.ADJUSTMENT_CHOICES],
        }
    ]
    session = make_session(tmp_path, state=state)
    research_store.save_session(session["id"], state)
    return session, state


# --- the fixed taxonomy -----------------------------------------------------


def test_the_failure_taxonomy_is_fixed():
    # ADR-0012's four causes, each with its plain-Persian name — wrong
    # Tool is the registry's emission (the gather's refused graph hop).
    assert set(research.RESEARCH_FAILURE_CAUSES) == {
        "starved_corpus",
        "wrong_tool",
        "guard_drop",
        "question_fit",
    }
    assert research.RESEARCH_FAILURE_CAUSES["starved_corpus"] == (
        "کتاب‌ها برای این پرسش شواهد کافی ندارند"
    )
    assert research.RESEARCH_FAILURE_CAUSES["wrong_tool"] == (
        "روش جست‌وجو با این پرسش سازگار نبود"
    )
    assert research.RESEARCH_FAILURE_CAUSES["guard_drop"] == (
        "پاسخ نوشته‌شده از پالایۀ نقل‌قول گذر نکرد"
    )
    assert research.RESEARCH_FAILURE_CAUSES["question_fit"] == (
        "قالب کنونی پرسش از کتاب‌ها تغذیه نمی‌شود"
    )


def test_the_diagnosis_ledger_stays_capped():
    # Every working list of the state is bounded; the diagnoses are no
    # exception — newest kept.
    state = research.new_research_state("پرسش پژوهش؟")
    for index in range(research.RESEARCH_MAX_DIAGNOSES + 4):
        research.record_failure(state, "starved_corpus", f"fail {index}")
    assert len(state["diagnoses"]) == research.RESEARCH_MAX_DIAGNOSES
    assert state["diagnoses"][-1]["detail"] == (
        f"fail {research.RESEARCH_MAX_DIAGNOSES + 3}"
    )


# --- the diagnosed gather (AC1: a named cause plus choice chips) ------------


def test_an_empty_gather_names_its_cause_and_offers_the_choices(tmp_path):
    # The Books starve every searcher over an empty ledger: the reply
    # names the cause (starved corpus), the diagnosis is recorded, and
    # the adjustment menu parks — three real choices plus the reject.
    upstream = ResearchUpstream(
        composer_replies=[
            classify_reply("active_research", subquestions=["زیرپرسش؟"])
        ],
        recall_reply=starving,
    )
    session = make_session(tmp_path)
    turn = run_turn_sync(session, research.COMMAND_GATHER, upstream, tmp_path)
    assert turn.state == "done"
    diagnoses = session["state"]["diagnoses"]
    assert len(diagnoses) == 1
    assert diagnoses[0]["kind"] == "failure"
    assert diagnoses[0]["cause"] == "starved_corpus"
    notes = [b["text"] for b in turn.result["reply"] if b["type"] == "note"]
    assert any("تشخیص:" in text for text in notes)
    parked = session["state"]["pending_proposals"]
    assert len(parked) == 1 and parked[0]["kind"] == "adjustment"
    choices = [
        chip for chip in turn.result["suggestions"] if chip["kind"] == "proposal"
    ]
    assert [chip.get("choice") for chip in choices] == [
        "narrow",
        "tool",
        "gap",
        None,
    ]
    assert choices[0]["label"] == research.ADJUSTMENT_NARROW
    assert choices[-1]["label"] == "رد می‌کنم"
    # The map's projection carries the diagnosis for the operator.
    assert turn.result["research_state"]["diagnoses"] == [
        {
            "cause": "starved_corpus",
            "cause_label": research.RESEARCH_FAILURE_CAUSES["starved_corpus"],
            "turn": diagnoses[0]["turn"],
        }
    ]


def test_a_starved_gather_over_an_armed_ledger_names_question_fit(tmp_path):
    # The ledger already holds passages: the Books feed the topic, the
    # current framing starved — the diagnosis narrows to question fit.
    state = research.new_research_state("پرسش پژوهش؟")
    research.seed_evidence(
        state,
        [{"reference": "chunk 1 of document tarhe-kolli", "passage": SENTENCE}],
        "پرسش پژوهش؟",
    )
    upstream = ResearchUpstream(
        composer_replies=[
            classify_reply("active_research", subquestions=["زیرپرسش؟"])
        ],
        recall_reply=starving,
    )
    session = make_session(tmp_path, state=state)
    turn = run_turn_sync(session, research.COMMAND_GATHER, upstream, tmp_path)
    assert turn.state == "done"
    assert session["state"]["diagnoses"][0]["cause"] == "question_fit"
    # A second starved gather never re-parks over the waiting menu.
    assert len(session["state"]["pending_proposals"]) == 1


def test_a_guard_drop_records_its_cause(tmp_path):
    # The writer answered, the guard kept nothing — after the one
    # retry — the honest note lands and the diagnosis names the guard.
    state = research.new_research_state("پرسش پژوهش؟")
    research.seed_evidence(
        state,
        [{"reference": "chunk 1 of document tarhe-kolli", "passage": SENTENCE}],
        "پرسش پژوهش؟",
    )
    upstream = ResearchUpstream(
        composer_replies=[
            classify_reply("active_research"),
            unguarded_writer(),
            unguarded_writer(),
        ]
    )
    session = make_session(tmp_path, state=state)
    turn = run_turn_sync(
        session, research.COMMAND_SYNTHESIZE, upstream, tmp_path
    )
    assert turn.state == "done"
    assert session["state"]["diagnoses"][0]["cause"] == "guard_drop"
    notes = [b["text"] for b in turn.result["reply"] if b["type"] == "note"]
    assert any(research.RESEARCH_EMPTY_REPLY_DETAIL in text for text in notes)


def test_an_empty_recall_records_the_starve_without_parking_a_menu(tmp_path):
    # A conversational turn's empty pool is a recorded starvation too —
    # the chat skill's declared state write — but a side answer never
    # grows research checkpoints.
    upstream = ResearchUpstream(
        composer_replies=[classify_reply()],
        recall_reply=starving,
    )
    session = make_session(tmp_path)
    turn = run_turn_sync(session, "یک پرسش ساده", upstream, tmp_path)
    assert turn.state == "done"
    assert session["state"]["diagnoses"][0]["cause"] == "starved_corpus"
    assert session["state"]["pending_proposals"] == []


def test_a_refused_graph_tool_records_the_wrong_tool_diagnosis(tmp_path):
    # The gather's hop refusing (no picked Book — the registry's rule)
    # is the taxonomy's wrong-tool emission: the diagnosis is recorded
    # and the gather stands on its hybrid pool.
    upstream = ResearchUpstream(
        composer_replies=[
            classify_reply("active_research", subquestions=["زیرپرسش؟"])
        ]
    )
    session = make_session(tmp_path)
    session["state"]["datasets"] = []
    research_store.save_session(session["id"], session["state"])
    turn = run_turn_sync(session, research.COMMAND_GATHER, upstream, tmp_path)
    assert turn.state == "done"
    causes = [entry["cause"] for entry in session["state"]["diagnoses"]]
    assert causes == ["wrong_tool"]
    assert session["state"]["evidence"]


# --- the adjustment choices (the decide flow decides a choice) --------------


def test_the_narrow_choice_reversions_the_question_append_only(tmp_path):
    session, _ = adjustment_state(tmp_path)
    result, error = research.decide_proposal(
        PHONE, session["id"], "p1", True, choice="narrow"
    )
    assert error is None
    loaded = research_store.load_session(session["id"])
    question = loaded["state"]["research_question"]
    # The strongest fed aspect becomes the question; v1 stays intact
    # underneath — append-only provenance like every rewrite.
    assert question["current"] == "یکی؟"
    assert [v["text"] for v in question["versions"]] == ["پرسش پژوهش؟", "یکی؟"]
    assert "محدودسازی پس از تشخیص" in question["versions"][-1]["reason"]
    decisions = [item["text"] for item in loaded["state"]["decisions"]]
    assert any("پرسش پژوهش محدود شد" in text for text in decisions)


def test_the_tool_choice_rearms_the_starved_questions(tmp_path):
    session, _ = adjustment_state(tmp_path)
    result, error = research.decide_proposal(
        PHONE, session["id"], "p1", True, choice="tool"
    )
    assert error is None
    loaded = research_store.load_session(session["id"])
    statuses = {
        item["name"]: item["status"] for item in loaded["state"]["subquestions"]
    }
    # The starved aspect is open again — the re-search the (T4)
    # registry's next Tool will run.
    assert statuses["یکی"] == "pending"
    decisions = [item["text"] for item in loaded["state"]["decisions"]]
    assert any("جست‌وجوی دوباره با روش دیگر" in text for text in decisions)


def test_the_gap_choice_declares_the_honest_gap(tmp_path):
    session, _ = adjustment_state(tmp_path)
    result, error = research.decide_proposal(
        PHONE, session["id"], "p1", True, choice="gap"
    )
    assert error is None
    loaded = research_store.load_session(session["id"])
    statuses = {
        item["name"]: item["status"] for item in loaded["state"]["subquestions"]
    }
    assert statuses["یکی"] == "gap"
    decisions = [item["text"] for item in loaded["state"]["decisions"]]
    assert any("شکاف پژوهش اعلام شد" in text for text in decisions)
    # The frontier is clear: the wayfinder moves to what exists.
    assert research.next_best_move(loaded["state"]) == "synthesize"


def test_the_adjustment_reject_records_the_refusal(tmp_path):
    session, _ = adjustment_state(tmp_path)
    result, error = research.decide_proposal(
        PHONE, session["id"], "p1", False
    )
    assert error is None
    loaded = research_store.load_session(session["id"])
    assert loaded["state"]["pending_proposals"] == []
    decisions = [item["text"] for item in loaded["state"]["decisions"]]
    assert any("پیشنهاد رد شد" in text for text in decisions)


def test_an_adjustment_accept_without_a_choice_is_not_a_decision(tmp_path):
    session, _ = adjustment_state(tmp_path)
    result, error = research.decide_proposal(
        PHONE, session["id"], "p1", True
    )
    assert result is None and error[0] == 400
    result, error = research.decide_proposal(
        PHONE, session["id"], "p1", True, choice="teleport"
    )
    assert result is None and error[0] == 400
    # The proposal still waits — nothing was decided, nothing applied.
    loaded = research_store.load_session(session["id"])
    assert loaded["state"]["pending_proposals"][0]["id"] == "p1"
    assert loaded["state"]["research_question"]["current"] == "پرسش پژوهش؟"


# --- the stall escape (AC2: a starved session reaches the Brief) ------------


def test_a_starved_session_reaches_the_brief_through_the_escape(tmp_path):
    # The whole escape, end to end at the worker seam: the starved
    # gather diagnoses and offers the menu, the gap choice clears the
    # frontier, the synthesis writes what exists, and the Brief lands.
    state = research.new_research_state("پرسش پژوهش؟")
    research.seed_evidence(
        state,
        [{"reference": "chunk 1 of document tarhe-kolli", "passage": SENTENCE}],
        "پرسش پژوهش؟",
    )
    upstream = ResearchUpstream(
        composer_replies=[
            classify_reply("active_research", subquestions=["زیرپرسش؟"])
        ],
        recall_reply=starving,
    )
    session = make_session(tmp_path, state=state)
    turn = run_turn_sync(session, research.COMMAND_GATHER, upstream, tmp_path)
    assert turn.state == "done"
    proposal_id = session["state"]["pending_proposals"][0]["id"]

    result, error = research.decide_proposal(
        PHONE, session["id"], proposal_id, True, choice="gap"
    )
    assert error is None
    session = research_store.load_session(session["id"])
    assert research.next_best_move(session["state"]) == "synthesize"

    upstream = ResearchUpstream(
        composer_replies=[
            classify_reply("active_research"),
            guarded_writer(),
        ]
    )
    turn = run_turn_sync(
        session, research.COMMAND_SYNTHESIZE, upstream, tmp_path
    )
    assert turn.state == "done"
    assert len(session["state"]["claims"]) == 1

    # The accepted one-section plan (T8): the Brief writes its sections
    # against the contracts, and the escape still ends at a written
    # Brief.
    state = session["state"]
    sections = [{"title": "بخش یکم", "question": "", "claims": ["c1"]}]
    state["brief_plan"] = {
        "current": {"sections": sections},
        "versions": [{"sections": sections, "turn": state["turns"]}],
    }
    state["section_contracts"] = research._section_contracts_from_plan(
        state, sections
    )
    upstream = ResearchUpstream(
        composer_replies=[
            classify_reply("drafting"),
            guarded_writer(),
            composer_reply("روایت کوتاه."),
        ]
    )
    turn = run_turn_sync(session, research.COMMAND_BRIEF, upstream, tmp_path)
    assert turn.state == "done"
    paragraphs = [
        b for b in turn.result["reply"] if b["type"] == "paragraph"
    ]
    assert paragraphs
    assert session["state"]["phase"] == "drafting"


def test_the_stalled_session_is_offered_synthesize_and_stop(tmp_path):
    # The escape chips ride the suggestion set: a drained frontier
    # below the floor shows the gather still, plus synthesize-with-
    # what-exists and the honest stop.
    state = research.new_research_state("پرسش پژوهش؟")
    state["stage"] = "investigating"
    state["subquestions"] = [
        {"id": "q1", "name": "یکی", "text": "یکی؟", "status": "searched"}
    ]
    state["evidence"] = [{"id": "e1", "reference": "r", "passage": SENTENCE}]
    chips = research.research_suggestions(state)
    assert [chip["id"] for chip in chips] == [
        "gather",
        "synthesize",
        "stop",
    ]
    stop_chip = chips[-1]
    assert stop_chip["text"] == research.COMMAND_STOP


def test_the_stop_chip_closes_the_session_honestly(tmp_path):
    state = research.new_research_state("پرسش پژوهش؟")
    state["stage"] = "investigating"
    state["subquestions"] = [
        {"id": "q1", "name": "یکی", "text": "یکی؟", "status": "gap"}
    ]
    state["evidence"] = [{"id": "e1", "reference": "r", "passage": SENTENCE}]
    session = make_session(tmp_path, state=state)
    upstream = ResearchUpstream(composer_replies=[classify_reply()])
    turn = run_turn_sync(session, research.COMMAND_STOP, upstream, tmp_path)
    assert turn.state == "done"
    loaded = research_store.load_session(session["id"])
    assert loaded["state"]["closed"] is True
    decisions = [item["text"] for item in loaded["state"]["decisions"]]
    assert research.RESEARCH_STOP_DECISION in decisions
    notes = [b["text"] for b in turn.result["reply"] if b["type"] == "note"]
    assert any("پژوهش متوقف شد" in text for text in notes)
    # The closed session answers the state read with the honest 409 —
    # nothing resurrects it.
    result, error = research.research_session_state(PHONE, session["id"])
    assert result is None and error[0] == 409
