#!/usr/bin/env bash
# The restore drill (T15, GitLab #21): the rehearsed restore path. A backup is
# only a backup if a restore has actually been walked — this script takes one
# archive built by scripts/backup.sh (cognee_db.dump, usage.sqlite3,
# research.sqlite3, env/.env, books/, code.tar.gz), restores it into a
# THROWAWAY Postgres, verifies the memory came back, and cleans up — so the
# day a real restore is needed, it is a known path, not a first try.
#
# The safety rules are absolute and enforced both here and in tests:
#   1. Never touch, stop, or restore into any chat-with-books-* container —
#      the drill's throwaway is named <DRILL_PREFIX>-<stamp> and a prefix that
#      collides with the live stack is refused.
#   2. Never bind host port 5432 — the throwaway publishes DRILL_PORT (5433);
#      an override to 5432 is refused at startup.
#   3. Never write into the live repo tree — extraction goes to a mktemp dir,
#      removed by the EXIT trap together with the throwaway container.
#   4. Never print .env contents — the archive holds real keys; presence is
#      verified by filename (and non-emptiness) only.
#
# Modes:
#   (default)  the full drill: verify the archive, extract to a temp dir,
#              start the throwaway pgvector Postgres, wait for pg_isready,
#              docker cp the dump in, pg_restore with the HANDOFF flags,
#              run the checks, print the report — and ALWAYS clean up
#   --dry-run  verify the archive (all seven members, tar -tzf) and print the
#              plan — NO Docker, nothing touched: the test suite's seam
#
# Usage: restore.sh <archive.tar.gz> [--dry-run]
#
# Configuration is environment, never edits: DRILL_PORT, DRILL_IMAGE,
# DRILL_PREFIX, DRILL_PASSWORD, POSTGRES_USER, POSTGRES_DB, CHUNK_TABLE,
# EXPECTED_CHUNKS, PYTHON_BIN.

set -euo pipefail

# Git Bash on Windows rewrites absolute arguments that look like host paths;
# the container's /tmp paths must reach docker exec/cp untouched (the recorded
# HANDOFF note). A no-op on Linux.
export MSYS_NO_PATHCONV=1

DRILL_PORT="${DRILL_PORT:-5433}"
DRILL_IMAGE="${DRILL_IMAGE:-pgvector/pgvector:pg17}"
DRILL_PREFIX="${DRILL_PREFIX:-cwb-drill-pg}"
DRILL_PASSWORD="${DRILL_PASSWORD:-cognee}"
POSTGRES_USER="${POSTGRES_USER:-cognee}"
POSTGRES_DB="${POSTGRES_DB:-cognee_db}"
CHUNK_TABLE="${CHUNK_TABLE:-DocumentChunk_text}"
# The recorded chunk total across the two datasets (2026-09-19):
# tarhe-kolli 243 + 70143-336 98 = 341. A drift here is a finding, not noise.
EXPECTED_CHUNKS="${EXPECTED_CHUNKS:-341}"

# The seven members a backup.sh archive must carry; the drill refuses anything
# less, because a partial archive restores a partial memory.
REQUIRED_MEMBERS="cognee_db.dump usage.sqlite3 research.sqlite3 chats.sqlite3 env/.env code.tar.gz"
REQUIRED_DIR_PREFIX="books/"

die() { echo "restore: $*" >&2; exit 1; }
ok() { echo "drill: [OK]   $*"; }
fail() { echo "drill: [FAIL] $*"; FAILURES=$((FAILURES + 1)); }

# --- the safety guards, runtime as well as textual --------------------------
if [ "${DRILL_PORT}" = "5432" ]; then die "DRILL_PORT=5432 is forbidden — the throwaway never binds the live stack's host port"; fi
case "${DRILL_PREFIX}" in
  *chat-with-books*) die "the drill prefix must never collide with the live stack's container names (chat-with-books-*)";;
esac

STAMP="$(date +%Y%m%d-%H%M%S)"
DRILL_CONTAINER="${DRILL_PREFIX}-${STAMP}"
DRILL_TMP=""
DRILL_STARTED=""
FAILURES=0

# The EXIT trap is the always-cleanup promise: the throwaway container (docker
# rm -f) and the temp dir go even when the drill dies mid-step. Before the
# container exists (or fails to start) the flag stays empty and that step skips.
cleanup() {
  if [ -n "$DRILL_STARTED" ]; then
    docker rm -f "$DRILL_CONTAINER" >/dev/null 2>&1 || true
    echo "restore: پاک‌سازی — کانتینر موقت ${DRILL_CONTAINER} با docker rm -f حذف شد (همیشه، حتی در شکست)"
  fi
  if [ -n "$DRILL_TMP" ] && [ -d "$DRILL_TMP" ]; then
    rm -rf "$DRILL_TMP"
    echo "restore: پاک‌سازی — پوشهٔ موقت حذف شد؛ هیچ اثری از تمرین روی ماشین نمی‌ماند"
  fi
}
trap cleanup EXIT

check_members() {
  # tar -tzf reads the TOC without extracting: every required member must be
  # named (the ./ prefix backup.sh's staging tar adds is stripped first).
  local listing missing="" member
  listing="$(tar --force-local -tzf "$1")" || die "cannot read the archive: $1"
  listing="$(printf '%s\n' "$listing" | sed 's|^\./||')"
  for member in ${REQUIRED_MEMBERS}; do
    printf '%s\n' "$listing" | grep -qx -- "$member" || missing="$missing ${member}"
  done
  printf '%s\n' "$listing" | grep -q "^${REQUIRED_DIR_PREFIX}" || missing="$missing books/ (the Book PDFs)"
  # books/ is one member in the plan but two PDFs in the truth: a books/ that
  # carries only the page indexes is a broken Book set, so both names must sit
  # in the archive's TOC.
  for book in books/tarhe-kolli.pdf books/70143-336.pdf; do
    printf '%s\n' "$listing" | grep -qx -- "$book" || missing="$missing ${book}"
  done
  [ -z "$missing" ] || die "the archive is missing member(s):${missing} — a partial archive restores a partial memory"
  for member in ${REQUIRED_MEMBERS}; do
    # env/.env gets its own line below — presence by filename, never contents.
    [ "$member" = "env/.env" ] && continue
    echo "member: ${member}"
  done
  echo "member: books/tarhe-kolli.pdf"
  echo "member: books/70143-336.pdf"
  echo "member: env/.env — presence verified by filename only; its contents are never printed"
}

PYTHON_BIN="${PYTHON_BIN:-$(command -v python || command -v python3 || true)}"

sqlite_tables() {
  # The table count of one SQLite store, or -1 when it will not open — the
  # count is the check, the contents are never dumped to the transcript.
  if [ -z "$PYTHON_BIN" ]; then
    echo -1; return
  fi
  "$PYTHON_BIN" - "$1" <<'PY' 2>/dev/null || echo -1
import sqlite3, sys
try:
    con = sqlite3.connect(sys.argv[1])
    print(con.execute("select count(*) from sqlite_master where type='table'").fetchone()[0])
except Exception:
    print(-1)
PY
}

check_sqlite() {
  local file="$1" label="$2" tables
  tables="$(sqlite_tables "$file")"
  if [ -n "$tables" ] && [ "$tables" -ge 1 ] 2>/dev/null; then
    ok "the ${label} store opens and carries ${tables} table(s)"
  else
    fail "the ${label} store did not open (or holds no tables)"
  fi
}

main_drill() {
  echo "restore: تمرین بازگردانی — آرشیو: ${ARCHIVE}"
  echo "restore: قوانین — هیچ کانتینر chat-with-books-* لمس نمی‌شود؛ درگاه میزبان هرگز ۵۴۳۲ نیست (تمرین روی ${DRILL_PORT})؛ استخراج هرگز داخل مخزن زنده نیست؛ محتوای env/.env هرگز چاپ نمی‌شود."
  check_members "$ARCHIVE"

  DRILL_TMP="$(mktemp -d)"
  # Native binaries in the mix (python.exe, for the SQLite checks) cannot read
  # MSYS-style /tmp/... paths — they resolve them against the current drive
  # and the drill's first walk failed both store checks exactly that way
  # (2026-09-19). Convert once for those consumers. A no-op where cygpath
  # does not exist (Linux); the MSYS tools (tar, find, rm) keep the POSIX form.
  DRILL_TMP_NATIVE="$DRILL_TMP"
  command -v cygpath >/dev/null 2>&1 && DRILL_TMP_NATIVE="$(cygpath -m "$DRILL_TMP")"
  echo "restore: extracting into the mktemp dir ${DRILL_TMP} — never the live repo tree"
  tar --force-local -xzf "$ARCHIVE" -C "$DRILL_TMP"

  echo "restore: docker run -d --name ${DRILL_CONTAINER} -e POSTGRES_USER=${POSTGRES_USER} -e POSTGRES_PASSWORD=*** -e POSTGRES_DB=${POSTGRES_DB} -p ${DRILL_PORT}:5432 ${DRILL_IMAGE}"
  docker run -d --name "$DRILL_CONTAINER" \
    -e "POSTGRES_USER=${POSTGRES_USER}" \
    -e "POSTGRES_PASSWORD=${DRILL_PASSWORD}" \
    -e "POSTGRES_DB=${POSTGRES_DB}" \
    -p "${DRILL_PORT}:5432" \
    "$DRILL_IMAGE" >/dev/null
  DRILL_STARTED=1

  echo "restore: waiting for pg_isready inside the throwaway (120 s leash)"
  ready=""
  for _ in $(seq 1 60); do
    if docker exec "$DRILL_CONTAINER" pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; then
      ready=1; break
    fi
    sleep 2
  done
  if [ -n "$ready" ]; then
    ok "the throwaway Postgres answered pg_isready"
  else
    fail "the throwaway Postgres never answered pg_isready (120 s)"
  fi

  # Relative source from inside the tmpdir: with MSYS_NO_PATHCONV=1 (which
  # keeps the container's /tmp path intact on Git Bash), an absolute host path
  # would reach docker.exe unconverted and resolve against the wrong drive.
  # A subshell + relative path is portable on both — backup.sh's shape.
  (cd "$DRILL_TMP" && docker cp cognee_db.dump "${DRILL_CONTAINER}:/tmp/cognee_db.dump")

  echo "restore: pg_restore -U ${POSTGRES_USER} -d ${POSTGRES_DB} --clean --if-exists (the HANDOFF flags, verbatim)"
  if docker exec "$DRILL_CONTAINER" pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists /tmp/cognee_db.dump >"$DRILL_TMP/pg_restore.log" 2>&1; then
    ok "pg_restore exited clean"
  else
    fail "pg_restore exited non-zero — its last five lines:"
    tail -n 5 "$DRILL_TMP/pg_restore.log" | sed 's/^/    /'
  fi

  # The dump's own TOC: TABLE DATA entries prove the backup carries rows, not
  # only schema — the recorded T14 incident class (a silent -Fc drop) dies here.
  TABLE_DATA="$(docker exec "$DRILL_CONTAINER" pg_restore -l /tmp/cognee_db.dump 2>/dev/null | grep -c 'TABLE DATA' || true)"
  if [ "${TABLE_DATA:-0}" -gt 0 ]; then
    ok "pg_restore --list TABLE DATA entries: ${TABLE_DATA}"
  else
    fail "pg_restore --list TABLE DATA entries: ${TABLE_DATA:-0} — a schema-only dump restores an empty memory"
  fi

  # The real count query: the restore landed rows in the chunk table. The
  # recorded total across the two datasets is EXPECTED_CHUNKS; the suite
  # cannot see this — the drill is exactly the place where it must be true.
  CHUNK_SQL="SELECT payload->>'document_name', count(*) FROM public.\"${CHUNK_TABLE}\" GROUP BY 1"
  CHUNKS_OUT="$(printf '%s\n' "$CHUNK_SQL" | docker exec -i "$DRILL_CONTAINER" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA 2>&1 || true)"
  CHUNK_TOTAL="$(printf '%s\n' "$CHUNKS_OUT" | awk -F'|' '{s+=$NF} END{print s+0}')"
  CHUNKS_FLAT="$(printf '%s\n' "$CHUNKS_OUT" | sed 's/|/=/g' | tr '\n' ' ')"
  if [ "$CHUNK_TOTAL" -eq "$EXPECTED_CHUNKS" ]; then
    ok "public.\"${CHUNK_TABLE}\" per dataset: ${CHUNKS_FLAT}— total ${CHUNK_TOTAL} == expected ${EXPECTED_CHUNKS}"
  else
    fail "public.\"${CHUNK_TABLE}\" total ${CHUNK_TOTAL} != expected ${EXPECTED_CHUNKS} (per dataset: ${CHUNKS_FLAT})"
  fi

  check_sqlite "$DRILL_TMP_NATIVE/usage.sqlite3" "usage.sqlite3"
  check_sqlite "$DRILL_TMP_NATIVE/research.sqlite3" "research.sqlite3"
  check_sqlite "$DRILL_TMP_NATIVE/chats.sqlite3" "chats.sqlite3"

  PDFS="$(find "$DRILL_TMP/books" -maxdepth 1 -type f -name '*.pdf' 2>/dev/null | wc -l | tr -d ' ')"
  if [ -f "$DRILL_TMP/books/tarhe-kolli.pdf" ] && [ -f "$DRILL_TMP/books/70143-336.pdf" ]; then
    ok "books/: both Book PDFs extracted (${PDFS} pdf file(s))"
  else
    fail "books/: the Book PDFs did not survive extraction (${PDFS} pdf file(s) found)"
  fi

  if [ -s "$DRILL_TMP/env/.env" ]; then
    ok "env/.env is present and non-empty — its contents are never read or printed"
  else
    fail "env/.env did not survive extraction — the keys would be lost"
  fi

  echo ""
  if [ "$FAILURES" -eq 0 ]; then
    echo "drill: گزارش — همهٔ بررسی‌ها سبز است (${SECONDS} ثانیه)"
    echo "drill: report — the restore drill PASSED; restore is a rehearsed path, not a guess"
  else
    echo "drill: گزارش — ${FAILURES} بررسی ناکام ماند (${SECONDS} ثانیه)"
    echo "drill: report — the restore drill FAILED; the cleanup below ran anyway"
  fi
  [ "$FAILURES" -eq 0 ]
}

ARCHIVE="${1:-}"
[ -n "$ARCHIVE" ] || die "usage: restore.sh <archive.tar.gz> [--dry-run] — the archive scripts/backup.sh built"
[ -f "$ARCHIVE" ] || die "no archive at ${ARCHIVE}"

case "${2:-}" in
  --dry-run)
    check_members "$ARCHIVE"
    echo ""
    echo "plan: tar xzf the archive into a mktemp dir (never the live repo tree)"
    echo "plan: docker run -d --name ${DRILL_PREFIX}-<stamp> -e POSTGRES_USER=${POSTGRES_USER} -e POSTGRES_PASSWORD=*** -e POSTGRES_DB=${POSTGRES_DB} -p ${DRILL_PORT}:5432 ${DRILL_IMAGE}"
    echo "plan: wait for pg_isready inside the throwaway (120 s leash)"
    echo "plan: docker cp cognee_db.dump -> ${DRILL_PREFIX}-<stamp>:/tmp/cognee_db.dump"
    echo "plan: pg_restore -U ${POSTGRES_USER} -d ${POSTGRES_DB} --clean --if-exists /tmp/cognee_db.dump (the HANDOFF flags)"
    echo "plan: verify pg_restore --list TABLE DATA > 0; public.\"${CHUNK_TABLE}\" == ${EXPECTED_CHUNKS} rows across the two datasets"
    echo "plan: verify both SQLite stores open with python sqlite3; books/ holds both Book PDFs; env/.env present by filename only (contents never printed)"
    echo "plan: docker rm -f the throwaway + rm -rf the temp dir — the EXIT trap, even on failure"
    echo ""
    echo "restore: dry-run ok — all seven members verified, nothing was touched"
    ;;
  "")
    main_drill
    ;;
  *)
    die "usage: restore.sh <archive.tar.gz> [--dry-run]"
    ;;
esac
