from tests.conftest import REPO_ROOT

README = REPO_ROOT / "README.md"


def _section(heading: str) -> str:
    text = README.read_text(encoding="utf-8")
    return text.split(f"\n## {heading}", 1)[1].split("\n## ", 1)[0]


def _deep_dive_section() -> str:
    return _section("Deep dive")


def _session_ui_section() -> str:
    return _section("Session UI")


def test_readme_records_the_cot_probe_as_a_server_side_operator_probe():
    # The dive shipped (issue #25): the "Until the Deep dive ships"
    # marker is gone, and the recorded COT relay is re-labeled for what
    # it now is — a live server-side probe of the second service the
    # sheet no longer calls, with the same curl shape as before.
    section = _deep_dive_section()
    assert "Until the Deep dive ships" not in section
    assert "auto-started" not in section
    assert '"searchType":"GRAPH_COMPLETION_COT"' in section
    assert '"includeReferences":true' in section
    assert '"datasets":["tarhe-kolli","70143-336"]' in section
    assert "8001" in section
    assert "operator probe" in section
    assert "no longer calls it" in section
    assert "/next-tier-recall" in section


def test_readme_keeps_deep_dive_off_the_first_answer_path():
    text = README.read_text(encoding="utf-8")
    payloads = [chunk.split("'", 1)[0] for chunk in text.split("--data-raw '")[1:]]
    hybrid_payloads = [p for p in payloads if "HYBRID_COMPLETION" in p]
    assert hybrid_payloads, "README must record a first-answer HYBRID_COMPLETION curl"
    for payload in hybrid_payloads:
        # Any GRAPH_COMPLETION* — the dive's searcher type included —
        # never rides a first-answer payload.
        assert "GRAPH_COMPLETION" not in payload


def test_readme_pins_the_deep_dive_models_and_service_reason():
    section = _deep_dive_section()
    assert "glm-5.3" in section
    assert "/chat/completions" in section
    assert "LLM_QUERY_MODEL" in section
    assert "8000" in section


def test_readme_bounds_the_deep_dive_to_the_same_question():
    section = _deep_dive_section()
    assert "same question" in section
    assert "A new question is a new first answer" in section
    assert "FEELING_LUCKY" in section
    assert "AGENTIC_COMPLETION" in section
    assert "GRAPH_COMPLETION_DECOMPOSITION" in section


def test_readme_records_the_live_deep_dive_contract():
    # The shipped tracer bullet (ADR 0006, issue #25): the operator
    # button, the held /deep-dive request, and the pinned pipeline.
    section = _deep_dive_section()
    assert "operator-started button" in section
    assert "شروع مطالعۀ عمیق" in section
    assert "/deep-dive" in section
    assert "held request" in section
    # Payloads pinned server-side — the browser names no search type.
    assert "server-side" in section
    assert "Planner" in section
    assert "Synthesizer" in section
    assert "thinking on" in section
    assert "thinking disabled" in section
    assert "six" in section
    assert "GRAPH_COMPLETION" in section
    assert "2,000" in section and "4,000" in section
    assert "five to eight" in section
    # The references list is built server-side from the real pool.
    assert "references" in section
    assert "منابع" in section
    assert "verbatim guard" in section
    assert "continuation" in section
    assert "never counts" in section
    assert "stdlib-only" in section
    assert "ADR 0006" in section


def test_readme_records_the_registry_caps_and_gap_round_as_not_yet():
    # No job registry, caps, or gap round shipped in this slice — the
    # README must say they follow, not that they exist.
    section = _deep_dive_section()
    assert "one dive per phone" in section
    assert "three dives globally" in section
    assert "gap round" in section
    assert "No job registry, caps, or gap round yet" in section


def test_readme_records_the_operator_started_phase_three_in_session_ui():
    section = _session_ui_section()
    assert "مطالعۀ عمیق" in section
    assert "شروع مطالعۀ عمیق" in section
    # No auto-start anywhere: an ask never starts the dive, and the
    # whole-pipeline clock stops when phase 2 settles.
    assert "starts automatically" not in section
    assert "no longer auto-starts" in section
    assert "stops when phase 2 settles" in section
    assert "/deep-dive" in section
