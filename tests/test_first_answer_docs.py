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
