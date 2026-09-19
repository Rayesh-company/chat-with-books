import yaml

from tests.conftest import REPO_ROOT

COMPOSE = REPO_ROOT / "compose.yaml"


def _load_compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def test_compose_widens_cognee_evidence_tokens_for_farsi():
    compose = _load_compose()
    cognee = compose["services"]["cognee"]
    volumes = [str(item) for item in cognee.get("volumes", [])]
    assert any("enable_farsi_evidence.py" in item for item in volumes)
    assert cognee.get("entrypoint") == ["python", "/enable_farsi_evidence.py"]


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
    # The host side rides this machine's recorded 18000 remap (something
    # else owns host 8000 here — ARCHITECTURE §3/§13); the container side
    # stays Cognee's 8000.
    assert "18000:8000" in cognee["ports"]
    assert cognee["depends_on"]["postgres"]["condition"] == "service_healthy"


def test_compose_pins_postgres_demo_for_the_graph():
    compose = _load_compose()
    env = compose["services"]["cognee"]["environment"]
    graph = env.get("GRAPH_DATABASE_PROVIDER")
    assert graph in {"postgres_demo", "${GRAPH_DATABASE_PROVIDER:-postgres_demo}"}


def test_compose_pins_chat_and_embeddings_on_avalai():
    # Chat joined embeddings on AvalAI 2026-09-13: the Z.AI coding-plan
    # weekly quota was exhausted (error 1310, reset 2026-09-15 17:06).
    # AvalAI serves the GLM family on its 250 RPM / 4M TPM budget; the
    # chat key rides .env as LLM_API_KEY. To return chat to Z.AI after
    # the reset, flip LLM_ENDPOINT back to the coding endpoint here, in
    # cognee-next-tier, and in the session's composer env.
    compose = _load_compose()
    env = compose["services"]["cognee"]["environment"]
    assert env["LLM_PROVIDER"] == "custom"
    assert env["LLM_ENDPOINT"] == "https://api.avalai.ir/v1"
    assert env["LLM_MODEL"] == "openai/glm-5.3-flash"
    assert env["EMBEDDING_PROVIDER"] == "openai_compatible"
    assert env["EMBEDDING_ENDPOINT"] == "https://api.avalai.ir/v1"
    assert env["EMBEDDING_MODEL"] == "${EMBEDDING_MODEL:-text-embedding-3-large}"
    assert "EMBEDDING_API_KEY" not in env


def test_compose_keeps_the_embedder_env_overridable_on_both_tiers():
    # The 2026-09-12 incident: the hard-coded embedder meant a
    # `docker compose up -d` recreated the containers with
    # text-embedding-3-large/3072 over pgvector tables ingested at
    # small/1536 — every search errored "expected 1536 dimensions, not
    # 3072". The model and dimensions are therefore env-overridable by
    # interpolation (the recorded ADR 0004 default preserved), so a
    # stack whose data was ingested with a different embedder pins it
    # in .env instead of being silently mismatched.
    compose = _load_compose()
    for tier in ("cognee", "cognee-next-tier"):
        env = compose["services"][tier]["environment"]
        assert env["EMBEDDING_MODEL"] == "${EMBEDDING_MODEL:-text-embedding-3-large}"
        assert env["EMBEDDING_DIMENSIONS"] == '${EMBEDDING_DIMENSIONS:-"3072"}'


def test_readme_records_the_vps_embedder_pin_and_real_search_verification():
    # The incident class is recorded where deployers read it: the
    # embedder is frozen into the ingested data, a pre-ADR 0004 stack
    # pins small/1536 in .env, and post-deploy verification is a tool —
    # the smoke's real Farsi search (T17) — plus the start guard, after
    # health probes stayed green through the 2026-09-12 mismatch.
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    vps = readme.split("\n## VPS deploy", 1)[1].split("\n## ", 1)[0]
    assert "frozen into the ingested data" in vps
    assert "text-embedding-3-small" in vps and "1536" in vps
    assert "expected 1536 dimensions, not 3072" in vps
    assert "scripts/smoke.py" in vps
    assert "check_embedding_pin" in vps
    assert "2026-09-12" in vps


def test_compose_allows_avalai_class_embedding_throughput():
    compose = _load_compose()
    env = compose["services"]["cognee"]["environment"]
    assert int(env["EMBEDDING_BATCH_SIZE"]) <= 32
    assert env["EMBEDDING_RATE_LIMIT_ENABLED"] == "true"
    assert int(env["EMBEDDING_RATE_LIMIT_REQUESTS"]) <= 120
    assert str(env["EMBEDDING_RATE_LIMIT_INTERVAL"]) == "60"


def test_compose_routes_ingest_stages_to_avalai():
    # Z.AI's coding endpoint 429'd (code 1302) three ingest attempts on
    # 2026-09-10/11 — its quota is shared with coding tools and cannot
    # sustain extraction bursts. AvalAI handles 250 RPM / 4M TPM, so the
    # two ingest stages (extraction, summarization) are routed there via
    # Cognee's per-stage overrides, while the query stage (first answers,
    # Next-tier) keeps the recorded Z.AI model pins.
    compose = _load_compose()
    cognee_env = compose["services"]["cognee"]["environment"]
    for stage in ("EXTRACTION", "SUMMARIZATION"):
        assert cognee_env[f"LLM_{stage}_PROVIDER"] == "custom"
        assert cognee_env[f"LLM_{stage}_MODEL"] == "openai/glm-5.3-flash"
        assert cognee_env[f"LLM_{stage}_ENDPOINT"] == "https://api.avalai.ir/v1"
        assert cognee_env[f"LLM_{stage}_API_KEY"] == "${EMBEDDING_API_KEY}"
    assert "LLM_QUERY_MODEL" not in cognee_env

    # The RPM ceiling now guards AvalAI's documented budget, and the
    # patient retries remain as storm insurance.
    assert int(cognee_env["LLM_RATE_LIMIT_REQUESTS"]) <= 240
    assert int(cognee_env["LLM_MIN_RETRY_ATTEMPTS"]) >= 4
    assert int(cognee_env["LLM_MIN_RETRY_SECONDS"]) >= 900

    # Next-tier only queries — no stage overrides, its own Z.AI model.
    next_tier_env = compose["services"]["cognee-next-tier"]["environment"]
    assert "LLM_EXTRACTION_MODEL" not in next_tier_env
    assert "LLM_SUMMARIZATION_MODEL" not in next_tier_env
    assert int(next_tier_env["LLM_RATE_LIMIT_REQUESTS"]) <= 60


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


def test_compose_runs_next_tier_cot_on_its_own_cognee_service():
    compose = _load_compose()
    next_tier = compose["services"]["cognee-next-tier"]
    assert next_tier["image"] == compose["services"]["cognee"]["image"]

    env = next_tier["environment"]
    assert env["LLM_PROVIDER"] == "custom"
    assert env["LLM_MODEL"] == "openai/glm-5.3-flash"
    assert env["LLM_ENDPOINT"] == "https://api.avalai.ir/v1"

    volumes = [str(item) for item in next_tier.get("volumes", [])]
    assert any("enable_farsi_evidence.py" in item for item in volumes)
    assert next_tier.get("entrypoint") == ["python", "/enable_farsi_evidence.py"]

    assert env["DB_PROVIDER"] == "postgres"
    assert env["VECTOR_DB_PROVIDER"] == "pgvector"
    assert env["GRAPH_DATABASE_PROVIDER"] == "postgres_demo"

    first = compose["services"]["cognee"]["environment"]
    assert env["DB_HOST"] == first["DB_HOST"]
    assert env["DB_NAME"] == first["DB_NAME"]


def test_compose_keeps_next_tier_off_the_first_answer_path():
    compose = _load_compose()
    first = compose["services"]["cognee"]
    next_tier = compose["services"]["cognee-next-tier"]

    assert first["environment"]["LLM_MODEL"] == "openai/glm-5.3-flash"
    # Both tiers run flash now (2026-09-13, operator call); the
    # first-answer path stays isolated by service and port, not model.
    assert next_tier["environment"]["LLM_MODEL"] == "openai/glm-5.3-flash"

    first_ports = [str(item) for item in first["ports"]]
    next_tier_ports = [str(item) for item in next_tier["ports"]]
    assert "8001:8000" in next_tier_ports
    assert not (set(next_tier_ports) & set(first_ports))

    for service in (first, next_tier):
        assert "LLM_QUERY_MODEL" not in service["environment"]

    assert next_tier["depends_on"]["cognee"]["condition"] == "service_healthy"


def test_compose_runs_the_session_sheet_in_a_container():
    # The three-phase sheet can ride compose too (profile `session`): it
    # builds from ./ui, talks to both Cognee services by container-network
    # name, keeps the phone-gate quota in a volume, and lifts the composer
    # key from the compose-only .env — the key never enters the image or
    # the Cognee containers.
    compose = _load_compose()
    session = compose["services"]["session"]
    assert session["build"] == "./ui"
    assert session["container_name"] == "chat-with-books-session"
    assert "session" in session["profiles"]
    assert "8765:8765" in [str(item) for item in session["ports"]]

    env = session["environment"]
    assert env["COGNEE_URL"] == "http://cognee:8000"
    # The container-network URL uses next-tier's CONTAINER port — 8001 is
    # only the host publish.
    assert env["NEXT_TIER_URL"] == "http://cognee-next-tier:8000"
    # The relay's phase-3 leash is tunable from the host .env too (a
    # Next-tier search can pass ten minutes; serve.py defaults to 1200).
    assert env["NEXT_TIER_TIMEOUT"] == "${NEXT_TIER_TIMEOUT:-1200}"
    assert env["SESSION_UI_HOST"] == "0.0.0.0"
    assert env["SESSION_UI_QUOTA_DB"] == "/data/usage.sqlite3"
    # The composer key rides as compose interpolation from .env, never
    # baked into the image (the Dockerfile copies only the two files).
    assert env["LLM_API_KEY"] == "${LLM_API_KEY}"
    assert any(str(item).startswith("session_quota:") for item in session["volumes"])
    assert session["depends_on"]["cognee"]["condition"] == "service_healthy"
    assert (
        session["depends_on"]["cognee-next-tier"]["condition"] == "service_healthy"
    )

    # Liveness is local (/livez) — /health proxies to Cognee and would
    # couple this container's health to another service.
    assert "livez" in str(session["healthcheck"]["test"])
    for tier in ("cognee", "cognee-next-tier"):
        assert "LLM_API_KEY" not in compose["services"][tier]["environment"]
