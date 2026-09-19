"""The ask gate and its quota (ADR-0013): the sheet identifies the
Session operator by their Account — an email and a password issued by
the Admin — and the Account's attached phone gets five chats a day.
The honor-system phone gate is retired: the login cookie replaced the
phone header, and the phone number survives as legacy data attached to
the Account, still the quota stores' key."""

import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tests.conftest import REPO_ROOT

README = REPO_ROOT / "README.md"

import sys  # noqa: E402

sys.path.insert(0, str(REPO_ROOT))

from ui import quotas, serve  # noqa: E402

# A minimal non-streaming recall reply: one answer with an Evidence block.
COGNEE_REPLY = json.dumps(
    [
        {
            "text": "پاسخ آزمایشی.\n\nEvidence:\n- "
            "chunk 1 of document tarhe-kolli: \"متن نقل آزمایشی\""
        }
    ]
).encode("utf-8")


class FakeUpstream:
    """Stands in for Cognee (and, on /quoted-answer, the composer). Call
    targets are told apart by URL so one replacement serves both."""

    def __init__(self, composer_replies=()):
        self.calls = []
        self.bodies = []
        self.timeouts = []
        self.composer_replies = list(composer_replies)

    def __call__(self, request, timeout=None):
        url = request.full_url
        self.calls.append(url)
        self.timeouts.append(timeout)
        self.bodies.append(request.data.decode("utf-8") if request.data else "")
        body = self.composer_replies.pop(0) if "chat/completions" in url else COGNEE_REPLY

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

        return Response(body)


WRITER_BLOCKS = [
    {"type": "heading", "text": "عنوان"},
    {
        "type": "paragraph",
        "parts": [
            {"text": "متن پیوندده. "},
            {"quote": "متن نقل آزمایشی", "source": 0},
        ],
    },
]


def composer_replies():
    plan = json.dumps({"choices": [{"message": {"content": "طرح"}}]})
    content = json.dumps({"blocks": WRITER_BLOCKS}, ensure_ascii=False)
    writer = json.dumps({"choices": [{"message": {"content": content}}]})
    return [plan.encode("utf-8"), writer.encode("utf-8")]


# The gate-test harness (the POST client and the sheet-server
# with/stop pair) lives in tests.helpers — shared with the deep-dive
# endpoint tests, so neither test module is the other's library.
from tests.helpers import (  # noqa: E402
    GateServer,
    patch_accounts,
    post,
    stop_gate,
    unpatch_accounts,
    with_gate,
)


def test_normalize_phone_maps_farsi_and_arabic_digits():
    # The sheet normalizes before the header (headers are latin-1; Farsi
    # digits cannot ride them at all); the server normalizes again for
    # defense in depth, so both maps must agree.
    assert serve.normalize_phone("۰۹۱۲۳۴۵۶۷۸۹") == "09123456789"
    assert serve.normalize_phone("٠٩١٢٣٤٥٦٧٨٩") == "09123456789"
    assert serve.normalize_phone("+98 912 345 6789") == "989123456789"
    assert serve.normalize_phone("0912-345-6789") == "09123456789"
    assert serve.normalize_phone("12345") == ""
    assert serve.normalize_phone("") == ""
    assert serve.normalize_phone(None) == ""


def test_recall_without_a_login_is_rejected_before_touching_cognee(tmp_path):
    # The gate flip (ADR-0013): the phone header no longer authenticates
    # anything — an anonymous request is the overlay's 401 («برای ادامه
    # وارد شوید.»), and Cognee is never touched.
    upstream = FakeUpstream()
    base, server, original = with_gate(tmp_path, upstream)
    try:
        status, payload = post(
            base,
            "/api/v1/recall",
            {"searchType": "HYBRID_COMPLETION", "query": "پرسش؟"},
        )
    finally:
        stop_gate(server, original)
    assert status == 401
    assert "وارد شوید" in payload["detail"]
    assert upstream.calls == []


def test_an_attached_phone_that_normalizes_to_nothing_is_treated_as_none(tmp_path):
    # ADR-0013: the phone is legacy data attached to the Account; the
    # gate normalizes it exactly like before on every read. An Account
    # whose attached phone is not 10-13 digits has, effectively, none —
    # the phone-keyed stores cannot address it, so the 403 Farsi
    # «پیوند نخورده» answer, never a 400 and never a crash.
    upstream = FakeUpstream()
    base, server, original = with_gate(tmp_path, upstream)
    try:
        status, payload = post(
            base,
            "/api/v1/recall",
            {"searchType": "HYBRID_COMPLETION", "query": "پرسش؟"},
            phone="12345",
        )
    finally:
        stop_gate(server, original)
    assert status == 403
    assert "پیوند" in payload["detail"]
    assert upstream.calls == []


def test_a_valid_phone_passes_and_the_chat_is_recorded(tmp_path):
    upstream = FakeUpstream()
    base, server, original = with_gate(tmp_path, upstream)
    try:
        status, _ = post(
            base,
            "/api/v1/recall",
            {"searchType": "HYBRID_COMPLETION", "query": "پرسش؟"},
            phone="09123456789",
        )
    finally:
        stop_gate(server, original)
    assert status == 200
    # One upstream call — the recall itself — and one recorded chat.
    assert len(upstream.calls) == 1
    assert serve.chats_today("09123456789") == 1


def test_five_chats_is_the_daily_limit_and_other_phones_unaffected(tmp_path):
    upstream = FakeUpstream()
    base, server, original = with_gate(tmp_path, upstream)
    try:
        for _ in range(5):
            serve.record_chat("09120000001")
        status_full, payload = post(
            base,
            "/api/v1/recall",
            {"searchType": "HYBRID_COMPLETION", "query": "پرسش؟"},
            phone="09120000001",
        )
        status_other, _ = post(
            base,
            "/api/v1/recall",
            {"searchType": "HYBRID_COMPLETION", "query": "پرسش؟"},
            phone="09120000002",
        )
    finally:
        stop_gate(server, original)
    assert status_full == 429
    assert "امروز" in payload["detail"]
    assert status_other == 200
    # Only the unaffected phone reached Cognee.
    assert len(upstream.calls) == 1


def test_gate_rejection_reads_the_request_body_before_answering(tmp_path):
    # A gate rejection that answers without reading the request body
    # closes the socket over unread bytes; the kernel answers RST, not
    # FIN, and the client can lose the 429 it was owed (WinError 10054,
    # flaking the suite ~15% of runs, 2026-09-10). A large body makes
    # the race near-certain, so the rejection must still arrive.
    upstream = FakeUpstream()
    base, server, original = with_gate(tmp_path, upstream)
    try:
        for _ in range(5):
            serve.record_chat("09120000009")
        status, payload = post(
            base,
            "/api/v1/recall",
            {"searchType": "HYBRID_COMPLETION", "query": "پرسش؟", "pad": "x" * 200000},
            phone="09120000009",
        )
    finally:
        stop_gate(server, original)
    assert status == 429
    assert "امروز" in payload["detail"]
    assert upstream.calls == []


def test_yesterday_chats_do_not_count_against_today(tmp_path):
    quotas.QUOTA_DB = tmp_path / "usage.sqlite3"
    assert serve.chats_today("09120000003") == 0  # also creates the table
    conn = sqlite3.connect(str(quotas.QUOTA_DB))
    try:
        conn.execute(
            "INSERT INTO chats (phone, day) VALUES (?, ?)",
            ("09120000003", "2000-01-01"),
        )
        conn.commit()
    finally:
        conn.close()
    assert serve.chats_today("09120000003") == 0


# What the guard returns for WRITER_BLOCKS: text is stripped, every kept
# quote carries the labels of the passage it claims ("" — the locator has
# no page markers), and the heading + one quoting paragraph clear the
# swap threshold.
WRITER_KEPT = [
    {"type": "heading", "text": "عنوان"},
    {
        "type": "paragraph",
        "parts": [
            {"text": "متن پیوندده."},
            {
                "quote": "متن نقل آزمایشی",
                "source": 0,
                "pages_label": "",
                "first_page_label": "",
                "book_label": "طرح کلی اندیشۀ اسلامی در قرآن",
            },
        ],
    },
]


def test_quoted_answer_needs_a_phone_that_chatted_today(tmp_path):
    upstream = FakeUpstream(composer_replies())
    base, server, original = with_gate(tmp_path, upstream)
    payload = {
        "question": "پرسش؟",
        "answer": "پیش‌نویس",
        "sources": [
            {
                "reference": "chunk 1 of document tarhe-kolli",
                "passage": "متن نقل آزمایشی",
            }
        ],
    }
    try:
        status_none, _ = post(base, "/quoted-answer", payload, phone="09120000004")
        calls_after_rejected = list(upstream.calls)
        serve.record_chat("09120000004")
        status_ok, body = post(base, "/quoted-answer", payload, phone="09120000004")
    finally:
        stop_gate(server, original)
    # Without a chat today the composer never runs.
    assert status_none == 429
    assert calls_after_rejected == []
    assert status_ok == 200
    assert body["blocks"] == WRITER_KEPT


def test_next_tier_recall_needs_a_phone_that_chatted_today(tmp_path):
    # Phase 3 belongs to the chat phase 1 recorded — the same gate shape
    # as phase 2: a phone with no chat today never reaches the service.
    upstream = FakeUpstream()
    base, server, original = with_gate(tmp_path, upstream)
    try:
        status_none, payload = post(
            base, "/next-tier-recall", {"query": "پرسش؟"}, phone="09120000005"
        )
        calls_after_rejected = list(upstream.calls)
        serve.record_chat("09120000005")
        status_ok, body = post(
            base, "/next-tier-recall", {"query": "پرسش؟"}, phone="09120000005"
        )
    finally:
        stop_gate(server, original)
    assert status_none == 429
    assert calls_after_rejected == []
    assert status_ok == 200
    assert body == json.loads(COGNEE_REPLY)


def test_next_tier_recall_pins_the_search_shape_and_the_second_service(tmp_path):
    # The graph-retrieval answer must arrive with the recorded Next-tier
    # shape — the sheet sends only the query; the relay pins the rest and
    # rides the second Cognee service (port 8001), never the first-answer
    # path on 8000.
    upstream = FakeUpstream()
    base, server, original = with_gate(tmp_path, upstream)
    try:
        serve.record_chat("09120000006")
        status, _ = post(
            base, "/next-tier-recall", {"query": "پرسش؟"}, phone="09120000006"
        )
    finally:
        stop_gate(server, original)
    assert status == 200
    assert upstream.calls[-1].startswith("http://127.0.0.1:8001/api/v1/recall")
    assert json.loads(upstream.bodies[-1]) == {
        "searchType": "GRAPH_COMPLETION_COT",
        "query": "پرسش؟",
        "datasets": ["tarhe-kolli", "70143-336"],
        "includeReferences": True,
    }


def test_next_tier_recall_never_counts_a_chat(tmp_path):
    # Phase 3 neither counts nor checks the daily limit — like phase 2,
    # it belongs to a chat that already started (the fifth chat's own
    # phase 3 must pass too).
    upstream = FakeUpstream()
    base, server, original = with_gate(tmp_path, upstream)
    try:
        serve.record_chat("09120000007")
        post(base, "/next-tier-recall", {"query": "پرسش؟"}, phone="09120000007")
        post(base, "/next-tier-recall", {"query": "پرسش؟"}, phone="09120000007")
    finally:
        stop_gate(server, original)
    assert serve.chats_today("09120000007") == 1


def test_next_tier_recall_rejects_an_empty_query(tmp_path):
    upstream = FakeUpstream()
    base, server, original = with_gate(tmp_path, upstream)
    try:
        serve.record_chat("09120000008")
        status, _ = post(
            base, "/next-tier-recall", {"query": "   "}, phone="09120000008"
        )
    finally:
        stop_gate(server, original)
    assert status == 400
    assert upstream.calls == []


class SlowUpstreamHandler(BaseHTTPRequestHandler):
    """Answers /api/v1/recall only after `delay` seconds — the socket-
    timeout mechanics of the relay's urlopen run for real against it."""

    delay = 2.0

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", "0") or "0"))
        threading.Event().wait(self.delay)
        body = json.dumps([{"text": "پاسخ سطح بعدی"}]).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_next_tier_relay_rides_its_own_leash(tmp_path):
    # The Next-tier search runs four chain-of-thought rounds and can pass
    # ten minutes (live logs, 2026-09-11) — the shared 600s proxy leash
    # gave up on a search the second service had already finished. Phase
    # 3 gets its own, longer leash; phase 1 keeps the shared one.
    assert serve.NEXT_TIER_TIMEOUT == 1200
    upstream = FakeUpstream()
    base, server, original = with_gate(tmp_path, upstream)
    try:
        serve.record_chat("09120000011")
        post(base, "/next-tier-recall", {"query": "پرسش؟"}, phone="09120000011")
        post(
            base,
            "/api/v1/recall",
            {"searchType": "HYBRID_COMPLETION", "query": "پرسش؟"},
            phone="09120000011",
        )
    finally:
        stop_gate(server, original)
    assert upstream.timeouts[-2] == serve.NEXT_TIER_TIMEOUT
    assert upstream.timeouts[-1] == serve.PROXY_TIMEOUT


def test_next_tier_relay_survives_a_slow_search(tmp_path, monkeypatch):
    # A search that finishes inside the leash arrives whole — the exact
    # scenario the 2026-09-11 timeout cut.
    SlowUpstreamHandler.delay = 2.0
    slow = ThreadingHTTPServer(("127.0.0.1", 0), SlowUpstreamHandler)
    thread = threading.Thread(target=slow.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(
        serve, "NEXT_TIER_URL", f"http://127.0.0.1:{slow.server_address[1]}"
    )
    monkeypatch.setattr(serve, "NEXT_TIER_TIMEOUT", 5)
    quotas.QUOTA_DB = tmp_path / "usage.sqlite3"
    patch_accounts(tmp_path)
    serve.record_chat("09120000012")
    sheet = GateServer(("127.0.0.1", 0), serve.SessionHandler)
    threading.Thread(target=sheet.serve_forever, daemon=True).start()
    try:
        status, body = post(
            base=f"http://127.0.0.1:{sheet.server_address[1]}",
            path="/next-tier-recall",
            payload={"query": "پرسش؟"},
            phone="09120000012",
        )
    finally:
        sheet.shutdown()
        sheet.server_close()
        slow.shutdown()
        slow.server_close()
        unpatch_accounts()
    assert status == 200
    assert body == [{"text": "پاسخ سطح بعدی"}]


def test_next_tier_relay_reports_a_timed_out_search(tmp_path, monkeypatch):
    # Past its leash the relay still answers — the 504 notice — instead
    # of hanging the phase-3 tab forever.
    SlowUpstreamHandler.delay = 2.0
    slow = ThreadingHTTPServer(("127.0.0.1", 0), SlowUpstreamHandler)
    thread = threading.Thread(target=slow.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(
        serve, "NEXT_TIER_URL", f"http://127.0.0.1:{slow.server_address[1]}"
    )
    monkeypatch.setattr(serve, "NEXT_TIER_TIMEOUT", 1)
    quotas.QUOTA_DB = tmp_path / "usage.sqlite3"
    patch_accounts(tmp_path)
    serve.record_chat("09120000013")
    sheet = GateServer(("127.0.0.1", 0), serve.SessionHandler)
    threading.Thread(target=sheet.serve_forever, daemon=True).start()
    try:
        status, body = post(
            base=f"http://127.0.0.1:{sheet.server_address[1]}",
            path="/next-tier-recall",
            payload={"query": "پرسش؟"},
            phone="09120000013",
        )
    finally:
        sheet.shutdown()
        sheet.server_close()
        slow.shutdown()
        slow.server_close()
        unpatch_accounts()
    assert status == 504
    assert body == {"detail": "next-tier unreachable: timed out"}


def test_serve_pins_the_next_tier_relay():
    text = (REPO_ROOT / "ui" / "serve.py").read_text(encoding="utf-8")
    assert "NEXT_TIER_URL" in text
    # The second service's URL is defined in the dive module (the dive's
    # searchers and the relay read the same pin) — still from env with
    # the recorded default, never hardcoded past it.
    dive_text = (REPO_ROOT / "ui" / "dive.py").read_text(encoding="utf-8")
    assert 'os.environ.get("NEXT_TIER_URL", "http://127.0.0.1:8001")' in dive_text
    assert "GRAPH_COMPLETION_COT" in text
    assert "/next-tier-recall" in text


def test_sheet_sends_the_phone_and_shows_the_gate():
    html = (REPO_ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    assert 'id="phone"' in html
    assert "X-Session-Phone" in html
    assert "localStorage" in html
    assert 'inputmode="tel"' in html
    # Headers are latin-1: the sheet must normalize Farsi digits itself.
    assert "toAsciiDigits" in html


def test_serve_pins_the_gate_shape():
    text = (REPO_ROOT / "ui" / "serve.py").read_text(encoding="utf-8")
    quotas_text = (REPO_ROOT / "ui" / "quotas.py").read_text(encoding="utf-8")
    accounts_text = (REPO_ROOT / "ui" / "accounts.py").read_text(encoding="utf-8")
    # The gate flip (ADR-0013): no client header authenticates anything —
    # the login cookie does, and the identity resolver is the one door.
    assert "X-Session-Phone" not in text
    assert "cwb_auth" in text
    assert "resolve_identity" in text
    assert "AUTH_SECRET" in text
    assert "HttpOnly" in text
    assert "SESSION_UI_HOST" in text
    # The limit and its database live in the quota module now — still
    # pinned in source, never via env.
    assert "DAILY_CHAT_LIMIT = 5" in quotas_text
    assert "usage.sqlite3" in quotas_text
    # The Account store follows the same pattern: its own file, its
    # env-overridable path, the hash pin in source.
    assert "PBKDF2_ITERATIONS = 240_000" in accounts_text
    # The retired swap noun stays out of the gate comment (round-2
    # review, 2026-09-12): the 5th chat's own Quoted answer must pass.
    assert "the 5th chat's own Quoted answer" in quotas_text
    assert "swap must pass" not in text + quotas_text


def test_readme_records_the_account_gate():
    # The gate flip (ADR-0013) rewrote the README's gate chapter: the
    # login page replaced the honor-system phone gate, the Account is
    # issued by the Admin, and the phone survives as the quota's key.
    section = (
        README.read_text(encoding="utf-8")
        .split("### Accounts & login (ADR-0013)", 1)[1]
        .split("\n### ", 1)[0]
    )
    assert "POST /auth/login" in section
    assert "cwb_auth" in section
    assert "X-Session-Phone" not in section
    assert "no longer authenticates anything" in section
    # The 5-ask daily quota stays, per Account through the attached phone.
    assert "five chats a day" in section
    assert "usage.sqlite3" in section
    # No signup page exists; the first Admin comes from compose env
    # (T26: the bootstrap seed command is retired — README and the
    # deployment agree), and the console issues and tops up, audited.
    assert "ADMIN_EMAIL" in section
    assert "bootstrap_admin" not in section
    assert "/admin/topup" in section
    assert "audit log" in section
    # The cookie rides on every chat-owned call — the picker and the
    # research turns' polls included, not only the ask and phase 2
    # (full-spec review, 2026-09-12; ADR-0008).
    assert "/quote-selection" in section
    assert "/research/turn" in section
    # The retired swap vocabulary stays retired (round-2 review,
    # 2026-09-12): the tabbed sheet lands the document in phase 2's tab
    # or not at all, so the fifth chat's own Quoted answer and research
    # conversation must pass — no swap noun.
    assert "the fifth chat's own Quoted answer and research conversation must pass" in section
    assert "swap" not in section
