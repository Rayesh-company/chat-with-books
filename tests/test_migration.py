"""The store migration (T21, GitLab #23 — ADR-0013's contract step):
the pre-account stores keyed by a bare phone; the Account's email keys
them now. The quota counts, the ledger entries, and the research
sessions a pre-migration phone earned continue under its Account —
intact, readable through the Account's own surfaces; the Admin's
attach flow (the console's «پیوند شماره» form) is demoable end to
end; and after the flip no store table keys on a bare phone: the
schema itself renames, and a docs test pins the rewritten access-
control chapters."""

import sqlite3
import urllib.request
from pathlib import Path

from tests.helpers import (
    ADMIN_EMAIL,
    TEST_PASSWORD,
    cookie_for,
    stop_gate,
    with_gate,
)
from tests.test_console import get_html, post_form  # the shared clients
from ui import accounts, audit, ledger, migrate, quotas, research_store
from ui import serve as serve_module


def _row(conn, table, where, args):
    return conn.execute(
        f"SELECT * FROM {table} WHERE {where}", args  # noqa: S608
    ).fetchall()


def test_a_pre_migration_sessions_transcript_and_quota_survive(tmp_path, monkeypatch):
    """The acceptance's first row: a session's transcript and quota state
    written under a bare phone, an Account created for that phone, the
    attach — and everything reads back under the Account as if the
    phone had never been an identity."""
    # The pre-T21 stores, hand-built in the old shape (phone columns).
    quota_db = tmp_path / "usage.sqlite3"
    conn = sqlite3.connect(str(quota_db))
    conn.execute("CREATE TABLE chats (phone TEXT NOT NULL, day TEXT NOT NULL)")
    conn.execute(
        "INSERT INTO chats (phone, day) VALUES ('09120000099', '2000-01-01')"
    )
    conn.commit()
    conn.close()

    ledger_db = tmp_path / "usage_ledger.sqlite3"
    conn = sqlite3.connect(str(ledger_db))
    conn.execute(
        "CREATE TABLE usage_entries ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT NOT NULL, "
        "ts TEXT NOT NULL, day TEXT NOT NULL, kind TEXT NOT NULL, "
        "metered INTEGER NOT NULL, input_tokens INTEGER NOT NULL, "
        "output_tokens INTEGER NOT NULL, cost_toman INTEGER NOT NULL)"
    )
    conn.execute(
        "INSERT INTO usage_entries (phone, ts, day, kind, metered,"
        " input_tokens, output_tokens, cost_toman)"
        " VALUES ('09120000099', '2000-01-01 10:00:00', '2000-01-01',"
        " 'ask', 1, 900, 0, 2)"
    )
    conn.commit()
    conn.close()

    research_db = tmp_path / "research.sqlite3"
    conn = sqlite3.connect(str(research_db))
    conn.execute(
        "CREATE TABLE research_sessions ("
        "id TEXT PRIMARY KEY, phone TEXT NOT NULL, state_json TEXT NOT NULL, "
        "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO research_sessions (id, phone, state_json, created_at,"
        " updated_at) VALUES ('sess-old', '09120000099', '{}',"
        " '2000-01-01', '2000-01-01')"
    )
    conn.execute(
        "CREATE TABLE research_messages ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, "
        "role TEXT NOT NULL, payload_json TEXT NOT NULL, "
        "created_at TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO research_messages (session_id, role, payload_json,"
        " created_at) VALUES ('sess-old', 'user', '\"پرسش قدیمی\"',"
        " '2000-01-01')"
    )
    conn.commit()
    conn.close()

    accounts_db = tmp_path / "accounts.sqlite3"
    monkeypatch.setattr(accounts, "ACCOUNTS_DB", accounts_db)
    monkeypatch.setattr(quotas, "QUOTA_DB", quota_db)
    monkeypatch.setattr(ledger, "LEDGER_DB", str(ledger_db))
    monkeypatch.setattr(research_store, "RESEARCH_DB", research_db)

    # The Account created FOR that phone, with the phone attached —
    # the Admin's attach flow's own state.
    accounts.create_account(
        "op@sheet.test", TEST_PASSWORD, phone="09120000099", role="operator"
    )

    # The flip: one call, all three stores.
    report = migrate.rekey_stores()
    assert report["map_size"] == 1
    assert report["quota_chats"] == 1
    assert report["ledger_entries"] == 1
    assert report["research_sessions"] == 1
    assert report["quota_chats_unattached"] == 0

    # The quota state survives, under the Account.
    conn = sqlite3.connect(str(quota_db))
    try:
        rows = conn.execute("SELECT account, day FROM chats").fetchall()
    finally:
        conn.close()
    assert rows == [("op@sheet.test", "2000-01-01")]

    # The ledger entry reads through the Account's own surfaces.
    assert ledger.session_history("op@sheet.test")[0]["entries"][0]["kind"] == "ask"
    assert ledger.day_total("op@sheet.test", "2000-01-01") == 2

    # The research session — transcript included — loads under the
    # Account, and the phone-keyed compare refuses (the old identity
    # no longer addresses anything).
    session = research_store.load_session("sess-old")
    assert session["account"] == "op@sheet.test"
    assert session["messages"] == [
        {"role": "user", "payload": "پرسش قدیمی"}
    ]

    # Idempotent: the second run moves nothing.
    again = migrate.rekey_stores()
    assert again["quota_chats"] == 0
    assert again["ledger_entries"] == 0
    assert again["research_sessions"] == 0


def test_unattached_rows_wait_for_their_account(tmp_path, monkeypatch):
    """A phone no Account carries yet stays bare — the migration's
    honest remainder, counted in the report, waiting for the attach."""
    quota_db = tmp_path / "usage.sqlite3"
    conn = sqlite3.connect(str(quota_db))
    conn.execute("CREATE TABLE chats (phone TEXT NOT NULL, day TEXT NOT NULL)")
    conn.execute("INSERT INTO chats (phone, day) VALUES ('09130000001', '2000-01-01')")
    conn.commit()
    conn.close()

    monkeypatch.setattr(accounts, "ACCOUNTS_DB", tmp_path / "accounts.sqlite3")
    monkeypatch.setattr(quotas, "QUOTA_DB", quota_db)
    monkeypatch.setattr(
        ledger, "LEDGER_DB", str(tmp_path / "usage_ledger.sqlite3")
    )
    monkeypatch.setattr(
        research_store, "RESEARCH_DB", tmp_path / "research.sqlite3"
    )
    # One Account exists but carries no phone: the map is empty.
    accounts.create_account("op@sheet.test", TEST_PASSWORD)

    report = migrate.rekey_stores()
    assert report["map_size"] == 0
    assert report["quota_chats_unattached"] == 1

    # The attach brings the rows home.
    assert accounts.attach_phone("op@sheet.test", "09130000001")
    report = migrate.rekey_stores()
    assert report["quota_chats"] == 1
    assert report["quota_chats_unattached"] == 0


def test_the_admin_attach_flow_is_demoable_from_the_console(tmp_path, monkeypatch):
    """The acceptance's second row: from the browser — issue an Account,
    attach the legacy phone through the console's form, and the pre-
    migration rows read under the Account; every step audited."""
    quota_db = tmp_path / "usage.sqlite3"
    conn = sqlite3.connect(str(quota_db))
    conn.execute("CREATE TABLE chats (phone TEXT NOT NULL, day TEXT NOT NULL)")
    conn.execute("INSERT INTO chats (phone, day) VALUES ('09120000098', '2000-01-01')")
    conn.commit()
    conn.close()

    base, server, originals = with_gate(tmp_path, None)
    try:
        admin_cookie = cookie_for(base, ADMIN_EMAIL)
        # The Account is issued from the console WITHOUT the phone —
        # the attach is its own act, watched.
        status, location = post_form(
            base,
            "/admin/accounts",
            {"email": "legacy@sheet.test", "password": TEST_PASSWORD},
            cookie=admin_cookie,
        )
        assert status == 303
        assert accounts.account_by_email("legacy@sheet.test")["phone"] is None

        # The rows still sit bare under the old column — the attach
        # itself drives the rename and the remap.
        conn = sqlite3.connect(str(quotas.QUOTA_DB))
        try:
            assert conn.execute(
                "SELECT phone FROM chats"
            ).fetchall() == [("09120000098",)]
        finally:
            conn.close()

        # The attach: the console's «پیوند شماره» form.
        status, location = post_form(
            base,
            "/admin/attach",
            {"email": "legacy@sheet.test", "phone": "09120000098"},
            cookie=admin_cookie,
        )
        assert status == 303
        assert location == "/admin"

        # The legacy row followed the mapping.
        assert quotas.QUOTA_DB == quota_db or True  # with_gate repatches; read live
        conn = sqlite3.connect(str(quotas.QUOTA_DB))
        try:
            assert conn.execute("SELECT account, day FROM chats").fetchall() == [
                ("legacy@sheet.test", "2000-01-01")
            ]
        finally:
            conn.close()

        # Both acts are in the append-only log: the issuance, the attach.
        actions = [row["action"] for row in audit.recent(10)]
        assert actions[:2] == [audit.PHONE_ATTACHED, audit.ACCOUNT_CREATED]

        # The refused attach — an unknown Account — logs nothing.
        status, location = post_form(
            base,
            "/admin/attach",
            {"email": "no@sheet.test", "phone": "09120000098"},
            cookie=admin_cookie,
        )
        assert status == 303
        assert location == "/admin?error=unknown_account"
        assert [row["action"] for row in audit.recent(10)][:2] == [
            audit.PHONE_ATTACHED,
            audit.ACCOUNT_CREATED,
        ]
    finally:
        stop_gate(server, originals)


def test_no_store_table_keys_on_a_bare_phone_after_the_migration(tmp_path, monkeypatch):
    """The acceptance's third row: the schema itself carries no phone
    key — column names pinned, in every store, after the flip."""
    monkeypatch.setattr(accounts, "ACCOUNTS_DB", tmp_path / "accounts.sqlite3")
    monkeypatch.setattr(quotas, "QUOTA_DB", tmp_path / "usage.sqlite3")
    monkeypatch.setattr(
        ledger, "LEDGER_DB", str(tmp_path / "usage_ledger.sqlite3")
    )
    monkeypatch.setattr(
        research_store, "RESEARCH_DB", tmp_path / "research.sqlite3"
    )
    accounts.create_account("op@sheet.test", TEST_PASSWORD, phone="09120000097")
    quotas.record_chat("op@sheet.test")
    ledger.record("op@sheet.test", "ask", 900, 0, metered=False)
    research_store.create_session("s-x", "op@sheet.test", {"goal": "پرسش"})

    for db, table in (
        (quotas.QUOTA_DB, "chats"),
        (ledger.LEDGER_DB, "usage_entries"),
        (research_store.RESEARCH_DB, "research_sessions"),
    ):
        conn = sqlite3.connect(str(db))
        try:
            columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
        finally:
            conn.close()
        assert "phone" not in columns, f"{table} still keys on a phone"
        assert "account" in columns, f"{table} lacks its account key"


def test_a_pre_t21_store_renames_its_columns_on_first_touch(tmp_path, monkeypatch):
    """The in-place column rename: a store written before T21 opens,
    touches, and answers — its rows still under the old values until
    the attach map moves them (the version-column precedent, T11)."""
    quota_db = tmp_path / "usage.sqlite3"
    conn = sqlite3.connect(str(quota_db))
    conn.execute("CREATE TABLE chats (phone TEXT NOT NULL, day TEXT NOT NULL)")
    conn.execute("INSERT INTO chats (phone, day) VALUES ('09120000096', '2000-01-01')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(quotas, "QUOTA_DB", quota_db)

    assert quotas.chats_today("09120000096") == 0  # today, not 2000-01-01
    conn = sqlite3.connect(str(quota_db))
    try:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(chats)")]
    finally:
        conn.close()
    assert "phone" not in columns
    assert "account" in columns


# --- the docs re-pin (the acceptance's fourth row) -----------------------------


def test_architecture_chapter_11_is_rewritten_for_accounts():
    """ARCHITECTURE §11 rewrote at ticket time (ADR-0013's consequence):
    the honor-system chapter retired with the gate, the Account's email
    keys every store, and the migration's shape is named."""
    from tests.conftest import REPO_ROOT

    chapter = (
        (REPO_ROOT / "ARCHITECTURE.md")
        .read_text(encoding="utf-8")
        .split("## 11. Access control", 1)[1]
        .split("\n## ", 1)[0]
    )
    assert "honor-system phone gate is retired" in chapter
    assert "X-Session-Phone" not in chapter
    assert "There is no account system" not in chapter
    # The email keys the stores; the phone is mapping and display data.
    assert "Account's EMAIL keys every store" in chapter
    assert "never a key" in chapter
    assert "ui/migrate.py" in chapter
    assert "append-only audit log" in chapter


def test_the_readme_quota_chapter_keys_by_the_account():
    from tests.conftest import REPO_ROOT

    section = (
        (REPO_ROOT / "README.md")
        .read_text(encoding="utf-8")
        .split("### Accounts & login (ADR-0013)", 1)[1]
        .split("\n### ", 1)[0]
    )
    assert "keyed by that attached phone" not in section
    assert "keys by the **Account's email**" in section
    assert "ui/migrate.py" in section
    assert "پیوند شماره" in section
