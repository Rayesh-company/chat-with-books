#!/usr/bin/env python3
"""Serve the Farsi Session sheet, proxy first-answer recall to Cognee,
pick the Quote selection — the first answer rendered on the sheet
(ADR-0006, issue #28) — relay the recorded Next-tier COT probe to
cognee-next-tier (the operator probe remains), compose the Quoted answer
for phase 2, and run Research Mode (ADR-0008): the multi-turn Wayfinder
whose turns classify intent, gather evidence over the second Cognee
service with the old dive's bounded fan-out, synthesize guarded claims,
and close with a Brief built from the research state."""

from __future__ import annotations

from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

try:
    # The visual pipeline: pages of the Books rasterized by a real PDF
    # engine (PDFium, ADR-0007's rendering addendum) — the browser never
    # re-typesets these Persian subsets. Kept optional so a dev machine
    # without the wheel still serves the sheet (the reader's /books
    # page route answers 503 and the sheet explains itself).
    import pypdfium2 as _pdfium
except ImportError:  # pragma: no cover - the container installs it
    _pdfium = None

try:
    # The deep modules behind this facade; the re-exports below keep the
    # tests' single `from ui import serve` import seam.
    from ui.guard import (
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
    from ui.accounts import (
        account_by_email,
        create_account,
        deduct_balance,
        get_balance,
        list_accounts,
        verify_login,
    )
    from ui import audit
    from ui.console import console_html
    from ui.composer import (
        COMPOSER_MAX_TOKENS,
        COMPOSER_TIMEOUT,
        build_continuation_prompt,
        build_planner_prompt,
        build_quoted_prompt,
        compose_quoted_answer,
        set_meter,
    )
    from ui.ledger import (
        record_composer_call,
        record_size_estimate,
        session_total,
        today_total,
        day_total,
    )
    from ui import ledger
    from ui.picker import (
        build_picker_prompt,
        parse_picker_reply,
        pick_quote_selection,
    )
    from ui.recall_more import (
        build_broaden_prompt,
        parse_broaden_reply,
        recall_more,
    )
    from ui.page_resolver import install as install_page_resolver
    from ui.dive import (
        BOOK_DATASETS,
        DIVE_MAX_RETRIEVAL_ROUNDS,
        DIVE_MAX_SUB_QUESTIONS,
        DIVE_SEARCH_TIMEOUT,
        DIVE_STARVED_PASSAGES,
        NEXT_TIER_URL,
        dive_recall,
        dive_retrieve,
        parse_evidence_sources,
        run_dive_round,
    )
    from ui.report import research_session_report
    from ui.research import (
        COMMAND_AUDIT,
        COMMAND_BRIEF,
        COMMAND_GATHER,
        COMMAND_SYNTHESIZE,
        CONVERSATIONAL_INTENTS,
        RESEARCH_BUSY_GLOBAL_DETAIL,
        RESEARCH_BUSY_PHONE_DETAIL,
        RESEARCH_EVIDENCE_FLOOR,
        RESEARCH_EVENT_ABORTED,
        RESEARCH_EVENT_BRIEF,
        RESEARCH_EVENT_CLASSIFYING,
        RESEARCH_EVENT_DONE,
        RESEARCH_EVENT_FAILED,
        RESEARCH_EVENT_PLANNING,
        RESEARCH_EVENT_SEARCHING,
        RESEARCH_EVENT_WRITING,
        RESEARCH_FAILED_DETAIL,
        RESEARCH_INTENTS,
        RESEARCH_MAX_CONCURRENT,
        RESEARCH_MODEL,
        RESEARCH_NO_EVIDENCE_DETAIL,
        RESEARCH_RECENT_SETTLED,
        RESEARCH_REGISTRY,
        RESEARCH_REGISTRY_LOCK,
        RESEARCH_SESSION_CAP_DETAIL,
        RESEARCH_SESSION_CLOSED_DETAIL,
        RESEARCH_SESSION_NOT_FOUND_DETAIL,
        RESEARCH_SESSION_TURN_CAP,
        RESEARCH_TURN_NOT_FOUND_DETAIL,
        TURN_TERMINAL_STATES,
        ResearchTurn,
        abort_phone_research,
        abort_research_turn,
        build_classify_prompt,
        build_conversational_prompt,
        build_subquestions_prompt,
        build_synthesis_prompt,
        classify_message,
        compose_guarded_reply,
        decide_proposal,
        ensure_session,
        find_turn,
        next_best_move,
        parse_classify_reply,
        plan_subquestions,
        record_claims,
        research_session_messages,
        research_session_state,
        research_state_summary,
        research_suggestions,
        run_research_turn,
        seed_evidence,
        start_research_turn,
        turn_status_payload,
        with_references,
    )
except ImportError:  # the container runs this file as a script beside the modules
    from guard import (
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
    from accounts import (
        account_by_email,
        create_account,
        deduct_balance,
        get_balance,
        list_accounts,
        verify_login,
    )
    import audit
    from console import console_html
    from report import research_session_report
    from composer import (
        COMPOSER_MAX_TOKENS,
        COMPOSER_TIMEOUT,
        build_continuation_prompt,
        build_planner_prompt,
        build_quoted_prompt,
        compose_quoted_answer,
        set_meter,
    )
    from ledger import (
        record_composer_call,
        record_size_estimate,
        session_total,
        today_total,
        day_total,
    )
    import ledger
    from picker import (
        build_picker_prompt,
        parse_picker_reply,
        pick_quote_selection,
    )
    from recall_more import (
        build_broaden_prompt,
        parse_broaden_reply,
        recall_more,
    )
    from page_resolver import install as install_page_resolver
    from dive import (
        BOOK_DATASETS,
        DIVE_MAX_RETRIEVAL_ROUNDS,
        DIVE_MAX_SUB_QUESTIONS,
        DIVE_SEARCH_TIMEOUT,
        DIVE_STARVED_PASSAGES,
        NEXT_TIER_URL,
        dive_recall,
        dive_retrieve,
        parse_evidence_sources,
        run_dive_round,
    )
    from research import (
        COMMAND_AUDIT,
        COMMAND_BRIEF,
        COMMAND_GATHER,
        COMMAND_SYNTHESIZE,
        CONVERSATIONAL_INTENTS,
        RESEARCH_BUSY_GLOBAL_DETAIL,
        RESEARCH_BUSY_PHONE_DETAIL,
        RESEARCH_EVIDENCE_FLOOR,
        RESEARCH_EVENT_ABORTED,
        RESEARCH_EVENT_BRIEF,
        RESEARCH_EVENT_CLASSIFYING,
        RESEARCH_EVENT_DONE,
        RESEARCH_EVENT_FAILED,
        RESEARCH_EVENT_PLANNING,
        RESEARCH_EVENT_SEARCHING,
        RESEARCH_EVENT_WRITING,
        RESEARCH_FAILED_DETAIL,
        RESEARCH_INTENTS,
        RESEARCH_MAX_CONCURRENT,
        RESEARCH_MODEL,
        RESEARCH_NO_EVIDENCE_DETAIL,
        RESEARCH_RECENT_SETTLED,
        RESEARCH_REGISTRY,
        RESEARCH_REGISTRY_LOCK,
        RESEARCH_SESSION_CAP_DETAIL,
        RESEARCH_SESSION_CLOSED_DETAIL,
        RESEARCH_SESSION_NOT_FOUND_DETAIL,
        RESEARCH_SESSION_TURN_CAP,
        RESEARCH_TURN_NOT_FOUND_DETAIL,
        TURN_TERMINAL_STATES,
        ResearchTurn,
        abort_phone_research,
        abort_research_turn,
        build_classify_prompt,
        build_conversational_prompt,
        build_subquestions_prompt,
        build_synthesis_prompt,
        classify_message,
        compose_guarded_reply,
        decide_proposal,
        ensure_session,
        find_turn,
        next_best_move,
        parse_classify_reply,
        plan_subquestions,
        record_claims,
        research_session_messages,
        research_session_state,
        research_state_summary,
        research_suggestions,
        run_research_turn,
        seed_evidence,
        start_research_turn,
        turn_status_payload,
        with_references,
    )

UI_DIR = Path(__file__).resolve().parent
# The Book set's PDFs and per-page text indexes (tools/build_page_index.py).
# Default: the repo's books/ beside ui/ in dev; compose bind-mounts it at
# /books in the container — the same relative place from /app/ui.
BOOKS_DIR = Path(
    os.environ.get("SESSION_BOOKS_DIR", str(UI_DIR.parent / "books"))
)
# Rendered page rasters (the visual pipeline). The books/ mount is
# read-only, so the cache lives outside it — /tmp in the container,
# the system temp beside it in dev.
RENDER_CACHE_DIR = Path(
    os.environ.get(
        "SESSION_RENDER_CACHE",
        str(Path(tempfile.gettempdir()) / "chat-books-render"),
    )
)
# PDFium is not provably thread-safe across documents; renders are
# serialized (one page takes a fraction of a second).
RENDER_LOCK = threading.Lock()
COGNEE_URL = os.environ.get("COGNEE_URL", "http://127.0.0.1:8000").rstrip("/")
HOST = os.environ.get("SESSION_UI_HOST", "127.0.0.1")
PORT = int(os.environ.get("SESSION_UI_PORT", "8765"))
PROXY_TIMEOUT = 600
# A Next-tier search runs four chain-of-thought rounds and can pass ten
# minutes (live logs, 2026-09-11) — past the shared 600s leash the relay
# once gave up on a search the second service had already finished.
# Phase 3 waits on its own leash; "unreachable: timed out" only after it.
NEXT_TIER_TIMEOUT = int(os.environ.get("NEXT_TIER_TIMEOUT", "1200"))
# The prepaid wiring (T23, GitLab #25): every ledger entry's cost leaves
# the paying Account's Balance through this one path — the capture sites
# never deduct by hand, so none can forget.
ledger.set_deductor(deduct_balance)

ALLOWED_PROXY = {"/health", "/api/v1/recall"}
NEXT_TIER_SEARCH_TYPE = "GRAPH_COMPLETION_COT"

# --- the Account gate (ADR-0013) ---------------------------------------------
# The login page replaced the honor-system phone gate: an Account — an
# email and a password issued by the Admin, never self-created — is the
# only door, and the phone number survives as legacy data attached to
# it, not an identity. Authentication is a stateless signed token in an
# HttpOnly cookie: the payload is base64(JSON{email, role, exp}) and
# the signature is hmac-sha256 over exactly those bytes with the server
# secret — no revocation list, no server-side session store, nothing
# for a handful of Admin-issued Accounts to outgrow.

# The cookie the sheet's browser carries; HttpOnly so the sheet's own
# JS can never read the token, Path=/ so every endpoint sees it.
AUTH_COOKIE = "cwb_auth"
# Twelve hours: a working day plus margin — the Session operator logs
# in once per sitting, not once per ask. Pinned in source, never env.
AUTH_TOKEN_TTL = 12 * 3600
AUTH_LOGIN_401_DETAIL = "برای ادامه وارد شوید."
AUTH_NO_PHONE_403_DETAIL = (
    "حساب شما به شماره‌ای پیوند نخورده است؛ "
    "از مدیر بخواهید شماره را پیوند بزند."
)
AUTH_NOT_ADMIN_403_DETAIL = "ساختن حساب فقط از دست مدیر برمی‌آید."
# The console's own refusal (T25): the mirror is the Admin's surface —
# a logged-in operator's cookie reaches the endpoint but not the page,
# and the Farsi note names whose door it is.
ADMIN_CONSOLE_403_DETAIL = "میز مدیریت فقط از دست مدیر برمی‌آید."
# How many audit rows the console shows (T25): a glance at the newest
# actions, not the archive — the log itself keeps everything.
ADMIN_AUDIT_ROWS = 20
AUTH_BAD_CREDENTIALS_DETAIL = "ایمیل یا گذرواژه نادرست است."
AUTH_BAD_LOGIN_BODY_DETAIL = "ایمیل و گذرواژه را بفرستید."
AUTH_LOGOUT_DETAIL = "خارج شدید."
AUTH_EMAIL_TAKEN_DETAIL = "این ایمیل پیش‌تر حساب گرفته است."
AUTH_BAD_ACCOUNT_BODY_DETAIL = (
    "ایمیل و گذرواژهٔ حساب را بفرستید (گذرواژه خالی نباشد)."
)

# The generated-once-per-process secret lives here; auth_secret() reads
# the env on every call so a test (or an operator) that pins
# AUTH_SECRET always wins over a cached value.
_GENERATED_AUTH_SECRET = None


def auth_secret() -> bytes:
    """The token-signing secret: AUTH_SECRET from the env when pinned,
    otherwise one random secret generated once per process — with a
    Farsi warning, because a per-process secret invalidates every
    outstanding login at restart and the operator must know that. The
    tests pin AUTH_SECRET so the client and server sign alike."""
    global _GENERATED_AUTH_SECRET
    pinned = os.environ.get("AUTH_SECRET", "")
    if pinned:
        return pinned.encode("utf-8")
    if _GENERATED_AUTH_SECRET is None:
        _GENERATED_AUTH_SECRET = secrets.token_hex(32)
        sys.stderr.write(
            "هشدار: AUTH_SECRET تنظیم نشده است؛ یک کلید موقت فقط برای "
            "همین اجرا ساخته شد — با هر بازراه‌اندازی همه باید دوباره "
            "وارد شوند. (ADR-0013)\n"
        )
    return _GENERATED_AUTH_SECRET.encode("utf-8")


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue_token(email: str, role: str, now: float = None) -> str:
    """One signed login token — payload.signature, both URL-safe. The
    expiry rides inside the signed payload so a client cannot extend
    it; `now` is the injected clock the tests pin (the TurnBudget
    pattern), never read from env."""
    payload = {
        "email": email,
        "role": role,
        "exp": int((time.time() if now is None else now) + AUTH_TOKEN_TTL),
    }
    body = _b64url_encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    )
    signature = hmac.new(
        auth_secret(), body.encode("ascii"), hashlib.sha256
    ).hexdigest()
    return f"{body}.{signature}"


def verify_token(token: str):
    """The signed payload {email, role}, or None. An unsigned, tampered,
    malformed, or expired token — and a signature that merely fails to
    compare — all refuse the same way: the caller answers one 401 and
    never distinguishes why."""
    if not isinstance(token, str) or token.count(".") != 1:
        return None
    body, _, signature = token.partition(".")
    expected = hmac.new(
        auth_secret(), body.encode("ascii"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(_b64url_decode(body))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)) or exp < time.time():
        return None
    email, role = payload.get("email"), payload.get("role")
    if not isinstance(email, str) or not email or not isinstance(role, str):
        return None
    return {"email": email, "role": role}


def resolve_identity(handler):
    """The gate flip (ADR-0013): the Account's attached phone, derived
    from the cwb_auth cookie — the phone-keyed stores' key, normalized
    exactly like normalize_phone — or None after answering the request.

    Every endpoint that once read the client-supplied phone header goes
    through here; the header no longer authenticates anything. An
    absent, invalid, expired, or tampered token (and a token whose
    Account no longer exists — a re-issued bootstrap) answers 401; a
    valid Account with no attached phone answers 403, because the
    phone-keyed stores (quotas, research sessions) have no key to
    address it by — the Admin must attach one. Neither answer ever
    crashes: the gate's rejections keep the drain-first shape so the
    response never dies to a reset."""
    claims = verify_token(handler._cookie_token())
    if claims is not None:
        account = account_by_email(claims["email"])
    else:
        account = None
    if account is None:
        handler._json_error(401, AUTH_LOGIN_401_DETAIL)
        return None
    phone = normalize_phone(account.get("phone") or "")
    if not phone:
        handler._json_error(403, AUTH_NO_PHONE_403_DETAIL)
        return None
    return phone


def resolve_account(handler):
    """The auth-level identity (no phone required): the Account row
    behind the cookie, or None without answering — /auth/me turns None
    into the 401, the Admin-only account creation turns it into the
    same 401 before its role check."""
    claims = verify_token(handler._cookie_token())
    if claims is None:
        return None
    return account_by_email(claims["email"])


def validated_datasets(raw):
    """The ask's Book selection (ADR-0010): the client's list
    intersected with the Book set, in the set's stable order — a name
    outside the Book set never reaches an upstream. None (missing,
    malformed, or nothing left) means the whole Book set, the shape
    every searcher already treats as the default."""
    if not isinstance(raw, list):
        return None
    picked = {item for item in raw if isinstance(item, str) and item in BOOK_DATASETS}
    if not picked:
        return None
    return [dataset for dataset in BOOK_DATASETS if dataset in picked]


class SessionHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(UI_DIR), **kwargs)

    def log_message(self, format, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))

    def end_headers(self) -> None:
        # The sheet is an evolving single page: every load must revalidate,
        # never render a stale cached copy after a deploy (a plain refresh
        # kept showing the pre-tab page after the 2026-09-11 switchover).
        # Immutable assets (page rasters) override the policy per response.
        self.send_header(
            "Cache-Control",
            getattr(self, "_cache_policy", None) or "no-cache",
        )
        super().end_headers()

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path.startswith("/books/") and "/page/" in path:
            self._book_page_image(path)
            return
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
        if path == "/auth/me":
            # The whoami read (ADR-0013): open to reach — an anonymous
            # call gets the 401 that shows the sheet's login overlay.
            self._auth_me()
            return
        if path == "/health":
            self._proxy("GET")
            return
        if path == "/research/turn":
            self._research_turn_status()
            return
        if path == "/research/state":
            self._research_state()
            return
        if path == "/research/messages":
            self._research_messages()
            return
        if path == "/research/report":
            self._research_report()
            return
        if path == "/usage/live":
            self._usage_live()
            return
        if path == "/admin":
            # The «میز مدیریت» (T25): the Admin's server-rendered
            # mirror of the system — a read, never a mutation.
            self._admin_console()
            return
        if path.startswith("/books/"):
            self._book_file(path)
            return
        super().do_GET()

    def _book_file(self, path: str) -> None:
        """One Book asset — ``/books/<dataset>/book`` (the PDF, for the
        reader), ``/books/<dataset>.pdf`` (the download form), or the
        ``.pages.json`` text index (the reader's provenance surface,
        ADR-0007). The dataset must be one of the Book set: the allowlist
        is the path-traversal guard, so no ``..`` or hash directory can
        ever reach the filesystem. No HTTP Range: the browser's PDF.js
        falls back to one full fetch, fine at the Books' 3–16 MB.

        The reader fetches the extension-less ``/book`` form on purpose:
        download managers (IDM among them) intercept requests whose URL
        ends in ``.pdf`` and answer the page with an empty takeover —
        the recorded 204 that left the reader dark (2026-09-13)."""
        book = re.fullmatch(r"/books/([A-Za-z0-9_-]+?)(?:/book|\.pdf)", path)
        index = re.fullmatch(r"/books/([A-Za-z0-9_-]+)\.pages\.json", path)
        if book:
            dataset, suffix, content_type = (
                book.group(1),
                "pdf",
                "application/pdf",
            )
        elif index:
            dataset, suffix, content_type = (
                index.group(1),
                "pages.json",
                "application/json; charset=utf-8",
            )
        else:
            dataset = None
        if not dataset or dataset not in BOOK_DATASETS:
            self._drain_request_body()
            self.send_error(404, "Not found")
            return
        file_path = BOOKS_DIR / f"{dataset}.{suffix}"
        try:
            total_size = file_path.stat().st_size
        except OSError:
            self._drain_request_body()
            self.send_error(404, "Not found")
            return

        range_header = self.headers.get("Range")
        if range_header and range_header.startswith("bytes="):
            try:
                range_val = range_header[6:].strip()
                if "-" in range_val:
                    part_start, part_end = range_val.split("-", 1)
                    if not part_start:
                        length = int(part_end)
                        start = max(0, total_size - length)
                        end = total_size - 1
                    else:
                        start = int(part_start)
                        end = int(part_end) if part_end else total_size - 1
                else:
                    start = int(range_val)
                    end = total_size - 1

                if start >= total_size or start > end:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{total_size}")
                    self.end_headers()
                    return

                end = min(end, total_size - 1)
                chunk_len = end - start + 1

                self.send_response(206)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Range", f"bytes {start}-{end}/{total_size}")
                self.send_header("Content-Length", str(chunk_len))
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()

                with file_path.open("rb") as f:
                    f.seek(start)
                    remaining = chunk_len
                    while remaining > 0:
                        chunk = f.read(min(remaining, 64 * 1024))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
                return
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception:
                pass

        try:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(total_size))
            self.send_header("Accept-Ranges", "bytes")
            if suffix == "pdf" and path.endswith(".pdf"):
                self.send_header(
                    "Content-Disposition",
                    f'attachment; filename="{dataset}.pdf"',
                )
            self.end_headers()

            with file_path.open("rb") as f:
                while chunk := f.read(64 * 1024):
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            return

    _PAGE_WIDTH_RE = re.compile(r"/books/([A-Za-z0-9_-]+)/page/(\d{1,4})\.png$")

    def _book_page_image(self, path: str) -> None:
        """One Book page as a PDFium raster — ``/books/<d>/page/<n>.png?w=``.

        The visual pipeline (ADR-0007 rendering addendum): these Persian
        Books' subset fonts defeat pdf.js's canvas, so the pixel truth is
        produced server-side by a real PDF engine and the browser only
        overlays transparent text geometry for selection and highlights.
        Renders are cached on disk keyed by dataset, page, and width
        bucket; the w parameter is clamped and bucketed to bound the
        cache."""
        match = self._PAGE_WIDTH_RE.fullmatch(path)
        query = parse_qs(urlparse(self.path).query)
        if not match or match.group(1) not in BOOK_DATASETS:
            self._drain_request_body()
            self.send_error(404, "Not found")
            return
        dataset, page_number = match.group(1), int(match.group(2))
        if page_number < 1:
            self._drain_request_body()
            self.send_error(404, "Not found")
            return
        try:
            width = int((query.get("w") or ["1200"])[0])
        except ValueError:
            width = 1200
        width = min(2400, max(480, round(width / 160) * 160))
        if _pdfium is None:
            self._send_json(
                503,
                {
                    "detail": "موتور رندر PDF روی سرور نصب نیست؛ "
                    "ظرف session را با pip install pypdfium2 بازسازی کنید."
                },
            )
            return
        cache_key = f"{dataset}-p{page_number}-w{width}.png"
        cache_file = RENDER_CACHE_DIR / cache_key
        try:
            body = cache_file.read_bytes()
        except OSError:
            body = self._render_book_page(dataset, page_number, width, cache_file)
            if body is None:
                self._drain_request_body()
                self.send_error(404, "Not found")
                return
        self._cache_policy = "max-age=604800, immutable"
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _render_book_page(dataset, page_number, width, cache_file):
        """One page raster, cached atomically; None when the page does
        not exist or the PDF is unreadable."""
        source = BOOKS_DIR / f"{dataset}.pdf"
        if not source.exists():
            return None
        RENDER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with RENDER_LOCK:
            try:
                body = cache_file.read_bytes()
            except OSError:
                body = None
            if body is not None:
                return body
            try:
                pdf = _pdfium.PdfDocument(str(source))
                try:
                    if page_number > len(pdf):
                        return None
                    page = pdf[page_number - 1]
                    bitmap = page.render(scale=width / page.get_width())
                    image = bitmap.to_pil()
                finally:
                    pdf.close()
                tmp = cache_file.with_suffix(f".tmp{threading.get_ident()}")
                image.save(tmp, format="PNG")
                os.replace(tmp, cache_file)
                return cache_file.read_bytes()
            except Exception:
                sys.stderr.write(
                    f"render failed: {dataset} p{page_number}\n"
                )
                return None

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

    def _send_json(self, status: int, payload: dict, extra_headers=()) -> None:
        """One JSON reply. The caller must have consumed the request body
        already (or never had one) — unlike _json_error this does not
        drain, so a second read cannot block on bytes already taken.
        extra_headers rides the same header block (the login's
        Set-Cookie); the body stays untouched."""
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for name, value in extra_headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _cookie_token(self) -> str:
        """The cwb_auth token out of the Cookie header — '' when absent.
        The only credential the gate reads: the client-supplied phone
        header is retired (ADR-0013), and the cookie is HttpOnly, so
        the sheet's JS cannot forge or read it either."""
        for part in self.headers.get("Cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == AUTH_COOKIE:
                return value
        return ""

    def _auth_cookie_header(self, token: str) -> tuple:
        """The Set-Cookie pair that logs an Account in: HttpOnly (the
        sheet's JS never holds the token), SameSite=Lax (cross-site
        posts cannot ride it), Max-Age matching the token's own signed
        expiry so the browser and the payload agree."""
        return (
            "Set-Cookie",
            f"{AUTH_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; "
            f"Max-Age={AUTH_TOKEN_TTL}",
        )

    _CLEAR_COOKIE = (
        "Set-Cookie",
        f"{AUTH_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0",
    )

    def _balance_gate(self, phone: str) -> bool:
        """The prepaid stop (T23, GitLab #25): an Account whose Balance
        (اعتبار) is spent answers 402 with the Farsi fix — the ask and
        the phases each pay for themselves before they run, and a turn
        that cannot be paid for never starts. A turn or phase already
        running finishes; only the NEXT spend is stopped. The daily
        quota still applies on top of the Balance, never instead."""
        if get_balance(phone) <= 0:
            self._json_error(
                402,
                "اعتبار این حساب تمام شده است؛ از مدیر بخواهید اعتبار را شارژ کند.",
            )
            return False
        return True

    def _gate_phone(self):
        """The ask gate (ADR-0013): the Account's attached phone —
        resolved from the login cookie, never from a client header —
        with chats left today; records the chat. The identity resolver
        answers 401/403 itself and returns None when rejected; the
        quota still bounds per Account through the attached phone,
        which is exactly the store key the quota DB has always had."""
        phone = resolve_identity(self)
        if phone is None:
            return None
        if not self._balance_gate(phone):
            return None
        if chats_today(phone) >= DAILY_CHAT_LIMIT:
            self._json_error(
                429, "شمار گفتگوهای امروز این شماره پر شده است؛ فردا بیایید."
            )
            return None
        record_chat(phone)
        return phone

    def _quoted_phone(self):
        """The phase-2 gate: an Account whose attached phone has at
        least one chat today (the quoted answer belongs to a chat that
        already started)."""
        phone = resolve_identity(self)
        if phone is None:
            return None
        if not self._balance_gate(phone):
            return None
        if chats_today(phone) < 1:
            self._json_error(
                429, "پاسخ استنادی بخشی از همان گفتگو است؛ اول یک پرسش بپرسید."
            )
            return None
        return phone

    # --- the Account endpoints (ADR-0013) -----------------------------------
    # Login is the one door; /auth/logout and /auth/me serve the sheet's
    # overlay; /auth/accounts is the Admin's issuance. All four stay open
    # to REACH (no token needed to be told 401) — the gate itself lives in
    # resolve_identity.

    def _auth_login(self) -> None:
        """The one door: email + password in, the signed HttpOnly cookie
        out — plus the whoami body the sheet reloads against. An unknown
        email and a wrong password answer the identical 401: the
        difference is never an attacker's to read."""
        length = int(self.headers.get("Content-Length", "0") or "0")
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            email = payload["email"]
            password = payload["password"]
            if not isinstance(email, str) or not isinstance(password, str):
                raise ValueError("email and password are required")
        except (ValueError, KeyError, TypeError):
            self._send_json(400, {"detail": AUTH_BAD_LOGIN_BODY_DETAIL})
            return
        account = verify_login(email, password)
        if account is None:
            self._send_json(401, {"detail": AUTH_BAD_CREDENTIALS_DETAIL})
            return
        token = issue_token(account["email"], account["role"])
        self._send_json(
            200,
            {"email": account["email"], "role": account["role"]},
            extra_headers=[self._auth_cookie_header(token)],
        )

    def _auth_logout(self) -> None:
        """Clear the cookie client-side. The token is stateless, so the
        server has nothing to revoke — its signed expiry ends it."""
        self._send_json(
            200, {"detail": AUTH_LOGOUT_DETAIL}, extra_headers=[self._CLEAR_COOKIE]
        )

    def _auth_me(self) -> None:
        """The whoami read: the Account behind the cookie, or the same
        401 every gated endpoint answers — one shape, so the sheet's
        overlay hook treats it one way."""
        account = resolve_account(self)
        if account is None:
            self._json_error(401, AUTH_LOGIN_401_DETAIL)
            return
        self._send_json(
            200,
            {
                "email": account["email"],
                "role": account["role"],
                "phone": account["phone"],
            },
        )

    def _auth_create_account(self) -> None:
        """The Admin issues an operator Account (ADR-0013) — the only
        creation path that exists, no signup page beside it. Anonymous
        gets the standard 401; a logged-in operator gets 403, because
        issuing Accounts is the Admin's act alone. The optional phone is
        the legacy attachment the quota and research stores key by."""
        account = resolve_account(self)
        if account is None:
            self._json_error(401, AUTH_LOGIN_401_DETAIL)
            return
        if account["role"] != "admin":
            self._json_error(403, AUTH_NOT_ADMIN_403_DETAIL)
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            email = payload["email"]
            password = payload["password"]
            phone = payload.get("phone")
            if not isinstance(email, str) or not email.strip():
                raise ValueError("email is required")
            if not isinstance(password, str) or not password:
                raise ValueError("password is required")
            if phone is not None and not isinstance(phone, str):
                raise ValueError("phone must be a string")
        except (ValueError, KeyError, TypeError):
            # The body is already read above, so _send_json is safe —
            # _json_error would drain a second time and block.
            self._send_json(400, {"detail": AUTH_BAD_ACCOUNT_BODY_DETAIL})
            return
        if phone:
            phone = normalize_phone(phone)
            if not phone:
                self._send_json(
                    400, {"detail": "شمارهٔ تلفن همراه را وارد کنید."}
                )
                return
        else:
            phone = None
        created = create_account(email, password, phone=phone, role="operator")
        if created is None:
            self._send_json(409, {"detail": AUTH_EMAIL_TAKEN_DETAIL})
            return
        # The audit's first wired action (ADR-0013, T25): an issuance is
        # the Admin's act and lands in the append-only log — the issuer
        # and the issued, appended only after the creation truly
        # succeeded. A refused creation (the 409 above, the bad body
        # before it) logs nothing: the log records what HAPPENED, never
        # what was attempted. A broken audit store must not un-issue
        # the Account either — the failure stays loud on stderr, never
        # silent, and the issuance stands.
        try:
            audit.append(
                audit.ACCOUNT_CREATED,
                actor_email=account["email"],
                detail={"email": created["email"], "phone": created["phone"]},
            )
        except Exception as exc:
            sys.stderr.write(
                f"audit append failed for account_created "
                f"{created['email']}: {exc!r}\n"
            )
        self._send_json(
            200,
            {
                "email": created["email"],
                "role": created["role"],
                "phone": created["phone"],
            },
        )

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path == "/auth/login":
            # The one door (ADR-0013): email + password in, the
            # HttpOnly cookie out. Open by design — there is no other
            # way in.
            self._auth_login()
            return
        if path == "/auth/logout":
            self._auth_logout()
            return
        if path == "/auth/accounts":
            self._auth_create_account()
            return
        if path == "/api/v1/recall":
            phone = self._gate_phone()
            if phone is None:
                return
            # A new ask owns the sheet (issue #26): after the gate has
            # recorded the chat, the phone's in-flight research turns
            # abort — cooperatively; the worker exits at its next
            # boundary — and their sessions close, so the new ask's
            # Research Mode starts from a fresh investigation. Another
            # phone's research is never touched.
            abort_phone_research(phone)
            # The ask's own entry (T22, GitLab #24): the gate knows the
            # request's size before the relay streams the answer — an
            # input-side estimate, marked estimated like every
            # non-metered entry. The phases' composer calls ride the
            # thread's usage tap and land metered or estimated by the
            # reply's own honesty.
            record_size_estimate(
                phone, "ask", int(self.headers.get("Content-Length", "0") or "0")
            )
            self._proxy("POST")
            return
        if path == "/quoted-answer":
            phone = self._quoted_phone()
            if phone is None:
                return
            # The planner and the writer both ride this thread's composer
            # calls — each upstream call lands its own ledger entry.
            set_meter(
                lambda prompt, reply: record_composer_call(
                    phone, "writer", prompt, reply
                )
            )
            try:
                self._quoted_answer()
            finally:
                set_meter(None)
            return
        if path == "/quote-selection":
            phone = self._quoted_phone()
            if phone is None:
                return
            set_meter(
                lambda prompt, reply: record_composer_call(
                    phone, "picker", prompt, reply
                )
            )
            try:
                self._quote_selection()
            finally:
                set_meter(None)
            return
        if path == "/recall-more":
            phone = self._quoted_phone()
            if phone is None:
                return
            set_meter(
                lambda prompt, reply: record_composer_call(
                    phone, "composer", prompt, reply
                )
            )
            try:
                self._recall_more()
            finally:
                set_meter(None)
            return
        if path == "/evidence-fallback":
            if self._quoted_phone() is None:
                return
            self._evidence_fallback()
            return
        if path == "/research/message":
            phone = self._quoted_phone()
            if phone is None:
                return
            self._research_message(phone)
            return
        if path == "/research/decide":
            phone = self._quoted_phone()
            if phone is None:
                return
            self._research_decide(phone)
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

    def _evidence_fallback(self) -> None:
        """The phase-1 citation fallback (ADR-0011): the first message's
        role is to FIND CITATIONS — when the streamed reply came back
        without an Evidence block, ONE pinned reference-on search over
        the question (the dive kernel's searcher, the ask's selected
        Books) fetches the pool directly so the Quote selection and
        phase 2 still run. The gate is the ask's own shape (a phone
        with a chat today; the ask already recorded it — this never
        counts another). A malformed body answers 400; a searcher that
        finds nothing answers 200 {"sources": []} — the honest empty
        the sheet's no-citation note consumes, never a 5xx."""
        length = int(self.headers.get("Content-Length", "0") or "0")
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            question = payload["question"]
            if not isinstance(question, str) or not question.strip():
                raise ValueError("question is required")
            datasets = validated_datasets(payload.get("datasets"))
        except (ValueError, KeyError, TypeError):
            # The body is already read above, so _send_json is safe.
            self._send_json(400, {"detail": "پرسش را بفرستید."})
            return
        sources = dive_recall(question.strip(), datasets)
        self._send_json(200, {"sources": sources})

    def _recall_more(self) -> None:
        """The «جست‌وجوی بیشتر» operation (ADR-0010): one broaden call
        reasons out the question's not-yet-covered facets, the dive
        kernel's pinned searchers run them on the second service, and
        the pool's NEW passages ride back — the sheet merges them,
        re-picks the Quote selection, and re-runs phase 2. The gate is
        phase 2's shape (belongs to a chat that already started, never
        counts one). The body carries the ask's current pool so the
        merge can return only fresh passages, and the selected Books'
        datasets (validated against the Book set; empty means both). A
        malformed body answers 400 before any upstream call; a broaden
        that finds nothing answers 200 {"sources": []} — the honest
        empty the sheet's no-joy note consumes, never a 5xx."""
        length = int(self.headers.get("Content-Length", "0") or "0")
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            question = payload["question"]
            sources = [
                {
                    "reference": source["reference"],
                    "passage": source["passage"],
                }
                for source in payload.get("sources", [])
                if isinstance(source, dict)
                and isinstance(source.get("reference"), str)
                and isinstance(source.get("passage"), str)
            ]
            datasets = validated_datasets(payload.get("datasets"))
            if not isinstance(question, str) or not question.strip():
                raise ValueError("question is required")
        except (ValueError, KeyError, TypeError):
            # The body is already read above, so _send_json is safe —
            # _json_error would drain a second time and block.
            self._send_json(400, {"detail": "پرسش و شواهد فعلی را بفرستید."})
            return
        fresh = recall_more(question.strip(), sources, datasets)
        body = json.dumps({"sources": fresh}, ensure_ascii=False).encode(
            "utf-8"
        )
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

    def _research_turn_status(self) -> None:
        """The turn poll surface: the sheet asks for a turn's state,
        Farsi events, and elapsed seconds — and, when the turn settled,
        its outcome. `done` carries the reply payload ({"reply",
        "suggestions", "state"}), `failed` a short Farsi detail, and an
        unknown id (a restart emptied the registry, or a foreign phone)
        answers 404 — the recorded failure surface, never a hang. The
        phone must match the turn's: one Account's poll never reads
        another's research. The identity comes from the login cookie
        (ADR-0013): no valid Account, no poll — 401."""
        phone = resolve_identity(self)
        if phone is None:
            return
        query = parse_qs(urlparse(self.path).query)
        turn_id = (query.get("turn") or [""])[0]
        payload = None
        turn = find_turn(turn_id)
        if turn is not None and turn.phone == phone:
            payload = turn_status_payload(turn)
        if payload is None:
            self._json_error(404, RESEARCH_TURN_NOT_FOUND_DETAIL)
            return
        self._send_json(200, payload)

    def _research_state(self) -> None:
        """The state panel's read: one session's summary projection —
        the research question and its version count, scope, evidence and
        claim counts, gaps, pending proposals — phone matched, straight
        from the SQLite store, never from the in-memory registry. The
        chip set rides beside the summary under ``suggestions`` so a
        refresh re-renders the skip with the map (T10, GitLab #11).
        The identity comes from the login cookie (ADR-0013)."""
        phone = resolve_identity(self)
        if phone is None:
            return
        query = parse_qs(urlparse(self.path).query)
        session_id = (query.get("session") or [""])[0]
        if not session_id:
            self._json_error(404, RESEARCH_SESSION_NOT_FOUND_DETAIL)
            return
        payload, error = research_session_state(phone, session_id)
        if payload is None:
            self._json_error(error[0], error[1])
            return
        self._send_json(200, payload)

    def _research_messages(self) -> None:
        """The transcript read (ADR-0011): one session's messages in
        order, phone matched — a browser refresh re-fetches what was
        said instead of an empty chat. No LLM, no research side
        effects. The identity comes from the login cookie (ADR-0013):
        no valid Account, no transcript — 401."""
        phone = resolve_identity(self)
        if phone is None:
            return
        query = parse_qs(urlparse(self.path).query)
        session_id = (query.get("session") or [""])[0]
        payload, error = research_session_messages(phone, session_id)
        if payload is None:
            self._send_json(error[0], {"detail": error[1]})
            return
        self._send_json(200, payload)

    def _usage_live(self) -> None:
        """The sheet header's live read (T22, GitLab #24): the open
        Session's running Toman total — everything since this Account's
        newest `ask` entry inclusive — plus the server-local day's
        spend. The tariff is config's business; this endpoint only
        reports what the ledger already recorded."""
        phone = resolve_identity(self)
        if phone is None:
            return
        self._send_json(
            200,
            {
                "session_toman": session_total(phone),
                "today_toman": today_total(phone),
            },
        )

    def _admin_console(self) -> None:
        """The «میز مدیریت» read (T25, GitLab #26): the Admin's
        server-rendered mirror of the system — every Account with its
        Balance (اعتبار), the day's spend off the ledger, the day's
        chats against the daily limit, the live research turns and the
        recently settled ones with their failures called out, and the
        audit log's newest rows. One HTML document, no JS dependency —
        the console reads, it does not run the system from the browser.

        The gate is /auth/accounts' shape exactly (resolve_account plus
        the role check), and deliberately NOT resolve_identity: the
        Admin's own Account carries no attached phone — it issues
        Accounts, it does not chat — so resolve_identity's
        attach-a-phone 403 would lock the Admin out of its own console
        before the role ever ran. Anonymous still gets the standard
        401; a logged-in operator gets the console's own 403.

        Every read here stays a read: the registry snapshot copies the
        live turns and the recent-settled ring under
        RESEARCH_REGISTRY_LOCK and never writes back, the ledger and
        the quota store answer their per-phone sums, and the audit read
        is a plain SELECT against the append-only table. The console
        never mutates silently — this round it never mutates at all
        (the issuance and top-up write paths are T26, GitLab #28)."""
        account = resolve_account(self)
        if account is None:
            self._json_error(401, AUTH_LOGIN_401_DETAIL)
            return
        if account["role"] != "admin":
            self._json_error(403, ADMIN_CONSOLE_403_DETAIL)
            return
        yesterday = time.strftime(
            "%Y-%m-%d",
            time.localtime(time.time() - 24 * 60 * 60),
        )
        accounts_rows = []
        for row in list_accounts():
            phone = normalize_phone(row.get("phone") or "")
            accounts_rows.append(
                {
                    "email": row["email"],
                    "role": row["role"],
                    "phone": phone,
                    "balance_toman": row["balance_toman"],
                    # The ledger and the quota key by the attached
                    # phone; an Account with none attached (the Admin
                    # itself) reads as zero spend and zero chats —
                    # honest, not hidden.
                    "yesterday_spend_toman": day_total(phone, yesterday)
                    if phone
                    else 0,
                    "today_spend_toman": today_total(phone) if phone else 0,
                    "chats_today": chats_today(phone) if phone else 0,
                }
            )
        now_mono = time.monotonic()
        with RESEARCH_REGISTRY_LOCK:
            live_turns = [
                {
                    "id": turn.id,
                    "phone": turn.phone,
                    "state": turn.state,
                    "elapsed": round(now_mono - turn.started_at, 1),
                }
                for turn in RESEARCH_REGISTRY.values()
            ]
            # The ring is appended oldest-first as turns settle; the
            # page renders newest first, so the snapshot hands it over
            # reversed. list() copies before the lock lets go.
            settled_turns = [
                {
                    "id": turn.id,
                    "phone": turn.phone,
                    "state": turn.state,
                    "error": turn.error,
                }
                for turn in reversed(list(RESEARCH_RECENT_SETTLED))
            ]
        body = console_html(
            accounts_rows,
            live_turns,
            settled_turns,
            audit.recent(ADMIN_AUDIT_ROWS),
            quota_limit=DAILY_CHAT_LIMIT,
            generated=time.strftime("%Y-%m-%d %H:%M"),
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _research_report(self) -> None:
        """The Session report's read (T18, GitLab #19): one session's
        walk-away artifact — question, destination, map summary, the
        standing Brief with its quotes and pages, and the server-built
        «منابع», as one self-contained RTL HTML document. Phone matched;
        a CLOSED session still answers (a finished Session's report is
        the deliverable); no Brief sections yet is the Farsi refusal.
        The chat transcript never enters it. The identity comes from
        the login cookie (ADR-0013): no valid Account, no report — 401.

        `format=md` (T19, GitLab #22) serves the Markdown twin of the
        Brief's body instead: `text/markdown; charset=utf-8` as a
        DOWNLOAD attachment named for the artifact — text reuse wants a
        file, not a page — while the default html stays inline for the
        sheet's chip and the print-to-PDF path. Anything but "md" reads
        as the default html."""
        phone = resolve_identity(self)
        if phone is None:
            return
        query = parse_qs(urlparse(self.path).query)
        session_id = (query.get("session") or [""])[0]
        fmt = (query.get("format") or ["html"])[0]
        document, error = research_session_report(phone, session_id, fmt=fmt)
        if document is None:
            self._send_json(error[0], {"detail": error[1]})
            return
        if fmt == "md":
            content_type = "text/markdown; charset=utf-8"
            # The attachment name carries the session's own id; only the
            # id's safe characters ride, so the header can never be
            # shaped by the query string.
            safe_session = re.sub(r"[^A-Za-z0-9._-]", "", session_id)
            disposition = f'attachment; filename="gonzarsh-seshat-{safe_session}.md"'
        else:
            content_type = "text/html; charset=utf-8"
            disposition = None
        body = document.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        if disposition:
            self.send_header("Content-Disposition", disposition)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _research_message(self, phone: str) -> None:
        """The Research Mode message start (ADR-0008): the gate is the
        phase-2 shape exactly — the research conversation belongs to the
        chat phase 1 recorded, so it needs a phone with at least one
        chat today and never counts or checks the limit. The creating
        call carries the ask's question and its phase-1 Evidence pool
        (the session's founding goal and evidence); later calls carry
        only the text. The turn runs on its own registry job and this
        handler answers the turn identity immediately: 202 {"turn_id",
        "session_id"} — the sheet polls /research/turn for state,
        events, and the reply. Nothing is ever queued: a busy phone (or
        a full registry) is rejected, not deferred."""
        length = int(self.headers.get("Content-Length", "0") or "0")
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            text = payload["text"]
            if not isinstance(text, str) or not text.strip():
                raise ValueError("text is required")
            session_id = payload.get("session_id")
            if session_id is not None and not isinstance(session_id, str):
                raise ValueError("session_id must be a string")
            question = payload.get("question")
            if not session_id and (
                not isinstance(question, str) or not question.strip()
            ):
                raise ValueError("question is required to start a session")
            sources = payload.get("sources")
            if sources is not None and not isinstance(sources, list):
                raise ValueError("sources must be a list")
            datasets = validated_datasets(payload.get("datasets"))
        except (ValueError, KeyError, TypeError):
            # The body is already read above, so _send_json is safe —
            # _json_error would drain a second time and block.
            self._send_json(400, {"detail": "پیام پژوهش را بفرستید."})
            return
        session, error = ensure_session(
            phone, session_id, text.strip(), question, sources, datasets
        )
        if session is None:
            self._send_json(error[0], {"detail": error[1]})
            return
        turn, busy_detail = start_research_turn(phone, session, text.strip())
        if turn is None:
            self._send_json(429, {"detail": busy_detail})
            return
        self._send_json(202, {"turn_id": turn.id, "session_id": session["id"]})

    def _research_decide(self, phone: str) -> None:
        """The checkpoint resolution: one pending proposal applied or
        dropped — the only path a research question or scope change
        lands through. Synchronous (no LLM, no registry job): the
        decision is bookkeeping, and its answer carries the updated
        summary and chip set the same shape a settled turn does."""
        length = int(self.headers.get("Content-Length", "0") or "0")
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            session_id = payload["session_id"]
            proposal_id = payload["proposal_id"]
            accept = payload["accept"]
            # The adjustment checkpoint (T6) decides a choice, not a
            # yes/no — the chosen adjustment rides along when present.
            choice = payload.get("choice")
            if not isinstance(session_id, str) or not session_id.strip():
                raise ValueError("session_id is required")
            if not isinstance(proposal_id, str) or not proposal_id.strip():
                raise ValueError("proposal_id is required")
            if not isinstance(accept, bool):
                raise ValueError("accept must be a boolean")
            if choice is not None and not isinstance(choice, str):
                raise ValueError("choice must be a string")
        except (ValueError, KeyError, TypeError):
            self._send_json(400, {"detail": "تصمیم پیشنهاد را بفرستید."})
            return
        result, error = decide_proposal(
            phone,
            session_id.strip(),
            proposal_id.strip(),
            accept,
            choice=(choice or None),
        )
        if result is None:
            self._send_json(error[0], {"detail": error[1]})
            return
        self._send_json(200, result)

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
        if method == "POST" and path == "/api/v1/recall":
            # The recall body is validated, not forwarded blind
            # (ADR-0010): the query is required and the datasets are the
            # ask's Book selection intersected with the Book set — a
            # browser never names an upstream dataset outside it.
            patched = self._validated_recall_body(body)
            if patched is None:
                return
            body = patched
            headers = {"Content-Type": "application/json; charset=utf-8"}
            request = Request(
                f"{COGNEE_URL}{path}",
                data=body,
                headers=headers,
                method=method,
            )
            self._relay(request, "cognee")
            return
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

    def _validated_recall_body(self, raw: bytes):
        """The recall POST's patched body bytes, or None after answering
        400. The body is already read here, so rejections use _send_json
        (never the draining _json_error — a second read would block)."""
        try:
            payload = json.loads(raw or b"{}")
            query = payload["query"]
            if not isinstance(query, str) or not query.strip():
                raise ValueError("query is required")
        except (ValueError, KeyError, TypeError):
            self._send_json(400, {"detail": "پرسش را بنویسید."})
            return None
        datasets = validated_datasets(payload.get("datasets"))
        if datasets is not None:
            payload["datasets"] = datasets
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")


# The embedder is frozen into the stored vectors (ADR-0004). The 2026-09-12
# VPS incident: a recreated stack whose .env drifted to the wrong pins —
# health stayed green and every search died. Present-but-wrong pins refuse
# the start here, loudly; absent pins (a bare dev run outside compose)
# cannot contradict the data and pass.
FROZEN_EMBEDDING_MODEL = "text-embedding-3-large"
FROZEN_EMBEDDING_DIMENSIONS = "3072"


def check_embedding_pin() -> None:
    """The drift guard (T17, GitLab #18): refuse a start whose embedding
    config contradicts the frozen vectors — a crash with the Farsi operator
    fix, never green-but-broken."""
    drifted = []
    for name, frozen in (
        ("EMBEDDING_MODEL", FROZEN_EMBEDDING_MODEL),
        ("EMBEDDING_DIMENSIONS", FROZEN_EMBEDDING_DIMENSIONS),
    ):
        value = os.environ.get(name)
        if value is not None and value != frozen:
            drifted.append(f"{name}={value} (ثابت: {frozen})")
    if drifted:
        sys.exit(
            "پیکربندی امبدینگ با بردارهای ذخیره‌شده نمی‌خواند: "
            + " ، ".join(drifted)
            + " — سرویس اجرا نمی‌شود. مقدارهای ثابت را در .env بگذارید: "
            + f"EMBEDDING_MODEL={FROZEN_EMBEDDING_MODEL} ، EMBEDDING_DIMENSIONS={FROZEN_EMBEDDING_DIMENSIONS} (ADR-0004)."
        )


def main() -> None:
    # The drift guard runs before anything binds: a drifted stack must not
    # come up even briefly.
    check_embedding_pin()
    # The true-page resolver (ADR-0011): kept quotes' labels name the
    # passage's actual page, not the locator's drifted estimate.
    install_page_resolver()
    server = ThreadingHTTPServer((HOST, PORT), SessionHandler)
    print(f"Session sheet http://{HOST}:{PORT}", flush=True)
    print(f"Proxying /api/v1/recall and /health to {COGNEE_URL}", flush=True)
    print(f"Relaying /next-tier-recall to {NEXT_TIER_URL}", flush=True)
    print(f"Running Research Mode turns against {NEXT_TIER_URL}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
