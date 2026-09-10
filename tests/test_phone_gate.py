"""The phone gate: the public sheet identifies a Customer by a phone
number, and each number gets five chats a day (PM call, 2026-09-10, for
the VPS deploy). Honor-system — no SMS verification; the gate exists to
stop casual credit-burn, not a determined caller."""

import json
import os
import sqlite3
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from tests.conftest import REPO_ROOT

README = REPO_ROOT / "README.md"

import sys  # noqa: E402

sys.path.insert(0, str(REPO_ROOT))

from ui import serve  # noqa: E402

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
        self.composer_replies = list(composer_replies)

    def __call__(self, request, timeout=None):
        url = request.full_url
        self.calls.append(url)
        if "chat/completions" in url:
            body = self.composer_replies.pop(0)
        else:
            body = COGNEE_REPLY

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
    must shut the server down and restore both patches."""
    serve.QUOTA_DB = tmp_path / "usage.sqlite3"
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


def test_recall_without_a_phone_is_rejected_before_touching_cognee(tmp_path):
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
    assert status == 400
    assert "شماره" in payload["detail"]
    assert upstream.calls == []


def test_recall_with_a_too_short_phone_is_rejected(tmp_path):
    upstream = FakeUpstream()
    base, server, original = with_gate(tmp_path, upstream)
    try:
        status, _ = post(
            base,
            "/api/v1/recall",
            {"searchType": "HYBRID_COMPLETION", "query": "پرسش؟"},
            phone="12345",
        )
    finally:
        stop_gate(server, original)
    assert status == 400
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
    serve.QUOTA_DB = tmp_path / "usage.sqlite3"
    assert serve.chats_today("09120000003") == 0  # also creates the table
    conn = sqlite3.connect(str(serve.QUOTA_DB))
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
    assert "X-Session-Phone" in text
    assert "DAILY_CHAT_LIMIT = 5" in text
    assert "SESSION_UI_HOST" in text
    assert "usage.sqlite3" in text


def test_readme_records_the_phone_gate():
    text = README.read_text(encoding="utf-8")
    assert "X-Session-Phone" in text
    assert "five chats a day" in text
    assert "no SMS verification" in text
