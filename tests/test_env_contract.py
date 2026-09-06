from tests.conftest import REPO_ROOT, parse_dotenv

ENV_EXAMPLE = REPO_ROOT / ".env.example"


def test_env_example_points_chat_and_embeddings_at_avalai():
    env = parse_dotenv(ENV_EXAMPLE)

    assert env["LLM_PROVIDER"] == "custom"
    assert env["LLM_ENDPOINT"] == "https://api.avalai.ir/v1"
    assert env["LLM_MODEL"] == "openai/gpt-5.4-mini"
    assert "LLM_API_KEY" in env

    assert env["EMBEDDING_PROVIDER"] == "openai_compatible"
    assert env["EMBEDDING_ENDPOINT"] == "https://api.avalai.ir/v1"
    assert env["EMBEDDING_MODEL"] == "text-embedding-3-small"
    assert env["EMBEDDING_DIMENSIONS"] == "1536"
    assert "EMBEDDING_API_KEY" in env


def test_env_example_keeps_the_whole_memory_layer_on_postgres():
    env = parse_dotenv(ENV_EXAMPLE)

    assert env["DB_PROVIDER"] == "postgres"
    assert env["VECTOR_DB_PROVIDER"] == "pgvector"
    assert env["GRAPH_DATABASE_PROVIDER"] == "postgres_demo"
    assert env["CACHE_BACKEND"] == "postgres"


def test_env_example_does_not_ship_a_real_api_key():
    env = parse_dotenv(ENV_EXAMPLE)
    for key in ("LLM_API_KEY", "EMBEDDING_API_KEY"):
        value = env[key]
        assert value in {"", "your_avalai_api_key"}
        assert not value.startswith("aa-")
        assert not value.startswith("sk-")


def test_env_example_caps_embedding_throughput_for_avalai():
    env = parse_dotenv(ENV_EXAMPLE)
    assert int(env["EMBEDDING_BATCH_SIZE"]) <= 8
    assert env["EMBEDDING_RATE_LIMIT_ENABLED"].lower() == "true"
    assert int(env["EMBEDDING_RATE_LIMIT_REQUESTS"]) <= 12
    assert env["EMBEDDING_RATE_LIMIT_INTERVAL"] == "60"
