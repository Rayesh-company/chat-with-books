"""The nightly backup (T14, GitLab #16): the script's dry-run seam and its
pruning. The real mode needs the running stack — Docker and network stay
outside the suite — so the suite holds the two modes that promise to touch
nothing: --dry-run (the printed plan, the prerequisites, zero side effects)
and --prune-only (retention applied to a prepared directory). The README's
ritual is pinned here too."""

import os
import shutil
import subprocess
from pathlib import Path

from tests.conftest import REPO_ROOT

BACKUP_SCRIPT = REPO_ROOT / "scripts" / "backup.sh"
README = REPO_ROOT / "README.md"


def _find_bash() -> str:
    """A bash that reads Windows paths. subprocess's PATH lookup lands on the
    WSL stub (System32 bash.exe) first on some machines — it cannot see
    D:/..., so Git for Windows' own bash is preferred; on POSIX the PATH's
    bash is the right one."""
    program_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
    candidates = [
        str(Path(program_files) / "Git" / "bin" / "bash.exe"),
        str(Path(program_files) / "Git" / "usr" / "bin" / "bash.exe"),
    ]
    on_path = shutil.which("bash")
    if on_path and "system32" not in on_path.lower():
        candidates.append(on_path)
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    return "bash"

# A minimal repo stand-in: the two prerequisites the script demands before it
# plans anything — the keys and the Book set.
REPO_STANDIN_FILES = (".env", "books/tarhe-kolli.pdf")


def run_backup(args: list[str], repo_dir: Path, backup_dir: Path, retention: str = "14") -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update(
        REPO_DIR=repo_dir.as_posix(),
        BACKUP_DIR=backup_dir.as_posix(),
        RETENTION=retention,
        POSTGRES_CONTAINER="unused-postgres",
        SESSION_CONTAINER="unused-session",
    )
    return subprocess.run(
        [_find_bash(), BACKUP_SCRIPT.as_posix(), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def make_repo_standin(tmp_path: Path, with_env: bool = True) -> Path:
    repo = tmp_path / "repo"
    (repo / "books").mkdir(parents=True)
    for rel in REPO_STANDIN_FILES:
        (repo / rel).write_text("stand-in\n", encoding="utf-8")
    if not with_env:
        (repo / ".env").unlink()
    return repo


PLANNED_STEPS = (
    "cognee_db.dump",
    "usage.sqlite3",
    "research.sqlite3",
    "env/.env",
    "books/",
    "code.tar.gz",
)


def test_dry_run_prints_the_whole_plan_and_touches_nothing(tmp_path):
    """The dry-run names all six components and the archive destination —
    and creates nothing anywhere."""
    repo = make_repo_standin(tmp_path)
    backup_dir = tmp_path / "backups"

    result = run_backup(["--dry-run"], repo, backup_dir)

    assert result.returncode == 0, result.stderr
    for component in PLANNED_STEPS:
        assert component in result.stdout, f"the plan never names {component}"
    assert "dry-run ok" in result.stdout
    # The plan is the command's mirror: the dump rides in custom format, or
    # pg_restore cannot read it back (the HANDOFF restore path).
    assert "-Fc" in result.stdout
    assert not backup_dir.exists() or not list(backup_dir.iterdir()), "the dry-run wrote into the backup dir"


def test_the_script_matches_its_plan():
    """The real command and the dry-run's plan must agree — the recorded
    incident: the plan promised -Fc while the command silently dropped it,
    and only the restore proof caught it."""
    script = BACKUP_SCRIPT.read_text(encoding="utf-8")
    plan_line = next(line for line in script.splitlines() if "plan: docker exec" in line)
    real_line = next(line for line in script.splitlines() if "pg_dump" in line and "docker exec" in line and "plan:" not in line)
    for token in ("-Fc", "pg_dump"):
        assert token in plan_line and token in real_line, f"plan and command disagree on {token}"


def test_dry_run_refuses_a_repo_without_keys(tmp_path):
    """No .env, no plan — the keys ride the archive, a backup without them is
    a restore into a broken stack."""
    repo = make_repo_standin(tmp_path, with_env=False)

    result = run_backup(["--dry-run"], repo, tmp_path / "backups")

    assert result.returncode != 0
    assert ".env" in result.stderr


def test_dry_run_needs_no_docker(tmp_path):
    """The dry-run must hold on a machine with no Docker at all — the plan is
    printed before any docker call would exist, and the suite runs nowhere
    near the daemon."""
    repo = make_repo_standin(tmp_path)

    result = run_backup(["--dry-run"], repo, tmp_path / "backups")

    assert result.returncode == 0, result.stderr
    # The plan names the containers, but as text — a machine without docker
    # still passes, which is the whole point of the seam.
    assert "unused-postgres" in result.stdout


def test_prune_only_keeps_retention_and_drops_the_oldest(tmp_path):
    """--prune-only applies retention to a prepared directory: the newest
    RETENTION stay, the older go — nothing else about them matters."""
    repo = make_repo_standin(tmp_path)
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    for day in range(1, 6):  # five fakes, retention three
        name = backup_dir / f"chat-with-books-backup-202609{day:02d}-033000.tar.gz"
        name.write_bytes(b"fake archive")
        os.utime(name, (1_700_000_000 + day, 1_700_000_000 + day))

    result = run_backup(["--prune-only"], repo, backup_dir, retention="3")

    assert result.returncode == 0, result.stderr
    survivors = sorted(p.name for p in backup_dir.iterdir())
    assert len(survivors) == 3
    assert "chat-with-books-backup-20260905-033000.tar.gz" in survivors, "the newest must survive"
    assert "chat-with-books-backup-20260901-033000.tar.gz" not in survivors, "the oldest must go"


def test_prune_leaves_a_directory_under_retention_untouched(tmp_path):
    """Fewer archives than retention: nothing is pruned, nothing is created."""
    repo = make_repo_standin(tmp_path)
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    only = backup_dir / "chat-with-books-backup-20260919-033000.tar.gz"
    only.write_bytes(b"fake archive")

    result = run_backup(["--prune-only"], repo, backup_dir, retention="14")

    assert result.returncode == 0, result.stderr
    assert [p.name for p in backup_dir.iterdir()] == [only.name]


def test_readme_documents_the_ritual():
    """The docs lock: the README carries the nightly backup's ritual — the
    cron line's shape, the archive's contents, and the second location — so
    the next reader can reconstruct the whole scheme from the repo alone."""
    readme = README.read_text(encoding="utf-8")
    for marker in ("nightly backup", "scripts/backup.sh", "cognee_db.dump", "pull_backup"):
        assert marker in readme, f"the README's backup ritual never mentions {marker}"
