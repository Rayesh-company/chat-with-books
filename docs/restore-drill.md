# The restore drill (T15, GitLab #21)

The ritual, one paragraph: every night at 03:30 VPS time `scripts/backup.sh` builds the timestamped archive (T14) and `scripts/pull_backup.ps1` pulls the newest one to the operator's machine, so the archive always lives in two places; and because a backup is only a backup if a restore has actually been walked, `scripts/restore.sh` walks one for real — it restores the newest archive into a **throwaway** Postgres on port `5433`, verifies the memory came back (schema, rows, stores, Books), and cleans up after itself. Run the drill after every major change (a re-ingest, an embedding-pin change, a new archive scheme), not on a schedule: the rehearsed path is the deliverable. When the day comes that the real restore is needed, it is this exact procedure minus the throwaway — the same `pg_restore` flags against the live stack, the stores and books unpacked into the tree. **Backup nightly (T14) → drill rehearsed → restore is a known path, not a guess.**

## Running the drill

```bash
# the rehearsal (dev machine or VPS — needs Docker, nothing else)
bash scripts/restore.sh /path/to/chat-with-books-backup-<stamp>.tar.gz

# the seam — verify the archive and print the plan, touch nothing (the suite runs here)
bash scripts/restore.sh /path/to/chat-with-books-backup-<stamp>.tar.gz --dry-run
```

Configuration is environment, never edits: `DRILL_PORT` (5433), `DRILL_IMAGE` (pgvector/pgvector:pg17), `DRILL_PREFIX` (cwb-drill-pg), `POSTGRES_USER`/`POSTGRES_DB` (cognee/cognee_db), `CHUNK_TABLE` (DocumentChunk_text), `EXPECTED_CHUNKS` (341 — recorded 2026-09-19: tarhe-kolli 243 + 70143-336 98).

## The safety rules (absolute)

1. **Never touch, stop, or restore into any `chat-with-books-*` container.** The drill's throwaway is `cwb-drill-pg-<stamp>`; a prefix that collides with the live stack is refused at startup, and the tests pin the script's text so no docker call can ever name a live container.
2. **Never bind host port 5432.** The throwaway publishes `5433`; an override back to 5432 is refused. The VPS shares its host with other projects — the drill must never contend for the live stack's port anywhere.
3. **Never write into the live repo tree.** Extraction goes to a `mktemp` dir, removed by the EXIT trap together with the throwaway container — even when the drill fails mid-step.
4. **Never print `.env` contents.** The archive holds real keys; the drill verifies its presence by filename (and non-emptiness) only, and the plan prints the throwaway's dummy password as `***`.

## The rehearsal — 2026-09-19

Archive: `D:/code/CHATBOT/backups/chat-with-books-backup-20260919-115248.tar.gz` (~356 MB, the newest of the T14 nightlies). Docker Desktop running, live stack up the whole time.

**Walk one failed — and that is the drill working.** `pg_restore` was clean and the chunk table held exactly 341 rows, but both SQLite checks failed: the drill's first pass handed `python.exe` (a native Windows binary) the extraction dir in MSYS form (`/tmp/tmp.…`), which the binary resolves against the current drive — the recorded backup.sh lesson, missed once, caught by the drill's own checks on its first ever walk. The fix is the `cygpath -m` conversion the script now carries (same pattern as backup.sh). The EXIT trap cleaned up the throwaway container and the temp dir despite the failure — the always-cleanup promise held on its first test.

**Walk two, the full transcript (the green run):**

```text
$ bash scripts/restore.sh D:/code/CHATBOT/backups/chat-with-books-backup-20260919-115248.tar.gz
restore: تمرین بازگردانی — آرشیو: D:/code/CHATBOT/backups/chat-with-books-backup-20260919-115248.tar.gz
restore: قوانین — هیچ کانتینر chat-with-books-* لمس نمی‌شود؛ درگاه میزبان هرگز ۵۴۳۲ نیست (تمرین روی 5433)؛ استخراج هرگز داخل مخزن زنده نیست؛ محتوای env/.env هرگز چاپ نمی‌شود.
member: cognee_db.dump
member: usage.sqlite3
member: research.sqlite3
member: code.tar.gz
member: books/tarhe-kolli.pdf
member: books/70143-336.pdf
member: env/.env — presence verified by filename only; its contents are never printed
restore: extracting into the mktemp dir /tmp/tmp.e3E0ILpvaj — never the live repo tree
restore: docker run -d --name cwb-drill-pg-20260919-133357 -e POSTGRES_USER=cognee -e POSTGRES_PASSWORD=*** -e POSTGRES_DB=cognee_db -p 5433:5432 pgvector/pgvector:pg17
restore: waiting for pg_isready inside the throwaway (120 s leash)
drill: [OK]   the throwaway Postgres answered pg_isready
restore: pg_restore -U cognee -d cognee_db --clean --if-exists (the HANDOFF flags, verbatim)
drill: [OK]   pg_restore exited clean
drill: [OK]   pg_restore --list TABLE DATA entries: 50
drill: [OK]   public."DocumentChunk_text" per dataset: tarhe-kolli=243 70143-336=98 — total 341 == expected 341
drill: [OK]   the usage.sqlite3 store opens and carries 1 table(s)
drill: [OK]   the research.sqlite3 store opens and carries 3 table(s)
drill: [OK]   books/: both Book PDFs extracted (2 pdf file(s))
drill: [OK]   env/.env is present and non-empty — its contents are never read or printed

drill: گزارش — همهٔ بررسی‌ها سبز است (43 ثانیه)
drill: report — the restore drill PASSED; restore is a rehearsed path, not a guess
restore: پاک‌سازی — کانتینر موقت cwb-drill-pg-20260919-133357 با docker rm -f حذف شد (همیشه، حتی در شکست)
restore: پاک‌سازی — پوشهٔ موقت حذف شد؛ هیچ اثری از تمرین روی ماشین نمی‌ماند
$ echo $?
0
```

Total: 43 seconds — a drill that costs nothing has no excuse to be skipped. Afterward: zero `cwb-drill-pg-*` containers, zero temp dirs, and the live stack never restarted (every `chat-with-books-*` container still `Up` from before the drill).

## What each check proves, and why

- **The six members (`tar -tzf`, both modes)** — a partial archive restores a partial memory; the drill refuses before anything runs and names what is missing. `books/` counts as two PDFs by name, not one directory entry.
- **`pg_restore --clean --if-exists` exits clean** — the HANDOFF flags verbatim; `--if-exists` keeps the restore idempotent (safe to re-run), which is what makes the real day calm.
- **`--list` TABLE DATA entries > 0** — the dump carries rows, not only schema; this is where the recorded T14 incident class (a silently dropped `-Fc`) would die.
- **`DocumentChunk_text` == 341 across the two datasets** — the restore landed real memory; the suite cannot see this, so the drill is exactly the place where it must be true. A drift is a finding, not noise — re-check what ingested or pruned since the last recorded number.
- **Both SQLite stores open with ≥ 1 table** — the quota counts and research sessions survived the round trip.
- **Both Book PDFs extracted; `env/.env` present and non-empty** — the Books and the keys are the two things git never holds.

## The real day (the drill minus the throwaway)

1. Provision Postgres (compose) with `POSTGRES_USER=cognee`, `POSTGRES_DB=cognee_db` — the embedder pin (`text-embedding-3-large`/3072) must match the restored tables.
2. `docker cp cognee_db.dump chat-with-books-postgres:/tmp/` then `docker exec chat-with-books-postgres pg_restore -U cognee -d cognee_db --clean --if-exists /tmp/cognee_db.dump` (prefix `MSYS_NO_PATHCONV=1` on Git Bash).
3. Unpack `usage.sqlite3`/`research.sqlite3` into the session store's volume, `books/` into the tree, and the keys into `.env` — then verify with one real search (`python scripts/smoke.py`), not just health probes.
