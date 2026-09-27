"""The research session's linkage to the Session store (ADR-0016): the
pointer that survives a reload, a sidebar round-trip, and another tab.
Until the linkage landed, the research conversation's only address was
the browser's sessionStorage — the sidebar's restore erased it and the
reload skipped the chat. The store tests run against a patched
RESEARCH_DB (the quotas.py pattern); the endpoint tests run the real
sheet server over the with_gate harness, the same way the research
endpoints' own contract tests do."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.helpers import (  # noqa: E402
    get,
    post,
    stop_gate,
    wait_turn_done,
    with_gate,
)
from tests.test_research_mode import (  # noqa: E402
    ACCOUNT,
    OTHER_ACCOUNT,
    OTHER_PHONE,
    PHONE,
    ResearchUpstream,
)

from ui import research_store, session_store  # noqa: E402


# --- the store's linkage reads and writes ------------------------------------


def test_attach_and_lookup_roundtrip(tmp_path):
    original_db = research_store.RESEARCH_DB
    research_store.RESEARCH_DB = tmp_path / "research.sqlite3"
    try:
        stamps = iter([f"2026-09-27T10:00:0{i}" for i in range(10)])
        original_now = research_store._now
        research_store._now = lambda: next(stamps)
        try:
            research_store.create_session("rs1", ACCOUNT, {"closed": False})
            # No linkage yet — the pre-linkage answer is None, not an error.
            assert research_store.latest_for_chat_session(ACCOUNT, "5") is None
            assert research_store.chat_session_for(ACCOUNT, "rs1") is None

            assert research_store.attach_chat_session("rs1", ACCOUNT, "5") is True
            assert research_store.latest_for_chat_session(ACCOUNT, "5") == "rs1"
            assert research_store.chat_session_for(ACCOUNT, "rs1") == "5"

            # The cross-account silences: another Account reads nothing
            # and writes nothing.
            assert research_store.latest_for_chat_session(OTHER_ACCOUNT, "5") is None
            assert research_store.attach_chat_session("rs1", OTHER_ACCOUNT, "6") is False
            assert research_store.chat_session_for(OTHER_ACCOUNT, "rs1") is None

            # A second research session in the same sitting: the newest
            # is the one the sitting reopens.
            research_store.create_session("rs2", ACCOUNT, {"closed": False})
            research_store.attach_chat_session("rs2", ACCOUNT, "5")
            assert research_store.latest_for_chat_session(ACCOUNT, "5") == "rs2"
        finally:
            research_store._now = original_now
    finally:
        research_store.RESEARCH_DB = original_db


# --- the endpoints over the real sheet server --------------------------------


def test_the_creating_message_links_the_sitting(tmp_path):
    upstream = ResearchUpstream()
    base, server, original = with_gate(tmp_path, upstream)
    try:
        sitting = session_store.create_session(
            ACCOUNT, book="tarhe-kolli", title="نشست پژوهشی"
        )["id"]
        status, body = post(
            base,
            "/research/message",
            {
                "text": "پیام",
                "question": "پرسش؟",
                "chat_session_id": sitting,
            },
            phone=PHONE,
        )
        wait_turn_done(body["turn_id"])
        resumed, payload = get(base, f"/sessions/{sitting}", phone=PHONE)
        _, state = get(
            base,
            f"/research/state?session={body['session_id']}",
            phone=PHONE,
        )
    finally:
        stop_gate(server, original)
    assert status == 202
    # The resume read names the research conversation (ADR-0016).
    assert resumed == 200
    assert payload["research_session_id"] == body["session_id"]
    # The reverse lookup: the refresh reconnect opens the WHOLE sitting.
    assert state["chat_session_id"] == str(sitting)


def test_a_foreign_sitting_is_never_linked(tmp_path):
    upstream = ResearchUpstream()
    base, server, original = with_gate(tmp_path, upstream)
    try:
        foreign = session_store.create_session(OTHER_ACCOUNT, title="دیگری")["id"]
        own = session_store.create_session(ACCOUNT, title="خودی")["id"]
        status, body = post(
            base,
            "/research/message",
            {
                "text": "پیام",
                "question": "پرسش؟",
                "chat_session_id": foreign,
            },
            phone=PHONE,
        )
        wait_turn_done(body["turn_id"])
        _, payload = get(base, f"/sessions/{own}", phone=PHONE)
    finally:
        stop_gate(server, original)
    # The ask itself succeeded (the linkage is bookkeeping, never a gate)
    # but the foreign id attached nowhere: the Account's own sitting
    # carries no research pointer from it.
    assert status == 202
    assert payload["research_session_id"] is None
