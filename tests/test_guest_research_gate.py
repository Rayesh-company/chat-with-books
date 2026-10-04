"""The guest cut's contract locks (2026-10-04): an Account the Admin
issues without research — the store carries the flag on the Account
row (default on, the issuance and the console flip are the only
writers), the research door answers 403 under its own Farsi name so a
guest never mistakes a closed door for a spent one, the Balance gate
STILL runs first (a broke guest is broke, not cut), the ask path is
untouched (a guest chats on its Balance exactly like any operator),
/auth/me carries the flag for the sheet's toggle, and the console's
issuance checkbox and research-access form write the same column the
audit log records. External behavior through the real server, store
behavior through the patched module — the house test shape."""

import json
import sys
import urllib.parse
import urllib.request

from tests.conftest import REPO_ROOT
from tests.helpers import (
    ADMIN_EMAIL,
    TEST_PASSWORD,
    cookie_for,
    post,
    raw_get,
    raw_post,
    stop_gate,
    with_gate,
)

sys.path.insert(0, str(REPO_ROOT))

from ui import accounts, serve  # noqa: E402


class NoUpstream:
    """The gate tests' stand-in: nothing past the gate may call an
    upstream — a leak is the finding."""

    def __call__(self, request, timeout=None):
        raise AssertionError(f"upstream called: {request.full_url}")


class CannedUpstream:
    """Answers every upstream call with one canned JSON body — the
    recall path's minimal Cognee (the quota test's shape)."""

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


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def post_form(base, path, fields, cookie=None):
    """One form-encoded POST that refuses to follow the redirect —
    (status, Location); the console form tests' own client."""
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


GUEST_EMAIL = "guest@sheet.test"
GUEST_PASSWORD = "guest-pass"


# --- the store: the flag, the default, the flip --------------------------------


def test_the_store_carries_the_cut_and_its_flip(tmp_path):
    original_db = accounts.ACCOUNTS_DB
    accounts.ACCOUNTS_DB = tmp_path / "accounts.sqlite3"
    try:
        # The default stands: an Account issued plainly keeps research.
        accounts.create_account("plain@sheet.test", TEST_PASSWORD)
        assert accounts.account_by_email("plain@sheet.test")[
            "research_enabled"
        ] is True
        # The guest cut: the issuance-time flag lands on the row, and
        # every read shape carries it (login included — /auth/me reads
        # the same dict).
        accounts.create_account(
            GUEST_EMAIL, GUEST_PASSWORD, research_enabled=False
        )
        assert accounts.account_by_email(GUEST_EMAIL)[
            "research_enabled"
        ] is False
        assert accounts.verify_login(GUEST_EMAIL, GUEST_PASSWORD)[
            "research_enabled"
        ] is False
        assert [
            row["research_enabled"] for row in accounts.list_accounts()
        ] == [True, False]
        # The Admin's flip for a standing Account — and the loud refusal
        # for an unknown email.
        assert accounts.set_research_enabled(GUEST_EMAIL, True) is True
        assert accounts.account_by_email(GUEST_EMAIL)[
            "research_enabled"
        ] is True
        assert accounts.set_research_enabled("no-one@sheet.test", False) is False
    finally:
        accounts.ACCOUNTS_DB = original_db


# --- the gate: 403 under its own name, Balance still first ---------------------


def test_the_research_door_answers_403_under_its_own_name(tmp_path):
    base, server, original = with_gate(tmp_path, NoUpstream())
    try:
        admin_cookie = cookie_for(base, ADMIN_EMAIL)
        # The Admin issues a guest through the JSON door.
        status, payload = raw_post(
            base,
            "/auth/accounts",
            {
                "email": GUEST_EMAIL,
                "password": GUEST_PASSWORD,
                "research_enabled": False,
            },
            cookie=admin_cookie,
        )
        assert status == 200
        # A non-boolean flag is a bad body, never a silent default.
        status, payload = raw_post(
            base,
            "/auth/accounts",
            {
                "email": "other@sheet.test",
                "password": TEST_PASSWORD,
                "research_enabled": "off",
            },
            cookie=admin_cookie,
        )
        assert status == 400
        guest_cookie = cookie_for(base, GUEST_EMAIL, GUEST_PASSWORD)
        # /auth/me carries the cut for the sheet's toggle.
        status, payload = raw_get(base, "/auth/me", cookie=guest_cookie)
        assert status == 200
        assert payload["research_enabled"] is False
        # A funded guest meets the closed door on every research verb —
        # under its own name, never the Balance's.
        accounts.credit_balance(GUEST_EMAIL, 1_000)
        for path in ("/research/message", "/research/decide"):
            status, payload = raw_post(base, path, {}, cookie=guest_cookie)
            assert status == 403, path
            assert payload["detail"] == serve.RESEARCH_DISABLED_403_DETAIL
        # The Balance gate still runs FIRST: a broke guest is broke
        # before it is cut.
        accounts.adjust_balance(GUEST_EMAIL, -1_001)
        status, payload = raw_post(base, "/research/message", {}, cookie=guest_cookie)
        assert status == 402
        # The flip opens the door again — the next research verb passes
        # the cut (and refuses on its own body terms instead).
        accounts.set_research_enabled(GUEST_EMAIL, True)
        status, _ = raw_post(base, "/research/decide", {}, cookie=guest_cookie)
        assert status != 403
    finally:
        stop_gate(server, original)


def test_a_guest_still_chats_on_its_balance(tmp_path):
    base, server, original = with_gate(tmp_path, CannedUpstream())
    try:
        admin_cookie = cookie_for(base, ADMIN_EMAIL)
        status, _ = raw_post(
            base,
            "/auth/accounts",
            {
                "email": GUEST_EMAIL,
                "password": GUEST_PASSWORD,
                "research_enabled": False,
            },
            cookie=admin_cookie,
        )
        assert status == 200
        accounts.credit_balance(GUEST_EMAIL, 1_000)
        guest_cookie = cookie_for(base, GUEST_EMAIL, GUEST_PASSWORD)
        # The ask path never read the cut: the recall door answers 200
        # for the guest exactly as for any operator (the quota test's
        # canned Cognee).
        status, _ = post(
            base, "/api/v1/recall", {"query": "پرسش؟"}, phone="09120000301"
        )  # the operator's sanity beside it
        assert status == 200
        request = urllib.request.Request(
            base + "/api/v1/recall",
            data=json.dumps({"query": "پرسش مهمان؟"}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Cookie": guest_cookie,
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            assert response.status == 200
    finally:
        stop_gate(server, original)


# --- the console: the checkbox, the flip form, the mirror ----------------------


def test_the_console_forms_issue_and_flip_the_cut(tmp_path):
    base, server, original = with_gate(tmp_path, None)
    try:
        admin_cookie = cookie_for(base, ADMIN_EMAIL)
        # The issuance form's unchecked checkbox never rides the body —
        # the absence IS the guest cut.
        status, location = post_form(
            base,
            "/admin/accounts",
            {"email": GUEST_EMAIL, "password": GUEST_PASSWORD},
            cookie=admin_cookie,
        )
        assert status == 303
        assert location == "/admin"
        assert accounts.account_by_email(GUEST_EMAIL)[
            "research_enabled"
        ] is False
        # The flip form writes the same column and audits its own act.
        accounts.credit_balance(GUEST_EMAIL, 1_000)
        guest_cookie = cookie_for(base, GUEST_EMAIL, GUEST_PASSWORD)
        status, _ = post_form(
            base,
            "/admin/research-access",
            {"email": GUEST_EMAIL, "state": "on"},
            cookie=admin_cookie,
        )
        assert status == 303
        from ui import audit

        rows = audit.recent(10)
        assert rows[0]["action"] == audit.RESEARCH_ACCESS_CHANGED
        assert '"research_enabled": true' in rows[0]["detail"]
        # The door is open again for this Account.
        status, _ = raw_post(base, "/research/decide", {}, cookie=guest_cookie)
        assert status != 403
        # A hand-crafted state is a bad body, not a guess.
        status, location = post_form(
            base,
            "/admin/research-access",
            {"email": GUEST_EMAIL, "state": "maybe"},
            cookie=admin_cookie,
        )
        assert status == 303
        assert location == "/admin?error=bad_body"
        status, location = post_form(
            base,
            "/admin/research-access",
            {"email": "no-one@sheet.test", "state": "off"},
            cookie=admin_cookie,
        )
        assert status == 303
        assert location == "/admin?error=unknown_account"
        # The mirror shows the cut: the guest's «ندارد» beside the
        # form that flips it.
        status, _ = post_form(
            base,
            "/admin/research-access",
            {"email": GUEST_EMAIL, "state": "off"},
            cookie=admin_cookie,
        )
        assert status == 303
        request = urllib.request.Request(
            base + "/admin", headers={"Cookie": admin_cookie}
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            html = response.read().decode("utf-8")
        assert "ندارد" in html
        assert "دسترسی پژوهش" in html
        assert 'action="/admin/research-access"' in html
    finally:
        stop_gate(server, original)


def test_the_sheet_names_the_cut_for_the_toggle():
    """The sheet's own half: /auth/me's flag is the toggle's truth, and
    the disabled toggle carries a Farsi note — pinned as markup facts,
    the login-overlay marker's style."""
    sheet = (REPO_ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    assert "research_enabled === false" in sheet
    assert "حالت پژوهش برای این حساب فعال نیست." in sheet
    assert ".mode-toggle:disabled" in sheet
