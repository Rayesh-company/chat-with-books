"""The per-section Brief writer's contract locks (ticket T8, GitLab
issue #9, ADR-0012's Brief assembly line): accepting the plan derives
each section's Section contract — the claims it must carry, the
question it answers, the scope lines it must not cross — and the Brief
writes its sections one bounded op each, guarded, with exactly one
retry, then the honest-gap fallback; the assembled Brief matches the
plan the Session operator accepted, the budget stops the chain with
the partial Brief standing, the refuse-without-claims rule stands, and
the plan itself is required now (the strangler flip)."""

import json

from tests.conftest import REPO_ROOT

import sys  # noqa: E402

sys.path.insert(0, str(REPO_ROOT))

from tests.upstream_fakes import (  # noqa: E402
    OTHER_SENTENCE,
    PHONE,
    SENTENCE,
    ResearchUpstream,
    classify_reply,
    composer_bodies,
    composer_reply,
    make_session,
    run_turn_sync,
)
from ui import research, research_store  # noqa: E402

# The accepted plan: each section tied to its named open question and
# the claim ids that will support it.
PLAN_SECTIONS = [
    {"title": "شهود و ساحت", "question": "یکی", "claims": ["c1"]},
    {"title": "جمع‌بندی", "question": "دو", "claims": ["c2"]},
]

FED_CLAIMS = [
    {
        "id": "c1",
        "text": "ادعای یکم",
        "status": "direct_support",
        "evidence_ids": ["e1"],
    },
    {
        "id": "c2",
        "text": "ادعای دوم",
        "status": "supported_synthesis",
        "evidence_ids": ["e2"],
    },
]


def brief_ready_state(sections=None):
    """A session at the Brief's door: two fed claims with their
    evidence, named open questions, scope lines, the accepted plan, and
    the contracts its acceptance derived."""
    state = research.new_research_state("پرسش پژوهش؟")
    research.seed_evidence(
        state,
        [
            {
                "reference": "chunk 1 of document tarhe-kolli",
                "passage": SENTENCE,
            },
            {
                "reference": "chunk 2 of document tarhe-kolli",
                "passage": OTHER_SENTENCE,
            },
        ],
        "پرسش پژوهش؟",
    )
    state["claims"] = [dict(claim) for claim in FED_CLAIMS]
    state["subquestions"] = [
        {"id": "q1", "name": "یکی", "text": "پرسش بازِ یکی؟", "status": "searched"},
        {"id": "q2", "name": "دو", "text": "پرسش بازِ دو؟", "status": "searched"},
    ]
    state["scope"]["in"] = ["دامنۀ داخل"]
    state["scope"]["out"] = ["دامنۀ بیرون"]
    sections = PLAN_SECTIONS if sections is None else sections
    state["brief_plan"] = {
        "current": {"sections": sections},
        "versions": [{"sections": sections, "turn": 1}],
    }
    state["section_contracts"] = research._section_contracts_from_plan(
        state, sections
    )
    return state


def section_reply(index, passage, extra=()):
    """One section's writer reply: a single quoting paragraph — with
    the server-composed heading beside it, the guard's minimum."""
    blocks = [
        {
            "type": "paragraph",
            "parts": [
                {"text": f"نگارشِ این بخش با نقلِ شمارۀ {index + 1}: "},
                {"quote": passage, "source": index},
            ],
        }
    ]
    blocks.extend(extra)
    return composer_reply(json.dumps({"blocks": blocks}, ensure_ascii=False))


NARRATION = composer_reply("نقشۀ پژوهش به خلاصه رسید.")

# The Closing review's judgment reply (T9): the ONE call that closes a
# completed assembly — the review's verdict on the finished document.
JUDGMENT = composer_reply(
    json.dumps(
        {"verdict": "delivers", "reason": "سند مقصد را می‌رساند."},
        ensure_ascii=False,
    )
)


# --- acceptance derives the contracts ---------------------------------------


def test_accepting_a_plan_derives_the_section_contracts(tmp_path):
    state = research.new_research_state("پرسش پژوهش؟")
    state["claims"] = [dict(claim) for claim in FED_CLAIMS]
    state["scope"]["out"] = ["موضوع بیرون از دامنه"]
    research._apply_classify_updates(
        state,
        {
            "intent": "research_exploration",
            "rq_proposal": "",
            "reason": "",
            "concepts": [],
            "subquestions": [],
            "scope_in": [],
            "scope_out": [],
            "brief_plan": PLAN_SECTIONS,
        },
    )
    research_store.RESEARCH_DB = tmp_path / "research.sqlite3"
    session, error = research.ensure_session(
        PHONE, None, "پیام آغازین", "پرسش پژوهش؟", []
    )
    assert error is None
    session["state"] = state
    research_store.save_session(session["id"], state)
    proposal = next(
        p for p in state["pending_proposals"] if p["kind"] == "brief_plan"
    )
    result, decide_error = research.decide_proposal(
        PHONE, session["id"], proposal["id"], True
    )
    assert decide_error is None
    loaded = research_store.load_session(session["id"])["state"]
    assert loaded["section_contracts"] == [
        {
            "title": "شهود و ساحت",
            "key": research.normalize_for_match("شهود و ساحت"),
            "status": "accepted",
            "question": "یکی",
            "claims": ["c1"],
            "scope_in": [],
            "scope_out": ["موضوع بیرون از دامنه"],
        },
        {
            "title": "جمع‌بندی",
            "key": research.normalize_for_match("جمع‌بندی"),
            "status": "accepted",
            "question": "دو",
            "claims": ["c2"],
            "scope_in": [],
            "scope_out": ["موضوع بیرون از دامنه"],
        },
    ]


def test_a_contract_drops_claim_ids_the_ledger_does_not_hold():
    # The plan's author may name a claim id the ledger never recorded —
    # the contract pins only what exists, or no section could ever pass.
    state = research.new_research_state("پرسش پژوهش؟")
    state["claims"] = [dict(FED_CLAIMS[0])]
    contracts = research._section_contracts_from_plan(
        state,
        [
            {"title": "بخش", "question": "یکی", "claims": ["c1", "c9"]},
            {"title": "بخش دیگر", "question": "دو", "claims": ["c8"]},
        ],
    )
    assert contracts[0]["claims"] == ["c1"]
    assert contracts[1]["claims"] == []


def test_the_state_shape_backfills_contracts_for_a_persisted_plan():
    # A session persisted between the plan checkpoint (T7) and the
    # section writer (T8) holds an accepted plan but no contracts — the
    # upgrade derives them, a pure shape fill, never a rewrite.
    state = brief_ready_state()
    derived = state["section_contracts"]
    del state["section_contracts"]
    research.ensure_state_shape(state)
    assert state["section_contracts"] == derived


# --- the section writer ------------------------------------------------------


def test_the_brief_writes_section_by_section_against_the_contracts(tmp_path):
    state = brief_ready_state()
    upstream = ResearchUpstream(
        composer_replies=[
            classify_reply("drafting"),
            section_reply(0, SENTENCE),
            section_reply(1, OTHER_SENTENCE),
            NARRATION,
            JUDGMENT,
        ]
    )
    session = make_session(tmp_path, state=state)
    turn = run_turn_sync(session, research.COMMAND_BRIEF, upstream, tmp_path)
    assert turn.state == "done"
    bodies = composer_bodies(upstream)
    # The classify, ONE bounded writer op per section, the narrator,
    # and the ONE judgment call (T9) — the one-shot Brief call is gone.
    assert len(bodies) == 5
    first = bodies[1]["messages"][0]["content"]
    assert "شهود و ساحت" in first
    # The contract rides the prompt: the assigned question resolved by
    # its map name, the claims by their ledger text, the scope lines.
    assert "پرسش بازِ یکی؟" in first
    assert "ادعای یکم" in first
    assert "دامنۀ بیرون" in first
    second = bodies[2]["messages"][0]["content"]
    assert "جمع‌بندی" in second
    assert "ادعای دوم" in second
    # The assembled Brief matches the accepted plan: its headings in
    # plan order, a guarded paragraph under each.
    reply = turn.result["reply"]
    assert [b["text"] for b in reply if b["type"] == "heading"] == [
        "شهود و ساحت",
        "جمع‌بندی",
    ]
    assert len([b for b in reply if b["type"] == "paragraph"]) == 2
    assert session["state"]["phase"] == "drafting"


def test_the_writer_never_supplies_the_headings(tmp_path):
    # A writer that ignores «no headings» cannot rename the Brief — the
    # plan's own titles are the only headings the assembly keeps.
    state = brief_ready_state()
    upstream = ResearchUpstream(
        composer_replies=[
            classify_reply("drafting"),
            section_reply(
                0,
                SENTENCE,
                extra=[{"type": "heading", "text": "عنوانِ ساختگی"}],
            ),
            section_reply(1, OTHER_SENTENCE),
            NARRATION,
            JUDGMENT,
        ]
    )
    session = make_session(tmp_path, state=state)
    turn = run_turn_sync(session, research.COMMAND_BRIEF, upstream, tmp_path)
    assert turn.state == "done"
    assert [b["text"] for b in turn.result["reply"] if b["type"] == "heading"] == [
        "شهود و ساحت",
        "جمع‌بندی",
    ]


def test_a_contract_miss_retries_once_then_falls_back_honestly(tmp_path):
    # Section one's writer keeps quoting the passage its claim does not
    # rest on — the contract misses, the ONE retry runs the same brief,
    # and the honest gap lands instead of a drifted section. The next
    # section still writes.
    state = brief_ready_state()
    upstream = ResearchUpstream(
        composer_replies=[
            section_reply(1, OTHER_SENTENCE),  # c1 needs e1; e2 quoted
            section_reply(1, OTHER_SENTENCE),  # the one retry misses too
            section_reply(1, OTHER_SENTENCE),  # c2 needs e2 — carried
            NARRATION,
            JUDGMENT,
        ]
    )
    session = make_session(tmp_path, state=state)
    turn = run_turn_sync(session, research.COMMAND_BRIEF, upstream, tmp_path)
    assert turn.state == "done"
    bodies = composer_bodies(upstream)
    # W4 (stage C): the chip's classify call is gone — five composer
    # bodies now, and the retry pair leads them.
    assert len(bodies) == 5
    # Exactly one retry, and it rides the SAME brief.
    assert bodies[0]["messages"][0]["content"] == bodies[1]["messages"][0]["content"]
    reply = turn.result["reply"]
    assert [b["text"] for b in reply if b["type"] == "heading"] == [
        "شهود و ساحت",
        "جمع‌بندی",
    ]
    paragraphs = [b for b in reply if b["type"] == "paragraph"]
    assert len(paragraphs) == 1
    gap_notes = [
        b
        for b in reply
        if b["type"] == "note" and "شواهد کافی ندارند" in b.get("text", "")
    ]
    assert len(gap_notes) == 1
    assert "شهود و ساحت" in gap_notes[0]["text"]
    # The miss is diagnosed, never silent, and the gap is ledgered.
    assert any(
        diagnosis["cause"] == "starved_corpus" and "شهود و ساحت" in diagnosis["detail"]
        for diagnosis in session["state"]["diagnoses"]
    )
    assert any(
        "شهود و ساحت" in gap["text"] for gap in session["state"]["gaps"]
    )


def test_an_unguardable_section_falls_back_after_the_same_one_retry(tmp_path):
    # The guard keeps nothing from either attempt — the same honest-gap
    # fallback, one retry, no loop.
    state = brief_ready_state()
    upstream = ResearchUpstream(
        composer_replies=[
            classify_reply("drafting"),
            composer_reply(
                json.dumps(
                    {
                        "blocks": [
                            {
                                "type": "paragraph",
                                "parts": [{"text": "متنی بی هیچ نقلی"}],
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            ),
            composer_reply("این اصلاً JSON نیست."),
            section_reply(1, OTHER_SENTENCE),
            NARRATION,
            JUDGMENT,
        ]
    )
    session = make_session(tmp_path, state=state)
    turn = run_turn_sync(session, research.COMMAND_BRIEF, upstream, tmp_path)
    assert turn.state == "done"
    assert len(composer_bodies(upstream)) == 6
    reply = turn.result["reply"]
    assert [b["text"] for b in reply if b["type"] == "heading"] == [
        "شهود و ساحت",
        "جمع‌بندی",
    ]
    assert len([b for b in reply if b["type"] == "paragraph"]) == 1
    assert any(
        b["type"] == "note" and "شواهد کافی ندارند" in b.get("text", "")
        for b in reply
    )


# --- the chain budget across the sections -------------------------------------


def test_an_exhausted_budget_stops_the_chain_with_the_partial_brief(tmp_path):
    # The cap fits the classify and section one only: the partial Brief
    # stands and the honest stop note closes the reply — never a
    # fabricated completion of the remaining sections.
    state = brief_ready_state()
    upstream = ResearchUpstream(
        composer_replies=[
            classify_reply("drafting"),
            section_reply(0, SENTENCE),
        ]
    )
    session = make_session(tmp_path, state=state)
    turn = run_turn_sync(session, research.COMMAND_BRIEF, upstream, tmp_path,
                         call_cap=2)
    assert turn.state == "done"
    payload = research.turn_status_payload(turn)
    assert payload["budget"]["reason"] == "call_cap"
    reply = turn.result["reply"]
    assert [b["text"] for b in reply if b["type"] == "heading"] == ["شهود و ساحت"]
    texts = [b.get("text", "") for b in reply]
    assert any(research.RESEARCH_BUDGET_STOP_DETAIL in text for text in texts)


# --- the standing refusals and the flip ---------------------------------------


def test_plans_are_required_now():
    # The strangler flip (T7's recorded switch): the per-section writer
    # landed, so the Brief demands an accepted plan.
    assert research.BRIEF_PLANS_REQUIRED is True


def test_the_brief_refuses_without_an_accepted_plan(tmp_path):
    state = brief_ready_state()
    state["brief_plan"] = {"current": None, "versions": []}
    state["section_contracts"] = []
    upstream = ResearchUpstream(composer_replies=[])
    session = make_session(tmp_path, state=state)
    turn = run_turn_sync(session, research.COMMAND_BRIEF, upstream, tmp_path)
    assert turn.state == "done"
    assert len(composer_bodies(upstream)) == 0
    assert any(
        research.RESEARCH_BRIEF_NEEDS_PLAN_DETAIL in b.get("text", "")
        for b in turn.result["reply"]
    )


def test_the_refuse_without_claims_rule_stands(tmp_path):
    # The plan gate never shadows the older one: no recorded claims, no
    # Brief — even with an accepted plan in hand.
    state = brief_ready_state()
    state["claims"] = []
    upstream = ResearchUpstream(composer_replies=[])
    session = make_session(tmp_path, state=state)
    turn = run_turn_sync(session, research.COMMAND_BRIEF, upstream, tmp_path)
    assert turn.state == "done"
    assert len(composer_bodies(upstream)) == 0
    assert any("ادعای مستندی" in b.get("text", "") for b in turn.result["reply"])


# --- the dispatch table tells the truth ---------------------------------------


def test_the_skill_table_declares_the_section_writer():
    row = research.SKILL_TABLE_BY_NAME["drafting"]
    assert "section_contracts" in row["state_reads"]
    assert "per section" in row["caps"]
