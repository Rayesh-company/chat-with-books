"""The Ticket store (the ticket-system wayfinder map, 2026-10-05): one
SQLite row per reported problem — «سازوکار مشخص ثبت بازخورد و اصلاح
خطاهای پاسخ‌دهی». The user chips a specific answer inside a Session;
the ticket CARRIES that answer (question, answer text, book, mode, the
turn's best-effort cost) as a snapshot the Session's deletion can never
cut, plus the ids as logical references — no CASCADE, no live join, the
audit store's durability logic applied to support requests.

The lifecycle is three states and one narrow door back: باز → پاسخ
داده شد → بسته, reopened by the OWNER alone within seven days, once
(the map's decision 02). Filing is free and ungated — the Balance gate
stays the ask's own, never the feedback's — with a soft cap of
non-closed tickets per Account as the anti-spam seam.

The image attachment (the operator's 2026-10-05 amendment): one
optional image at filing time, magic-byte-validated, at most
MAX_IMAGE_BYTES, stored beside the DB in TICKET_FILES_DIR — never a
BLOB, so the store's pages stay light — and served only through the
ownership-gated route. Replies stay text; a ticket is a conversation
about an answer, not a folder.

The store follows the session_store.py pattern exactly: the database
path is the module attribute tests patch, its own file so a patched
Ticket DB never shares a test with the Session, quota, or ledger
stores; every write lands under one lock; timestamps are ISO UTC."""

from __future__ import annotations

import base64
import datetime
import json
import os
import sqlite3
import threading
from pathlib import Path

TICKETS_DB = Path(
    os.environ.get(
        "TICKETS_DB", str(Path(__file__).resolve().parent / "tickets.sqlite3")
    )
)

# Attachment files live beside the DB (the /data volume in the
# container) unless the env moves them — tests patch this to tmp_path.
TICKET_FILES_DIR = os.environ.get("TICKET_FILES_DIR") or str(
    Path(TICKETS_DB).resolve().parent / "ticket_files"
)

_LOCK = threading.Lock()

# The roster (decision 02): fixed, Farsi-labeled, DRAFT until the PM
# row lands in CONTEXT.md. One tuple in the UI's display order; one
# dict the server-rendered inbox and the API answers share — never two
# spellings of the same category.
CATEGORIES = (
    "off_book",
    "bad_ref",
    "garbled",
    "language",
    "cost",
    "slow",
    "other",
)
CATEGORY_LABELS = {
    "off_book": "خارج از کتاب",
    "bad_ref": "ارجاع نادرست",
    "garbled": "متن ناخوانا",
    "language": "خطای نگارش",
    "cost": "هزینه و اعتبار",
    "slow": "کندی یا قطعی",
    "other": "سایر",
}

STATUS_OPEN = "open"
STATUS_ANSWERED = "answered"
STATUS_CLOSED = "closed"
STATUSES = (STATUS_OPEN, STATUS_ANSWERED, STATUS_CLOSED)
STATUS_LABELS = {
    STATUS_OPEN: "باز",
    STATUS_ANSWERED: "پاسخ داده شد",
    STATUS_CLOSED: "بسته",
}

# The filing form's floors and ceilings (decisions 01–02): the free
# text is mandatory and at least ten characters — a category alone is
# not a report; the soft cap counts the Account's non-closed tickets
# and refuses FRIENDLY (an audit row, not a hard error); reopening is
# the owner's alone, within this many days, once per ticket.
BODY_FLOOR_CHARS = 10
SOFT_CAP = 10
REOPEN_WINDOW_DAYS = 7

# The image amendment's limits: one image, at most 2 MB decoded, only
# these four types — sniffed from magic bytes, never the filename the
# client sent (the client sends none).
MAX_IMAGE_BYTES = 2_000_000
_IMAGE_MAGICS = (
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
    (b"\xff\xd8\xff", "jpg", "image/jpeg"),
    (b"GIF87a", "gif", "image/gif"),
    (b"GIF89a", "gif", "image/gif"),
)


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="seconds"
    )


def sniff_image(data: bytes) -> tuple[str, str] | None:
    """(ext, mime) for a supported image's magic bytes, else None — the
    attachment's whole trust check. WebP needs the RIFF header AND the
    lpac at offset 8; the others lead with their signature."""
    if not isinstance(data, (bytes, bytearray)) or not data:
        return None
    head = bytes(data[:16])
    for magic, ext, mime in _IMAGE_MAGICS:
        if head.startswith(magic):
            return ext, mime
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "webp", "image/webp"
    return None


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(TICKETS_DB)
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account TEXT NOT NULL,
            session_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            ask_key TEXT,
            mode TEXT NOT NULL DEFAULT 'chat',
            category TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            body TEXT NOT NULL,
            snapshot TEXT NOT NULL,
            turn_cost_toman REAL,
            has_image INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS ticket_replies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER NOT NULL,
            author_role TEXT NOT NULL,
            author TEXT NOT NULL,
            body TEXT NOT NULL,
            read_at TEXT,
            edited_at TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    # The lifecycle columns (decision 02) top up a pre-amendment store
    # in place — the quotas.py idempotent-ALTER shape.
    columns = {row[1] for row in con.execute("PRAGMA table_info(tickets)")}
    if "closed_at" not in columns:
        con.execute("ALTER TABLE tickets ADD COLUMN closed_at TEXT")
    if "reopen_used" not in columns:
        con.execute(
            "ALTER TABLE tickets ADD COLUMN reopen_used"
            " INTEGER NOT NULL DEFAULT 0"
        )
    if "has_image" not in columns:
        con.execute(
            "ALTER TABLE tickets ADD COLUMN has_image INTEGER NOT NULL"
            " DEFAULT 0"
        )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_tickets_account"
        " ON tickets(account, status)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_replies_ticket"
        " ON ticket_replies(ticket_id)"
    )
    con.commit()
    return con


def _files_dir() -> Path:
    path = Path(TICKET_FILES_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _row_ticket(row) -> dict:
    return {
        "id": row[0],
        "account": row[1],
        "session_id": row[2],
        "message_id": row[3],
        "ask_key": row[4],
        "mode": row[5],
        "category": row[6],
        "category_label": CATEGORY_LABELS.get(row[6], row[6]),
        "status": row[7],
        "status_label": STATUS_LABELS.get(row[7], row[7]),
        "body": row[8],
        "snapshot": json.loads(row[9]),
        "turn_cost_toman": row[10],
        "has_image": bool(row[11]),
        "created_at": row[12],
        "updated_at": row[13],
        "closed_at": row[14],
        "reopen_used": bool(row[15]),
    }


_TICKET_COLS = (
    "id, account, session_id, message_id, ask_key, mode, category,"
    " status, body, snapshot, turn_cost_toman, has_image, created_at,"
    " updated_at, closed_at, reopen_used"
)


def create_ticket(
    account: str,
    session_id: int,
    message_id: int,
    category: str,
    body: str,
    snapshot: dict,
    ask_key: str | None = None,
    mode: str = "chat",
    turn_cost_toman: float | None = None,
    image: bytes | None = None,
) -> dict | str:
    """File one ticket. Returns the row, or one of the refusal codes
    ("bad_category", "short_body", "soft_cap", "bad_image") the route
    answers with its own Farsi. The snapshot is built SERVER-side by
    the route (the client only points at the message); this store
    judges only the form's own floors."""
    if category not in CATEGORIES:
        return "bad_category"
    if not isinstance(body, str) or len(body.strip()) < BODY_FLOOR_CHARS:
        return "short_body"
    if image is not None:
        if len(image) > MAX_IMAGE_BYTES:
            return "bad_image"
        if sniff_image(image) is None:
            return "bad_image"
    with _LOCK:
        con = _connect()
        try:
            open_rows = con.execute(
                "SELECT COUNT(*) FROM tickets WHERE account = ?"
                " AND status != ?",
                (account, STATUS_CLOSED),
            ).fetchone()[0]
            if open_rows >= SOFT_CAP:
                return "soft_cap"
            now = _now()
            cur = con.execute(
                "INSERT INTO tickets (account, session_id, message_id,"
                " ask_key, mode, category, status, body, snapshot,"
                " turn_cost_toman, has_image, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    account,
                    session_id,
                    message_id,
                    ask_key,
                    mode,
                    category,
                    STATUS_OPEN,
                    body,
                    json.dumps(snapshot, ensure_ascii=False),
                    turn_cost_toman,
                    1 if image is not None else 0,
                    now,
                    now,
                ),
            )
            ticket_id = cur.lastrowid
            if image is not None:
                ext, _ = sniff_image(image)
                (_files_dir() / f"ticket-{ticket_id}.{ext}").write_bytes(
                    image
                )
            con.commit()
            return get_ticket(ticket_id, _con=con) or {}
        finally:
            con.close()


def save_image(ticket_id: int, image: bytes) -> bool:
    """Write the attachment beside the DB and flip has_image — the
    route decodes and sniffs first; this is the write half alone."""
    sniffed = sniff_image(image)
    if sniffed is None:
        return False
    ext, _ = sniffed
    with _LOCK:
        con = _connect()
        try:
            con.execute(
                "UPDATE tickets SET has_image = 1 WHERE id = ?",
                (ticket_id,),
            )
            con.commit()
        finally:
            con.close()
    (_files_dir() / f"ticket-{ticket_id}.{ext}").write_bytes(image)
    return True


def read_image(ticket_id: int) -> tuple[bytes, str] | None:
    """The stored image's (bytes, mime), or None when the ticket has no
    attachment — the gated file route's whole read."""
    for _, ext, mime in _IMAGE_MAGICS + (("RIFF", "webp", "image/webp"),):
        path = _files_dir() / f"ticket-{ticket_id}.{ext}"
        if path.exists():
            return path.read_bytes(), mime
    return None


def get_ticket(
    ticket_id: int, account: str | None = None, _con=None
) -> dict | None:
    """One ticket with its thread. account=None is the Admin's read (any
    row); an account reads only its own — another Account's ticket is
    None, the same cross-account silence every gated store gives."""
    con = _con or _connect()
    try:
        row = con.execute(
            f"SELECT {_TICKET_COLS} FROM tickets WHERE id = ?",
            (ticket_id,),
        ).fetchone()
        if row is None:
            return None
        if account is not None and row[1] != account:
            return None
        ticket = _row_ticket(row)
        ticket["replies"] = _replies(con, ticket_id)
        ticket["unread_for_user"] = any(
            r["author_role"] == "admin" and r["read_at"] is None
            for r in ticket["replies"]
        )
        return ticket
    finally:
        if _con is None:
            con.close()


def _replies(con: sqlite3.Connection, ticket_id: int) -> list[dict]:
    return [
        {
            "id": r[0],
            "ticket_id": r[1],
            "author_role": r[2],
            "author": r[3],
            "body": r[4],
            "read_at": r[5],
            "edited_at": r[6],
            "created_at": r[7],
        }
        for r in con.execute(
            "SELECT id, ticket_id, author_role, author, body, read_at,"
            " edited_at, created_at FROM ticket_replies"
            " WHERE ticket_id = ? ORDER BY id",
            (ticket_id,),
        )
    ]


def list_tickets(
    account: str | None = None,
    category: str | None = None,
    status: str | None = None,
    since: str | None = None,
    until: str | None = None,
    cap: int = 100,
) -> list[dict]:
    """The inbox's read: newest-first, filterable by category, status,
    and a created_at window (the manager's «بر اساس دسته و زمان»); an
    account sees its own rows alone. The rows ride light — no threads,
    the body's head as the snippet."""
    clauses, params = [], []
    if account is not None:
        clauses.append("account = ?")
        params.append(account)
    if category in CATEGORIES:
        clauses.append("category = ?")
        params.append(category)
    if status in STATUSES:
        clauses.append("status = ?")
        params.append(status)
    if since:
        clauses.append("created_at >= ?")
        params.append(since)
    if until:
        clauses.append("created_at <= ?")
        params.append(until)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(cap)
    with _LOCK:
        con = _connect()
        try:
            rows = con.execute(
                f"SELECT {_TICKET_COLS} FROM tickets{where}"
                " ORDER BY created_at DESC, id DESC LIMIT ?",
                params,
            ).fetchall()
        finally:
            con.close()
    tickets = []
    for row in rows:
        ticket = _row_ticket(row)
        ticket.pop("snapshot", None)
        ticket["snippet"] = ticket.pop("body")[:90]
        tickets.append(ticket)
    return tickets


def add_reply(
    ticket_id: int, author_role: str, author: str, body: str
) -> dict | str:
    """Append one reply and walk the machine (decision 02): the
    manager's first reply moves باز → پاسخ داده شد; the owner's
    follow-up moves it back to باز. A closed ticket takes no replies —
    the reopen door is the only way back. The owner writes alone (an
    account's reply on another's ticket is silence); the manager is
    any admin the route already gated."""
    if not isinstance(body, str) or not body.strip():
        return "bad_body"
    with _LOCK:
        con = _connect()
        try:
            row = con.execute(
                "SELECT account, status FROM tickets WHERE id = ?",
                (ticket_id,),
            ).fetchone()
            if row is None:
                return "not_found"
            owner, status = row
            if author_role == "user" and owner != author:
                return "not_found"
            if status == STATUS_CLOSED:
                return "closed"
            now = _now()
            con.execute(
                "INSERT INTO ticket_replies (ticket_id, author_role,"
                " author, body, created_at) VALUES (?, ?, ?, ?, ?)",
                (ticket_id, author_role, author, body, now),
            )
            new_status = (
                STATUS_ANSWERED if author_role == "admin" else STATUS_OPEN
            )
            con.execute(
                "UPDATE tickets SET status = ?, updated_at = ? WHERE id = ?",
                (new_status, now, ticket_id),
            )
            con.commit()
            return get_ticket(ticket_id, _con=con) or {}
        finally:
            con.close()


def edit_reply(reply_id: int, admin_email: str, body: str) -> dict | str:
    """The manager edits their OWN reply only (decision 03): the edit
    stamps edited_at, the original body is gone — append-only means no
    deletions and no silent rewrites; the stamp is the honesty. The
    owner's rows are never editable by anyone."""
    if not isinstance(body, str) or not body.strip():
        return "bad_body"
    with _LOCK:
        con = _connect()
        try:
            row = con.execute(
                "SELECT author_role, author, ticket_id FROM"
                " ticket_replies WHERE id = ?",
                (reply_id,),
            ).fetchone()
            if row is None:
                return "not_found"
            if row[0] != "admin" or row[1] != admin_email:
                return "forbidden"
            now = _now()
            con.execute(
                "UPDATE ticket_replies SET body = ?, edited_at = ?"
                " WHERE id = ?",
                (body, now, reply_id),
            )
            con.execute(
                "UPDATE tickets SET updated_at = ? WHERE id = ?",
                (now, row[2]),
            )
            con.commit()
            return get_ticket(row[2], _con=con) or {}
        finally:
            con.close()


def close_ticket(
    ticket_id: int, by_account: str | None = None, by_admin: bool = False
) -> dict | str:
    """Close: the owner's «مشکل حل شد» or the manager's «بستن تیکت» —
    closed_at opens the seven-day reopen window. Idempotent refusal on
    an already-closed ticket keeps the stamp honest."""
    with _LOCK:
        con = _connect()
        try:
            row = con.execute(
                "SELECT account, status FROM tickets WHERE id = ?",
                (ticket_id,),
            ).fetchone()
            if row is None:
                return "not_found"
            owner, status = row
            if not by_admin and owner != by_account:
                return "not_found"
            if status == STATUS_CLOSED:
                return "closed"
            now = _now()
            con.execute(
                "UPDATE tickets SET status = ?, closed_at = ?,"
                " updated_at = ? WHERE id = ?",
                (STATUS_CLOSED, now, now, ticket_id),
            )
            con.commit()
            return get_ticket(ticket_id, _con=con) or {}
        finally:
            con.close()


def reopen_ticket(ticket_id: int, account: str, body: str = "") -> dict | str:
    """The owner's one door back (decision 02): within
    REOPEN_WINDOW_DAYS of closed_at, once per ticket, closed → باز —
    an optional body lands as the reopening's first reply so the thread
    says why. Outside the window or second time: "reopen_denied" (the
    route's friendly Farsi); the answer is a NEW ticket."""
    with _LOCK:
        con = _connect()
        try:
            row = con.execute(
                "SELECT account, status, closed_at, reopen_used FROM"
                " tickets WHERE id = ?",
                (ticket_id,),
            ).fetchone()
            if row is None or row[0] != account:
                return "not_found"
            _, status, closed_at, reopen_used = row
            if status != STATUS_CLOSED:
                return "not_closed"
            if reopen_used:
                return "reopen_denied"
            try:
                closed = datetime.datetime.fromisoformat(closed_at)
            except (TypeError, ValueError):
                return "reopen_denied"
            now_dt = datetime.datetime.fromisoformat(_now())
            if (now_dt - closed).days > REOPEN_WINDOW_DAYS:
                return "reopen_denied"
            now = now_dt.isoformat(timespec="seconds")
            con.execute(
                "UPDATE tickets SET status = ?, closed_at = NULL,"
                " reopen_used = 1, updated_at = ? WHERE id = ?",
                (STATUS_OPEN, now, ticket_id),
            )
            if isinstance(body, str) and body.strip():
                con.execute(
                    "INSERT INTO ticket_replies (ticket_id, author_role,"
                    " author, body, created_at)"
                    " VALUES (?, 'user', ?, ?, ?)",
                    (ticket_id, account, body, now),
                )
            con.commit()
            return get_ticket(ticket_id, _con=con) or {}
        finally:
            con.close()


def mark_admin_replies_read(ticket_id: int, account: str) -> bool:
    """The owner opening a ticket reads it: every unread reply of the
    manager's takes read_at — the badge's other half (the manager's
    «پاسخِ شما هنوز خوانده نشده» rides the same column)."""
    with _LOCK:
        con = _connect()
        try:
            row = con.execute(
                "SELECT account FROM tickets WHERE id = ?", (ticket_id,)
            ).fetchone()
            if row is None or row[0] != account:
                return False
            con.execute(
                "UPDATE ticket_replies SET read_at = ? WHERE ticket_id = ?"
                " AND author_role = 'admin' AND read_at IS NULL",
                (_now(), ticket_id),
            )
            con.commit()
            return True
        finally:
            con.close()


def unread_count(account: str) -> int:
    """The panel badge's number: the account's tickets holding at
    least one unread reply of the manager's — a per-ticket count, not
    per-reply, so one busy thread cannot balloon the dot."""
    with _LOCK:
        con = _connect()
        try:
            return con.execute(
                "SELECT COUNT(DISTINCT t.id) FROM tickets t"
                " JOIN ticket_replies r ON r.ticket_id = t.id"
                " WHERE t.account = ? AND r.author_role = 'admin'"
                " AND r.read_at IS NULL",
                (account,),
            ).fetchone()[0]
        finally:
            con.close()


def open_count() -> int:
    """The console link's badge: every non-closed ticket, all Accounts
    — the manager's whole outstanding queue in one number."""
    with _LOCK:
        con = _connect()
        try:
            return con.execute(
                "SELECT COUNT(*) FROM tickets WHERE status != ?",
                (STATUS_CLOSED,),
            ).fetchone()[0]
        finally:
            con.close()


def decode_image_b64(value) -> bytes | str:
    """The route's decode half: base64 (any whitespace tolerated) to
    bytes, or "bad_image" — the sniff and size checks stay in
    create_ticket so the store's own floors are one place."""
    if not isinstance(value, str) or not value.strip():
        return "bad_image"
    try:
        return base64.b64decode(
            "".join(value.split()), validate=True
        )
    except (ValueError, TypeError):
        return "bad_image"
