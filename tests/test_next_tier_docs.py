from tests.conftest import REPO_ROOT

README = REPO_ROOT / "README.md"


def _next_tier_section() -> str:
    text = README.read_text(encoding="utf-8")
    return text.split("## Next-tier search", 1)[1].split("## ", 1)[0]


def test_readme_records_next_tier_cot_search_contract():
    section = _next_tier_section()
    assert "/api/v1/recall" in section
    assert '"searchType":"GRAPH_COMPLETION_COT"' in section
    assert '"includeReferences":true' in section
    assert '"datasets":["tarhe-kolli"]' in section
    assert "8001" in section


def test_readme_keeps_next_tier_off_the_first_answer_path():
    text = README.read_text(encoding="utf-8")
    first_answer_curl = text.split("/api/v1/recall", 2)[1].split("--data-raw '", 1)[1]
    first_answer_curl = first_answer_curl.split("'", 1)[0]
    assert "HYBRID_COMPLETION" in first_answer_curl
    assert "GRAPH_COMPLETION_COT" not in first_answer_curl


def test_readme_pins_the_next_tier_model_and_its_reason():
    section = _next_tier_section()
    assert "glm-5.3" in section
    assert "/v1/models" in section
    assert "LLM_QUERY_MODEL" in section
    assert "8000" in section


def test_readme_bounds_next_tier_to_a_second_search_on_the_same_question():
    section = _next_tier_section()
    assert "same question" in section
    assert "A new question is a new first answer" in section
    assert "FEELING_LUCKY" in section
    assert "AGENTIC_COMPLETION" in section
    assert "GRAPH_COMPLETION_DECOMPOSITION" in section
    assert "max_iter=4" in section
