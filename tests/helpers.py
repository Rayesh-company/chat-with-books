"""Shared sheet-test helpers: the POST client the gate tests read, and
the real sheet server behind a patched quota DB and a fake upstream.
Lives outside both test modules so neither is the other's library."""

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from tests.conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT))

from ui import serve  # noqa: E402


def post(base, path, payload, phone=None):
    headers = {"Content-Type": "application/json"}
    if phone is not None:
        headers["X-Session-Phone"] = phone
    request = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


def get(base, path, phone=None):
    """One GET with the optional session phone; (status, payload)."""
    headers = {}
    if phone is not None:
        headers["X-Session-Phone"] = phone
    request = urllib.request.Request(base + path, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


def wait_job_done(job_id, timeout=15):
    """Wait for a dive job to settle and its worker thread to exit.

    Every test that starts a dive must end here (or abort the job) before
    stop_gate: a worker still running past the patch restore would call
    the real urlopen."""
    deadline = time.monotonic() + timeout
    job = serve.DIVE_REGISTRY[job_id]
    while time.monotonic() < deadline:
        if job.state in serve.DIVE_TERMINAL_STATES and job.done.is_set():
            return job
        time.sleep(0.02)
    raise AssertionError(f"dive job {job_id} never settled: state={job.state}")


class GateServer(ThreadingHTTPServer):
    # ThreadingHTTPServer sets daemon_threads=True, so plain shutdown()
    # can return while a handler thread is still mid-request — and the
    # next test would restore patches under it. Non-daemon threads plus
    # server_close() (ThreadingMixIn joins them) make stop_gate a full
    # barrier.
    daemon_threads = False


def with_gate(tmp_path, upstream):
    """Run a real sheet server against a patched quota DB and upstream;
    return (base URL, server, original urlopen). The tests' finally blocks
    must shut the server down and restore both patches. The dive registry
    starts empty — a server restart is what empties it in production."""
    serve.QUOTA_DB = tmp_path / "usage.sqlite3"
    serve.DIVE_REGISTRY.clear()
    original = serve.urlopen
    serve.urlopen = upstream
    os.environ["LLM_API_KEY"] = "test-key"
    server = GateServer(("127.0.0.1", 0), serve.SessionHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return f"http://127.0.0.1:{server.server_address[1]}", server, original


def stop_gate(server, original):
    server.shutdown()
    server.server_close()
    serve.urlopen = original
    os.environ.pop("LLM_API_KEY", None)
