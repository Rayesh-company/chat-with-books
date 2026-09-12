import re

from tests.conftest import REPO_ROOT, parse_dotenv

ENV_EXAMPLE = REPO_ROOT / ".env.example"


def test_env_example_points_chat_at_z_ai_and_embeddings_at_avalai():
    env = parse_dotenv(ENV_EXAMPLE)

    assert env["LLM_PROVIDER"] == "custom"
    assert env["LLM_ENDPOINT"] == "https://api.z.ai/api/coding/paas/v4"
    assert env["LLM_MODEL"] == "openai/glm-5.3-flash"
    assert "LLM_API_KEY" in env

    assert env["EMBEDDING_PROVIDER"] == "openai_compatible"
    assert env["EMBEDDING_ENDPOINT"] == "https://api.avalai.ir/v1"
    assert env["EMBEDDING_MODEL"] == "text-embedding-3-large"
    assert env["EMBEDDING_DIMENSIONS"] == "3072"
    assert "EMBEDDING_API_KEY" in env


def test_env_example_keeps_the_whole_memory_layer_on_postgres():
    env = parse_dotenv(ENV_EXAMPLE)

    assert env["DB_PROVIDER"] == "postgres"
    assert env["VECTOR_DB_PROVIDER"] == "pgvector"
    assert env["GRAPH_DATABASE_PROVIDER"] == "postgres_demo"
    assert env["CACHE_BACKEND"] == "postgres"


def test_env_example_does_not_ship_a_real_api_key():
    env = parse_dotenv(ENV_EXAMPLE)
    placeholders = {
        "LLM_API_KEY": {"", "your_z_ai_api_key"},
        "EMBEDDING_API_KEY": {"", "your_avalai_api_key"},
    }
    for key, allowed in placeholders.items():
        value = env[key]
        assert value in allowed
        assert not value.startswith("aa-")
        assert not value.startswith("sk-")
        assert not re.fullmatch(r"[0-9a-f]{32}\.[A-Za-z0-9]{16}", value)


def test_env_example_caps_embedding_throughput_for_avalai():
    env = parse_dotenv(ENV_EXAMPLE)
    assert int(env["EMBEDDING_BATCH_SIZE"]) <= 32
    assert env["EMBEDDING_RATE_LIMIT_ENABLED"].lower() == "true"
    assert int(env["EMBEDDING_RATE_LIMIT_REQUESTS"]) <= 120
    assert env["EMBEDDING_RATE_LIMIT_INTERVAL"] == "60"
