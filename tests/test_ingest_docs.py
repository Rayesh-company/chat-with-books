from tests.conftest import REPO_ROOT

README = REPO_ROOT / "README.md"


def test_readme_documents_remember_ingest_of_the_book():
    text = README.read_text(encoding="utf-8")
    assert "tarhe-kolli.pdf" in text
    assert "/api/v1/remember" in text
    assert "datasetName=tarhe-kolli" in text
    assert "data=@tarhe-kolli.pdf" in text
    assert "run_in_background=true" in text


def test_readme_documents_chunk_retrieval_smoke():
    text = README.read_text(encoding="utf-8")
    assert "/api/v1/search" in text
    assert "CHUNKS" in text
    assert '"datasets":["tarhe-kolli"]' in text
    assert "tarhe-kolli.pdf" in text
