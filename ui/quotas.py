"""The daily chat quota: one SQLite record per (account, day) — the
normalize-then-count-and-record functions the ask and phase-2 gates
read. The database path is the module attribute tests patch.

The key is the ACCOUNT's email (T21, GitLab #23 — ADR-0013's contract
step): the phone no longer keys anything, it survives only as the
attached legacy field the Admin uses to map a pre-account store's rows
onto the Account created for it. A store written before T21 has its
rows under a `phone` column; _connect renames the column in place and
ui/migrate.py remaps the values phone→email from the accounts store's
attach map."""

from __future__ import annotations

import datetime
import os
import sqlite3
from pathlib import Path

# A chat is one ask: phase 1 records it, and phase 2 (/quoted-answer)
# belongs to that chat — it needs an Account with a chat today, and never
# counts or checks the limit itself (the 5th chat's own Quoted answer must
# pass). DAILY_CHAT_LIMIT per Account per server-local day.
DAILY_CHAT_LIMIT = 5
QUOTA_DB = Path(
    os.environ.get(
        "SESSION_UI_QUOTA_DB", str(Path(__file__).resolve().parent / "usage.sqlite3")
    )
)

# Persian and Arabic-Indic digits users type into the phone field —
# still the attach form's validation shape, never an identity.
_DIGIT_MAP = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def normalize_phone(raw) -> str:
    """ASCII digits out of whatever was typed; '' unless 10-13 digits."""
    digits = "".join(ch for ch in str(raw).translate(_DIGIT_MAP) if ch.isdigit())
    return digits if 10 <= len(digits) <= 13 else ""


def _today() -> str:
    return datetime.date.today().isoformat()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(QUOTA_DB), timeout=5)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS chats (account TEXT NOT NULL, day TEXT NOT NULL)"
    )
    # A pre-T21 store keys its rows `phone` — the column renames in
    # place (the values follow when ui/migrate.py runs the attach map).
    columns = {row[1] for row in conn.execute("PRAGMA table_info(chats)")}
    if "phone" in columns and "account" not in columns:
        conn.execute("ALTER TABLE chats RENAME COLUMN phone TO account")
        conn.commit()
    return conn


def chats_today(account: str) -> int:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM chats WHERE account = ? AND day = ?",
            (account, _today()),
        ).fetchone()
    finally:
        conn.close()
    return row[0]


def record_chat(account: str) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO chats (account, day) VALUES (?, ?)", (account, _today())
        )
        conn.commit()
    finally:
        conn.close()
