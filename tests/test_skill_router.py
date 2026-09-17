"""The skill router's contract locks (ticket T2, GitLab issue #3, the
ADR-0012 spine): the SKILL_TABLE is data — one row per Research skill,
each declaring its purpose, kind, allowed stages, caps, Tools, state
reads/writes, and guard applicability — the router prompt is GENERATED
from that table, and a malformed or failed router pick is RECORDED as
a diagnosis with its explicit fallback, never a silent degrade to
chat.

These tests rode the red → green loop of ticket #3 and stay the
router's contract afterward: adding a skill row touches nothing but
the table."""

from tests.conftest import REPO_ROOT

import sys  # noqa: E402

sys.path.insert(0, str(REPO_ROOT))

from tests.upstream_fakes import (  # noqa: E402
    conversational_writer_reply,
    make_session,
    run_turn_sync,
    composer_reply,
    recall_call_count,
    classify_reply,
    ResearchUpstream,
)
from ui import research  # noqa: E402

ROW_FIELDS = (
    "name",
    "purpose",
    "kind",
    "runner",
    "allowed_stages",
    "caps",
    "tools",
    "state_reads",
    "state_writes",
    "guarded",
)


def test_the_skill_table_declares_a_complete_row_per_skill():
    # The dispatch table is data: every Research skill declares its
    # contract, and the table covers exactly the router's intents.
    assert len(research.SKILL_TABLE) == len(research.RESEARCH_INTENTS)
    assert {row["name"] for row in research.SKILL_TABLE} == set(
        research.RESEARCH_INTENTS
    )
    for row in research.SKILL_TABLE:
        for field in ROW_FIELDS:
            assert field in row, f"{row.get('name')} misses {field}"
        assert row["kind"] in ("chat", "chart", "work")
        # A row without a runner would pass the lock and KeyErrors at
        # dispatch time — the runner is pinned here.
        assert row["runner"] in research.SKILL_RUNNERS
        assert isinstance(row["tools"], tuple)
        assert isinstance(row["state_writes"], tuple)


def test_the_router_prompt_is_generated_from_the_table():
    # Adding a row touches nothing else: the prompt's intent block is
    # the table, printed.
    state = research.new_research_state("پرسش پژوهش؟")
    prompt = research.build_classify_prompt("پیام", state, "")
    for row in research.SKILL_TABLE:
        assert row["name"] in prompt
        assert row["purpose"] in prompt
    # The deterministic commands and the reply contract stay pinned.
    assert research.COMMAND_GATHER in prompt
    assert "Reply with ONLY a JSON object" in prompt


def test_validate_skill_pick_accepts_every_row_on_a_fresh_state():
    # The code-side validation seam: today every skill runs in every
    # stage, and the check says so — later tickets tighten rows here,
    # not at the call site.
    state = research.new_research_state("پرسش پژوهش؟")
    for row in research.SKILL_TABLE:
        ok, detail = research.validate_skill_pick(row, state)
        assert ok, detail


def test_a_failed_router_pick_is_recorded_with_its_fallback(tmp_path):
    # The silent degrade is retired: the classify upstream dying still
    # falls back to the conversational skill, but the turn now records
    # the diagnosis — kind, what broke, where it fell back.
    upstream = ResearchUpstream(
        composer_replies=[
            OSError("classify downstream dead"),
            conversational_writer_reply(),
        ]
    )
    session = make_session(tmp_path)
    turn = run_turn_sync(session, "یک پرسش ساده", upstream, tmp_path)
    assert turn.state == "done"
    diagnoses = session["state"]["diagnoses"]
    assert len(diagnoses) == 1
    entry = diagnoses[0]
    assert entry["kind"] == "router"
    assert "upstream" in entry["detail"]
    assert entry["fallback"] == "casual_question"


def test_an_unparseable_router_reply_is_recorded_too(tmp_path):
    # A garbage classify reply is the same diagnosable event as a dead
    # upstream — the pick never silently becomes a chat.
    upstream = ResearchUpstream(
        composer_replies=[
            composer_reply("not json at all"),
            conversational_writer_reply(),
        ]
    )
    session = make_session(tmp_path)
    turn = run_turn_sync(session, "یک پرسش ساده", upstream, tmp_path)
    assert turn.state == "done"
    assert recall_call_count(upstream) == 1
    diagnoses = session["state"]["diagnoses"]
    assert len(diagnoses) == 1
    entry = diagnoses[0]
    assert entry["kind"] == "router"
    assert "unparseable" in entry["detail"]
    assert entry["fallback"] == "casual_question"


def test_a_healthy_router_pick_records_no_diagnosis(tmp_path):
    # The normal path must not grow diagnosis noise: a clean classify
    # leaves the ledger empty.
    upstream = ResearchUpstream(
        composer_replies=[
            classify_reply(),
            conversational_writer_reply(),
        ]
    )
    session = make_session(tmp_path)
    turn = run_turn_sync(session, "یک پرسش ساده", upstream, tmp_path)
    assert turn.state == "done"
    assert session["state"]["diagnoses"] == []
