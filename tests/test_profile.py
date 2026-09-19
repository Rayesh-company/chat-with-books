"""The profile (T24, GitLab #27): the Account's own view of its اعتبار
and its Sessions' spend. The ledger's read groups the history into
Sessions by the same anchor discipline `session_total` sums by — each
ask opens a group, the open (newest) one leads, capped; the endpoint
answers exactly the calling Account's numbers (the cookie IS the
address — no id parameter exists to read another's), the Balance rides
from the store and nowhere else; and the honesty badges — the DRAFT
pair that keeps an estimate from ever looking measured — ride the
endpoint's data while the sheet's source pins the same strings."""

import re
from pathlib import Path

from tests.helpers import (
    TEST_BALANCE_TOMAN,
    get,
    raw_get,
    stop_gate,
    with_gate,
)
from ui import accounts, ledger, serve
from ui.ledger import (
    cost_toman,
    record,
    record_size_estimate,
    session_history,
    today_total,
)

PHONE = "09120000000"
OTHER = "09350000000"

# The two-session composition the grouping tests read: each ask's
# input-side estimate (300 / 400 tokens) rounds to 1 toman, the picker
# and the writer each bill 2000 at their rate — so both Sessions sum
# to 2001 and tell each other apart by their metered entry's kind.
FIRST_COST = cost_toman(300, 0) + cost_toman(1_000_000, 0)
SECOND_COST = cost_toman(400, 0) + cost_toman(0, 250_000)


def _seed_two_sessions():
    """One Account's first two Sessions: ask + picker, then a second
    ask + writer — the shape the profile's grouping and badges read."""
    record_size_estimate(PHONE, "ask", 900)  # session one opens
    record(PHONE, "picker", 1_000_000, 0, metered=True)  # 2000, metered
    record_size_estimate(PHONE, "ask", 1200)  # session two re-anchors
    record(PHONE, "writer", 0, 250_000, metered=True)  # 2000, metered


# --- the ledger read ---------------------------------------------------------


def test_the_history_groups_two_asks_into_two_sessions_newest_first(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(accounts, "ACCOUNTS_DB", tmp_path / "accounts.sqlite3")
    _seed_two_sessions()

    groups = session_history(PHONE)

    assert len(groups) == 2, "each ask opens its own Session group"
    newest, oldest = groups  # the open Session leads
    assert [entry["kind"] for entry in oldest["entries"]] == ["ask", "picker"]
    assert [entry["kind"] for entry in newest["entries"]] == ["ask", "writer"]
    assert oldest["cost_toman"] == FIRST_COST
    assert newest["cost_toman"] == SECOND_COST
    assert newest["cost_toman"] == sum(
        entry["cost_toman"] for entry in newest["entries"]
    )
    assert oldest["started"] == oldest["entries"][0]["ts"], (
        "the Session opens at its ask entry's own timestamp"
    )
    assert newest["started"] == newest["entries"][0]["ts"]
    assert newest["started"] >= oldest["started"]


def test_the_grouped_entries_carry_their_honesty(tmp_path, monkeypatch):
    """The metered flag rides every grouped entry — the profile's whole
    honesty depends on the data saying which spends were measured."""
    monkeypatch.setattr(accounts, "ACCOUNTS_DB", tmp_path / "accounts.sqlite3")
    _seed_two_sessions()

    groups = session_history(PHONE)
    by_kind = {entry["kind"]: entry for entry in groups[0]["entries"]}
    assert by_kind["writer"]["metered"] is True
    assert by_kind["ask"]["metered"] is False, "the estimate never passes for a measurement"
    assert {"kind", "metered", "input_tokens", "output_tokens", "cost_toman", "ts"} == set(
        by_kind["writer"]
    )


def test_the_history_caps_at_twenty_sessions_but_the_day_counts_all(
    tmp_path, monkeypatch
):
    """The cap trims the view, never the accounting: twenty Sessions
    render, the day's spend still counts every ask behind them."""
    monkeypatch.setattr(accounts, "ACCOUNTS_DB", tmp_path / "accounts.sqlite3")
    for _ in range(ledger.SESSION_HISTORY_CAP + 5):
        record_size_estimate(PHONE, "ask", 900)

    groups = session_history(PHONE)
    assert len(groups) == ledger.SESSION_HISTORY_CAP
    assert groups[0]["started"] >= groups[-1]["started"], "newest first"
    assert today_total(PHONE) == ledger.SESSION_HISTORY_CAP + 5


def test_the_history_is_per_account(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts, "ACCOUNTS_DB", tmp_path / "accounts.sqlite3")
    _seed_two_sessions()
    assert session_history(OTHER) == []


# --- the endpoint ------------------------------------------------------------


def test_the_endpoint_answers_only_its_own_account(tmp_path):
    """The cookie IS the address: two Accounts against the same server
    read back exactly their own Sessions, and a query string naming
    the other Account changes nothing — there is no id parameter to
    speak of, so no caller can ever fetch another's spend."""
    base, server, originals = with_gate(tmp_path, None)
    try:
        get(base, "/profile/data", phone=PHONE)  # seeds both Accounts
        get(base, "/profile/data", phone=OTHER)
        record_size_estimate(PHONE, "ask", 900)
        record(PHONE, "picker", 1_000_000, 0, metered=True)
        record_size_estimate(OTHER, "ask", 900)

        status, mine = get(base, "/profile/data", phone=PHONE)
        assert status == 200
        status, theirs = get(base, "/profile/data", phone=OTHER)
        assert status == 200

        my_kinds = [
            entry["kind"]
            for session in mine["sessions"]
            for entry in session["entries"]
        ]
        their_kinds = [
            entry["kind"]
            for session in theirs["sessions"]
            for entry in session["entries"]
        ]
        assert "picker" in my_kinds
        assert "picker" not in their_kinds, "one Account never reads another's spend"

        status, still_mine = get(base, f"/profile/data?id={OTHER}", phone=PHONE)
        assert status == 200
        assert still_mine == mine, "no id parameter exists — the cookie decides"
    finally:
        stop_gate(server, originals)


def test_the_session_grouping_rides_the_endpoint(tmp_path):
    base, server, originals = with_gate(tmp_path, None)
    try:
        get(base, "/profile/data", phone=PHONE)
        _seed_two_sessions()

        status, body = get(base, "/profile/data", phone=PHONE)
        assert status == 200
        sessions = body["sessions"]
        assert len(sessions) == 2
        # Newest first: the writer's Session leads, the picker's follows.
        assert [e["kind"] for e in sessions[0]["entries"]] == ["ask", "writer"]
        assert [e["kind"] for e in sessions[1]["entries"]] == ["ask", "picker"]
        assert sessions[0]["cost_toman"] == SECOND_COST
        assert sessions[1]["cost_toman"] == FIRST_COST
        assert sessions[0]["started"] == sessions[0]["entries"][0]["ts"]
    finally:
        stop_gate(server, originals)


def test_the_balance_rides_the_response_and_matches_the_store(tmp_path):
    base, server, originals = with_gate(tmp_path, None)
    try:
        status, body = get(base, "/profile/data", phone=PHONE)
        assert status == 200
        assert body["balance_toman"] == accounts.get_balance(PHONE)
        assert body["balance_toman"] == TEST_BALANCE_TOMAN
        assert body["today_toman"] == ledger.today_total(PHONE) == 0

        record(PHONE, "picker", 1_000_000, 0, metered=True)  # 2000 off the Balance

        status, body = get(base, "/profile/data", phone=PHONE)
        assert status == 200
        assert body["balance_toman"] == accounts.get_balance(PHONE)
        assert body["balance_toman"] == TEST_BALANCE_TOMAN - cost_toman(1_000_000, 0)
        assert body["today_toman"] == today_total(PHONE) == cost_toman(1_000_000, 0)
    finally:
        stop_gate(server, originals)


def test_the_badge_vocabulary_rides_the_data_and_pins_the_sheet(tmp_path):
    """The distinction is server-owned: each entry's badge — the DRAFT
    pair in serve.py — rides the payload, and the sheet's source carries
    the same two strings (its render is client-side JS the tests cannot
    execute, so the light source-lock is what holds the two together)."""
    base, server, originals = with_gate(tmp_path, None)
    try:
        get(base, "/profile/data", phone=PHONE)
        record_size_estimate(PHONE, "ask", 900)  # estimated
        record(PHONE, "picker", 1_000_000, 0, metered=True)  # metered

        status, body = get(base, "/profile/data", phone=PHONE)
        assert status == 200
        entries = {
            entry["kind"]: entry for entry in body["sessions"][0]["entries"]
        }
        assert entries["picker"]["badge"] == serve.PROFILE_METERED_BADGE
        assert entries["picker"]["badge"] == "اندازه‌گیری‌شده"
        assert entries["picker"]["metered"] is True
        assert entries["ask"]["badge"] == serve.PROFILE_ESTIMATED_BADGE
        assert entries["ask"]["badge"] == "تخمینی"
        assert entries["ask"]["metered"] is False
        assert entries["ask"]["badge"] != entries["picker"]["badge"], (
            "an estimate must never look measured"
        )
    finally:
        stop_gate(server, originals)

    # The light source-lock: the sheet's DRAFT comment documents the
    # same vocabulary and its render reads the endpoint's badges.
    html = (Path(serve.__file__).parent / "index.html").read_text(
        encoding="utf-8"
    )
    assert serve.PROFILE_METERED_BADGE in html
    assert serve.PROFILE_ESTIMATED_BADGE in html
    assert "/profile/data" in html


def test_the_anonymous_profile_refuses(tmp_path):
    base, server, originals = with_gate(tmp_path, None)
    try:
        status, body = raw_get(base, "/profile/data")
        assert status == 401
    finally:
        stop_gate(server, originals)


# --- the config contract -----------------------------------------------------


def test_the_image_ships_every_module_serve_imports():
    """T24 needed no new module — the ledger, the Account store, and the
    facade suffice — and this lock keeps it so: every ui/*.py the
    facade names in its dual imports must ride ui/Dockerfile's COPY, or
    the container ships a sheet that cannot start. The lock speaks for
    the whole COPY line, so a future module that joins serve.py's
    imports cannot slip past the image."""
    ui_dir = Path(serve.__file__).parent
    serve_src = (ui_dir / "serve.py").read_text(encoding="utf-8")
    local = {path.stem for path in ui_dir.glob("*.py")} - {"__init__"}
    named = set(re.findall(r"from\s+(?:ui\.)?(\w+)\s+import", serve_src))
    named |= {
        match.group(1)
        for match in re.finditer(
            r"^\s*import\s+(?:ui\.)?(\w+)\s*$", serve_src, re.M
        )
    }
    named &= local
    dockerfile = (ui_dir / "Dockerfile").read_text(encoding="utf-8")
    copy_line = next(
        line for line in dockerfile.splitlines() if line.startswith("COPY serve.py")
    )
    assert named, "the facade's own modules must be discoverable"
    for name in sorted(named):
        assert f"{name}.py" in copy_line, f"{name}.py rides the image"
    assert "index.html" in copy_line, "the sheet — the profile view with it"
