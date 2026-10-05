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
import datetime
import gzip
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sys
import time
import threading
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
        _fuzzy_window,
        _stream_with_offsets,
        book_label,
        display_text,
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
        admins_exist,
        attach_phone,
        create_account,
        credit_balance,
        deduct_balance,
        ensure_admin,
        get_balance,
        list_accounts,
        set_research_enabled,
        verify_login,
    )
    from ui import audit, migrate
    from ui.console import console_html
    from ui.composer import (
        COMPOSER_MAX_TOKENS,
        COMPOSER_TIMEOUT,
        build_continuation_prompt,
        build_planner_prompt,
        build_quoted_prompt,
        compose_quoted_answer,
        rewrite_followup_query,
        set_meter,
    )
    from ui.ledger import (
        record_composer_call,
        record_size_estimate,
        session_history,
        session_total,
        today_total,
        day_total,
    )
    from ui import ledger
    from ui import session_store
    from ui import note_store
    from ui import ticket_store
    from ui.tickets_admin import (
        admin_inbox_html,
        admin_ticket_html,
        admin_transcript_html,
    )
    from ui import research_store
    from ui.picker import (
        QUOTE_SELECTION_FLOOR,
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
    from ui import chat_store
    from ui.chat_store import latest_chat
    from ui.ask import ask_pool
    from ui.research import (
        COMMAND_AUDIT,
        COMMAND_BRIEF,
        COMMAND_GATHER,
        COMMAND_SYNTHESIZE,
        CONVERSATIONAL_INTENTS,
        RESEARCH_BUSY_GLOBAL_DETAIL,
        RESEARCH_BUSY_ACCOUNT_DETAIL,
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
        pause_account_research,
        abort_research_turn,
        decide_plan_item,
        resume_closed_session,
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
        _fuzzy_window,
        _stream_with_offsets,
        book_label,
        display_text,
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
        admins_exist,
        attach_phone,
        create_account,
        credit_balance,
        deduct_balance,
        ensure_admin,
        get_balance,
        list_accounts,
        set_research_enabled,
        verify_login,
    )
    import audit, migrate
    from console import console_html
    from report import research_session_report
    from ask import ask_pool
    from chat_store import latest_chat
    import chat_store
    import ticket_store
    from tickets_admin import (
        admin_inbox_html,
        admin_ticket_html,
        admin_transcript_html,
    )
    from composer import (
        COMPOSER_MAX_TOKENS,
        COMPOSER_TIMEOUT,
        build_continuation_prompt,
        build_planner_prompt,
        build_quoted_prompt,
        compose_quoted_answer,
        rewrite_followup_query,
        set_meter,
    )
    from ledger import (
        record_composer_call,
        record_size_estimate,
        session_history,
        session_total,
        today_total,
        day_total,
    )
    import ledger
    import session_store
    import note_store
    import research_store
    from picker import (
        QUOTE_SELECTION_FLOOR,
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
        RESEARCH_BUSY_ACCOUNT_DETAIL,
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
        pause_account_research,
        abort_research_turn,
        decide_plan_item,
        resume_closed_session,
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
# read-only, so the cache lives outside it. Default: a `render-cache/`
# beside the books dir — persistent across restarts and deploys, unlike
# the old /tmp default that turned every container rebuild into a fully
# cold reader (ADR-0017); compose pins the volume explicitly.
RENDER_CACHE_DIR = Path(
    os.environ.get("SESSION_RENDER_CACHE", str(BOOKS_DIR.parent / "render-cache"))
)
# The cache's ceiling (ADR-0017): width buckets × pages can outgrow a
# small disk, so once the total passes the cap the oldest rasters go
# first. Overridable for tests and tight volumes.
RENDER_CACHE_CAP = int(os.environ.get("SESSION_RENDER_CACHE_CAP", str(3 << 30)))
# PDFium is not provably thread-safe across documents; renders are
# serialized (one page takes a fraction of a second).
RENDER_LOCK = threading.Lock()
# The open-document reuse (ADR-0017): a raster cache miss used to pay a
# full PdfDocument parse of the whole file on EVERY page — under the
# global lock, so every waiting client waited too. The parsed document
# handles live here, newest-used wins, closed on eviction; reuse stays
# inside RENDER_LOCK so the thread-safety argument above holds unchanged.
PDF_DOCS_CACHE: dict[str, object] = {}
PDF_DOCS_CACHE_CAP = 2
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
AUTH_NOT_ADMIN_403_DETAIL = "ساختن حساب فقط از دست مدیر برمی‌آید."
# The guest cut (2026-10-04): an Account the Admin issued without
# research — the Balance still gates the ask, but the research door
# answers 403 under its own name, so a guest never mistakes a closed
# door for a spent one.
RESEARCH_DISABLED_403_DETAIL = (
    "حالت پژوهش برای این حساب فعال نیست؛ از مدیر بخواهید فعالش کند."
)
# The console's own refusal (T25): the mirror is the Admin's surface —
# a logged-in operator's cookie reaches the endpoint but not the page,
# and the Farsi note names whose door it is.
ADMIN_CONSOLE_403_DETAIL = "میز مدیریت فقط از دست مدیر برمی‌آید."
# The ticket system's Farsi answers (the ticket-system map, 2026-10-05;
# the sheet mirrors its half — these are the SERVER's words). The
# friendly refusals carry their own detail strings, never bare codes:
# a soft cap is a nudge, a denied reopen is an explanation, and the
# user never meets an English error.
TICKET_NOT_FOUND_404_DETAIL = "تیکت پیدا نشد."
TICKET_BAD_BODY_400_DETAIL = "دسته و توضیح تیکت را کامل بفرستید."
TICKET_SHORT_BODY_DETAIL = (
    "توضیح مشکل حداقل ۱۰ حرف باشد؛ دسته به‌تنهایی کافی نیست."
)
TICKET_BAD_IMAGE_DETAIL = (
    "تصویر پیوست نامعتبر است؛ فقط PNG، JPEG، WebP یا GIF تا ۲ مگابایت."
)
TICKET_SOFT_CAP_DETAIL = (
    "شمار تیکت‌های باز شما به سقف رسیده است؛ اول تیکت‌های باز را"
    " دنبال کنید یا ببندید، بعد تیکت تازه بزنید."
)
TICKET_CLOSED_DETAIL = (
    "این تیکت بسته است؛ برای پیگیری، همان تیکت را (تا ۷ روز) بازگشایی"
    " کنید یا تیکت تازه بزنید."
)
TICKET_REOPEN_DENIED_DETAIL = (
    "زمان یا سهمِ بازگشایی این تیکت تمام شده است؛ تیکت تازه بزنید."
)
TICKET_BAD_REPLY_DETAIL = "متن پاسخ خالی نیست."
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
# The console's write side (T26, GitLab #28): the forms POST
# form-encoded bodies and get a 303 back to the page, so a refused
# write re-renders the console with its Farsi note — one whitelisted
# error code in the query string, never user text in a URL.
ADMIN_ERROR_BAD_BODY = "bad_body"
ADMIN_ERROR_BAD_PHONE = "bad_phone"
ADMIN_ERROR_EMAIL_TAKEN = "email_taken"
ADMIN_ERROR_BAD_AMOUNT = "bad_amount"
ADMIN_ERROR_UNKNOWN_ACCOUNT = "unknown_account"
ADMIN_ERROR_NOTES = {
    ADMIN_ERROR_BAD_BODY: "ایمیل و گذرواژهٔ حساب را بفرستید (گذرواژه خالی نباشد).",
    ADMIN_ERROR_BAD_PHONE: "شمارهٔ تلفن همراه را وارد کنید.",
    ADMIN_ERROR_EMAIL_TAKEN: "این ایمیل پیش‌تر حساب گرفته است.",
    ADMIN_ERROR_BAD_AMOUNT: "مقدار شارژ را به تومان و مثبت وارد کنید.",
    ADMIN_ERROR_UNKNOWN_ACCOUNT: "حسابی با این ایمیل نیست.",
}

# The profile's honesty badges (T24, GitLab #27) — DRAFT display
# vocabulary, pending PM approval (2026-09-19, CONTEXT.md's draft
# roster discipline): the two labels that keep an estimate from ever
# rendering as a measurement. They live here in ONE constant pair and
# ride the /profile/data payload per entry, so the PM's approval
# renames them in one line and the sheet never decides what counts as
# measured (index.html documents the same strings in its own DRAFT
# comment — the tests lock the two together).
PROFILE_METERED_BADGE = "اندازه‌گیری‌شده"
PROFILE_ESTIMATED_BADGE = "تخمینی"

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


def audit_quiet(action: str, actor_email, detail) -> None:
    """One audit append that never breaks the action it records (T26's
    shared shape): the log records what HAPPENED, so it rides AFTER the
    store said yes — and a broken audit store must not un-issue an
    Account or un-top a Balance. The failure stays loud on stderr,
    never silent, and the action stands."""
    try:
        audit.append(action, actor_email=actor_email, detail=detail)
    except Exception as exc:
        sys.stderr.write(
            f"audit append failed for {action}: {exc!r}\n"
        )


def ensure_first_admin_from_env() -> None:
    """The first Admin from config (T26, GitLab #28 — the bootstrap
    seed command retired): compose env carries ADMIN_EMAIL and
    ADMIN_PASSWORD; the server creates that Admin once at startup and
    records it in the audit log (the deployment's act is history too).
    When an Admin already stands — or either variable is empty, or the
    email is taken — nothing is touched and the skip is loud on
    stderr: a restart must never quietly re-issue the PM's password,
    and the console never mutates silently, at startup included."""
    email = os.environ.get("ADMIN_EMAIL", "").strip()
    password = os.environ.get("ADMIN_PASSWORD", "")
    if not email or not password:
        return
    if ensure_admin(email, password):
        audit_quiet(audit.ADMIN_SEEDED, actor_email=email, detail={"email": email})
        sys.stderr.write(
            f"مدیر نخستین از ADMIN_EMAIL ساخته شد ({email}) و در "
            "دفتر رخدادها ثبت شد.\n"
        )
    elif not admins_exist():
        sys.stderr.write(
            f"ADMIN_EMAIL ({email}) پیش‌تر به یک حساب دیگر رسیده است؛ "
            "مدیری ساخته نشد.\n"
        )
    else:
        sys.stderr.write(
            "مدیری از پیش وجود دارد؛ ADMIN_EMAIL/ADMIN_PASSWORD نادیده "
            "گرفته شد — گذرواژهٔ کسی بی‌کنش عوض نمی‌شود.\n"
        )


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
    """The gate flip (ADR-0013), finished (T21, GitLab #23): the
    Account's EMAIL — the identity every store now keys by — derived
    from the cwb_auth cookie, or None after answering the request.

    Every endpoint that once read the client-supplied phone header goes
    through here; the header no longer authenticates anything. An
    absent, invalid, expired, or tampered token (and a token whose
    Account no longer exists) answers 401. The old no-attached-phone
    403 is retired with the phone-keyed stores: an Account without
    attached legacy history is a working Account — it chats, it spends,
    it is bounded by its quota and Balance like any other. The attached
    phone survives as legacy data only: the mapping the Admin's attach
    flow gives ui/migrate.py to carry pre-T21 rows onto their Accounts.
    Neither answer ever crashes: the gate's rejections keep the
    drain-first shape so the response never dies to a reset."""
    claims = verify_token(handler._cookie_token())
    if claims is not None:
        account = account_by_email(claims["email"])
    else:
        account = None
    if account is None:
        handler._json_error(401, AUTH_LOGIN_401_DETAIL)
        return None
    return account["email"]


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


# The follow-up thread's budget (ADR-0015, widened by ADR-0019): the
# sitting's recent turns ride as framing only — a follow-up-shaped
# question (at most this many words) is what gets rewritten, and each
# earlier answer contributes a capped connective text, so the prompts
# stay small. 25 words: real clarifications and refinements run 5–20
# words; a longer message almost always names its own subject and
# rides raw.
REWRITE_MAX_WORDS = 25
_TAIL_TURNS = 3
_TAIL_ANSWER_CHARS = 800


def _article_connective_text(payload) -> str:
    """A stored assistant payload's own writing — the blocks' text
    parts only. The quote parts are the passages' job (the writer
    re-copies them verbatim from the sources), so they never ride the
    tail; the connective text is what tells the rewriter and the
    phase-2 writer what the sitting has already covered."""
    if not isinstance(payload, dict):
        return ""
    parts: list[str] = []
    for block in payload.get("blocks") or []:
        if not isinstance(block, dict) or block.get("type") != "paragraph":
            continue
        for part in block.get("parts") or []:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"].strip())
    return " ".join(p for p in parts if p)[:_TAIL_ANSWER_CHARS]


def _conversation_tail(account: str, session_id) -> str:
    """The sitting's recent turns (the Session store's own rows, the
    same transcript the resume renders) as one context string — the
    last few ask/answer pairs, oldest first. The follow-up rewrite and
    the phase-2 framing read it; everything about it fails soft: no
    sitting, an unreadable store, or no turns answer an empty string
    and the callers ride without context."""
    if not isinstance(session_id, int):
        return ""
    try:
        session = session_store.get_session(account, session_id)
    except Exception:
        return ""
    if not session:
        return ""
    lines: list[str] = []
    for message in session.get("messages") or []:
        if not isinstance(message, dict):
            continue
        payload = message.get("payload")
        if message.get("role") == "user":
            text = payload.get("text") if isinstance(payload, dict) else None
            if isinstance(text, str) and text.strip():
                lines.append(f"Q: {text.strip()[:_TAIL_ANSWER_CHARS]}")
        else:
            text = _article_connective_text(payload)
            if text:
                lines.append(f"A: {text}")
    return "\n".join(lines[-_TAIL_TURNS * 2 :])


# The pages index's gzip cache (ADR-0017): the per-Book text index
# shrinks ~6x on the wire but re-compressing it per request spends CPU
# on every reader open — the compressed bytes live here keyed by the
# file's mtime, so an unchanged index compresses exactly once. (The
# mtime key makes a re-indexed Book re-compress for free.)
_GZIP_CACHE: dict[tuple[str, int], bytes] = {}


def _gzipped_file(file_path: Path, mtime_ns: int) -> bytes:
    key = (str(file_path), mtime_ns)
    body = _GZIP_CACHE.get(key)
    if body is None:
        body = gzip.compress(file_path.read_bytes(), compresslevel=6)
        _GZIP_CACHE.clear()  # one Book's index at a time — the files are few
        _GZIP_CACHE[key] = body
    return body


# --- the ticket snapshot's builders (the ticket-system map) -----------
# The snapshot is the SERVER's word for what the user saw, built from
# the Session store the moment the ticket is filed — the client sends
# ids only, so nothing report-shaped can be forged. Everything here
# fails soft: an unreadable payload answers its empty string, never a
# refused ticket.

_TICKET_SNAPSHOT_CAP = 1200


def _ticket_snapshot_parts(
    session: dict, message_id=None, ask_key=None
) -> dict | None:
    """The message the chip pointed at, as the snapshot's parts: the
    answer itself (its connective text when the payload is blocks —
    the same discipline _conversation_tail applies), the sitting's
    last standing question before it, the book, a best-effort mode,
    and the ask_key/ts the cost lookup wants. The pointer is the
    message id OR the ask's ask_key — the sheet knows the key live
    (it mints it) and the id only after a resume read; both name the
    same settled row. None when neither names one of the Session's
    assistant rows."""
    messages = session.get("messages") or []
    target = None
    last_user = ""
    for message in messages:
        if not isinstance(message, dict):
            continue
        payload = message.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        if message.get("role") == "user":
            text = payload.get("text")
            if isinstance(text, str) and text.strip():
                last_user = text.strip()
        if message.get("role") != "assistant":
            continue
        if (
            message.get("id") == message_id
            if message_id is not None
            else False
        ) or (
            ask_key is not None
            and message.get("ask_key") == ask_key
        ):
            target = message
            break
    if target is None:
        return None
    payload = target.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    answer = payload.get("text")
    if not isinstance(answer, str) or not answer.strip():
        answer = _article_connective_text(payload)
    mode = (
        "research"
        if payload.get("research") or payload.get("mode") == "research"
        else "chat"
    )
    model = payload.get("model")
    snapshot = {
        "question": last_user[:_TICKET_SNAPSHOT_CAP],
        "answer": (answer or "").strip()[:_TICKET_SNAPSHOT_CAP],
        "book": session.get("book") or "",
        "mode": mode,
        "model": model if isinstance(model, str) else None,
    }
    references = payload.get("references")
    if isinstance(references, list) and references:
        snapshot["extra"] = {"references": references[:12]}
    return {
        "snapshot": snapshot,
        "ask_key": target.get("ask_key"),
        "message_id": target.get("id"),
        "ts": target.get("ts"),
    }


def _turn_cost_best_effort(account: str, message_ts) -> float | None:
    """The turn's cost out of the ledger's ask groups (the map's
    decision 01: silent capture, best-effort, never a refusal): the
    newest ask group that opened at or before the message is the turn
    the answer belongs to — its summed cost is the honest estimate. A
    ledger that cannot answer (no groups, unreadable ts) stays NULL."""
    try:
        groups = ledger.session_history(account)
    except Exception:
        return None
    if not isinstance(message_ts, str) or not message_ts:
        return None
    eligible = [
        group
        for group in groups
        if isinstance(group, dict)
        and isinstance(group.get("started"), str)
        and group["started"] <= message_ts
    ]
    if not eligible:
        return None
    cost = eligible[0].get("cost_toman")
    return float(cost) if isinstance(cost, (int, float)) else None


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
        if path == "/chat/latest":
            self._chat_latest()
            return
        if path == "/admin":
            # The «میز مدیریت» (T25): the Admin's server-rendered
            # mirror of the system — a read, never a mutation.
            self._admin_console()
            return
        if path == "/profile/data":
            self._profile_data()
            return
        if path == "/sessions":
            # The Session store's list (T27 stage 3, GitLab #40): the
            # sidebar's read, the caller's own Sessions only.
            self._sessions_list()
            return
        if path == "/notes":
            # The Notebook's read (the selection map, ticket 08): the
            # panel's list, the caller's own notes only.
            self._notes_list()
            return
        if path == "/tickets":
            # The ticket panel's list (the ticket-system map): the
            # caller's own tickets, newest first.
            self._tickets_list()
            return
        if path == "/tickets/unread":
            # The panel badge's number: the account's tickets holding
            # an unread reply of the manager's — polled beside
            # /usage/live.
            self._tickets_unread()
            return
        if path.startswith("/tickets/"):
            rest = path[len("/tickets/"):]
            if rest.isdigit():
                self._ticket_get(int(rest))
                return
            file_id = rest[: -len("/file")] if rest.endswith("/file") else ""
            if file_id.isdigit():
                self._ticket_file(int(file_id))
                return
        if path.startswith("/admin/tickets"):
            # The manager's inbox (the ticket-system map): the console's
            # sibling page, server-rendered, the role gate alone.
            account = self._admin_gate()
            if account is None:
                return
            rest = path[len("/admin/tickets"):]
            if rest in ("", "/"):
                self._admin_tickets_page(account)
                return
            detail = rest[1:] if rest.startswith("/") else rest
            if detail.isdigit():
                self._admin_ticket_page(account, int(detail))
                return
            transcript = detail[: -len("/transcript")] if detail.endswith(
                "/transcript"
            ) else ""
            if transcript.isdigit():
                self._admin_ticket_transcript(account, int(transcript))
                return
        if path.startswith("/sessions/"):
            rest = path[len("/sessions/"):]
            if rest.isdigit():
                self._session_get(int(rest))
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
        ever reach the filesystem. HTTP Range is honored byte-for-byte
        (pdf.js fetches a Book's pages lazily with disableAutoFetch,
        ADR-0017), and the full-form answers carry an ETag + 304
        revalidation so reopening a Book never refetches it whole.

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
            stat = file_path.stat()
            total_size = stat.st_size
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
                self.send_header("ETag", f'"{total_size:x}-{stat.st_mtime_ns:x}"')
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
            # The revalidation pair (ADR-0017): a strong ETag off the
            # file's own stat, and a must-revalidate policy — the
            # browser re-asks on every reader open and a 304 answer
            # costs one stat, where the old unconditioned 200 refetched
            # the whole 16 MB Book each time. Range requests (pdf.js's
            # lazy page fetches with disableAutoFetch) carry the ETag
            # too but never take the 304 short-circuit.
            etag = f'"{total_size:x}-{stat.st_mtime_ns:x}"'
            if (
                not range_header
                and self.headers.get("If-None-Match") == etag
            ):
                self.send_response(304)
                self.send_header("ETag", etag)
                self.send_header(
                    "Cache-Control", "public, max-age=0, must-revalidate"
                )
                self.end_headers()
                return
            gzip_wanted = (
                suffix == "pages.json"
                and "gzip" in (
                    self.headers.get("Accept-Encoding") or ""
                )
            )
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("ETag", etag)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header(
                "Cache-Control", "public, max-age=0, must-revalidate"
            )
            if suffix == "pdf" and path.endswith(".pdf"):
                self.send_header(
                    "Content-Disposition",
                    f'attachment; filename="{dataset}.pdf"',
                )
            if gzip_wanted:
                body = _gzipped_file(file_path, stat.st_mtime_ns)
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_header("Content-Length", str(total_size))
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
        started = time.monotonic()
        try:
            body = cache_file.read_bytes()
        except OSError:
            body = None
        if body is not None:
            # The fast path's own witness (ADR-0017): a warm cache hit
            # answers here, before the lock, and is logged like a miss.
            sys.stderr.write(
                f"render {dataset} p{page_number} w{width}"
                f" hit {time.monotonic() - started:.3f}s\n"
            )
        else:
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
    def _pdf_doc_handle(source: Path):
        """The reused parsed document (ADR-0017): an LRU of open
        PdfDocuments, closed on eviction. A raster cache miss used to
        pay a full parse of the whole file on EVERY page — under the
        global lock, so every waiting client waited with it. Callers
        hold RENDER_LOCK, so the reuse never widens PDFium's thread
        exposure; the books are read-only mounts, the handle cannot go
        stale under a live file."""
        key = str(source)
        pdf = PDF_DOCS_CACHE.get(key)
        if pdf is not None:
            PDF_DOCS_CACHE[key] = PDF_DOCS_CACHE.pop(key)
            return pdf
        while len(PDF_DOCS_CACHE) >= PDF_DOCS_CACHE_CAP:
            oldest = next(iter(PDF_DOCS_CACHE))
            try:
                PDF_DOCS_CACHE.pop(oldest).close()
            except Exception:
                pass
        pdf = _pdfium.PdfDocument(str(source))
        PDF_DOCS_CACHE[key] = pdf
        return pdf

    @staticmethod
    def _prune_render_cache():
        """The cache's ceiling (ADR-0017): once the rasters outgrow the
        cap the oldest go first. The sweep runs only on the write path
        and only when the cap is crossed — the hit path never pays for
        it."""
        try:
            entries = list(RENDER_CACHE_DIR.glob("*.png"))
            total = sum(e.stat().st_size for e in entries)
            if total <= RENDER_CACHE_CAP:
                return
            entries.sort(key=lambda e: e.stat().st_mtime)
            for entry in entries:
                if total <= RENDER_CACHE_CAP:
                    break
                try:
                    total -= entry.stat().st_size
                except OSError:
                    continue
                entry.unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def _render_book_page(dataset, page_number, width, cache_file):
        """One page raster, cached atomically; None when the page does
        not exist or the PDF is unreadable. Every answer logs its cost —
        hit/miss and milliseconds — so a slow page has a witness, and a
        miss reuses the open-document cache instead of re-parsing the
        whole file (ADR-0017)."""
        source = BOOKS_DIR / f"{dataset}.pdf"
        if not source.exists():
            return None
        RENDER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        try:
            body = cache_file.read_bytes()
        except OSError:
            body = None
        with RENDER_LOCK:
            if body is None:
                # The double-check: another thread may have rendered
                # this exact page while this one waited on the lock.
                try:
                    body = cache_file.read_bytes()
                except OSError:
                    body = None
            if body is not None:
                sys.stderr.write(
                    f"render {dataset} p{page_number} w{width}"
                    f" hit {time.monotonic() - started:.3f}s\n"
                )
                return body
            try:
                pdf = SessionHandler._pdf_doc_handle(source)
                if page_number > len(pdf):
                    return None
                page = pdf[page_number - 1]
                bitmap = page.render(scale=width / page.get_width())
                image = bitmap.to_pil()
                tmp = cache_file.with_suffix(f".tmp{threading.get_ident()}")
                image.save(tmp, format="PNG")
                os.replace(tmp, cache_file)
                body = cache_file.read_bytes()
            except Exception:
                sys.stderr.write(
                    f"render failed: {dataset} p{page_number}\n"
                )
                return None
        sys.stderr.write(
            f"render {dataset} p{page_number} w{width}"
            f" miss {time.monotonic() - started:.3f}s\n"
        )
        SessionHandler._prune_render_cache()
        return body

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

    def _balance_gate(self, account: str) -> bool:
        """The prepaid stop (T23, GitLab #25): an Account whose Balance
        (اعتبار) is spent answers 402 with the Farsi fix — the ask and
        the phases each pay for themselves before they run, and a turn
        that cannot be paid for never starts. A turn or phase already
        running finishes; only the NEXT spend is stopped. The daily
        quota still applies on top of the Balance, never instead."""
        if get_balance(account) <= 0:
            self._json_error(
                402,
                "اعتبار این حساب تمام شده است؛ از مدیر بخواهید اعتبار را شارژ کند.",
            )
            return False
        return True

    def _gate_account(self):
        """The ask gate (ADR-0013): the Account behind the login
        cookie — resolved, never client-supplied — with chats left
        today; records the chat. The identity resolver answers 401
        itself and returns None when rejected; the quota bounds per
        Account through the email the store keys by (T21)."""
        account = resolve_identity(self)
        if account is None:
            return None
        if not self._balance_gate(account):
            return None
        if chats_today(account) >= DAILY_CHAT_LIMIT:
            self._json_error(
                429, "شمار گفتگوهای امروز این حساب پر شده است؛ فردا بیایید."
            )
            return None
        record_chat(account)
        return account

    def _quoted_account(self):
        """The phase-2 gate: an Account with at least one chat today
        (the quoted answer belongs to a chat that already started)."""
        account = resolve_identity(self)
        if account is None:
            return None
        if not self._balance_gate(account):
            return None
        if chats_today(account) < 1:
            self._json_error(
                429, "پاسخ استنادی بخشی از همان گفتگو است؛ اول یک پرسش بپرسید."
            )
            return None
        return account

    def _research_account(self):
        """The research gate (ADR-0015, widened by the guest cut,
        2026-10-04): the Account behind the login cookie with a positive
        Balance — and, since the cut, research left on the Account. The
        old minimum-of-one-chat precondition existed because research
        was seeded from a prior ask's Evidence pool; the composer's
        research toggle starts the conversation from the typed question
        alone (the pool seed is optional), so the day's first act may
        be research. The daily chat quota stays the normal ask's quota —
        research turns record no chats and never burn it; the Balance
        is the research spend's own prepaid stop. An Account issued
        without research (a guest) answers 403 under its own name: the
        door is closed, not spent, and the Balance gate above stays
        the ask's own."""
        account = resolve_identity(self)
        if account is None:
            return None
        if not self._balance_gate(account):
            return None
        row = account_by_email(account)
        if row is not None and row.get("research_enabled") is False:
            self._json_error(403, RESEARCH_DISABLED_403_DETAIL)
            return None
        return account

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
        overlay hook treats it one way. research_enabled rides along
        (the guest cut): the sheet's toggle learns its own Account's
        door here, never from a guess."""
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
                "research_enabled": account.get("research_enabled", True),
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
            research_enabled = payload.get("research_enabled", True)
            if not isinstance(email, str) or not email.strip():
                raise ValueError("email is required")
            if not isinstance(password, str) or not password:
                raise ValueError("password is required")
            if phone is not None and not isinstance(phone, str):
                raise ValueError("phone must be a string")
            if not isinstance(research_enabled, bool):
                raise ValueError("research_enabled must be a boolean")
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
        created = create_account(
            email,
            password,
            phone=phone,
            role="operator",
            research_enabled=research_enabled,
        )
        if created is None:
            self._send_json(409, {"detail": AUTH_EMAIL_TAKEN_DETAIL})
            return
        # The audit's first wired action (ADR-0013, T25): an issuance is
        # the Admin's act and lands in the append-only log — the issuer
        # and the issued, appended only after the creation truly
        # succeeded. A refused creation (the 409 above, the bad body
        # before it) logs nothing: the log records what HAPPENED, never
        # what was attempted. A guest issuance (research off, the
        # 2026-10-04 cut) says so in its row — the cut is a fact of the
        # Account, not a mode of the moment.
        audit_detail = {"email": created["email"], "phone": created["phone"]}
        if not research_enabled:
            audit_detail["research_enabled"] = False
        audit_quiet(
            audit.ACCOUNT_CREATED,
            actor_email=account["email"],
            detail=audit_detail,
        )
        self._send_json(
            200,
            {
                "email": created["email"],
                "role": created["role"],
                "phone": created["phone"],
            },
        )

    def do_DELETE(self):
        """DELETE: the Session store's one destructive verb (T27 stage
        3, the list-v1 shape — delete only) and the Notebook's single
        delete. A row that is not the caller's is a 404, never an
        erase."""
        path = self.path.split("?", 1)[0]
        if path.startswith("/sessions/"):
            rest = path[len("/sessions/"):]
            if rest.isdigit():
                self._session_delete(int(rest))
                return
        if path.startswith("/notes/"):
            rest = path[len("/notes/"):]
            if rest.isdigit():
                self._note_delete(int(rest))
                return
        self.send_error(404, "Not found")

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
        if path == "/ask":
            # The retrieval-only reference ask (ADR-0014): the T27
            # shell's composer does not call this door — production
            # asks through /api/v1/recall's one-door composer (ADR-0015)
            # — but the reference flow keeps its gate and its record.
            account = self._gate_account()
            if account is None:
                return
            # A new ask PAUSES the Account's research (decision 04, the
            # research-mode v2 wayfinder map): the in-flight turn
            # aborts cooperatively and its message queues for the
            # resume chip — the session itself stays open, its map and
            # evidence intact. Closing stays the user's word alone.
            pause_account_research(account)
            # The ask's own ledger entry (T22): input-side size
            # estimate, like the recall POST always recorded.
            record_size_estimate(
                account, "ask", int(self.headers.get("Content-Length", "0") or "0")
            )
            self._ask(account)
        if path == "/admin/accounts":
            # The console's issuance form (T26, GitLab #28): the same
            # act as /auth/accounts, wearing the page's shape.
            self._admin_create_account()
            return
        if path == "/admin/topup":
            # The console's top-up form (T26, GitLab #28): Toman lands
            # on a Balance, audited, from the browser.
            self._admin_topup()
            return
        if path == "/admin/attach":
            # The console's attach form (T21, GitLab #23): the legacy
            # phone joins its Account, and the stores' pre-account rows
            # follow the mapping — audited, from the browser.
            self._admin_attach()
            return
        if path == "/admin/research-access":
            # The console's guest-cut flip (2026-10-04): research on or
            # off for a standing Account — audited, from the browser.
            self._admin_research_access()
            return
        if path == "/tickets":
            # File one ticket (the ticket-system map): the client only
            # points at the message — the snapshot is built here, from
            # the Session store, so nothing report-shaped is forged.
            self._ticket_create()
            return
        if path.startswith("/tickets/"):
            rest = path[len("/tickets/"):]
            ticket_id = rest.split("/", 1)[0]
            action = rest[len(ticket_id) + 1 :] if "/" in rest else ""
            if ticket_id.isdigit():
                if action == "replies":
                    self._ticket_reply(int(ticket_id))
                    return
                if action == "close":
                    self._ticket_close(int(ticket_id))
                    return
                if action == "reopen":
                    self._ticket_reopen(int(ticket_id))
                    return
        if path == "/admin/tickets/reply":
            # The inbox's reply form (the ticket-system map): the
            # manager's answer lands as a thread row and moves the
            # ticket to پاسخ داده شد — form-encoded, 303 back, audited.
            self._admin_ticket_reply()
            return
        if path == "/admin/tickets/close":
            self._admin_ticket_close()
            return
        if path == "/admin/tickets/edit-reply":
            self._admin_ticket_edit_reply()
            return
        if path == "/api/v1/recall":
            account = self._gate_account()
            if account is None:
                return
            # A new ask PAUSES research (decision 04): after the gate
            # has recorded the chat, the Account's in-flight research
            # turn aborts — cooperatively; the worker exits at its next
            # boundary — and its message queues for the resume chip.
            # The session stays open beside the new ask; another
            # Account's research is never touched.
            pause_account_research(account)
            # The ask's own entry (T22, GitLab #24): the gate knows the
            # request's size before the relay streams the answer — an
            # input-side estimate, marked estimated like every
            # non-metered entry. The phases' composer calls ride the
            # thread's usage tap and land metered or estimated by the
            # reply's own honesty.
            record_size_estimate(
                account, "ask", int(self.headers.get("Content-Length", "0") or "0")
            )
            self._proxy("POST", account)
            return
        if path == "/sessions":
            # The Session store's create (T27 stage 3): the sheet opens
            # the sitting on its first ask.
            self._session_create()
            return
        if path == "/notes":
            # The Notebook's quick-save (the selection map, ticket 08):
            # the popover's capture lands here with its defaults.
            self._note_create()
            return
        if path == "/notes/bulk-delete":
            self._note_bulk_delete()
            return
        if path.startswith("/notes/"):
            rest = path[len("/notes/"):]
            if rest.isdigit():
                self._note_update(int(rest))
                return
        if path.startswith("/sessions/"):
            rest = path[len("/sessions/"):]
            if rest.endswith("/messages") and rest[: -len("/messages")].isdigit():
                self._session_append(int(rest[: -len("/messages")]))
                return
        if path == "/quoted-answer":
            account = self._quoted_account()
            if account is None:
                return
            # The planner and the writer both ride this thread's composer
            # calls — each upstream call lands its own ledger entry.
            set_meter(
                lambda prompt, reply: record_composer_call(
                    account, "writer", prompt, reply
                )
            )
            try:
                self._quoted_answer(account)
            finally:
                set_meter(None)
            return
        if path == "/quote-selection":
            account = self._quoted_account()
            if account is None:
                return
            set_meter(
                lambda prompt, reply: record_composer_call(
                    account, "picker", prompt, reply
                )
            )
            try:
                self._quote_selection(account)
            finally:
                set_meter(None)
            return
        if path == "/recall-more":
            account = self._quoted_account()
            if account is None:
                return
            set_meter(
                lambda prompt, reply: record_composer_call(
                    account, "composer", prompt, reply
                )
            )
            try:
                self._recall_more(account)
            finally:
                set_meter(None)
            return
        if path == "/evidence-fallback":
            if self._quoted_account() is None:
                return
            self._evidence_fallback()
            return
        if path == "/research/message":
            account = self._research_account()
            if account is None:
                return
            self._research_message(account)
            return
        if path == "/research/resume-from":
            # The closed session's door (decision 04, the research-mode
            # v2 wayfinder map): the same gate as the message start —
            # bookkeeping only, no upstream call, nothing to meter.
            account = self._research_account()
            if account is None:
                return
            self._research_resume_from(account)
            return
        if path == "/research/decide":
            # The same gate as the message start (ADR-0015): a
            # toggle-first conversation's proposals must be decidable
            # even when no normal ask ran today. Bookkeeping — no
            # upstream call, nothing to meter.
            account = self._research_account()
            if account is None:
                return
            self._research_decide(account)
            return
        if path == "/research/plan-item":
            # The per-item plan door (decision 03, the research-mode v2
            # wayfinder map): one section accepted, rejected, or edited
            # by its stable key — bookkeeping, the same gate as every
            # research write.
            account = self._research_account()
            if account is None:
                return
            self._research_plan_item(account)
            return
        if path == "/next-tier-recall":
            if self._quoted_account() is None:
                return
            self._next_tier_recall()
            return
        self._drain_request_body()
        self.send_error(404, "Not found")

    def _quoted_answer(self, account: str) -> None:
        """Compose the Quoted answer; empty blocks = fallback.

        The settled article is also the Account's Session record
        (vanishing-content fix, 2026-09-24): when the body carries the
        sitting's ``session_id`` and this ask's ``ask_key``, the server
        settles the assistant turn ITSELF, before the reply is written —
        a browser that reloads mid-compose leaves the sitting with its
        answer, because the store write no longer waits for the client
        to report it. A re-settle of the same ask_key upgrades the row
        in place, so the transcript keeps ONE answer per ask. A store
        failure never fails the phase reply.

        The reference flow's record (ADR-0014) rides beside it: a body
        carrying the ask-minted ``chat_id`` updates the reference chat
        store's row — the reload's restore read re-renders the guarded
        document there."""
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
            chat_id = payload.get("chat_id")
            if not isinstance(chat_id, str):
                chat_id = ""
            session_id = payload.get("session_id")
            ask_key = payload.get("ask_key")
            if not isinstance(question, str) or not question.strip() or not sources:
                raise ValueError("question and sources are required")
            if not isinstance(answer, str):
                answer = ""
        except (ValueError, KeyError, TypeError):
            self.send_error(400, "Bad request")
            return
        # The sitting's earlier turns ride as framing (the follow-up
        # thread, ADR-0015) — a stored snapshot contributes nothing
        # (its parts are quotes only), so the parallel picker's row,
        # if it settled first, never pollutes the writer's context.
        tail = _conversation_tail(account, session_id)
        blocks, truncated = compose_quoted_answer(
            question, answer, sources, conversation_tail=tail
        )
        # The settle is gated on a written document (the other half of
        # the arrive-order race, 2026-09-27 operator report): a failed
        # phase 2 used to settle empty blocks and erase the snapshot
        # the picker had already stored — the store refuses downgrades
        # too, but the empty write never even starts here.
        if (
            blocks
            and isinstance(session_id, int)
            and isinstance(ask_key, str)
            and ask_key.strip()
        ):
            try:
                session_store.settle_ask(
                    account,
                    session_id,
                    ask_key,
                    {
                        "question": question,
                        "blocks": blocks,
                        "truncated": truncated,
                        "citations": sources[:10],
                    },
                )
            except Exception:
                sys.stderr.write("session settle failed (quoted-answer)\n")
        if blocks and chat_id:
            # The reload's record (ADR-0014): the guarded document that
            # rendered is the document the restore re-renders — a
            # write guarded by the owner's key, so a foreign chat_id
            # lands nowhere.
            chat_store.update_quoted(chat_id, account, blocks, truncated)
        body = json.dumps(
            {"blocks": blocks, "truncated": truncated}, ensure_ascii=False
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _ask(self, account: str) -> None:
        """The retrieval-only ask (ADR-0014): the first answer's Evidence
        pool with NO LLM completion in the loop — one only_context search
        per selected Book over the main Cognee service, so the pool is
        the hybrid retrieval's own passages every time, in seconds. The
        sheet's first answer stays retrieval (the Quote selection over
        this pool) and can never arrive as a conclusive essay. The ask
        mints its chat row here — the picker, the Quoted answer, and the
        widen update it by the chat_id this reply carries, and a reload
        restores the whole sheet from it. A malformed body answers 400; a
        searcher that finds nothing answers 200 {"sources": []} — the
        honest empty the sheet's no-citation note consumes, never a
        5xx."""
        length = int(self.headers.get("Content-Length", "0") or "0")
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            query = payload["query"]
            if not isinstance(query, str) or not query.strip():
                raise ValueError("query is required")
            datasets = validated_datasets(payload.get("datasets"))
        except (ValueError, KeyError, TypeError):
            # The body is already read above, so _send_json is safe.
            self._send_json(400, {"detail": "پرسش را بنویسید."})
            return
        sources = ask_pool(query.strip(), datasets)
        chat_id = secrets.token_hex(8)
        chat_store.create_chat(chat_id, account, query.strip(), datasets, sources)
        self._send_json(
            200,
            {"chat_id": chat_id, "sources": sources, "pool_size": len(sources)},
        )

    def _chat_latest(self) -> None:
        """The reload's restore read (ADR-0014): the Account's newest ask
        row — question, Book selection, pool, Quote selection, and the
        Quoted answer's blocks — or null when the Account never asked.
        No LLM, no side effects; the identity comes from the login
        cookie (ADR-0013)."""
        account = resolve_identity(self)
        if account is None:
            return
        self._send_json(200, {"chat": latest_chat(account)})

    def _evidence_fallback(self) -> None:
        """The phase-1 citation fallback (ADR-0011): the first message's
        role is to FIND CITATIONS — when the streamed reply came back
        without an Evidence block, ONE pinned reference-on search over
        the question (the dive kernel's searcher, the ask's selected
        Books) fetches the pool directly so the Quote selection and
        phase 2 still run. The gate is the ask's own shape (an Account
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

    def _recall_more(self, account: str) -> None:
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
        if fresh:
            # The widen's growth is part of the ask's record (ADR-0014):
            # the restored sheet's pool is the merged one, so the store
            # grows by the same fresh passages the reply carries —
            # phone-guarded, keyed by the chat_id the ask minted.
            chat_id = payload.get("chat_id")
            if isinstance(chat_id, str) and chat_id:
                chat_store.update_pool(
                    chat_id, account, sources + fresh
                )
        body = json.dumps({"sources": fresh}, ensure_ascii=False).encode(
            "utf-8"
        )
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _quote_selection(self, account: str) -> None:
        """Pick the Quote selection (ADR-0006, issue #28): the pool
        exactly as the sheet parsed it, one picker call, the guarded
        selections back. The gate is phase 2's shape — the picker
        belongs to the chat phase 1 recorded — so it needs an Account
        with at least one chat today and never records or counts one. A
        malformed body or an empty pool answers 400 (a JSON detail, the
        gate's shape) before any upstream call; a picker failure or a
        below-floor selection answers 200 {"selections": []} — the one
        uniform empty shape the sheet's prose fallback consumes, never
        a 5xx.

        With the sitting's ``session_id`` and this ask's ``ask_key`` in
        the body, the kept selections also settle the ask's assistant
        turn SERVER-SIDE (the vanishing-content fix, 2026-09-24): the
        first-answer snapshot rides the same idempotent row the phase-2
        article later upgrades, so a reload after the picker — even
        hours before phase 2 lands — leaves the sitting with the answer
        the operator actually received. The snapshot's blocks are the
        selections rendered as quoting paragraphs, the exact shape the
        resume read draws.

        The reference flow's record (ADR-0014) rides beside it: a body
        carrying the ask-minted ``chat_id`` records the kept selections
        in the reference chat store's row."""
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
            chat_id = payload.get("chat_id")
            if not isinstance(chat_id, str):
                chat_id = ""
            session_id = payload.get("session_id")
            ask_key = payload.get("ask_key")
            if not isinstance(question, str) or not question.strip() or not sources:
                raise ValueError("question and sources are required")
        except (ValueError, KeyError, TypeError):
            # The body is already read above, so _send_json is safe —
            # _json_error would drain a second time and block.
            self._send_json(
                400, {"detail": "پرسش و استنادهای بازیابی‌شده را بفرستید."}
            )
            return
        # The sitting's earlier turns ride the picker as framing too
        # (the follow-up thread, ADR-0019) — the first answer the user
        # sees should continue the thread, not re-answer the last ask.
        # Same fail-soft tail as the writer's: no sitting, no context.
        tail = _conversation_tail(account, session_id)
        selections = pick_quote_selection(question, sources, tail)
        if (
            selections
            and isinstance(session_id, int)
            and isinstance(ask_key, str)
            and ask_key.strip()
        ):
            snapshot = {
                "question": question,
                "blocks": [
                    {
                        "type": "paragraph",
                        "parts": [
                            {
                                "quote": item["text"],
                                "reference": item["reference"],
                                "pages_label": item.get("pages_label", ""),
                                "first_page_label": item.get(
                                    "first_page_label", ""
                                ),
                                "book_label": item.get("book_label", ""),
                            }
                        ],
                    }
                    for item in selections
                ],
                "citations": sources[:10],
                "selection_snapshot": True,
            }
            try:
                session_store.settle_ask(
                    account, session_id, ask_key, snapshot
                )
            except Exception:
                sys.stderr.write("session settle failed (quote-selection)\n")
        if chat_id and selections:
            chat_store.update_selections(chat_id, account, selections)
        self._send_json(200, {"selections": selections, "pool_size": len(sources)})

    def _research_turn_status(self) -> None:
        """The turn poll surface: the sheet asks for a turn's state,
        Farsi events, and elapsed seconds — and, when the turn settled,
        its outcome. `done` carries the reply payload ({"reply",
        "suggestions", "state"}), `failed` a short Farsi detail, and an
        unknown id (a restart emptied the registry, or a foreign Account)
        answers 404 — the recorded failure surface, never a hang. The
        account must match the turn's: one Account's poll never reads
        another's research. The identity comes from the login cookie
        (ADR-0013): no valid Account, no poll — 401."""
        account = resolve_identity(self)
        if account is None:
            return
        query = parse_qs(urlparse(self.path).query)
        turn_id = (query.get("turn") or [""])[0]
        payload = None
        turn = find_turn(turn_id)
        if turn is not None and turn.account == account:
            payload = turn_status_payload(turn)
        if payload is None:
            self._json_error(404, RESEARCH_TURN_NOT_FOUND_DETAIL)
            return
        self._send_json(200, payload)

    def _research_state(self) -> None:
        """The state panel's read: one session's summary projection —
        the research question and its version count, scope, evidence and
        claim counts, gaps, pending proposals — account matched, straight
        from the SQLite store, never from the in-memory registry. The
        chip set rides beside the summary under ``suggestions`` so a
        refresh re-renders the skip with the map (T10, GitLab #11).
        The sitting's chat Session id rides as ``chat_session_id``
        (ADR-0016's reverse linkage): the refresh reconnect uses it to
        open the WHOLE sitting — chat and research in one thread —
        instead of the bare research hang a reload used to leave; null
        for a pre-linkage conversation. The identity comes from the
        login cookie (ADR-0013)."""
        account = resolve_identity(self)
        if account is None:
            return
        query = parse_qs(urlparse(self.path).query)
        session_id = (query.get("session") or [""])[0]
        if not session_id:
            self._json_error(404, RESEARCH_SESSION_NOT_FOUND_DETAIL)
            return
        payload, error = research_session_state(account, session_id)
        if payload is None:
            self._json_error(error[0], error[1])
            return
        payload["chat_session_id"] = research_store.chat_session_for(
            account, session_id
        )
        self._send_json(200, payload)

    def _research_messages(self) -> None:
        """The transcript read (ADR-0011): one session's messages in
        order, account matched — a browser refresh re-fetches what was
        said instead of an empty chat. No LLM, no research side
        effects. The identity comes from the login cookie (ADR-0013):
        no valid Account, no transcript — 401."""
        account = resolve_identity(self)
        if account is None:
            return
        query = parse_qs(urlparse(self.path).query)
        session_id = (query.get("session") or [""])[0]
        payload, error = research_session_messages(account, session_id)
        if payload is None:
            self._send_json(error[0], {"detail": error[1]})
            return
        self._send_json(200, payload)

    def _usage_live(self) -> None:
        """The sheet header's live read (T22, GitLab #24): the open
        Session's running Toman total — everything since this Account's
        newest `ask` entry inclusive — plus the server-local day's
        spend, and the Account's Balance beside them (2026-09-29): the
        cost chip shows the sitting against the credit it spends, and
        one read answers all three. The tariff is config's business;
        this endpoint only reports what the ledger already recorded."""
        account = resolve_identity(self)
        if account is None:
            return
        self._send_json(
            200,
            {
                "session_toman": session_total(account),
                "today_toman": today_total(account),
                "balance_toman": get_balance(account),
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
        console is nobody's account view — it mirrors the whole system —
        so the identity an endpoint like /profile/data derives from the
        cookie is beside the point here. Anonymous still gets the
        standard 401; a logged-in operator gets the console's own 403.

        Every read here stays a read: the registry snapshot copies the
        live turns and the recent-settled ring under
        RESEARCH_REGISTRY_LOCK and never writes back, the ledger and
        the quota store answer their per-Account sums (keyed by the
        Account's email, T21), and the audit read
        is a plain SELECT against the append-only table. The page's own
        writes ride the two forms below (T26, GitLab #28) — audited,
        admin-only, never silent."""
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
            accounts_rows.append(
                {
                    "email": row["email"],
                    "role": row["role"],
                    # The attached phone is display data now (T21): the
                    # legacy handle the Admin attached history by, never
                    # a key — the spend and quota reads below key by the
                    # Account's own email.
                    "phone": normalize_phone(row.get("phone") or ""),
                    "balance_toman": row["balance_toman"],
                    "yesterday_spend_toman": day_total(row["email"], yesterday),
                    "today_spend_toman": today_total(row["email"]),
                    "chats_today": chats_today(row["email"]),
                }
            )
        now_mono = time.monotonic()
        with RESEARCH_REGISTRY_LOCK:
            live_turns = [
                {
                    "id": turn.id,
                    "account": turn.account,
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
                    "account": turn.account,
                    "state": turn.state,
                    "error": turn.error,
                }
                for turn in reversed(list(RESEARCH_RECENT_SETTLED))
            ]
        # A refused write redirects back with one whitelisted code
        # (T26): only codes with a Farsi note render — user text never
        # rides a URL into the page.
        error_code = self.path.split("?", 1)[-1] if "?" in self.path else ""
        error_code = dict(
            pair.split("=", 1) for pair in error_code.split("&") if "=" in pair
        ).get("error", "")
        if error_code not in ADMIN_ERROR_NOTES:
            error_code = ""
        body = console_html(
            accounts_rows,
            live_turns,
            settled_turns,
            audit.recent(ADMIN_AUDIT_ROWS),
            quota_limit=DAILY_CHAT_LIMIT,
            generated=time.strftime("%Y-%m-%d %H:%M"),
            error_code=error_code,
            open_tickets=ticket_store.open_count(),
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_redirect(self, location: str) -> None:
        """The form posts' answer (T26): 303 See Other — the browser
        re-reads the page it acted from, fresh. Post, redirect, get:
        the console re-renders its own new state."""
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _admin_gate(self):
        """The write forms' gate — the console's own (resolve_account
        plus the role check). Returns the acting Admin's account dict,
        or None after the refusal was sent (401 anonymous, 403
        operator): the console is nobody's account view, so identity
        resolution is beside the point here — the role is the whole
        gate."""
        account = resolve_account(self)
        if account is None:
            self._json_error(401, AUTH_LOGIN_401_DETAIL)
            return None
        if account["role"] != "admin":
            self._json_error(403, ADMIN_CONSOLE_403_DETAIL)
            return None
        return account

    def _read_form(self) -> dict:
        """One form-encoded body as a plain dict (first value wins —
        these forms carry no repeated fields). Empty body → {}."""
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b""
        parsed = parse_qs(raw.decode("utf-8"), keep_blank_values=True)
        return {key: values[0] for key, values in parsed.items()}

    def _admin_create_account(self) -> None:
        """The console's issuance form (T26, GitLab #28) — account
        creation moves into the console here: email, password, the
        optional legacy phone attach, and the guest cut's research
        checkbox (checked = a normal Account, unchecked = a guest),
        posted from the page, audited like /auth/accounts, and answered
        with a redirect back to the fresh mirror. A refused write
        redirects with a whitelisted error code and logs nothing."""
        account = self._admin_gate()
        if account is None:
            return
        form = self._read_form()
        email = (form.get("email") or "").strip()
        password = form.get("password") or ""
        phone = (form.get("phone") or "").strip() or None
        # An unchecked checkbox simply never rides the form body — the
        # absence IS the guest cut.
        research_enabled = bool(form.get("research"))
        if not email or not password:
            self._send_redirect(f"/admin?error={ADMIN_ERROR_BAD_BODY}")
            return
        if phone:
            phone = normalize_phone(phone)
            if not phone:
                self._send_redirect(f"/admin?error={ADMIN_ERROR_BAD_PHONE}")
                return
        created = create_account(
            email,
            password,
            phone=phone,
            role="operator",
            research_enabled=research_enabled,
        )
        if created is None:
            self._send_redirect(f"/admin?error={ADMIN_ERROR_EMAIL_TAKEN}")
            return
        audit_detail = {"email": created["email"], "phone": created["phone"]}
        if not research_enabled:
            audit_detail["research_enabled"] = False
        audit_quiet(
            audit.ACCOUNT_CREATED,
            actor_email=account["email"],
            detail=audit_detail,
        )
        self._send_redirect("/admin")

    def _admin_research_access(self) -> None:
        """The console's guest-cut flip (2026-10-04): research on or off
        for one standing Account — set_research_enabled writes the same
        column the issuance set, the audit log carries the actor and
        the new state, and the redirect re-renders the mirror with the
        پژوهش column showing the change. The sheet learns it on the
        Account's next /auth/me; a live guest research turn cannot
        exist to disturb (the gate never opened one)."""
        actor = self._admin_gate()
        if actor is None:
            return
        form = self._read_form()
        email = (form.get("email") or "").strip()
        state = (form.get("state") or "").strip()
        if not email or state not in ("on", "off"):
            self._send_redirect(f"/admin?error={ADMIN_ERROR_BAD_BODY}")
            return
        if not set_research_enabled(email, state == "on"):
            self._send_redirect(f"/admin?error={ADMIN_ERROR_UNKNOWN_ACCOUNT}")
            return
        audit_quiet(
            audit.RESEARCH_ACCESS_CHANGED,
            actor_email=actor["email"],
            detail={"email": email, "research_enabled": state == "on"},
        )
        self._send_redirect("/admin")

    def _admin_topup(self) -> None:
        """The console's top-up form (T26, GitLab #28): Toman lands on
        the named Account's Balance (credit_balance — email-keyed, the
        same row the operator's own reads answer from), the audit log
        carries the actor, the amount, and the new balance, and the
        redirect re-renders the mirror with the new اعتبار showing."""
        account = self._admin_gate()
        if account is None:
            return
        form = self._read_form()
        email = (form.get("email") or "").strip()
        try:
            amount = int((form.get("amount") or "").strip())
        except ValueError:
            amount = 0
        if amount <= 0:
            self._send_redirect(f"/admin?error={ADMIN_ERROR_BAD_AMOUNT}")
            return
        new_balance = credit_balance(email, amount)
        if new_balance is None:
            self._send_redirect(f"/admin?error={ADMIN_ERROR_UNKNOWN_ACCOUNT}")
            return
        audit_quiet(
            audit.BALANCE_TOPPED,
            actor_email=account["email"],
            detail={
                "email": email,
                "amount_toman": amount,
                "new_balance_toman": new_balance,
            },
        )
        self._send_redirect("/admin")

    def _admin_attach(self) -> None:
        """The console's attach form (T21, GitLab #23): the Admin
        attaches each legacy phone to the Account created for it — the
        mapping ui/migrate.py rekeys the pre-account stores by — and
        the rekey runs immediately, so the demo is attach, redirect,
        and the old rows read under their Account. Audited
        (phone_attached), admin-only, refused with the whitelisted
        codes and logged with nothing."""
        actor = self._admin_gate()
        if actor is None:
            return
        form = self._read_form()
        email = (form.get("email") or "").strip()
        phone = normalize_phone(form.get("phone") or "")
        if not email or not phone:
            self._send_redirect(f"/admin?error={ADMIN_ERROR_BAD_BODY}")
            return
        if not attach_phone(email, phone):
            self._send_redirect(f"/admin?error={ADMIN_ERROR_UNKNOWN_ACCOUNT}")
            return
        report = migrate.rekey_stores()
        audit_quiet(
            audit.PHONE_ATTACHED,
            actor_email=actor["email"],
            detail={"email": email, "phone": phone, "rekeyed": report},
        )
        self._send_redirect("/admin")

    def _profile_data(self) -> None:
        """The profile read (T24, GitLab #27): the Account's own Balance
        (اعتبار) straight from the store, the server-local day's spend,
        and the spend history grouped into Sessions by the ledger's
        session_history — the open Session first. The cookie IS the
        address: there is no id parameter at all, so a caller — whatever
        it sends — can only ever read its own Account's numbers, the
        same shape /usage/live answers with. Each entry's honesty badge
        rides the data (the DRAFT constants above): the sheet renders
        the meter's own verdict, it never judges metered against
        estimated itself, so an estimate can never look measured."""
        account = resolve_identity(self)
        if account is None:
            return
        sessions = session_history(account)
        for session in sessions:
            for entry in session["entries"]:
                entry["badge"] = (
                    PROFILE_METERED_BADGE
                    if entry["metered"]
                    else PROFILE_ESTIMATED_BADGE
                )
        self._send_json(
            200,
            {
                "balance_toman": get_balance(account),
                "today_toman": today_total(account),
                "sessions": sessions,
            },
        )

    def _sessions_list(self) -> None:
        """The sidebar's read (T27 stage 3, GitLab #40): the caller's
        Sessions, newest activity first. The cookie IS the address, so
        the list is only ever the caller's own; the store caps the
        payload, not the history (the accepted list-v1 shape)."""
        account = resolve_identity(self)
        if account is None:
            return
        self._send_json(200, {"sessions": session_store.list_sessions(account)})

    def _session_get(self, session_id: int) -> None:
        """One Session with its stored messages oldest→newest — the
        resume read. Another Account's Session answers 404, the same
        silence the store gives; the sheet renders the transcript
        read-only. The payload names the sitting's newest research
        session (ADR-0016): the linkage a reload or a sidebar round-trip
        needs to raise the research trail beside the chat again — until
        the linkage landed, the trail's only address lived in the
        browser's sessionStorage and died at the first sidebar click."""
        account = resolve_identity(self)
        if account is None:
            return
        session = session_store.get_session(account, session_id)
        if session is None:
            self._json_error(404, "نشست پیدا نشد.")
            return
        session["research_session_id"] = research_store.latest_for_chat_session(
            account, str(session_id)
        )
        self._send_json(200, session)

    def _session_create(self) -> None:
        """Open one Session (T27 stage 3): the sheet creates it on the
        sitting's first ask, sending the Book and the truncated title.
        The store judges neither — the ledger stays the money truth."""
        account = resolve_identity(self)
        if account is None:
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            book = payload.get("book")
            title = payload.get("title")
            if book is not None and not isinstance(book, str):
                raise ValueError
            if title is not None and not isinstance(title, str):
                raise ValueError
        except (ValueError, TypeError, json.JSONDecodeError):
            self._send_json(400, {"detail": "درخواست نادرست است."})
            return
        session = session_store.create_session(account, book or "", title or "")
        self._send_json(200, session)

    def _session_append(self, session_id: int) -> None:
        """Append one turn to the caller's Session (T27 stage 3): the
        sheet reports its settled turns — the operator's ask, the
        assistant's article — as {role, payload}; the store renders
        nothing and judges nothing. A foreign Session is 404."""
        account = resolve_identity(self)
        if account is None:
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            role = payload["role"]
            body = payload["payload"]
            if role not in ("user", "assistant") or not isinstance(body, dict):
                raise ValueError
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            self._send_json(400, {"detail": "درخواست نادرست است."})
            return
        session = session_store.append_message(account, session_id, role, body)
        if session is None:
            # The body is already read above, so _send_json is safe —
            # _json_error would drain again and block on taken bytes
            # (the _evidence_fallback rule).
            self._send_json(404, {"detail": "نشست پیدا نشد."})
            return
        self._send_json(200, session)

    def _session_delete(self, session_id: int) -> None:
        """Delete the caller's Session whole (list-v1: delete only). The
        transcript goes; the ledger's spend rows stay — the profile's
        and the console's numbers never falsify."""
        account = resolve_identity(self)
        if account is None:
            return
        if not session_store.delete_session(account, session_id):
            self._json_error(404, "نشست پیدا نشد.")
            return
        self._send_json(200, {"deleted": True})

    # ---- The Notebook (the selection map, ticket 08) ----
    # The researcher's capture: the popover quick-saves, the panel
    # reads/edits/deletes. Ownership is the cookie, as everywhere.

    @staticmethod
    def _note_fields(payload: dict) -> dict:
        """The create payload's shape: the quote is required; the book
        side (doc/pages/refs/source) and the editable defaults ride
        optional. Anything else the client sends is ignored — the store
        judges the shapes it knows."""
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError
        doc = payload.get("doc")
        if doc is not None and not isinstance(doc, str):
            raise ValueError
        pages = payload.get("pages") or []
        refs = payload.get("refs") or []
        source = payload.get("source") or {}
        category = payload.get("category") or ""
        opinion = payload.get("opinion") or ""
        if not isinstance(pages, list) or not isinstance(refs, list):
            raise ValueError
        if not isinstance(source, dict):
            raise ValueError
        if not isinstance(category, str) or not isinstance(opinion, str):
            raise ValueError
        return {
            "text": text,
            "doc": doc,
            "pages": pages,
            "refs": refs,
            "source": source,
            "category": category,
            "opinion": opinion,
        }

    def _notes_list(self) -> None:
        account = resolve_identity(self)
        if account is None:
            return
        self._send_json(200, {"notes": note_store.list_notes(account)})

    def _note_create(self) -> None:
        account = resolve_identity(self)
        if account is None:
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            fields = self._note_fields(payload)
        except (ValueError, TypeError, json.JSONDecodeError):
            # The body is already read, so _send_json is safe (the
            # _evidence_fallback rule).
            self._send_json(400, {"detail": "درخواست نادرست است."})
            return
        self._send_json(200, note_store.create_note(account, **fields))

    def _note_update(self, note_id: int) -> None:
        """Edit the editable fields (category / opinion); the quote is
        fixed. A foreign note is 404."""
        account = resolve_identity(self)
        if account is None:
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            category = payload.get("category")
            opinion = payload.get("opinion")
            if category is not None and not isinstance(category, str):
                raise ValueError
            if opinion is not None and not isinstance(opinion, str):
                raise ValueError
        except (ValueError, TypeError, json.JSONDecodeError):
            self._send_json(400, {"detail": "درخواست نادرست است."})
            return
        note = note_store.update_note(
            account, note_id, category=category, opinion=opinion
        )
        if note is None:
            self._send_json(404, {"detail": "یادداشت پیدا نشد."})
            return
        self._send_json(200, note)

    def _note_delete(self, note_id: int) -> None:
        account = resolve_identity(self)
        if account is None:
            return
        if not note_store.delete_note(account, note_id):
            self._json_error(404, "یادداشت پیدا نشد.")
            return
        self._send_json(200, {"deleted": True})

    def _note_bulk_delete(self) -> None:
        account = resolve_identity(self)
        if account is None:
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            ids = payload.get("ids")
            if not isinstance(ids, list):
                raise ValueError
        except (ValueError, TypeError, json.JSONDecodeError):
            self._send_json(400, {"detail": "درخواست نادرست است."})
            return
        deleted = note_store.delete_notes_many(account, ids)
        self._send_json(200, {"deleted": deleted})


    # --- the ticket endpoints (the ticket-system map, 2026-10-05) ------
    # The feedback loop: the sheet chips an answer, POSTs the pointer,
    # and everything report-shaped is built HERE — the snapshot from
    # the Session store, the cost from the ledger's ask groups, the
    # attachment sniffed from magic bytes. The panel reads its own
    # tickets; the inbox is the console's sibling page. Filing rides no
    # balance and no quota: feedback is not spend.

    def _tickets_list(self) -> None:
        account = resolve_identity(self)
        if account is None:
            return
        self._send_json(
            200, {"tickets": ticket_store.list_tickets(account=account)}
        )

    def _tickets_unread(self) -> None:
        account = resolve_identity(self)
        if account is None:
            return
        self._send_json(200, {"unread_count": ticket_store.unread_count(account)})

    def _ticket_get(self, ticket_id: int) -> None:
        account = resolve_identity(self)
        if account is None:
            return
        ticket = ticket_store.get_ticket(ticket_id, account=account)
        if ticket is None:
            self._json_error(404, TICKET_NOT_FOUND_404_DETAIL)
            return
        # Opening the thread reads the manager's unread replies — the
        # badge and the inbox's «خوانده‌نشده» ride the same column.
        ticket_store.mark_admin_replies_read(ticket_id, account)
        ticket = ticket_store.get_ticket(ticket_id, account=account)
        self._send_json(200, {"ticket": ticket})

    def _ticket_file(self, ticket_id: int) -> None:
        """The attachment's one door: the owner or an Admin, never a
        bare static path — the bytes answer with the sniffed type and a
        private cache policy, and a ticket without an image is 404."""
        email = resolve_identity(self)
        if email is None:
            return
        ticket = ticket_store.get_ticket(ticket_id)
        if ticket is None or (
            ticket["account"] != email
            and (account_by_email(email) or {}).get("role") != "admin"
        ):
            self._json_error(404, TICKET_NOT_FOUND_404_DETAIL)
            return
        image = ticket_store.read_image(ticket_id)
        if image is None:
            self._json_error(404, TICKET_NOT_FOUND_404_DETAIL)
            return
        data, mime = image
        self._cache_policy = "private, max-age=600"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _ticket_create(self) -> None:
        account = resolve_identity(self)
        if account is None:
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            session_id = payload.get("session_id")
            category = payload.get("category")
            body = payload.get("body")
            message_id = payload.get("message_id")
            ask_key = payload.get("ask_key")
            if not isinstance(session_id, int):
                raise ValueError
            if message_id is None and (
                not isinstance(ask_key, str) or not ask_key.strip()
            ):
                # Neither pointer — nothing for the snapshot to name.
                raise ValueError
            if not isinstance(category, str) or not isinstance(body, str):
                raise ValueError
        except (ValueError, TypeError, json.JSONDecodeError):
            self._send_json(400, {"detail": TICKET_BAD_BODY_400_DETAIL})
            return
        session = session_store.get_session(account, session_id)
        if session is None:
            # The body is read — _send_json, never _json_error's second
            # drain (the drain-twice rule).
            self._send_json(404, {"detail": "نشست پیدا نشد."})
            return
        found = _ticket_snapshot_parts(
            session,
            message_id=message_id if isinstance(message_id, int) else None,
            ask_key=ask_key,
        )
        if found is None:
            self._send_json(404, {"detail": "پیام پیدا نشد."})
            return
        image = None
        if payload.get("image_b64") is not None:
            decoded = ticket_store.decode_image_b64(payload.get("image_b64"))
            if isinstance(decoded, str):
                self._send_json(400, {"detail": TICKET_BAD_IMAGE_DETAIL})
                return
            image = decoded
        result = ticket_store.create_ticket(
            account,
            session_id,
            found["message_id"],
            category,
            body,
            found["snapshot"],
            ask_key=found["ask_key"],
            mode=found["snapshot"]["mode"],
            turn_cost_toman=_turn_cost_best_effort(
                account, found["ts"]
            ),
            image=image,
        )
        if isinstance(result, str):
            if result == "soft_cap":
                audit_quiet(
                    audit.TICKET_SOFT_CAP,
                    actor_email=account,
                    detail={"open_count": ticket_store.SOFT_CAP},
                )
                self._send_json(429, {"detail": TICKET_SOFT_CAP_DETAIL})
            elif result == "short_body":
                self._send_json(400, {"detail": TICKET_SHORT_BODY_DETAIL})
            elif result == "bad_image":
                self._send_json(400, {"detail": TICKET_BAD_IMAGE_DETAIL})
            else:
                self._send_json(400, {"detail": TICKET_BAD_BODY_400_DETAIL})
            return
        audit_quiet(
            audit.TICKET_FILED,
            actor_email=account,
            detail={
                "ticket_id": result["id"],
                "category": result["category"],
                "session_id": session_id,
                "message_id": result["message_id"],
                "has_image": result["has_image"],
            },
        )
        self._send_json(200, {"ticket": result})

    def _ticket_reply(self, ticket_id: int) -> None:
        account = resolve_identity(self)
        if account is None:
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            body = payload.get("body")
            if not isinstance(body, str):
                raise ValueError
        except (ValueError, TypeError, json.JSONDecodeError):
            self._send_json(400, {"detail": TICKET_BAD_REPLY_DETAIL})
            return
        result = ticket_store.add_reply(
            ticket_id, "user", account, body
        )
        if isinstance(result, str):
            self._ticket_refusal(result)
            return
        audit_quiet(
            audit.TICKET_REPLIED,
            actor_email=account,
            detail={"ticket_id": ticket_id, "role": "user"},
        )
        self._send_json(200, {"ticket": result})

    def _ticket_close(self, ticket_id: int) -> None:
        account = resolve_identity(self)
        if account is None:
            return
        result = ticket_store.close_ticket(
            ticket_id, by_account=account
        )
        if isinstance(result, str):
            self._ticket_refusal(result)
            return
        audit_quiet(
            audit.TICKET_CLOSED,
            actor_email=account,
            detail={"ticket_id": ticket_id, "by": "user"},
        )
        self._send_json(200, {"ticket": result})

    def _ticket_reopen(self, ticket_id: int) -> None:
        account = resolve_identity(self)
        if account is None:
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            body = payload.get("body")
            if body is not None and not isinstance(body, str):
                raise ValueError
        except (ValueError, TypeError, json.JSONDecodeError):
            self._send_json(400, {"detail": TICKET_BAD_BODY_400_DETAIL})
            return
        result = ticket_store.reopen_ticket(
            ticket_id, account, body or ""
        )
        if isinstance(result, str):
            self._ticket_refusal(result)
            return
        audit_quiet(
            audit.TICKET_REOPENED,
            actor_email=account,
            detail={"ticket_id": ticket_id},
        )
        self._send_json(200, {"ticket": result})

    def _ticket_refusal(self, code: str) -> None:
        """One mapper for the store's refusal codes — every branch
        carries its own Farsi, none leaks a bare English code. The
        body is already read by the callers (the drain-twice rule), so
        this answers with _send_json, never _json_error."""
        details = {
            "not_found": (404, TICKET_NOT_FOUND_404_DETAIL),
            "closed": (409, TICKET_CLOSED_DETAIL),
            "not_closed": (409, "بازگشایی فقط برای تیکت بسته است."),
            "reopen_denied": (409, TICKET_REOPEN_DENIED_DETAIL),
            "bad_body": (400, TICKET_BAD_REPLY_DETAIL),
        }
        status, detail = details.get(code, (400, TICKET_BAD_BODY_400_DETAIL))
        self._send_json(status, {"detail": detail})

    # The inbox's pages and forms — the console's own conventions
    # (server-rendered, form posts answered 303, everything audited).

    def _send_html(self, document: str) -> None:
        body = document.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _admin_tickets_page(self, account_row) -> None:
        query = parse_qs(
            self.path.split("?", 1)[1] if "?" in self.path else ""
        )
        category = (query.get("category") or [""])[0]
        status = (query.get("status") or [""])[0]
        days = (query.get("days") or [""])[0]
        error_code = (query.get("error") or [""])[0]
        since = None
        if days.isdigit() and int(days) > 0:
            since = (
                datetime.datetime.now(datetime.timezone.utc)
                - datetime.timedelta(days=int(days))
            ).isoformat(timespec="seconds")
        tickets = ticket_store.list_tickets(
            account=None,
            category=category or None,
            status=status or None,
            since=since,
        )
        self._send_html(
            admin_inbox_html(
                tickets, category, status, days, error_code=error_code
            )
        )

    def _admin_ticket_page(self, account_row, ticket_id: int) -> None:
        ticket = ticket_store.get_ticket(ticket_id)
        if ticket is None:
            self._json_error(404, TICKET_NOT_FOUND_404_DETAIL)
            return
        self._send_html(admin_ticket_html(ticket))

    def _admin_ticket_transcript(self, account_row, ticket_id: int) -> None:
        ticket = ticket_store.get_ticket(ticket_id)
        if ticket is None:
            self._json_error(404, TICKET_NOT_FOUND_404_DETAIL)
            return
        session = session_store.get_session(
            ticket["account"], ticket["session_id"]
        )
        self._send_html(admin_transcript_html(ticket, session))

    def _admin_ticket_reply(self) -> None:
        actor = self._admin_gate()
        if actor is None:
            return
        form = self._read_form()
        raw_id = (form.get("ticket_id") or "").strip()
        body = (form.get("body") or "").strip()
        if not raw_id.isdigit() or not body:
            self._send_redirect("/admin/tickets?error=bad_body")
            return
        result = ticket_store.add_reply(
            int(raw_id), "admin", actor["email"], body
        )
        if isinstance(result, str):
            self._send_redirect(f"/admin/tickets?error={result}")
            return
        audit_quiet(
            audit.TICKET_REPLIED,
            actor_email=actor["email"],
            detail={"ticket_id": int(raw_id), "role": "admin"},
        )
        self._send_redirect(f"/admin/tickets/{raw_id}")

    def _admin_ticket_close(self) -> None:
        actor = self._admin_gate()
        if actor is None:
            return
        form = self._read_form()
        raw_id = (form.get("ticket_id") or "").strip()
        if not raw_id.isdigit():
            self._send_redirect("/admin/tickets?error=bad_body")
            return
        result = ticket_store.close_ticket(int(raw_id), by_admin=True)
        if isinstance(result, str):
            self._send_redirect(f"/admin/tickets?error={result}")
            return
        audit_quiet(
            audit.TICKET_CLOSED,
            actor_email=actor["email"],
            detail={"ticket_id": int(raw_id), "by": "admin"},
        )
        self._send_redirect(f"/admin/tickets/{raw_id}")

    def _admin_ticket_edit_reply(self) -> None:
        actor = self._admin_gate()
        if actor is None:
            return
        form = self._read_form()
        raw_reply = (form.get("reply_id") or "").strip()
        body = (form.get("body") or "").strip()
        if not raw_reply.isdigit() or not body:
            self._send_redirect("/admin/tickets?error=bad_body")
            return
        result = ticket_store.edit_reply(
            int(raw_reply), actor["email"], body
        )
        if isinstance(result, str):
            self._send_redirect(f"/admin/tickets?error={result}")
            return
        audit_quiet(
            audit.TICKET_REPLY_EDITED,
            actor_email=actor["email"],
            detail={"reply_id": int(raw_reply)},
        )
        self._send_redirect(f"/admin/tickets/{result['id']}")


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
        account = resolve_identity(self)
        if account is None:
            return
        query = parse_qs(urlparse(self.path).query)
        session_id = (query.get("session") or [""])[0]
        fmt = (query.get("format") or ["html"])[0]
        document, error = research_session_report(account, session_id, fmt=fmt)
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

    def _research_message(self, account: str) -> None:
        """The Research Mode message start (ADR-0008; gate per ADR-0015):
        the Balance is the prepaid stop and nothing else — the research
        conversation may be the sitting's (or the day's) first act, the
        composer toggle routes the typed question straight here. It
        never counts or checks the daily chat limit. The creating
        call carries the question (the session's founding goal) and —
        when a prior ask seeded one — its phase-1 Evidence pool; later
        calls carry only the text. The turn runs on its own registry
        job and this handler answers the turn identity immediately:
        202 {"turn_id", "session_id"} — the sheet polls /research/turn
        for state, events, and the reply. Nothing is ever queued: a
        busy Account (or a full registry) is rejected, not deferred."""
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
            # The Session-store linkage (ADR-0016): the sheet reports
            # which chat Session this research conversation belongs to,
            # so the trail survives a reload and a sidebar round-trip.
            # A malformed or foreign id never fails the ask — the
            # research is the user's act; the linkage is bookkeeping.
            chat_session_id = payload.get("chat_session_id")
            if chat_session_id is not None and not isinstance(
                chat_session_id, (int, str)
            ):
                chat_session_id = None
        except (ValueError, KeyError, TypeError):
            # The body is already read above, so _send_json is safe —
            # _json_error would drain a second time and block.
            self._send_json(400, {"detail": "پیام پژوهش را بفرستید."})
            return
        session, error = ensure_session(
            account, session_id, text.strip(), question, sources, datasets
        )
        if session is None:
            self._send_json(error[0], {"detail": error[1]})
            return
        if chat_session_id and not session_id:
            # The creating call only: a live session keeps whatever
            # sitting founded it, and the id rides only after the
            # ownership check — another Account's Session is invisible.
            try:
                owned = session_store.get_session(
                    account, int(chat_session_id)
                )
            except (TypeError, ValueError):
                owned = None
            if owned is not None:
                research_store.attach_chat_session(
                    session["id"], account, str(chat_session_id)
                )
        turn, busy_detail = start_research_turn(account, session, text.strip())
        if turn is None:
            self._send_json(429, {"detail": busy_detail})
            return
        self._send_json(202, {"turn_id": turn.id, "session_id": session["id"]})

    def _research_resume_from(self, account: str) -> None:
        """The closed session's fork door (decision 04): a STOPPED
        investigation continues in a fresh session id carrying its
        state — the old row never reopens, a stop stays honest. The
        sheet adopts the returned session id as its research
        conversation."""
        length = int(self.headers.get("Content-Length", "0") or "0")
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            session_id = payload["session_id"]
            if not isinstance(session_id, str) or not session_id.strip():
                raise ValueError("session_id is required")
        except (ValueError, KeyError, TypeError):
            self._send_json(400, {"detail": "نشست پژوهشِ بسته را بفرستید."})
            return
        forked, error = resume_closed_session(account, session_id.strip())
        if forked is None:
            self._send_json(error[0], {"detail": error[1]})
            return
        self._send_json(200, forked)

    def _research_decide(self, account: str) -> None:
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
            account,
            session_id.strip(),
            proposal_id.strip(),
            accept,
            choice=(choice or None),
        )
        if result is None:
            self._send_json(error[0], {"detail": error[1]})
            return
        self._send_json(200, result)

    def _research_plan_item(self, account: str) -> None:
        """The per-item plan door (decision 03): one accepted plan's
        section is accepted, rejected, or edited by its stable key —
        synchronous bookkeeping, no upstream call, nothing to meter. A
        user's own steering never arms a cooldown."""
        length = int(self.headers.get("Content-Length", "0") or "0")
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            session_id = payload["session_id"]
            key = payload["key"]
            status = payload["status"]
            edited = payload.get("edited")
            if not isinstance(session_id, str) or not session_id.strip():
                raise ValueError("session_id is required")
            if not isinstance(key, str) or not key.strip():
                raise ValueError("key is required")
            if not isinstance(status, str):
                raise ValueError("status is required")
            if edited is not None and not isinstance(edited, str):
                raise ValueError("edited must be a string")
        except (ValueError, KeyError, TypeError):
            self._send_json(400, {"detail": "تصمیم بخش برنامه را بفرستید."})
            return
        result, error = decide_plan_item(
            account,
            session_id.strip(),
            key.strip(),
            status.strip(),
            edited=(edited or None),
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

    def _proxy(self, method: str, account: str = "") -> None:
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
            # browser never names an upstream dataset outside it. The
            # account rides for the follow-up rewrite; the meter taps
            # that one composer call (a rewrite is real spend and the
            # ledger watches it like every other).
            set_meter(
                lambda prompt, reply: record_composer_call(
                    account, "composer", prompt, reply
                )
            )
            try:
                patched = self._validated_recall_body(body, account)
            finally:
                set_meter(None)
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

    def _validated_recall_body(self, raw: bytes, account: str = ""):
        """The recall POST's patched body bytes, or None after answering
        400. The body is already read here, so rejections use _send_json
        (never the draining _json_error — a second read would block).
        The sitting's id rides to the Session store, never to Cognee;
        a short follow-up over an existing sitting is rewritten into a
        self-contained query before the relay (ADR-0015) — any failure
        answers the raw question."""
        try:
            payload = json.loads(raw or b"{}")
            query = payload["query"]
            if not isinstance(query, str) or not query.strip():
                raise ValueError("query is required")
        except (ValueError, KeyError, TypeError):
            self._send_json(400, {"detail": "پرسش را بنویسید."})
            return None
        session_id = payload.pop("session_id", None)
        payload.pop("history", None)
        query = self._contextual_query(account, session_id, query)
        payload["query"] = query
        datasets = validated_datasets(payload.get("datasets"))
        if datasets is not None:
            payload["datasets"] = datasets
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def _contextual_query(self, account: str, session_id, query: str) -> str:
        """A follow-up's self-contained search query (ADR-0015, ADR-0019):
        when the sitting has earlier turns and the question is
        follow-up-shaped (at most REWRITE_MAX_WORDS words), ONE fast
        glm-5.3-flash call rewrites it over the sitting's recent turns —
        «بیشتر توضیح بده» alone retrieves noise, the rewritten form
        retrieves the subject under discussion; a self-contained message
        rides back unchanged (the rewriter's own instruction). Every
        failure is the raw question: the store silent, the endpoint down,
        the short timeout — the rewrite is a retrieval hint, never a
        gate, and the sheet's displayed question is always the user's own
        words."""
        history = _conversation_tail(account, session_id)
        if not history or len(query.split()) > REWRITE_MAX_WORDS:
            return query
        try:
            return rewrite_followup_query(query, history)
        except Exception:
            sys.stderr.write("follow-up rewrite failed; raw query rides\n")
            return query


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
    # The first Admin from compose env (T26, GitLab #28): the seed
    # command is retired — the deployment itself plants the PM's
    # Account, once, loudly.
    ensure_first_admin_from_env()
    # The store migration (T21, GitLab #23): every attached phone's
    # legacy rows rekey to their Account before the first request —
    # ADR-0013's contract step, run loudly.
    report = migrate.rekey_stores()
    sys.stderr.write(f"store migration: {report}\n")
    # The Book PDFs' presence at boot (the 09-28 regression: a deploy
    # swap dropped the gitignored PDFs and every cold page render 404'd
    # while the warm render-cache pages kept drawing — say so loudly).
    for dataset in BOOK_DATASETS:
        if (BOOKS_DIR / f"{dataset}.pdf").exists():
            sys.stderr.write(f"book pdf ok: {dataset}\n")
        else:
            sys.stderr.write(
                f"BOOK PDF MISSING: {dataset} — the reader can draw only already-cached pages\n"
            )
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
