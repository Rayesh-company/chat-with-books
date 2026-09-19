"""The usage ledger (T22, GitLab #24): every ask-path upstream call lands
one entry on the paying Account — metered where the upstream exposes its
token counts (the composer seam), estimated from text volume where it
does not (Cognee's internal completion is invisible to this layer) — and
an entry that is not metered is MARKED as estimated, never passed off as
measured. The tariff converts tokens to Toman; the rates live in config
(compose env) and never in code, so a price change is an .env edit and a
restart, not a deploy.

The live read: `session_total` sums from the newest `ask` entry
inclusive — the sheet's header shows the open Session's running cost,
and a new ask (which records `ask` first) re-anchors it to zero-plus.

The store follows the quotas.py pattern: one SQLite file, its path over
env (`LEDGER_DB`), the module attribute the tests patch. Nothing here
imports the other ui modules — the container runs this file flat."""

from __future__ import annotations

import math
import os
import sqlite3
import threading
import time
from pathlib import Path

# The tariff (T22): Toman per million tokens, config-held. The defaults
# are the PM's starting dials for glm-5.3-flash on AvalAI — they are
# deliberately NOT measured market prices; the shape (config over code)
# is the decision (ADR-0013 batch D), the numbers are one .env edit away.
TARIFF_INPUT_TOMAN_PER_MTOK = int(
    os.environ.get("TARIFF_INPUT_TOMAN_PER_MTOK", "2000")
)
TARIFF_OUTPUT_TOMAN_PER_MTOK = int(
    os.environ.get("TARIFF_OUTPUT_TOMAN_PER_MTOK", "8000")
)

# The deduction wire (T23, GitLab #25): serve sets this once at import —
# every recorded entry's cost leaves the paying Account's Balance through
# exactly one path, so no capture site can forget it.
_DEDUCTOR = None


def set_deductor(fn) -> None:
    global _DEDUCTOR
    _DEDUCTOR = fn


LEDGER_DB = os.environ.get(
    "LEDGER_DB", str(Path(__file__).resolve().parent / "usage_ledger.sqlite3")
)

_LOCK = threading.Lock()


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(LEDGER_DB)
    con.execute(
        """CREATE TABLE IF NOT EXISTS usage_entries (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               phone TEXT NOT NULL,
               ts TEXT NOT NULL,
               day TEXT NOT NULL,
               kind TEXT NOT NULL,
               metered INTEGER NOT NULL,
               input_tokens INTEGER NOT NULL,
               output_tokens INTEGER NOT NULL,
               cost_toman INTEGER NOT NULL
           )"""
    )
    return con


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


def estimate_tokens(text: str) -> int:
    """The text-volume estimate: Farsi prose runs roughly three characters
    per token through the GLM tokenizer — a coarse mid, honest exactly
    because every entry it produces is marked estimated."""
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 3))


def cost_toman(input_tokens: int, output_tokens: int) -> int:
    """The pure tariff conversion — the function the table-driven tests
    pin, so a rate change is provably arithmetic, never code."""
    cost = (
        input_tokens / 1_000_000 * TARIFF_INPUT_TOMAN_PER_MTOK
        + output_tokens / 1_000_000 * TARIFF_OUTPUT_TOMAN_PER_MTOK
    )
    return int(cost + 0.5)


def record(
    phone: str,
    kind: str,
    input_tokens: int,
    output_tokens: int,
    metered: bool,
) -> dict:
    """One ledger entry: the Account pays for tokens, the entry says
    whether those tokens were measured or estimated."""
    cost = cost_toman(input_tokens, output_tokens)
    with _LOCK:
        con = _connect()
        try:
            con.execute(
                "INSERT INTO usage_entries (phone, ts, day, kind, metered,"
                " input_tokens, output_tokens, cost_toman)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    phone,
                    time.strftime("%Y-%m-%d %H:%M:%S"),
                    _today(),
                    kind,
                    1 if metered else 0,
                    input_tokens,
                    output_tokens,
                    cost,
                ),
            )
            con.commit()
        finally:
            con.close()
    if _DEDUCTOR is not None and cost:
        try:
            _DEDUCTOR(phone, cost)
        except Exception:
            pass  # the meter watches the work; it never breaks it
    return {
        "kind": kind,
        "metered": metered,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_toman": cost,
    }


def record_composer_call(phone: str, kind: str, prompt: str, reply) -> dict | None:
    """One composer-seam call: metered when the upstream's usage block
    rode the reply, estimated from the text volumes when it did not —
    the caller never decides, the reply's own honesty does."""
    usage = reply.get("usage") if isinstance(reply, dict) else None
    if isinstance(usage, dict) and isinstance(
        usage.get("prompt_tokens"), int
    ):
        output = usage.get("completion_tokens")
        output = output if isinstance(output, int) else estimate_tokens(
            _reply_text(reply)
        )
        return record(phone, kind, usage["prompt_tokens"], output, metered=True)
    output_text = _reply_text(reply)
    return record(
        phone,
        kind,
        estimate_tokens(prompt),
        estimate_tokens(output_text),
        metered=False,
    )


def record_size_estimate(phone: str, kind: str, input_bytes: int) -> dict:
    """The ask entry (T22): the gate knows only the request's size before
    the relay streams the answer — an input-side estimate, marked
    estimated like every non-metered entry."""
    return record(
        phone, kind, estimate_tokens("x" * max(0, input_bytes)), 0, metered=False
    )


def _reply_text(reply) -> str:
    """The reply's own completion text, for the output-side estimate."""
    try:
        return reply["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        return ""


def session_total(phone: str) -> int:
    """The open Session's running cost: everything since the newest
    `ask` entry inclusive — the entry a fresh ask records first, so the
    header re-anchors per ask and grows as the phases spend."""
    with _LOCK:
        con = _connect()
        try:
            anchor = con.execute(
                "SELECT id FROM usage_entries WHERE phone = ? AND kind = 'ask'"
                " ORDER BY id DESC LIMIT 1",
                (phone,),
            ).fetchone()
            if anchor is None:
                return 0
            row = con.execute(
                "SELECT COALESCE(SUM(cost_toman), 0) FROM usage_entries"
                " WHERE phone = ? AND id >= ?",
                (phone, anchor[0]),
            ).fetchone()
            return int(row[0])
        finally:
            con.close()


def today_total(phone: str) -> int:
    """The server-local day's spend for the Account — the number the
    profile (T24) and the console (T25) will read."""
    with _LOCK:
        con = _connect()
        try:
            row = con.execute(
                "SELECT COALESCE(SUM(cost_toman), 0) FROM usage_entries"
                " WHERE phone = ? AND day = ?",
                (phone, _today()),
            ).fetchone()
            return int(row[0])
        finally:
            con.close()
