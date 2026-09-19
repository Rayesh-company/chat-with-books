from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch):
    """Every test's ledger entries land in that test's own file — the
    quotas.py isolation shape, applied once here because the metering
    tap (T22) fires from any gated call, in far too many tests to patch
    one by one. No test's spend ever leaks into another's totals."""
    from ui import ledger

    monkeypatch.setattr(
        ledger, "LEDGER_DB", str(tmp_path / "usage_ledger.sqlite3")
    )


@pytest.fixture(autouse=True)
def _isolated_audit(tmp_path, monkeypatch):
    """Every test's audit rows land in that test's own file — the same
    isolation shape as the ledger's (T25): /auth/accounts appends an
    account_created row from far too many tests to patch one by one,
    and an audit row leaking between tests would be exactly the kind
    of history the log exists to keep honest."""
    from ui import audit

    monkeypatch.setattr(audit, "AUDIT_DB", tmp_path / "audit.sqlite3")


def parse_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values
