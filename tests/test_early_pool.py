"""The early-pool sheet + the streamed Quoted answer (2026-10-08).

The sheet's ask now fires the retrieval-only /ask door BESIDE the
recall stream, so the pool the picker and phase 2 read lands in
seconds and the two composer phases start before the streamed
completion's `final` — and /quoted-answer grew a streamed twin whose
guarded blocks ride SSE `block` events while the writer still writes.
The locks: the incremental block scanner, the streaming composer's
reply shape, the server's SSE contract, the companion quota semantics
(the cap checked on both legs, the increment on one), the /ask leg's
shared follow-up rewrite, and the sheet's own wiring."""

import json
import os
import sys

from tests.conftest import REPO_ROOT
from tests.helpers import (
    account_email_for_phone,
    post,
    stop_gate,
    with_gate,
)

sys.path.insert(0, str(REPO_ROOT))

from ui import composer, guard, serve  # noqa: E402

# The composer seam reads the key at call time; the unit-level tests
# below run outside with_gate's env patch.
os.environ.setdefault("LLM_API_KEY", "test-key")

UI = REPO_ROOT / "ui" / "index.html"
PHONE = "09120000000"

# The same text-layer noise the quoted-answer contract tests record.
BS = "\b"
PASSAGE = (
    f"سـخن{BS}در{BS}این{BS}اسـت؛{BS}اگرچه می‌گویند "
    f"قرآن{BS}کتابی{BS}اسـت{BS}برای{BS}زندگی{BS}جمعی{BS}انسان‌ها و …"
)
SOURCES = [
    {
        "reference": "chunk 101 of document tarhe-kolli (pages 740-745)",
        "passage": PASSAGE,
    }
]
WRITER_BLOCKS = [
    {"type": "heading", "text": "۱. طرح کلی"},
    {
        "type": "paragraph",
        "parts": [
            {"text": "پیش از هر چیز باید معنای واژه را روشن کرد: "},
            {"quote": "سخن در این است؛", "source": 0},
        ],
    },
    {
        "type": "paragraph",
        "parts": [
            {"text": "و در قطعه‌ای دیگر می‌خوانیم: "},
            {"quote": "سخن در این است؛", "source": 0},
        ],
    },
]


# --- the incremental block scanner ------------------------------------------


def test_scanner_yields_blocks_only_once_they_close():
    content = (
        '{"blocks": [' + ",".join(json.dumps(b, ensure_ascii=False) for b in WRITER_BLOCKS) + "]}"
    )
    # Every strict prefix yields only the blocks fully inside it; the
    # full text yields all three, in order.
    for cut in range(1, len(content)):
        got = guard.iter_complete_array_objects(content[:cut])
        assert len(got) <= 3
        for obj in got:
            json.loads(obj)  # every yielded object parses, always
    whole = guard.iter_complete_array_objects(content)
    assert len(whole) == 3
    assert [json.loads(o)["type"] for o in whole] == ["heading", "paragraph", "paragraph"]


def test_scanner_ignores_braces_inside_strings_and_nested_parts():
    block = {
        "type": "paragraph",
        "parts": [
            {"text": "این متن {شامل} آکولاد و \"quote: {حتی}\" است"},
            {"quote": "سخن در این است؛", "source": 0},
        ],
    }
    content = '{"blocks": [' + json.dumps(block, ensure_ascii=False) + "]}"
    found = guard.iter_complete_array_objects(content)
    assert len(found) == 1, "a nested parts object or in-string brace leaked"
    assert json.loads(found[0])["parts"][0]["text"].startswith("این متن")


def test_scanner_stops_at_the_closed_root():
    content = '{"blocks": [{"type": "heading", "text": "عنوان"}]} دنبالهٔ نویز'
    found = guard.iter_complete_array_objects(content)
    assert len(found) == 1


def test_parse_streamed_blocks_skips_non_dict_entries():
    content = '{"blocks": ["نه", {"type": "heading", "text": "عنوان"}]}'
    blocks = guard.parse_streamed_blocks(content)
    assert blocks == [{"type": "heading", "text": "عنوان"}]


# --- the streaming composer --------------------------------------------------


class SseReply:
    """The streamed writer call's upstream reply: SSE lines yielded off
    the context manager, exactly the iteration stream_writer_reply reads."""

    def __init__(self, lines):
        self._lines = [line.encode("utf-8") for line in lines]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self._lines)


class JsonReply:
    def __init__(self, body):
        self._body = body.encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class ComposerUpstream:
    """The composer seam: the planner call (no "stream" in the payload)
    gets a canned JSON reply; the streamed writer call gets an SSE
    stream split so the FIRST block closes mid-stream."""

    def __init__(self, plan="طرح: بندها و بافت‌دهی", blocks=None, usage=None):
        self.plan = plan
        self.blocks = list(blocks or WRITER_BLOCKS)
        self.usage = usage
        self.payloads = []

    def __call__(self, request, timeout=None):
        payload = json.loads(request.data.decode("utf-8"))
        self.payloads.append(payload)
        if payload.get("stream") is not True:
            reply = {"choices": [{"message": {"content": self.plan}, "finish_reason": "stop"}]}
            return JsonReply(json.dumps(reply, ensure_ascii=False))
        content = (
            '{"blocks": ['
            + ",".join(json.dumps(b, ensure_ascii=False) for b in self.blocks)
            + "]}"
        )
        first_close = content.index("}") + 1  # the heading block closes here
        chunks = [content[: first_close + 1], content[first_close + 1 :]]
        lines = []
        for i, piece in enumerate(chunks):
            chunk = {"choices": [{"delta": {"content": piece}}]}
            if self.usage is not None and i == len(chunks) - 1:
                chunk["usage"] = self.usage
            lines.append("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n")
        lines.append("data: [DONE]\n\n")
        return SseReply(lines)


def test_stream_writer_paints_blocks_as_they_close_and_keeps_the_shape(monkeypatch):
    # Gate tests pop the key in their finally — the seam reads it at
    # call time, so the unit tests pin their own.
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    upstream = ComposerUpstream(usage={"prompt_tokens": 10, "completion_tokens": 20})
    original = composer.urlopen
    composer.urlopen = upstream
    painted = []
    try:
        reply = composer.stream_writer_reply(
            "پرامپت نویسنده", on_block_text=painted.append
        )
    finally:
        composer.urlopen = original
    # The streamed call carried the stream flag and the writer's shape.
    assert upstream.payloads[0]["stream"] is True
    assert upstream.payloads[0]["thinking"] == {"type": "disabled"}
    # At least one block rode on_block_text BEFORE the stream ended, and
    # every painted object parses.
    assert painted, "no block painted mid-stream"
    for obj in painted:
        json.loads(obj)
    # The reply keeps the blocking shape: content, finish_reason, usage.
    content = reply["choices"][0]["message"]["content"]
    assert json.loads(content)["blocks"] == WRITER_BLOCKS
    assert reply["choices"][0]["finish_reason"] == "stop"
    assert reply["usage"] == {"prompt_tokens": 10, "completion_tokens": 20}


def test_compose_streaming_runs_planner_then_streaming_writer(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    upstream = ComposerUpstream()
    original = composer.urlopen
    composer.urlopen = upstream
    previews = []
    try:
        (kept, truncated) = composer.compose_quoted_answer_streaming(
            "پرسش؟", "پیش‌نویس", SOURCES, on_block=previews.append
        )
    finally:
        composer.urlopen = original
    # Two calls, planner first (thinking on) then the streamed writer.
    assert len(upstream.payloads) == 2
    assert upstream.payloads[0]["thinking"] == {"type": "enabled"}
    assert upstream.payloads[1]["stream"] is True
    # The previews are per-block guarded paragraphs (threshold off —
    # a lone paragraph must not vanish because its document has not
    # streamed yet) and carry the guard's labels.
    assert previews and all(b["type"] == "paragraph" for b in previews)
    quote = previews[0]["parts"][1]
    assert quote["pages_label"]
    # The return stays the one authority: the guarded document.
    assert not truncated
    assert len([b for b in kept if b["type"] == "paragraph"]) == 2


# --- the server's streamed /quoted-answer ------------------------------------


class GateUpstream:
    """The sheet server's whole upstream: the relayed recall (JSON;
    bodies captured), the composer's planner (JSON) and streamed writer
    (SSE) — told apart by URL and payload."""

    def __init__(self):
        self.search_bodies = []

    def __call__(self, request, timeout=None):
        url = request.full_url
        if "chat/completions" in url:
            inner = ComposerUpstream()
            return inner(request, timeout=timeout)
        if request.data:
            try:
                self.search_bodies.append(json.loads(request.data.decode("utf-8")))
            except ValueError:
                pass
        body = json.dumps([{"text": "پاسخ آزمایشی"}], ensure_ascii=False).encode()

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

        return Response(body)


def test_quoted_answer_stream_mode_emits_blocks_then_the_final_document():
    import pathlib
    import tempfile

    upstream = GateUpstream()
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="early-pool-quoted-"))
    base, server, original = with_gate(tmp, upstream)
    try:
        serve.record_chat(account_email_for_phone(PHONE))
        status, headers, body = raw_post(base, "/quoted-answer", {
            "question": "پرسش؟",
            "answer": "",
            "sources": SOURCES,
            "stream": True,
        }, phone=PHONE)
        assert status == 200
        assert "text/event-stream" in headers
        events = parse_sse(body)
        names = [name for name, _ in events]
        assert "block" in names and names[-1] == "final"
        finals = [json.loads(data) for name, data in events if name == "final"]
        blocks = finals[0]["blocks"]
        assert len([b for b in blocks if b["type"] == "paragraph"]) == 2
        assert finals[0]["truncated"] is False
    finally:
        stop_gate(server, original)


def raw_post(base, path, payload, phone=None):
    """One POST read to the END of the body (SSE-safe); (status,
    Content-Type, decoded body)."""
    import urllib.request

    from tests.helpers import _login_cookie

    headers = {"Content-Type": "application/json"}
    if phone is not None:
        headers["Cookie"] = _login_cookie(base, phone)
    request = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        return response.status, response.headers.get("Content-Type", ""), response.read().decode("utf-8")


def parse_sse(body):
    events = []
    for frame in body.split("\n\n"):
        name = None
        data = ""
        for line in frame.split("\n"):
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        if name and data:
            events.append((name, data))
    return events


# --- the companion quota semantics -------------------------------------------


def test_recall_leg_with_ask_key_checks_the_cap_but_never_records():
    import pathlib
    import tempfile

    upstream = GateUpstream()
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="early-pool-quota-"))
    base, server, original = with_gate(tmp, upstream)
    account = account_email_for_phone(PHONE)
    try:
        for _ in range(5):
            serve.record_chat(account)
        # At the cap: BOTH legs refuse — the companion key must not
        # smuggle the stream past a refusal its /ask leg got.
        status, _ = post(base, "/api/v1/recall", _recall_body(ask_key="ask-1"), phone=PHONE)
        assert status == 429
        assert serve.chats_today(account) == 5
    finally:
        stop_gate(server, original)


def test_recall_leg_with_ask_key_leaves_the_count_to_the_ask_door():
    import pathlib
    import tempfile

    upstream = GateUpstream()
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="early-pool-quota-"))
    base, server, original = with_gate(tmp, upstream)
    account = account_email_for_phone(PHONE)
    try:
        serve.record_chat(account)  # the companion /ask leg's record
        status, _ = post(base, "/api/v1/recall", _recall_body(ask_key="ask-1"), phone=PHONE)
        assert status == 200
        assert serve.chats_today(account) == 1, "the recall leg double-recorded"
        # The no-key shape still records — the reference flow's door.
        status, _ = post(base, "/api/v1/recall", _recall_body(), phone=PHONE)
        assert status == 200
        assert serve.chats_today(account) == 2
    finally:
        stop_gate(server, original)


def _recall_body(**extra):
    body = {
        "searchType": "HYBRID_COMPLETION",
        "query": "پرسش؟",
        "datasets": ["tarhe-kolli"],
        "includeReferences": True,
        "stream": True,
    }
    body.update(extra)
    return body


# --- the /ask leg's shared rewrite -------------------------------------------


def test_ask_rides_the_followup_rewrite(monkeypatch):
    import pathlib
    import tempfile

    upstream = GateUpstream()
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="early-pool-rewrite-"))
    base, server, original = with_gate(tmp, upstream)
    try:
        # The sitting's tail exists; the rewriter rewrites. serve bound
        # the rewriter into its OWN namespace at import — the patch goes
        # there, or the real rewriter runs and the fake composer answers
        # it with a plan-shaped query.
        monkeypatch.setattr(
            serve, "_conversation_tail", lambda account, session_id: "کاربر: …"
        )
        monkeypatch.setattr(
            serve,
            "rewrite_followup_query",
            lambda query, history: "پرسش بازنویسی‌شده",
        )
        status, payload = post(base, "/ask", {
            "query": "بیشتر توضیح بده",
            "datasets": ["tarhe-kolli"],
            "session_id": 3,
            "ask_key": "ask-9",
        }, phone=PHONE)
        assert status == 200
        assert payload["pool_size"] == 0  # the fake context doc has no passages
        # The pool search ran on the REWRITTEN query — the pool the
        # early start reads must match the stream leg's own retrieval.
        assert upstream.search_bodies, "the /ask door never searched"
        assert upstream.search_bodies[0]["query"] == "پرسش بازنویسی‌شده"
        assert "ask_key" not in upstream.search_bodies[0], "the ask_key leaked upstream"
        assert "session_id" not in upstream.search_bodies[0], "the session id leaked upstream"
    finally:
        stop_gate(server, original)


# --- the sheet's wiring -------------------------------------------------------


def test_sheet_fires_the_early_pool_beside_the_stream():
    html = UI.read_text(encoding="utf-8")
    # The /ask leg fires with the sitting's id and the ask's key.
    assert 'fetch("/ask"' in html
    assert html.count("ask_key: currentAskKey || undefined") >= 2, (
        "the companion key must ride BOTH legs (the /ask body and the recall body)"
    )
    assert "session_id: sessionState.storeId || undefined" in html
    # Whichever lane delivers the pool first starts the phases; the
    # draft's paint stands down once the first answer settled.
    assert "earlyPoolStarted" in html
    assert "firstAnswerSettled" in html
    assert "if (firstAnswerSettled) return;" in html
    # The quoted-answer call asks for the streamed twin, and the
    # streamed preview renders subordinate like the prose draft.
    assert "stream: true" in html
    assert ".quoted-doc.draft-stream" in html
