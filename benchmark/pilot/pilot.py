"""The pilot harness (2026-09-28): an HTTP-level driver that plays the
product's own client — the sheet's exact call sequence — and records the
timeline the operator asked for: time-to-first-token, the streamed-line
wall, the citation walls, and every Toman the ledger charges, for the
simple ask AND the full research journey. The deployed service is never
touched: this file only knocks on the front door with the same JSON the
browser sends.

Subcommands:
    accounts  create the four bench Accounts + top them up (admin door)
    simple    N runs of the full ask sequence (recall SSE + picker +
              quoted answer, the sheet's parallel phase-2)
    research  one full research session, driven chip-by-chip to the
              standing Brief and its report
    report    aggregate a runs directory into CSV + a markdown table set

Usage:
    python benchmark/pilot/pilot.py accounts --base-url https://booksai.rayesh-team.ir
    python benchmark/pilot/pilot.py simple --account bench-simple-1 --runs 3 \
        --query "کتاب طرح کلی اندیشه اسلامی درباره چیست؟" --datasets tarhe-kolli
    python benchmark/pilot/pilot.py research --account bench-research-1 \
        --question "سیر تحول اندیشه اسلامی از عصر پیامبر تا امروز چه عواملی داشته است؟" \
        --datasets tarhe-kolli
    python benchmark/pilot/pilot.py report --runs benchmark/runs/pilot/<stamp>

stdlib only, in the house style: urllib + http.cookiejar, one fetch seam,
perf_counter walls, JSON per run. Credentials ride accounts.json beside
this file (gitignored) — never in code, never committed.
"""

from __future__ import annotations

import argparse
import csv
import http.client
import http.cookiejar
import json
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ACCOUNTS_FILE = HERE / "accounts.json"
DEFAULT_BASE = "https://booksai.rayesh-team.ir"
POLL_SECONDS = 2.0  # the sheet's own research cadence

# The Book set (ui/dive.py's BOOK_DATASETS) — the two fixed Books.
BOOK_DATASETS = ("tarhe-kolli", "70143-336")

# The four bench Accounts the pilot runs ride (created by `accounts`).
BENCH_ACCOUNTS = {
    "bench-simple-1": "کتاب طرح کلی اندیشه اسلامی درباره چیست؟",
    "bench-simple-2": "کتاب انسان ۲۵۰ ساله چه ساختی دارد؟",
    "bench-research-1": "سیر تحول اندیشه اسلامی از عصر پیامبر تا امروز چه عواملی داشته و چه چیزهایی آن را شکل داده است؟",
    "bench-research-2": "چهار دوره پنجاه‌ساله زندگی انسان چه ساختار، وظایف و بحران‌هایی دارند؟",
}
TOPUP_TOMAN = 5_000_000


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Client:
    """One logged-in browser: a cookiejar behind the sheet's HttpOnly
    door, UTF-8 bodies exactly as the sheet sends them, and perf_counter
    walls around every call. The fetch seam is this class's `open` — the
    tests of the harness (none yet) would patch it."""

    def __init__(self, base_url: str, timeout: int = 600):
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )

    def open(self, path: str, payload=None, form=False, timeout=None):
        """One HTTP round trip: (status, headers, body-bytes). JSON in,
        JSON or SSE out; a form flag sends urlencoded (the console's
        forms are form-shaped, not JSON)."""
        url = self.base + path
        headers = {}
        data = None
        if payload is not None:
            if form:
                data = urllib.parse.urlencode(payload).encode("utf-8")
                headers["Content-Type"] = "application/x-www-form-urlencoded"
            else:
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                headers["Content-Type"] = "application/json; charset=utf-8"
        request = urllib.request.Request(url, data=data, headers=headers)
        started = time.perf_counter()
        try:
            with self.opener.open(request, timeout=timeout or self.timeout) as resp:
                body = resp.read()
                wall = time.perf_counter() - started
                return resp.status, dict(resp.headers), body, wall
        except urllib.error.HTTPError as exc:
            body = exc.read()
            wall = time.perf_counter() - started
            return exc.code, dict(exc.headers), body, wall

    def json_call(self, path: str, payload=None, form=False):
        status, headers, body, wall = self.open(path, payload, form)
        try:
            parsed = json.loads(body.decode("utf-8", "replace")) if body else {}
        except json.JSONDecodeError:
            parsed = {"_raw": body[:400].decode("utf-8", "replace")}
        return {
            "status": status,
            "json": parsed,
            "wall_s": round(wall, 3),
            # The parsed body alone hides what the server actually said
            # (the pilot's empty-picker runs recorded only a count); the
            # head of the raw body rides along for the run record.
            "body": body[:8000].decode("utf-8", "replace") if body else "",
        }

    def login(self, email: str, password: str) -> dict:
        result = self.json_call("/auth/login", {"email": email, "password": password})
        if result["status"] != 200:
            raise SystemExit(f"login failed {result['status']}: {result['json']}")
        return result

    def usage_live(self) -> dict:
        return self.json_call("/usage/live")["json"]


# --- the SSE reader ---------------------------------------------------------


def read_sse(body: bytes):
    """Parse one Cognee SSE stream into (event, data-json) pairs, the
    sheet's readSse shape: `event:`/`data:` pairs dispatched on the blank
    line. The relay is close-delimited (HTTP/1.0) — the stream ends when
    the body ends."""
    event = None
    data_lines = []
    for raw in body.split(b"\n"):
        line = raw.decode("utf-8", "replace").rstrip("\r")
        if line == "":
            if event is not None or data_lines:
                yield event or "message", "\n".join(data_lines)
            event, data_lines = None, []
            continue
        if line.startswith("event:"):
            event = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())
    if event is not None or data_lines:
        yield event or "message", "\n".join(data_lines)


def extract_results(payload) -> list:
    """The recall's result rows — however the upstream shapes them: the
    final frame carries `results`; a plain JSON body is the object
    itself or a list."""
    if isinstance(payload, dict):
        for key in ("results", "chunks", "data"):
            if isinstance(payload.get(key), list):
                return payload[key]
        return [payload]
    if isinstance(payload, list):
        return payload
    return []


def split_citation(text: str) -> tuple:
    """The client's splitCitation: the answer before the `Evidence:`
    marker, then one citation string per `- locator: "passage"` bullet.
    The sheet renders the answer and pools the bullets; the harness
    records both and pools them for phase 2."""
    marker = "Evidence:"
    at = text.find(marker)
    if at == -1:
        return text.strip(), []
    answer = text[:at].strip()
    rest = text[at + len(marker):].strip()
    citations = [
        item.strip()
        for item in rest.split("\n- ")
        if item.strip()
    ]
    return answer, citations


def clean_farsi(text: str) -> str:
    """The client's cleanFarsi twin, narrowed to what the passage pool
    needs: the tarhe-kolli text layer's literal backspaces out — the
    guard upstream re-verifies every gap anyway."""
    return text.replace("\x08", "")


def evidence_sources(citations: list) -> list:
    """The client's evidenceSources: each `locator: "passage"` bullet
    split at the first `: "` into {reference, passage} — the exact keys
    the phase-2 endpoints validate, and the exact pool the sheet sends."""
    sources = []
    for item in citations:
        at = item.find(': "')
        if at == -1:
            continue
        reference = item[:at].strip()
        passage = clean_farsi(item[at + 3:].rstrip('"').strip())
        if reference and passage:
            sources.append({"reference": reference, "passage": passage})
    return sources


# --- the simple ask (test 1) ------------------------------------------------


def run_simple_once(client: Client, query: str, datasets: list, session_id) -> dict:
    """The sheet's full ask sequence, timed at every milestone. Phase 1
    (recall SSE) is read line-by-line so each milestone is stamped the
    moment its frame hits the socket — a whole-body read would collapse
    every wall onto the download's end. The settled pool then feeds the
    two parallel phase-2 calls exactly as the sheet fires them."""
    record = {"started_at": now_iso(), "query": query, "datasets": datasets}

    # Phase 1 — the ask: SSE milestones, read incrementally.
    body = json.dumps(
        {
            "searchType": "HYBRID_COMPLETION",
            "query": query,
            "datasets": datasets,
            "includeReferences": True,
            "stream": True,
            "session_id": session_id,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        client.base + "/api/v1/recall",
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    t0 = time.perf_counter()
    milestones = {}
    final_frame = None
    stream_error = None
    deltas = 0
    try:
        with client.opener.open(request, timeout=600) as resp:
            ctype = resp.headers.get("Content-Type", "")
            record["recall_status"] = resp.status
            record["recall_content_type"] = ctype
            if "text/event-stream" in ctype:
                event = None
                data_lines = []

                def dispatch():
                    """One complete SSE frame, timestamped on arrival."""
                    nonlocal event, data_lines, final_frame, stream_error, deltas
                    if event is None and not data_lines:
                        return
                    name = event or "message"
                    data = "\n".join(data_lines)
                    event, data_lines = None, []
                    wall = time.perf_counter() - t0
                    if name == "stage":
                        milestones.setdefault("first_stage_s", wall)
                        try:
                            if json.loads(data).get("stage") == "generating":
                                milestones.setdefault("generating_s", wall)
                        except json.JSONDecodeError:
                            pass
                    elif name == "delta":
                        deltas += 1
                        milestones.setdefault("first_delta_s", wall)  # TTFT
                        milestones["last_delta_s"] = wall
                    elif name == "reset":
                        pass
                    elif name == "error":
                        try:
                            stream_error = json.loads(data).get("message")
                        except json.JSONDecodeError:
                            stream_error = data[:200]
                    elif name == "final":
                        milestones.setdefault("final_s", wall)
                        try:
                            final_frame = json.loads(data)
                        except json.JSONDecodeError:
                            final_frame = None

                try:
                    while True:
                        raw = resp.readline()
                        if not raw:
                            break
                        line = raw.decode("utf-8", "replace").rstrip("\r\n")
                        if line == "":
                            dispatch()
                        elif line.startswith("event:"):
                            event = line[len("event:"):].strip()
                        elif line.startswith("data:"):
                            data_lines.append(line[len("data:"):].strip())
                    dispatch()  # a stream that ends without its blank line
                except http.client.IncompleteRead as exc:
                    record["incomplete_read"] = getattr(exc, "args", [None])[0]
                    record["deltas"] = deltas
                    milestones["total_s"] = time.perf_counter() - t0
                    record["milestones"] = {k: round(v, 3) for k, v in milestones.items()}
                    record["stream_error"] = stream_error
                    record["ok"] = False
                    return record
            else:
                raw = resp.read()
                milestones["final_s"] = time.perf_counter() - t0
                try:
                    final_frame = json.loads(raw.decode("utf-8", "replace"))
                except json.JSONDecodeError:
                    final_frame = None
    except urllib.error.HTTPError as exc:
        record["recall_status"] = exc.code
        record["recall_error"] = exc.read()[:300].decode("utf-8", "replace")
        record["milestones"] = milestones
        record["ok"] = False
        return record
    except Exception as exc:  # noqa: BLE001 — the honest record beats a crash
        record["recall_exception"] = repr(exc)
        record["milestones"] = milestones
        record["ok"] = False
        return record

    if stream_error:
        record["stream_error"] = stream_error
    record["deltas"] = deltas

    results = extract_results(final_frame) if final_frame is not None else []
    record["recall_results"] = len(results)
    answer, citations = split_citation(first_text(results))
    record["answer_chars"] = len(answer)
    record["citations"] = len(citations)
    record["citation_samples"] = [c[:160] for c in citations[:2]]
    record["final_frame_keys"] = (
        sorted(final_frame.keys()) if isinstance(final_frame, dict) else None
    )

    # The pool the sheet derives from the citations (evidenceSources):
    # `locator: "passage"` bullets flattened to {reference, passage} —
    # the exact payload both phase-2 calls carry.
    sources = evidence_sources(citations)
    record["sources"] = len(sources)

    # Phase 2 — the two parallel walls (picker + quoted-answer), each in
    # its own thread with its own clock, like the browser fires them.
    phase2 = {}

    def picker():
        phase2["picker"] = client.json_call(
            "/quote-selection",
            {"question": query, "sources": sources, "session_id": session_id},
        )

    def writer():
        phase2["quoted"] = client.json_call(
            "/quoted-answer",
            {
                "question": query,
                "answer": answer,
                "sources": sources,
                "session_id": session_id,
            },
        )

    threads = [threading.Thread(target=picker), threading.Thread(target=writer)]
    t2 = time.perf_counter()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    phase2_wall = time.perf_counter() - t2
    record["phase2_wall_s"] = round(phase2_wall, 3)

    picker_result = phase2.get("picker", {})
    record["picker_status"] = picker_result.get("status")
    record["picker_wall_s"] = picker_result.get("wall_s")
    selections = picker_result.get("json", {}).get("selections") or []
    record["picker_selections"] = len(selections)
    record["picker_body"] = picker_result.get("body", "")

    quoted_result = phase2.get("quoted", {})
    record["quoted_status"] = quoted_result.get("status")
    record["quoted_wall_s"] = quoted_result.get("wall_s")
    record["quoted_body"] = quoted_result.get("body", "")
    quoted_json = quoted_result.get("json", {})
    blocks = quoted_json.get("blocks") or []
    record["quoted_blocks"] = len(blocks)
    record["quoted_truncated"] = bool(quoted_json.get("truncated"))

    milestones["total_s"] = milestones.get("final_s", 0) + phase2_wall
    record["milestones"] = {k: round(v, 3) for k, v in milestones.items()}
    record["ok"] = (
        record.get("recall_status") == 200
        and "final_s" in milestones
        and record["picker_selections"] > 0
        and record["quoted_blocks"] > 0
    )
    return record


def first_text(results) -> str:
    """The recall's first result row's text — the row that carries the
    answer plus its Evidence block, exactly what the sheet reads."""
    for item in results:
        if isinstance(item, dict) and item.get("text"):
            return item["text"]
    return ""


def cmd_simple(args) -> None:
    email, password = account_credentials(args.account, args)
    client = Client(args.base_url)
    client.login(email, password)
    datasets = [item for item in args.datasets.split(",") if item]
    out_dir = Path(args.out) if args.out else HERE / "runs" / f"simple-{utc_stamp()}"
    out_dir.mkdir(parents=True, exist_ok=True)

    usage_before = client.usage_live()
    print(f"usage before: {usage_before}")

    runs = []
    for index in range(1, args.runs + 1):
        print(f"--- run {index}/{args.runs}: {query_preview(query_preview_note(args, index))}")
        session_id = None
        session_create = client.json_call(
            "/sessions",
            {"book": datasets[0] if datasets else "", "title": args.query[:42]},
        )
        if session_create["status"] == 200:
            session_id = session_create["json"].get("id")
        record = run_simple_once(client, args.query, datasets, session_id)
        record["run"] = index
        record["session_created_ms"] = round(session_create["wall_s"] * 1000, 1)
        record["session_id"] = session_id
        record["usage_after"] = client.usage_live()
        runs.append(record)
        (out_dir / f"run-{index:02d}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            f"    TTFT={record['milestones'].get('first_delta_s')}s "
            f"final={record['milestones'].get('final_s')}s "
            f"phase2={record.get('phase2_wall_s')}s "
            f"picker={record.get('picker_selections')}q "
            f"blocks={record.get('quoted_blocks')} "
            f"ok={record.get('ok')}"
        )
        if index < args.runs:
            time.sleep(args.pause)

    (out_dir / "summary.json").write_text(
        json.dumps(
            {
                "kind": "simple",
                "account": email,
                "query": args.query,
                "datasets": datasets,
                "usage_before": usage_before,
                "runs": runs,
                "finished_at": now_iso(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"saved: {out_dir / 'summary.json'}")


def query_preview(text: str) -> str:
    return text if len(text) <= 60 else text[:60] + "…"


def query_preview_note(args, index: int) -> str:
    # The runner logs the same query every run; the note hook keeps the
    # per-variant future open without a second flag.
    return args.query


# --- the research driver (test 2) -------------------------------------------


class ResearchDriver:
    """The collaborator at the keyboard: polls each turn to settle,
    accepts every proposal, answers guided questions with their first
    option, and works the frontier moves until the Brief stands and the
    closing review is accepted — the full journey, one honest record."""

    def __init__(self, client: Client, out_dir: Path, max_turns: int):
        self.client = client
        self.out_dir = out_dir
        self.max_turns = max_turns
        self.turns = []
        self.stage_timeline = []
        self.revised_once = False
        self.plan_nudges = 0
        self.sent_texts = set()

    def send(self, research_session_id: str, text: str) -> dict | None:
        result = self.client.json_call(
            "/research/message", {"text": text, "session_id": research_session_id}
        )
        if result["status"] != 202:
            print(f"    send refused {result['status']}: {result['json']}")
            return None
        return result["json"]

    def poll_turn(self, turn_id: str, timeout_s: float) -> dict | None:
        """Poll /research/turn to a terminal state, the sheet's chained
        setTimeout; None on a poll that outlived the timeout (the honest
        abort — the server's turn keeps running, the record says so)."""
        deadline = time.perf_counter() + timeout_s
        while time.perf_counter() < deadline:
            status, headers, body, wall = self.client.open(
                f"/research/turn?turn={urllib.parse.quote(turn_id)}"
            )
            if status == 404:
                return {"state": "not_found"}
            if status == 200:
                payload = json.loads(body.decode("utf-8", "replace"))
                if payload.get("state") in ("done", "failed", "aborted"):
                    return payload
            time.sleep(POLL_SECONDS)
        return None

    def settle_decisions(self, research_session_id: str, suggestions: list) -> tuple:
        """Accept every pending proposal, one decide call each — the
        checkpoints are synchronous bookkeeping (no LLM) — and return
        the refreshed chip set."""
        while True:
            proposal = next(
                (
                    s
                    for s in suggestions
                    if s.get("kind") == "proposal" and s.get("accept")
                ),
                None,
            )
            if proposal is None:
                return suggestions
            body = {
                "session_id": research_session_id,
                "proposal_id": proposal["id"],
                "accept": True,
            }
            if proposal.get("choice"):
                body["choice"] = proposal["choice"]
            result = self.client.json_call("/research/decide", body)
            print(
                f"    decide {proposal['id']}: {result['status']} "
                f"«{proposal.get('text', '')[:60]}»"
            )
            if result["status"] != 200:
                return suggestions
            suggestions = result["json"].get("suggestions", suggestions)

    def choose_move(self, suggestions: list) -> tuple:
        """The next action from the chip row, priority-ordered to walk
        the journey: guided answers, mapping facets, the frontier's
        working moves, then the Brief; None when only the report chip
        (or nothing actionable) stands. An option already sent this
        session is skipped in favor of a fresh one — answering the same
        mapping facet three times stalls the walk, not the map — with
        the skip chip as the honest floor."""
        answers = [s for s in suggestions if s.get("kind") == "answer"]
        if answers:
            fresh = next(
                (s for s in answers if s["text"] not in self.sent_texts), None
            )
            chosen = fresh or answers[0]
            return chosen["text"], "answer"
        facets = [s for s in suggestions if s.get("kind") == "facet"]
        if facets:
            fresh = next(
                (s for s in facets if s["text"] not in self.sent_texts), None
            )
            if fresh is not None:
                return fresh["text"], "facet"
            skips = [s for s in suggestions if s.get("kind") == "skip"]
            if skips:
                return skips[0]["text"], "skip"
            return facets[0]["text"], "facet"
        moves = [s for s in suggestions if s.get("kind") == "move"]
        for wanted in ("brief", "synthesize", "gather", "guide"):
            chip = next((s for s in moves if s.get("id") == wanted), None)
            if chip is not None:
                return chip["text"], wanted
        targeted = next((s for s in moves if str(s.get("id", "")).startswith("q:")), None)
        if targeted is not None:
            return targeted["text"], "targeted_gather"
        skips = [s for s in suggestions if s.get("kind") == "skip"]
        if skips:
            return skips[0]["text"], "skip"
        return None, None

    def snapshot_state(self, research_session_id: str) -> dict:
        status, headers, body, wall = self.client.open(
            f"/research/state?session={urllib.parse.quote(research_session_id)}"
        )
        if status != 200:
            return {}
        return json.loads(body.decode("utf-8", "replace"))

    def run(self, question: str, datasets: list) -> dict:
        # The chat Session the research links to (ADR-0016): created
        # first, its id rides the founding call — the sheet does the
        # same when a session is open.
        chat_session = self.client.json_call(
            "/sessions", {"book": datasets[0] if datasets else "", "title": question[:42]}
        )
        chat_session_id = chat_session["json"].get("id")
        started = time.perf_counter()
        create = self.client.json_call(
            "/research/message",
            {
                "text": question,
                "question": question,
                "datasets": datasets,
                "chat_session_id": chat_session_id,
            },
        )
        if create["status"] != 202:
            raise SystemExit(
                f"research start refused {create['status']}: {create['json']}"
            )
        research_session_id = create["json"]["session_id"]
        turn_id = create["json"]["turn_id"]
        print(f"research session {research_session_id} (chat {chat_session_id})")

        index = 0
        stalled = 0
        last_move = ""
        while index < self.max_turns:
            index += 1
            t0 = time.perf_counter()
            print(f"--- turn {index} ({turn_id[:8]})")
            payload = self.poll_turn(turn_id, timeout_s=1800)
            wall = round(time.perf_counter() - t0, 1)
            if payload is None:
                print("    turn poll timeout (1800s) — recording honestly")
                self.turns.append({"index": index, "state": "timeout", "wall_s": wall})
                break
            state = payload.get("state")
            budget = payload.get("budget", {})
            print(
                f"    {state} wall={wall}s budget={budget.get('calls')}/"
                f"{budget.get('cap')}"
                + (f" exhausted={budget.get('reason')}" if budget.get("exhausted") else "")
            )
            suggestions = payload.get("suggestions", [])
            research_state = payload.get("research_state", {})
            self.stage_timeline.append(
                {
                    "turn": index,
                    "stage": research_state.get("stage"),
                    "evidence": research_state.get("evidence_count"),
                    "claims": len(research_state.get("claims") or []),
                    "gaps": len(research_state.get("gaps") or []),
                    "brief_sections": research_state.get("brief_sections"),
                    "wall_s": wall,
                }
            )
            if state != "done":
                self.turns.append(
                    {"index": index, "state": state, "wall_s": wall,
                     "detail": payload.get("detail")}
                )
                break

            suggestions = self.settle_decisions(research_session_id, suggestions)
            snapshot = self.snapshot_state(research_session_id)
            review = (snapshot.get("closing_review") or {}) if snapshot else {}
            self.turns.append(
                {
                    "index": index,
                    "state": state,
                    "wall_s": wall,
                    "budget": budget,
                    "stage": research_state.get("stage"),
                    "stage_snapshot": {
                        k: snapshot.get(k)
                        for k in (
                            "stage",
                            "evidence_count",
                            "claims",
                            "gaps",
                            "pending_proposals",
                            "destination",
                            "brief_plan",
                            "brief_sections",
                            "closing_review",
                        )
                    },
                    "suggestions_seen": [
                        {"kind": s.get("kind"), "id": s.get("id"), "label": s.get("label")}
                        for s in suggestions
                    ],
                }
            )
            (self.out_dir / f"turn-{index:02d}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2)[:4_000_000],
                encoding="utf-8",
            )

            # The journey's completion signal (the map's own ledger):
            # the closing review's verdict accepted — the Brief stands
            # reviewed. A review with failing sections earns ONE revise
            # round (its own chip), then the next verdict is accepted
            # whatever it says: the walk ends, honestly.
            if review.get("reviewed"):
                failing = review.get("failing") or []
                revise_chip = next(
                    (s for s in suggestions if s.get("id") == "revise"), None
                )
                if failing and revise_chip and not self.revised_once:
                    self.revised_once = True
                    print(f"    review failing {len(failing)} sections — one revise round")
                    sent = self.send(research_session_id, revise_chip["text"])
                    if sent is None:
                        break
                    turn_id = sent["turn_id"]
                    self.turns[-1]["revise_sent"] = True
                    continue
                print("    journey complete: closing review accepted")
                self.turns[-1]["journey_complete"] = True
                break

            move, kind = self.choose_move(suggestions)
            report_ready = any(s.get("id") == "report" for s in suggestions)
            if move is None:
                if report_ready:
                    print("    journey complete: Brief stands, report ready")
                else:
                    print("    no actionable chip — ending the walk")
                break
            if move == last_move:
                stalled += 1
            else:
                stalled = 0
            last_move = move
            if stalled >= 3:
                print("    same move three turns running — stopping the journey")
                self.send(research_session_id, "توقف پژوهش")
                self.turns[-1]["stalled_stop"] = True
                break
            if kind == "brief" and not bool(
                (snapshot.get("brief_plan") or {}).get("accepted")
            ):
                # The plan gate (T8): the Brief refuses without an
                # accepted section plan, and a plan proposal parks only
                # on an exploration turn — a typed conversational nudge
                # invites it, the decide flow accepts it. Two nudges,
                # then the brief goes anyway and the refusal is the
                # honest record.
                if self.plan_nudges < 2:
                    self.plan_nudges += 1
                    nudge = "برای نوشتن خلاصۀ پژوهش، برنامۀ بخش‌ها را پیشنهاد بده"
                    print(f"    brief gated on the plan — nudge {self.plan_nudges}/2")
                    sent = self.send(research_session_id, nudge)
                    if sent is None:
                        break
                    turn_id = sent["turn_id"]
                    self.turns[-1]["plan_nudge"] = True
                    continue
                print("    plan never parked — sending brief anyway, the refusal records")
            if kind == "brief" and report_ready:
                # A standing Brief plus the report chip: the walk is
                # done — one more brief would rewrite what stands.
                print("    Brief already stands — journey complete")
                break
            print(f"    next move [{kind}]: {move[:60]}")
            self.sent_texts.add(move)
            sent = self.send(research_session_id, move)
            if sent is None:
                break
            turn_id = sent["turn_id"]

        total_minutes = round((time.perf_counter() - started) / 60, 1)
        report_saved = self.fetch_report(research_session_id)
        return {
            "research_session_id": research_session_id,
            "chat_session_id": chat_session_id,
            "turns": self.turns,
            "stage_timeline": self.stage_timeline,
            "total_minutes": total_minutes,
            "report_saved": report_saved,
        }

    def fetch_report(self, research_session_id: str) -> bool:
        for fmt, name in (("md", "research-report.md"), ("html", "research-report.html")):
            status, headers, body, wall = self.client.open(
                f"/research/report?session={urllib.parse.quote(research_session_id)}"
                f"&format={fmt}"
            )
            if status == 200:
                (self.out_dir / name).write_bytes(body)
                print(f"    report {fmt}: {len(body)} bytes")
            else:
                print(f"    report {fmt}: {status}")
                return False
        return True


def cmd_research(args) -> None:
    email, password = account_credentials(args.account, args)
    client = Client(args.base_url)
    client.login(email, password)
    datasets = [item for item in args.datasets.split(",") if item]
    out_dir = Path(args.out) if args.out else HERE / "runs" / f"research-{utc_stamp()}"
    out_dir.mkdir(parents=True, exist_ok=True)

    usage_before = client.usage_live()
    print(f"usage before: {usage_before}")
    driver = ResearchDriver(client, out_dir, max_turns=args.max_turns)
    summary = driver.run(args.question, datasets)
    summary.update(
        {
            "kind": "research",
            "account": email,
            "question": args.question,
            "datasets": datasets,
            "usage_before": usage_before,
            "usage_after": client.usage_live(),
            "finished_at": now_iso(),
        }
    )
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"saved: {out_dir / 'summary.json'} — {summary['total_minutes']} min, "
        f"{len(summary['turns'])} turns"
    )


# --- the accounts subcommand -------------------------------------------------


def cmd_accounts(args) -> None:
    admin = Client(args.base_url)
    if args.admin_email and args.admin_password:
        admin.login(args.admin_email, args.admin_password)
    else:
        raise SystemExit(
            "admin credentials required: --admin-email / --admin-password "
            "(or pass --accounts-file with an existing accounts.json)"
        )
    entries = {}
    for name, question in BENCH_ACCOUNTS.items():
        email = f"{name}@pilot.local"
        password = secrets.token_urlsafe(12)
        created = admin.json_call(
            "/auth/accounts", {"email": email, "password": password}
        )
        print(f"create {email}: {created['status']}")
        if created["status"] not in (200, 201):
            continue
        topped = admin.json_call(
            "/admin/topup", {"email": email, "amount": TOPUP_TOMAN}, form=True
        )
        print(f"topup {email}: {topped['status']}")
        entries[name] = {
            "email": email,
            "password": password,
            "question": question,
            "topup_toman": TOPUP_TOMAN,
        }
    ACCOUNTS_FILE.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.chmod(ACCOUNTS_FILE, 0o600)
    print(f"saved: {ACCOUNTS_FILE} ({len(entries)} accounts)")


def account_credentials(name_or_email: str, args) -> tuple:
    if "@" in name_or_email:
        return name_or_email, args.password or ""
    if ACCOUNTS_FILE.exists():
        entries = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
        entry = entries.get(name_or_email)
        if entry:
            return entry["email"], entry["password"]
    raise SystemExit(f"unknown account {name_or_email!r}; run `accounts` first")


# --- the report subcommand ---------------------------------------------------


def cmd_report(args) -> None:
    runs_root = Path(args.runs)
    rows_simple, rows_research = [], []
    for summary_path in sorted(runs_root.rglob("summary.json")):
        data = json.loads(summary_path.read_text(encoding="utf-8"))
        if data.get("kind") == "simple":
            for run in data.get("runs", []):
                m = run.get("milestones", {})
                rows_simple.append(
                    {
                        "run_dir": summary_path.parent.name,
                        "run": run.get("run"),
                        "account": data.get("account"),
                        "query": data.get("query"),
                        "ttft_s": m.get("first_delta_s"),
                        "final_s": m.get("final_s"),
                        "phase2_wall_s": run.get("phase2_wall_s"),
                        "picker_wall_s": run.get("picker_wall_s"),
                        "quoted_wall_s": run.get("quoted_wall_s"),
                        "total_s": m.get("total_s"),
                        "picker_selections": run.get("picker_selections"),
                        "quoted_blocks": run.get("quoted_blocks"),
                        "ok": run.get("ok"),
                    }
                )
        elif data.get("kind") == "research":
            rows_research.append(
                {
                    "run_dir": summary_path.parent.name,
                    "account": data.get("account"),
                    "question": data.get("question"),
                    "turns": len(data.get("turns", [])),
                    "total_minutes": data.get("total_minutes"),
                    "report_saved": data.get("report_saved"),
                }
            )

    simple_csv = runs_root / "simple-runs.csv"
    if rows_simple:
        with simple_csv.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows_simple[0].keys()))
            writer.writeheader()
            writer.writerows(rows_simple)
    research_csv = runs_root / "research-runs.csv"
    if rows_research:
        with research_csv.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows_research[0].keys()))
            writer.writeheader()
            writer.writerows(rows_research)
    print(f"simple rows: {len(rows_simple)} -> {simple_csv}")
    print(f"research rows: {len(rows_research)} -> {research_csv}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default=os.environ.get("PILOT_BASE_URL", DEFAULT_BASE))
    sub = parser.add_subparsers(dest="command", required=True)

    p_accounts = sub.add_parser("accounts")
    p_accounts.add_argument("--admin-email", default=os.environ.get("PILOT_ADMIN_EMAIL", ""))
    p_accounts.add_argument("--admin-password", default=os.environ.get("PILOT_ADMIN_PASSWORD", ""))

    p_simple = sub.add_parser("simple")
    p_simple.add_argument("--account", required=True)
    p_simple.add_argument("--password", default="")
    p_simple.add_argument("--query", required=True)
    p_simple.add_argument("--datasets", default="tarhe-kolli")
    p_simple.add_argument("--runs", type=int, default=3)
    p_simple.add_argument("--pause", type=float, default=5.0)
    p_simple.add_argument("--out", default="")

    p_research = sub.add_parser("research")
    p_research.add_argument("--account", required=True)
    p_research.add_argument("--password", default="")
    p_research.add_argument("--question", required=True)
    p_research.add_argument("--datasets", default="tarhe-kolli")
    p_research.add_argument("--max-turns", type=int, default=45)
    p_research.add_argument("--out", default="")

    p_report = sub.add_parser("report")
    p_report.add_argument("--runs", required=True)

    args = parser.parse_args()
    {"accounts": cmd_accounts, "simple": cmd_simple,
     "research": cmd_research, "report": cmd_report}[args.command](args)


if __name__ == "__main__":
    main()
