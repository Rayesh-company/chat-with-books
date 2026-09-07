from tests.conftest import REPO_ROOT

README = REPO_ROOT / "README.md"


def test_readme_records_remember_command_contract():
    text = README.read_text(encoding="utf-8")
    assert "tarhe-kolli.pdf" in text
    assert "/api/v1/remember" in text
    assert "datasetName=tarhe-kolli" in text
    assert "data=@tarhe-kolli.pdf" in text
    assert "run_in_background=true" in text


def test_readme_records_chunks_search_contract():
    text = README.read_text(encoding="utf-8")
    assert "/api/v1/search" in text
    assert "CHUNKS" in text
    assert '"datasets":["tarhe-kolli"]' in text
    assert "Book (`document_name`)" in text
    assert "document_name" in text
    assert "tarhe-kolli" in text


def test_readme_records_one_book_limit_contract():
    text = README.read_text(encoding="utf-8")
    assert "The Book set is one Book: طرح کلی اندیشۀ اسلامی در قرآن" in text
    assert text.count("datasetName=") == 1
    assert "datasetName=tarhe-kolli" in text
    assert text.count("data=@") == 1
    assert "data=@tarhe-kolli.pdf" in text


def test_readme_records_ingest_contract_not_live_stack():
    text = README.read_text(encoding="utf-8")
    assert "recorded ingest contract" in text
    assert "A green suite does not mean remember has run" in text
    assert "CHUNKS returned hits" in text
