"""The phone gate's quota: one SQLite record per (phone, day) — the
normalize-then-count-and-record functions the ask and phase-2 gates
read. The database path is the module attribute tests patch."""

from __future__ import annotations

import datetime
import os
import sqlite3
from pathlib import Path

# Phone gate (PM call, 2026-09-10, for the public VPS deploy): the sheet
# identifies a Customer by a phone number and each number gets
# DAILY_CHAT_LIMIT chats per server-local day. Honor-system — no SMS
# verification; it stops casual credit-burn, not a determined caller.
# A chat is one ask: phase 1 records it, and phase 2 (/quoted-answer)
# belongs to that chat — it needs a phone with a chat today, and never
# counts or checks the limit itself (the 5th chat's own Quoted answer
# must pass).
DAILY_CHAT_LIMIT = 5
QUOTA_DB = Path(
    os.environ.get(
        "SESSION_UI_QUOTA_DB", str(Path(__file__).resolve().parent / "usage.sqlite3")
    )
)

# Persian and Arabic-Indic digits users type into the phone field.
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
        "CREATE TABLE IF NOT EXISTS chats (phone TEXT NOT NULL, day TEXT NOT NULL)"
    )
    return conn


def chats_today(phone: str) -> int:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM chats WHERE phone = ? AND day = ?",
            (phone, _today()),
        ).fetchone()
    finally:
        conn.close()
    return row[0]


def record_chat(phone: str) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO chats (phone, day) VALUES (?, ?)", (phone, _today())
        )
        conn.commit()
    finally:
        conn.close()
