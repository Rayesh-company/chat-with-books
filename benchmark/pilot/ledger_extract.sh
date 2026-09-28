#!/usr/bin/env bash
# The pilot's ledger extract: the usage_entries rows for the bench
# Accounts, read straight out of /data/usage_ledger.sqlite3 inside the
# session container (the container ships python3, not the sqlite3 CLI)
# and written as CSV on stdout. Redirect into the runs directory.
#
# Usage:
#   ./ledger_extract.sh <out.csv> [account-email ...]
#   ./ledger_extract.sh ledger.csv bench-simple-1@pilot.local bench-research-1@pilot.local
set -euo pipefail

OUT="${1:-ledger.csv}"
shift || true
ACCOUNTS="${*:-}"

HOST="${VPS_HOST:-ubuntu@94.183.176.80}"
PORT="${VPS_PORT:-6041}"

ssh -p "$PORT" "$HOST" "docker exec -i chat-with-books-session python3 -" <<EOF > "$OUT"
import sqlite3, sys, json

accounts = """$ACCOUNTS""".split()
con = sqlite3.connect('/data/usage_ledger.sqlite3')
con.row_factory = sqlite3.Row

print("account,ts,day,kind,metered,input_tokens,output_tokens,cost_toman")
if accounts:
    marks = ",".join("?" for _ in accounts)
    rows = con.execute(
        "SELECT account, ts, day, kind, metered, input_tokens, output_tokens, cost_toman"
        f" FROM usage_entries WHERE account IN ({marks}) ORDER BY id", accounts)
else:
    rows = con.execute(
        "SELECT account, ts, day, kind, metered, input_tokens, output_tokens, cost_toman"
        " FROM usage_entries ORDER BY id")
for row in rows:
    print(",".join(str(row[k]) for k in (
        "account", "ts", "day", "kind", "metered",
        "input_tokens", "output_tokens", "cost_toman")))

balances = {}
for row in con.execute("SELECT account, SUM(cost_toman) s FROM usage_entries GROUP BY account"):
    balances[row["account"]] = row["s"]
import pathlib
try:
    acc = sqlite3.connect('/data/accounts.sqlite3')
    acc.row_factory = sqlite3.Row
    for row in acc.execute("SELECT email, balance_toman FROM accounts"):
        if "pilot.local" in row["email"] or row["email"] in balances:
            print(f"# balance,{row['email']},{row['balance']},ledger_sum={balances.get(row['email'],0)}")
except Exception as exc:
    print(f"# accounts read skipped: {exc}", file=sys.stderr)
EOF

echo "saved: $OUT"
