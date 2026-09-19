"""The deploy that refuses to lie (T17, GitLab #18): the smoke's three
checks against a fake fetcher — no test touches the network — and the
sheet's embedding drift guard. The 2026-09-12 incident is the shape of the
locks: health green, searches dead, nothing naming why."""

import importlib.util
import inspect
import sys

from tests.conftest import REPO_ROOT
from ui import serve

SMOKE_SCRIPT = REPO_ROOT / "scripts" / "smoke.py"

spec = importlib.util.spec_from_file_location("smoke", SMOKE_SCRIPT)
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


def fake_fetch(responses):
    """A fetcher canned by URL suffix → (status, body); every call is
    recorded for the UTF-8 and routing assertions."""
    calls = []

    def fetch(method, url, payload=None, timeout=120):
        calls.append({"method": method, "url": url, "payload": payload})
        for suffix, (status, body) in responses.items():
            if url.endswith(suffix):
                return status, body
        raise AssertionError(f"the smoke called an unexpected URL: {method} {url}")

    fetch.calls = calls
    return fetch


GREEN = {
    "/livez": (200, "ok"),
    "/health": (200, '{"status":"ok"}'),
    "/api/v1/search": (200, '[{"chunk":"one"},{"chunk":"two"},{"chunk":"three"}]'),
}


def test_green_smoke_names_all_three_checks():
    ok, lines = smoke.run_checks("http://sheet", "http://cognee", fetch=fake_fetch(GREEN))

    assert ok is True
    joined = "\n".join(lines)
    for step in ("livez", "health", "book search"):
        assert step in joined
    assert "3 hits" in joined
    assert lines[-1] == "ok   book search (3 hits — the embedder, pgvector, and the data are alive)"


def test_the_probe_rides_as_real_farsi_over_the_book_set():
    """The smoke's own question is the Farsi path it exists to prove: real
    UTF-8 bytes (never \\u escapes), the fixed Book set, CHUNKS against
    Cognee directly — no LLM call, no chat spent from the quota."""
    fetcher = fake_fetch(GREEN)
    smoke.run_checks("http://sheet", "http://cognee", fetch=fetcher)

    search_call = next(c for c in fetcher.calls if c["url"].endswith("/api/v1/search"))
    assert search_call["payload"]["query"] == smoke.FASSI_PROBE
    assert search_call["payload"]["searchType"] == "CHUNKS"
    assert search_call["payload"]["datasets"] == ["tarhe-kolli", "70143-336"]
    encoded = smoke.encode_payload(search_call["payload"])
    assert encoded.decode("utf-8") == '{"searchType": "CHUNKS", "query": "' + smoke.FASSI_PROBE + '", "datasets": ["tarhe-kolli", "70143-336"], "topK": 3}'


def test_a_dead_sheet_stops_the_run_before_health():
    ok, lines = smoke.run_checks(
        "http://sheet",
        "http://cognee",
        fetch=fake_fetch({"/livez": (503, "down")}),
    )

    assert ok is False
    assert len(lines) == 1 and "livez" in lines[0]


def test_failed_health_names_health():
    ok, lines = smoke.run_checks(
        "http://sheet",
        "http://cognee",
        fetch=fake_fetch({"/livez": (200, "ok"), "/health": (502, "cognee unreachable")}),
    )

    assert ok is False
    assert "health" in lines[-1] and "502" in lines[-1]


def test_a_dimension_error_names_the_drift_signature():
    """The incident's own shape: HTTP 500 with the dimensions complaint —
    the smoke says what it is instead of a generic failure."""
    ok, lines = smoke.run_checks(
        "http://sheet",
        "http://cognee",
        fetch=fake_fetch({"/livez": (200, "ok"), "/health": (200, "ok"), "/api/v1/search": (500, "expected 1536 dimensions, not 3072")}),
    )

    assert ok is False
    assert "dimension" in lines[-1] and "drift" in lines[-1]


def test_zero_hits_is_a_failure_never_a_pass():
    ok, lines = smoke.run_checks(
        "http://sheet",
        "http://cognee",
        fetch=fake_fetch({"/livez": (200, "ok"), "/health": (200, "ok"), "/api/v1/search": (200, "[]")}),
    )

    assert ok is False
    assert "0 hits" in lines[-1]


def test_a_connection_refusal_is_a_named_failure():
    import urllib.error

    def refusing(method, url, payload=None, timeout=120):
        raise urllib.error.URLError("Connection refused")

    ok, lines = smoke.run_checks("http://sheet", "http://cognee", fetch=refusing)

    assert ok is False
    assert "Connection refused" in lines[-1]


# --- the drift guard ---


def test_guard_passes_when_pins_are_absent_or_frozen(monkeypatch):
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("EMBEDDING_DIMENSIONS", raising=False)
    serve.check_embedding_pin()  # a bare dev run cannot contradict the data

    monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-3-large")
    monkeypatch.setenv("EMBEDDING_DIMENSIONS", "3072")
    serve.check_embedding_pin()  # the frozen pins start


def test_guard_refuses_a_drifted_model_in_farsi(monkeypatch):
    monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-3-small")
    monkeypatch.setenv("EMBEDDING_DIMENSIONS", "3072")

    try:
        serve.check_embedding_pin()
        raised = None
    except SystemExit as exit_error:
        raised = exit_error

    assert raised is not None, "a drifted model must refuse the start"
    message = str(raised.code)
    assert "EMBEDDING_MODEL=text-embedding-3-small" in message
    assert "text-embedding-3-large" in message, "the fix must be named"
    assert "نمی‌خواند" in message, "the operator's message is Farsi"


def test_guard_refuses_drifted_dimensions_alone(monkeypatch):
    monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-3-large")
    monkeypatch.setenv("EMBEDDING_DIMENSIONS", "1536")

    try:
        serve.check_embedding_pin()
        raised = None
    except SystemExit as exit_error:
        raised = exit_error

    assert raised is not None, "a drifted dimension count must refuse the start"
    assert "1536" in str(raised.code)


def test_main_runs_the_guard_before_anything_binds():
    source = inspect.getsource(serve.main)
    assert "check_embedding_pin()" in source, "the guard is main's first act — a drifted stack never binds"
