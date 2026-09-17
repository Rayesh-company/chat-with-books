"""The «جست‌وجوی بیشتر» contract locks (ADR-0010): the broaden prompt's
digest shape, the guarded parse (junk contributes nothing, queries cap
at two, dedupe on the normalized letter stream), the operation's
new-passages-only merge against the ask's existing pool, the datasets
passthrough to the pinned searchers, and the endpoint's phase-2 gate —
never counts a chat, 400 on a malformed body, 200 {"sources": []} as
the honest no-joy shape."""

import json
import sys

from tests.conftest import REPO_ROOT
from tests.helpers import post, stop_gate, with_gate

sys.path.insert(0, str(REPO_ROOT))

from ui import dive, recall_more, serve  # noqa: E402

PHONE = "09120000000"

SENTENCE = "سخن در این است؛"
OTHER_SENTENCE = "این جمله از قطعهٔ دیگری است."
EXISTING = [
    {
        "reference": "chunk 1 of document tarhe-kolli (pages 10-12)",
        "passage": SENTENCE,
    }
]
FACETS = ["پرسش مجاورِ اول؟", "پرسش مجاورِ دوم؟"]


def broaden_reply():
    return json.dumps(FACETS, ensure_ascii=False)


def composer_reply(content):
    return json.dumps({"choices": [{"message": {"content": content}}]}).encode(
        "utf-8"
    )


def facet_payload(query):
    """One searcher's well-fed Evidence reply, query-specific so the two
    facets never dedupe against each other."""
    return (
        "پاسخ.\n\nEvidence:\n"
        f'- chunk 5 of document tarhe-kolli (pages 20-25): "{query} — {OTHER_SENTENCE}"\n'
        f'- chunk 9 of document 70143-336: "{query} — تازهٔ دوم."'
    )


class WidenUpstream:
    """The broaden call (composer URL) and the searchers (recall URL),
    told apart by URL; the search bodies are captured for the datasets
    assertions. `recall_fn(payload)` scripts the searchers."""

    def __init__(self, composer_items=(), recall_fn=None):
        self.composer_items = list(composer_items)
        self.recall_fn = recall_fn or (lambda payload: facet_payload(payload["query"]))
        self.search_bodies = []

    def __call__(self, request, timeout=None):
        url = request.full_url
        body = request.data.decode("utf-8")
        if "chat/completions" in url:
            item = self.composer_items.pop(0)
            if isinstance(item, Exception):
                raise item
            return FakeReply(item)
        payload = json.loads(body)
        self.search_bodies.append(payload)
        return FakeReply(json.dumps([{"text": self.recall_fn(payload)}]).encode("utf-8"))


class FakeReply:
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


def patched(upstream, fn):
    """Run fn against the widen's two seams (its own composer urlopen
    and the dive kernel's searcher urlopen) with the test key set."""
    original = recall_more.urlopen
    dive_original = dive.urlopen
    recall_more.urlopen = upstream
    dive.urlopen = upstream
    import os

    os.environ["LLM_API_KEY"] = "test-key"
    try:
        return fn()
    finally:
        recall_more.urlopen = original
        dive.urlopen = dive_original
        os.environ.pop("LLM_API_KEY", None)


# --- the broaden call -------------------------------------------------------


def test_broaden_prompt_carries_the_question_and_short_digests():
    long_passage = "کلمهٔ پُرتکرار " * 40
    prompt = recall_more.build_broaden_prompt(
        "پرسش؟", [{"passage": long_passage}]
    )
    assert "پرسش؟" in prompt
    # The digest, not the whole passage.
    assert long_passage.strip() not in prompt
    assert "up to two SHORT Farsi search" in prompt


def test_parse_broaden_reply_guards_junk_caps_and_dedupes():
    for junk in ("", "not json", '{"object": true}'):
        assert recall_more.parse_broaden_reply(junk) == []
    # Non-strings and blanks drop.
    assert recall_more.parse_broaden_reply('["یک؟", 5, "  "]') == ["یک؟"]
    parsed = recall_more.parse_broaden_reply(
        json.dumps(["یک؟", "یـک؟", "دو؟", "سه؟"], ensure_ascii=False)
    )
    # Dedupe on the normalized letter stream, cap at two.
    assert parsed == ["یک؟", "دو؟"]


def test_recall_more_returns_only_fresh_passages():
    upstream = WidenUpstream(composer_items=[composer_reply(broaden_reply())])
    fresh = patched(
        upstream, lambda: recall_more.recall_more("پرسش؟", EXISTING)
    )
    # Two facet searchers, two passages each, none of them the
    # existing pool's passage.
    assert len(fresh) == 4
    assert all(source["passage"] != SENTENCE for source in fresh)
    assert len(upstream.search_bodies) == 2


def test_recall_more_dedupes_against_the_existing_pool():
    def scripted(payload):
        if payload["query"] == FACETS[0]:
            # The first facet's searcher returns the passage the pool
            # already holds — it must not come back as fresh.
            return (
                "پاسخ.\n\nEvidence:\n"
                f'- chunk 1 of document tarhe-kolli (pages 10-12): "{SENTENCE}"'
            )
        return facet_payload(payload["query"])

    upstream = WidenUpstream(
        composer_items=[composer_reply(broaden_reply())], recall_fn=scripted
    )
    fresh = patched(
        upstream, lambda: recall_more.recall_more("پرسش؟", EXISTING)
    )
    assert len(fresh) == 2
    assert all(source["passage"] != SENTENCE for source in fresh)


def test_recall_more_with_a_failed_broaden_contributes_nothing():
    upstream = WidenUpstream(composer_items=[OSError("composer down")])
    fresh = patched(
        upstream, lambda: recall_more.recall_more("پرسش؟", EXISTING)
    )
    assert fresh == []
    # The searchers never ran.
    assert upstream.search_bodies == []


def test_recall_more_passes_the_selected_datasets_to_the_searchers():
    upstream = WidenUpstream(composer_items=[composer_reply(broaden_reply())])
    patched(
        upstream,
        lambda: recall_more.recall_more(
            "پرسش؟", EXISTING, datasets=["70143-336"]
        ),
    )
    assert upstream.search_bodies
    for body in upstream.search_bodies:
        assert body["datasets"] == ["70143-336"]
        assert body["searchType"] == "HYBRID_COMPLETION"


# --- the endpoint -----------------------------------------------------------


def test_recall_more_endpoint_gates_like_phase_two(tmp_path):
    upstream = WidenUpstream(composer_items=[composer_reply(broaden_reply())])
    base, server, original = with_gate(tmp_path, upstream)
    try:
        no_phone, _ = post(base, "/recall-more", {"question": "پرسش؟"})
        no_chat, payload = post(
            base, "/recall-more", {"question": "پرسش؟"}, phone=PHONE
        )
    finally:
        stop_gate(server, original)
    assert no_phone == 400
    assert no_chat == 429
    assert "گفتگو" in payload["detail"]
    assert not upstream.search_bodies


def test_recall_more_endpoint_rejects_a_body_without_a_question(tmp_path):
    upstream = WidenUpstream(composer_items=[composer_reply(broaden_reply())])
    base, server, original = with_gate(tmp_path, upstream)
    try:
        serve.record_chat(PHONE)
        bad, _ = post(base, "/recall-more", {"sources": []}, phone=PHONE)
    finally:
        stop_gate(server, original)
    assert bad == 400


def test_recall_more_endpoint_returns_only_the_fresh_sources(tmp_path):
    upstream = WidenUpstream(composer_items=[composer_reply(broaden_reply())])
    base, server, original = with_gate(tmp_path, upstream)
    try:
        serve.record_chat(PHONE)
        status, payload = post(
            base,
            "/recall-more",
            {"question": "پرسش؟", "sources": EXISTING},
            phone=PHONE,
        )
    finally:
        stop_gate(server, original)
    assert status == 200
    assert len(payload["sources"]) == 4
    assert all(source["passage"] != SENTENCE for source in payload["sources"])


def test_recall_more_endpoint_answers_the_honest_empty_shape(tmp_path):
    # A broaden that finds no uncovered facet: 200 with no sources —
    # the sheet's no-joy note consumes this, never a 5xx.
    upstream = WidenUpstream(composer_items=[composer_reply("[]")])
    base, server, original = with_gate(tmp_path, upstream)
    try:
        serve.record_chat(PHONE)
        status, payload = post(
            base, "/recall-more", {"question": "پرسش؟"}, phone=PHONE
        )
    finally:
        stop_gate(server, original)
    assert status == 200
    assert payload == {"sources": []}


# --- the phase-1 evidence fallback (ADR-0011) --------------------------------


def test_evidence_fallback_gates_like_the_ask_without_counting(tmp_path):
    upstream = WidenUpstream()
    base, server, original = with_gate(tmp_path, upstream)
    try:
        no_phone, _ = post(base, "/evidence-fallback", {"question": "پرسش؟"})
        no_chat, payload = post(
            base, "/evidence-fallback", {"question": "پرسش؟"}, phone=PHONE
        )
        # A phone WITH a chat today passes — and it never records a
        # second one.
        serve.record_chat(PHONE)
        status, _ = post(
            base, "/evidence-fallback", {"question": "پرسش؟"}, phone=PHONE
        )
    finally:
        stop_gate(server, original)
    assert no_phone == 400
    assert no_chat == 429
    assert "گفتگو" in payload["detail"]
    assert status == 200
    assert serve.chats_today(PHONE) == 1


def test_evidence_fallback_returns_the_searched_pool(tmp_path):
    upstream = WidenUpstream()
    base, server, original = with_gate(tmp_path, upstream)
    try:
        serve.record_chat(PHONE)
        status, payload = post(
            base,
            "/evidence-fallback",
            {
                "question": "پرسش؟",
                "datasets": ["70143-336", "bogus"],
            },
            phone=PHONE,
        )
    finally:
        stop_gate(server, original)
    assert status == 200
    assert len(payload["sources"]) == 2
    # The search ran the pinned shape over the VALIDATED selection.
    assert upstream.search_bodies[0]["datasets"] == ["70143-336"]
    assert upstream.search_bodies[0]["searchType"] == "HYBRID_COMPLETION"


def test_evidence_fallback_rejects_a_body_without_a_question(tmp_path):
    upstream = WidenUpstream()
    base, server, original = with_gate(tmp_path, upstream)
    try:
        serve.record_chat(PHONE)
        bad, _ = post(base, "/evidence-fallback", {}, phone=PHONE)
    finally:
        stop_gate(server, original)
    assert bad == 400
    assert not upstream.search_bodies
