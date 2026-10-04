"""The Closing review's contract locks (ticket T9, GitLab issue #10,
ADR-0012): before the Brief is done, the finished document faces two
axes — pure-code traceability (every section traces to its contract's
claims and evidence, nothing crosses the scope lines) and ONE
destination-judgment call — and the result lands with accept/revise
chips through the standing decide flow; accept marks the Brief
reviewed, and the revise command reruns ONLY the failing sections."""

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

# The accepted plan and its fed claims — the same Brief's-door shape the
# section writer's locks (T8) hold.
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

NARRATION = composer_reply("نقشۀ پژوهش به خلاصه رسید.")


def judgment_reply(verdict="delivers", reason="سند پاسخ می‌دهد."):
    """The destination-judgment call's reply: the strict JSON verdict."""
    return composer_reply(
        json.dumps({"verdict": verdict, "reason": reason}, ensure_ascii=False)
    )


def brief_ready_state(scope_out=("دامنۀ بیرون",)):
    """A session at the Brief's door: two fed claims with their
    evidence, named open questions, the scope lines, the accepted plan,
    and the contracts its acceptance derived."""
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
    state["scope"]["out"] = list(scope_out)
    state["map"]["destination"] = "فهم طرح کلی اندیشۀ اسلامی"
    state["brief_plan"] = {
        "current": {"sections": PLAN_SECTIONS},
        "versions": [{"sections": PLAN_SECTIONS, "turn": 1}],
    }
    state["section_contracts"] = research._section_contracts_from_plan(
        state, PLAN_SECTIONS
    )
    return state


def section_reply(index, passage, filler=None, extra=()):
    """One section's writer reply: a single quoting paragraph — with
    the server-composed heading beside it, the guard's minimum. The
    filler text is overridable so a test can plant scope drift in the
    section's own words."""
    blocks = [
        {
            "type": "paragraph",
            "parts": [
                {
                    "text": (
                        filler
                        if filler is not None
                        else f"نگارشِ این بخش با نقلِ شمارۀ {index + 1}: "
                    )
                },
                {"quote": passage, "source": index},
            ],
        }
    ]
    blocks.extend(extra)
    return composer_reply(json.dumps({"blocks": blocks}, ensure_ascii=False))


def clean_brief_turn(upstream, tmp_path):
    """One drafting turn over a fresh brief-ready session; (turn,
    session) back."""
    state = brief_ready_state()
    session = make_session(tmp_path, state=state)
    turn = run_turn_sync(session, research.COMMAND_BRIEF, upstream, tmp_path)
    return turn, session


# --- the dispatch table tells the truth ---------------------------------------


def test_the_skill_table_declares_the_closing_review():
    row = research.SKILL_TABLE_BY_NAME["closing_review"]
    assert row["display_name"] == "بازبین"
    assert row["runner"] == "review"
    assert "one judgment call" in row["caps"]
    assert "brief_document" in row["state_reads"]
    assert "closing_review" in row["state_writes"]
    assert "closing_review" in research.RESEARCH_INTENTS


def test_the_revise_chip_resolves_without_classification():
    assert research.resolve_command(research.COMMAND_REVISE) == ("revise", None)


# --- axis one: the pure-code traceability -------------------------------------


def test_traceability_flags_scope_drift_by_code():
    # The section's own text crosses a contract scope line — the drift
    # is code's finding, before any judgment call exists.
    state = brief_ready_state()
    state["brief_document"] = {
        "complete": True,
        "sections": [
            {
                "title": "شهود و ساحت",
                "paragraphs": [
                    {
                        "type": "paragraph",
                        "parts": [
                            {"text": "و اما دامنۀ بیرون هم گفته می‌شود: "},
                            {"quote": SENTENCE, "source": 0},
                        ],
                    }
                ],
                "gap": False,
            }
        ],
    }
    findings = research.closing_review_findings(state)
    assert findings[0]["title"] == "شهود و ساحت"
    assert findings[0]["status"] == "scope"
    assert findings[0]["detail"] == "دامنۀ بیرون"


def test_traceability_reads_an_honest_gap_as_starvation_not_failure():
    # A section the Books could not feed is written AS a gap (T8's
    # fallback) — the review reads it like any starvation, never a
    # traceability miss.
    state = brief_ready_state()
    state["brief_document"] = {
        "complete": True,
        "sections": [{"title": "شهود و ساحت", "paragraphs": [], "gap": True}],
    }
    findings = research.closing_review_findings(state)
    assert findings[0]["status"] == "gap"


def test_a_verbatim_quote_mentioning_an_out_of_scope_topic_is_not_drift():
    # The scope check reads the section's OWN words — a Book sentence
    # quoted as evidence may mention an out-of-scope topic; the writer's
    # filler may not cross the line.
    state = brief_ready_state()
    state["brief_document"] = {
        "complete": True,
        "sections": [
            {
                "title": "شهود و ساحت",
                "paragraphs": [
                    {
                        "type": "paragraph",
                        "parts": [
                            {"text": "کتاب می‌گوید: "},
                            {"quote": f"این از دامنۀ بیرون است؛ {SENTENCE}", "source": 0},
                        ],
                    }
                ],
                "gap": False,
            }
        ],
    }
    findings = research.closing_review_findings(state)
    assert findings[0]["status"] == "ok"


# --- the drafting turn closes with the review ---------------------------------


def test_the_finished_brief_faces_the_closing_review(tmp_path):
    state = brief_ready_state()
    upstream = ResearchUpstream(
        composer_replies=[
            classify_reply("drafting"),
            section_reply(0, SENTENCE),
            section_reply(1, OTHER_SENTENCE),
            NARRATION,
            judgment_reply("delivers", "سند مقصد را می‌رساند."),
        ]
    )
    turn, session = clean_brief_turn(upstream, tmp_path)
    assert turn.state == "done"
    bodies = composer_bodies(upstream)
    # Classify, ONE bounded op per section, the narrator, and the ONE
    # destination-judgment call — the review's only upstream call.
    assert len(bodies) == 5
    judgment_body = bodies[4]["messages"][0]["content"]
    assert "فهم طرح کلی اندیشۀ اسلامی" in judgment_body
    assert "شهود و ساحت" in judgment_body
    # The verdict lands in the reply, the versioned ledger, and the
    # parked proposal the chips decide.
    reply = turn.result["reply"]
    assert any(
        b["type"] == "note" and "بازبینی پایانی" in b.get("text", "")
        for b in reply
    )
    review = session["state"]["closing_review"]
    assert review["current"]["verdict"] == "delivers"
    assert len(review["versions"]) == 1
    proposal = next(
        p
        for p in session["state"]["pending_proposals"]
        if p["kind"] == "closing_review"
    )
    assert proposal["verdict"] == "delivers"
    assert proposal["failing"] == []
    chips = turn.result["suggestions"]
    accept = next(
        chip
        for chip in chips
        if chip["kind"] == "proposal" and chip["id"] == proposal["id"]
    )
    assert accept["accept"] is True
    # Nothing failed: no revise chip renders.
    assert not any(chip.get("id") == "revise" for chip in chips)


def test_scope_drift_is_flagged_before_the_judgment_runs(tmp_path):
    # Section one carries its claim AND crosses its scope line: code
    # flags the drift first, the findings ride the judgment prompt, and
    # the revise chip renders with the failing section named.
    state = brief_ready_state()
    upstream = ResearchUpstream(
        composer_replies=[
            classify_reply("drafting"),
            section_reply(0, SENTENCE, filler="و اما دامنۀ بیرون هم: "),
            section_reply(1, OTHER_SENTENCE),
            NARRATION,
            judgment_reply("delivers", "سند مقصد را می‌رساند."),
        ]
    )
    turn, session = clean_brief_turn(upstream, tmp_path)
    assert turn.state == "done"
    bodies = composer_bodies(upstream)
    assert len(bodies) == 5
    # The code finding stood BEFORE the judgment call: its label and
    # the crossed line ride the judgment's prompt.
    judgment_body = bodies[4]["messages"][0]["content"]
    assert "از خط دامنه بیرون می‌زند" in judgment_body
    assert "دامنۀ بیرون" in judgment_body
    review = session["state"]["closing_review"]["current"]
    assert [f["status"] for f in review["findings"]] == ["scope", "ok"]
    assert review["failing"] == ["شهود و ساحت"]
    proposal = next(
        p
        for p in session["state"]["pending_proposals"]
        if p["kind"] == "closing_review"
    )
    assert proposal["failing"] == ["شهود و ساحت"]
    revise_chip = next(
        chip
        for chip in turn.result["suggestions"]
        if chip.get("id") == "revise"
    )
    assert revise_chip["text"] == research.COMMAND_REVISE


def test_the_judgment_call_runs_exactly_once(tmp_path):
    # A one-section document: classify + writer + narrator + ONE
    # judgment — never a retry loop on the verdict.
    state = brief_ready_state()
    sections = [{"title": "بخش یکم", "question": "یکی", "claims": ["c1"]}]
    state["brief_plan"] = {
        "current": {"sections": sections},
        "versions": [{"sections": sections, "turn": 1}],
    }
    state["section_contracts"] = research._section_contracts_from_plan(
        state, sections
    )
    upstream = ResearchUpstream(
        composer_replies=[
            classify_reply("drafting"),
            section_reply(0, SENTENCE),
            NARRATION,
            judgment_reply("delivers", "کافی است."),
        ]
    )
    session = make_session(tmp_path, state=state)
    turn = run_turn_sync(session, research.COMMAND_BRIEF, upstream, tmp_path)
    assert turn.state == "done"
    assert len(composer_bodies(upstream)) == 4


def test_a_judgment_that_fails_is_recorded_never_silent(tmp_path):
    # The judgment upstream answers junk: the verdict lands unjudged, a
    # diagnosis names it, and the review still lands — the code
    # findings and the chips stand alone.
    state = brief_ready_state()
    upstream = ResearchUpstream(
        composer_replies=[
            classify_reply("drafting"),
            section_reply(0, SENTENCE),
            section_reply(1, OTHER_SENTENCE),
            NARRATION,
            composer_reply("این اصلاً JSON نیست."),
        ]
    )
    turn, session = clean_brief_turn(upstream, tmp_path)
    assert turn.state == "done"
    review = session["state"]["closing_review"]["current"]
    assert review["verdict"] == "unjudged"
    assert any(
        "judgment" in diagnosis.get("detail", "")
        for diagnosis in session["state"]["diagnoses"]
    )
    assert any(
        b["type"] == "note" and "بازبینی پایانی" in b.get("text", "")
        for b in turn.result["reply"]
    )
    assert any(
        p["kind"] == "closing_review"
        for p in session["state"]["pending_proposals"]
    )


# --- accept marks the Brief reviewed ------------------------------------------


def test_accept_marks_the_brief_reviewed(tmp_path):
    state = brief_ready_state()
    upstream = ResearchUpstream(
        composer_replies=[
            classify_reply("drafting"),
            section_reply(0, SENTENCE),
            section_reply(1, OTHER_SENTENCE),
            NARRATION,
            judgment_reply("delivers", "سند مقصد را می‌رساند."),
        ]
    )
    turn, session = clean_brief_turn(upstream, tmp_path)
    assert turn.state == "done"
    proposal = next(
        p
        for p in session["state"]["pending_proposals"]
        if p["kind"] == "closing_review"
    )
    result, error = research.decide_proposal(
        PHONE, session["id"], proposal["id"], True
    )
    assert error is None
    loaded = research_store.load_session(session["id"])["state"]
    assert loaded["closing_review"]["current"]["status"] == "accepted"
    assert any(
        "بازبینی و پذیرفته شد" in decision.get("text", "")
        for decision in loaded["decisions"]
    )
    summary = research.research_state_summary(loaded)
    assert summary["closing_review"]["reviewed"] is True


# --- revise reruns only the failing sections -----------------------------------


def test_revise_reruns_only_the_failing_sections(tmp_path):
    # Turn one flags section one for scope drift; the revise command
    # reruns ONLY that section (one bounded op), keeps the passing
    # section's paragraphs verbatim, reassembles the whole document,
    # and the review faces it again — the fresh verdict superseding the
    # parked one.
    state = brief_ready_state()
    first = ResearchUpstream(
        composer_replies=[
            classify_reply("drafting"),
            section_reply(0, SENTENCE, filler="و اما دامنۀ بیرون هم: "),
            section_reply(1, OTHER_SENTENCE),
            NARRATION,
            judgment_reply("delivers", "سند مقصد را می‌رساند."),
        ]
    )
    session = make_session(tmp_path, state=state)
    turn = run_turn_sync(session, research.COMMAND_BRIEF, first, tmp_path)
    assert turn.state == "done"
    passing_paragraph = next(
        block
        for block in turn.result["reply"]
        if block.get("type") == "paragraph" and OTHER_SENTENCE in json.dumps(
            block, ensure_ascii=False
        )
    )

    second = ResearchUpstream(
        composer_replies=[
            classify_reply("drafting"),
            section_reply(0, SENTENCE),
            NARRATION,
            judgment_reply("delivers", "این بار سند می‌رساند."),
        ]
    )
    turn = run_turn_sync(session, research.COMMAND_REVISE, second, tmp_path)
    assert turn.state == "done"
    bodies = composer_bodies(second)
    # Classify, the ONE failing section rewritten, the narrator, the
    # fresh judgment — the passing section never rewrites.
    assert len(bodies) == 4
    rewritten_prompt = bodies[1]["messages"][0]["content"]
    assert "شهود و ساحت" in rewritten_prompt
    assert "جمع‌بندی" not in rewritten_prompt
    reply = turn.result["reply"]
    assert [b["text"] for b in reply if b["type"] == "heading"] == [
        "شهود و ساحت",
        "جمع‌بندی",
    ]
    paragraphs = [b for b in reply if b["type"] == "paragraph"]
    assert len(paragraphs) == 2
    # The passing section's block is the very one from turn one.
    assert passing_paragraph in paragraphs
    proposals = [
        p
        for p in session["state"]["pending_proposals"]
        if p["kind"] == "closing_review"
    ]
    assert len(proposals) == 1
    review = session["state"]["closing_review"]
    assert review["current"]["verdict"] == "delivers"
    assert len(review["versions"]) == 2


def test_a_judgment_that_fails_the_destination_flags_the_written_sections(
    tmp_path,
):
    # No section misses its contract, but the judgment says the
    # document does not deliver the destination and does not honestly
    # state the Books' limits: every written section joins the revise
    # set — the failing set is always explicit.
    state = brief_ready_state()
    upstream = ResearchUpstream(
        composer_replies=[
            classify_reply("drafting"),
            section_reply(0, SENTENCE),
            section_reply(1, OTHER_SENTENCE),
            NARRATION,
            judgment_reply("not_yet", "سند به مقصد نمی‌رسد."),
        ]
    )
    turn, session = clean_brief_turn(upstream, tmp_path)
    assert turn.state == "done"
    review = session["state"]["closing_review"]["current"]
    assert review["verdict"] == "not_yet"
    assert review["failing"] == ["شهود و ساحت", "جمع‌بندی"]
    assert [f["status"] for f in review["findings"]] == [
        "destination",
        "destination",
    ]
    assert any(
        chip.get("id") == "revise" for chip in turn.result["suggestions"]
    )


def test_an_honest_gaps_verdict_needs_no_revision(tmp_path):
    # Section one falls back to its honest gap, the judgment reads the
    # document as honestly stating what the Books cannot establish:
    # nothing failed, the accept chip renders, no revise chip.
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
            judgment_reply("honest_gaps", "سند صادقانه است."),
        ]
    )
    turn, session = clean_brief_turn(upstream, tmp_path)
    assert turn.state == "done"
    review = session["state"]["closing_review"]["current"]
    assert review["verdict"] == "honest_gaps"
    assert review["failing"] == []
    assert [f["status"] for f in review["findings"]] == ["gap", "ok"]
    chips = turn.result["suggestions"]
    assert any(
        chip["kind"] == "proposal" and chip.get("accept") is True
        for chip in chips
    )
    assert not any(chip.get("id") == "revise" for chip in chips)


def test_an_all_gap_brief_is_still_reviewed(tmp_path):
    # Every section falls back to its honest gap: no paragraph exists,
    # yet the completed assembly faces the review — the judgment exists
    # to bless (or refuse) exactly the document that honestly states
    # what the Books cannot establish.
    state = brief_ready_state()
    junk = composer_reply(
        json.dumps(
            {
                "blocks": [
                    {"type": "paragraph", "parts": [{"text": "متنی بی هیچ نقلی"}]}
                ]
            },
            ensure_ascii=False,
        )
    )
    upstream = ResearchUpstream(
        composer_replies=[
            junk,  # section one's write
            composer_reply("این اصلاً JSON نیست."),  # its one retry
            junk,  # section two's write
            composer_reply("این اصلاً JSON نیست."),  # its one retry
            judgment_reply("honest_gaps", "سند صادقانه است."),
        ]
    )
    turn, session = clean_brief_turn(upstream, tmp_path)
    assert turn.state == "done"
    reply = turn.result["reply"]
    # No narration call ran (nothing was written), yet the verdict and
    # the chips land. W4 (stage C) took the chip turn's classify call:
    # five composer calls, not six.
    assert len(composer_bodies(upstream)) == 5
    assert [b["text"] for b in reply if b["type"] == "heading"] == [
        "شهود و ساحت",
        "جمع‌بندی",
    ]
    review = session["state"]["closing_review"]["current"]
    assert review["verdict"] == "honest_gaps"
    assert review["failing"] == []
    assert [f["status"] for f in review["findings"]] == ["gap", "gap"]
    assert any(
        p["kind"] == "closing_review"
        for p in session["state"]["pending_proposals"]
    )


def test_a_fresh_review_never_reuses_a_waiting_proposals_id(tmp_path):
    # A scope proposal waits (p1 the review, p2 the scope): the revise
    # turn's fresh review supersedes the parked one — its id must not
    # collide with the scope proposal still waiting, or a chip could
    # resolve the wrong checkpoint.
    state = brief_ready_state()
    first = ResearchUpstream(
        composer_replies=[
            classify_reply("drafting"),
            section_reply(0, SENTENCE, filler="و اما دامنۀ بیرون هم: "),
            section_reply(1, OTHER_SENTENCE),
            NARRATION,
            judgment_reply("delivers", "سند مقصد را می‌رساند."),
        ]
    )
    session = make_session(tmp_path, state=state)
    turn = run_turn_sync(session, research.COMMAND_BRIEF, first, tmp_path)
    assert turn.state == "done"
    state = research_store.load_session(session["id"])["state"]
    state["pending_proposals"].append(
        {
            "id": research._next_proposal_id(state),
            "kind": "scope",
            "text": "دامنۀ پژوهش: آزمایشی",
            "scope_in": [],
            "scope_out": [],
        }
    )
    research_store.save_session(session["id"], state)
    second = ResearchUpstream(
        composer_replies=[
            classify_reply("drafting"),
            section_reply(0, SENTENCE),
            NARRATION,
            judgment_reply("delivers", "این بار سند می‌رساند."),
        ]
    )
    turn = run_turn_sync(session, research.COMMAND_REVISE, second, tmp_path)
    assert turn.state == "done"
    pending = research_store.load_session(session["id"])["state"][
        "pending_proposals"
    ]
    ids = [p["id"] for p in pending]
    assert len(ids) == len(set(ids))
    reviews = [p for p in pending if p["kind"] == "closing_review"]
    assert len(reviews) == 1
    assert reviews[0]["id"] not in {
        p["id"] for p in pending if p["kind"] == "scope"
    }


# --- the review as its own skill -----------------------------------------------


def test_a_picked_review_runs_over_the_standing_document(tmp_path):
    # The classifier picks the review skill on a finished Brief: pure
    # code plus the ONE judgment call — no writer runs.
    state = brief_ready_state()
    first = ResearchUpstream(
        composer_replies=[
            classify_reply("drafting"),
            section_reply(0, SENTENCE),
            section_reply(1, OTHER_SENTENCE),
            NARRATION,
            judgment_reply("delivers", "سند مقصد را می‌رساند."),
        ]
    )
    session = make_session(tmp_path, state=state)
    turn = run_turn_sync(session, research.COMMAND_BRIEF, first, tmp_path)
    assert turn.state == "done"
    research.decide_proposal(
        PHONE,
        session["id"],
        next(
            p
            for p in research_store.load_session(session["id"])["state"][
                "pending_proposals"
            ]
            if p["kind"] == "closing_review"
        )["id"],
        True,
    )

    second = ResearchUpstream(
        composer_replies=[
            classify_reply("closing_review"),
            judgment_reply("delivers", "دوباره بررسی شد."),
        ]
    )
    turn = run_turn_sync(session, "بازبینی خلاصه", second, tmp_path)
    assert turn.state == "done"
    assert len(composer_bodies(second)) == 2
    assert any(
        b["type"] == "note" and "بازبینی پایانی" in b.get("text", "")
        for b in turn.result["reply"]
    )
    review = research_store.load_session(session["id"])["state"][
        "closing_review"
    ]
    assert len(review["versions"]) == 2


def test_the_review_refuses_without_a_document(tmp_path):
    state = brief_ready_state()
    upstream = ResearchUpstream(composer_replies=[classify_reply("closing_review")])
    session = make_session(tmp_path, state=state)
    turn = run_turn_sync(session, "بازبینی خلاصه", upstream, tmp_path)
    assert turn.state == "done"
    assert len(composer_bodies(upstream)) == 1
    assert any(
        research.RESEARCH_REVIEW_NO_DOCUMENT_DETAIL in b.get("text", "")
        for b in turn.result["reply"]
    )
    assert not any(
        p["kind"] == "closing_review"
        for p in research_store.load_session(session["id"])["state"][
            "pending_proposals"
        ]
    )


# --- the state shape -----------------------------------------------------------


def test_the_state_shape_backfills_the_review_fields():
    # A session persisted before the Closing review existed gains the
    # empty document and the empty review ledger — a pure fill.
    state = research.new_research_state("پرسش پژوهش؟")
    del state["brief_document"]
    del state["closing_review"]
    research.ensure_state_shape(state)
    assert state["brief_document"] == {"complete": False, "sections": []}
    assert state["closing_review"] == {"current": None, "versions": []}
