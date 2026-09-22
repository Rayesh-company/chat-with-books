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

from ui import (  # noqa: E402
    accounts,
    composer,
    dive,
    picker,
    quotas,
    recall_more,
    research,
    research_store,
    serve,
    session_store,
)


def delete(base, path, phone=None):
    """One DELETE; (status, payload) — the Session store's verb (T27
    stage 3). The cookie translation is post()'s exactly."""
    headers = {}
    if phone is not None:
        headers["Cookie"] = _login_cookie(base, phone)
    request = urllib.request.Request(base + path, headers=headers, method="DELETE")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


def post(base, path, payload, phone=None):
    """One POST; (status, payload). The gate tests speak the phone
    dialect and the server now speaks Accounts (ADR-0013), so a phone
    is translated here into that Account's login cookie — every call
    site keeps working unchanged, and the wire shape is the cookie the
    browser would carry. phone=None stays anonymous (the 401 shape)."""
    headers = {"Content-Type": "application/json"}
    if phone is not None:
        headers["Cookie"] = _login_cookie(base, phone)
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
    """One GET with the optional session phone — translated into the
    Account's login cookie exactly like post(); (status, payload)."""
    headers = {}
    if phone is not None:
        headers["Cookie"] = _login_cookie(base, phone)
    request = urllib.request.Request(base + path, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


# --- the Account seeding (ADR-0013) ------------------------------------------
# One Account per test phone: email derived from the phone, the fixed
# test password, the phone attached, role operator — plus one Admin per
# patched DB (minted in patch_accounts). The seed runs at call time
# against the patched ACCOUNTS_DB, because no fixture can know which
# phones a test will pick; the store dedupes (an issued Account is
# never re-issued).

TEST_PASSWORD = "cwb-test-password"
ADMIN_EMAIL = "admin@sheet.test"


def account_email_for_phone(phone: str) -> str:
    return f"{phone}@sheet.test"


TEST_BALANCE_TOMAN = 1_000_000


def ensure_account(phone: str) -> str:
    """The seeded operator's Account carries a generous Balance — the
    house tests exercise the pipeline, not the prepaid stop; the tests
    that pin the stop drain their own Account explicitly and stay
    drained: the top-up rides CREATION only, so a re-ensure never
    refills a drained Account. Returns the Account's EMAIL — the key
    every store takes since T21."""
    email = account_email_for_phone(phone)
    existed = accounts.account_by_email(email) is not None
    accounts.create_account(email, TEST_PASSWORD, phone=phone, role="operator")
    if not existed:
        accounts.adjust_balance(email, TEST_BALANCE_TOMAN)
    return email


# Login cookies per (base, phone) — the token lives twelve hours, far
# past any test; the cache is cleared when the patches are.
_COOKIE_CACHE = {}


def _login_cookie(base: str, phone: str) -> str:
    ensure_account(phone)
    cached = _COOKIE_CACHE.get((base, phone))
    if cached is not None:
        return cached
    request = urllib.request.Request(
        base + "/auth/login",
        data=json.dumps(
            {
                "email": account_email_for_phone(phone),
                "password": TEST_PASSWORD,
            }
        ).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        set_cookie = response.headers.get("Set-Cookie", "")
    cookie = set_cookie.split(";", 1)[0].strip()
    if not cookie:
        raise AssertionError(f"login for {phone} set no cookie")
    _COOKIE_CACHE[(base, phone)] = cookie
    return cookie


def raw_post(base: str, path: str, payload, cookie: str = None):
    """One POST with an explicit cookie header value (not the phone
    translation) — the auth tests' own client, so tampered/expired
    tokens can ride exactly the header a browser would send."""
    headers = {"Content-Type": "application/json"}
    if cookie is not None:
        headers["Cookie"] = cookie
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


def raw_get(base: str, path: str, cookie: str = None):
    """One GET with an explicit cookie header value — see raw_post."""
    headers = {"Cookie": cookie} if cookie is not None else {}
    request = urllib.request.Request(base + path, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


def cookie_for(base: str, email: str, password: str = None) -> str:
    """The cwb_auth cookie for an arbitrary Account (the Admin's tests
    use this for /auth/accounts), via one real /auth/login."""
    request = urllib.request.Request(
        base + "/auth/login",
        data=json.dumps(
            {
                "email": email,
                "password": TEST_PASSWORD if password is None else password,
            }
        ).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        cookie = response.headers.get("Set-Cookie", "").split(";", 1)[0].strip()
    if not cookie:
        raise AssertionError(f"login for {email} set no cookie")
    return cookie


def patch_accounts(tmp_path) -> None:
    """Patch the Account store to the test's own file (the quotas.py
    pattern: the module attribute is the seam) and pin the auth secret
    for the whole process — the sheet server and the test client must
    sign tokens with the same secret. Mints the Admin (no phone
    attached: the Admin issues Accounts, the Session operator's
    Accounts carry the phones)."""
    accounts.ACCOUNTS_DB = tmp_path / "accounts.sqlite3"
    os.environ["AUTH_SECRET"] = "test-secret"
    _COOKIE_CACHE.clear()
    accounts.create_account(ADMIN_EMAIL, TEST_PASSWORD, phone=None, role="admin")


def unpatch_accounts() -> None:
    os.environ.pop("AUTH_SECRET", None)
    _COOKIE_CACHE.clear()


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
    failed call) against the patched composer and picker urlopens — the
    owning modules' seams — with the test key set."""
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

    originals = [(module, module.urlopen) for module in (composer, picker)]
    for module, _ in originals:
        module.urlopen = fake_urlopen
    os.environ["LLM_API_KEY"] = "test-key"
    try:
        result = call()
    finally:
        for module, urlopen_original in originals:
            module.urlopen = urlopen_original
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


def wait_turn_done(turn_id, timeout=15):
    """Wait for a research turn to settle and its worker thread to exit.

    Every test that starts a turn must end here (or abort the turn)
    before stop_gate: a worker still running past the patch restore
    would call the real urlopen. The lookup goes through the engine's
    own (find_turn) — a settled turn is reaped from the registry (T11)
    but stays answerable through the recent-settled ring."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        turn = research.find_turn(turn_id)
        if (
            turn is not None
            and turn.state in research.TURN_TERMINAL_STATES
            and turn.done.is_set()
        ):
            return turn
        time.sleep(0.02)
    raise AssertionError(f"research turn {turn_id} never settled")


class GateServer(ThreadingHTTPServer):
    # ThreadingHTTPServer sets daemon_threads=True, so plain shutdown()
    # can return while a handler thread is still mid-request — and the
    # next test would restore patches under it. Non-daemon threads plus
    # server_close() (ThreadingMixIn joins them) make stop_gate a full
    # barrier.
    daemon_threads = False


def with_gate(tmp_path, upstream):
    """Run a real sheet server against a patched quota DB, accounts DB,
    research DB, and upstream; return (base URL, server, original
    urlopen). The tests' finally blocks must shut the server down and
    restore all patches. The quota DB is patched at its owning module
    (ui.quotas reads it per connection), the accounts DB and the auth
    secret through patch_accounts (the sheet server and the test
    client must sign tokens alike), the research store's DB at
    ui.research_store, and the fake stands at every owning module's
    urlopen seam — the facade's own relay/proxy, the composer's, the
    picker's, the dive kernel's, and the research engine's (one
    upstream told apart by URL). The turn registry starts empty — a
    server restart is what empties it in production."""
    quotas.QUOTA_DB = tmp_path / "usage.sqlite3"
    research_store.RESEARCH_DB = tmp_path / "research.sqlite3"
    session_store.SESSIONS_DB = tmp_path / "sessions.sqlite3"
    patch_accounts(tmp_path)
    research.RESEARCH_REGISTRY.clear()
    originals = [
        (module, module.urlopen)
        for module in (serve, composer, picker, dive, research, recall_more)
    ]
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
    unpatch_accounts()
    os.environ.pop("LLM_API_KEY", None)
