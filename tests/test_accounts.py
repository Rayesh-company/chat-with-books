"""The Account gate's contract locks (ADR-0013): the store's pbkdf2
hashing and verify, the stateless signed token (sign, verify, expiry,
tamper), the open/Admin-only shapes of the /auth endpoints, the flipped
gate's 401/403 over the real sheet server, the first-admin env seeding's
create-once discipline (T26 — the bootstrap seed command retired), the
top-up's store shape, the quota's survival per Account, and the sheet's
login-overlay marker. External behavior through the real server, store
behavior through the patched module — the house test shape."""

import base64
import json
import os
import time

from tests.conftest import REPO_ROOT
from tests.helpers import (
    ADMIN_EMAIL,
    TEST_PASSWORD,
    account_email_for_phone,
    cookie_for,
    get,
    patch_accounts,
    post,
    raw_get,
    raw_post,
    stop_gate,
    unpatch_accounts,
    with_gate,
)

import sys  # noqa: E402

sys.path.insert(0, str(REPO_ROOT))

from ui import accounts, serve  # noqa: E402


class NoUpstream:
    """The auth tests' stand-in: the auth endpoints never touch an
    upstream, so any call here is a gate leak."""

    def __call__(self, request, timeout=None):
        raise AssertionError(f"upstream called: {request.full_url}")


class CannedUpstream:
    """Answers every upstream call with one canned JSON body — the
    recall proxy's minimal Cognee for the quota test."""

    def __init__(self, body=b"[]"):
        self.body = body

    def __call__(self, request, timeout=None):
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

        return Response(self.body)


# --- the store: hashing, verify, issuance -------------------------------------


def test_password_hashes_use_pbkdf2_with_a_fresh_salt_per_account():
    text = (REPO_ROOT / "ui" / "accounts.py").read_text(encoding="utf-8")
    # The pin is in source, never env — a stored hash can never silently
    # meet a weaker scheme.
    assert "PBKDF2_ITERATIONS = 240_000" in text
    assert "ACCOUNTS_DB" in text
    assert "accounts.sqlite3" in text
    first = accounts.hash_password("گذرواژه")
    second = accounts.hash_password("گذرواژه")
    assert first.startswith("pbkdf2_sha256$240000$")
    # Two Accounts with the same password never share a stored digest.
    assert first != second
    assert accounts.verify_password("گذرواژه", first)
    assert not accounts.verify_password("گذرواژهٔ نادرست", first)
    # A malformed stored value is a failed login, never a crash.
    assert not accounts.verify_password("گذرواژه", "garbage")
    assert not accounts.verify_password("گذرواژه", "")


def test_verify_login_round_trips_and_issuance_is_once(tmp_path):
    patch_accounts(tmp_path)
    try:
        created = accounts.create_account(
            "sara@sheet.test", "رمز", phone="09123456789"
        )
        assert created == {
            "email": "sara@sheet.test",
            "phone": "09123456789",
            "role": "operator",
            # The guest cut (2026-10-04): every issuance carries its
            # research state — on, unless the Admin cut it.
            "research_enabled": True,
        }
        # An Account is issued once; a second issuance is refused, not
        # a silent overwrite.
        assert accounts.create_account("sara@sheet.test", "دیگر") is None
        account = accounts.verify_login("sara@sheet.test", "رمز")
        assert account is not None and account["role"] == "operator"
        assert account["phone"] == "09123456789"
        # The email normalizes (case, whitespace) on every read.
        assert accounts.verify_login("  SARA@sheet.test ", "رمز") is not None
        assert accounts.verify_login("sara@sheet.test", "نادرست") is None
        assert accounts.verify_login("nobody@sheet.test", "رمز") is None
        assert accounts.get_role("sara@sheet.test") == "operator"
        assert accounts.get_role(ADMIN_EMAIL) == "admin"
        assert accounts.get_role("no-one@sheet.test") is None
        # The phone is legacy data: attach updates the attached field.
        assert accounts.attach_phone("sara@sheet.test", "09120000999") is True
        assert accounts.account_by_email("sara@sheet.test")["phone"] == (
            "09120000999"
        )
        assert accounts.attach_phone("no-one@sheet.test", "09120000999") is False
    finally:
        unpatch_accounts()


# --- the token: sign, verify, expiry, tamper -----------------------------------


def test_token_signs_verifies_expires_and_refuses_tampering():
    os.environ["AUTH_SECRET"] = "test-secret"
    try:
        token = serve.issue_token("sara@sheet.test", "operator")
        assert serve.verify_token(token) == {
            "email": "sara@sheet.test",
            "role": "operator",
        }
        body, _, signature = token.partition(".")
        # A forged payload (same signature) refuses — the signature is
        # over exactly those bytes.
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        payload["email"] = "attacker@sheet.test"
        forged = (
            base64.urlsafe_b64encode(
                json.dumps(payload, separators=(",", ":")).encode("utf-8")
            )
            .rstrip(b"=")
            .decode("ascii")
        )
        assert serve.verify_token(f"{forged}.{signature}") is None
        # A wrong signature over the real payload refuses.
        assert serve.verify_token(f"{body}.{'0' * 64}") is None
        # Junk and wrong shapes refuse.
        assert serve.verify_token("") is None
        assert serve.verify_token("not-a-token") is None
        assert serve.verify_token(None) is None
        # An expired token refuses even with its valid signature — the
        # injected clock pins the issuance in the past.
        expired = serve.issue_token(
            "sara@sheet.test", "operator", now=time.time() - serve.AUTH_TOKEN_TTL - 10
        )
        assert serve.verify_token(expired) is None
    finally:
        os.environ.pop("AUTH_SECRET", None)


# --- the /auth endpoints over the real server ---------------------------------


def test_login_me_and_logout_over_the_real_server(tmp_path):
    base, server, original = with_gate(tmp_path, NoUpstream())
    try:
        # Anonymous whoami is the same 401 every gated endpoint answers.
        status, payload = raw_get(base, "/auth/me")
        assert status == 401
        assert payload["detail"] == serve.AUTH_LOGIN_401_DETAIL
        # A wrong password and an unknown email refuse identically.
        status, payload = raw_post(
            base, "/auth/login", {"email": ADMIN_EMAIL, "password": "نادرست"}
        )
        assert status == 401
        assert payload["detail"] == serve.AUTH_BAD_CREDENTIALS_DETAIL
        status, _ = raw_post(
            base,
            "/auth/login",
            {"email": "no-one@sheet.test", "password": TEST_PASSWORD},
        )
        assert status == 401
        # The one door opens on the right credentials.
        status, payload = raw_post(
            base, "/auth/login", {"email": ADMIN_EMAIL, "password": TEST_PASSWORD}
        )
        assert status == 200
        assert payload == {"email": ADMIN_EMAIL, "role": "admin"}
        cookie = cookie_for(base, ADMIN_EMAIL)
        status, payload = raw_get(base, "/auth/me", cookie=cookie)
        assert status == 200
        assert payload["email"] == ADMIN_EMAIL
        assert payload["role"] == "admin"
        assert payload["phone"] is None  # the Admin carries no phone
        # Logout answers its Farsi note; the stateless token's life ends
        # client-side (the cleared cookie rides the Set-Cookie header).
        status, payload = raw_post(base, "/auth/logout", {}, cookie=cookie)
        assert status == 200
        assert payload["detail"] == serve.AUTH_LOGOUT_DETAIL
    finally:
        stop_gate(server, original)


def test_account_creation_is_admin_only(tmp_path):
    base, server, original = with_gate(tmp_path, NoUpstream())
    operator_phone = "09120000100"
    try:
        # Anonymous is the standard 401.
        status, _ = raw_post(
            base,
            "/auth/accounts",
            {"email": "x@sheet.test", "password": "پ"} ,
        )
        assert status == 401
        # A logged-in operator is 403 Farsi: issuing Accounts is the
        # Admin's act alone.
        status, payload = post(
            base,
            "/auth/accounts",
            {"email": "x@sheet.test", "password": "پ"},
            phone=operator_phone,
        )
        assert status == 403
        assert payload["detail"] == serve.AUTH_NOT_ADMIN_403_DETAIL
        # The Admin mints an operator with a Farsi-digit phone; the
        # attachment stores the normalized digits.
        admin_cookie = cookie_for(base, ADMIN_EMAIL)
        status, payload = raw_post(
            base,
            "/auth/accounts",
            {
                "email": "sara@sheet.test",
                "password": "رمز",
                "phone": "۰۹۱۲۳۴۵۶۷۸۹",
            },
            cookie=admin_cookie,
        )
        assert status == 200
        assert payload == {
            "email": "sara@sheet.test",
            "role": "operator",
            "phone": "09123456789",
        }
        # A second issuance of the same email is the Admin's mistake to
        # see — 409, never an overwrite.
        status, _ = raw_post(
            base,
            "/auth/accounts",
            {"email": "sara@sheet.test", "password": "دیگر"},
            cookie=admin_cookie,
        )
        assert status == 409
        # The minted operator logs in and passes the gate: the store
        # answers 404 (unknown session), never the 401.
        sara_cookie = cookie_for(base, "sara@sheet.test", "رمز")
        status, _ = raw_get(
            base, "/research/state?session=no-such", cookie=sara_cookie
        )
        assert status == 404
    finally:
        stop_gate(server, original)


# --- the flipped gate: 401, tamper, 403 ----------------------------------------


def test_anonymous_and_broken_tokens_are_401_on_the_gated_paths(tmp_path):
    base, server, original = with_gate(tmp_path, NoUpstream())
    try:
        # The ask gate.
        status, payload = post(base, "/api/v1/recall", {"query": "پرسش؟"})
        assert status == 401
        assert payload["detail"] == serve.AUTH_LOGIN_401_DETAIL
        # A garbage cookie: the same 401, never a crash.
        status, _ = raw_post(
            base,
            "/api/v1/recall",
            {"query": "پرسش؟"},
            cookie="cwb_auth=garbage.signature",
        )
        assert status == 401
        # An expired token (valid signature, past exp): 401.
        expired = serve.issue_token(
            ADMIN_EMAIL, "admin", now=time.time() - serve.AUTH_TOKEN_TTL - 10
        )
        status, _ = raw_post(
            base,
            "/api/v1/recall",
            {"query": "پرسش؟"},
            cookie=f"cwb_auth={expired}",
        )
        assert status == 401
        # A token whose Account no longer exists is anonymous again.
        ghost = serve.issue_token("ghost@sheet.test", "operator")
        status, _ = raw_post(
            base,
            "/api/v1/recall",
            {"query": "پرسش؟"},
            cookie=f"cwb_auth={ghost}",
        )
        assert status == 401
        # Every research read is gated the same way.
        for path in (
            "/research/state?session=x",
            "/research/turn?turn=x",
            "/research/messages?session=x",
            "/research/report?session=x",
        ):
            status, payload = get(base, path)
            assert status == 401, path
            assert payload["detail"] == serve.AUTH_LOGIN_401_DETAIL
    finally:
        stop_gate(server, original)


def test_an_account_without_a_phone_is_a_working_account(tmp_path):
    """The no-attached-phone 403 retired with the phone-keyed stores
    (T21, GitLab #23): the stores key by the Account's email, so an
    Account issued without legacy history is a working Account — it
    reaches the gates, bounded by quota and Balance like any other.
    The attached phone, when it comes, is the migration's mapping, not
    an identity."""
    base, server, original = with_gate(tmp_path, NoUpstream())
    try:
        # The Admin mints a phoneless operator.
        admin_cookie = cookie_for(base, ADMIN_EMAIL)
        status, _ = raw_post(
            base,
            "/auth/accounts",
            {"email": "phoneless@sheet.test", "password": "رمز"},
            cookie=admin_cookie,
        )
        assert status == 200
        cookie = cookie_for(base, "phoneless@sheet.test", "رمز")
        # The research read answers the UNKNOWN-SESSION 404, not a
        # 403 about a missing phone — the Account is addressable.
        status, payload = raw_get(
            base, "/research/state?session=no-such", cookie=cookie
        )
        assert status == 404
        # The auth-level surface still shows the attached phone: none.
        status, payload = raw_get(base, "/auth/me", cookie=cookie)
        assert status == 200 and payload["phone"] is None
    finally:
        stop_gate(server, original)


# --- the quota survives, per Account -------------------------------------------


def test_the_daily_quota_still_bounds_per_account(tmp_path):
    base, server, original = with_gate(tmp_path, CannedUpstream())
    phone_a, phone_b = "09120000201", "09120000202"
    account_a, account_b = (
        account_email_for_phone(phone_a),
        account_email_for_phone(phone_b),
    )
    try:
        for _ in range(serve.DAILY_CHAT_LIMIT):
            serve.record_chat(account_a)
        status_full, payload = post(
            base, "/api/v1/recall", {"query": "پرسش؟"}, phone=phone_a
        )
        status_other, _ = post(
            base, "/api/v1/recall", {"query": "پرسش؟"}, phone=phone_b
        )
    finally:
        stop_gate(server, original)
    # The store keys by the ACCOUNT's email (T21): account A is spent,
    # B is untouched — the phone is only the login handle now.
    assert status_full == 429
    assert "امروز" in payload["detail"]
    assert status_other == 200


# --- the first admin from config (T26: the seed command retired) -----------------


def test_ensure_admin_mints_once_and_never_touches_a_standing_admin(tmp_path):
    db = tmp_path / "accounts.sqlite3"
    original_db = accounts.ACCOUNTS_DB
    accounts.ACCOUNTS_DB = db
    try:
        # The empty store takes the seed: the admin logs in at once.
        assert accounts.ensure_admin("pm@sheet.test", "رمز-مدیر") is True
        assert accounts.verify_login("pm@sheet.test", "رمز-مدیر")["role"] == "admin"
        # A second seeding touches NOTHING — a restart must never
        # quietly re-issue the PM's password, the force path is gone.
        assert accounts.ensure_admin("pm@sheet.test", "رمز-تازه") is False
        assert accounts.verify_login("pm@sheet.test", "رمز-تازه") is None
        assert accounts.verify_login("pm@sheet.test", "رمز-مدیر") is not None
        # A different email refuses too while any admin stands.
        assert accounts.ensure_admin("other@sheet.test", "دیگر") is False
        assert accounts.account_by_email("other@sheet.test") is None
    finally:
        accounts.ACCOUNTS_DB = original_db


def test_ensure_admin_refuses_an_email_an_operator_already_holds(tmp_path):
    db = tmp_path / "accounts.sqlite3"
    original_db = accounts.ACCOUNTS_DB
    accounts.ACCOUNTS_DB = db
    try:
        created = accounts.create_account(
            "pm@sheet.test", "رمز-اپراتور", role="operator"
        )
        assert created is not None
        assert accounts.ensure_admin("pm@sheet.test", "رمز-مدیر") is False
        # The operator Account is untouched by the refused seed.
        assert accounts.verify_login("pm@sheet.test", "رمز-اپراتور")["role"] == (
            "operator"
        )
    finally:
        accounts.ACCOUNTS_DB = original_db


# --- the top-up store (T26) ------------------------------------------------------


def test_credit_balance_lands_toman_on_the_account(tmp_path):
    db = tmp_path / "accounts.sqlite3"
    original_db = accounts.ACCOUNTS_DB
    accounts.ACCOUNTS_DB = db
    try:
        accounts.create_account("op@sheet.test", TEST_PASSWORD, phone="09120000042")
        assert accounts.get_balance("op@sheet.test") == 0
        # The top-up keys by EMAIL — the Account is the identity — and
        # the phone-keyed read answers the same row: the operator sees
        # the new اعتبار wherever the Balance renders.
        new_balance = accounts.credit_balance("op@sheet.test", 250_000)
        assert new_balance == 250_000
        assert accounts.get_balance("op@sheet.test") == 250_000
        assert accounts.credit_balance("op@sheet.test", 7) == 250_007
        assert accounts.get_balance("op@sheet.test") == 250_007
    finally:
        accounts.ACCOUNTS_DB = original_db


def test_credit_balance_refuses_the_unknown_and_the_non_positive(tmp_path):
    db = tmp_path / "accounts.sqlite3"
    original_db = accounts.ACCOUNTS_DB
    accounts.ACCOUNTS_DB = db
    try:
        accounts.create_account("op@sheet.test", TEST_PASSWORD, phone="09120000043")
        assert accounts.credit_balance("noone@sheet.test", 1000) is None
        import pytest

        with pytest.raises(ValueError):
            accounts.credit_balance("op@sheet.test", 0)
        with pytest.raises(ValueError):
            accounts.credit_balance("op@sheet.test", -500)
        # The refusals landed nothing.
        assert accounts.get_balance("op@sheet.test") == 0
    finally:
        accounts.ACCOUNTS_DB = original_db


# --- the sheet's login overlay ---------------------------------------------------


def test_the_sheet_carries_the_login_overlay():
    html = (REPO_ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    assert 'id="auth-gate"' in html
    assert 'id="auth-form"' in html
    assert 'id="auth-email"' in html
    assert 'id="auth-password"' in html
    # The Farsi labels, the error line, and the button.
    assert "ورود" in html
    assert 'id="auth-error"' in html
    # The one-block rule: the overlay hooks every 401 by wrapping the
    # window's fetch — the sheet's own call sites stay untouched — and
    # a successful login reloads with the cookie.
    assert "window.fetch = function" in html
    assert "/auth/login" in html
    assert "window.location.reload()" in html
