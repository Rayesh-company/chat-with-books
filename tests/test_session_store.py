"""The Session store (T27 stage 3, GitLab #40): the Account's durable
نشست history. The store is a notebook the SHEET writes — create on the
sitting's first ask, the settled turns appended as {role, payload},
the list newest-activity-first, the resume read-only, the delete whole.
The gate's shape holds everywhere: the cookie IS the address, a
foreign Session is a 404 (never a leak), an anonymous caller is a 401,
and deleting a Session never touches the ledger — the money truth is
not the transcript's to erase."""

from pathlib import Path

import json

from tests.helpers import (
    account_email_for_phone,
    POOL,
    FakeComposer,
    delete,
    get,
    post,
    stop_gate,
    with_gate,
)
from tests.test_quoted_answer import (  # noqa: E402
    PLAN_MARKER,
    WRITER_KEPT,
    planner_reply,
    writer_reply,
)
from tests.test_quote_selection import (  # noqa: E402
    picker_reply,
    verbatim_selections,
)
from ui import ledger, serve, session_store
from ui.ledger import record, record_size_estimate

PHONE = "09120000000"
OTHER = "09350000000"

ASK = {"text": "چهار دروازهٔ انسان ۲۵۰ ساله کدام‌اند؟"}
ANSWER = {
    "question": ASK["text"],
    "blocks": [
        {"type": "heading", "text": "دروازۀ نخست"},
        {"type": "paragraph", "text": "انسان در حرکت است…"},
    ],
    "citations": [{"reference": "انسان ۲۵۰ ساله، ص ۳۴", "passage": "…"}],
}


def _ask_and_answer(base, question_text=ASK["text"]):
    """One stored turn pair: the sheet's create-then-append shape."""
    status, session = post(
        base,
        "/sessions",
        {"book": "70143-336", "title": question_text[:42]},
        phone=PHONE,
    )
    assert status == 200
    status, _ = post(
        base,
        f"/sessions/{session['id']}/messages",
        {"role": "user", "payload": {"text": question_text}},
        phone=PHONE,
    )
    assert status == 200
    status, _ = post(
        base,
        f"/sessions/{session['id']}/messages",
        {"role": "assistant", "payload": ANSWER},
        phone=PHONE,
    )
    assert status == 200
    return session["id"]


# --- the endpoints -----------------------------------------------------------


def test_the_anonymous_caller_refuses(tmp_path):
    base, server, originals = with_gate(tmp_path, None)
    try:
        assert get(base, "/sessions")[0] == 401
        assert post(base, "/sessions", {"book": ""})[0] == 401
        assert post(base, "/sessions/1/messages", {"role": "user", "payload": {}})[0] == 401
        assert delete(base, "/sessions/1")[0] == 401
    finally:
        stop_gate(server, originals)


def test_the_sitting_opens_titled_and_lists_newest_first(tmp_path):
    base, server, originals = with_gate(tmp_path, None)
    try:
        first_id = _ask_and_answer(base, "نخستین پرسشِ نشست اول")
        second_id = _ask_and_answer(base, "پرسشِ نشست دوم")
        status, body = get(base, "/sessions", phone=PHONE)
        assert status == 200
        rows = body["sessions"]
        assert [row["id"] for row in rows][:2] == [second_id, first_id], (
            "the newest activity leads"
        )
        assert rows[0]["title"] == "پرسشِ نشست دوم"
        assert rows[0]["book"] == "70143-336"
    finally:
        stop_gate(server, originals)


def test_the_resume_read_carries_the_turns_in_order(tmp_path):
    base, server, originals = with_gate(tmp_path, None)
    try:
        session_id = _ask_and_answer(base)
        status, body = get(base, f"/sessions/{session_id}", phone=PHONE)
        assert status == 200
        assert body["title"] == ASK["text"][:42]
        roles = [message["role"] for message in body["messages"]]
        assert roles == ["user", "assistant"]
        assert body["messages"][0]["payload"]["text"] == ASK["text"]
        assert body["messages"][1]["payload"]["blocks"] == ANSWER["blocks"]
    finally:
        stop_gate(server, originals)


def test_a_foreign_session_is_a_404_never_a_leak(tmp_path):
    base, server, originals = with_gate(tmp_path, None)
    try:
        session_id = _ask_and_answer(base)
        # Another Account sees neither the read, the append, nor the delete.
        assert get(base, f"/sessions/{session_id}", phone=OTHER)[0] == 404
        assert post(
            base,
            f"/sessions/{session_id}/messages",
            {"role": "user", "payload": {"text": "نفوذ"}},
            phone=OTHER,
        )[0] == 404
        assert delete(base, f"/sessions/{session_id}", phone=OTHER)[0] == 404
        # The owner's transcript is untouched by all three attempts.
        status, body = get(base, f"/sessions/{session_id}", phone=PHONE)
        assert status == 200
        assert len(body["messages"]) == 2
    finally:
        stop_gate(server, originals)


def test_the_delete_removes_the_transcript_whole(tmp_path):
    base, server, originals = with_gate(tmp_path, None)
    try:
        session_id = _ask_and_answer(base)
        status, body = delete(base, f"/sessions/{session_id}", phone=PHONE)
        assert status == 200 and body["deleted"] is True
        assert get(base, f"/sessions/{session_id}", phone=PHONE)[0] == 404
        assert get(base, "/sessions", phone=PHONE)[1]["sessions"] == []
    finally:
        stop_gate(server, originals)


def test_a_malformed_append_refuses(tmp_path):
    base, server, originals = with_gate(tmp_path, None)
    try:
        status, session = post(
            base, "/sessions", {"book": "70143-336"}, phone=PHONE
        )
        assert status == 200
        assert (
            post(
                base,
                f"/sessions/{session['id']}/messages",
                {"role": "admin", "payload": {}},
                phone=PHONE,
            )[0]
            == 400
        )
        assert (
            post(
                base,
                f"/sessions/{session['id']}/messages",
                {"role": "user"},
                phone=PHONE,
            )[0]
            == 400
        )
    finally:
        stop_gate(server, originals)


def test_deleting_a_session_leaves_the_ledger_untouched(tmp_path):
    """The transcript is not the money's ledger: spend rows recorded
    against the sitting survive the delete — the profile's and the
    console's numbers never falsify."""
    base, server, originals = with_gate(tmp_path, None)
    try:
        record_size_estimate("09120000000@sheet.test", "ask", 900)
        record("09120000000@sheet.test", "writer", 0, 250_000, metered=True)
        session_id = _ask_and_answer(base)
        assert delete(base, f"/sessions/{session_id}", phone=PHONE)[0] == 200
        history = ledger.session_history("09120000000@sheet.test")
        assert history and history[0]["cost_toman"] > 0
    finally:
        stop_gate(server, originals)


def test_the_sheet_boots_the_list_and_reopens_the_sitting():
    """The refresh continuity (the operator's report, 2026-09-22): the
    sidebar lists on page load and the sitting reopens — the light
    source-lock holds the sheet's boot to the store's shape (the JS the
    tests cannot execute)."""
    html = (Path(session_store.__file__).parent / "index.html").read_text(
        encoding="utf-8"
    )
    # The boot block exists and lists before anything else can.
    assert 'sessionStorage.getItem("storeSessionId")' in html
    assert "rememberStoreSession" in html
    # The continuity writes ride the three state changes (create, open,
    # clear) — the boot reads what they wrote.
    assert html.count("rememberStoreSession();") >= 3
    # The boot fetches the list and reopens: newest as the fallback.
    assert 'fetch("/sessions", { cache: "no-store" })' in html
    assert "openStoreSession(target.id)" in html
    # The unanswered tail: a resumed sitting whose last turn is the
    # operator's question shows the honest note and the explicit re-ask
    # — never an automatic re-billing.
    assert "UNANSWERED_NOTE" in html and "REASK_LABEL" in html
    assert 'again.textContent = REASK_LABEL' in html
    assert 'last.role === "user"' in html

# --- the server-side per-ask settle (vanishing-content fix, 2026-09-24) ------


SNAPSHOT = {
    "question": ASK["text"],
    "blocks": [
        {
            "type": "paragraph",
            "parts": [{"quote": "انسان در حرکت است…", "reference": "r (page 3)"}],
        }
    ],
    "citations": [],
    # The marker /quote-selection rides on every snapshot it settles
    # (the store's downgrade rule reads it — the arrive-order race).
    "selection_snapshot": True,
}
ARTICLE = {
    "question": ASK["text"],
    "blocks": WRITER_KEPT,
    "truncated": False,
    "citations": POOL,
}


def test_settle_ask_inserts_then_upgrades_in_place(tmp_path):
    # The picker's snapshot INSERTS the ask's assistant row; the phase-2
    # article for the SAME ask_key UPDATES that row — the transcript
    # keeps ONE answer per ask, and the latest content wins.
    session_store.SESSIONS_DB = tmp_path / "sessions.sqlite3"
    session_id = session_store.create_session(PHONE, "70143-336", "")["id"]
    settled = session_store.settle_ask(PHONE, session_id, "ask-1", SNAPSHOT)
    assert settled is not None
    rows = [m for m in settled["messages"] if m["role"] == "assistant"]
    assert len(rows) == 1 and rows[0]["payload"] == SNAPSHOT
    settled = session_store.settle_ask(PHONE, session_id, "ask-1", ARTICLE)
    rows = [m for m in settled["messages"] if m["role"] == "assistant"]
    assert len(rows) == 1 and rows[0]["payload"] == ARTICLE
    # A different ask settles its OWN row.
    settled = session_store.settle_ask(PHONE, session_id, "ask-2", ARTICLE)
    rows = [m for m in settled["messages"] if m["role"] == "assistant"]
    assert len(rows) == 2


def test_settle_ask_refuses_foreign_and_empty_keys(tmp_path):
    session_store.SESSIONS_DB = tmp_path / "sessions.sqlite3"
    session_id = session_store.create_session(PHONE, "", "")["id"]
    # A foreign Account's settle is the same silence as every gated read.
    assert session_store.settle_ask(OTHER, session_id, "ask-1", SNAPSHOT) is None
    # An empty ask_key has nothing to be idempotent about.
    assert session_store.settle_ask(PHONE, session_id, "", SNAPSHOT) is None
    assert session_store.settle_ask(PHONE, session_id, "  ", SNAPSHOT) is None
    settled = session_store.get_session(PHONE, session_id)
    assert [m for m in settled["messages"] if m["role"] == "assistant"] == []


def test_the_phase_endpoints_settle_the_sitting_server_side(tmp_path):
    # /quote-selection settles the FIRST answer's snapshot and
    # /quoted-answer upgrades it to the article — both keyed by the
    # ask, both WITHOUT the sheet reporting anything afterwards.
    composer = FakeComposer(
        [
            picker_reply(verbatim_selections(4)),
            planner_reply(PLAN_MARKER),
            writer_reply(),
        ]
    )
    base, server, originals = with_gate(tmp_path, composer)
    try:
        serve.record_chat(account_email_for_phone(PHONE))
        status, session = post(
            base, "/sessions", {"book": "", "title": ""}, phone=PHONE
        )
        assert status == 200
        session_id = session["id"]
        post(
            base,
            f"/sessions/{session_id}/messages",
            {"role": "user", "payload": {"text": ASK["text"]}},
            phone=PHONE,
        )
        ask_key = "ask-1727100000000"
        account = account_email_for_phone(PHONE)
        status, _ = post(
            base,
            "/quote-selection",
            {
                "question": ASK["text"],
                "sources": POOL,
                "session_id": session_id,
                "ask_key": ask_key,
            },
            phone=PHONE,
        )
        assert status == 200
        mid = session_store.get_session(account, session_id)
        assistant = [m for m in mid["messages"] if m["role"] == "assistant"]
        assert len(assistant) == 1
        assert assistant[0]["payload"]["selection_snapshot"] is True
        status, article = post(
            base,
            "/quoted-answer",
            {
                "question": ASK["text"],
                "sources": POOL,
                "session_id": session_id,
                "ask_key": ask_key,
            },
            phone=PHONE,
        )
        assert status == 200
        final = session_store.get_session(account, session_id)
        assistant = [m for m in final["messages"] if m["role"] == "assistant"]
        # ONE row per ask — the store records EXACTLY the reply the
        # phase carried: the same blocks, question, and pool, with the
        # snapshot's marker gone.
        assert len(assistant) == 1
        stored = assistant[0]["payload"]
        assert stored["question"] == ASK["text"]
        assert stored["blocks"] == article["blocks"]
        assert stored["truncated"] == article["truncated"]
        assert stored["citations"] == POOL
        assert "selection_snapshot" not in stored
    finally:
        stop_gate(server, originals)


def test_the_phases_stay_silent_without_the_sitting_keys(tmp_path):
    # Bodies without session_id/ask_key behave exactly as before: the
    # phases reply, the store never hears of it.
    composer = FakeComposer(
        [
            picker_reply(verbatim_selections(4)),
            planner_reply(PLAN_MARKER),
            writer_reply(),
        ]
    )
    base, server, originals = with_gate(tmp_path, composer)
    try:
        serve.record_chat(account_email_for_phone(PHONE))
        status, _ = post(
            base,
            "/quote-selection",
            {"question": ASK["text"], "sources": POOL},
            phone=PHONE,
        )
        assert status == 200
        status, _ = post(
            base,
            "/quoted-answer",
            {"question": ASK["text"], "sources": POOL},
            phone=PHONE,
        )
        assert status == 200
        con = session_store._connect()
        try:
            rows = con.execute(
                "SELECT COUNT(*) FROM session_messages"
            ).fetchone()[0]
        finally:
            con.close()
        assert rows == 0
    finally:
        stop_gate(server, originals)


# --- the settle's shape precedence (the arrive-order race, 2026-09-27) --------


def test_settle_ask_never_downgrades_the_stored_answer(tmp_path):
    """The two phase endpoints settle the same ask_key from two
    concurrent handlers and arrival order is not guaranteed: a slow
    picker's quote-only snapshot landing after the article used to
    overwrite it (the reload then showed only quotes and page labels —
    the operator's single-use answer report), and a failed phase 2's
    empty blocks erased the row entirely. The store now judges shapes,
    not arrival: the article always wins, an empty write never
    erases."""
    session_store.SESSIONS_DB = tmp_path / "sessions.sqlite3"
    session_id = session_store.create_session(PHONE, "70143-336", "")["id"]
    # The article settles first; the late snapshot cannot take it back.
    session_store.settle_ask(PHONE, session_id, "ask-1", ARTICLE)
    settled = session_store.settle_ask(PHONE, session_id, "ask-1", SNAPSHOT)
    rows = [m for m in settled["messages"] if m["role"] == "assistant"]
    assert len(rows) == 1 and rows[0]["payload"] == ARTICLE
    # A failed phase 2's empty write cannot erase it either.
    emptied = dict(ARTICLE, blocks=[])
    session_store.settle_ask(PHONE, session_id, "ask-1", emptied)
    settled = session_store.get_session(PHONE, session_id)
    rows = [m for m in settled["messages"] if m["role"] == "assistant"]
    assert len(rows) == 1 and rows[0]["payload"] == ARTICLE
    # Snapshot over snapshot keeps latest-wins (two picker re-runs).
    second_snapshot = dict(SNAPSHOT, question="بازپرسشِ همان پرسش")
    session_store.settle_ask(PHONE, session_id, "ask-2", SNAPSHOT)
    settled = session_store.settle_ask(
        PHONE, session_id, "ask-2", second_snapshot
    )
    rows = [m for m in settled["messages"] if m["role"] == "assistant"]
    assert [r["payload"] for r in rows] == [ARTICLE, second_snapshot]


# --- the follow-up thread (ADR-0015) ------------------------------------------


REWRITE_CONTENT = "دروازه‌های چهارگانهٔ انسان ۲۵۰ ساله چیست؟"


class RewriteUpstream:
    """Tells the composer (the rewrite call) from Cognee (the relay) by
    URL, and captures what the relay finally received — the rewritten
    query's ride and the sitting id's absence are the test's subject."""

    def __init__(self):
        self.composer_bodies = []
        self.relay_bodies = []

    def __call__(self, request, timeout=None):
        url = request.full_url
        body = request.data.decode("utf-8") if request.data else ""
        if "chat/completions" in url:
            self.composer_bodies.append(json.loads(body))
            payload = json.dumps(
                {
                    "choices": [
                        {
                            "message": {"content": REWRITE_CONTENT},
                            "finish_reason": "stop",
                        }
                    ]
                }
            ).encode("utf-8")
        else:
            self.relay_bodies.append(json.loads(body))
            payload = b"[]"

        class Response:
            status = 200
            headers = {"Content-Type": "application/json"}

            def __init__(self, body):
                self._body = body

            def read(self):
                return self._body

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return Response(payload)


def _sitting_with_a_turn(base):
    """One stored turn pair: the raw material the rewrite reads."""
    status, session = post(
        base, "/sessions", {"book": "70143-336", "title": ""}, phone=PHONE
    )
    assert status == 200
    session_id = session["id"]
    status, _ = post(
        base,
        f"/sessions/{session_id}/messages",
        {"role": "user", "payload": {"text": ASK["text"]}},
        phone=PHONE,
    )
    assert status == 200
    status, _ = post(
        base,
        f"/sessions/{session_id}/messages",
        {"role": "assistant", "payload": ARTICLE},
        phone=PHONE,
    )
    assert status == 200
    return session_id


def test_a_short_followup_is_rewritten_over_the_sitting(tmp_path):
    """The follow-up thread (ADR-0015, gate widened by ADR-0019): with
    an existing sitting, a follow-up-shaped question is rewritten into
    a self-contained query over the sitting's stored turns BEFORE the
    relay — the sitting's id never rides upstream. A mid-length
    clarification — over the old eight-word gate, under the new
    twenty-five — rewrites too; a self-contained question beyond the
    gate rides raw with no rewrite call at all, and the rewrite's
    spend is metered like every composer call."""
    upstream = RewriteUpstream()
    base, server, originals = with_gate(tmp_path, upstream)
    try:
        session_id = _sitting_with_a_turn(base)
        status, _ = post(
            base,
            "/api/v1/recall",
            {
                "query": "بیشتر توضیح بده",
                "session_id": session_id,
                "datasets": ["70143-336"],
            },
            phone=PHONE,
        )
        assert status == 200
        assert len(upstream.composer_bodies) == 1
        prompt = upstream.composer_bodies[0]["messages"][0]["content"]
        assert ASK["text"] in prompt and "بیشتر توضیح بده" in prompt
        assert len(upstream.relay_bodies) == 1
        relayed = upstream.relay_bodies[0]
        assert relayed["query"] == REWRITE_CONTENT
        assert "session_id" not in relayed
        # A mid-length clarification rides the widened gate too — the
        # band past the old eight words is where real refinements live.
        mid_query = (
            "نه منظورم فصل دوم همان کتاب بود لطفا با ذکر مثال توضیح بده"
        )
        assert 8 < len(mid_query.split()) <= serve.REWRITE_MAX_WORDS
        status, _ = post(
            base,
            "/api/v1/recall",
            {"query": mid_query, "session_id": session_id},
            phone=PHONE,
        )
        assert status == 200
        assert len(upstream.composer_bodies) == 2
        assert len(upstream.relay_bodies) == 2
        assert upstream.relay_bodies[1]["query"] == REWRITE_CONTENT
        assert "session_id" not in upstream.relay_bodies[1]
        # A self-contained question beyond the gate is its own query —
        # the rewriter sleeps.
        long_query = (
            "این یک پرسش بلند و خودبسنده با موضوعی روشن است که هیچ "
            "ارجاعی به پرسش پیشین خود ندارد و نیازی به بازنویسی ندارد "
            "پس عینا به موتور جست‌وجو فرستاده می شود"
        )
        assert len(long_query.split()) > serve.REWRITE_MAX_WORDS
        status, _ = post(
            base,
            "/api/v1/recall",
            {"query": long_query, "session_id": session_id},
            phone=PHONE,
        )
        assert status == 200
        assert len(upstream.composer_bodies) == 2
        assert upstream.relay_bodies[2]["query"] == long_query
    finally:
        stop_gate(server, originals)


def test_the_sittings_tail_rides_the_picker_planner_and_writer(tmp_path):
    """ADR-0019: the follow-up thread rides every generation surface —
    the picker (the first answer the user sees), the planner, and the
    writer all read the sitting's tail as framing. The stored turn's
    words are the marker: the follow-up's question is different, so
    their presence in each composer prompt is the tail's ride."""
    composer = FakeComposer(
        [
            picker_reply(verbatim_selections(4)),
            planner_reply(PLAN_MARKER),
            writer_reply(),
        ]
    )
    base, server, originals = with_gate(tmp_path, composer)
    try:
        serve.record_chat(account_email_for_phone(PHONE))
        session_id = _sitting_with_a_turn(base)
        followup = "بیشتر دربارهٔ دروازۀ دوم توضیح بده"
        ask_key = "ask-1727100000001"
        status, _ = post(
            base,
            "/quote-selection",
            {
                "question": followup,
                "sources": POOL,
                "session_id": session_id,
                "ask_key": ask_key,
            },
            phone=PHONE,
        )
        assert status == 200
        status, _ = post(
            base,
            "/quoted-answer",
            {
                "question": followup,
                "sources": POOL,
                "session_id": session_id,
                "ask_key": ask_key,
            },
            phone=PHONE,
        )
        assert status == 200
        assert len(composer.payloads) == 3
        prompts = [p["messages"][0]["content"] for p in composer.payloads]
        for prompt in prompts:  # picker, planner, writer
            assert "framing only" in prompt
            assert ASK["text"] in prompt
        # The stored answer's connective text rides too — the writer's
        # "unhappy with the answer" signal.
        marker = "پیش از هر چیز باید معنای واژه را روشن کرد"
        assert all(marker in prompt for prompt in prompts)
    finally:
        stop_gate(server, originals)


def test_research_starts_without_a_prior_chat(tmp_path):
    """ADR-0015: the research conversation may be the day's first act.
    The old minimum-of-one-chat precondition answered 429 «اول یک پرسش
    بپرسید» before the body was even read; the Balance-only gate lets a
    malformed body reach its validation (400), and the toggle-first
    sitting starts."""
    base, server, originals = with_gate(tmp_path, None)
    try:
        status, body = post(
            base, "/research/message", {"text": ""}, phone=PHONE
        )
        assert status == 400
        assert "اول یک پرسش" not in str(body)
    finally:
        stop_gate(server, originals)
