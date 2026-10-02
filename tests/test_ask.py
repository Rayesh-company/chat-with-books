"""The retrieval-only ask contract locks (ADR-0014): the only_context
context doc's passage parse (the entities and facts sections drop, the
junk-free verbatim chunks keep their Page markers into the reference),
the per-Book parallel search with cross-Book dedupe, the endpoint's
ask-shaped gate (one chat recorded, balance and quota before any
upstream call, datasets validated), the phone-guarded chat store behind
the reload, and /chat/latest's restore read."""

import json
import sys

from tests.conftest import REPO_ROOT
from tests.helpers import (
    account_email_for_phone,
    cookie_for,
    post,
    raw_get,
    stop_gate,
    with_gate,
)

sys.path.insert(0, str(REPO_ROOT))

from ui import ask, chat_store, serve  # noqa: E402

PHONE = "09120000000"
OTHER_PHONE = "09120000001"

# The recorded live shape (tests/fixtures/recall-hybrid-only-context.json,
# captured 2026-09-23): the reply item's raw.value holds the context doc.
LIVE_CONTEXT = json.loads(
    (REPO_ROOT / "tests" / "fixtures" / "recall-hybrid-only-context.json").read_text(
        encoding="utf-8"
    )
)
LIVE_DOCUMENT = LIVE_CONTEXT[0]["raw"]["value"]


def only_context_reply(document):
    body = json.dumps(
        [
            {
                "kind": "graph_completion",
                "search_type": "HYBRID_COMPLETION",
                "text": "",
                "metadata": {},
                "raw": {"value": document},
            }
        ],
        ensure_ascii=False,
    ).encode("utf-8")
    return body


def sample_document(marker="Page 10:"):
    """A context doc shaped like the live one: one passage in the
    passages section (carrying its text-layer page marker), then entity
    and fact sections that must drop."""
    return (
        "## Relevant passages\n"
        f"جملهٔ کتاب این است.\n\n{marker}\n"
        "\n## Relevant entities\n"
        "### یک مفهوم\n\n## Related facts\n"
        "- رابطه‌ای"
    )


class AskUpstream:
    """The ask searchers (the main service's recall URL, only_context
    bodies) and the composer calls, told apart by URL; every search
    body is captured for the shape assertions."""

    def __init__(self, documents=None):
        # One scripted context doc per search, in call order; the
        # default feeds both Books of the default fan-out.
        self.documents = list(
            documents or [sample_document("Page 10:"), sample_document("Page 30:")]
        )
        self.search_bodies = []

    def __call__(self, request, timeout=None):
        url = request.full_url
        if "/api/v1/recall" in url:
            payload = json.loads(request.data.decode("utf-8"))
            self.search_bodies.append(payload)
            document = self.documents.pop(0) if self.documents else ""
            return _Reply(only_context_reply(document))
        raise AssertionError(f"unexpected upstream call: {url}")


class _Reply:
    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# --- the context-doc parse ---------------------------------------------------


def test_parse_takes_only_the_passages_section():
    passages = ask.parse_context_passages(LIVE_DOCUMENT)
    assert passages, "the recorded live context yielded no passages"
    # Every kept chunk is real passage text, and the parse runs to the
    # entities heading — a graph label never crosses.
    assert "## Relevant entities" not in "".join(passages)
    assert "###" not in "".join(passages)


def test_parse_of_a_missing_or_malformed_doc_is_empty():
    assert ask.parse_context_passages("") == []
    assert ask.parse_context_passages(None) == []
    assert ask.parse_context_passages("## Relevant entities\n### گره") == []


def test_reference_carries_the_pages_from_the_text_layer():
    # The unit seam: run the module's own parse + reference build over
    # the sample doc through the patched urlopen.
    original = ask.urlopen
    ask.urlopen = AskUpstream(documents=[sample_document()])
    try:
        got = ask._dataset_passages("tarhe-kolli", "پرسش؟")
    finally:
        ask.urlopen = original
    assert got[0]["reference"] == "document tarhe-kolli (page 10)"
    assert got[0]["passage"].startswith("جملهٔ کتاب")


def test_ask_pool_searches_each_book_once_and_dedupes():
    upstream = AskUpstream(
        documents=[
            sample_document(),
            sample_document(),  # same text from the second Book
        ]
    )
    original = ask.urlopen
    ask.urlopen = upstream
    try:
        pool = ask.ask_pool("پرسش؟", ["tarhe-kolli", "70143-336"])
    finally:
        ask.urlopen = original
    # One search per Book, both Book set members, references on.
    assert len(upstream.search_bodies) == 2
    for body in upstream.search_bodies:
        assert body["searchType"] == "HYBRID_COMPLETION"
        assert body["only_context"] is True
        assert body["includeReferences"] is True
        assert len(body["datasets"]) == 1
    # Identical passages dedupe across Books (the second Book's copy
    # drops — its reference names the first Book's call, which is the
    # honest owner of the first-seen text).
    assert len(pool) == 1
    references = [source["reference"] for source in pool]
    assert all("document tarhe-kolli" in ref for ref in references)


# --- the endpoint's gate and reply -------------------------------------------


def test_ask_gates_and_records_exactly_one_chat(tmp_path):
    upstream = AskUpstream()
    base, server, original = with_gate(tmp_path, upstream)
    try:
        anonymous, _ = post(base, "/ask", {"query": "پرسش؟"})
        status, payload = post(base, "/ask", {"query": "پرسش؟"}, phone=PHONE)
        again, _ = post(base, "/ask", {"query": "پرسش؟"}, phone=OTHER_PHONE)
    finally:
        stop_gate(server, original)
    assert anonymous == 401
    assert status == 200
    assert payload["pool_size"] == len(payload["sources"]) == 2
    assert isinstance(payload["chat_id"], str) and payload["chat_id"]
    # The ask is the ask: ONE chat recorded for the asking Account, one
    # for the second asker, and no upstream call for the rejected one
    # (the stores key by the Account's email, T21).
    assert serve.chats_today(account_email_for_phone(PHONE)) == 1
    assert serve.chats_today(account_email_for_phone(OTHER_PHONE)) == 1
    assert len(upstream.search_bodies) == 4  # two per ask


def test_ask_rejects_a_body_without_a_query(tmp_path):
    upstream = AskUpstream()
    base, server, original = with_gate(tmp_path, upstream)
    try:
        serve.record_chat(account_email_for_phone(PHONE))
        bad, _ = post(base, "/ask", {}, phone=PHONE)
    finally:
        stop_gate(server, original)
    assert bad == 400
    assert not upstream.search_bodies


def test_ask_validates_the_dataset_selection(tmp_path):
    upstream = AskUpstream(documents=[sample_document()])
    base, server, original = with_gate(tmp_path, upstream)
    try:
        serve.record_chat(account_email_for_phone(PHONE))
        status, payload = post(
            base,
            "/ask",
            {"query": "پرسش؟", "datasets": ["70143-336", "bogus"]},
            phone=PHONE,
        )
    finally:
        stop_gate(server, original)
    assert status == 200
    # Only the Book-set member ran, once — a name outside the set
    # never reaches an upstream.
    assert len(upstream.search_bodies) == 1
    assert upstream.search_bodies[0]["datasets"] == ["70143-336"]
    assert payload["sources"][0]["reference"].startswith("document 70143-336")


def test_ask_answers_the_honest_empty_when_retrieval_finds_nothing(tmp_path):
    upstream = AskUpstream(documents=["", ""])  # both Books come back empty
    base, server, original = with_gate(tmp_path, upstream)
    try:
        serve.record_chat(account_email_for_phone(PHONE))
        status, payload = post(base, "/ask", {"query": "پرسش؟"}, phone=PHONE)
    finally:
        stop_gate(server, original)
    assert status == 200
    assert payload["sources"] == []
    assert payload["pool_size"] == 0


# --- the chat store and the reload's restore ---------------------------------


def test_the_store_round_trips_phone_guarded_updates(tmp_path):
    chat_store.CHAT_DB = tmp_path / "chats.sqlite3"
    chat_store.create_chat("chat1", PHONE, "پرسش؟", ["tarhe-kolli"], [])
    # A foreign phone's update lands nowhere.
    chat_store.update_selections("chat1", OTHER_PHONE, [{"text": "x"}])
    assert chat_store.latest_chat(PHONE)["selections"] == []
    chat_store.update_selections("chat1", PHONE, [{"text": "نقل"}])
    chat_store.update_quoted("chat1", PHONE, [{"type": "heading", "text": "سند"}], True)
    chat_store.update_pool("chat1", PHONE, [{"reference": "r", "passage": "p"}])
    row = chat_store.latest_chat(PHONE)
    assert row["question"] == "پرسش؟"
    assert row["datasets"] == ["tarhe-kolli"]
    assert row["selections"] == [{"text": "نقل"}]
    assert row["blocks"] == [{"type": "heading", "text": "سند"}]
    assert row["truncated"] is True
    assert row["sources"] == [{"reference": "r", "passage": "p"}]
    # Another phone's latest stays its own (None when it never asked).
    assert chat_store.latest_chat(OTHER_PHONE) is None


def test_chat_latest_restores_the_phones_newest_ask(tmp_path):
    upstream = AskUpstream()
    base, server, original = with_gate(tmp_path, upstream)
    try:
        anonymous_status, _ = post(base, "/ask", {"query": "پرسش؟"})
        status, payload = post(base, "/ask", {"query": "پرسش؟"}, phone=PHONE)
        assert status == 200
        cookie = cookie_for(base, account_email_for_phone(PHONE))
        restored_status, restored = raw_get(base, "/chat/latest", cookie=cookie)
    finally:
        stop_gate(server, original)
    assert anonymous_status == 401
    assert restored_status == 200
    chat = restored["chat"]
    assert chat["question"] == "پرسش؟"
    assert chat["id"] == payload["chat_id"]
    assert chat["sources"] == payload["sources"]
    assert chat["selections"] == []
    assert chat["blocks"] == []


def test_the_phases_persist_their_replies_under_the_ask_row(tmp_path):
    # One scripted composer reply stands at both the picker and the
    # writer seams (the URL never differs for the fake).
    picker_content = json.dumps(
        {"selections": []}, ensure_ascii=False
    )
    upstream = _ComposerAndSearchUpstream(picker_content)
    base, server, original = with_gate(tmp_path, upstream)
    try:
        serve.record_chat(account_email_for_phone(PHONE))
        ask_status, ask_payload = post(
            base, "/ask", {"query": "پرسش؟", "datasets": ["tarhe-kolli"]},
            phone=PHONE,
        )
        pick_status, _ = post(
            base,
            "/quote-selection",
            {
                "question": "پرسش؟",
                "sources": ask_payload["sources"],
                "chat_id": ask_payload["chat_id"],
            },
            phone=PHONE,
        )
        quoted_status, _ = post(
            base,
            "/quoted-answer",
            {
                "question": "پرسش؟",
                "sources": ask_payload["sources"],
                "chat_id": ask_payload["chat_id"],
            },
            phone=PHONE,
        )
    finally:
        stop_gate(server, original)
    assert ask_status == 200
    assert pick_status == 200
    assert quoted_status == 200
    row = chat_store.latest_chat(account_email_for_phone(PHONE))
    # An empty selection and an empty document are the honest shapes —
    # the row records them as they landed, never invents content.
    assert row["selections"] == []
    assert row["blocks"] == []


class _ComposerAndSearchUpstream(AskUpstream):
    """The ask searchers plus every composer-shaped call (the picker's
    and the writer's), told apart by URL."""

    def __init__(self, picker_content):
        super().__init__(documents=[sample_document()])
        self.picker_content = picker_content

    def __call__(self, request, timeout=None):
        url = request.full_url
        if "/chat/completions" in url:
            return _Reply(
                json.dumps(
                    {"choices": [{"message": {"content": self.picker_content}}]}
                ).encode("utf-8")
            )
        return super().__call__(request, timeout=timeout)
