from tests.conftest import REPO_ROOT

README = REPO_ROOT / "README.md"


def _deep_dive_section() -> str:
    text = README.read_text(encoding="utf-8")
    return text.split("## Deep dive", 1)[1].split("## ", 1)[0]


def test_readme_records_the_live_cot_engine_until_the_dive_ships():
    section = _deep_dive_section()
    assert "/api/v1/recall" in section
    assert '"searchType":"GRAPH_COMPLETION_COT"' in section
    assert '"includeReferences":true' in section
    assert '"datasets":["tarhe-kolli","70143-336"]' in section
    assert "8001" in section
    assert "Until the Deep dive ships" in section


def test_readme_keeps_deep_dive_off_the_first_answer_path():
    text = README.read_text(encoding="utf-8")
    payloads = [chunk.split("'", 1)[0] for chunk in text.split("--data-raw '")[1:]]
    hybrid_payloads = [p for p in payloads if "HYBRID_COMPLETION" in p]
    assert hybrid_payloads, "README must record a first-answer HYBRID_COMPLETION curl"
    for payload in hybrid_payloads:
        assert "GRAPH_COMPLETION_COT" not in payload


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


def test_readme_records_the_deep_dive_contract_and_caps():
    section = _deep_dive_section()
    assert "operator-started button" in section
    assert "Planner" in section
    assert "Synthesizer" in section
    assert "2,000" in section and "4,000" in section
    assert "one dive per phone" in section
    assert "three dives globally" in section
    assert "stdlib-only" in section
    assert "ADR 0006" in section
