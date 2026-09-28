"""The ask chat store (ADR-0014): one SQLite row per ask — the
question, its Book selection, the retrieval pool, the Quote selection,
and the Quoted answer's blocks — so a browser reload restores the whole
sheet instead of an empty page. The store is the research_store's
pattern (its own file, a module-attribute DB path the tests patch);
every write is idempotent per field, keyed by the row the ask minted,
and the reader is one phone-keyed ``latest_chat``.

Why a store of our own and not cognee's session manager: cognee's
(cognee/infrastructure/session) is a cache-backed completion-layer
assistant for its own retrievers — Redis/FsCache history that no-ops
when caching is off — not a durable, phone-keyed, phone-authorized
record of the sheet's phases. The sheet owns its persistence; the
retrieval service stays stateless to us."""

from __future__ import annotations

import datetime
import json
import os
import sqlite3
from pathlib import Path

CHAT_DB = Path(
    os.environ.get(
        "SESSION_CHAT_DB", str(Path(__file__).resolve().parent / "chats.sqlite3")
    )
)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(CHAT_DB), timeout=5)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS chats ("
        "id TEXT PRIMARY KEY, phone TEXT NOT NULL, "
        "question TEXT NOT NULL, datasets_json TEXT NOT NULL, "
        "sources_json TEXT NOT NULL, selections_json TEXT NOT NULL, "
        "blocks_json TEXT NOT NULL, truncated INTEGER NOT NULL DEFAULT 0, "
        "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    return conn


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def create_chat(chat_id: str, phone: str, question: str, datasets, sources) -> None:
    """Insert one ask's row with its founding pool; the caller owns the
    id (the handler minted it before the first write, so the ask is
    always addressable by the id it already answered with)."""
    conn = _connect()
    try:
        stamp = _now()
        conn.execute(
            "INSERT INTO chats (id, phone, question, datasets_json, sources_json,"
            " selections_json, blocks_json, truncated, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)",
            (
                chat_id,
                phone,
                question,
                json.dumps(list(datasets or []), ensure_ascii=False),
                json.dumps(sources, ensure_ascii=False),
                "[]",
                "[]",
                stamp,
                stamp,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _owned(chat_id: str, phone: str) -> bool:
    """The row exists AND belongs to this phone — every phase update's
    guard, so a foreign chat_id in a request body can never write
    another phone's ask."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT 1 FROM chats WHERE id = ? AND phone = ?", (chat_id, phone)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _update(chat_id: str, phone: str, field: str, value) -> None:
    """One column's write, phone-guarded; the row's updated_at rides
    along. A value that is not the column's list shape is ignored — a
    malformed phase reply never corrupts the row."""
    if not isinstance(value, list) or not _owned(chat_id, phone):
        return
    conn = _connect()
    try:
        conn.execute(
            f"UPDATE chats SET {field} = ?, updated_at = ? WHERE id = ?",
            (json.dumps(value, ensure_ascii=False), _now(), chat_id),
        )
        conn.commit()
    finally:
        conn.close()


def update_pool(chat_id: str, phone: str, sources) -> None:
    """The pool column — the ask's founding write and the widen's
    (recall-more) growth land the same way."""
    _update(chat_id, phone, "sources_json", sources)


def update_selections(chat_id: str, phone: str, selections) -> None:
    """The Quote-selection column (ADR-0006): the first answer's
    verbatim Book sentences, kept for the reload."""
    _update(chat_id, phone, "selections_json", selections)


def update_quoted(chat_id: str, phone: str, blocks, truncated: bool = False) -> None:
    """The Quoted-answer column: phase 2's guarded document, kept for
    the reload; truncated rides beside it so the restored sheet shows
    the same continuation note the live one did."""
    if not isinstance(blocks, list) or not _owned(chat_id, phone):
        return
    conn = _connect()
    try:
        conn.execute(
            "UPDATE chats SET blocks_json = ?, truncated = ?, updated_at = ?"
            " WHERE id = ?",
            (
                json.dumps(blocks, ensure_ascii=False),
                1 if truncated else 0,
                _now(),
                chat_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def latest_chat(phone: str):
    """The phone's newest ask row as {"id", "question", "datasets",
    "sources", "selections", "blocks", "truncated", "updated_at"}, or
    None — the reload's restore payload. One phone's reload never
    reads another's ask."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT id, question, datasets_json, sources_json, selections_json,"
            " blocks_json, truncated, updated_at FROM chats WHERE phone = ?"
            " ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (phone,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {
        "id": row[0],
        "question": row[1],
        "datasets": json.loads(row[2]),
        "sources": json.loads(row[3]),
        "selections": json.loads(row[4]),
        "blocks": json.loads(row[5]),
        "truncated": bool(row[6]),
        "updated_at": row[7],
    }
