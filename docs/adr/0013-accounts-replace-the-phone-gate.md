# ADR 0013 — Accounts replace the honor-system phone gate: email identity, prepaid Toman balance, one Admin

Date: 2026-09-19
Status: accepted
Amends: the access-control decision (ARCHITECTURE §11 — the honor-system phone gate) and the quota chapter of the README

## Context

The grilling session of 2026-09-19 planned the platform's V0.2 (spec2.md) and confronted the access model with two facts it was never built for:

1. **Real money arrives.** Batch D displays live Toman prices per Session and deducts them from a prepaid Balance. The phone gate was deliberately lightweight — "stops casual credit-burn, not a determined caller" — a fine bar while the product charged nothing and burned only API budget. A 10–13 digit header with no verification cannot carry a balance anyone can lose.
2. **The Admin model needs identities.** The PM becomes the platform's only account issuer and its monitor. Honor-system phones have no owner, no history beyond a day's asks, and no role to attach.

Spend was already real before this: one research turn may spend up to 24 upstream calls, and no surface anywhere answered "what does a Session cost in Toman?".

## Decision

- **Account = email + password** (stdlib `pbkdf2`), **created only by the Admin** — no signup page exists. The email choice is the PM's; its price is a one-time migration of the phone-keyed stores (see Consequences).
- **The login page replaces the phone gate.** A stdlib-signed token authenticates every request; the server resolves the Account and derives what the phone header used to carry. The phone number survives as an attached field for store migration and legacy history, then retires as an identity.
- **The 5-ask daily quota stays; the Balance adds on top.** Events deduct Toman per the tariff table; at zero the service stops with the honest Farsi note. Only the Admin tops up.
- **The tariff table lives in config, never code** — changing prices never redeploys the sheet.
- **One Admin role flag, the same login, one server-rendered console**: create accounts, per-account spend/balance/top-up, live turns, failures and diagnoses, quota state. Every admin action appends to an audit log; the console never mutates silently.
- **Explicitly out, layerable later without rework**: SMS OTP, payment gateways.

## Considered options

- **Phone + password** — preserves the stores' keys for free, but rejected: the PM chose email + password, and the migration is small and one-time.
- **Phone + SMS OTP** — stronger than passwords alone, but a provider dependency and per-message cost for a B2B with a handful of Admin-issued accounts; parked.
- **Display-only metering (no Balance)** — a ticker, not control; "real price shown" implies bounded spend. Rejected.
- **Payment gateway (Zarinpal…)** — no self-serve scale to justify it; admin top-up fits the B2B shape. Rejected for now.
- **Keep the honor gate, add credits on top** — money without identity is unauditable; retrofitting identity later costs more than this migration. Rejected.

## Consequences

- `usage.sqlite3` and `research.sqlite3` migrate phone → account id; the Admin attaches each existing phone to the Account created for it, so quotas and session history survive.
- ARCHITECTURE §11 and the README's gate chapters are rewritten at ticket time; the docs tests re-pin. The honor-system sentence retires with the gate.
- The sheet's wire shape barely moves: the token replaces the header, resolved server-side.
- CONTEXT.md gains the vocabulary (Account, Login, Balance, Tariff, Admin, Admin console, Session report); the Persian display rows stay a draft until the PM approves them.
- Next step in the development workflow: `/to-tickets` — accounts → metering → admin as tracer-bullet tickets with blocking edges.
