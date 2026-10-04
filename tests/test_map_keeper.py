"""The state caps and the map keeper's contract locks (ticket T12,
GitLab issue #13, ADR-0012): the working ledgers — evidence, claims,
gaps, decisions — cap newest-kept so the writer prompts stay bounded in
a long session, and the map's own rows (open questions, fog) never trim
silently — the map keeper (نقشه‌بان) surveys them in pure code and
proposes each cleanup as ONE pending decision through the standing
decide flow, damped by the usual cooldown.

The three acceptance criteria ride these tests:

- the prompts stay bounded as the caps are hit (the ledger IS the pool
  the writers read; the Brief-prompt bound is pinned end to end in
  tests/test_characterization.py's rewritten T12 pin);
- the keeper's proposals land through the decide flow with cooldown;
- no history is removed without an accepted decision."""

import json

from tests.conftest import REPO_ROOT

import sys  # noqa: E402

sys.path.insert(0, str(REPO_ROOT))

from tests.upstream_fakes import (  # noqa: E402
    PHONE,
    ResearchUpstream,
    classify_reply,
    composer_bodies,
    make_session,
    run_turn_sync,
)
from ui import research, research_store  # noqa: E402


def crowded_state():
    """A map the keeper has work on: two pending questions on one topic
    (the older duplicated by the newer's sharper phrasing), a fog note a
    question's topic already took over, and five finished questions —
    one more than the map's readability keep."""
    state = research.new_research_state("پرسش پژوهش؟")
    state["stage"] = "investigating"
    state["subquestions"] = [
        {"id": "q1", "name": "کهنه", "text": "کهنه و تکراری؟", "status": "pending"},
        {
            "id": "q2",
            "name": "تازه",
            "text": "کهنه و تکراری با ادامۀ دقیق‌تر؟",
            "status": "pending",
        },
    ] + [
        {
            "id": f"d{i}",
            "name": f"تمام‌شده {i}",
            "text": f"پرسش تمام‌شدۀ شمارۀ {i}؟",
            "status": "searched",
        }
        for i in range(5)
    ]
    state["map"]["fog"] = [{"id": "f1", "text": "کهنه و تکراری"}]
    return state


def keeper_turn(session, upstream, tmp_path):
    return run_turn_sync(session, research.COMMAND_KEEP_MAP, upstream, tmp_path)


# --- the working ledgers' caps (AC 1) ----------------------------------------


def test_the_evidence_cap_keeps_the_newest_and_the_ids_stay_unique():
    state = research.new_research_state("پرسش پژوهش؟")
    passages = [
        {"reference": f"chunk {i} of document tarhe-kolli", "passage": f"نشانه{i}پایان"}
        for i in range(research.RESEARCH_MAX_EVIDENCE + 5)
    ]
    research.seed_evidence(state, passages, "پرسش پژوهش؟")
    assert len(state["evidence"]) == research.RESEARCH_MAX_EVIDENCE
    # The five oldest dropped, the newest kept; ids never reused, so a
    # claim's recorded evidence_ids can never silently re-point.
    assert [item["id"] for item in state["evidence"][:2]] == ["e6", "e7"]
    assert state["evidence"][-1]["id"] == f"e{research.RESEARCH_MAX_EVIDENCE + 5}"
    assert len({item["id"] for item in state["evidence"]}) == len(state["evidence"])
    # A merge past the cap keeps the bound; ids keep climbing.
    research.seed_evidence(
        state,
        [{"reference": "r new", "passage": "نشانهٔ تازهٔ یکتا."}],
        "پرسش پژوهش؟",
    )
    assert len(state["evidence"]) == research.RESEARCH_MAX_EVIDENCE
    assert state["evidence"][-1]["id"] == f"e{research.RESEARCH_MAX_EVIDENCE + 6}"


def test_the_claim_gap_and_decision_ledgers_cap_newest_kept():
    state = research.new_research_state("پرسش پژوهش؟")
    state["evidence"] = [
        {"id": "e1", "reference": "r", "passage": "شاهد یکتا."}
    ]
    blocks = [
        {
            "type": "paragraph",
            "parts": [
                {"text": f"ادعای شمارۀ {i}: "},
                {"quote": "شاهد یکتا.", "source": 0},
            ],
        }
        for i in range(research.RESEARCH_MAX_CLAIMS + 3)
    ]
    research.record_claims(state, blocks, ["e1"])
    assert len(state["claims"]) == research.RESEARCH_MAX_CLAIMS
    assert state["claims"][0]["id"] == "c4"
    assert state["claims"][-1]["id"] == f"c{research.RESEARCH_MAX_CLAIMS + 3}"
    for i in range(research.RESEARCH_MAX_GAPS + 2):
        research._add_gap(state, f"شکاف یکتای شمارۀ {i}.")
    assert len(state["gaps"]) == research.RESEARCH_MAX_GAPS
    for i in range(research.RESEARCH_MAX_DECISIONS + 2):
        research._add_decision(state, f"تصمیم یکتای شمارۀ {i}.")
    assert len(state["decisions"]) == research.RESEARCH_MAX_DECISIONS
    assert state["decisions"][0]["text"] == "تصمیم یکتای شمارۀ 2."


def test_a_capped_trim_never_mispoints_the_standing_document(tmp_path):
    # The evidence cap's front-trim re-anchors the standing Brief
    # document's quoted positions in the same mutation, and a claim
    # whose recorded support the cap retired is carried by its own
    # record — the Closing review never false-fails a section for a
    # trim it did not write.
    state = research.new_research_state("پرسش پژوهش؟")
    for i in range(3):
        research.seed_evidence(
            state,
            [{"reference": f"r{i}", "passage": f"شاهد شمارۀ {i}."}],
            "پرسش پژوهش؟",
        )
    pool, ids = research._evidence_pool(state)
    blocks = [
        {
            "type": "paragraph",
            "parts": [
                {"text": "ادعا: "},
                {"quote": "شاهد شمارۀ 0.", "source": 0},
            ],
        }
    ]
    research.record_claims(state, blocks, ids)
    state["brief_document"] = {
        "complete": True,
        "sections": [
            {
                "title": "بخش یکم",
                "paragraphs": blocks,
                "gap": False,
            }
        ],
    }
    state["section_contracts"] = [
        {
            "title": "بخش یکم",
            "question": "",
            "claims": [claim["id"] for claim in state["claims"]],
            "scope_in": [],
            "scope_out": [],
        }
    ]
    # The review reads ok before the trim: the section quotes the
    # claim's support.
    findings = research.closing_review_findings(state)
    assert [finding["status"] for finding in findings] == ["ok"]
    # A merge big enough to trim the quoted passage: the document's
    # source index re-anchors (its passage retired, the locator drops),
    # and the claim carries by its record.
    research.seed_evidence(
        state,
        [
            {"reference": f"t{i}", "passage": f"نشانهٔ تازهٔ شمارۀ {i}."}
            for i in range(research.RESEARCH_MAX_EVIDENCE + 2)
        ],
        "پرسش پژوهش؟",
    )
    assert len(state["evidence"]) == research.RESEARCH_MAX_EVIDENCE
    assert "e1" not in {item["id"] for item in state["evidence"]}
    findings = research.closing_review_findings(state)
    assert [finding["status"] for finding in findings] == ["ok"]


# --- the keeper's proposals ride the decide flow (AC 2) -----------------------


def test_the_keeper_chip_offers_the_survey_when_the_map_needs_it():
    state = crowded_state()
    chips = research.research_suggestions(state)
    assert chips[-1] == {
        "kind": "move",
        "id": "map_keeper",
        "text": research.COMMAND_KEEP_MAP,
    }
    assert research.resolve_command(research.COMMAND_KEEP_MAP) == (
        "map_keeper",
        None,
    )
    # A clean map offers no chip; a waiting cleanup or a running
    # cooldown offers none either.
    assert not any(
        chip["id"] == "map_keeper"
        for chip in research.research_suggestions(research.new_research_state("پرسش؟"))
    )
    parked = crowded_state()
    research._park_map_cleanup(
        parked, research.map_cleanup_survey(parked)
    )
    assert not any(
        chip["id"] == "map_keeper" for chip in research.research_suggestions(parked)
    )
    cooled = crowded_state()
    cooled["proposal_cooldowns"]["map_cleanup"] = 1
    assert not any(
        chip["id"] == "map_keeper" for chip in research.research_suggestions(cooled)
    )


def test_the_keeper_parks_one_cleanup_and_moves_nothing(tmp_path):
    # The survey runs pure code and mutates nothing: the turn's reply is
    # the checkpoint's row-by-row diff, the proposal waits, and the map
    # holds every row it held before — nothing is removed without the
    # operator's decision.
    state = crowded_state()
    before = json.dumps(state["subquestions"]), json.dumps(state["map"]["fog"])
    upstream = ResearchUpstream(composer_replies=[])
    session = make_session(tmp_path, state=state)
    turn = keeper_turn(session, upstream, tmp_path)
    assert turn.state == "done"
    # The survey is free — and W4 (stage C) took the chip's classify
    # call: the turn's upstream footprint is exactly zero.
    assert len(composer_bodies(upstream)) == 0
    proposal = session["state"]["pending_proposals"]
    assert [item["kind"] for item in proposal] == ["map_cleanup"]
    assert proposal[0]["drop_questions"] == ["q1", "d0"]
    assert proposal[0]["drop_fog"] == ["f1"]
    notes = [block["text"] for block in turn.result["reply"]]
    assert any("مرتب‌کردن برنامه" in text for text in notes)
    assert any("پرسش «کهنه» از برنامه برداشته می‌شود" in text for text in notes)
    assert (json.dumps(session["state"]["subquestions"]), json.dumps(
        session["state"]["map"]["fog"]
    )) == before


def test_the_accepted_cleanup_applies_exactly_its_named_rows(tmp_path):
    state = crowded_state()
    state["research_question"]["versions"] = [
        {"text": "پرسش پژوهش؟", "reason": "پرسش آغازین", "turn": 0}
    ]
    upstream = ResearchUpstream(composer_replies=[classify_reply("map_keeper")])
    session = make_session(tmp_path, state=state)
    turn = keeper_turn(session, upstream, tmp_path)
    assert turn.state == "done"
    proposal_id = session["state"]["pending_proposals"][0]["id"]
    result, error = research.decide_proposal(
        PHONE, session["id"], proposal_id, True
    )
    assert error is None
    loaded = research_store.load_session(session["id"])
    state = loaded["state"]
    # Exactly the named rows left: the duplicate and the oldest finished
    # question, the stale fog — the newer phrasing, the other finished
    # questions, and every ledger stay.
    assert [item["id"] for item in state["subquestions"]] == [
        "q2",
        "d1",
        "d2",
        "d3",
        "d4",
    ]
    assert state["map"]["fog"] == []
    assert state["evidence"] == []
    # The decision line names what went; the provenance is untouched.
    assert "برنامه مرتب شد" in state["decisions"][-1]["text"]
    assert "«کهنه»" in state["decisions"][-1]["text"]
    assert len(state["research_question"]["versions"]) == 1
    # The usual cooldown damps the kind.
    assert state["proposal_cooldowns"]["map_cleanup"] == (
        research.PROPOSAL_COOLDOWN_TURNS
    )
    assert any("برنامه مرتب شد" in block["text"] for block in result["reply"])


def test_the_reject_records_the_refusal_damps_and_never_reproposes(tmp_path):
    # A rejected cleanup removes nothing, the refusal joins the
    # decision index, and the damped window that follows parks nothing
    # while the rows still wait; after it, the SAME cleanup never
    # re-parks — a decided text (accepted OR rejected) stays decided.
    state = crowded_state()
    upstream = ResearchUpstream(
        composer_replies=[classify_reply("map_keeper") for _ in range(4)]
    )
    session = make_session(tmp_path, state=state)
    turn = keeper_turn(session, upstream, tmp_path)
    proposal_id = session["state"]["pending_proposals"][0]["id"]
    result, error = research.decide_proposal(
        PHONE, session["id"], proposal_id, False
    )
    assert error is None
    state = research_store.load_session(session["id"])["state"]
    # Nothing was removed: every row the survey named is still there.
    assert [item["id"] for item in state["subquestions"]] == [
        "q1",
        "q2",
        "d0",
        "d1",
        "d2",
        "d3",
        "d4",
    ]
    assert len(state["map"]["fog"]) == 1
    assert any(
        "پیشنهاد رد شد" in item["text"] for item in state["decisions"]
    )
    assert state["proposal_cooldowns"]["map_cleanup"] == (
        research.PROPOSAL_COOLDOWN_TURNS
    )
    # The harness's in-memory session reloads before the next turn.
    session["state"] = state
    # The damped window parks nothing and says so.
    for _ in range(research.PROPOSAL_COOLDOWN_TURNS):
        turn = keeper_turn(session, upstream, tmp_path)
        assert turn.state == "done"
        assert session["state"]["pending_proposals"] == []
        notes = [block["text"] for block in turn.result["reply"]]
        assert notes[-1] == research.RESEARCH_MAP_COOLDOWN_NOTE
    # Out of the window: the exact cleanup is still a no — the decided
    # text never comes back.
    turn = keeper_turn(session, upstream, tmp_path)
    assert session["state"]["pending_proposals"] == []
    notes = [block["text"] for block in turn.result["reply"]]
    assert notes[-1] == research.RESEARCH_MAP_DECIDED_NOTE


# --- nothing is removed without an accepted decision (AC 3) -------------------


def test_a_clean_map_costs_one_honest_note(tmp_path):
    # Nothing to clean: the keeper says so, moves nothing, and the
    # classify call stays the turn's only upstream call.
    state = research.new_research_state("پرسش پژوهش؟")
    state["stage"] = "investigating"
    state["subquestions"] = [
        {"id": "q1", "name": "یکی", "text": "یکی؟", "status": "pending"}
    ]
    upstream = ResearchUpstream(composer_replies=[])
    session = make_session(tmp_path, state=state)
    turn = keeper_turn(session, upstream, tmp_path)
    assert turn.state == "done"
    # W4 (stage C): the chip turn's classify call is gone — the clean
    # map's honest note costs zero composer calls.
    assert len(composer_bodies(upstream)) == 0
    assert session["state"]["pending_proposals"] == []
    assert session["state"]["subquestions"][0]["id"] == "q1"
    notes = [block["text"] for block in turn.result["reply"]]
    assert notes[-1] == research.RESEARCH_MAP_CLEAN_NOTE


def test_the_keeper_turn_never_asks_and_never_moves_a_stage(tmp_path):
    # The keeper is a work skill: no guided question on its turn, and
    # the stage machine stands still — the survey reads the map, it
    # does not walk it.
    state = crowded_state()
    state["stage"] = "orientation"
    state["grilling"] = {"asked_in_stage": 0, "current_question": "", "options": []}
    upstream = ResearchUpstream(composer_replies=[classify_reply("map_keeper")])
    session = make_session(tmp_path, state=state)
    turn = keeper_turn(session, upstream, tmp_path)
    assert turn.state == "done"
    assert session["state"]["stage"] == "orientation"
    assert session["state"]["grilling"]["current_question"] == ""
