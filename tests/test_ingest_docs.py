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
    assert '"datasets":["tarhe-kolli","70143-336"]' in text
    assert "Books (`document_name`)" in text
    assert "document_name" in text
    assert "tarhe-kolli" in text
    assert "70143-336" in text


def test_readme_records_two_book_limit_contract():
    text = README.read_text(encoding="utf-8")
    assert (
        "The Book set is two Books: طرح کلی اندیشۀ اسلامی در قرآن (`tarhe-kolli.pdf`) "
        "and انسان ۲۵۰ ساله (`70143-336.pdf`)" in text
    )
    assert text.count("datasetName=") == 2
    assert "datasetName=tarhe-kolli" in text
    assert "datasetName=70143-336" in text
    assert text.count("data=@") == 2
    assert "data=@tarhe-kolli.pdf" in text
    assert "data=@70143-336.pdf" in text


def test_readme_records_the_five_page_chunk_cap_contract():
    text = README.read_text(encoding="utf-8")
    assert "chunk_size" in text
    assert "chunks_per_batch=36" in text
    assert "chunk_size=4100" in text
    assert "chunk_size=3500" in text
    assert "in tokens" in text
    assert "5 PDF pages" in text


def test_readme_records_ingest_contract_not_live_stack():
    text = README.read_text(encoding="utf-8")
    assert "recorded ingest contract" in text
    assert "A green suite does not mean remember has run" in text
    assert "CHUNKS returned hits" in text
