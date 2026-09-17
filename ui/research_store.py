"""The Research Mode store: one SQLite record per research session and
one per transcript message — the load/save/append functions the
wayfinder engine reads. The database path is the module attribute tests
patch (the quotas.py pattern, its own file so a patched quota DB and a
patched research DB never share a test)."""

from __future__ import annotations

import datetime
import json
import os
import sqlite3
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


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(RESEARCH_DB), timeout=5)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS research_sessions ("
        "id TEXT PRIMARY KEY, phone TEXT NOT NULL, "
        "state_json TEXT NOT NULL, created_at TEXT NOT NULL, "
        "updated_at TEXT NOT NULL)"
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


def create_session(session_id: str, phone: str, state: dict) -> None:
    """Insert one new session row; the caller owns the id (the engine's
    registry minted it before the first write, so a created session is
    always addressable by the id it already answered with)."""
    conn = _connect()
    try:
        stamp = _now()
        conn.execute(
            "INSERT INTO research_sessions (id, phone, state_json, created_at,"
            " updated_at) VALUES (?, ?, ?, ?, ?)",
            (session_id, phone, json.dumps(state, ensure_ascii=False), stamp, stamp),
        )
        conn.commit()
    finally:
        conn.close()


def save_session(session_id: str, state: dict) -> None:
    """Snapshot one session's research state; '' (not found) when the
    session row does not exist, so a caller never resurrects a closed or
    foreign session by blind write."""
    conn = _connect()
    try:
        cursor = conn.execute(
            "UPDATE research_sessions SET state_json = ?, updated_at = ? "
            "WHERE id = ?",
            (json.dumps(state, ensure_ascii=False), _now(), session_id),
        )
        conn.commit()
        return "" if cursor.rowcount == 0 else session_id
    finally:
        conn.close()


def load_session(session_id: str):
    """One session row as {"id", "phone", "state", "messages"}; None when
    unknown. The messages ride in creation order — the transcript the
    engine's classify pass reads its recent tail from."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT id, phone, state_json FROM research_sessions WHERE id = ?",
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
    return {
        "id": row[0],
        "phone": row[1],
        "state": json.loads(row[2]),
        "messages": [
            {"role": role, "payload": json.loads(payload)}
            for role, payload in messages
        ],
    }


def append_message(session_id: str, role: str, payload) -> None:
    """Append one transcript message (role "user" or "assistant"); the
    payload is stored as-is (a string for the user's text, the reply
    blocks for the assistant)."""
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
