from tests.conftest import REPO_ROOT

README = REPO_ROOT / "README.md"


def test_readme_documents_compose_up_and_health():
    text = README.read_text(encoding="utf-8")
    assert "docker compose up -d" in text
    assert "http://localhost:8000/health" in text
    assert "docker compose --profile ui up -d" in text
    assert "curl.exe -f http://localhost:3000" in text
