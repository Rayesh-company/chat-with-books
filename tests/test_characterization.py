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

The fakes below are this file's own copies of the contract-lock
harness (tests/helpers.py is the fakes' canonical home; consolidating
these into it is the T2 router ticket's harness refactor, kept out of
this ticket to leave the sibling module untouched)."""

import json
import sys
import threading

import pytest

from tests.conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT))

from ui import dive, research, research_store  # noqa: E402

PHONE = "09120000000"
SENTENCE = "سخن در این است؛"
OTHER_SENTENCE = "این جمله از قطعهٔ دیگری است."

FED_RECALL_TEXT = (
    "پاسخ.\n\nEvidence:\n"
    f"- chunk 1 of document tarhe-kolli (pages 10-12): \"{SENTENCE}\"\n"
    f"- chunk 29 of document tarhe-kolli: \"{OTHER_SENTENCE}\""
)


def cognee_payload(text):
    return json.dumps([{"text": text}]).encode("utf-8")


def composer_reply(content):
    return json.dumps({"choices": [{"message": {"content": content}}]}).encode(
        "utf-8"
    )


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


def guarded_blocks(sources):
    """One quoting paragraph per source — the shape the verbatim guard
    keeps. Each paragraph's text differs, so each records as its own
    claim."""
    blocks = [{"type": "heading", "text": "بخش نخست"}]
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
    """The scripted composer + second-tier Cognee, told apart by URL;
    an entry that is an Exception instance is raised instead of
    answered. Thread-safe — the gather's searchers call in parallel."""

    def __init__(self, composer_replies=(), recall_reply=None):
        self.lock = threading.Lock()
        self.calls = []
        self.bodies = []
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


def composer_bodies(upstream):
    return [
        json.loads(body)
        for url, body in zip(upstream.calls, upstream.bodies)
        if "chat/completions" in url
    ]


def recall_call_count(upstream):
    return len(upstream.calls) - len(composer_bodies(upstream))


def with_patched_upstream(upstream, tmp_path):
    """Patch the engine's and the kernel's urlopens plus the store's DB
    for one direct call; restore after."""

    def run(fn):
        research_store.RESEARCH_DB = tmp_path / "research.sqlite3"
        originals = [(module, module.urlopen) for module in (research, dive)]
        for module, _ in originals:
            module.urlopen = upstream
        import os

        os.environ["LLM_API_KEY"] = "test-key"
        try:
            return fn()
        finally:
            for module, original in originals:
                module.urlopen = original
            del os.environ["LLM_API_KEY"]

    return run


def run_turn_sync(session, message, upstream, tmp_path):
    """One turn's worker, synchronously over a registry turn — the
    orchestration seam without the HTTP layer or the thread."""
    turn = research.ResearchTurn(session["phone"], session["id"], message)
    research.RESEARCH_REGISTRY[turn.id] = turn
    with_patched_upstream(upstream, tmp_path)(
        lambda: research.run_research_turn(turn, session)
    )
    return turn


def make_session(tmp_path, state=None):
    research_store.RESEARCH_DB = tmp_path / "research.sqlite3"
    session, error = research.ensure_session(
        PHONE, None, "پیام آغازین", "پرسش پژوهش؟", []
    )
    assert session is not None, error
    if state:
        session["state"] = state
    return session


def conversational_writer_reply():
    return composer_reply(
        json.dumps(
            guarded_blocks([{"passage": SENTENCE}]), ensure_ascii=False
        )
    )


def parked_proposal_state():
    state = research.new_research_state("پرسش پژوهش؟")
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
    return state


# --- the conversation layer's silent degrade (audit 4) -----------------------


def test_a_classify_upstream_failure_falls_back_conversational_and_silently(
    tmp_path,
):
    # [T2 #3 adds the recorded diagnosis event — this pin's event list
    # is what that ticket rewrites] The classify call dying degrades the
    # turn to the conversational path — one searcher, one guarded
    # writer, no crash, no research formalism moved — and today nothing
    # records WHY.
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
    # The silent part: the event timeline carries only the lifecycle's
    # own marks — no diagnosis, no anomaly.
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
    # [T12 #13: the state caps] The Brief's writer prompt embeds the
    # ENTIRE evidence ledger — today nothing bounds what a long session
    # ships to the composer.
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
    pool = [
        {"reference": "chunk 1 of document tarhe-kolli", "passage": SENTENCE},
        {"reference": "chunk 2 of document tarhe-kolli", "passage": second_passage},
    ]
    upstream = ResearchUpstream(
        composer_replies=[
            classify_reply("drafting"),
            composer_reply(json.dumps(guarded_blocks(pool), ensure_ascii=False)),
        ]
    )
    session = make_session(tmp_path, state=state)
    turn = run_turn_sync(session, research.COMMAND_BRIEF, upstream, tmp_path)
    assert turn.state == "done"
    writer_prompt = composer_bodies(upstream)[1]["messages"][0]["content"]
    assert SENTENCE in writer_prompt
    assert second_passage in writer_prompt


# --- the all-starved stall (audit 5) -----------------------------------------


def test_an_all_starved_session_is_offered_only_gather():
    # [T6 #7: the stall escape] Every question starved, evidence below
    # the floor: the ladder offers gather and nothing else — synthesize
    # and the Brief are unreachable however many turns the operator
    # spends.
    state = research.new_research_state("پرسش پژوهش؟")
    state["stage"] = "investigating"
    state["subquestions"] = [
        {"id": "q1", "name": "یکی", "text": "یکی؟", "status": "gap"}
    ]
    state["evidence"] = [
        {"id": "e1", "reference": "r", "passage": SENTENCE}
    ]
    assert research.next_best_move(state) == "gather"
    assert [chip["id"] for chip in research.research_suggestions(state)] == [
        "gather"
    ]


# --- the decide-vs-worker race (audit 3) -------------------------------------


def test_a_worker_save_clobbers_a_concurrent_decision(tmp_path):
    # [T11 #12: serialization] The worker saves its WHOLE in-memory
    # state at turn end; a decide that landed mid-turn is overwritten
    # by the stale snapshot — the accepted decision is lost and the
    # proposal resurrected.
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
    research_store.save_session(session["id"], worker_snapshot)  # the end-of-turn save
    loaded = research_store.load_session(session["id"])
    # The decision is gone — the question reverted, the proposal back.
    assert loaded["state"]["research_question"]["current"] == "پرسش پژوهش؟"
    assert loaded["state"]["pending_proposals"]


@pytest.mark.xfail(
    reason="serialization arrives with ticket #12 (T11)", strict=True
)
def test_a_decision_survives_a_concurrent_worker_save(tmp_path):
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


# --- the registry's growth (audit 7) -----------------------------------------


def test_a_settled_turn_stays_in_the_registry(tmp_path):
    # [T11 #12: reaping — the recorded YAGNI limit] Terminal turns are
    # never evicted; growth is bounded only by a server restart.
    upstream = ResearchUpstream(composer_replies=[classify_reply("evidence_audit")])
    session = make_session(tmp_path)
    turn = run_turn_sync(session, research.COMMAND_AUDIT, upstream, tmp_path)
    assert turn.state == "done"
    try:
        assert turn.id in research.RESEARCH_REGISTRY
    finally:
        research.RESEARCH_REGISTRY.pop(turn.id, None)


@pytest.mark.xfail(reason="reaping arrives with ticket #12 (T11)", strict=True)
def test_terminal_turns_are_reaped(tmp_path):
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


# --- the orphaned user message (audit 8) -------------------------------------


def orphaned_message_case(tmp_path):
    """The busy path, arranged once for the pin and its xfail twin."""
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


def test_a_busy_rejection_orphans_the_user_message(tmp_path):
    # [T11 #12: admission before append] ensure_session appends the
    # user's message to the transcript BEFORE start_research_turn can
    # reject — a busy 429 leaves the message sitting there forever
    # unanswered.
    assert orphaned_message_case(tmp_path).count("پیام تازه") == 1


@pytest.mark.xfail(
    reason="admission-before-append arrives with ticket #12 (T11)", strict=True
)
def test_a_busy_rejection_leaves_the_transcript_clean(tmp_path):
    assert orphaned_message_case(tmp_path).count("پیام تازه") == 0


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


# --- the desired-but-unbuilt turn budget (audit 1's fix) ---------------------


@pytest.mark.xfail(
    reason="the injected turn budget arrives with ticket #4 (T3)", strict=True
)
def test_a_turn_respects_an_injected_budget(tmp_path):
    session = make_session(tmp_path)
    turn = research.ResearchTurn(PHONE, session["id"], "پیام")
    with_patched_upstream(ResearchUpstream(), tmp_path)(
        lambda: research.run_research_turn(turn, session, budget_seconds=0.01)
    )
    assert turn.state == "done"
