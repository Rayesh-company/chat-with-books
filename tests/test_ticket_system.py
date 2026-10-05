"""The ticket system (the ticket-system map, 2026-10-05): the feedback
loop from the sheet's chip to the manager's inbox. The store's own
discipline (floors, soft cap, ownership silence, the reopen window),
then the API through the real sheet server — the snapshot is the
SERVER's word (built from the Session store, never the client's), the
attachment is sniffed from magic bytes, and every transition lands on
the append-only audit log."""

import base64
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from tests.helpers import (
    ADMIN_EMAIL,
    FakeComposer,
    cookie_for,
    ensure_account,
    get,
    post,
    stop_gate,
    with_gate,
)
from ui import audit, ticket_store

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
)  # the signature plus padding — the sniff trusts the magic, not the body


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _post_form(base, path, form, cookie):
    """One form-encoded POST that does NOT follow the 303 — the inbox's
    forms answer like the console's, and the test pins the redirect
    itself, not the page it lands on."""
    data = "&".join(
        f"{k}={urllib.request.quote(str(v))}" for k, v in form.items()
    ).encode("utf-8")
    request = urllib.request.Request(
        base + path,
        data=data,
        headers={"Cookie": cookie, "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with _OPENER.open(request, timeout=10) as response:
            return response.status, response.headers.get("Location", "")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Location", "")


def _get_bytes(base, path, cookie):
    request = urllib.request.Request(base + path, headers={"Cookie": cookie})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read(), response.headers.get(
                "Content-Type", ""
            )
    except urllib.error.HTTPError as exc:
        return exc.code, b"", exc.headers.get("Content-Type", "")


def _get_html(base, path, cookie=None):
    headers = {"Cookie": cookie} if cookie else {}
    request = urllib.request.Request(base + path, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return (
                response.status,
                response.read().decode("utf-8"),
            )
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(ticket_store, "TICKETS_DB", tmp_path / "tickets.sqlite3")
    monkeypatch.setattr(
        ticket_store, "TICKET_FILES_DIR", str(tmp_path / "ticket_files")
    )
    return ticket_store


SNAP = {
    "question": "پرسش؟",
    "answer": "پاسخ",
    "book": "tarhe-kolli",
    "mode": "chat",
    "model": None,
}


def _file(db, account="a@x", category="garbled", body="متن ناخواناست در پاسخ", **kw):
    return db.create_ticket(account, 7, 11, category, body, dict(SNAP), **kw)


# --- the store ------------------------------------------------------------


def test_floors_and_category_roster(db):
    assert isinstance(_file(db), dict)
    assert _file(db, category="nope") == "bad_category"
    assert _file(db, body="کوتاه") == "short_body"
    assert _file(db, body="   ") == "short_body"


def test_soft_cap_counts_non_closed(db):
    for i in range(ticket_store.SOFT_CAP):
        assert isinstance(_file(db, body=f"گزارش شمارۀ {i} از مشکل"), dict)
    assert _file(db, body="یکی دیگر از همان مشکل") == "soft_cap"
    # Closing one frees the door again.
    db.close_ticket(1, by_account="a@x")
    assert isinstance(_file(db, body="حالا باز شد درست است"), dict)


def test_ownership_is_silence(db):
    ticket = _file(db)
    assert db.get_ticket(ticket["id"], account="b@x") is None
    assert db.get_ticket(ticket["id"], account="a@x")["id"] == ticket["id"]
    assert db.get_ticket(ticket["id"])["account"] == "a@x"
    assert db.add_reply(ticket["id"], "user", "b@x", "جاسوسی") == "not_found"
    assert db.close_ticket(ticket["id"], by_account="b@x") == "not_found"


def test_status_machine_and_unread(db):
    ticket = _file(db)
    assert ticket["status"] == "open"
    db.add_reply(ticket["id"], "user", "a@x", "تکمیل شرح مشکل")
    assert db.get_ticket(ticket["id"])["status"] == "open"
    db.add_reply(ticket["id"], "admin", ADMIN_EMAIL, "دنبال می‌کنیم")
    answered = db.get_ticket(ticket["id"])
    assert answered["status"] == "answered"
    assert db.unread_count("a@x") == 1
    assert answered["unread_for_user"] is True
    db.mark_admin_replies_read(ticket["id"], "a@x")
    assert db.unread_count("a@x") == 0
    # The owner's follow-up walks it back to open.
    db.add_reply(ticket["id"], "user", "a@x", "هنوز درست نشده")
    assert db.get_ticket(ticket["id"])["status"] == "open"


def _set_closed_at(db, ticket_id, value):
    con = sqlite3.connect(str(ticket_store.TICKETS_DB))
    con.execute(
        "UPDATE tickets SET closed_at = ? WHERE id = ?", (value, ticket_id)
    )
    con.commit()
    con.close()


def test_reopen_window_once_and_timed(db):
    ticket = _file(db)
    db.close_ticket(ticket["id"], by_account="a@x")
    # Eight days stale: the stamp is old, the door is shut — the test
    # moves the STAMP, not the store's clock.
    _set_closed_at(db, ticket["id"], "2020-01-01T00:00:00+00:00")
    assert (
        db.reopen_ticket(ticket["id"], "a@x", "هنوز مشکل داریم")
        == "reopen_denied"
    )
    # Fresh stamp: one reopen lands (with its why-reply), then never
    # again — even freshly closed.
    _set_closed_at(db, ticket["id"], ticket_store._now())
    reopened = db.reopen_ticket(ticket["id"], "a@x", "هنوز مشکل داریم")
    assert isinstance(reopened, dict) and reopened["status"] == "open"
    assert reopened["reopen_used"] is True
    assert reopened["replies"][-1]["body"] == "هنوز مشکل داریم"
    db.close_ticket(ticket["id"], by_account="a@x")
    assert db.reopen_ticket(ticket["id"], "a@x") == "reopen_denied"


def test_admin_edits_own_reply_only(db):
    ticket = _file(db)
    db.add_reply(ticket["id"], "admin", ADMIN_EMAIL, "پاسخ نخست")
    db.add_reply(ticket["id"], "user", "a@x", "پاسخ کاربر")
    reply_id = db.get_ticket(ticket["id"])["replies"][0]["id"]
    assert (
        db.edit_reply(reply_id, "other-admin@x", "دست کسی دیگر")
        == "forbidden"
    )
    edited = db.edit_reply(reply_id, ADMIN_EMAIL, "پاسخ ویرایش‌شده")
    assert isinstance(edited, dict)
    row = edited["replies"][0]
    assert row["body"] == "پاسخ ویرایش‌شده" and row["edited_at"]


def test_image_sniff_size_and_storage(db):
    assert ticket_store.sniff_image(PNG_BYTES)[0] == "png"
    assert ticket_store.sniff_image(b"RIFF____WEBPVP8 ")[0] == "webp"
    assert ticket_store.sniff_image(b"hello world") is None
    ticket = _file(db, image=PNG_BYTES)
    assert ticket["has_image"] is True
    data, mime = ticket_store.read_image(ticket["id"])
    assert data == PNG_BYTES and mime == "image/png"
    assert (
        _file(db, image=b"x" * (ticket_store.MAX_IMAGE_BYTES + 1))
        == "bad_image"
    )
    assert _file(db, image=b"not really a png") == "bad_image"


def test_list_filters_and_labels(db):
    _file(db, account="a@x", category="cost", body="دوبار پول گرفت از من")
    _file(db, account="b@x", category="garbled", body="متن به‌هم‌ریخته بود")
    rows = db.list_tickets(account="a@x")
    assert len(rows) == 1 and rows[0]["category_label"] == "هزینه و اعتبار"
    assert len(db.list_tickets(category="garbled")) == 1
    assert len(db.list_tickets()) == 2
    assert rows[0]["snippet"]


# --- the API, through the real sheet server ------------------------------


QUESTION = "نوآوری‌های کتاب چیست؟"
ANSWER = "نوآوری‌ها بر سه محور استوارند."


def _seed_session(base, phone):
    """One sitting with one ask and one settled answer, written through
    the store the server itself reads — the answer rides the sheet's
    blocks shape so the snapshot builder walks its real path. Returns
    (email, session_id, assistant_message_id)."""
    from ui import session_store

    email = ensure_account(phone)
    session = session_store.create_session(
        email, book="tarhe-kolli", title=QUESTION
    )
    session_store.append_message(
        email, session["id"], "user", {"text": QUESTION}
    )
    session_store.settle_ask(
        email,
        session["id"],
        "ask_test_1",
        {
            "blocks": [
                {"type": "paragraph", "parts": [{"text": ANSWER}]}
            ]
        },
    )
    full = session_store.get_session(email, session["id"])
    message_id = [
        m for m in full["messages"] if m["role"] == "assistant"
    ][-1]["id"]
    return email, session["id"], message_id


@pytest.fixture()
def gate(tmp_path):
    base, server, originals = with_gate(tmp_path, FakeComposer())
    yield base
    stop_gate(server, originals)


def test_file_reply_close_reopen_over_http(gate):
    base = gate
    email, session_id, message_id = _seed_session(base, "09120000001")
    status, payload = post(
        base,
        "/tickets",
        {
            "session_id": session_id,
            "message_id": message_id,
            "category": "garbled",
            "body": "متنِ اولِ پاسخ ناخواناست و جمله می‌شکند.",
        },
        phone="09120000001",
    )
    assert status == 200, payload
    ticket = payload["ticket"]
    assert ticket["snapshot"]["question"] == QUESTION
    assert ticket["snapshot"]["answer"] == ANSWER
    assert ticket["snapshot"]["book"] == "tarhe-kolli"
    assert ticket["status"] == "open"
    assert any(row["action"] == audit.TICKET_FILED for row in audit.recent(50))

    # The list is the owner's alone.
    stranger = cookie_for(base, ensure_account("09120000002"))
    status, other = _get_json(base, "/tickets", stranger)
    assert status == 200 and other["tickets"] == []

    # The badge fills with the manager's reply, empties when the owner
    # opens the thread.
    status, unread = get(base, "/tickets/unread", phone="09120000001")
    assert unread["unread_count"] == 0
    admin_cookie = cookie_for(base, ADMIN_EMAIL)
    status, location = _post_form(
        base,
        "/admin/tickets/reply",
        {"ticket_id": ticket["id"], "body": "پیگیری شد؛ اصلاح می‌کنیم."},
        admin_cookie,
    )
    assert status == 303 and location.endswith(f"/admin/tickets/{ticket['id']}")
    status, unread = get(base, "/tickets/unread", phone="09120000001")
    assert unread["unread_count"] == 1
    status, opened = get(base, f"/tickets/{ticket['id']}", phone="09120000001")
    assert opened["ticket"]["status"] == "answered"
    assert opened["ticket"]["unread_for_user"] is False
    status, unread = get(base, "/tickets/unread", phone="09120000001")
    assert unread["unread_count"] == 0

    # The owner's follow-up walks it back; close; the once-reopen door;
    # and a reply on the closed ticket is the friendly 409.
    status, payload = post(
        base,
        f"/tickets/{ticket['id']}/replies",
        {"body": "هنوز ناخواناست."},
        phone="09120000001",
    )
    assert payload["ticket"]["status"] == "open"
    status, payload = post(
        base, f"/tickets/{ticket['id']}/close", {}, phone="09120000001"
    )
    assert payload["ticket"]["status"] == "closed"
    status, payload = post(
        base,
        f"/tickets/{ticket['id']}/reopen",
        {"body": "دوباره همان مشکل برگشت."},
        phone="09120000001",
    )
    assert payload["ticket"]["status"] == "open"
    # A reply on a STILL-closed ticket is the friendly 409.
    status, payload = post(
        base, f"/tickets/{ticket['id']}/close", {}, phone="09120000001"
    )
    assert payload["ticket"]["status"] == "closed"
    status, payload = post(
        base,
        f"/tickets/{ticket['id']}/replies",
        {"body": "پاسخ روی تیکت بسته."},
        phone="09120000001",
    )
    assert status == 409


def _get_json(base, path, cookie):
    request = urllib.request.Request(base + path, headers={"Cookie": cookie})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            import json as _json

            return response.status, _json.load(response)
    except urllib.error.HTTPError as exc:
        import json as _json

        return exc.code, _json.load(exc)


def test_validation_and_foreign_silence_over_http(gate):
    base = gate
    _, session_id, message_id = _seed_session(base, "09120000003")
    assert (
        post(
            base,
            "/tickets",
            {
                "session_id": session_id,
                "message_id": message_id,
                "category": "garbled",
                "body": "کوتاه",
            },
            phone="09120000003",
        )[0]
        == 400
    )
    assert (
        post(
            base,
            "/tickets",
            {
                "session_id": session_id,
                "message_id": message_id,
                "category": "weird",
                "body": "دستهٔ نامعتبر اینجاست",
            },
            phone="09120000003",
        )[0]
        == 400
    )
    _, foreign_sid, foreign_mid = _seed_session(base, "09120000004")
    assert (
        post(
            base,
            "/tickets",
            {
                "session_id": foreign_sid,
                "message_id": foreign_mid,
                "category": "garbled",
                "body": "تیکت روی نشست دیگری",
            },
            phone="09120000003",
        )[0]
        == 404
    )
    assert (
        post(
            base,
            "/tickets",
            {
                "session_id": session_id,
                "message_id": 999999,
                "category": "garbled",
                "body": "پیامِ ناموجود اینجاست",
            },
            phone="09120000003",
        )[0]
        == 404
    )


def test_ask_key_pointer_files_the_same_answer(gate):
    """The live sheet knows its ask_key (it mints it) and the row id
    only after a resume read — the chip points by key live, by id on
    history, and both must name the same settled answer."""
    base = gate
    email, session_id, message_id = _seed_session(base, "09120000008")
    status, payload = post(
        base,
        "/tickets",
        {
            "session_id": session_id,
            "ask_key": "ask_test_1",
            "category": "off_book",
            "body": "شواهد از مقدمهٔ گردآورنده آمد نه خود کتاب.",
        },
        phone="09120000008",
    )
    assert status == 200, payload
    assert payload["ticket"]["message_id"] == message_id
    assert payload["ticket"]["snapshot"]["answer"] == ANSWER


def test_attachment_over_http(gate):
    base = gate
    email, session_id, message_id = _seed_session(base, "09120000005")
    status, payload = post(
        base,
        "/tickets",
        {
            "session_id": session_id,
            "message_id": message_id,
            "category": "bad_ref",
            "body": "ارجاع به صفحهٔ غلط می‌داد.",
            "image_b64": base64.b64encode(PNG_BYTES).decode("ascii"),
        },
        phone="09120000005",
    )
    assert status == 200, payload
    assert payload["ticket"]["has_image"] is True
    ticket_id = payload["ticket"]["id"]
    # The owner reads the bytes; a stranger meets the same 404 the
    # store's silence always answers; a text file never lands.
    status, body, ctype = _get_bytes(
        base, f"/tickets/{ticket_id}/file", cookie_for(base, email)
    )
    assert status == 200 and body.startswith(PNG_BYTES[:8])
    assert ctype == "image/png"
    status, _, _ = _get_bytes(
        base,
        f"/tickets/{ticket_id}/file",
        cookie_for(base, ensure_account("09120000006")),
    )
    assert status == 404
    status, payload2 = post(
        base,
        "/tickets",
        {
            "session_id": session_id,
            "message_id": message_id,
            "category": "garbled",
            "body": "تصویر خراب پیوست می‌کنم.",
            "image_b64": base64.b64encode(b"plain text file").decode("ascii"),
        },
        phone="09120000005",
    )
    assert status == 400


def test_admin_pages_gated_and_rendered(gate):
    base = gate
    email, session_id, message_id = _seed_session(base, "09120000007")
    post(
        base,
        "/tickets",
        {
            "session_id": session_id,
            "message_id": message_id,
            "category": "cost",
            "body": "دو بار هزینهٔ کامل کسر شد.",
        },
        phone="09120000007",
    )
    admin_cookie = cookie_for(base, ADMIN_EMAIL)
    # Anonymous and the owner-operator are refused; the Admin reads.
    assert _get_html(base, "/admin/tickets")[0] == 401
    assert _get_html(base, "/admin/tickets", cookie_for(base, email))[0] == 403
    status, html_text = _get_html(base, "/admin/tickets", cookie=admin_cookie)
    assert status == 200
    assert "صندوق تیکت‌ها" in html_text and "هزینه و اعتبار" in html_text
    # The detail page, the transcript behind it, the edit-own-reply
    # disclosure, and the console's standing link with the open count.
    status, detail = _get_html(base, "/admin/tickets/1", cookie=admin_cookie)
    assert status == 200 and ANSWER in detail
    assert "ویرایش پاسخ" not in detail  # no manager reply yet to edit
    status, transcript = _get_html(
        base, "/admin/tickets/1/transcript", cookie=admin_cookie
    )
    assert status == 200 and QUESTION in transcript
    status, console = _get_html(base, "/admin", cookie=admin_cookie)
    assert status == 200 and "صندوق تیکت‌ها" in console


# --- the pinned surfaces (the naming gate's doc-tests) --------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
UI = REPO_ROOT / "ui" / "index.html"


def test_sheet_pins_the_ticket_draft_strings():
    # The naming gate: every NEW Farsi string rides the draft table as
    # a single constant — «ثبت مشکل» deliberately outside the «گزارش»
    # family («گزارش نشست» is taken), the rename a one-line change.
    html = UI.read_text(encoding="utf-8")
    assert 'TICKET_CHIP = "ثبت مشکل"' in html
    assert 'TICKETS_PANEL_TITLE = "تیکت‌های من"' in html
    assert 'TICKET_CLOSE_USER = "مشکل حل شد"' in html
    assert 'TICKET_REOPEN = "بازگشایی"' in html
    assert 'TICKET_ATTACH_LABEL = "تصویر (اختیاری، تا ۲ مگابایت)"' in html
    # The roster mirrors the store's wire vocabulary one-to-one.
    for key in ("off_book", "bad_ref", "garbled", "language", "cost",
                "slow", "other"):
        assert f'["{key}",' in html


def test_sheet_wires_the_ticket_surface():
    # The chip rides both renderers (live surface + resume), the panel
    # polls the badge beside the usage meter, and the client only ever
    # POINTS — the snapshot is the server's word.
    html = UI.read_text(encoding="utf-8")
    assert "makeTicketChip({" in html
    assert "messageId: message.id," in html
    assert "ticketPointer.askKey = currentAskKey" in html
    assert 'fetch("/tickets/unread"' in html
    assert 'fetch("/tickets", {' in html
    assert 'fetch(`/tickets/${ticket.id}/replies`' in html
    assert "payload.ask_key = pointer.askKey" in html
    assert "payload.message_id = pointer.messageId" in html


def test_ticket_db_rides_the_persisted_volume():
    # compose.yaml must point TICKETS_DB at /data like SESSIONS_DB — a
    # store defaulting to /app inside the container would lose every
    # report at the next image rebuild.
    compose = (REPO_ROOT / "compose.yaml").read_text(encoding="utf-8")
    assert "TICKETS_DB: /data/tickets.sqlite3" in compose


def test_inbox_pins_its_draft_names():
    page = (REPO_ROOT / "ui" / "tickets_admin.py").read_text(encoding="utf-8")
    assert 'INBOX_TITLE = "صندوق تیکت‌ها"' in page
    assert 'REPLY_BTN = "پاسخ"' in page
    assert 'CLOSE_BTN = "بستن تیکت"' in page
    assert 'TRANSCRIPT_TITLE = "دیدن کل نشست"' in page
