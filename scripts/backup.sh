#!/usr/bin/env bash
# The nightly backup archive (T14, GitLab #16): one timestamped tar.gz holding
# everything git does not — the memory layer's pg_dump, both SQLite stores off
# the session container's quota volume, the .env keys, the Book PDFs, and a
# zip of the code for convenience (git remains the code's real backup).
#
# Where it runs: the VPS under cron (the nightly ritual), or a dev machine
# against the local stack — same script, same container names. Modes:
#   (default)  build the archive, then prune to RETENTION
#   --dry-run  print the plan, verify the repo prerequisites, touch NOTHING
#              (no Docker, no git, no files) — the test suite's seam
#   --prune-only  apply retention to BACKUP_DIR and exit
#
# Configuration is environment, never edits: BACKUP_DIR, RETENTION,
# POSTGRES_CONTAINER, SESSION_CONTAINER, POSTGRES_USER, POSTGRES_DB, REPO_DIR.

set -euo pipefail

# Git Bash on Windows rewrites absolute arguments that look like host paths;
# the container's /data paths must reach docker cp untouched. A no-op on Linux.
export MSYS_NO_PATHCONV=1

BACKUP_DIR="${BACKUP_DIR:-/var/backups/chat-with-books}"
RETENTION="${RETENTION:-14}"
POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-chat-with-books-postgres}"
SESSION_CONTAINER="${SESSION_CONTAINER:-chat-with-books-session}"
POSTGRES_USER="${POSTGRES_USER:-cognee}"
POSTGRES_DB="${POSTGRES_DB:-cognee_db}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${REPO_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
# Native binaries in the mix (git.exe) cannot read MSYS-style /d/... paths —
# convert once for every consumer below. A no-op where cygpath does not exist.
command -v cygpath >/dev/null 2>&1 && REPO_DIR="$(cygpath -m "$REPO_DIR")"
STAMP="$(date +%Y%m%d-%H%M%S)"
ARCHIVE_NAME="chat-with-books-backup-${STAMP}.tar.gz"

die() { echo "backup: $*" >&2; exit 1; }

prune_archives() {
  local kept=0 removed=0 old
  while IFS= read -r old; do
    if [ "${1:-}" = "--apply" ]; then
      rm -f "$old"; removed=$((removed + 1))
    else
      echo "plan: prune $old"
    fi
  done < <(ls -1t "${BACKUP_DIR}"/chat-with-books-backup-*.tar.gz 2>/dev/null | tail -n +"$((RETENTION + 1))")
  if [ "${1:-}" = "--apply" ]; then
    kept=$(ls -1t "${BACKUP_DIR}"/chat-with-books-backup-*.tar.gz 2>/dev/null | wc -l)
    echo "backup: ${kept} archive(s) kept, ${removed} pruned (retention ${RETENTION})"
  fi
}

check_prerequisites() {
  [ -f "${REPO_DIR}/.env" ] || die "no .env in ${REPO_DIR} — the keys ride the archive"
  [ -d "${REPO_DIR}/books" ] || die "no books/ in ${REPO_DIR} — the Book set rides the archive"
  [ -d "$BACKUP_DIR" ] || mkdir -p "$BACKUP_DIR" || die "cannot create ${BACKUP_DIR}"
}

case "${1:-}" in
  --dry-run)
    echo "plan: archive ${BACKUP_DIR}/${ARCHIVE_NAME}"
    echo "plan: docker exec ${POSTGRES_CONTAINER} pg_dump -Fc -U ${POSTGRES_USER} ${POSTGRES_DB} -> cognee_db.dump"
    echo "plan: docker cp ${SESSION_CONTAINER}:/data/usage.sqlite3 -> usage.sqlite3"
    echo "plan: docker cp ${SESSION_CONTAINER}:/data/research.sqlite3 -> research.sqlite3"
    echo "plan: docker cp ${SESSION_CONTAINER}:/data/chats.sqlite3 -> chats.sqlite3"
    echo "plan: .env -> env/.env"
    echo "plan: books/ -> books/"
    echo "plan: code -> code.tar.gz (git archive HEAD; a tree tar on a no-git VPS tree)"
    echo "plan: tar czf the seven into ${ARCHIVE_NAME}, then prune to ${RETENTION}"
    check_prerequisites
    echo "backup: dry-run ok — prerequisites hold, nothing was touched"
    ;;
  --prune-only)
    check_prerequisites
    prune_archives --apply
    ;;
  "")
    check_prerequisites
    command -v docker >/dev/null 2>&1 || die "docker not found — the archive needs the running stack"
    command -v git >/dev/null 2>&1 || die "git not found — the code zip needs the cloned repo"
    docker ps --filter "name=${POSTGRES_CONTAINER}" --format '{{.Names}}' | grep -qx "${POSTGRES_CONTAINER}" \
      || die "${POSTGRES_CONTAINER} is not running — bring the stack up first"

    TMPDIR_B="$(mktemp -d)"
    trap 'rm -rf "$TMPDIR_B"' EXIT
    mkdir -p "${TMPDIR_B}/env"

    echo "backup: pg_dump ${POSTGRES_DB} (custom format — pg_restore reads it, HANDOFF-style)"
    docker exec "${POSTGRES_CONTAINER}" pg_dump -Fc -U "${POSTGRES_USER}" "${POSTGRES_DB}" > "${TMPDIR_B}/cognee_db.dump"

    echo "backup: the SQLite stores off ${SESSION_CONTAINER}:/data"
    # Relative targets from inside the tmpdir: with MSYS_NO_PATHCONV=1 (which
    # keeps the container's /data paths intact on Git Bash), an absolute
    # host path would reach docker.exe unconverted and resolve against the
    # wrong drive. A subshell + relative path is portable on both.
    (cd "$TMPDIR_B" && docker cp "${SESSION_CONTAINER}:/data/usage.sqlite3" usage.sqlite3)
    (cd "$TMPDIR_B" && docker cp "${SESSION_CONTAINER}:/data/research.sqlite3" research.sqlite3)
    (cd "$TMPDIR_B" && docker cp "${SESSION_CONTAINER}:/data/chats.sqlite3" chats.sqlite3)

    echo "backup: the keys, the Books, the code"
    cp "${REPO_DIR}/.env" "${TMPDIR_B}/env/.env"
    cp -r "${REPO_DIR}/books" "${TMPDIR_B}/books"
    # A git checkout archives HEAD; the VPS tree is a tarball swap with no
    # .git — there the code rides as a tree tar, minus what already rides
    # separately (the stores, the keys, the Books) and the caches.
    if [ -d "${REPO_DIR}/.git" ]; then
      # git resolves -o relative to its -C directory, not the CWD — hand it
      # the staging path in native form (cygpath -m) so the zip lands in the
      # staging dir and the temp dir, not the repo root.
      CODE_OUT="${TMPDIR_B}/code.tar.gz"
      command -v cygpath >/dev/null 2>&1 && CODE_OUT="$(cygpath -m "$TMPDIR_B")/code.tar.gz"
      git -C "${REPO_DIR}" archive --format=tar.gz -o "$CODE_OUT" HEAD
    else
      tar czf "${TMPDIR_B}/code.tar.gz" --exclude='.git' --exclude='books' \
        --exclude='*.sqlite3' --exclude='.env' --exclude='__pycache__' \
        -C "${REPO_DIR}" .
    fi

    # --force-local: a Windows-form path (D:/...) reads as host:path to GNU
    # tar without it; harmless where no colon ever appears.
    tar czf "${BACKUP_DIR}/${ARCHIVE_NAME}" --force-local -C "$TMPDIR_B" .
    echo "backup: ${BACKUP_DIR}/${ARCHIVE_NAME} ($(du -h "${BACKUP_DIR}/${ARCHIVE_NAME}" | cut -f1))"
    prune_archives --apply
    ;;
  *)
    die "usage: backup.sh [--dry-run | --prune-only]"
    ;;
esac
