"""The fog probe's contract locks (ticket T13, GitLab issue #14,
ADR-0012's کاوشگر): a cheap probe of a fog question — ONE bounded
gather — reports whether it is fertile, real but starved, or not
specifiable. Fog graduates into an open question ON EVIDENCE, or the
Diagnoser predicts a Gap early. Out-of-scope items never graduate.

The three acceptance criteria ride these tests:

- the probe runs inside the per-turn budget and records its result
  (the search pre-paid like every bounded step, the verdict riding the
  note in the state);
- graduation and early gap-prediction are both demoable (one chip tap
  each, the reply naming what happened);
- out-of-scope items never graduate (the veto is pure code — not even
  one search is spent)."""

from tests.conftest import REPO_ROOT

import sys  # noqa: E402

sys.path.insert(0, str(REPO_ROOT))

from tests.upstream_fakes import (  # noqa: E402
    ResearchUpstream,
    classify_reply,
    cognee_payload,
    composer_bodies,
    make_session,
    recall_call_count,
    run_turn_sync,
)
from ui import research  # noqa: E402

FOG_TEXT = "نقش عدل در زندگی اجتماعی چیست؟"


def foggy_state():
    """An investigating session whose map holds one fog note and one
    pending open question — the probe's working ground."""
    state = research.new_research_state("پرسش پژوهش؟")
    state["stage"] = "investigating"
    state["subquestions"] = [
        {"id": "q1", "name": "شهود", "text": "شهود و ساحت؟", "status": "pending"}
    ]
    research._add_fog(state, FOG_TEXT)
    return state


def probe_turn(session, upstream, tmp_path, message=None, **worker_kwargs):
    return run_turn_sync(
        session,
        message or research.COMMAND_PROBE_FOG,
        upstream,
        tmp_path,
        **worker_kwargs,
    )


# --- the chip (the deterministic door) ---------------------------------------


def test_the_probe_chip_offers_when_unprobed_fog_exists():
    state = foggy_state()
    chips = research.research_suggestions(state)
    assert {
        "kind": "move",
        "id": "fog_probe",
        "text": research.COMMAND_PROBE_FOG,
    } in chips
    assert research.resolve_command(research.COMMAND_PROBE_FOG) == (
        "fog_probe",
        None,
    )
    # A fogless map offers no chip; a fully probed fog offers none
    # either — a note is never probed twice.
    assert not any(
        chip["id"] == "fog_probe"
        for chip in research.research_suggestions(
            research.new_research_state("پرسش؟")
        )
    )
    probed = foggy_state()
    probed["map"]["fog"][0]["probe"] = {"verdict": "starved", "passages": 0}
    assert not any(
        chip["id"] == "fog_probe"
        for chip in research.research_suggestions(probed)
    )


def test_the_probe_chip_leaves_the_keeper_chip_last():
    # Both map-care skills can offer on one map: the probe chip joins
    # beside the keeper's, and the keeper's stays the map's last offer —
    # the T12 chip lock keeps its position.
    state = foggy_state()
    state["subquestions"].extend(
        [
            {
                "id": f"d{i}",
                "name": f"تمام‌شده {i}",
                "text": f"پرسش تمام‌شدۀ شمارۀ {i}؟",
                "status": "searched",
            }
            for i in range(5)
        ]
    )
    state["map"]["fog"].append({"id": "f9", "text": "شهود و ساحت"})
    chips = research.research_suggestions(state)
    assert chips[-1] == {
        "kind": "move",
        "id": "map_keeper",
        "text": research.COMMAND_KEEP_MAP,
    }
    assert any(chip["id"] == "fog_probe" for chip in chips)


# --- graduation on evidence (AC 2, the demoable fertile path) ----------------


def test_a_fertile_probe_graduates_the_fog_into_an_open_question(tmp_path):
    # One chip tap: the probe pools the kernel's own starvation bar in
    # passages, the note graduates into a NAMED pending open question on
    # that evidence (the gather's own graduation rule retires it), and
    # the passages join the ledger. The turn stays cheap: the classify
    # call plus exactly ONE search — no planning call, no re-search
    # round, no graph hop.
    upstream = ResearchUpstream(composer_replies=[])
    session = make_session(tmp_path, state=foggy_state())
    turn = probe_turn(session, upstream, tmp_path)
    assert turn.state == "done"
    assert len(composer_bodies(upstream)) == 0
    assert recall_call_count(upstream) == 1
    state = session["state"]
    graduated = [item for item in state["subquestions"] if item["id"] != "q1"]
    assert [item["text"] for item in graduated] == [FOG_TEXT]
    assert graduated[0]["status"] == "pending"
    assert graduated[0]["name"]
    # The note lives only as its question now.
    assert state["map"]["fog"] == []
    assert len(state["evidence"]) == 2
    assert state["evidence"][0]["found_for"] == FOG_TEXT
    # The verdict rode the reply in plain Persian.
    notes = [block["text"] for block in turn.result["reply"]]
    assert any("کاوشگر" in text and FOG_TEXT in text for text in notes)
    assert any("پربار" in text for text in notes)


def test_the_router_can_pick_the_probe_by_intent(tmp_path):
    # The chip is the deterministic door; the model's own pick is the
    # other one — the same skill runs, the same graduation lands.
    upstream = ResearchUpstream(
        composer_replies=[classify_reply("fog_probe")]
    )
    session = make_session(tmp_path, state=foggy_state())
    turn = probe_turn(session, upstream, tmp_path, message="این مه را بسنج")
    assert turn.state == "done"
    assert recall_call_count(upstream) == 1
    state = session["state"]
    assert state["map"]["fog"] == []
    assert any(item["text"] == FOG_TEXT for item in state["subquestions"])


# --- the early Gap prediction (AC 2, the demoable starved path) --------------


def test_a_starved_probe_predicts_the_gap_early(tmp_path):
    # The Books pooled nothing for a specifiable, in-scope note: the
    # Diagnoser predicts the Gap EARLY — the recorded diagnosis rides
    # the map's diagnoses row — and the note keeps its place, marked
    # with the verdict. The formal gap stays the full gather's honest
    # outcome to declare, so no gap entry lands here and nothing
    # graduates.
    upstream = ResearchUpstream(
        composer_replies=[classify_reply()],
        recall_reply=lambda payload: cognee_payload("پاسخی یافت نشد."),
    )
    session = make_session(tmp_path, state=foggy_state())
    turn = probe_turn(session, upstream, tmp_path)
    assert turn.state == "done"
    assert recall_call_count(upstream) == 1
    state = session["state"]
    assert [item["text"] for item in state["subquestions"]] == ["شهود و ساحت؟"]
    assert len(state["map"]["fog"]) == 1
    assert state["map"]["fog"][0]["probe"]["verdict"] == "starved"
    assert state["gaps"] == []
    diagnoses = [
        item for item in state["diagnoses"] if item.get("cause")
    ]
    assert len(diagnoses) == 1
    assert diagnoses[0]["cause"] == "starved_corpus"
    summary = research.research_state_summary(state)
    assert summary["diagnoses"][-1]["cause"] == "starved_corpus"
    notes = [block["text"] for block in turn.result["reply"]]
    assert any("تشخیص‌گر" in text and "شکاف" in text for text in notes)
    # A probed note is never probed twice: the next probe turn finds
    # nothing fresh and spends no search.
    session["state"] = state
    upstream2 = ResearchUpstream(composer_replies=[classify_reply()])
    turn2 = probe_turn(session, upstream2, tmp_path)
    assert turn2.state == "done"
    assert recall_call_count(upstream2) == 0
    notes2 = [block["text"] for block in turn2.result["reply"]]
    assert notes2[-1] == research.RESEARCH_PROBE_NO_FOG_DETAIL


# --- the out-of-scope veto (AC 3) ---------------------------------------------


def test_out_of_scope_fog_never_graduates_and_never_spends_a_search(tmp_path):
    # The operator's own scope ledger ruled the topic out: the probe's
    # veto is pure code, so the turn runs the classify call alone — not
    # even one search — and the note keeps its place, marked with the
    # refusal. The verdict rides the reply in plain Persian.
    state = foggy_state()
    state["scope"]["out"] = ["نقش عدل در زندگی اجتماعی"]
    upstream = ResearchUpstream(composer_replies=[])
    session = make_session(tmp_path, state=state)
    turn = probe_turn(session, upstream, tmp_path)
    assert turn.state == "done"
    assert len(composer_bodies(upstream)) == 0
    assert recall_call_count(upstream) == 0
    state = session["state"]
    assert len(state["map"]["fog"]) == 1
    assert state["map"]["fog"][0]["probe"] == {
        "verdict": "not_specifiable",
        "reason": "out_of_scope",
        "turn": state["turns"],
    }
    assert [item["text"] for item in state["subquestions"]] == ["شهود و ساحت؟"]
    assert state["evidence"] == []
    notes = [block["text"] for block in turn.result["reply"]]
    assert any("هرگز به پرسش باز تبدیل نمی‌شود" in text for text in notes)


def test_a_note_whose_topic_is_already_asked_never_graduates(tmp_path):
    # The stale rule: the note's anchor rides inside an existing open
    # question's text — the map keeper's stale-fog finding — so there is
    # nothing new to specify. No search, no graduation, no duplicate
    # question; the keeper's survey remains the note's cleanup path.
    state = foggy_state()
    state["subquestions"].append(
        {
            "id": "q2",
            "name": "عدل",
            "text": f"{FOG_TEXT} با جزئیات بیشتر؟",
            "status": "pending",
        }
    )
    upstream = ResearchUpstream(composer_replies=[classify_reply()])
    session = make_session(tmp_path, state=state)
    turn = probe_turn(session, upstream, tmp_path)
    assert turn.state == "done"
    assert recall_call_count(upstream) == 0
    state = session["state"]
    assert len(state["map"]["fog"]) == 1
    assert state["map"]["fog"][0]["probe"]["verdict"] == "not_specifiable"
    assert state["map"]["fog"][0]["probe"]["reason"] == "already_asked"
    assert [item["id"] for item in state["subquestions"]] == ["q1", "q2"]


# --- the budget (AC 1) and the work-skill shape -------------------------------


def test_the_probe_runs_inside_the_per_turn_budget(tmp_path):
    # The one search is pre-paid from the turn's budget like every
    # bounded step (T3): a cap the classify call alone exhausts stops
    # the turn at that boundary — the honest stop note, the note
    # unmarked, no search, no verdict faked.
    upstream = ResearchUpstream(composer_replies=[])
    session = make_session(tmp_path, state=foggy_state())
    turn = probe_turn(session, upstream, tmp_path, call_cap=0)
    assert turn.state == "done"
    assert len(composer_bodies(upstream)) == 0
    assert recall_call_count(upstream) == 0
    state = session["state"]
    assert state["map"]["fog"][0] == {"id": "f1", "text": FOG_TEXT}
    assert state["subquestions"][0]["id"] == "q1"
    notes = [block["text"] for block in turn.result["reply"]]
    assert notes[-1] == research.RESEARCH_BUDGET_STOP_DETAIL


def test_the_probe_turn_never_asks_and_never_moves_a_stage(tmp_path):
    # The probe is a work skill: no guided question on its turn, the
    # stage machine stands still, and a pending checkpoint survives a
    # probe command (an explicit command executes, ADR-0011).
    state = foggy_state()
    state["stage"] = "mapping"
    state["grilling"] = {"asked_in_stage": 0, "current_question": "", "options": []}
    upstream = ResearchUpstream(composer_replies=[classify_reply()])
    session = make_session(tmp_path, state=state)
    turn = probe_turn(session, upstream, tmp_path)
    assert turn.state == "done"
    assert session["state"]["stage"] == "mapping"
    assert session["state"]["grilling"]["current_question"] == ""


def test_a_tool_refusal_is_a_recorded_diagnosis(tmp_path):
    # An empty pick refuses every Tool (the registry's own rule): the
    # probe records the wrong-tool diagnosis and the note stays — never
    # a silent empty pool, never a faked verdict.
    state = foggy_state()
    state["datasets"] = []
    upstream = ResearchUpstream(composer_replies=[classify_reply()])
    session = make_session(tmp_path, state=state)
    turn = probe_turn(session, upstream, tmp_path)
    assert turn.state == "done"
    state = session["state"]
    assert any(
        item.get("cause") == "wrong_tool" for item in state["diagnoses"]
    )
    assert "probe" not in state["map"]["fog"][0]
    assert state["map"]["fog"] != []


def test_the_probe_row_declares_a_work_skill_without_a_side_door():
    # The dispatch row is the contract: a work skill that searches with
    # the one cheap Tool, parks no proposal of its own — the decide flow
    # stays the only path that mutates the state by decision.
    row = research.SKILL_TABLE_BY_NAME["fog_probe"]
    assert row["kind"] == "work"
    assert row["runner"] in research.SKILL_RUNNERS
    assert row["display_name"] == "کاوشگر"
    assert row["tools"] == ("hybrid",)
    assert "pending_proposals" not in row["state_writes"]
    assert "decide" not in row["caps"]
