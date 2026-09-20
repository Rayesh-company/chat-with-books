"""The Admin console (T25, GitLab #26): the «میز مدیریت» reads the
system and answers the Admin alone — an operator's cookie gets the
console's own Farsi 403, the anonymous gets the standard 401; the page
carries the Accounts mirror (Balance, today's spend, chats against the
daily limit), the live research turns, the settled ones with their
failures called out, and the audit log's newest rows. The audit itself
is pinned truly append-only: a row goes in, and the engine's own
triggers abort any UPDATE or DELETE — an audit that can be edited is
not an audit. Account creation appends exactly one row; a refused
second issuance appends nothing."""

import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from tests.helpers import (
    ADMIN_EMAIL,
    TEST_PASSWORD,
    cookie_for,
    ensure_account,
    get,
    raw_get,
    raw_post,
    stop_gate,
    with_gate,
)
from ui import accounts as accounts_store
from ui import audit, ledger, quotas, research
from ui.console import CONSOLE_TITLE, console_html
from ui.ledger import cost_toman, record


def get_html(base, path, cookie=None):
    """One GET answered with the raw document — (status, content type,
    text). The shared helpers parse JSON bodies, and the console
    answers HTML; this keeps the assertions on the served bytes and
    the Content-Type the browser would see."""
    headers = {"Cookie": cookie} if cookie is not None else {}
    request = urllib.request.Request(base + path, headers=headers)
    try:
        response = urllib.request.urlopen(request, timeout=10)
        with response:
            return (
                response.status,
                response.headers.get("Content-Type", ""),
                response.read().decode("utf-8"),
            )
    except urllib.error.HTTPError as exc:
        return (
            exc.code,
            exc.headers.get("Content-Type", ""),
            exc.read().decode("utf-8"),
        )


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """The form tests' client: a 303 must be SEEN, not silently
    followed — the post/redirect/get discipline is the write side's
    contract with the browser."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def post_form(base, path, fields, cookie=None):
    """One form-encoded POST that refuses to follow the redirect —
    (status, Location header). The browser's own view of a console
    write."""
    opener = urllib.request.build_opener(_NoRedirect)
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if cookie is not None:
        headers["Cookie"] = cookie
    body = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(base + path, data=body, headers=headers)
    try:
        with opener.open(request, timeout=10) as response:
            return response.status, response.headers.get("Location", "")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Location", "")


def seed_live_turn(account):
    """One fake live turn in the registry — a real ResearchTurn at a
    working state, so the console's snapshot has something to mirror.
    The test removes it in its finally (the registry is the engine's
    own; the test only borrows a slot)."""
    turn = research.ResearchTurn(account=account, session_id="sess-console", message="پرسش آزمایشی")
    turn.state = "searching"
    with research.RESEARCH_REGISTRY_LOCK:
        research.RESEARCH_REGISTRY[turn.id] = turn
    return turn


def seed_settled_turn(account, state, error=None):
    """One fake settled turn in the recent-settled ring — the shape a
    worker leaves behind at reap time (T11), state terminal, `done`
    set."""
    turn = research.ResearchTurn(account=account, session_id="sess-settled", message="پیام آزمایشی")
    turn.state = state
    turn.error = error
    turn.done.set()
    with research.RESEARCH_REGISTRY_LOCK:
        research.RESEARCH_RECENT_SETTLED.append(turn)
    return turn


# --- the page for the Admin --------------------------------------------------


def test_the_console_renders_the_system_for_the_admin():
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    base, server, originals = with_gate(tmp, None)
    turn = None
    try:
        phone = "09120000001"
        account = ensure_account(phone)
        # One metered ask-path entry: 3000 in / 1500 out at the pinned
        # tariff — 18 toman off the seeded 1,000,000 Balance today,
        # keyed by the Account's email (T21).
        record(account, "ask", 3000, 1500, metered=True)
        assert cost_toman(3000, 1500) == 18
        # One yesterday entry, landed directly with its own day: the
        # console's per-day read answers history, not just the live
        # day (the acceptance's "yesterday's per-account spend").
        yesterday = time.strftime(
            "%Y-%m-%d", time.localtime(time.time() - 24 * 60 * 60)
        )
        conn = sqlite3.connect(ledger.LEDGER_DB)
        try:
            conn.execute(
                "INSERT INTO usage_entries (account, ts, day, kind, metered,"
                " input_tokens, output_tokens, cost_toman)"
                " VALUES (?, ?, ?, 'ask', 1, 1000, 500, 6)",
                (account, f"{yesterday} 10:00:00", yesterday),
            )
            conn.commit()
        finally:
            conn.close()
        assert ledger.day_total(account, yesterday) == 6
        assert ledger.day_total(account, ledger._today()) == 18
        quotas.record_chat(account)
        turn = seed_live_turn(account)
        failed = seed_settled_turn(
            account, "failed", error=research.RESEARCH_FAILED_DETAIL
        )
        done = seed_settled_turn(account, "done")

        status, content_type, html = get_html(
            base, "/admin", cookie=cookie_for(base, ADMIN_EMAIL)
        )
        assert status == 200
        assert content_type == "text/html; charset=utf-8"
        # The DRAFT-named mirror, right-to-left.
        assert CONSOLE_TITLE in html
        assert 'dir="rtl"' in html and "پیش‌نویس" in html
        # The Accounts mirror: the seeded operator's row, its Balance
        # after the deduction, yesterday's and today's spend, the day's
        # chat count against the daily limit — and the Admin's own row
        # beside it.
        assert f"{phone}@sheet.test" in html
        assert "999,982" in html
        assert "خرج دیروز (تومان)" in html
        assert '<td class="num">6</td>' in html
        assert "<td class=\"num\">18</td>" in html
        assert "1 از 5" in html
        assert ADMIN_EMAIL in html
        # The live turn rides with its id and its Farsi state.
        assert turn.id in html
        assert "جست‌وجوی شواهد" in html
        # The settled ring: the failure called out with its Farsi state
        # and recorded detail, the done one quiet beside it.
        assert failed.id in html
        assert "ناتمام ماند" in html
        assert research.RESEARCH_FAILED_DETAIL in html
        assert done.id in html
        assert "تمام شد" in html
    finally:
        with research.RESEARCH_REGISTRY_LOCK:
            if turn is not None:
                research.RESEARCH_REGISTRY.pop(turn.id, None)
            research.RESEARCH_RECENT_SETTLED.clear()
        stop_gate(server, originals)


def test_an_operator_gets_the_console_403_farsi():
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    base, server, originals = with_gate(tmp, None)
    try:
        status, body = get(base, "/admin", phone="09120000002")
        assert status == 403
        assert body["detail"] == "میز مدیریت فقط از دست مدیر برمی‌آید."
    finally:
        stop_gate(server, originals)


def test_the_console_refuses_the_anonymous():
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    base, server, originals = with_gate(tmp, None)
    try:
        status, body = raw_get(base, "/admin")
        assert status == 401
        assert body["detail"] == "برای ادامه وارد شوید."
    finally:
        stop_gate(server, originals)


# --- the pure page -----------------------------------------------------------


def test_the_console_page_is_pure_over_its_inputs():
    """The renderer takes plain dicts and returns the page — no store,
    no registry, no clock of its own — so the mirror shows exactly what
    the caller gathered, nothing more."""
    html = console_html(
        [
            {
                "email": "op@sheet.test",
                "role": "operator",
                "phone": "09120000003",
                "balance_toman": 500_000,
                "yesterday_spend_toman": 40,
                "today_spend_toman": 120,
                "chats_today": 2,
            }
        ],
        [{"id": "turn1", "phone": "09120000003", "state": "writing", "elapsed": 4.2}],
        [
            {"id": "turn0", "phone": "09120000003", "state": "failed", "error": "ناکامی ثبت‌شده"},
            {"id": "turn9", "phone": "09120000003", "state": "done"},
        ],
        [
            {
                "ts": "2026-09-19T10:00:00",
                "action": audit.ACCOUNT_CREATED,
                "actor_email": ADMIN_EMAIL,
                "detail": "op@sheet.test",
            }
        ],
        quota_limit=5,
    )
    assert CONSOLE_TITLE in html and "پیش‌نویس" in html
    assert "500,000" in html and "40" in html and "120" in html and "2 از 5" in html
    assert "خرج دیروز (تومان)" in html and "خرج امروز (تومان)" in html
    assert "اپراتور نشست" in html
    assert "turn1" in html and "نوشتن پاسخ" in html
    assert "turn0" in html and "ناکامی ثبت‌شده" in html
    assert "1 پیام پژوهش ناتمام ماند" in html
    assert "ساختن حساب" in html and "2026-09-19T10:00:00" in html
    # An empty mirror renders the em-dash rows, never a crash.
    empty = console_html([], [], [], [], quota_limit=5)
    assert CONSOLE_TITLE in empty


# --- the audit log -----------------------------------------------------------


def test_the_audit_log_is_truly_append_only():
    """A row goes in; UPDATE and DELETE are both aborted by the
    engine's own triggers — below every application layer — and the
    row stands unchanged. An audit that can be edited is not an
    audit."""
    row = audit.append(
        audit.ACCOUNT_CREATED,
        actor_email=ADMIN_EMAIL,
        detail="op@sheet.test",
    )
    assert row["action"] == audit.ACCOUNT_CREATED
    assert audit.recent(10) == [row]

    conn = sqlite3.connect(str(audit.AUDIT_DB))
    try:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                "UPDATE audit SET action = 'tampered' WHERE id = ?", (row["id"],)
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM audit WHERE id = ?", (row["id"],))
    finally:
        conn.close()
    after = audit.recent(10)
    assert len(after) == 1
    assert after[0]["action"] == audit.ACCOUNT_CREATED
    assert after[0]["actor_email"] == ADMIN_EMAIL


def test_account_creation_appends_exactly_one_audit_row():
    """The one wired action (T25): the Admin's issuance lands once,
    with the issuer and the issued — and a refused second issuance of
    the same email logs nothing, because the log records what happened,
    never what was attempted."""
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    base, server, originals = with_gate(tmp, None)
    try:
        admin_cookie = cookie_for(base, ADMIN_EMAIL)
        payload = {
            "email": "op@sheet.test",
            "password": TEST_PASSWORD,
            "phone": "09120000009",
        }
        status, body = raw_post(base, "/auth/accounts", payload, cookie=admin_cookie)
        assert status == 200
        rows = audit.recent(10)
        assert len(rows) == 1
        assert rows[0]["action"] == audit.ACCOUNT_CREATED
        assert rows[0]["actor_email"] == ADMIN_EMAIL
        assert "op@sheet.test" in rows[0]["detail"]
        # The console's own mirror shows the action in Farsi with the
        # new Account's email in the details column.
        _, _, html = get_html(base, "/admin", cookie=admin_cookie)
        assert "ساختن حساب" in html and "op@sheet.test" in html
        # The refused second issuance: still exactly one row.
        status, _ = raw_post(base, "/auth/accounts", payload, cookie=admin_cookie)
        assert status == 409
        assert len(audit.recent(10)) == 1
    finally:
        stop_gate(server, originals)


# --- the write side (T26, GitLab #28) ------------------------------------------


def test_the_console_page_carries_the_write_forms():
    """The page itself is the write surface: the issuance form and the
    top-up form, the latter's account list rendered from the very rows
    the mirror shows — the pure function renders only what it is
    handed."""
    html = console_html(
        [
            {
                "email": "op@sheet.test",
                "role": "operator",
                "phone": "09120000003",
                "balance_toman": 500_000,
                "yesterday_spend_toman": 0,
                "today_spend_toman": 0,
                "chats_today": 0,
            }
        ],
        [],
        [],
        [],
        quota_limit=5,
    )
    assert 'action="/admin/accounts"' in html
    assert 'action="/admin/topup"' in html
    assert 'method="post"' in html
    assert '<option value="op@sheet.test">' in html
    # A refused write's note renders from the whitelisted code only.
    assert "این ایمیل پیش‌تر حساب گرفته است." in console_html(
        [], [], [], [], quota_limit=5, error_code="email_taken"
    )
    # An unknown code — or user text wearing one — renders no note.
    plain = console_html([], [], [], [], quota_limit=5)
    odd = console_html(
        [], [], [], [], quota_limit=5, error_code="<script>alert(1)</script>"
    )
    assert '<p class="write-error" role="alert">' not in plain
    assert '<p class="write-error" role="alert">' not in odd


def test_the_admin_issues_an_account_from_the_console():
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    base, server, originals = with_gate(tmp, None)
    try:
        admin_cookie = cookie_for(base, ADMIN_EMAIL)
        status, location = post_form(
            base,
            "/admin/accounts",
            {"email": "new@sheet.test", "password": TEST_PASSWORD,
             "phone": "09120000011"},
            cookie=admin_cookie,
        )
        assert status == 303
        assert location == "/admin"
        # The Account exists with its attached phone, and logs in.
        from ui import accounts

        assert accounts.account_by_email("new@sheet.test")["phone"] == (
            "09120000011"
        )
        # The audited issuance: one row, the issuer and the issued.
        rows = audit.recent(10)
        assert [row["action"] for row in rows] == [audit.ACCOUNT_CREATED]
        assert "new@sheet.test" in rows[0]["detail"]
        # The redirect re-renders the mirror with the new row showing.
        _, _, html = get_html(base, "/admin", cookie=admin_cookie)
        assert "new@sheet.test" in html
    finally:
        stop_gate(server, originals)


def test_the_admin_tops_a_balance_up_from_the_console():
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    base, server, originals = with_gate(tmp, None)
    try:
        phone = "09120000012"
        ensure_account(phone)
        admin_cookie = cookie_for(base, ADMIN_EMAIL)
        status, location = post_form(
            base,
            "/admin/topup",
            {"email": f"{phone}@sheet.test", "amount": "120000"},
            cookie=admin_cookie,
        )
        assert status == 303
        assert location == "/admin"
        # The audited top-up: actor, amount, and the new balance —
        # the seeded 1,000,000 plus the top-up's 120,000.
        rows = audit.recent(10)
        assert [row["action"] for row in rows] == [audit.BALANCE_TOPPED]
        assert '"amount_toman": 120000' in rows[0]["detail"]
        assert '"new_balance_toman": 1120000' in rows[0]["detail"]
        # The operator sees the new اعتبار wherever the Balance renders
        # — the profile (/profile/data) answers the same row the top-up
        # landed on, keyed by the operator's own cookie.
        status, body = get(base, "/profile/data", phone=phone)
        assert status == 200
        assert body["balance_toman"] == 1_120_000
        # The console's own mirror re-renders it, and the audit table
        # names the action in Farsi.
        _, _, html = get_html(base, "/admin", cookie=admin_cookie)
        assert "1,120,000" in html
        assert "شارژ اعتبار" in html
    finally:
        stop_gate(server, originals)


def test_the_write_paths_refuse_everyone_but_the_admin():
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    base, server, originals = with_gate(tmp, None)
    try:
        ensure_account("09120000021")
        operator_cookie = cookie_for(base, "09120000021@sheet.test")
        # A logged-in operator: both forms refused with the console's
        # own Farsi 403 — issuance and top-up are the Admin's acts.
        for path in ("/admin/accounts", "/admin/topup"):
            status, body = raw_post(base, path, {"email": "x@y.test"}, cookie=operator_cookie)
            assert status == 403
            assert body["detail"] == "میز مدیریت فقط از دست مدیر برمی‌آید."
            status, _ = post_form(base, path, {"email": "x@y.test"}, cookie=operator_cookie)
            assert status == 403
            # The anonymous: the standard 401.
            status, body = raw_post(base, path, {"email": "x@y.test"})
            assert status == 401
            assert body["detail"] == "برای ادامه وارد شوید."
    finally:
        stop_gate(server, originals)


def test_refused_writes_redirect_with_a_code_and_log_nothing():
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    base, server, originals = with_gate(tmp, None)
    try:
        admin_cookie = cookie_for(base, ADMIN_EMAIL)
        # A duplicate issuance: 409's console shape is the redirect
        # with the email_taken code — and the log records nothing,
        # because nothing happened.
        payload = {"email": "dup@sheet.test", "password": TEST_PASSWORD}
        status, _ = post_form(base, "/admin/accounts", payload, cookie=admin_cookie)
        assert status == 303
        status, location = post_form(
            base, "/admin/accounts", payload, cookie=admin_cookie
        )
        assert status == 303
        assert location == "/admin?error=email_taken"
        # A bad amount and an unknown account: their own codes.
        status, location = post_form(
            base, "/admin/topup", {"email": "dup@sheet.test", "amount": "-5"},
            cookie=admin_cookie,
        )
        assert location == "/admin?error=bad_amount"
        status, location = post_form(
            base, "/admin/topup", {"email": "no@sheet.test", "amount": "1000"},
            cookie=admin_cookie,
        )
        assert location == "/admin?error=unknown_account"
        status, location = post_form(
            base, "/admin/accounts", {"email": "", "password": ""},
            cookie=admin_cookie,
        )
        assert location == "/admin?error=bad_body"
        # Exactly two lawful actions stand in the log: the one
        # issuance, no top-up at all.
        actions = [row["action"] for row in audit.recent(10)]
        assert actions == [audit.ACCOUNT_CREATED]
    finally:
        stop_gate(server, originals)


def test_the_first_admin_comes_from_the_env():
    """The seed command is retired (T26): the deployment plants the
    first Admin from ADMIN_EMAIL/ADMIN_PASSWORD at startup — once,
    audited — and a restart, an empty env, or a taken email never
    touches a standing Account."""
    import tempfile

    from ui import serve as serve_module

    original_db = accounts_store.ACCOUNTS_DB
    db = Path(tempfile.mkdtemp()) / "accounts.sqlite3"
    accounts_store.ACCOUNTS_DB = db
    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("ADMIN_EMAIL", "pm@sheet.test")
            mp.setenv("ADMIN_PASSWORD", "رمز-مدیر")
            serve_module.ensure_first_admin_from_env()
        assert accounts_store.verify_login("pm@sheet.test", "رمز-مدیر")["role"] == (
            "admin"
        )
        # The deployment's act is history: one admin_seeded row.
        rows = audit.recent(10)
        assert [row["action"] for row in rows] == [audit.ADMIN_SEEDED]
        # The restart: the standing Admin is untouched, nothing logs.
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("ADMIN_EMAIL", "pm@sheet.test")
            mp.setenv("ADMIN_PASSWORD", "رمز-تازه")
            serve_module.ensure_first_admin_from_env()
            assert accounts_store.verify_login("pm@sheet.test", "رمز-تازه") is None
            assert len(audit.recent(10)) == 1
            # An empty env: no seed, no refusal noise, no NEW row — the
            # phase-one row stands, history is never rewritten.
            accounts_store.ACCOUNTS_DB = Path(tempfile.mkdtemp()) / "fresh.sqlite3"
            with pytest.MonkeyPatch.context() as mp:
                mp.delenv("ADMIN_EMAIL", raising=False)
                mp.delenv("ADMIN_PASSWORD", raising=False)
                serve_module.ensure_first_admin_from_env()
            assert len(audit.recent(10)) == 1
    finally:
        accounts_store.ACCOUNTS_DB = original_db


# --- the config lock ---------------------------------------------------------


def test_the_config_contract_is_pinned():
    """The image ships the audit and console modules — the Dockerfile's
    COPY is the config lock (a module serve.py imports but the image
    lacks dies only in the deployed container, the flat-layout fix's
    lesson), and the audit DB rides compose's persisted volume."""
    dockerfile = (Path(audit.__file__).parent / "Dockerfile").read_text(
        encoding="utf-8"
    )
    assert "audit.py" in dockerfile
    assert "console.py" in dockerfile
    compose = (Path(audit.__file__).parent.parent / "compose.yaml").read_text(
        encoding="utf-8"
    )
    assert "AUDIT_DB: /data/audit.sqlite3" in compose
    # The naming row rides the draft roster (the standing gate): the
    # console renders «میز مدیریت» only because CONTEXT.md carries it
    # as DRAFT pending PM approval, and the page badge (pinned above)
    # says so to the reader.
    context = (Path(audit.__file__).parent.parent / "CONTEXT.md").read_text(
        encoding="utf-8"
    )
    assert "Admin console «میز مدیریت»" in context
    assert "DRAFT — pending PM approval" in context
