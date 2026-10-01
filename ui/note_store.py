"""The Notebook store (the selection map, wayfinder ticket 08): one
SQLite row per یادداشت — the researcher's durable capture. The
session_store.py pattern exactly: the database path is the module
attribute tests patch, its own file so a patched Notebook DB never
shares a test with the Session, account, quota, ledger, or research
stores.

A note is the POPOVER'S quick-save made durable: the quote (the
selection's cleaned text) is FIXED — it is the source's own words and
never rewritten — while the category and the operator's opinion
(«نظر من») stay editable. The source rides as a JSON snapshot
(book title + page range, or a Session's id and title) so deleting the
Session never cuts the note — the jump may die, the note survives
(the ticket-02 decision). The book-side refs ride as JSON too: the
{page, at, len, full} coordinates in each page's normalized stream,
the exact space matchOnPage/paintMatch consume, so a stored note can
re-highlight deterministically.

Ownership is the cookie-is-the-address rule (ADR-0013): an Account
creates, reads, edits, and deletes only its own rows; another
Account's note is None/False/absent, never a leak."""

from __future__ import annotations

import datetime
import json
import os
import sqlite3
import threading
from pathlib import Path

# The Notebook store's own file (the session_store.py pattern): ui/ in
# dev, the /data volume in deploys; NOTES_DB moves it for tests.
NOTES_DB = Path(
    os.environ.get("NOTES_DB", str(Path(__file__).resolve().parent / "notes.sqlite3"))
)

_LOCK = threading.Lock()

# The quote's safety net: a whole-page selection can be very long, but
# a runaway payload is never a note. (~a dense page of Farsi.)
_TEXT_FLOOR = 8000
_OPINION_FLOOR = 2000
_CATEGORY_FLOOR = 60


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="seconds"
    )


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(NOTES_DB)
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account TEXT NOT NULL,
            text TEXT NOT NULL,
            doc TEXT,
            pages TEXT NOT NULL DEFAULT '[]',
            refs TEXT NOT NULL DEFAULT '[]',
            source TEXT NOT NULL DEFAULT '{}',
            category TEXT NOT NULL DEFAULT '',
            opinion TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_notes_account ON notes(account)"
    )
    return con


def _row_to_note(row) -> dict:
    return {
        "id": row[0],
        "text": row[1],
        "doc": row[2],
        "pages": json.loads(row[3]),
        "refs": json.loads(row[4]),
        "source": json.loads(row[5]),
        "category": row[6],
        "opinion": row[7],
        "created_at": row[8],
        "updated_at": row[9],
    }


_SELECT = (
    "SELECT id, text, doc, pages, refs, source, category, opinion,"
    " created_at, updated_at FROM notes"
)


def create_note(
    account: str,
    text: str,
    doc: str | None = None,
    pages: list | None = None,
    refs: list | None = None,
    source: dict | None = None,
    category: str = "",
    opinion: str = "",
) -> dict:
    """Quick-save one note for the Account. The quote is required and
    fixed from here on; everything else carries the popover's defaults
    (no category, no opinion) until the panel edits them."""
    with _LOCK:
        con = _connect()
        try:
            now = _now()
            cur = con.execute(
                "INSERT INTO notes (account, text, doc, pages, refs,"
                " source, category, opinion, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    account,
                    str(text)[:_TEXT_FLOOR],
                    doc,
                    json.dumps(pages or [], ensure_ascii=False),
                    json.dumps(refs or [], ensure_ascii=False),
                    json.dumps(source or {}, ensure_ascii=False),
                    str(category or "")[:_CATEGORY_FLOOR],
                    str(opinion or "")[:_OPINION_FLOOR],
                    now,
                    now,
                ),
            )
            con.commit()
            return get_note(account, cur.lastrowid) or {}
        finally:
            con.close()


def get_note(account: str, note_id: int) -> dict | None:
    """One of the Account's notes; another Account's is None."""
    con = _connect()
    try:
        row = con.execute(
            _SELECT + " WHERE id = ? AND account = ?", (note_id, account)
        ).fetchone()
        return _row_to_note(row) if row else None
    finally:
        con.close()


def list_notes(account: str, cap: int = 200) -> list[dict]:
    """The panel's read: the Account's notes, newest first — the cap
    bounds the payload, not the store (the session list's shape)."""
    with _LOCK:
        con = _connect()
        try:
            return [
                _row_to_note(row)
                for row in con.execute(
                    _SELECT + " WHERE account = ?"
                    " ORDER BY id DESC LIMIT ?",
                    (account, cap),
                )
            ]
        finally:
            con.close()


def update_note(
    account: str, note_id: int, category: str | None = None,
    opinion: str | None = None,
) -> dict | None:
    """Edit the editable fields only — the quote is the source's own
    words and never rewritten (the ticket-02 rule). A field left None
    keeps its value; a foreign note is None."""
    with _LOCK:
        con = _connect()
        try:
            row = con.execute(
                "SELECT id FROM notes WHERE id = ? AND account = ?",
                (note_id, account),
            ).fetchone()
            if row is None:
                return None
            if category is not None:
                con.execute(
                    "UPDATE notes SET category = ?, updated_at = ?"
                    " WHERE id = ?",
                    (str(category)[:_CATEGORY_FLOOR], _now(), note_id),
                )
            if opinion is not None:
                con.execute(
                    "UPDATE notes SET opinion = ?, updated_at = ?"
                    " WHERE id = ?",
                    (str(opinion)[:_OPINION_FLOOR], _now(), note_id),
                )
            con.commit()
            return get_note(account, note_id)
        finally:
            con.close()


def delete_note(account: str, note_id: int) -> bool:
    """Delete the Account's note; False for a note that is not the
    caller's."""
    with _LOCK:
        con = _connect()
        try:
            cur = con.execute(
                "DELETE FROM notes WHERE id = ? AND account = ?",
                (note_id, account),
            )
            con.commit()
            return cur.rowcount > 0
        finally:
            con.close()


def delete_notes_many(account: str, note_ids: list[int]) -> int:
    """Bulk-delete the caller's notes among the ids; the count that
    actually went (another Account's ids are silently not theirs)."""
    clean = [int(n) for n in note_ids if isinstance(n, (int, float))]
    if not clean:
        return 0
    with _LOCK:
        con = _connect()
        try:
            marks = ",".join("?" * len(clean))
            cur = con.execute(
                f"DELETE FROM notes WHERE account = ? AND id IN ({marks})",
                (account, *clean),
            )
            con.commit()
            return cur.rowcount
        finally:
            con.close()
