#!/usr/bin/env python3
"""Mint the first Admin Account (ADR-0013) — the one door the sheet's
Admin-only account creation needs before any Account exists.

    python scripts/bootstrap_admin.py <email> <password> [--force]

The first run creates the Admin and prints the Farsi success line; a
second run refuses while an Admin stands (--force re-issues the given
credentials instead — the PM's recovery path). The store is the same
ui/accounts.py file the sheet reads (ACCOUNTS_DB moves it), so the
minted Admin can log in immediately."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# The script lives in scripts/; the store lives in ui/ — put the repo
# root on the path so the import works from any working directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ui.accounts import bootstrap_admin  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Mint the first Admin Account (ADR-0013)."
    )
    parser.add_argument("email", help="the Admin Account's email")
    parser.add_argument("password", help="the Admin Account's password")
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-issue the credentials even when an Admin already exists",
    )
    args = parser.parse_args()

    outcome = bootstrap_admin(args.email, args.password, force=args.force)
    if outcome == "exists":
        print(
            "یک مدیر از قبل وجود دارد؛ ساختن مدیر تازه رد شد. "
            "برای صدور دوبارهٔ همان حساب از --force استفاده کنید."
        )
        return 1
    if outcome == "reissued":
        print(f"حساب مدیر دوباره صادر شد: {args.email}")
        return 0
    print(f"حساب مدیر ساخته شد: {args.email} — از همین ایمیل و گذرواژه وارد شوید.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
