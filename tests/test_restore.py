"""The restore drill (T15, GitLab #21): scripts/restore.sh's dry-run seam and
its textual safety rules. The drill's real mode restores the memory into a
throwaway Postgres on port 5433 — Docker, a ~356 MB archive and minutes of
pg_restore stay far outside the suite — so the suite holds the seam that
promises to touch nothing: --dry-run (the six members verified via tar -tzf,
the plan printed) and the script's own text, where the absolute safety rules
live: never the live containers, never host port 5432, never the live repo
tree, never .env contents."""

import os
import subprocess
import tarfile
from pathlib import Path

from tests.conftest import REPO_ROOT
from tests.test_backup import _find_bash

RESTORE_SCRIPT = REPO_ROOT / "scripts" / "restore.sh"
README = REPO_ROOT / "README.md"
DRILL_DOC = REPO_ROOT / "docs" / "restore-drill.md"

# The seven members a backup.sh archive must carry — a partial archive
# restores a partial memory, so the drill refuses one and names what is
# missing.
ARCHIVE_MEMBERS = (
    "cognee_db.dump",
    "usage.sqlite3",
    "research.sqlite3",
    "chats.sqlite3",
    "env/.env",
    "books/tarhe-kolli.pdf",
    "books/70143-336.pdf",
    "code.tar.gz",
)


def run_restore(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_find_bash(), RESTORE_SCRIPT.as_posix(), *args],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        timeout=120,
    )


def make_archive(tmp_path: Path, missing: str | None = None) -> Path:
    """A tiny stand-in tar.gz in backup.sh's shape (no ./ prefix is required —
    the script strips it): every member a byte or two, because the dry-run
    reads names, never contents."""
    staging = tmp_path / "staging"
    (staging / "env").mkdir(parents=True)
    (staging / "books").mkdir()
    payloads = {
        "cognee_db.dump": b"PGDMP-stand-in",
        "usage.sqlite3": b"SQLite format 3\x00 stand-in",
        "research.sqlite3": b"SQLite format 3\x00 stand-in",
        "chats.sqlite3": b"SQLite format 3\x00 stand-in",
        "env/.env": b"LLM_API_KEY=stand-in-not-a-real-key\n",
        "books/tarhe-kolli.pdf": b"%PDF-1.7 stand-in",
        "books/70143-336.pdf": b"%PDF-1.7 stand-in",
        "code.tar.gz": b"stand-in code tar",
    }
    for rel, data in payloads.items():
        if rel == missing:
            continue
        (staging / rel).write_bytes(data)
    archive = tmp_path / "chat-with-books-backup-20260919-033000.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for path in sorted(staging.rglob("*")):
            tar.add(path, arcname=path.relative_to(staging).as_posix())
    return archive


def test_dry_run_verifies_all_seven_members_and_prints_the_plan(tmp_path):
    """A complete archive passes the seam: every member named, the plan
    printed, and nothing anywhere touched."""
    archive = make_archive(tmp_path)

    result = run_restore([archive.as_posix(), "--dry-run"])

    assert result.returncode == 0, result.stderr
    for member in ARCHIVE_MEMBERS:
        assert member in result.stdout, f"the dry-run never named {member}"
    assert "dry-run ok" in result.stdout
    # The seam's promise: no Docker call, no extraction — the plan is text.
    assert "plan:" in result.stdout


def test_dry_run_refuses_an_incomplete_archive_and_names_the_member(tmp_path):
    """A backup missing a member is a partial memory; the drill refuses and
    names exactly what is gone — research.sqlite3 here."""
    archive = make_archive(tmp_path, missing="research.sqlite3")

    result = run_restore([archive.as_posix(), "--dry-run"])

    assert result.returncode != 0
    assert "research.sqlite3" in result.stderr


def test_the_plan_names_the_throwaway_stack_and_the_handoff_flags(tmp_path):
    """The plan is the drill's mirror: the throwaway container prefix, host
    port 5433, the pgvector image, and the HANDOFF pg_restore flags verbatim —
    the restore path this whole ticket exists to rehearse."""
    archive = make_archive(tmp_path)

    result = run_restore([archive.as_posix(), "--dry-run"])

    assert result.returncode == 0, result.stderr
    stdout = result.stdout
    assert "cwb-drill-pg-<stamp>" in stdout, "the plan never names the throwaway container"
    assert "5433" in stdout, "the plan never names the drill's host port"
    assert "pgvector/pgvector:pg17" in stdout, "the plan never names the throwaway image"
    assert "--clean" in stdout and "--if-exists" in stdout, "the plan must carry the HANDOFF flags"
    assert "pg_restore -U cognee -d cognee_db" in stdout
    assert "mktemp" in stdout, "the plan never says where extraction lands"


def test_the_safety_rules_hold_textually():
    """The absolute rules, pinned on the script's own text: 5432 never appears
    as a host bind target (only container-side or in the refusal guard), the
    live stack's containers are never a docker target, extraction always lands
    in the mktemp dir, and .env is never read into the transcript."""
    text = RESTORE_SCRIPT.read_text(encoding="utf-8")
    lines = text.splitlines()

    # Rule 2: never bind host port 5432 — the host side of -p is always the
    # DRILL_PORT variable; the only literal 5432s are container-side or guard.
    assert "-p 5432" not in text
    assert "5432:5432" not in text
    assert any('"5432"' in line and "die" in line for line in lines), "the 5432 refusal guard is gone"
    assert "5433" in text, "the drill's own host port disappeared from the script"

    # Rule 1: never touch the live containers — the exact live postgres name
    # is never written, and no line that runs docker names the live stack.
    assert "chat-with-books-postgres" not in text
    for line in lines:
        if "chat-with-books" in line:
            assert "docker run" not in line and "docker exec" not in line and "docker cp" not in line and "docker stop" not in line and "docker rm" not in line, f"a docker call targets the live stack: {line}"
    assert "chat-with-books" in text, "the refusal guard against the live prefix is gone"

    # Rule 3: never write into the live repo tree — extraction is mktemp + -C.
    assert "mktemp -d" in text
    extract_lines = [line for line in lines if "-xzf" in line]
    assert extract_lines, "the script never extracts"
    assert all('-C "$DRILL_TMP"' in line for line in extract_lines), "extraction does not target the temp dir"

    # Rule 4: never print .env contents — presence by filename only.
    for line in lines:
        if ".env" in line:
            assert "cat" not in line, f".env is read into the transcript: {line}"
    assert "[ -s" in text, "presence-by-filename check is gone"


def test_the_docs_carry_the_rehearsal():
    """The docs lock: the README's restore bullet points at the script and the
    rehearsal transcript, and docs/restore-drill.md records the ritual — the
    nightly backup (T14), the drill, the known restore path."""
    readme = README.read_text(encoding="utf-8")
    assert "scripts/restore.sh" in readme, "the README's restore bullet never mentions the script"
    assert "docs/restore-drill.md" in readme, "the README's restore bullet never points at the transcript"

    assert DRILL_DOC.exists(), "the rehearsal transcript is missing"
    doc = DRILL_DOC.read_text(encoding="utf-8")
    for marker in (
        "scripts/backup.sh",
        "scripts/restore.sh",
        "pg_restore -U cognee -d cognee_db --clean --if-exists",
        "5433",
        "cognee_db.dump",
        "341",
    ):
        assert marker in doc, f"the drill doc never mentions {marker}"
