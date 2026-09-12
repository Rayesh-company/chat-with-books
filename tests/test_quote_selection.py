"""The Quote selection picker (ADR-0006, issue #28): the first answer
rendered on the sheet is ten verbatim Book sentences (guarded 4-12)
picked from the Evidence pool by EXACTLY ONE glm-5.3-flash call. The
gate is phase 2's shape — the picker belongs to the chat phase 1
recorded — and it never records or counts a chat."""

import json
import os
import sys

from tests.conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT))

from ui import serve  # noqa: E402

# The Book's text layer separates words with real backspace characters
# and carries kashida and ZWNJ — the noise the verbatim guard collapses
# (recorded shape, tests.test_quoted_answer).
BS = "\b"
POOL_PASSAGE = (
    f"سـخن{BS}در{BS}این{BS}اسـت؛ "
    f"قرآن{BS}کتابی{BS}اسـت{BS}برای{BS}زندگی؛ "
    f"انسان{BS}در{BS}جامعه{BS}می‌زیید؛ "
    f"عـدل{BS}اساس{BS}اجتماع{BS}اسـت."
)
OTHER_PASSAGE = "این جمله از قطعهٔ دیگری است."

# Four complete Farsi sentences of the first passage, verbatim (modulo
# the text-layer noise the guard normalizes away).
SENTENCES = [
    "سخن در این است؛",
    "قرآن کتابی است برای زندگی؛",
    "انسان در جامعه می‌زیید؛",
    "عدل اساس اجتماع است.",
]
PARAPHRASE = "قرآن برای زندگی انسان‌ها کتابی است"  # reworded — not in the passage

POOL = [
    {
        "reference": "chunk 101 of document tarhe-kolli (pages 740-745)",
        "passage": POOL_PASSAGE,
    },
    {
        "reference": "chunk 29 of document tarhe-kolli",
        "passage": OTHER_PASSAGE,
    },
]

# What the guard + label attachment return for the four verbatim
# sentences of POOL[0]: the labels of exactly the claimed passage.
KEPT_WITH_LABELS = [
    {
        "text": sentence,
        "reference": POOL[0]["reference"],
        "pages_label": "صفحات 740 تا 745",
        "first_page_label": "صفحه 740",
        "book_label": "طرح کلی اندیشۀ اسلامی در قرآن",
    }
    for sentence in SENTENCES
]


def picker_reply(selections):
    content = json.dumps({"selections": selections}, ensure_ascii=False)
    return json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")


def verbatim_selections(count):
    """`count` verbatim selections cycling the first passage's sentences."""
    return [
        {"text": SENTENCES[i % len(SENTENCES)], "source": 0}
        for i in range(count)
    ]


# --- the picker pipeline, unit level -----------------------------------


class FakeResponse:
    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def run_pick_with_replies(replies, question="پرسش؟", sources=POOL):
    """Run pick_quote_selection against per-call canned replies; return
    (selections, captured payloads, captured timeouts) — the
    run_compose_with_replies pattern."""
    captured = {"payloads": [], "timeouts": []}
    queue = [
        reply.encode("utf-8") if isinstance(reply, str) else reply
        for reply in replies
    ]

    def fake_urlopen(request, timeout=None):
        captured["payloads"].append(json.loads(request.data.decode("utf-8")))
        captured["timeouts"].append(timeout)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return FakeResponse(item)

    original = serve.urlopen
    serve.urlopen = fake_urlopen
    os.environ["LLM_API_KEY"] = "test-key"
    try:
        selections = serve.pick_quote_selection(question, sources)
    finally:
        serve.urlopen = original
        del os.environ["LLM_API_KEY"]
    return selections, captured


def test_picker_prompt_carries_question_passages_and_reply_shape():
    prompt = serve.build_picker_prompt("پرسش؟", POOL)
    assert "پرسش؟" in prompt
    assert "chunk 101 of document tarhe-kolli (pages 740-745)" in prompt
    assert POOL_PASSAGE in prompt
    assert OTHER_PASSAGE in prompt
    # Selection, not writing: verbatim from exactly one passage.
    assert "VERBATIM" in prompt
    assert "exactly ONE" in prompt
    assert "Do not paraphrase" in prompt
    # The aim rides in the prompt wording, derived from the constant.
    assert "aim for ten" in prompt
    # The reply shape is the selection list guard_sentences consumes.
    assert '"selections"' in prompt


def test_parse_picker_reply_accepts_plain_and_fenced_json():
    payload = {"selections": [{"text": "سخن در این است؛", "source": 0}]}
    assert serve.parse_picker_reply(json.dumps(payload)) == payload["selections"]
    fenced = f"```json\n{json.dumps(payload)}\n```"
    assert serve.parse_picker_reply(fenced) == payload["selections"]


def test_parse_picker_reply_rejects_malformed_content():
    assert serve.parse_picker_reply("here are the sentences you asked for") == []
    assert serve.parse_picker_reply('{"selections": "not a list"}') == []
    assert serve.parse_picker_reply('{"blocks": []}') == []
    assert serve.parse_picker_reply("") == []
    assert serve.parse_picker_reply(None) == []


def test_pick_is_exactly_one_flash_call_with_thinking_disabled():
    # EXACTLY ONE composer call per picker request: the same endpoint,
    # model pin, output ceiling, and default temperature as the phase-2
    # writer, thinking disabled — copy-matching, not reasoning.
    selections, captured = run_pick_with_replies(
        [picker_reply(verbatim_selections(4))]
    )
    assert len(captured["payloads"]) == 1
    payload = captured["payloads"][0]
    assert payload["model"] == "glm-5.3-flash"
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["max_tokens"] == serve.COMPOSER_MAX_TOKENS
    assert "temperature" not in payload
    assert captured["timeouts"] == [serve.COMPOSER_TIMEOUT]
    assert "پرسش؟" in payload["messages"][0]["content"]


def test_pick_drops_paraphrase_and_wrong_index_keeps_verbatim():
    # A scripted reply mixing a paraphrase and a verbatim sentence: only
    # the verbatim ones survive, each with the labels of exactly the
    # passage it claims; a verbatim sentence claiming the wrong index
    # drops too — its tooltip would cite a passage it never came from.
    reply = verbatim_selections(4) + [
        {"text": PARAPHRASE, "source": 0},
        {"text": SENTENCES[0], "source": 1},
    ]
    selections, _ = run_pick_with_replies([picker_reply(reply)])
    assert selections == KEPT_WITH_LABELS


def test_pick_caps_survivors_at_twelve():
    selections, _ = run_pick_with_replies(
        [picker_reply(verbatim_selections(16))]
    )
    assert len(selections) == 12


def test_pick_below_the_floor_returns_empty():
    # One verbatim survivor is under the floor of four — the sheet falls
    # back to the prose render, so the picker replies nothing.
    reply = [{"text": SENTENCES[0], "source": 0}, {"text": PARAPHRASE, "source": 0}]
    selections, _ = run_pick_with_replies([picker_reply(reply)])
    assert selections == []


def test_pick_failure_returns_empty():
    # A picker failure (OSError etc.) is an empty selection — the same
    # shape as below the floor, never an exception to the sheet.
    selections, captured = run_pick_with_replies([OSError("picker down")])
    assert selections == []
    assert len(captured["payloads"]) == 1


# --- the endpoint, over the real sheet server --------------------------

from tests.helpers import post, stop_gate, with_gate  # noqa: E402


class FakeComposer:
    """Stands in for the composer endpoint. Captures every call and
    scripts the picker's replies in order."""

    def __init__(self, replies=()):
        self.calls = []
        self.payloads = []
        self.timeouts = []
        self.replies = list(replies)

    def __call__(self, request, timeout=None):
        self.calls.append(request.full_url)
        self.payloads.append(json.loads(request.data.decode("utf-8")))
        self.timeouts.append(timeout)
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item

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

        return Response(item)


def quote_payload(question="پرسش؟", sources=POOL):
    return {"question": question, "sources": sources}


def test_quote_selection_without_a_phone_is_rejected_before_the_picker(tmp_path):
    composer = FakeComposer([picker_reply(verbatim_selections(4))])
    base, server, original = with_gate(tmp_path, composer)
    try:
        status, payload = post(base, "/quote-selection", quote_payload())
    finally:
        stop_gate(server, original)
    assert status == 400
    assert "شماره" in payload["detail"]
    assert composer.calls == []


def test_quote_selection_needs_a_chat_today_and_never_counts(tmp_path):
    # The picker belongs to the chat phase 1 recorded: a phone with no
    # chat today never reaches the composer; at the daily limit it still
    # runs, and it never records or counts a chat of its own.
    composer = FakeComposer(
        [picker_reply(verbatim_selections(4)), picker_reply(verbatim_selections(4))]
    )
    base, server, original = with_gate(tmp_path, composer)
    phone = "09120000021"
    try:
        status_none, _ = post(
            base, "/quote-selection", quote_payload(), phone=phone
        )
        calls_after_rejected = list(composer.calls)
        for _ in range(5):
            serve.record_chat(phone)
        status_ok, body = post(
            base, "/quote-selection", quote_payload(), phone=phone
        )
        post(base, "/quote-selection", quote_payload(), phone=phone)
    finally:
        stop_gate(server, original)
    assert status_none == 429
    assert calls_after_rejected == []
    assert status_ok == 200
    # The day's five chats stayed five — the picker never counts.
    assert serve.chats_today(phone) == 5


def test_quote_selection_rejects_a_malformed_body_and_an_empty_pool(tmp_path):
    # Malformed body or empty sources answer 400 before any upstream
    # call — the picker is never invoked without a pool.
    composer = FakeComposer()
    base, server, original = with_gate(tmp_path, composer)
    phone = "09120000022"
    serve.record_chat(phone)
    try:
        status_no_sources, _ = post(base, "/quote-selection", {"question": "پرسش؟"}, phone=phone)
        status_empty, _ = post(
            base, "/quote-selection", quote_payload(sources=[]), phone=phone
        )
        status_no_question, _ = post(base, "/quote-selection", {"sources": POOL}, phone=phone)
    finally:
        stop_gate(server, original)
    assert status_no_sources == 400
    assert status_empty == 400
    assert status_no_question == 400
    assert composer.calls == []


def test_quote_selection_answers_the_mixed_reply_with_labels_and_pool_size(tmp_path):
    # The full endpoint path: exactly one composer call, the reply's
    # paraphrase and wrong-index claim dropped by the verbatim guard,
    # labels attached per sentence, and the pool size reported.
    composer = FakeComposer(
        [
            picker_reply(
                verbatim_selections(4)
                + [{"text": PARAPHRASE, "source": 0}, {"text": SENTENCES[0], "source": 1}]
            )
        ]
    )
    base, server, original = with_gate(tmp_path, composer)
    phone = "09120000023"
    serve.record_chat(phone)
    try:
        status, body = post(
            base, "/quote-selection", quote_payload(), phone=phone
        )
    finally:
        stop_gate(server, original)
    assert status == 200
    assert len(composer.calls) == 1
    assert "chat/completions" in composer.calls[0]
    assert body["pool_size"] == 2
    assert body["selections"] == KEPT_WITH_LABELS


def test_quote_selection_picker_failure_answers_empty_with_200(tmp_path):
    # A picker failure is never a 5xx: the one uniform empty shape the
    # sheet's prose fallback consumes.
    composer = FakeComposer([OSError("composer down")])
    base, server, original = with_gate(tmp_path, composer)
    phone = "09120000024"
    serve.record_chat(phone)
    try:
        status, body = post(
            base, "/quote-selection", quote_payload(), phone=phone
        )
    finally:
        stop_gate(server, original)
    assert status == 200
    assert body == {"selections": [], "pool_size": 2}


def test_quote_selection_below_the_floor_answers_empty_with_200(tmp_path):
    composer = FakeComposer(
        [picker_reply([{"text": SENTENCES[0], "source": 0}, {"text": PARAPHRASE, "source": 0}])]
    )
    base, server, original = with_gate(tmp_path, composer)
    phone = "09120000025"
    serve.record_chat(phone)
    try:
        status, body = post(
            base, "/quote-selection", quote_payload(), phone=phone
        )
    finally:
        stop_gate(server, original)
    assert status == 200
    assert body == {"selections": [], "pool_size": 2}
