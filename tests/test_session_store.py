"""The Session store (T27 stage 3, GitLab #40): the Account's durable
نشست history. The store is a notebook the SHEET writes — create on the
sitting's first ask, the settled turns appended as {role, payload},
the list newest-activity-first, the resume read-only, the delete whole.
The gate's shape holds everywhere: the cookie IS the address, a
foreign Session is a 404 (never a leak), an anonymous caller is a 401,
and deleting a Session never touches the ledger — the money truth is
not the transcript's to erase."""

from pathlib import Path

from tests.helpers import (
    delete,
    get,
    post,
    stop_gate,
    with_gate,
)
from ui import ledger, session_store
from ui.ledger import record, record_size_estimate

PHONE = "09120000000"
OTHER = "09350000000"

ASK = {"text": "چهار دروازهٔ انسان ۲۵۰ ساله کدام‌اند؟"}
ANSWER = {
    "question": ASK["text"],
    "blocks": [
        {"type": "heading", "text": "دروازۀ نخست"},
        {"type": "paragraph", "text": "انسان در حرکت است…"},
    ],
    "citations": [{"reference": "انسان ۲۵۰ ساله، ص ۳۴", "passage": "…"}],
}


def _ask_and_answer(base, question_text=ASK["text"]):
    """One stored turn pair: the sheet's create-then-append shape."""
    status, session = post(
        base,
        "/sessions",
        {"book": "70143-336", "title": question_text[:42]},
        phone=PHONE,
    )
    assert status == 200
    status, _ = post(
        base,
        f"/sessions/{session['id']}/messages",
        {"role": "user", "payload": {"text": question_text}},
        phone=PHONE,
    )
    assert status == 200
    status, _ = post(
        base,
        f"/sessions/{session['id']}/messages",
        {"role": "assistant", "payload": ANSWER},
        phone=PHONE,
    )
    assert status == 200
    return session["id"]


# --- the endpoints -----------------------------------------------------------


def test_the_anonymous_caller_refuses(tmp_path):
    base, server, originals = with_gate(tmp_path, None)
    try:
        assert get(base, "/sessions")[0] == 401
        assert post(base, "/sessions", {"book": ""})[0] == 401
        assert post(base, "/sessions/1/messages", {"role": "user", "payload": {}})[0] == 401
        assert delete(base, "/sessions/1")[0] == 401
    finally:
        stop_gate(server, originals)


def test_the_sitting_opens_titled_and_lists_newest_first(tmp_path):
    base, server, originals = with_gate(tmp_path, None)
    try:
        first_id = _ask_and_answer(base, "نخستین پرسشِ نشست اول")
        second_id = _ask_and_answer(base, "پرسشِ نشست دوم")
        status, body = get(base, "/sessions", phone=PHONE)
        assert status == 200
        rows = body["sessions"]
        assert [row["id"] for row in rows][:2] == [second_id, first_id], (
            "the newest activity leads"
        )
        assert rows[0]["title"] == "پرسشِ نشست دوم"
        assert rows[0]["book"] == "70143-336"
    finally:
        stop_gate(server, originals)


def test_the_resume_read_carries_the_turns_in_order(tmp_path):
    base, server, originals = with_gate(tmp_path, None)
    try:
        session_id = _ask_and_answer(base)
        status, body = get(base, f"/sessions/{session_id}", phone=PHONE)
        assert status == 200
        assert body["title"] == ASK["text"][:42]
        roles = [message["role"] for message in body["messages"]]
        assert roles == ["user", "assistant"]
        assert body["messages"][0]["payload"]["text"] == ASK["text"]
        assert body["messages"][1]["payload"]["blocks"] == ANSWER["blocks"]
    finally:
        stop_gate(server, originals)


def test_a_foreign_session_is_a_404_never_a_leak(tmp_path):
    base, server, originals = with_gate(tmp_path, None)
    try:
        session_id = _ask_and_answer(base)
        # Another Account sees neither the read, the append, nor the delete.
        assert get(base, f"/sessions/{session_id}", phone=OTHER)[0] == 404
        assert post(
            base,
            f"/sessions/{session_id}/messages",
            {"role": "user", "payload": {"text": "نفوذ"}},
            phone=OTHER,
        )[0] == 404
        assert delete(base, f"/sessions/{session_id}", phone=OTHER)[0] == 404
        # The owner's transcript is untouched by all three attempts.
        status, body = get(base, f"/sessions/{session_id}", phone=PHONE)
        assert status == 200
        assert len(body["messages"]) == 2
    finally:
        stop_gate(server, originals)


def test_the_delete_removes_the_transcript_whole(tmp_path):
    base, server, originals = with_gate(tmp_path, None)
    try:
        session_id = _ask_and_answer(base)
        status, body = delete(base, f"/sessions/{session_id}", phone=PHONE)
        assert status == 200 and body["deleted"] is True
        assert get(base, f"/sessions/{session_id}", phone=PHONE)[0] == 404
        assert get(base, "/sessions", phone=PHONE)[1]["sessions"] == []
    finally:
        stop_gate(server, originals)


def test_a_malformed_append_refuses(tmp_path):
    base, server, originals = with_gate(tmp_path, None)
    try:
        status, session = post(
            base, "/sessions", {"book": "70143-336"}, phone=PHONE
        )
        assert status == 200
        assert (
            post(
                base,
                f"/sessions/{session['id']}/messages",
                {"role": "admin", "payload": {}},
                phone=PHONE,
            )[0]
            == 400
        )
        assert (
            post(
                base,
                f"/sessions/{session['id']}/messages",
                {"role": "user"},
                phone=PHONE,
            )[0]
            == 400
        )
    finally:
        stop_gate(server, originals)


def test_deleting_a_session_leaves_the_ledger_untouched(tmp_path):
    """The transcript is not the money's ledger: spend rows recorded
    against the sitting survive the delete — the profile's and the
    console's numbers never falsify."""
    base, server, originals = with_gate(tmp_path, None)
    try:
        record_size_estimate("09120000000@sheet.test", "ask", 900)
        record("09120000000@sheet.test", "writer", 0, 250_000, metered=True)
        session_id = _ask_and_answer(base)
        assert delete(base, f"/sessions/{session_id}", phone=PHONE)[0] == 200
        history = ledger.session_history("09120000000@sheet.test")
        assert history and history[0]["cost_toman"] > 0
    finally:
        stop_gate(server, originals)


def test_the_sheet_boots_the_list_and_reopens_the_sitting():
    """The refresh continuity (the operator's report, 2026-09-22): the
    sidebar lists on page load and the sitting reopens — the light
    source-lock holds the sheet's boot to the store's shape (the JS the
    tests cannot execute)."""
    html = (Path(session_store.__file__).parent / "index.html").read_text(
        encoding="utf-8"
    )
    # The boot block exists and lists before anything else can.
    assert 'sessionStorage.getItem("storeSessionId")' in html
    assert "rememberStoreSession" in html
    # The continuity writes ride the three state changes (create, open,
    # clear) — the boot reads what they wrote.
    assert html.count("rememberStoreSession();") >= 3
    # The boot fetches the list and reopens: newest as the fallback.
    assert 'fetch("/sessions", { cache: "no-store" })' in html
    assert "openStoreSession(target.id)" in html
    # The unanswered tail: a resumed sitting whose last turn is the
    # operator's question shows the honest note and the explicit re-ask
    # — never an automatic re-billing.
    assert "UNANSWERED_NOTE" in html and "REASK_LABEL" in html
    assert 'again.textContent = REASK_LABEL' in html
    assert 'last.role === "user"' in html
