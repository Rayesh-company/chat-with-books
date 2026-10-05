"""The Session store (T27 stage 3, GitLab #40): one SQLite record per
نشست — the durable history the sheet's sidebar lists and the operator
resumes. The quotas.py/accounts.py pattern exactly: the database path
is the module attribute tests patch, its own file so a patched Session
DB never shares a test with the account, quota, ledger, or research
stores.

The sheet orchestrates its own pipeline (the recall, quote-selection,
and quoted-answer endpoints are stateless operations), so the store is
a durable notebook the SHEET writes: the client appends each turn (the
operator's ask, the assistant's settled article) to the Session its
cookie owns, and reads them back on resume. The server never
reconstructs a conversation it did not see — trust stays where ADR-0013
put it: the cookie IS the address, an Account writes and reads only its
own rows, and the ledger (not this store) remains the money truth.

A Session is one sitting against one Book: created on the sitting's
first ask, titled by that question (truncated exactly as the sheet's
own sidebar truncates), listed newest-first, deleted whole — the
accepted list-v1 shape (2026-09-20): no retention limits, no rename, no
LLM titling, delete only. Deleting a Session hides its transcript; it
never touches the ledger's spend rows — the profile's and console's
numbers stay honest. Resume renders read-only: the stored article is
the settled text, its live interactions (widen, research) belong to the
sitting that is open, not the history."""

from __future__ import annotations

import datetime
import json
import os
import sqlite3
import threading
from pathlib import Path

# The Session store's own file (the quotas.py pattern): ui/ in dev,
# beside the other stores; SESSIONS_DB moves it for tests and deploys.
SESSIONS_DB = Path(
    os.environ.get(
        "SESSIONS_DB", str(Path(__file__).resolve().parent / "sessions.sqlite3")
    )
)

_LOCK = threading.Lock()

# The sidebar's title discipline lives in ONE place the sheet shares:
# the sheet truncates identically client-side (42 chars) — the store's
# own cap is the wider safety net for a client that sends nothing.
_TITLE_FLOOR = 60


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="seconds"
    )


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(SESSIONS_DB)
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account TEXT NOT NULL,
            book TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS session_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL REFERENCES sessions(id)
                ON DELETE CASCADE,
            role TEXT NOT NULL,
            payload TEXT NOT NULL,
            ts TEXT NOT NULL
        )
        """
    )
    # The per-ask settle key (the vanishing-content fix, 2026-09-24):
    # the phase endpoints settle the ask's assistant turn SERVER-SIDE —
    # one row per ask, upgraded in place from the first answer's
    # snapshot to the settled article, so a reload mid-pipeline never
    # leaves a sitting without its answer. A store written before the
    # fix has no column — top it up in place.
    columns = {
        row[1] for row in con.execute("PRAGMA table_info(session_messages)")
    }
    if "ask_key" not in columns:
        con.execute("ALTER TABLE session_messages ADD COLUMN ask_key TEXT")
        con.commit()
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_messages_session"
        " ON session_messages(session_id)"
    )
    return con


def create_session(account: str, book: str = "", title: str = "") -> dict:
    """Open one Session for the Account and return its row. The title is
    the sitting's first question, truncated here as the sheet does —
    the store keeps the roster's v1 shape, no LLM titling."""
    with _LOCK:
        con = _connect()
        try:
            now = _now()
            cur = con.execute(
                "INSERT INTO sessions (account, book, title, created_at,"
                " updated_at) VALUES (?, ?, ?, ?, ?)",
                (account, book, str(title)[:_TITLE_FLOOR], now, now),
            )
            con.commit()
            return get_session(account, cur.lastrowid, _con=con) or {}
        finally:
            con.close()


def append_message(
    account: str, session_id: int, role: str, payload: dict
) -> dict | None:
    """Append one turn to the Account's Session and touch it. The role
    is the sheet's vocabulary ("user" / "assistant"); the payload rides
    as JSON verbatim — the store renders nothing and judges nothing.
    A Session that is not the caller's answers None, the same
    cross-account silence every gated read gives."""
    with _LOCK:
        con = _connect()
        try:
            row = con.execute(
                "SELECT id FROM sessions WHERE id = ? AND account = ?",
                (session_id, account),
            ).fetchone()
            if row is None:
                return None
            now = _now()
            con.execute(
                "INSERT INTO session_messages (session_id, role, payload, ts)"
                " VALUES (?, ?, ?, ?)",
                (session_id, role, json.dumps(payload, ensure_ascii=False), now),
            )
            con.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )
            con.commit()
            return get_session(account, session_id, _con=con)
        finally:
            con.close()


def _settled_over(incoming: dict, existing_json: str) -> bool:
    """True when the incoming settle would DOWNGRADE the stored answer
    (the arrive-order race, 2026-09-27 operator report): the two phase
    endpoints settle the same ask_key from two concurrent threads, and
    whoever lands last used to win — so a slow picker's quote-only
    snapshot could overwrite the settled article (the reload then
    showed only quotes and page labels), and a failed phase 2's empty
    blocks could erase the row entirely. The store now judges shapes,
    not arrival: the article always upgrades the snapshot, an empty
    write never erases a stored answer, and equal shapes keep
    latest-wins. An unreadable stored payload is never a downgrade —
    the incoming write proceeds."""
    try:
        existing = json.loads(existing_json)
    except ValueError:
        return False
    if not isinstance(existing, dict):
        return False
    existing_blocks = existing.get("blocks")
    existing_rich = isinstance(existing_blocks, list) and len(existing_blocks) > 0
    incoming_blocks = incoming.get("blocks")
    incoming_empty = not (
        isinstance(incoming_blocks, list) and len(incoming_blocks) > 0
    )
    if incoming_empty and existing_rich:
        return True
    if (
        incoming.get("selection_snapshot") is True
        and existing_rich
        and not existing.get("selection_snapshot")
    ):
        return True
    return False


def settle_ask(
    account: str, session_id: int, ask_key: str, payload: dict
) -> dict | None:
    """Settle one ask's assistant turn SERVER-SIDE and idempotently
    (the vanishing-content fix, 2026-09-24).

    The phase endpoints (/quote-selection, /quoted-answer) call this
    after they have computed their output, so the sitting's answer is
    durable even when the browser is gone by then — a reload during the
    streamed ask or during phase 2 used to lose everything the client
    had not yet reported. The first settle (the picker's Quote-selection
    snapshot) INSERTS the assistant row; a later settle for the SAME
    ask_key (the phase-2 article) UPDATES that row in place — the
    resumed transcript shows the same single answer per ask the live
    sheet always did, now guaranteed to exist. An UPDATE that would
    downgrade the stored answer (a snapshot arriving after the article,
    an empty write after any content — _settled_over) is refused: the
    row keeps its richer shape. A Session that is not
    the caller's answers None; an empty ask_key is refused the same
    way (nothing to be idempotent about)."""
    if not isinstance(ask_key, str) or not ask_key.strip():
        return None
    with _LOCK:
        con = _connect()
        try:
            row = con.execute(
                "SELECT id FROM sessions WHERE id = ? AND account = ?",
                (session_id, account),
            ).fetchone()
            if row is None:
                return None
            now = _now()
            existing = con.execute(
                "SELECT id FROM session_messages"
                " WHERE session_id = ? AND role = 'assistant' AND ask_key = ?"
                " ORDER BY id LIMIT 1",
                (session_id, ask_key),
            ).fetchone()
            if existing is not None:
                current = con.execute(
                    "SELECT payload FROM session_messages WHERE id = ?",
                    (existing[0],),
                ).fetchone()
                if current is not None and _settled_over(
                    payload, current[0]
                ):
                    # The downgrade keeps the stored answer whole; the
                    # sitting's freshness still moves — the ask happened.
                    return get_session(account, session_id, _con=con)
                con.execute(
                    "UPDATE session_messages SET payload = ?, ts = ?"
                    " WHERE id = ?",
                    (
                        json.dumps(payload, ensure_ascii=False),
                        now,
                        existing[0],
                    ),
                )
            else:
                con.execute(
                    "INSERT INTO session_messages (session_id, role,"
                    " payload, ts, ask_key) VALUES (?, 'assistant', ?, ?, ?)",
                    (
                        session_id,
                        json.dumps(payload, ensure_ascii=False),
                        now,
                        ask_key,
                    ),
                )
            con.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )
            con.commit()
            return get_session(account, session_id, _con=con)
        finally:
            con.close()


def get_session(
    account: str, session_id: int, _con: sqlite3.Connection | None = None
) -> dict | None:
    """One Session with its messages oldest→newest — only the caller's
    own; another Account's Session is None, never a leak."""
    con = _con or _connect()
    try:
        row = con.execute(
            "SELECT id, account, book, title, created_at, updated_at"
            " FROM sessions WHERE id = ? AND account = ?",
            (session_id, account),
        ).fetchone()
        if row is None:
            return None
        messages = [
            {"id": m_id, "role": m_role, "payload": json.loads(m_payload),
             "ts": m_ts, "ask_key": m_ask_key}
            for m_id, m_role, m_payload, m_ts, m_ask_key in con.execute(
                "SELECT id, role, payload, ts, ask_key FROM"
                " session_messages WHERE session_id = ? ORDER BY id",
                (session_id,),
            )
        ]
        return {
            "id": row[0],
            "account": row[1],
            "book": row[2],
            "title": row[3],
            "created_at": row[4],
            "updated_at": row[5],
            "messages": messages,
        }
    finally:
        if _con is None:
            con.close()


def list_sessions(account: str, cap: int = 50) -> list[dict]:
    """The sidebar's read: the Account's Sessions, newest activity
    first, capped — the list-v1 shape (no retention limits behind the
    cap; the cap bounds the payload, not the store)."""
    with _LOCK:
        con = _connect()
        try:
            return [
                {
                    "id": r[0],
                    "book": r[1],
                    "title": r[2],
                    "created_at": r[3],
                    "updated_at": r[4],
                }
                for r in con.execute(
                    "SELECT id, book, title, created_at, updated_at"
                    " FROM sessions WHERE account = ?"
                    " ORDER BY updated_at DESC, id DESC LIMIT ?",
                    (account, cap),
                )
            ]
        finally:
            con.close()


def delete_session(account: str, session_id: int) -> bool:
    """Delete the Account's Session whole — its transcript goes, its
    ledger rows stay (the money truth is not the transcript's to
    erase). False for a Session that is not the caller's."""
    with _LOCK:
        con = _connect()
        try:
            cur = con.execute(
                "DELETE FROM sessions WHERE id = ? AND account = ?",
                (session_id, account),
            )
            con.commit()
            return cur.rowcount > 0
        finally:
            con.close()
