"""The store migration (T21, GitLab #23 — ADR-0013's contract step):
the pre-account stores keyed their rows by a bare phone number; the
Account's email is the key now. This module is the one place that
carries the flip — it reads the accounts store's attach map (each
attached phone → the Account created for it) and remaps the legacy
rows in the quota store (usage.sqlite3), the usage ledger
(usage_ledger.sqlite3), and the research store (research.sqlite3).

The Admin's attach flow drives it: attach a phone to its Account
(the console's «پیوند شماره» form, or the seed's own attachment) and
the rekey runs — the pre-migration quota counts, ledger entries, and
research sessions continue under the Account as if the phone had
never been an identity. Idempotent by construction: a row already
carrying an email never matches the map's phone keys, and running
twice remaps nothing twice. The column renames themselves happen in
each store's _connect (the pre-T11 version-column precedent); this
module only moves values.

Deployment runs it at startup (serve.py's main), so a store written
before T21 is migrated before the first request lands."""

from __future__ import annotations

try:
    from ui import accounts
    from ui import ledger as ledger_store
    from ui import quotas as quota_store
    from ui import research_store
except ImportError:  # the container runs this file flat beside the modules
    import accounts
    import ledger as ledger_store
    import quotas as quota_store
    import research_store


def attach_map() -> dict:
    """The attached phones → their Accounts' emails, straight from the
    accounts store. A phone attached to no Account maps nowhere and its
    legacy rows wait — the Admin attaches each existing phone number to
    the Account created for it, and the rows follow."""
    mapping = {}
    for row in accounts.list_accounts():
        phone = (row.get("phone") or "").strip()
        if phone:
            mapping[phone] = row["email"]
    return mapping


def _remap(conn, table: str, mapping: dict) -> int:
    """One table's bare-phone values → their Accounts' emails; the
    count of rows that moved. Only exact phone keys remap — a row
    already keyed by an email is untouched."""
    moved = 0
    for phone, email in mapping.items():
        cursor = conn.execute(
            f"UPDATE {table} SET account = ? WHERE account = ?",  # noqa: S608
            (email, phone),
        )
        moved += max(0, cursor.rowcount)
    conn.commit()
    return moved


def _unattached(conn, table: str) -> int:
    """Rows still keyed by a bare phone — no Account carries their
    phone yet. The migration's honest remainder: not an error, a
    TODO the attach flow drives to zero."""
    row = conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE account NOT LIKE '%@%'"  # noqa: S608
    ).fetchone()
    return int(row[0])


def rekey_stores() -> dict:
    """The flip itself: every attached phone's rows move to its
    Account's email across the three stores. Returns the per-store
    moved counts plus each store's unattached remainder, so a deploy
    log can show the migration's honest progress."""
    mapping = attach_map()
    report = {"map_size": len(mapping)}
    for name, store, table in (
        ("quota_chats", quota_store, "chats"),
        ("ledger_entries", ledger_store, "usage_entries"),
        ("research_sessions", research_store, "research_sessions"),
    ):
        conn = store._connect()
        try:
            report[name] = _remap(conn, table, mapping)
            report[f"{name}_unattached"] = _unattached(conn, table)
        finally:
            conn.close()
    return report
