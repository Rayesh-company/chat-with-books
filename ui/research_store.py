"""The Research Mode store: one SQLite record per research session and
one per transcript message — the load/save/append functions the
wayfinder engine reads. The database path is the module attribute tests
patch (the quotas.py pattern, its own file so a patched quota DB and a
patched research DB never share a test). Sessions key by the ACCOUNT's
email (T21, GitLab #23 — ADR-0013's contract step): the phone no longer
keys anything; a pre-T21 store's rows remap through ui/migrate.py's
attach map.

The store also owns the write serialization (T11): every session state
row carries a version, every reader's snapshot is stamped with the
version it was read at, and a save from an older base — the in-flight
worker settling over a decision that landed mid-turn — is MERGED, not
written blind: the decide's effect was recorded in an in-memory ledger
exactly once, and the stale write folds it back in before saving. A
restart empties the ledger with the registry (no stale worker write can
follow a restart), so the ledger never needs to outlive the process."""

from __future__ import annotations

import datetime
import json
import os
import sqlite3
import threading
from pathlib import Path

# Research Mode (ADR-0008): a research conversation spans far longer
# than one request — a restart must not throw the investigation away
# (the dive's in-process registry did, by design). Sessions and their
# transcripts persist here; the IN-FLIGHT TURN registry stays in
# ui.research's memory, so a restart still settles an in-flight turn as
# the recorded 404 failure surface while the session itself reloads.
RESEARCH_DB = Path(
    os.environ.get(
        "SESSION_RESEARCH_DB", str(Path(__file__).resolve().parent / "research.sqlite3")
    )
)

# The persistence stamp (T11): load_session and create_session mark the
# state dict with the row version it was read at; save_session compares.
# A write whose base is older than the row is stale — the writer's
# snapshot predates another writer's save — and is merged through
# ``on_stale_save``, never written blind. The key never reaches the
# stored JSON (it is stripped on write and re-stamped on read).
SAVE_BASE_VERSION = "_saved_from_version"

# The decision ledger (T11): the opaque records a decide save parks (its
# kind payload plus the row version it wrote), keyed by session, so a
# LATER stale snapshot write can fold the decision in. The decide's own
# write alone cannot survive the worker's snapshot save — the record is
# what makes the merge possible. In-memory by design: a restart empties
# the registry with it, and the next turn loads fresh from the store.
_DECISION_RECORDS: dict[str, list[dict]] = {}

# One write lock per session (T11): the decide's whole read-modify-write
# and the worker's settle save serialize on it, so a decision never
# straddles a settle save and a settle save never straddles a decision.
# Reentrant — the decide holds it across its own save_session call.
_SAVE_LOCKS: dict[str, threading.RLock] = {}
_SAVE_LOCKS_GUARD = threading.Lock()

# The domain's stale-write merge, installed by ui.research at import:
# ``on_stale_save(session_id, state, records) -> merged state``. The
# store stays domain-dumb — it detects the staleness and hands over the
# recorded decisions; the engine knows how to re-apply them.
on_stale_save = None


def session_save_lock(session_id: str) -> threading.RLock:
    """The session's write lock — the decide flow holds it across its
    whole load-mutate-save cycle (T11), the worker's saves take it for
    each write."""
    with _SAVE_LOCKS_GUARD:
        lock = _SAVE_LOCKS.get(session_id)
        if lock is None:
            lock = _SAVE_LOCKS[session_id] = threading.RLock()
        return lock


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(RESEARCH_DB), timeout=5)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS research_sessions ("
        "id TEXT PRIMARY KEY, account TEXT NOT NULL, "
        "state_json TEXT NOT NULL, created_at TEXT NOT NULL, "
        "updated_at TEXT NOT NULL, "
        "version INTEGER NOT NULL DEFAULT 0)"
    )
    # A pre-T21 store keys its rows `phone` — the column renames in
    # place (the values follow when ui/migrate.py runs the attach map).
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(research_sessions)")
    }
    if "phone" in columns and "account" not in columns:
        conn.execute(
            "ALTER TABLE research_sessions RENAME COLUMN phone TO account"
        )
        conn.commit()
    # A store written before T11 has no version column — top it up in
    # place; every existing row starts at 0.
    columns = {row[1] for row in conn.execute("PRAGMA table_info(research_sessions)")}
    if "version" not in columns:
        conn.execute(
            "ALTER TABLE research_sessions "
            "ADD COLUMN version INTEGER NOT NULL DEFAULT 0"
        )
        conn.commit()
    # The Session-store linkage (ADR-0016): which chat Session (the
    # sheet's sidebar row) a research session belongs to. Nullable —
    # rows written before the linkage carry NULL and stay reachable the
    # old same-tab way; the column tops up in place like `version`.
    columns = {row[1] for row in conn.execute("PRAGMA table_info(research_sessions)")}
    if "chat_session_id" not in columns:
        conn.execute(
            "ALTER TABLE research_sessions ADD COLUMN chat_session_id TEXT"
        )
        conn.commit()
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_research_chat_session"
        " ON research_sessions(account, chat_session_id)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS research_messages ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, "
        "role TEXT NOT NULL, payload_json TEXT NOT NULL, "
        "created_at TEXT NOT NULL)"
    )
    return conn


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _durable(state: dict) -> str:
    """The state as it is stored — the persistence stamp is the
    reader's bookkeeping, never part of the record."""
    return json.dumps(
        {k: v for k, v in state.items() if k != SAVE_BASE_VERSION},
        ensure_ascii=False,
    )


def create_session(session_id: str, account: str, state: dict) -> None:
    """Insert one new session row; the caller owns the id (the engine's
    registry minted it before the first write, so a created session is
    always addressable by the id it already answered with). The state
    is stamped with the row version it now corresponds to — the turn
    that saves it later is checked for staleness like any other
    writer."""
    conn = _connect()
    try:
        stamp = _now()
        state[SAVE_BASE_VERSION] = 0
        conn.execute(
            "INSERT INTO research_sessions (id, account, state_json, created_at,"
            " updated_at, version) VALUES (?, ?, ?, ?, ?, 0)",
            (session_id, account, _durable(state), stamp, stamp),
        )
        conn.commit()
    finally:
        conn.close()


def save_session(session_id: str, state: dict, decision_record: dict = None) -> str:
    """Snapshot one session's research state; '' (not found) when the
    session row does not exist, so a caller never resurrects a closed or
    foreign session by blind write.

    The write is version-checked (T11): a snapshot stamped older than
    the row is stale — its writer started before another writer saved —
    and the decisions recorded since its base are folded back in through
    ``on_stale_save`` before the write, so the in-flight worker's settle
    save carries both its own work and the operator's decision, and a
    stale snapshot never un-closes a session. ``decision_record`` (the
    decide flow's payload) is parked in the ledger under the version
    this write lands, exactly once, for a later stale write to fold."""
    with session_save_lock(session_id):
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT version, state_json FROM research_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                return ""
            stored_version, stored_json = int(row[0]), row[1]
            base = state.get(SAVE_BASE_VERSION)
            if base is None:
                # No stamp: a state built in memory, never read from
                # this row — the pre-T11 blind write, kept for it.
                pass
            elif base < stored_version:
                # Stale: refuse to un-close (the abort's close outlives
                # the in-flight turn's snapshot) and fold the decisions
                # recorded since the snapshot's base back in. A folded
                # record is consumed (it now lives in the written
                # state); records the snapshot already carried are
                # consumed too; records newer than this write's
                # knowledge stay parked for an even older snapshot.
                stored_state = json.loads(stored_json)
                if (
                    isinstance(stored_state, dict)
                    and stored_state.get("closed")
                    and not state.get("closed")
                ):
                    return ""
                records = _DECISION_RECORDS.get(session_id, [])
                replayable = [item for item in records if item["version"] > base]
                if replayable and on_stale_save is not None:
                    state = on_stale_save(session_id, state, replayable)
                    _DECISION_RECORDS[session_id] = [
                        item for item in records if item["version"] <= base
                    ]
            else:
                # Fresh: the loaded state already carries every
                # recorded decision — the ledger's work here is done.
                _DECISION_RECORDS[session_id] = []
            new_version = stored_version + 1
            cursor = conn.execute(
                "UPDATE research_sessions SET state_json = ?, updated_at = ?, "
                "version = ? WHERE id = ?",
                (_durable(state), _now(), new_version, session_id),
            )
            conn.commit()
            if decision_record is not None:
                _DECISION_RECORDS.setdefault(session_id, []).append(
                    {**decision_record, "version": new_version}
                )
            state[SAVE_BASE_VERSION] = new_version
            return "" if cursor.rowcount == 0 else session_id
        finally:
            conn.close()


def load_session(session_id: str):
    """One session row as {"id", "account", "state", "messages"}; None when
    unknown. The messages ride in creation order — the transcript the
    engine's classify pass reads its recent tail from. The state is
    stamped with the row version it was read at (T11) — the writer's
    claim about how fresh its snapshot is."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT id, account, state_json, version FROM research_sessions "
            "WHERE id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        messages = conn.execute(
            "SELECT role, payload_json FROM research_messages "
            "WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
    finally:
        conn.close()
    state = json.loads(row[2])
    if isinstance(state, dict):
        state[SAVE_BASE_VERSION] = int(row[3])
    return {
        "id": row[0],
        "account": row[1],
        "state": state,
        "messages": [
            {"role": role, "payload": json.loads(payload)}
            for role, payload in messages
        ],
    }


def append_message(session_id: str, role: str, payload) -> None:
    """Append one transcript message (role "user" or "assistant"); the
    payload is stored as-is (a string for the user's text, the reply
    blocks for the assistant). Append-only by contract — a decide note
    or a settled reply is never rewritten by a later state save."""
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO research_messages (session_id, role, payload_json,"
            " created_at) VALUES (?, ?, ?, ?)",
            (
                session_id,
                role,
                json.dumps(payload, ensure_ascii=False),
                _now(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def attach_chat_session(session_id: str, account: str, chat_session_id: str) -> bool:
    """Record the chat Session a research session belongs to (ADR-0016):
    the linkage the reload and the session switch need to find the trail
    again — until now the only copy lived in the browser's
    sessionStorage and died at the first sidebar click. The id is
    accepted only from the session's own Account (the same cross-account
    silence every gated write gives) and the write is an UPDATE, never
    an insert — a research session is created exactly once, by
    ensure_session; this call can only label the existing row."""
    conn = _connect()
    try:
        cursor = conn.execute(
            "UPDATE research_sessions SET chat_session_id = ? "
            "WHERE id = ? AND account = ?",
            (chat_session_id, session_id, account),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def latest_for_chat_session(account: str, chat_session_id: str) -> str | None:
    """The id of the Account's newest research session tied to one chat
    Session (ADR-0016) — the resume answer for "which research
    conversation does this sitting reopen?" None when the sitting never
    researched (the common case, answered with the cheapest possible
    query) or the linkage is not the caller's own."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT id FROM research_sessions "
            "WHERE account = ? AND chat_session_id = ? "
            "ORDER BY created_at DESC, updated_at DESC LIMIT 1",
            (account, chat_session_id),
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def chat_session_for(account: str, session_id: str) -> str | None:
    """The reverse lookup (ADR-0016): which chat Session one research
    session belongs to — the refresh reconnect reads it to open the
    WHOLE sitting (chat and research together) instead of the bare
    research thread. None for an unknown, foreign, or pre-linkage
    session."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT chat_session_id FROM research_sessions "
            "WHERE id = ? AND account = ?",
            (session_id, account),
        ).fetchone()
        return row[0] if row and row[0] else None
    finally:
        conn.close()
