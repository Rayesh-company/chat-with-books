"""The characterization net (ticket T1, GitLab issue #2): today's
behavior of the audit's risk areas, pinned at the agreed seams — the
turn worker and the persistence layer — so the skill-engine flip
(ADR-0012) cannot silently change anything.

The discipline of this file:

- A pin asserts what the engine does TODAY. It passes against the
  current code; a failing pin means the engine moved, and only the
  ticket that owns that move may rewrite it.
- A pin that asserts a KNOWN BUG carries a marker comment naming the
  ticket that will change it (``[T<n> #<issue>]``), and the fixing
  ticket rewrites both the pin and its marker.
- A behavior that is desired but UNBUILT stands as a strict ``xfail``
  with the same pointer; its fixing ticket drops the marker and makes
  it pass — a silent XPASS now fails the suite and forces exactly
  that.

Ticket numbers are GitLab issues on gitlab.rayesh-team.ir
(mohamadreza/chatbot-v1): T1=#2 … T13=#14, under spec #1.

The fakes live in tests/upstream_fakes.py — the harness's canonical
home, outside every test module (tests/helpers.py's rule)."""

import json
import os
import threading

from tests.conftest import REPO_ROOT

import sys  # noqa: E402

sys.path.insert(0, str(REPO_ROOT))

from tests.upstream_fakes import (  # noqa: E402
    PHONE,
    SENTENCE,
    ResearchUpstream,
    classify_reply,
    cognee_payload,
    composer_bodies,
    composer_reply,
    conversational_writer_reply,
    guarded_blocks,
    make_session,
    parked_proposal_state,
    recall_call_count,
    run_turn_sync,
    with_patched_upstream,
)
from ui import research, research_store  # noqa: E402


# --- the conversation layer's silent degrade (audit 4) -----------------------


def test_a_classify_upstream_failure_falls_back_conversational_and_silently(
    tmp_path,
):
    # Fulfilled by T2 #3: the fallback stays conversational — one
    # searcher, one guarded writer, no crash, no research formalism
    # moved — and the failure is now RECORDED as a router diagnosis
    # (the router contract owns the deeper assertions; see
    # test_skill_router.py). This pin keeps the fallback itself honest.
    upstream = ResearchUpstream(
        composer_replies=[
            OSError("classify downstream dead"),
            conversational_writer_reply(),
        ]
    )
    session = make_session(tmp_path)
    turn = run_turn_sync(session, "یک پرسش ساده", upstream, tmp_path)
    assert turn.state == "done"
    assert len(composer_bodies(upstream)) == 2
    assert recall_call_count(upstream) == 1
    state = session["state"]
    assert state["subquestions"] == []
    assert state["pending_proposals"] == []
    assert state["decisions"] == []
    assert [d["kind"] for d in state["diagnoses"]] == ["router"]
    # The event timeline carries only the lifecycle's own marks — the
    # diagnosis lives in the state, not the turn's event list.
    assert turn.events == [
        research.RESEARCH_EVENT_CLASSIFYING,
        research.RESEARCH_EVENT_SEARCHING,
        research.RESEARCH_EVENT_WRITING,
        research.RESEARCH_EVENT_DONE,
    ]


def test_a_dead_narrator_silences_only_the_note(tmp_path):
    # Audit 6: the narrator has zero retries — any upstream failure
    # (here: no reply left in the queue at all) must cost nothing but
    # the note. The attempt itself still happens; the operation's own
    # reply stands, its fact note first.
    state = research.new_research_state("پرسش پژوهش؟")
    state["subquestions"] = [
        {"id": "q1", "name": "یکی", "text": "یکی؟", "status": "pending"}
    ]
    upstream = ResearchUpstream(composer_replies=[classify_reply("active_research")])
    session = make_session(tmp_path, state=state)
    turn = run_turn_sync(session, research.COMMAND_GATHER, upstream, tmp_path)
    assert turn.state == "done"
    calls = composer_bodies(upstream)
    assert len(calls) == 2
    assert "You are the narrator" in calls[1]["messages"][0]["content"]
    reply = turn.result["reply"]
    assert reply[0].get("type") == "note"
    assert "نقل‌قول تازه" in reply[0]["text"]


# --- the classifier's authority over chips and cooldowns (audit 10) ----------


def test_a_misrouted_option_chip_keeps_the_question_alive_silently(tmp_path):
    # [T2 #3: chips resolve deterministically] The option chip is free
    # text to the classifier; classified conversational, the guided
    # answer never folds — no decision, no destination, and the same
    # question still live for a later ask.
    state = research.new_research_state("پرسش پژوهش؟")
    state["grilling"] = {
        "asked_in_stage": 1,
        "current_question": "مقصد چیست؟",
        "options": ["گزینهٔ الف"],
    }
    upstream = ResearchUpstream(
        composer_replies=[
            classify_reply("casual_question"),
            conversational_writer_reply(),
        ]
    )
    session = make_session(tmp_path, state=state)
    turn = run_turn_sync(session, "گزینهٔ الف", upstream, tmp_path)
    assert turn.state == "done"
    state = session["state"]
    assert state["decisions"] == []
    assert not state["map"].get("destination")
    assert state["grilling"]["current_question"] == "مقصد چیست؟"
    assert state["grilling"]["asked_in_stage"] == 1


def test_a_conversational_turn_does_not_tick_the_cooldown(tmp_path):
    # [T2 #3: the cooldown ticks on every turn] The decision cooldown
    # decrements only in the research-side branch — a chat, an audit,
    # or a drafting turn leaves it frozen.
    state = research.new_research_state("پرسش پژوهش؟")
    state["proposal_cooldowns"] = {"research_question": 1, "scope": 0}
    upstream = ResearchUpstream(
        composer_replies=[
            classify_reply("casual_question"),
            conversational_writer_reply(),
        ]
    )
    session = make_session(tmp_path, state=state)
    turn = run_turn_sync(session, "یک پرسش ساده", upstream, tmp_path)
    assert turn.state == "done"
    assert session["state"]["proposal_cooldowns"] == {
        "research_question": 1,
        "scope": 0,
    }


def test_the_landscape_flag_lands_before_its_own_survey(tmp_path):
    # [T2 #3: the flag waits for a successful survey] The mapping turn
    # sets landscape_done BEFORE the search and the writer — a writer
    # that keeps nothing has still burned the stage's one survey.
    state = research.new_research_state("پرسش پژوهش؟")
    state["stage"] = "mapping"
    upstream = ResearchUpstream(
        composer_replies=[
            classify_reply("research_exploration"),
            composer_reply("prose, no blocks"),
            composer_reply("still no blocks"),
        ]
    )
    session = make_session(tmp_path, state=state)
    turn = run_turn_sync(session, "زمین را نقشه‌برداری کن", upstream, tmp_path)
    assert turn.state == "done"
    assert session["state"]["map"]["landscape_done"] is True
    assert any(b.get("type") == "note" for b in turn.result["reply"])


def test_an_empty_pool_burns_the_landscape_turn_and_advances(tmp_path):
    # [T2 #3: same owner] The audit's exact claim, the search-failure
    # variant: the Books give the survey nothing, the flag is burned
    # anyway, and the journey leaves the mapping stage on the skip
    # note — one dead searcher, no second chance.
    state = research.new_research_state("پرسش پژوهش؟")
    state["stage"] = "mapping"
    upstream = ResearchUpstream(
        composer_replies=[classify_reply("research_exploration")],
        recall_reply=lambda payload: cognee_payload("پاسخ بدون شواهد."),
    )
    session = make_session(tmp_path, state=state)
    turn = run_turn_sync(session, "زمین را نقشه‌برداری کن", upstream, tmp_path)
    assert turn.state == "done"
    assert session["state"]["map"]["landscape_done"] is True
    assert session["state"]["stage"] == "investigating"


# --- the unbounded ledger (audit 2) ------------------------------------------


def test_the_brief_prompt_carries_the_whole_ledger(tmp_path):
    # [T12 #13: the state caps] Each Brief section's writer prompt
    # embeds the ENTIRE evidence ledger — today nothing bounds what a
    # long session ships to the composer, per section now as before.
    second_passage = "این جمله از قطعهٔ دیگری است."
    state = research.new_research_state("پرسش پژوهش؟")
    research.seed_evidence(
        state,
        [
            {"reference": "chunk 1 of document tarhe-kolli", "passage": SENTENCE},
            {"reference": "chunk 2 of document tarhe-kolli", "passage": second_passage},
        ],
        "پرسش پژوهش؟",
    )
    state["claims"] = [
        {"id": "c1", "text": "ادعا", "status": "direct_support", "evidence_ids": ["e1"]}
    ]
    sections = [{"title": "بخش یکم", "question": "", "claims": ["c1"]}]
    state["brief_plan"] = {
        "current": {"sections": sections},
        "versions": [{"sections": sections, "turn": 1}],
    }
    pool = [
        {"reference": "chunk 1 of document tarhe-kolli", "passage": SENTENCE},
        {"reference": "chunk 2 of document tarhe-kolli", "passage": second_passage},
    ]
    upstream = ResearchUpstream(
        composer_replies=[
            classify_reply("drafting"),
            composer_reply(json.dumps(guarded_blocks(pool), ensure_ascii=False)),
            composer_reply("روایت کوتاه."),
            # The Closing review's judgment (T9).
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
    writer_prompt = composer_bodies(upstream)[1]["messages"][0]["content"]
    assert SENTENCE in writer_prompt
    assert second_passage in writer_prompt


# --- the all-starved stall (audit 5) -----------------------------------------


def test_the_all_starved_stall_offers_the_escape():
    # [T6 #7: flipped — the escape landed] Every question starved,
    # evidence below the floor: the ladder used to offer gather and
    # nothing else, the Brief unreachable however many turns the
    # operator spent. The stall escape now joins synthesize-with-what-
    # exists and the honest stop to the gather, and the wayfinder
    # itself moves to the synthesis of what exists.
    state = research.new_research_state("پرسش پژوهش؟")
    state["stage"] = "investigating"
    state["subquestions"] = [
        {"id": "q1", "name": "یکی", "text": "یکی؟", "status": "gap"}
    ]
    state["evidence"] = [
        {"id": "e1", "reference": "r", "passage": SENTENCE}
    ]
    assert research.next_best_move(state) == "synthesize"
    assert [chip["id"] for chip in research.research_suggestions(state)] == [
        "gather",
        "synthesize",
        "stop",
    ]


# --- the decide-vs-worker race (audit 3) -------------------------------------


def test_a_decision_survives_a_concurrent_worker_save(tmp_path):
    # [T11 #12: fulfilled — the store's stale-write merge] The worker's
    # settle save is version-checked: written from an older snapshot
    # than the row, the decisions recorded since its base are folded
    # back in — the accepted decision is kept, the proposal stays gone.
    research_store.RESEARCH_DB = tmp_path / "research.sqlite3"
    session = make_session(tmp_path)
    state = parked_proposal_state()
    session["state"] = state
    research_store.save_session(session["id"], state)
    worker_snapshot = research_store.load_session(session["id"])["state"]
    proposal_id = worker_snapshot["pending_proposals"][0]["id"]
    result, error = research.decide_proposal(
        PHONE, session["id"], proposal_id, True
    )
    assert error is None
    research_store.save_session(session["id"], worker_snapshot)
    loaded = research_store.load_session(session["id"])
    assert loaded["state"]["research_question"]["current"] == "پرسش دقیق‌تر؟"
    assert loaded["state"]["pending_proposals"] == []


def test_a_stale_settle_save_keeps_the_turns_work_and_the_decision(tmp_path):
    # [T11 #12: fulfilled — both sides of the race] The fold replays the
    # decision ONTO the worker's snapshot, so the merge keeps the turn's
    # own work (its pool, absent from every decide write) as well.
    research_store.RESEARCH_DB = tmp_path / "research.sqlite3"
    session = make_session(tmp_path)
    state = parked_proposal_state()
    session["state"] = state
    research_store.save_session(session["id"], state)
    worker_snapshot = research_store.load_session(session["id"])["state"]
    worker_snapshot["evidence"] = [
        {"id": "e1", "reference": "r", "passage": SENTENCE}
    ]
    proposal_id = worker_snapshot["pending_proposals"][0]["id"]
    result, error = research.decide_proposal(
        PHONE, session["id"], proposal_id, True
    )
    assert error is None
    research_store.save_session(session["id"], worker_snapshot)
    loaded = research_store.load_session(session["id"])
    assert [item["id"] for item in loaded["state"]["evidence"]] == ["e1"]
    assert loaded["state"]["research_question"]["current"] == "پرسش دقیق‌تر؟"
    assert loaded["state"]["pending_proposals"] == []


def test_a_decision_after_the_settle_loads_the_turns_work(tmp_path):
    # [T11 #12: the other order, serialized by the same write lock] The
    # settle save lands first; the decide's read-modify-write then loads
    # FRESH — the turn's work in hand — and applies on top. Nothing is
    # lost either way.
    research_store.RESEARCH_DB = tmp_path / "research.sqlite3"
    session = make_session(tmp_path)
    state = parked_proposal_state()
    session["state"] = state
    research_store.save_session(session["id"], state)
    worker_snapshot = research_store.load_session(session["id"])["state"]
    worker_snapshot["evidence"] = [
        {"id": "e1", "reference": "r", "passage": SENTENCE}
    ]
    research_store.save_session(session["id"], worker_snapshot)
    proposal_id = worker_snapshot["pending_proposals"][0]["id"]
    result, error = research.decide_proposal(
        PHONE, session["id"], proposal_id, True
    )
    assert error is None
    loaded = research_store.load_session(session["id"])
    assert [item["id"] for item in loaded["state"]["evidence"]] == ["e1"]
    assert loaded["state"]["research_question"]["current"] == "پرسش دقیق‌تر؟"
    assert loaded["state"]["pending_proposals"] == []


def test_a_decision_during_an_in_flight_turn_survives_the_settle(tmp_path):
    # [T11 #12: the race, end to end] The gather blocks mid-search; the
    # operator accepts the parked proposal while the turn runs; the
    # settle save is stale and the store folds the decision into it —
    # the pool AND the accepted question land in one record.
    research_store.RESEARCH_DB = tmp_path / "research.sqlite3"
    state = parked_proposal_state()
    session = make_session(tmp_path, state=state)
    research_store.save_session(session["id"], state)
    session = research_store.load_session(session["id"])
    proposal_id = state["pending_proposals"][0]["id"]

    searched = threading.Event()
    release = threading.Event()

    def gated_recall(payload):
        searched.set()
        assert release.wait(timeout=30)
        return cognee_payload(
            "پاسخ.\n\nEvidence:\n"
            f"- chunk 1 of document tarhe-kolli (pages 10-12): \"{SENTENCE}\""
        )

    upstream = ResearchUpstream(
        composer_replies=[
            classify_reply("active_research", subquestions=["یکی؟"]),
            composer_reply(""),
        ],
        recall_reply=gated_recall,
    )
    turn = research.ResearchTurn(PHONE, session["id"], research.COMMAND_GATHER)
    research.RESEARCH_REGISTRY[turn.id] = turn

    def run():
        with_patched_upstream(upstream, tmp_path)(
            lambda: research.run_research_turn(turn, session)
        )

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    try:
        assert searched.wait(timeout=30)
        result, error = research.decide_proposal(
            PHONE, session["id"], proposal_id, True
        )
        assert error is None
        release.set()
        worker.join(timeout=30)
        assert not worker.is_alive()
    finally:
        release.set()
        research.RESEARCH_REGISTRY.pop(turn.id, None)
    assert turn.state == "done"
    loaded = research_store.load_session(session["id"])
    assert loaded["state"]["evidence"]
    assert loaded["state"]["research_question"]["current"] == "پرسش دقیق‌تر؟"
    assert loaded["state"]["pending_proposals"] == []
    assert any(
        "پرسش پژوهش به‌روز شد" in item["text"]
        for item in loaded["state"]["decisions"]
    )
    messages, _ = research.research_session_messages(PHONE, session["id"])
    roles = [message["role"] for message in messages["messages"]]
    assert roles.count("assistant") >= 2  # the decision note and the reply


# --- the registry's growth (audit 7) -----------------------------------------


def test_a_settled_turn_is_reaped_but_stays_answerable(tmp_path):
    # [T11 #12: fulfilled — the YAGNI limit lifted] The registry holds
    # live turns only: a settled turn is reaped the moment its worker
    # exits. Its outcome stays answerable — the recent-settled ring
    # serves the poll's delivery window, and the store holds the reply.
    upstream = ResearchUpstream(composer_replies=[classify_reply("evidence_audit")])
    session = make_session(tmp_path)
    turn = run_turn_sync(session, research.COMMAND_AUDIT, upstream, tmp_path)
    assert turn.state == "done"
    assert turn.id not in research.RESEARCH_REGISTRY
    found = research.find_turn(turn.id)
    assert found is turn
    payload = research.turn_status_payload(found)
    assert payload["state"] == "done"
    assert payload["reply"]
    stored, _ = research.research_session_messages(PHONE, session["id"])
    assert any(
        message["role"] == "assistant" for message in stored["messages"]
    )


def test_terminal_turns_are_reaped(tmp_path):
    # [T11 #12: fulfilled] After the worker exits, no terminal turn
    # remains in the registry — the live table and the ring are the
    # whole story.
    upstream = ResearchUpstream(composer_replies=[classify_reply("evidence_audit")])
    session = make_session(tmp_path)
    turn = run_turn_sync(session, research.COMMAND_AUDIT, upstream, tmp_path)
    assert turn.state == "done"
    terminal = [
        t
        for t in research.RESEARCH_REGISTRY.values()
        if t.state in research.TURN_TERMINAL_STATES
    ]
    assert terminal == []


def test_a_turn_beyond_the_recent_ring_answers_unknown(tmp_path):
    # [T11 #12: the bound] The ring caps how long a settled outcome
    # stays answerable — a turn id beyond it is unknown, the recorded
    # 404 surface, the store being the record. Growth is bounded by the
    # ring, never by a restart cycle.
    class StubTurn:
        pass

    try:
        with research.RESEARCH_REGISTRY_LOCK:
            research.RESEARCH_RECENT_SETTLED.clear()
        for index in range(research.RESEARCH_RECENT_SETTLED_CAP + 1):
            stub = StubTurn()
            stub.id = f"t{index}"
            with research.RESEARCH_REGISTRY_LOCK:
                research.RESEARCH_RECENT_SETTLED.append(stub)
        assert research.find_turn("t0") is None
        assert research.find_turn("t1") is not None
    finally:
        with research.RESEARCH_REGISTRY_LOCK:
            research.RESEARCH_RECENT_SETTLED.clear()


# --- the orphaned user message (audit 8) -------------------------------------


def orphaned_message_case(tmp_path):
    """The busy path, arranged once for the pin."""
    research_store.RESEARCH_DB = tmp_path / "research.sqlite3"
    session = make_session(tmp_path)
    blocker = research.ResearchTurn(PHONE, "another-session", "سایه")
    blocker.state = "searching"
    research.RESEARCH_REGISTRY[blocker.id] = blocker
    try:
        loaded, error = research.ensure_session(
            PHONE, session["id"], "پیام تازه", None, []
        )
        assert error is None
        turn, detail = research.start_research_turn(PHONE, loaded, "پیام تازه")
        assert turn is None
        messages, _ = research.research_session_messages(PHONE, session["id"])
        user_texts = [
            m["payload"] for m in messages["messages"] if m["role"] == "user"
        ]
        return user_texts
    finally:
        research.RESEARCH_REGISTRY.pop(blocker.id, None)


def test_a_busy_rejection_admits_nothing_to_the_transcript(tmp_path):
    # [T11 #12: fulfilled — admission after acceptance] The user's
    # message joins the persisted transcript only when the session
    # ACCEPTS the turn: the busy 429 admits nothing, so no orphaned,
    # unanswered bubble can exist.
    assert orphaned_message_case(tmp_path) == []


def test_a_busy_rejection_leaves_the_transcript_clean(tmp_path):
    assert orphaned_message_case(tmp_path).count("پیام تازه") == 0


def test_an_accepted_turn_admits_the_message_exactly_once(tmp_path):
    # The other half of the move: a STARTED turn appends the message
    # itself — the transcript carries it exactly once, and the settled
    # turn is reaped (the registry keeps live turns only).
    research_store.RESEARCH_DB = tmp_path / "research.sqlite3"
    upstream = ResearchUpstream(composer_replies=[classify_reply("evidence_audit")])
    original_urlopen = research.urlopen
    research.urlopen = upstream
    os.environ["LLM_API_KEY"] = "test-key"
    try:
        session, error = research.ensure_session(
            PHONE, None, "پیام تازه", "پرسش پژوهش؟", []
        )
        assert error is None
        turn, detail = research.start_research_turn(PHONE, session, "پیام تازه")
        assert turn is not None
        assert detail is None
        assert turn.done.wait(timeout=30)
    finally:
        research.urlopen = original_urlopen
        os.environ.pop("LLM_API_KEY", None)
    assert turn.state == "done"
    assert turn.id not in research.RESEARCH_REGISTRY
    messages, _ = research.research_session_messages(PHONE, session["id"])
    user_texts = [
        m["payload"] for m in messages["messages"] if m["role"] == "user"
    ]
    assert user_texts == ["پیام تازه"]


def test_a_stale_settle_save_never_uncloses_a_session(tmp_path):
    # [T11 #12: the abort's close outlives the snapshot] A new ask
    # closes the session while a turn is in flight; the worker's stale
    # settle save is refused, so it cannot write the session back open.
    research_store.RESEARCH_DB = tmp_path / "research.sqlite3"
    session = make_session(tmp_path)
    worker_snapshot = research_store.load_session(session["id"])["state"]
    session["state"]["closed"] = True
    research_store.save_session(session["id"], session["state"])
    research_store.save_session(session["id"], worker_snapshot)
    loaded = research_store.load_session(session["id"])
    assert loaded["state"]["closed"] is True


# --- the work-mode lock (audit 9, the DESIGNED behavior — survives) ----------


def test_free_text_reshows_the_checkpoint_but_a_command_executes(tmp_path):
    # The chart-mode rule exactly as ADR-0011 landed it: a free message
    # while a proposal waits re-shows the checkpoint; an explicit
    # command EXECUTES — the gather runs, and the parked proposal
    # survives for the next free turn.
    state = parked_proposal_state()
    state["subquestions"] = [
        {"id": "q1", "name": "یکی", "text": "یکی؟", "status": "pending"}
    ]
    upstream = ResearchUpstream(
        composer_replies=[classify_reply("research_exploration")]
    )
    session = make_session(tmp_path, state=state)
    turn = run_turn_sync(session, "خب، ادامه بدهیم", upstream, tmp_path)
    assert turn.state == "done"
    texts = [b.get("text", "") for b in turn.result["reply"]]
    assert any("پرسش دقیق‌تر" in text for text in texts)
    assert session["state"]["pending_proposals"]

    upstream = ResearchUpstream(
        composer_replies=[classify_reply("active_research")]
    )
    turn = run_turn_sync(
        session, research.COMMAND_GATHER, upstream, tmp_path
    )
    assert turn.state == "done"
    assert len(session["state"]["evidence"]) > 0
    assert session["state"]["pending_proposals"]  # parked, untouched


# --- the turn budget (audit 1's fix) -----------------------------------------


def test_a_turn_respects_an_injected_budget(tmp_path):
    # Fulfilled by T3 #4: the worker takes the budget at its highest
    # point; a deadline of 0 is exhausted before the first bounded step,
    # so the turn settles done with the honest stop note — and makes no
    # upstream call at all.
    session = make_session(tmp_path)
    turn = research.ResearchTurn(PHONE, session["id"], "پیام")
    upstream = ResearchUpstream(composer_replies=[classify_reply()])
    with_patched_upstream(upstream, tmp_path)(
        lambda: research.run_research_turn(turn, session, budget_seconds=0)
    )
    assert turn.state == "done"
    assert upstream.calls == []
    texts = [block.get("text", "") for block in turn.result["reply"]]
    assert texts == [research.RESEARCH_BUDGET_STOP_DETAIL]
