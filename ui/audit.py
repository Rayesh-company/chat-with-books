"""The admin audit log (ADR-0013, T25 GitLab #26): every admin action
appends one row — who did it, what, and on whom — and NOTHING can ever
change or remove a row. An audit that can be edited is not an audit:
the whole point of the log is that the Admin's own later self (or a
successor) cannot quietly rewrite what was issued or topped up. The
append-only property is enforced BELOW the application, in SQLite
itself — BEFORE UPDATE and BEFORE DELETE triggers raise ABORT on the
table — so no future code path, no SQL console, no forgotten migration
can silently mutate history; only INSERT ever succeeds.

The store follows the quotas.py pattern exactly: one SQLite file, its
path over env (`AUDIT_DB`), the module attribute the tests patch. The
read side (`recent`) is the console's only door — the log is read for
the «میز مدیریت» page and nothing else. Nothing here imports the other
ui modules — the container runs this file flat beside them."""

from __future__ import annotations

import datetime
import json
import os
import sqlite3
from pathlib import Path

# The audit log's own file (the quotas.py pattern): ui/ in dev, beside
# the other stores; AUDIT_DB moves it for tests and deploys (compose
# pins it on the persisted /data volume — an audit that evaporates on a
# rebuild is not an audit).
AUDIT_DB = Path(
    os.environ.get("AUDIT_DB", str(Path(__file__).resolve().parent / "audit.sqlite3"))
)

# The action vocabulary (T25 wires the first one; T26's top-ups and
# issuances grow it). Actions are stable wire strings — the console's
# Farsi labels key off them, never off display text.
ACCOUNT_CREATED = "account_created"


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(AUDIT_DB), timeout=5)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS audit ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "ts TEXT NOT NULL, "
        "action TEXT NOT NULL, "
        "actor_email TEXT, "
        "detail TEXT)"
    )
    # The append-only lock (ADR-0013): two triggers, raised at the
    # engine, below every application layer. IF NOT EXISTS, so a store
    # created before this ticket gains the triggers on first touch too.
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS audit_no_update "
        "BEFORE UPDATE ON audit "
        "BEGIN SELECT RAISE(ABORT, 'append-only'); END"
    )
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS audit_no_delete "
        "BEFORE DELETE ON audit "
        "BEGIN SELECT RAISE(ABORT, 'append-only'); END"
    )
    return conn


def _detail_text(detail) -> str:
    """The detail column's text: a dict or list rides as JSON (the
    structured facts of the action — amounts, emails — stay queryable
    later), anything else as its own string, None as ''."""
    if detail is None:
        return ""
    if isinstance(detail, (dict, list)):
        return json.dumps(detail, ensure_ascii=False)
    return str(detail)


def append(action: str, actor_email=None, detail=None) -> dict:
    """One audit row — the ONLY write this module exposes. `action` is
    the stable wire name (ACCOUNT_CREATED and its future siblings),
    `actor_email` the acting Admin's Account, `detail` the action's
    object (a string, or a dict/list that lands as JSON). Returns the
    stored row. There is deliberately no update, no delete, and no
    prune here: the table's triggers refuse both, and the log's size
    is a deploy's concern, never an application's sudden urge to
    forget."""
    row_detail = _detail_text(detail)
    ts = _now()
    actor = None if actor_email is None else str(actor_email)
    conn = _connect()
    try:
        cursor = conn.execute(
            "INSERT INTO audit (ts, action, actor_email, detail)"
            " VALUES (?, ?, ?, ?)",
            (ts, str(action), actor, row_detail),
        )
        conn.commit()
        row_id = cursor.lastrowid
    finally:
        conn.close()
    return {
        "id": row_id,
        "ts": ts,
        "action": str(action),
        "actor_email": actor,
        "detail": row_detail,
    }


def recent(limit: int = 20) -> list:
    """The log's newest rows, newest first — the console's read. A
    plain SELECT: the log is a mirror, and a read must never touch it
    (the triggers would refuse a touch anyway)."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, ts, action, actor_email, detail FROM audit "
            "ORDER BY id DESC LIMIT ?",
            (max(0, int(limit)),),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "id": row[0],
            "ts": row[1],
            "action": row[2],
            "actor_email": row[3],
            "detail": row[4],
        }
        for row in rows
    ]
