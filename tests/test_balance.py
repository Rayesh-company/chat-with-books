"""The prepaid Balance (T23, GitLab #25): the Account's اعتبار exists,
migrates in place, and deducts through the ledger's single wire; the
ask, every phase, and the next research turn are stopped at zero with
the Farsi fix — a turn or phase already running finishes; the turn's
own composer calls land kind='turn' entries on the paying Account; and
the daily quota still applies on top of the Balance, never instead."""

import json
import sqlite3

from tests.helpers import (
    TEST_BALANCE_TOMAN,
    ensure_account,
    patch_accounts,
    post,
    stop_gate,
    with_gate,
)
from ui import accounts, dive, ledger, research, research_store, serve
from ui.ledger import record

PHONE = "09120000000"


class FakeUpstream:
    """Stands in for Cognee (the recall relay) and the composer endpoint
    (the turn's classify call) — told apart by URL, the house shape. The
    relay reads the reply's own headers, so the response carries them."""

    def __init__(self, composer_reply=None):
        self.composer_reply = composer_reply

    def __call__(self, request, timeout=None):
        url = request.full_url
        if "chat/completions" in url:
            body = self.composer_reply or json.dumps(
                {"choices": [{"message": {"content": "{}"}}]}
            )
        else:
            body = json.dumps({"answer": "پاسخ", "evidence": []})

        class Response:
            status = 200
            headers = {"Content-Type": "application/json"}

            def __init__(self, body):
                self._body = body

            def read(self):
                return self._body

            def readline(self):
                return b""

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        payload = body if isinstance(body, bytes) else body.encode("utf-8")
        return Response(payload)


def test_an_existing_accounts_store_migrates_in_place(tmp_path):
    """The Balance landed after the first Accounts did — an old store
    opens, gains the column, and keeps its rows working."""
    db = tmp_path / "accounts.sqlite3"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE accounts ("
        "email TEXT PRIMARY KEY, password_hash TEXT NOT NULL, phone TEXT, "
        "role TEXT NOT NULL, created_at TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO accounts VALUES ('old@x.ir', 'hash', '09150000000',"
        " 'operator', '2026-09-01')"
    )
    conn.commit()
    conn.close()

    original = accounts.ACCOUNTS_DB
    accounts.ACCOUNTS_DB = db
    try:
        assert accounts.get_balance("09150000000") == 0
        accounts.adjust_balance("09150000000", 5_000)
        assert accounts.get_balance("09150000000") == 5_000
        assert accounts.deduct_balance("09150000000", 2_000) == 3_000
    finally:
        accounts.ACCOUNTS_DB = original


def test_every_recorded_entry_deducts_through_one_wire(tmp_path):
    """The capture sites never deduct by hand — the ledger's single
    deduction path (wired in serve at import) carries every cost, so no
    site can forget it."""
    patch_accounts(tmp_path)
    ensure_account(PHONE)
    before = accounts.get_balance(PHONE)
    assert before == TEST_BALANCE_TOMAN

    record(PHONE, "picker", 1_000_000, 0, metered=True)  # 2000 at the input rate
    assert accounts.get_balance(PHONE) == before - 2000


def test_the_ask_gate_stops_an_empty_account_with_the_farsi_fix(tmp_path):
    patch_accounts(tmp_path)
    base, server, originals = with_gate(tmp_path, None)
    try:
        ensure_account(PHONE)  # seeds inside with_gate's own accounts DB
        accounts.deduct_balance(PHONE, accounts.get_balance(PHONE))  # drain
        status, payload = post(
            base, "/api/v1/recall", {"query": "پرسش؟"}, phone=PHONE
        )
    finally:
        stop_gate(server, originals)

    assert status == 402
    assert "اعتبار" in payload["detail"] and "شارژ" in payload["detail"]


def test_a_phase_gate_stops_an_empty_account(tmp_path):
    patch_accounts(tmp_path)
    base, server, originals = with_gate(tmp_path, None)
    try:
        ensure_account(PHONE)
        accounts.deduct_balance(PHONE, accounts.get_balance(PHONE))
        status, payload = post(
            base,
            "/quote-selection",
            {"question": "پرسش؟", "sources": [{"reference": "r", "passage": "p"}]},
            phone=PHONE,
        )
    finally:
        stop_gate(server, originals)

    assert status == 402


def test_a_turn_that_cannot_be_paid_for_never_starts(tmp_path):
    """The research turn's start rides the phase gate — an empty Balance
    answers 402 before the turn exists, so nothing runs on credit."""
    patch_accounts(tmp_path)
    base, server, originals = with_gate(tmp_path, None)
    try:
        ensure_account(PHONE)
        accounts.deduct_balance(PHONE, accounts.get_balance(PHONE))
        status, payload = post(
            base,
            "/research/message",
            {"text": "شواهد بیشتری پیدا کن"},
            phone=PHONE,
        )
    finally:
        stop_gate(server, originals)

    assert status == 402


def test_a_funded_account_still_asks_and_the_ask_deducts(tmp_path):
    """The stop is only at zero: a funded account asks normally. A tiny
    ask's honest estimate can round to zero Toman — the entry still
    lands, the balance still moves when the cost rounds up."""
    patch_accounts(tmp_path)
    base, server, originals = with_gate(tmp_path, FakeUpstream())
    try:
        ensure_account(PHONE)
        balance_before = accounts.get_balance(PHONE)
        status, _ = post(
            base,
            "/api/v1/recall",
            {"query": "انسان در اندیشۀ اسلامی چه جایگاهی دارد؟", "datasets": ["70143-336"]},
            phone=PHONE,
        )
        assert status == 200
    finally:
        stop_gate(server, originals)

    assert accounts.get_balance(PHONE) <= balance_before
    rows = ledger_rows()
    assert any(row[0] == "ask" for row in rows), "the ask landed its entry"


def test_the_turns_composer_calls_land_turn_entries(tmp_path, monkeypatch):
    """One scripted turn through the worker seam: the tap fires per
    composer call with the turn's phone, and the ledger carries the
    kind='turn' entries the console (T25) will read — each cost leaving
    the Balance through the one wire."""
    from tests.test_research_mode import classify_reply

    patch_accounts(tmp_path)
    ensure_account(PHONE)
    balance_before = accounts.get_balance(PHONE)
    research_store.RESEARCH_DB = tmp_path / "research.sqlite3"
    session, error = research.ensure_session(
        PHONE, None, "پیام آغازین", "پرسش پژوهش؟", []
    )
    assert session is not None, error

    upstream = FakeUpstream(
        composer_reply=classify_reply(intent="concept_learning", gist="آزمون")
    )
    originals = [(module, module.urlopen) for module in (research, dive)]
    for module, _ in originals:
        module.urlopen = upstream
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    turn = research.ResearchTurn(PHONE, session["id"], "شهود چیست؟")
    research.RESEARCH_REGISTRY[turn.id] = turn
    try:
        research.run_research_turn(turn, session)
    finally:
        for module, original in originals:
            module.urlopen = original

    turn_rows = [row for row in ledger_rows() if row[0] == "turn"]
    assert turn_rows, "the turn's composer calls never reached the ledger"
    assert accounts.get_balance(PHONE) < balance_before, "the turn spent credit"


def test_the_daily_quota_still_applies_on_top(tmp_path, monkeypatch):
    """The Balance never replaces the quota: a funded account that spent
    its five asks is still refused by the quota's own 429."""
    from ui import quotas
    from ui.quotas import DAILY_CHAT_LIMIT, record_chat

    patch_accounts(tmp_path)
    ensure_account(PHONE)
    monkeypatch.setattr(quotas, "QUOTA_DB", str(tmp_path / "usage.sqlite3"))
    for _ in range(DAILY_CHAT_LIMIT):
        record_chat(PHONE)

    base, server, originals = with_gate(tmp_path, FakeUpstream())
    try:
        # with_gate re-patches the quota DB; exhaust THAT file the same way
        for _ in range(DAILY_CHAT_LIMIT):
            record_chat(PHONE)
        status, payload = post(
            base, "/api/v1/recall", {"query": "پرسش؟"}, phone=PHONE
        )
    finally:
        stop_gate(server, originals)

    assert status == 429, "the quota's refusal, not the balance's 402"
    assert "فردا" in payload["detail"]


def ledger_rows():
    conn = sqlite3.connect(str(ledger.LEDGER_DB))
    try:
        return conn.execute(
            "SELECT kind, metered, input_tokens, output_tokens, cost_toman"
            " FROM usage_entries ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
