"""Shared sheet-test helpers: the POST client the gate tests read, the
real sheet server behind a patched quota DB and a fake upstream, and the
fake-upstream seam (FakeResponse plus the canned-reply runner) the
picker and composer unit tests read. Lives outside the test modules so
no test module is another's library."""

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

from ui import composer, dive, quotas, serve  # noqa: E402


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


# The Book's text layer separates words with real backspace characters
# and carries kashida and ZWNJ — the noise the verbatim guard collapses
# (recorded shape, tests.test_quoted_answer).
BS = "\b"
POOL_PASSAGE = (
    f"سـخن{BS}در{BS}این{BS}اسـت؛ "
    f"قرآن{BS}کتابی{BS}اسـت{BS}برای{BS}زندگی؛ "
    f"انسان{BS}در{BS}جامعه{BS}می‌زیید؛ "
    f"عـدل{BS}اساس{BS}اجتماع{BS}اسـت."
)
OTHER_PASSAGE = "این جمله از قطعهٔ دیگری است."

# The Evidence pool the picker seam runs against: the first passage
# carries page markers, the second does not.
POOL = [
    {
        "reference": "chunk 101 of document tarhe-kolli (pages 740-745)",
        "passage": POOL_PASSAGE,
    },
    {
        "reference": "chunk 29 of document tarhe-kolli",
        "passage": OTHER_PASSAGE,
    },
]


class FakeResponse:
    """Stands in for a urlopen reply: a readable body in a with-block."""

    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def run_call_with_replies(call, replies):
    """Run one composer-shaped callable against per-call canned replies;
    return (its result, captured payloads, captured timeouts) — the
    unit-level fake-upstream seam under the real sheet server's. Each
    queued reply is POSTed in order (a raised Exception stands in for a
    failed call) against the patched composer urlopen — the owning
    module's seam — with the test key set."""
    captured = {"payloads": [], "timeouts": []}
    queue = [
        reply.encode("utf-8") if isinstance(reply, str) else reply
        for reply in replies
    ]

    def fake_urlopen(request, timeout=None):
        captured["payloads"].append(json.loads(request.data.decode("utf-8")))
        captured["timeouts"].append(timeout)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return FakeResponse(item)

    original = composer.urlopen
    composer.urlopen = fake_urlopen
    os.environ["LLM_API_KEY"] = "test-key"
    try:
        result = call()
    finally:
        composer.urlopen = original
        del os.environ["LLM_API_KEY"]
    return result, captured


def run_pick_with_replies(replies, question="پرسش؟", sources=POOL):
    """Run pick_quote_selection against per-call canned replies; return
    (selections, captured payloads, captured timeouts) — the unit-level
    fake-upstream seam under the real sheet server's."""
    selections, captured = run_call_with_replies(
        lambda: serve.pick_quote_selection(question, sources), replies
    )
    return selections, captured


class FakeComposer:
    """Stands in for the composer endpoint. Captures every call and
    scripts the replies in order."""

    def __init__(self, replies=()):
        self.calls = []
        self.payloads = []
        self.timeouts = []
        self.replies = list(replies)

    def __call__(self, request, timeout=None):
        self.calls.append(request.full_url)
        self.payloads.append(json.loads(request.data.decode("utf-8")))
        self.timeouts.append(timeout)
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item

        class Response:
            status = 200
            headers = {"Content-Type": "application/json"}

            def __init__(self, body):
                self._body = body

            def read(self):
                return self._body

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return Response(item)


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
    must shut the server down and restore both patches. The quota DB is
    patched at its owning module (ui.quotas reads it per connection), the
    fake stands at every owning module's urlopen seam — the facade's own
    relay/proxy, the composer's, and the dive's (one upstream told apart
    by URL, as before). The dive registry starts empty — a server
    restart is what empties it in production."""
    quotas.QUOTA_DB = tmp_path / "usage.sqlite3"
    serve.DIVE_REGISTRY.clear()
    originals = [(module, module.urlopen) for module in (serve, composer, dive)]
    for module, _ in originals:
        module.urlopen = upstream
    os.environ["LLM_API_KEY"] = "test-key"
    server = GateServer(("127.0.0.1", 0), serve.SessionHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return f"http://127.0.0.1:{server.server_address[1]}", server, originals


def stop_gate(server, original):
    server.shutdown()
    server.server_close()
    for module, urlopen_original in original:
        module.urlopen = urlopen_original
    os.environ.pop("LLM_API_KEY", None)
