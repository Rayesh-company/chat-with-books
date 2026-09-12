#!/usr/bin/env python3
"""Serve the Farsi Session sheet, proxy first-answer recall to Cognee,
pick the Quote selection — the first answer rendered on the sheet
(ADR-0006, issue #28) — relay the recorded Next-tier COT probe to
cognee-next-tier (the sheet no longer calls it — the operator probe
remains), compose the Quoted answer for phase 2, and orchestrate the
Deep dive (ADR-0006): Planner, the bounded retrieval (round 1 plus at
most one gap round over the quote-starved sections, issue #27) on the
second Cognee service, Synthesizer."""

from __future__ import annotations

from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import json
import os
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

try:
    # The deep modules behind this facade; the re-exports below keep the
    # tests' single `from ui import serve` import seam.
    from ui.guard import (
        _citation_labels,
        _count_word,
        _json_object,
        _numbered_passages,
        _strip_code_fence,
        book_label,
        first_page_label,
        guard_blocks,
        guard_sentences,
        normalize_for_match,
        pages_label,
        parse_quoted_reply,
    )
    from ui.quotas import (
        DAILY_CHAT_LIMIT,
        chats_today,
        normalize_phone,
        record_chat,
    )
    from ui.composer import (
        COMPOSER_MAX_TOKENS,
        COMPOSER_TIMEOUT,
        _composer_content,
        _composer_reply,
        build_continuation_prompt,
        build_planner_prompt,
        build_quoted_prompt,
        compose_quoted_answer,
    )
    from ui.dive import (
        BOOK_DATASETS,
        DIVE_BUSY_GLOBAL_DETAIL,
        DIVE_BUSY_PHONE_DETAIL,
        DIVE_EVENT_ABORTED,
        DIVE_EVENT_DONE,
        DIVE_EVENT_FAILED,
        DIVE_EVENT_PLANNING,
        DIVE_EVENT_SEARCHING,
        DIVE_EVENT_WRITING,
        DIVE_FAILED_DETAIL,
        DIVE_MAX_CONCURRENT,
        DIVE_MAX_RETRIEVAL_ROUNDS,
        DIVE_MAX_SUB_QUESTIONS,
        DIVE_MODEL,
        DIVE_NOT_FOUND_DETAIL,
        DIVE_NO_EVIDENCE_DETAIL,
        DIVE_REGISTRY,
        DIVE_REGISTRY_LOCK,
        DIVE_SEARCH_TIMEOUT,
        DIVE_SEARCH_TYPE,
        DIVE_STARVED_PASSAGES,
        DIVE_TERMINAL_STATES,
        DiveJob,
        NEXT_TIER_URL,
        _dive_advance,
        _dive_gap_event,
        _start_dive_job,
        abort_dive_job,
        build_dive_planner_prompt,
        build_dive_prompt,
        compose_dive_study,
        dive_recall,
        dive_subquestions_from_reply,
        parse_evidence_sources,
        plan_dive_subquestions,
        run_dive_job,
        run_dive_round,
        with_dive_references,
    )
except ImportError:  # the container runs this file as a script beside the modules
    from guard import (
        _citation_labels,
        _count_word,
        _json_object,
        _numbered_passages,
        _strip_code_fence,
        book_label,
        first_page_label,
        guard_blocks,
        guard_sentences,
        normalize_for_match,
        pages_label,
        parse_quoted_reply,
    )
    from quotas import (
        DAILY_CHAT_LIMIT,
        chats_today,
        normalize_phone,
        record_chat,
    )
    from composer import (
        COMPOSER_MAX_TOKENS,
        COMPOSER_TIMEOUT,
        _composer_content,
        _composer_reply,
        build_continuation_prompt,
        build_planner_prompt,
        build_quoted_prompt,
        compose_quoted_answer,
    )
    from dive import (
        BOOK_DATASETS,
        DIVE_BUSY_GLOBAL_DETAIL,
        DIVE_BUSY_PHONE_DETAIL,
        DIVE_EVENT_ABORTED,
        DIVE_EVENT_DONE,
        DIVE_EVENT_FAILED,
        DIVE_EVENT_PLANNING,
        DIVE_EVENT_SEARCHING,
        DIVE_EVENT_WRITING,
        DIVE_FAILED_DETAIL,
        DIVE_MAX_CONCURRENT,
        DIVE_MAX_RETRIEVAL_ROUNDS,
        DIVE_MAX_SUB_QUESTIONS,
        DIVE_MODEL,
        DIVE_NOT_FOUND_DETAIL,
        DIVE_NO_EVIDENCE_DETAIL,
        DIVE_REGISTRY,
        DIVE_REGISTRY_LOCK,
        DIVE_SEARCH_TIMEOUT,
        DIVE_SEARCH_TYPE,
        DIVE_STARVED_PASSAGES,
        DIVE_TERMINAL_STATES,
        DiveJob,
        NEXT_TIER_URL,
        _dive_advance,
        _dive_gap_event,
        _start_dive_job,
        abort_dive_job,
        build_dive_planner_prompt,
        build_dive_prompt,
        compose_dive_study,
        dive_recall,
        dive_subquestions_from_reply,
        parse_evidence_sources,
        plan_dive_subquestions,
        run_dive_job,
        run_dive_round,
        with_dive_references,
    )

UI_DIR = Path(__file__).resolve().parent
COGNEE_URL = os.environ.get("COGNEE_URL", "http://127.0.0.1:8000").rstrip("/")
HOST = os.environ.get("SESSION_UI_HOST", "127.0.0.1")
PORT = int(os.environ.get("SESSION_UI_PORT", "8765"))
PROXY_TIMEOUT = 600
# A Next-tier search runs four chain-of-thought rounds and can pass ten
# minutes (live logs, 2026-09-11) — past the shared 600s leash the relay
# once gave up on a search the second service had already finished.
# Phase 3 waits on its own leash; "unreachable: timed out" only after it.
NEXT_TIER_TIMEOUT = int(os.environ.get("NEXT_TIER_TIMEOUT", "1200"))
ALLOWED_PROXY = {"/health", "/api/v1/recall"}
NEXT_TIER_SEARCH_TYPE = "GRAPH_COMPLETION_COT"

# Quote selection (ADR-0006, issue #28): the first answer rendered on
# the sheet is the Evidence pool's Quote selection — verbatim Book
# sentences, no AI prose — picked by EXACTLY ONE composer call. The
# model is the existing COMPOSER_MODEL pin (glm-5.3-flash, ADR-0002);
# thinking disabled for the phase-2 writer's measured reason:
# copy-matching, not reasoning. The pool itself is untouched — the same
# (reference, passage) pairs stay phase 2's exact input.
QUOTE_SELECTION_AIM = 10
QUOTE_SELECTION_CEILING = 12
QUOTE_SELECTION_FLOOR = 4


def build_picker_prompt(question: str, sources) -> str:
    """The picker's brief: select, don't write.

    The aim wording is derived from QUOTE_SELECTION_AIM — the two cannot
    drift — and the reply shape is the selection list guard_sentences
    already consumes.
    """
    passages = _numbered_passages(sources)
    return (
        "You are selecting the Quote selection for a Farsi Q&A sheet "
        "over the Books.\n\n"
        f"Question: {question}\n\n"
        "Passages (numbered, from the Books' retrieved Evidence; "
        "text-layer noise like \\b backspaces may appear between "
        "words):\n"
        f"{passages}\n\n"
        "Task: select the Farsi sentences that together best answer "
        f"the question — aim for {_count_word(QUOTE_SELECTION_AIM)}; "
        "fewer only when the passages hold fewer. Each selection is a "
        "complete Farsi sentence copied VERBATIM from exactly ONE "
        "passage (ignore the \\b noise; write proper Farsi). Do not "
        "paraphrase, do not merge, do not shorten. Never invent a "
        "sentence.\n\n"
        "Reply with ONLY a JSON object, no prose, no code fence:\n"
        '{"selections": [{"text": "<verbatim sentence>", '
        '"source": <passage index>}]}'
    )


def parse_picker_reply(content):
    """Pull the selections list out of the picker's reply; [] when
    malformed.

    The parse conventions of parse_quoted_reply — code fence stripped,
    json.loads with one brace-scoped retry for prose-wrapped JSON, the
    shared _json_object — without the salvage: a picker reply is tiny,
    and a truncated one would not survive the floor anyway.
    """
    parsed = _json_object(content)
    selections = parsed.get("selections") if isinstance(parsed, dict) else None
    return selections if isinstance(selections, list) else []


def pick_quote_selection(question: str, sources):
    """One picker call over the pool; the guarded selections, or [].

    Exactly ONE composer call (glm-5.3-flash, thinking disabled,
    COMPOSER_MAX_TOKENS, endpoint-default temperature). The reply runs
    the existing verbatim letter-stream guard — a paraphrase, or a
    verbatim sentence claiming the wrong index, drops; the survivors cap
    at QUOTE_SELECTION_CEILING, and fewer than QUOTE_SELECTION_FLOOR of
    them means the picker missed the floor: [] — the same empty shape a
    call failure returns, so the sheet's fallback is one uniform shape.
    Every kept sentence carries the labels of exactly the passage it
    claims, attached server-side like guard_blocks does.
    """
    try:
        reply = _composer_reply(build_picker_prompt(question, sources), "disabled")
        content = _composer_content(reply)
    except (KeyError, ValueError, OSError):
        return []
    kept = guard_sentences(parse_picker_reply(content), sources)
    kept = kept[:QUOTE_SELECTION_CEILING]
    if len(kept) < QUOTE_SELECTION_FLOOR:
        return []
    return [
        {
            "text": item["text"],
            "reference": item["reference"],
            **_citation_labels(item["reference"]),
        }
        for item in kept
    ]


class SessionHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(UI_DIR), **kwargs)

    def log_message(self, format, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))

    def end_headers(self) -> None:
        # The sheet is an evolving single page: every load must revalidate,
        # never render a stale cached copy after a deploy (a plain refresh
        # kept showing the pre-tab page after the 2026-09-11 switchover).
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/livez":
            # Local liveness for the container healthcheck — the sheet's
            # /health proxies to Cognee and would couple this container's
            # health to another service's.
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/health":
            self._proxy("GET")
            return
        if path == "/deep-dive/status":
            self._deep_dive_status()
            return
        super().do_GET()

    def _drain_request_body(self) -> None:
        """Read the body Content-Length promised before answering and
        closing. Closing with bytes unread makes the kernel answer RST,
        not FIN, and the response we just wrote can be lost to the reset
        (WinError 10054 flaking the gate tests; through nginx the same
        reset can surface as a 502 instead of the gate's 429)."""
        length = int(self.headers.get("Content-Length", "0") or "0")
        while length > 0:
            chunk = self.rfile.read(min(length, 65536))
            if not chunk:
                break
            length -= len(chunk)

    def _json_error(self, status: int, detail: str) -> None:
        self._drain_request_body()
        self._send_json(status, {"detail": detail})

    def _send_json(self, status: int, payload: dict) -> None:
        """One JSON reply. The caller must have consumed the request body
        already (or never had one) — unlike _json_error this does not
        drain, so a second read cannot block on bytes already taken."""
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _gate_phone(self):
        """The ask gate: a valid phone with chats left today; records the
        chat. Answers 400/429 itself and returns None when rejected."""
        phone = normalize_phone(self.headers.get("X-Session-Phone", ""))
        if not phone:
            self._json_error(400, "شمارهٔ تلفن همراه را وارد کنید.")
            return None
        if chats_today(phone) >= DAILY_CHAT_LIMIT:
            self._json_error(
                429, "شمار گفتگوهای امروز این شماره پر شده است؛ فردا بیایید."
            )
            return None
        record_chat(phone)
        return phone

    def _quoted_phone(self):
        """The phase-2 gate: a valid phone with at least one chat today
        (the quoted answer belongs to a chat that already started)."""
        phone = normalize_phone(self.headers.get("X-Session-Phone", ""))
        if not phone:
            self._json_error(400, "شمارهٔ تلفن همراه را وارد کنید.")
            return None
        if chats_today(phone) < 1:
            self._json_error(
                429, "پاسخ استنادی بخشی از همان گفتگو است؛ اول یک پرسش بپرسید."
            )
            return None
        return phone

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/v1/recall":
            phone = self._gate_phone()
            if phone is None:
                return
            # A new ask owns the sheet (issue #26): after the gate has
            # recorded the chat, the phone's own in-flight dive is
            # aborted — cooperatively; the worker exits at its next
            # boundary. Another phone's dive is never touched.
            with DIVE_REGISTRY_LOCK:
                own_dives = [
                    job
                    for job in DIVE_REGISTRY.values()
                    if job.phone == phone
                    and job.state not in DIVE_TERMINAL_STATES
                ]
            for job in own_dives:
                abort_dive_job(job)
            self._proxy("POST")
            return
        if path == "/quoted-answer":
            if self._quoted_phone() is None:
                return
            self._quoted_answer()
            return
        if path == "/quote-selection":
            if self._quoted_phone() is None:
                return
            self._quote_selection()
            return
        if path == "/deep-dive":
            phone = self._quoted_phone()
            if phone is None:
                return
            self._deep_dive(phone)
            return
        if path == "/next-tier-recall":
            if self._quoted_phone() is None:
                return
            self._next_tier_recall()
            return
        self._drain_request_body()
        self.send_error(404, "Not found")

    def _quoted_answer(self) -> None:
        """Compose the Quoted answer; empty blocks = fallback."""
        length = int(self.headers.get("Content-Length", "0") or "0")
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            question = payload["question"]
            answer = payload.get("answer")
            sources = [
                {
                    "reference": source["reference"],
                    "passage": source["passage"],
                }
                for source in payload["sources"]
                if isinstance(source, dict)
                and isinstance(source.get("reference"), str)
                and isinstance(source.get("passage"), str)
            ]
            if not isinstance(question, str) or not question.strip() or not sources:
                raise ValueError("question and sources are required")
            if not isinstance(answer, str):
                answer = ""
        except (ValueError, KeyError, TypeError):
            self.send_error(400, "Bad request")
            return
        blocks, truncated = compose_quoted_answer(question, answer, sources)
        body = json.dumps(
            {"blocks": blocks, "truncated": truncated}, ensure_ascii=False
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _quote_selection(self) -> None:
        """Pick the Quote selection (ADR-0006, issue #28): the pool
        exactly as the sheet parsed it, one picker call, the guarded
        selections back. The gate is phase 2's shape — the picker
        belongs to the chat phase 1 recorded — so it needs a phone with
        at least one chat today and never records or counts one. A
        malformed body or an empty pool answers 400 (a JSON detail, the
        gate's shape) before any upstream call; a picker failure or a
        below-floor selection answers 200 {"selections": []} — the one
        uniform empty shape the sheet's prose fallback consumes, never
        a 5xx."""
        length = int(self.headers.get("Content-Length", "0") or "0")
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            question = payload["question"]
            sources = [
                {
                    "reference": source["reference"],
                    "passage": source["passage"],
                }
                for source in payload["sources"]
                if isinstance(source, dict)
                and isinstance(source.get("reference"), str)
                and isinstance(source.get("passage"), str)
            ]
            if not isinstance(question, str) or not question.strip() or not sources:
                raise ValueError("question and sources are required")
        except (ValueError, KeyError, TypeError):
            # The body is already read above, so _send_json is safe —
            # _json_error would drain a second time and block.
            self._send_json(
                400, {"detail": "پرسش و استنادهای بازیابی‌شده را بفرستید."}
            )
            return
        selections = pick_quote_selection(question, sources)
        self._send_json(200, {"selections": selections, "pool_size": len(sources)})

    def _deep_dive_status(self) -> None:
        """The dive's poll surface (issue #26): the sheet asks for a
        job's state, Farsi events, and elapsed seconds — and, when the
        job settled, its outcome. `done` carries the study payload
        ({"blocks", "truncated"}), `failed` a short Farsi detail, and an
        unknown id (a restart emptied the registry, or a foreign phone)
        answers 404 — the recorded failure surface, never a hang. The
        phone must match the job's: one user's poll never reads another
        user's dive."""
        phone = normalize_phone(self.headers.get("X-Session-Phone", ""))
        query = parse_qs(urlparse(self.path).query)
        job_id = (query.get("job") or [""])[0]
        payload = None
        with DIVE_REGISTRY_LOCK:
            job = DIVE_REGISTRY.get(job_id)
            if job is not None and job.phone == phone:
                payload = {
                    "state": job.state,
                    "events": list(job.events),
                    "elapsed": round(time.monotonic() - job.started_at, 1),
                }
                if job.state == "done":
                    blocks, truncated = job.result
                    payload["blocks"] = blocks
                    payload["truncated"] = truncated
                elif job.state == "failed":
                    payload["detail"] = job.error
        if payload is None:
            self._json_error(404, DIVE_NOT_FOUND_DETAIL)
            return
        self._send_json(200, payload)

    def _deep_dive(self, phone: str) -> None:
        """The Deep dive start (ADR-0006, issue #26): the gate is the
        phase-2 shape exactly — the dive belongs to the chat phase 1
        recorded, so it needs a phone with at least one chat today and
        never counts or checks the limit. The body carries only the
        query; every upstream payload is pinned server-side. The dive
        runs on its own registry job and this handler answers the job
        identity immediately: 202 {"job_id": ...} — the sheet polls
        /deep-dive/status for state, events, and the study. Nothing is
        ever queued: a busy phone (or a full registry) is rejected, not
        deferred."""
        length = int(self.headers.get("Content-Length", "0") or "0")
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            query = payload["query"]
            if not isinstance(query, str) or not query.strip():
                raise ValueError("query is required")
        except (ValueError, KeyError, TypeError):
            # The body is already read above, so _send_json is safe —
            # _json_error would drain a second time and block.
            self._send_json(400, {"detail": "پرسش را بنویسید."})
            return
        job, busy_detail = _start_dive_job(phone, query.strip())
        if job is None:
            self._send_json(429, {"detail": busy_detail})
            return
        self._send_json(202, {"job_id": job.id})

    def _next_tier_recall(self) -> None:
        """The recorded Next-tier COT relay (ADR-0005), kept live as the
        Session operator's probe of the second service — the sheet no
        longer calls it (the dive replaced the auto-start). The search
        shape is pinned here, never chosen in the browser:
        GRAPH_COMPLETION_COT over the Book set, references on, not
        streamed (the operator waits for the JSON). Same chat as phase 1
        — the gate only checks a chat happened today and never counts."""
        length = int(self.headers.get("Content-Length", "0") or "0")
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            query = payload["query"]
            if not isinstance(query, str) or not query.strip():
                raise ValueError("query is required")
        except (ValueError, KeyError, TypeError):
            # The body is already read above, so _send_json is safe —
            # _json_error would drain a second time and block.
            self._send_json(400, {"detail": "پرسش را بنویسید."})
            return
        body = json.dumps(
            {
                "searchType": NEXT_TIER_SEARCH_TYPE,
                "query": query.strip(),
                "datasets": list(BOOK_DATASETS),
                "includeReferences": True,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = Request(
            f"{NEXT_TIER_URL}/api/v1/recall",
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        self._relay(request, "next-tier", timeout=NEXT_TIER_TIMEOUT)

    def _relay(self, request: Request, service: str, timeout: int = PROXY_TIMEOUT) -> None:
        """Relay one upstream reply — JSON as-is, text/event-stream
        unbuffered — with the unreachable contract (504 + detail)."""
        try:
            with urlopen(request, timeout=timeout) as resp:
                content_type = resp.headers.get("Content-Type", "application/json")
                if "text/event-stream" in content_type:
                    # Close-delimited relay (HTTP/1.0): no Content-Length, lines
                    # hit the socket as they arrive. readline() because read(n)
                    # would block until n bytes collect.
                    self.send_response(resp.status)
                    self.send_header("Content-Type", content_type)
                    self.end_headers()
                    try:
                        while True:
                            line = resp.readline()
                            if not line:
                                break
                            self.wfile.write(line)
                    except OSError:
                        sys.stderr.write(
                            "%s - stream client went away\n" % self.address_string()
                        )
                    return
                payload = resp.read()
                self.send_response(resp.status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
        except HTTPError as exc:
            payload = exc.read()
            self.send_response(exc.code)
            self.send_header("Content-Type", exc.headers.get("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except (URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            message = json.dumps(
                {"detail": f"{service} unreachable: {reason}"},
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_response(504)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(message)))
            self.end_headers()
            self.wfile.write(message)

    def _proxy(self, method: str) -> None:
        path = self.path.split("?", 1)[0]
        sys.stderr.write("%s - proxy %s %s\n" % (self.address_string(), method, path))
        sys.stderr.flush()
        if path not in ALLOWED_PROXY:
            self._drain_request_body()
            self.send_error(404, "Not found")
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length) if length else None
        headers = {}
        content_type = self.headers.get("Content-Type")
        if content_type:
            headers["Content-Type"] = content_type
        request = Request(
            f"{COGNEE_URL}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        self._relay(request, "cognee")


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), SessionHandler)
    print(f"Session sheet http://{HOST}:{PORT}", flush=True)
    print(f"Proxying /api/v1/recall and /health to {COGNEE_URL}", flush=True)
    print(f"Relaying /next-tier-recall to {NEXT_TIER_URL}", flush=True)
    print(f"Orchestrating /deep-dive against {NEXT_TIER_URL}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
