import yaml

from tests.conftest import REPO_ROOT

COMPOSE = REPO_ROOT / "compose.yaml"


def _load_compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def test_compose_runs_cognee_and_postgres_without_another_graph_store():
    compose = _load_compose()
    services = compose["services"]

    assert "postgres" in services
    assert "cognee" in services
    assert "neo4j" not in services
    assert "kuzu" not in services

    postgres = services["postgres"]
    assert postgres["image"].startswith("pgvector/pgvector")
    assert "5432:5432" in postgres["ports"]
    assert "profiles" not in postgres

    cognee = services["cognee"]
    assert cognee["image"].startswith("cognee/cognee")
    assert "build" not in cognee
    assert "8000:8000" in cognee["ports"]
    assert cognee["depends_on"]["postgres"]["condition"] == "service_healthy"


def test_compose_pins_postgres_demo_for_the_graph():
    compose = _load_compose()
    env = compose["services"]["cognee"]["environment"]
    graph = env.get("GRAPH_DATABASE_PROVIDER")
    assert graph in {"postgres_demo", "${GRAPH_DATABASE_PROVIDER:-postgres_demo}"}


def test_compose_pins_avalai_for_chat_and_embeddings():
    compose = _load_compose()
    env = compose["services"]["cognee"]["environment"]
    assert env["LLM_PROVIDER"] == "custom"
    assert env["LLM_ENDPOINT"] == "https://api.avalai.ir/v1"
    assert env["LLM_MODEL"] == "openai/gpt-5.4-mini"
    assert env["EMBEDDING_PROVIDER"] == "openai_compatible"
    assert env["EMBEDDING_ENDPOINT"] == "https://api.avalai.ir/v1"
    assert env["EMBEDDING_MODEL"] == "text-embedding-3-small"
    assert env["EMBEDDING_API_KEY"] == "${LLM_API_KEY}"


def test_compose_pins_postgres_connection_on_cognee():
    compose = _load_compose()
    env = compose["services"]["cognee"]["environment"]
    assert env["DB_HOST"] == "postgres"
    assert str(env["DB_PORT"]) == "5432"
    assert env["DB_USERNAME"] == "cognee"
    assert env["DB_PASSWORD"] == "cognee"
    assert env["DB_NAME"] == "cognee_db"


def test_compose_healthcheck_hits_cognee_health():
    compose = _load_compose()
    probe = compose["services"]["cognee"]["healthcheck"]["test"]
    assert any("/health" in str(part) for part in probe)


def test_ui_is_an_optional_profile_on_port_3000():
    compose = _load_compose()
    frontend = compose["services"]["frontend"]
    assert "ui" in frontend["profiles"]
    assert "3000:3000" in frontend["ports"]
    assert frontend["image"].startswith("cognee/cognee-ui")
