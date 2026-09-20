"""The Account store (ADR-0013): one SQLite record per Account — the
create/verify/attach functions the login gate and the Admin read. The
quotas.py pattern exactly: the database path is the module attribute
tests patch, its own file so a patched accounts DB never shares a test
with the quota or research stores.

An Account is an email and a password, created ONLY by the Admin (no
signup page exists — ADR-0013): the login page replaced the
honor-system phone gate, and the phone number survives as legacy data
attached to the Account, never an identity. The identity resolver in
serve.py derives the phone-keyed stores' key from this attached phone,
normalizing it exactly like normalize_phone on every read — the store
keeps what the Admin attached, the gate decides what it means.
Passwords hash with pbkdf2_hmac-sha256, a per-account random salt, and
a pinned iteration count; verification compares digests in constant
time. There is deliberately no password-reset, no lockout, and no
self-service surface: a handful of Admin-issued accounts does not need
them, and every added door is an unaudited one."""

from __future__ import annotations

import datetime
import hashlib
import hmac
import os
import secrets
import sqlite3
from pathlib import Path

# The Account store's own file (the quotas.py pattern): ui/ in dev,
# beside the other stores; ACCOUNTS_DB moves it for tests and deploys.
ACCOUNTS_DB = Path(
    os.environ.get(
        "ACCOUNTS_DB", str(Path(__file__).resolve().parent / "accounts.sqlite3")
    )
)

# The hash pin: pbkdf2-sha256 at 240k iterations (the recorded OWASP
# floor for 2023+) — pinned in source, never env, so a stored hash can
# never silently meet a weaker scheme.
PBKDF2_ITERATIONS = 240_000
PBKDF2_SCHEME = "pbkdf2_sha256"

# The two roles ADR-0013 names. The Admin issues every other Account
# and tops up Balances; the Session operator is the customer side.
ROLES = ("operator", "admin")


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(ACCOUNTS_DB), timeout=5)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS accounts ("
        "email TEXT PRIMARY KEY, "
        "password_hash TEXT NOT NULL, "
        "phone TEXT, "
        "role TEXT NOT NULL, "
        "created_at TEXT NOT NULL, "
        "balance_toman INTEGER NOT NULL DEFAULT 0)"
    )
    # The Balance (T23, GitLab #25) landed after the first Accounts did —
    # an existing store migrates in place, idempotently, on first touch.
    try:
        conn.execute(
            "ALTER TABLE accounts ADD COLUMN balance_toman INTEGER NOT NULL DEFAULT 0"
        )
    except sqlite3.OperationalError:
        pass  # the column is already there
    return conn


def _normalize_email(email) -> str:
    return str(email or "").strip().lower()


def hash_password(password: str) -> str:
    """One account's hash — scheme$iterations$salt$digest, the salt
    fresh per call so two Accounts with the same password never share a
    stored digest."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS
    )
    return (
        f"{PBKDF2_SCHEME}${PBKDF2_ITERATIONS}"
        f"${salt.hex()}${digest.hex()}"
    )


def verify_password(password: str, stored: str) -> bool:
    """The stored hash's own salt and iteration count, recomputed and
    compared in constant time — and any malformed stored value is
    simply a failed login, never a crash."""
    try:
        scheme, iterations, salt_hex, digest_hex = str(stored).split("$")
        if scheme != PBKDF2_SCHEME:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iterations),
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def _row_to_account(row) -> dict:
    return {
        "email": row[0],
        "phone": row[1],
        "role": row[2],
        "created_at": row[3],
    }


def create_account(email, password, phone=None, role="operator"):
    """Issue one Account (the Admin's act — the HTTP layer enforces the
    role, the store enforces the uniqueness). Returns the account dict,
    or None when the email is already taken: an Account is issued once,
    and a second issuance is the Admin's mistake to see, not a silent
    overwrite. The phone is stored as attached (legacy data — the
    identity resolver normalizes on read); the password is hashed
    here, never stored."""
    email = _normalize_email(email)
    if not email or not password or role not in ROLES:
        return None
    conn = _connect()
    try:
        try:
            conn.execute(
                "INSERT INTO accounts (email, password_hash, phone, role,"
                " created_at) VALUES (?, ?, ?, ?, ?)",
                (email, hash_password(password), phone, role, _now()),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            return None
    finally:
        conn.close()
    return {"email": email, "phone": phone, "role": role}


def verify_login(email, password):
    """The Account behind a login attempt, or None — unknown email and
    wrong password refuse identically (an attacker learns nothing from
    the difference)."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT email, password_hash, phone, role, created_at "
            "FROM accounts WHERE email = ?",
            (_normalize_email(email),),
        ).fetchone()
    finally:
        conn.close()
    if row is None or not verify_password(str(password or ""), row[1]):
        return None
    # The SELECT carries the hash at index 1; the account dict never does.
    return {"email": row[0], "phone": row[2], "role": row[3], "created_at": row[4]}


def account_by_email(email):
    """One Account row (no password hash) by its email; None when
    unknown — the identity resolver's lookup after the token verifies."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT email, phone, role, created_at FROM accounts "
            "WHERE email = ?",
            (_normalize_email(email),),
        ).fetchone()
    finally:
        conn.close()
    return _row_to_account(row) if row is not None else None


def list_accounts():
    """Every Account row (no password hash), creation order — the Admin
    console's mirror (T25, GitLab #26): the balance_toman column rides
    along (T23), so the console shows each Account's own Balance
    without a second round-trip per row. A read like any other: it
    never touches a hash."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT email, phone, role, created_at, balance_toman "
            "FROM accounts ORDER BY rowid"
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "email": row[0],
            "phone": row[1],
            "role": row[2],
            "created_at": row[3],
            "balance_toman": int(row[4]),
        }
        for row in rows
    ]


def get_role(email):
    """The Account's role — 'admin', 'operator', or None when unknown."""
    account = account_by_email(email)
    return account["role"] if account is not None else None


def attach_phone(email, phone) -> bool:
    """Attach the legacy phone to an Account — the Admin's migration
    act: the mapping ui/migrate.py rekeys the pre-account stores'
    rows by (quotas, ledger, research sessions), so a phone's history
    survives under its Account. False when the Account is unknown."""
    conn = _connect()
    try:
        cursor = conn.execute(
            "UPDATE accounts SET phone = ? WHERE email = ?",
            (phone, _normalize_email(email)),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def get_balance(account: str) -> int:
    """The Balance (اعتبار) of this Account — the prepaid Toman the
    metered events deduct from, keyed by the Account's own email
    (T21, GitLab #23): the phone no longer keys anything."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(balance_toman), 0) FROM accounts WHERE email = ?",
            (_normalize_email(account),),
        ).fetchone()
        return int(row[0])
    finally:
        conn.close()


def credit_balance(email, amount: int) -> int | None:
    """The Admin's top-up (T26, GitLab #28): Toman lands on the Account
    keyed by its EMAIL — the Account is the identity now (ADR-0013),
    and the row it updates is the same one the phone-keyed reads
    (get_balance, the profile, /usage/live) answer from, so the
    operator sees the new اعتبار the moment it lands. Returns the new
    balance, or None when no Account carries this email. A non-positive
    amount is a caller's bug, not a user's mistake — ValueError, never
    a silent no-op, and never a deduction wearing a top-up's name."""
    amount = int(amount)
    if amount <= 0:
        raise ValueError("a top-up is positive Toman")
    conn = _connect()
    try:
        cursor = conn.execute(
            "UPDATE accounts SET balance_toman = balance_toman + ?"
            " WHERE email = ?",
            (amount, _normalize_email(email)),
        )
        if cursor.rowcount == 0:
            conn.commit()
            return None
        row = conn.execute(
            "SELECT balance_toman FROM accounts WHERE email = ?",
            (_normalize_email(email),),
        ).fetchone()
        conn.commit()
        return int(row[0])
    finally:
        conn.close()


def adjust_balance(account: str, delta: int) -> int:
    """One Balance change (the Admin's top-up is #28's write path; the
    deduction below is the meter's) — returns the new balance, keyed
    by the Account's email (T21)."""
    conn = _connect()
    try:
        conn.execute(
            "UPDATE accounts SET balance_toman = balance_toman + ? WHERE email = ?",
            (delta, _normalize_email(account)),
        )
        conn.commit()
        row = conn.execute(
            "SELECT COALESCE(SUM(balance_toman), 0) FROM accounts WHERE email = ?",
            (_normalize_email(account),),
        ).fetchone()
        return int(row[0])
    finally:
        conn.close()


def deduct_balance(account: str, amount: int) -> int:
    """The meter's deduction: the cost of one recorded entry off the
    Balance. May land slightly negative — the event that emptied the
    Balance already ran (a running turn finishes, ADR-0013); the gate
    stops the NEXT spend, never the one in flight."""
    return adjust_balance(account, -amount)


def admins_exist() -> bool:
    """Whether any Admin Account exists — the first-admin guard: the
    env seeding (T26) creates an Admin only once, and a deployment
    restart must never quietly mint a second or overwrite the first."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT 1 FROM accounts WHERE role = 'admin' LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    return row is not None


def ensure_admin(email, password) -> bool:
    """The first Admin from config (T26, GitLab #28 — the bootstrap
    seed command's retirement): when no Admin exists, this creates one
    and answers True; when one already stands, it answers False and
    touches NOTHING — a restart that silently re-issued the PM's
    password would be exactly the mutation the audit log exists to
    make loud. The seed is a deployment act, not a console one, so the
    caller (serve.py's startup) owns the audit row and the stderr
    notes. False also when the email is already taken by an operator
    Account — the deployment picks a free email, the refusal is loud."""
    if admins_exist():
        return False
    created = create_account(email, password, phone=None, role="admin")
    return created is not None
