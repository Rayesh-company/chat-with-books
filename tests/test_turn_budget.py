"""The per-turn budget (ticket T3, GitLab issue #4): a hard wall-clock
deadline plus an upstream-call cap on every research turn, measured
through an injected clock so a test exhausts either instantly — no
waiting on real time.

The contract under test:

- A bounded step is started only when its full known call cost fits the
  budget (`require` pre-pays it), so the recorded cap can never be
  exceeded mid-call — even with slow upstreams.
- The deadline is cooperative like the cancel flag: checked at every
  chain boundary; an in-flight upstream call is never interrupted, so a
  turn overshoots by at most the one call already in flight.
- An over-budget turn stops at that boundary and SAYS SO: the honest
  Farsi note is the reply (after any partial blocks), the timeline
  carries the budget event, and the poll payload reports the budget
  state (calls, cap, exhausted, reason).
- The budget's own exception is never swallowed by an operation's
  upstream-failure guards — the worker is its only catcher.

The fakes live in tests/upstream_fakes.py — the harness's canonical
home, outside every test module (tests/helpers.py's rule)."""

import json

from tests.conftest import REPO_ROOT

import sys  # noqa: E402

sys.path.insert(0, str(REPO_ROOT))

import pytest  # noqa: E402

from tests.helpers import (  # noqa: E402
    account_email_for_phone,
    get,
    post,
    stop_gate,
    wait_turn_done,
    with_gate,
)
from tests.upstream_fakes import (  # noqa: E402
    FakeClock,
    PHONE,
    ResearchUpstream,
    SlowUpstream,
    classify_reply,
    cognee_payload,
    conversational_writer_reply,
    make_session,
    run_turn_sync,
)
from ui import research, serve  # noqa: E402

# The starved searcher's reply: ONE Evidence passage — under
# DIVE_STARVED_PASSAGES, so the dive wants its gap round.
STARVED_RECALL_TEXT = (
    "پاسخ.\n\nEvidence:\n"
    '- chunk 1 of document tarhe-kolli (pages 10-12): "سخن در این است؛"'
)


def starved_recall_reply(payload):
    return cognee_payload(STARVED_RECALL_TEXT)


# --- the budget object itself -------------------------------------------------


def test_the_budget_pre_pays_a_step_and_names_the_refusal_reason():
    clock = FakeClock(0)
    budget = research.TurnBudget(clock=clock, deadline_seconds=600, call_cap=3)
    budget.require(2)
    assert budget.calls == 2
    assert not budget.afford(2)
    assert budget.reason == "call_cap"
    with pytest.raises(research.BudgetExhausted) as raised:
        budget.require(2)
    assert raised.value.reason == "call_cap"


def test_the_budget_deadline_expires_through_the_injected_clock():
    clock = FakeClock(0)
    budget = research.TurnBudget(clock=clock, deadline_seconds=10, call_cap=99)
    assert not budget.expired()
    clock.advance(10)
    assert budget.expired()
    with pytest.raises(research.BudgetExhausted) as raised:
        budget.checkpoint()
    assert raised.value.reason == "deadline"


def test_the_budget_defaults_carry_the_recorded_worker_configuration():
    clock = FakeClock(0)
    budget = research.TurnBudget(clock=clock)
    assert research.RESEARCH_TURN_DEADLINE_SECONDS == 600
    assert budget.call_cap == research.RESEARCH_TURN_CALL_CAP
    assert budget.deadline_at == clock.now + research.RESEARCH_TURN_DEADLINE_SECONDS


# --- the cap: never exceeded, even mid-fan-out -------------------------------


def test_exhausting_the_call_cap_stops_the_turn_with_the_honest_note(tmp_path):
    # A conversational turn costs classify(1) + searcher(1) + writer(1+).
    # A cap of 2 pays the search and refuses the writer: the turn stops
    # at that boundary, says so, and the writer call never fires.
    upstream = ResearchUpstream(
        composer_replies=[classify_reply("casual_question")]
    )
    session = make_session(tmp_path)
    turn = run_turn_sync(session, "پیام ساده", upstream, tmp_path, call_cap=2)
    assert turn.state == "done"
    assert len(upstream.calls) == 2  # classify + searcher; the writer refused
    texts = [block.get("text", "") for block in turn.result["reply"]]
    assert texts == [research.RESEARCH_BUDGET_STOP_DETAIL]
    assert research.RESEARCH_EVENT_BUDGET in turn.events
    payload = research.turn_status_payload(turn)
    assert payload["budget"] == {
        "calls": 2,
        "cap": 2,
        "exhausted": True,
        "reason": "call_cap",
    }
    # The transcript tells the truth: the note is the recorded reply.
    stored, _ = research.research_session_messages(PHONE, session["id"])
    assert research.RESEARCH_BUDGET_STOP_DETAIL in json.dumps(
        stored, ensure_ascii=False
    )


def test_a_gather_never_exceeds_the_cap_and_keeps_its_partial_pool(tmp_path):
    # classify(1) + one 2-searcher round(2) spends a cap of 3 exactly;
    # the starved pool asks for a gap round and the budget refuses it.
    # The round-1 pool stays merged (honest partial work), the reply is
    # the gather's own notes plus the stop note, and no searcher beyond
    # the cap ever fires.
    upstream = ResearchUpstream(
        composer_replies=[
            classify_reply("active_research", subquestions=["یکی؟", "دو؟"])
        ],
        recall_reply=starved_recall_reply,
    )
    session = make_session(tmp_path)
    turn = run_turn_sync(
        session, research.COMMAND_GATHER, upstream, tmp_path, call_cap=3
    )
    assert turn.state == "done"
    composer_calls = len(
        [url for url in upstream.calls if "chat/completions" in url]
    )
    assert composer_calls == 1  # classify only: narrator refused, writer never ran
    recall_calls = len(upstream.calls) - composer_calls
    assert recall_calls == 2  # round 1 exactly; the gap round was refused
    assert session["state"]["evidence"]  # the partial pool is kept
    texts = [block.get("text", "") for block in turn.result["reply"]]
    assert texts[-1] == research.RESEARCH_BUDGET_STOP_DETAIL
    assert any("نقل‌قول" in text for text in texts[:-1])  # the gather's own note
    payload = research.turn_status_payload(turn)
    assert payload["budget"]["reason"] == "call_cap"
    assert payload["budget"]["calls"] == payload["budget"]["cap"] == 3


# --- the deadline: chain-boundary stop with slow upstreams -------------------


def test_the_deadline_stops_at_the_next_chain_boundary_despite_slow_upstreams(
    tmp_path,
):
    # Each upstream call burns 700s of the fake clock against a 600s
    # deadline — no sleeping. The classify call lands, the boundary
    # check refuses everything after it, and the turn says so.
    clock = FakeClock(0)
    slow = SlowUpstream(
        ResearchUpstream(composer_replies=[classify_reply()]), clock, 700
    )
    session = make_session(tmp_path)
    turn = run_turn_sync(
        session, "پیام ساده", slow, tmp_path, budget_seconds=600, clock=clock
    )
    assert turn.state == "done"
    assert len(slow.upstream.calls) == 1  # the classify; the skill never ran
    assert clock.now >= 600  # the clock the budget read — not real time
    texts = [block.get("text", "") for block in turn.result["reply"]]
    assert texts == [research.RESEARCH_BUDGET_STOP_DETAIL]
    payload = research.turn_status_payload(turn)
    assert payload["budget"]["reason"] == "deadline"
    assert payload["budget"]["exhausted"] is True


def test_a_deadline_passing_during_the_last_call_is_still_reported(tmp_path):
    # The edge the honest-stop contract must cover: the wall clock
    # crosses the deadline DURING the final bounded call — every afford
    # check passed beforehand, nothing refuses afterwards. The turn
    # still says so: the stop note closes the reply and the poll reads
    # deadline. (T5, GitLab #6: the Host's chain runs classify, the
    # fresh search, and one graph hop probe before the writer, so the
    # slow seconds now land the crossing inside the writer itself.)
    clock = FakeClock(0)
    slow = SlowUpstream(
        ResearchUpstream(
            composer_replies=[
                classify_reply("casual_question"),
                conversational_writer_reply(),
            ]
        ),
        clock,
        600,
    )
    session = make_session(tmp_path)
    turn = run_turn_sync(
        session, "پیام ساده", slow, tmp_path, budget_seconds=2000, clock=clock
    )
    assert turn.state == "done"
    assert clock.now >= 2000  # the deadline passed mid-call
    reply = turn.result["reply"]
    assert {
        "type": "note",
        "text": research.RESEARCH_BUDGET_STOP_DETAIL,
    } in reply
    # The completed writer's blocks stand beside it — nothing fabricated.
    assert any(block["type"] == "paragraph" for block in reply)
    payload = research.turn_status_payload(turn)
    assert payload["budget"]["exhausted"] is True
    assert payload["budget"]["reason"] == "deadline"


def test_a_zero_deadline_stops_before_any_upstream_call(tmp_path):
    upstream = ResearchUpstream(composer_replies=[classify_reply()])
    session = make_session(tmp_path)
    turn = run_turn_sync(session, "پیام", upstream, tmp_path, budget_seconds=0)
    assert turn.state == "done"
    assert upstream.calls == []
    texts = [block.get("text", "") for block in turn.result["reply"]]
    assert texts == [research.RESEARCH_BUDGET_STOP_DETAIL]


# --- an unbudgeted turn looks exactly like today -----------------------------


def test_a_turn_within_its_budget_reports_the_budget_open(tmp_path):
    upstream = ResearchUpstream(
        composer_replies=[
            classify_reply("casual_question"),
            conversational_writer_reply(),
        ]
    )
    session = make_session(tmp_path)
    turn = run_turn_sync(session, "پیام ساده", upstream, tmp_path)
    assert turn.state == "done"
    payload = research.turn_status_payload(turn)
    assert payload["budget"]["exhausted"] is False
    assert payload["budget"]["reason"] is None
    assert payload["budget"]["cap"] == research.RESEARCH_TURN_CALL_CAP
    assert payload["budget"]["calls"] == len(upstream.calls)


def test_the_sheet_poll_surfaces_the_budget(tmp_path):
    upstream = ResearchUpstream(
        composer_replies=[
            classify_reply("casual_question"),
            conversational_writer_reply(),
        ]
    )
    base, server, original = with_gate(tmp_path, upstream)
    try:
        serve.record_chat(account_email_for_phone(PHONE))
        status, body = post(
            base,
            "/research/message",
            {"text": "پیام ساده", "question": "پرسش؟"},
            phone=PHONE,
        )
        assert status == 202
        wait_turn_done(body["turn_id"])
        poll_status, payload = get(
            base, f"/research/turn?turn={body['turn_id']}", phone=PHONE
        )
    finally:
        stop_gate(server, original)
    assert poll_status == 200
    assert payload["state"] == "done"
    assert payload["budget"]["cap"] == research.RESEARCH_TURN_CALL_CAP
    assert payload["budget"]["exhausted"] is False
