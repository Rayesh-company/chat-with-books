from tests.conftest import REPO_ROOT

README = REPO_ROOT / "README.md"


def test_readme_records_recall_hybrid_first_answer_contract():
    text = README.read_text(encoding="utf-8")
    assert "/api/v1/recall" in text
    assert '"searchType":"HYBRID_COMPLETION"' in text
    assert '"includeReferences":true' in text
    assert '"datasets":["tarhe-kolli"]' in text


def test_readme_records_farsi_citation_from_chunk_document_name():
    text = README.read_text(encoding="utf-8")
    assert "Do not invent a citation formatter" in text
    assert "document_name" in text
    assert "omitted for Farsi" in text


def test_readme_new_question_starts_a_new_first_answer():
    text = README.read_text(encoding="utf-8")
    assert "A new question starts a new first answer" in text
    recall_json = text.split("/api/v1/recall", 1)[1]
    recall_json = recall_json.split("--data-raw '", 1)[1].split("'", 1)[0]
    assert "sessionId" not in recall_json
    assert "GRAPH_COMPLETION_COT" not in recall_json
