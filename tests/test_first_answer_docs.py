from tests.conftest import REPO_ROOT

README = REPO_ROOT / "README.md"


def test_readme_records_recall_hybrid_first_answer_contract():
    text = README.read_text(encoding="utf-8")
    assert "/api/v1/recall" in text
    assert '"searchType":"HYBRID_COMPLETION"' in text
    assert '"includeReferences":true' in text
    assert '"datasets":["tarhe-kolli","70143-336"]' in text


def test_readme_records_farsi_citation_as_cognee_evidence():
    text = README.read_text(encoding="utf-8")
    section = text.split("## First answer", 1)[1].split("## Tests", 1)[0]
    assert "Evidence:" in section
    assert "Do not invent a citation formatter" in section
    assert "omitted for Farsi" not in section
    assert "CHUNKS payload" not in section
    assert "enable_farsi_evidence.py" in section


def test_readme_new_question_starts_a_new_first_answer():
    text = README.read_text(encoding="utf-8")
    assert "A new question starts a new first answer" in text
    recall_json = text.split("/api/v1/recall", 1)[1]
    recall_json = recall_json.split("--data-raw '", 1)[1].split("'", 1)[0]
    assert "sessionId" not in recall_json
    assert "GRAPH_COMPLETION_COT" not in recall_json
