"""The Brief plan checkpoint's contract locks (ticket T7, GitLab issue
#8, ADR-0012's ported back half): the plan — sections mapped to named
open questions and the claims that will support them — is a THIRD
proposal kind in the existing decide flow, with its own cooldown and
append-only acceptance; the work chips stay visible while ANY proposal
waits (the old chip-hiding is retired); and once plans are required,
the Brief refuses to write without an accepted plan.

These tests rode the red → green loop of ticket #8 and stay the
checkpoint's contract afterward. The fakes live in
tests/upstream_fakes.py — the harness's canonical home."""

import json

from tests.conftest import REPO_ROOT

import sys  # noqa: E402

sys.path.insert(0, str(REPO_ROOT))

from tests.upstream_fakes import (  # noqa: E402
    PHONE,
    SENTENCE,
    ResearchUpstream,
    classify_reply,
    composer_bodies,
    composer_reply,
    guarded_blocks,
    make_session,
    run_turn_sync,
)
from ui import research, research_store  # noqa: E402

# A plan the router could author: sections tied to the map's named open
# questions and the claim ledger's ids that will support them.
PLAN_SECTIONS = [
    {"title": "شهود و ساحت", "question": "یکی", "claims": ["c1"]},
    {"title": "جمع‌بندی", "question": "دو", "claims": ["c1", "c2"]},
]


def accepted_sections(sections):
    """Decision 03's landed shape: acceptance stamps every section with
    its stable key and the accepted status — per-item steering's
    address space."""
    return [
        {
            **section,
            "key": research.normalize_for_match(section["title"]),
            "status": "accepted",
        }
        for section in sections
    ]

OTHER_PLAN_SECTIONS = [
    {"title": "مقدمه‌ای دیگر", "question": "یکی", "claims": ["c2"]},
]


def classify_with_plan(intent="research_exploration", sections=None):
    return classify_reply(intent, brief_plan=PLAN_SECTIONS if sections is None else sections)


def park_plan(state, sections=None, intent="research_exploration"):
    """Park a plan through the classify pass's parking seam, the same
    way the router's reply lands."""
    research._apply_classify_updates(
        state,
        {
            "intent": intent,
            "rq_proposal": "",
            "reason": "",
            "concepts": [],
            "subquestions": [],
            "scope_in": [],
            "scope_out": [],
            "brief_plan": PLAN_SECTIONS if sections is None else sections,
        },
    )


# --- the plan is a third proposal kind, parked only on chart turns --------


def test_new_state_seeds_the_plan_ledger_and_its_cooldown():
    state = research.new_research_state("پرسش؟")
    assert state["brief_plan"] == {"current": None, "versions": []}
    assert state["proposal_cooldowns"]["brief_plan"] == 0


def test_ensure_state_shape_upgrades_the_plan_ledger():
    # A session persisted before the plan existed gains the empty
    # ledger — a pure upgrade, never a rewrite.
    state = {
        "research_question": {"current": "پرسش؟", "versions": []},
        "scope": {"in": [], "out": [], "open_decisions": []},
        "subquestions": [],
        "concepts": [],
        "evidence": [],
        "claims": [],
        "gaps": [],
        "decisions": [],
        "phase": "investigating",
        "pending_proposals": [],
        "turns": 3,
        "closed": False,
    }
    research.ensure_state_shape(state)
    assert state["brief_plan"] == {"current": None, "versions": []}


def test_a_plan_parks_only_on_a_chart_mode_turn():
    # The chart-mode gate (ADR-0011) covers the plan like every chart
    # edit: an exploration turn parks it; a working turn never does.
    state = research.new_research_state("پرسش؟")
    park_plan(state)
    parked = [p for p in state["pending_proposals"] if p["kind"] == "brief_plan"]
    assert len(parked) == 1
    assert parked[0]["sections"] == PLAN_SECTIONS
    assert parked[0]["text"]

    state = research.new_research_state("پرسش؟")
    park_plan(state, intent="drafting")
    park_plan(state, intent="active_research")
    assert state["pending_proposals"] == []


def test_a_plan_never_parks_while_one_is_already_pending_or_cooling():
    state = research.new_research_state("پرسش؟")
    park_plan(state)
    park_plan(state)  # one pending plan is the whole queue
    assert len([p for p in state["pending_proposals"] if p["kind"] == "brief_plan"]) == 1

    state = research.new_research_state("پرسش؟")
    state["proposal_cooldowns"]["brief_plan"] = research.PROPOSAL_COOLDOWN_TURNS
    park_plan(state)
    assert state["pending_proposals"] == []


def test_a_decided_plan_never_reproposes_but_a_new_one_can():
    # The containment dedupe (ADR-0011): a decided plan's text narrates
    # inside the decision note, and a contained text can never re-park —
    # while a DIFFERENT plan parks freely once its cooldown has expired.
    state = research.new_research_state("پرسش؟")
    park_plan(state)
    proposal = state["pending_proposals"][0]
    state["pending_proposals"] = []
    state["decisions"].append(
        {"text": f"برنامۀ خلاصۀ پژوهش پذیرفته شد: {proposal['text']}", "turn": 1}
    )
    park_plan(state)  # same sections, cooldown expired
    assert state["pending_proposals"] == []
    park_plan(state, sections=OTHER_PLAN_SECTIONS)
    assert [p["kind"] for p in state["pending_proposals"]] == ["brief_plan"]


def test_the_router_prompt_offers_the_plan_and_its_shape():
    state = research.new_research_state("پرسش؟")
    prompt = research.build_classify_prompt("پیام", state, "")
    assert "brief_plan" in prompt
    assert '"title"' in prompt and '"claims"' in prompt
    parsed = research.parse_classify_strict(
        json.dumps(
            {
                "intent": "research_exploration",
                "brief_plan": [
                    {"title": "بخش", "question": "یکی", "claims": ["c1", 5, "  "]},
                    "junk",
                    {"title": ""},
                ],
            },
            ensure_ascii=False,
        )
    )
    assert parsed["brief_plan"] == [
        {"title": "بخش", "question": "یکی", "claims": ["c1"]}
    ]
    parsed = research.parse_classify_strict('{"intent": "casual_question"}')
    assert parsed["brief_plan"] == []


# --- accept appends, reject records, cooldown applies ------------------------


def decide_plan(tmp_path, accept, state=None):
    """Park a plan in a store-backed session and decide it — the flow's
    own path: park through the classify pass, save, decide."""
    session = make_session(tmp_path, state=state)
    park_plan(session["state"])
    research_store.save_session(session["id"], session["state"])
    proposal = next(
        p for p in session["state"]["pending_proposals"] if p["kind"] == "brief_plan"
    )
    result, error = research.decide_proposal(
        PHONE, session["id"], proposal["id"], accept
    )
    return session, result, error


def test_accepting_a_plan_appends_a_version_and_sets_it_current(tmp_path):
    session, result, error = decide_plan(tmp_path, accept=True)
    assert error is None
    loaded = research_store.load_session(session["id"])
    plan = loaded["state"]["brief_plan"]
    assert plan["current"] == {"sections": accepted_sections(PLAN_SECTIONS)}
    assert [v["sections"] for v in plan["versions"]] == [accepted_sections(PLAN_SECTIONS)]
    # The decision lands in the index and the proposal leaves the queue.
    assert any("پذیرفته شد" in d["text"] for d in loaded["state"]["decisions"])
    assert loaded["state"]["pending_proposals"] == []
    # The plan's own cooldown starts — the damper every kind shares.
    assert (
        loaded["state"]["proposal_cooldowns"]["brief_plan"]
        == research.PROPOSAL_COOLDOWN_TURNS
    )


def test_plan_acceptance_is_append_only(tmp_path):
    session, _, _ = decide_plan(tmp_path, accept=True)
    state = research_store.load_session(session["id"])["state"]
    # A second, different plan parks (the first decision's containment
    # only bars the SAME plan) and is accepted on top — v1 stays.
    state["proposal_cooldowns"]["brief_plan"] = 0
    park_plan(state, sections=OTHER_PLAN_SECTIONS)
    research_store.save_session(session["id"], state)
    proposal = next(
        p
        for p in research_store.load_session(session["id"])["state"][
            "pending_proposals"
        ]
        if p["kind"] == "brief_plan"
    )
    result, error = research.decide_proposal(
        PHONE, session["id"], proposal["id"], True
    )
    assert error is None
    plan = research_store.load_session(session["id"])["state"]["brief_plan"]
    assert [v["sections"] for v in plan["versions"]] == [
        accepted_sections(PLAN_SECTIONS),
        accepted_sections(OTHER_PLAN_SECTIONS),
    ]
    assert plan["current"] == {"sections": accepted_sections(OTHER_PLAN_SECTIONS)}


def test_rejecting_a_plan_records_the_decision_and_its_cooldown(tmp_path):
    session, result, error = decide_plan(tmp_path, accept=False)
    assert error is None
    # The decision reply projects the untouched, empty plan ledger.
    assert result["research_state"]["brief_plan"]["versions"] == 0
    loaded = research_store.load_session(session["id"])
    plan = loaded["state"]["brief_plan"]
    assert plan["current"] is None and plan["versions"] == []
    assert any("پیشنهاد رد شد" in d["text"] for d in loaded["state"]["decisions"])
    assert (
        loaded["state"]["proposal_cooldowns"]["brief_plan"]
        == research.PROPOSAL_COOLDOWN_TURNS
    )


# --- the plan rides the decide flow's turn -----------------------------------


def test_a_plan_turn_is_a_checkpoint_that_still_offers_the_moves(tmp_path):
    upstream = ResearchUpstream(composer_replies=[classify_with_plan()])
    session = make_session(tmp_path)
    turn = run_turn_sync(session, "برنامهٔ خلاصه را بچین", upstream, tmp_path)
    assert turn.state == "done"
    # The current plan ledger did NOT change; the proposal waits.
    assert session["state"]["brief_plan"]["current"] is None
    parked = [
        p for p in session["state"]["pending_proposals"] if p["kind"] == "brief_plan"
    ]
    assert parked and parked[0]["sections"] == PLAN_SECTIONS
    # The checkpoint reply presents the plan, section by section.
    texts = [b.get("text", "") for b in turn.result["reply"]]
    assert any("شهود و ساحت" in text for text in texts)
    assert any("جمع‌بندی" in text for text in texts)
    # And the chips start with the decision pair — the work moves
    # follow (the old chip-hiding is retired).
    kinds = [chip["kind"] for chip in turn.result["suggestions"]]
    assert kinds[0] == "proposal" and kinds[1] == "proposal"
    assert any(kind == "move" for kind in kinds)


def test_work_chips_stay_visible_while_any_proposal_waits():
    def state_with(move_setup):
        state = research.new_research_state("پرسش؟")
        state["stage"] = "investigating"
        move_setup(state)
        state["pending_proposals"] = [
            {"id": "p1", "kind": "brief_plan", "text": "برنامه", "sections": PLAN_SECTIONS}
        ]
        return state

    def below_floor(state):
        state["evidence"] = [{"id": "e1", "reference": "r", "passage": SENTENCE}]

    def fed(state):
        state["evidence"] = [
            {"id": f"e{i}", "reference": "r", "passage": f"{SENTENCE}{i}"}
            for i in range(research.RESEARCH_EVIDENCE_FLOOR)
        ]

    def analyzed(state):
        fed(state)
        state["claims"] = [{"id": "c1", "text": "ادعا", "status": "direct_support"}]

    # Gather while the evidence is short; synthesize when it is fed;
    # audit and brief once claims exist — EVERY case keeps its work
    # chips beside the decision pair.
    for setup, expected in (
        (below_floor, {"gather"}),
        (fed, {"synthesize"}),
        (analyzed, {"audit", "brief"}),
    ):
        chips = research.research_suggestions(state_with(setup))
        kinds = [chip["kind"] for chip in chips]
        assert kinds[:2] == ["proposal", "proposal"]
        move_ids = {chip["id"] for chip in chips if chip["kind"] == "move"}
        assert expected <= move_ids, (expected, move_ids)


def test_the_skip_survives_the_cap_beside_a_waiting_proposal():
    # Two proposal chips plus four option chips would crowd the skip
    # against the six-chip cap — the universal-skip rule wins and the
    # options yield instead.
    state = research.new_research_state("پرسش؟")
    state["grilling"] = {
        "asked_in_stage": 1,
        "current_question": "مقصد چیست؟",
        "options": ["الف", "ب", "پ", "ت"],
    }
    state["pending_proposals"] = [
        {
            "id": "p1",
            "kind": "brief_plan",
            "text": "برنامه",
            "sections": PLAN_SECTIONS,
        }
    ]
    chips = research.research_suggestions(state)
    assert [chip["kind"] for chip in chips] == [
        "proposal",
        "proposal",
        "answer",
        "answer",
        "answer",
        "skip",
    ]
    assert chips[-1]["id"] == "skip"


# --- the summary feeds the router; the table tells the truth -----------------


def test_the_summary_projects_the_plan_and_the_claim_ids():
    state = research.new_research_state("پرسش؟")
    state["claims"] = [{"id": "c1", "text": "ادعا", "status": "direct_support"}]
    summary = research.research_state_summary(state)
    # Claim ids ride the projection — the router authors plans against
    # them.
    assert summary["claims"][0]["id"] == "c1"
    assert summary["brief_plan"] == {"accepted": False, "versions": 0, "sections": []}
    state["brief_plan"] = {
        "current": {"sections": PLAN_SECTIONS},
        "versions": [{"sections": PLAN_SECTIONS, "turn": 1}],
    }
    summary = research.research_state_summary(state)
    assert summary["brief_plan"]["accepted"] is True
    assert summary["brief_plan"]["versions"] == 1
    # Decision 03's projection: per-item shape — the title, the stable
    # key, and the three-state status the plan panel steers by.
    assert summary["brief_plan"]["sections"] == [
        {
            "title": "شهود و ساحت",
            "key": research.normalize_for_match("شهود و ساحت"),
            "status": "accepted",
        },
        {
            "title": "جمع‌بندی",
            "key": research.normalize_for_match("جمع‌بندی"),
            "status": "accepted",
        },
    ]


def test_the_skill_table_declares_the_plan_touches():
    # The dispatch table stays the truth: the exploration skill writes
    # the plan; the drafting skill reads it.
    assert "brief_plan" in research.SKILL_TABLE_BY_NAME["research_exploration"][
        "state_writes"
    ]
    assert "brief_plan" in research.SKILL_TABLE_BY_NAME["drafting"]["state_reads"]


# --- the refuse-without-plan gate --------------------------------------------


def test_by_default_the_brief_demands_a_plan():
    # The strangler switch, flipped by the section writer (T8): the
    # one-shot Brief of the pre-plan days is gone — the Brief demands
    # an accepted plan and writes its sections against the contracts.
    assert research.BRIEF_PLANS_REQUIRED is True


def brief_ready_state():
    state = research.new_research_state("پرسش؟")
    state["evidence"] = [{"id": "e1", "reference": "r", "passage": SENTENCE}]
    state["claims"] = [
        {"id": "c1", "text": "ادعا", "status": "direct_support", "evidence_ids": ["e1"]}
    ]
    return state


def test_the_brief_refuses_without_an_accepted_plan(tmp_path):
    upstream = ResearchUpstream(composer_replies=[])
    session = make_session(tmp_path, state=brief_ready_state())
    turn = run_turn_sync(session, research.COMMAND_BRIEF, upstream, tmp_path)
    assert turn.state == "done"
    # The refusal is honest and cheap (W4 took the chip's classify
    # with it): no composer call at all — no writer ran without its
    # plan.
    assert len(composer_bodies(upstream)) == 0
    texts = [b.get("text", "") for b in turn.result["reply"]]
    assert any(research.RESEARCH_BRIEF_NEEDS_PLAN_DETAIL in text for text in texts)


def test_an_accepted_plan_lets_the_brief_write_per_section(tmp_path):
    state = brief_ready_state()
    state["brief_plan"] = {
        "current": {"sections": PLAN_SECTIONS},
        "versions": [{"sections": PLAN_SECTIONS, "turn": 1}],
    }
    pool = [{"passage": SENTENCE}]
    upstream = ResearchUpstream(
        composer_replies=[
            classify_reply("drafting"),
            composer_reply(json.dumps(guarded_blocks(pool), ensure_ascii=False)),
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
    # Classify, one writer op per planned section, the narrator, the
    # ONE judgment call — the writer ran WITH its plan, section by
    # section.
    assert len(composer_bodies(upstream)) == 5
    assert any(b["type"] == "paragraph" for b in turn.result["reply"])
