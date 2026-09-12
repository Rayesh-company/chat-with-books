"""The Deep dive tracer bullet (issue #25, ADR 0006): one dive runs
Planner (glm-5.3, thinking on) -> up to six parallel searchers on the
second Cognee service -> one Synthesizer call (glm-5.3) writing the
study, guarded like phase 2, with the references appended server-side.

The dive payloads are pinned server-side — the browser names no Cognee
search type — and the orchestration core follows the compose_quoted_answer
pattern: module-level functions over an injectable urlopen, so the tests
script the upstreams."""

import json
import os
import sys
import threading

from tests.conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT))

from ui import serve  # noqa: E402

SERVE = REPO_ROOT / "ui" / "serve.py"

BS = "\b"
NOISY_PASSAGE = (
    f"سـخن{BS}در{BS}این{BS}اسـت؛{BS}اگرچه می‌گویند "
    f"قرآن{BS}کتابی{BS}اسـت{BS}برای{BS}زندگی{BS}جمعی{BS}انسان‌ها و …"
)
OTHER_PASSAGE = "این جمله از قطعهٔ دیگری است و ربطی به قطعهٔ نخست ندارد."
SENTENCE = "سخن در این است؛"

# One recall reply's text carries the answer plus the Evidence block —
# the same shape the browser's splitCitation/evidenceSources parse today.
RECALL_TEXT_A = (
    f"پاسخ نخست.\n\nEvidence:\n- chunk 101 of document tarhe-kolli "
    f"(pages 740-745): \"{NOISY_PASSAGE}\""
)
RECALL_TEXT_B = (
    f"پاسخ دوم.\n\nEvidence:\n- chunk 29 of document tarhe-kolli: "
    f"\"{OTHER_PASSAGE}\""
)


def recall_text(passage, reference="chunk 1 of document tarhe-kolli"):
    return f"پاسخ.\n\nEvidence:\n- {reference}: \"{passage}\""


def cognee_payload(text):
    return json.dumps([{"text": text}]).encode("utf-8")


def composer_reply(content, finish_reason=None):
    choice = {"message": {"content": content}}
    if finish_reason:
        choice["finish_reason"] = finish_reason
    return json.dumps({"choices": [choice]}).encode("utf-8")


def subquestions_reply(subs):
    return composer_reply(json.dumps(subs, ensure_ascii=False))


def dive_blocks(sources):
    """A synthesizer reply's body: one heading, one good quoting
    paragraph per source, plus a paraphrase and a wrong-index sentence
    the guard must drop."""
    blocks = [{"type": "heading", "text": "بخش نخست"}]
    for index, source in enumerate(sources):
        blocks.append(
            {
                "type": "paragraph",
                "parts": [
                    {"text": "می‌خوانیم که "},
                    {"quote": source["passage"], "source": index},
                ],
            }
        )
    blocks.append(
        {
            "type": "paragraph",
            "parts": [
                {"text": "بند معیوب: "},
                {"quote": "نقل‌وارگی که در هیچ قطعه‌ای نیست", "source": 0},
                {"quote": sources[0]["passage"], "source": len(sources)},
            ],
        }
    )
    return blocks


class DiveUpstream:
    """Stands in for the composer and the second Cognee service, told
    apart by URL. Thread-safe — the dive's searchers call it in
    parallel — with a lock around the call log and the reply queues."""

    def __init__(self, composer_replies=(), recall_reply=None):
        self.lock = threading.Lock()
        self.calls = []
        self.bodies = []
        self.timeouts = []
        self.composer_replies = [
            reply.encode("utf-8") if isinstance(reply, str) else reply
            for reply in composer_replies
        ]
        self.recall_reply = recall_reply or (
            lambda payload: cognee_payload(RECALL_TEXT_A)
        )

    def __call__(self, request, timeout=None):
        url = request.full_url
        body = request.data.decode("utf-8") if request.data else ""
        if "chat/completions" in url:
            with self.lock:
                self.calls.append(url)
                self.bodies.append(body)
                self.timeouts.append(timeout)
                reply = self.composer_replies.pop(0)
        else:
            # The reply is built OUTSIDE the lock — a scripted recall
            # reply may block (the parallel fan-out test's barrier), and
            # holding the lock through it would serialize the searchers.
            reply = self.recall_reply(json.loads(body))
            with self.lock:
                self.calls.append(url)
                self.bodies.append(body)
                self.timeouts.append(timeout)
        if isinstance(reply, Exception):
            raise reply

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

        return Response(reply)


def with_upstream(upstream):
    """Patch serve.urlopen for one orchestration call; restores after."""

    def run(fn):
        original = serve.urlopen
        serve.urlopen = upstream
        os.environ["LLM_API_KEY"] = "test-key"
        try:
            return fn()
        finally:
            serve.urlopen = original
            del os.environ["LLM_API_KEY"]

    return run


def composer_calls(upstream):
    return [
        json.loads(body)
        for url, body in zip(upstream.calls, upstream.bodies)
        if "chat/completions" in url
    ]


def recall_calls(upstream):
    return [
        (url, json.loads(body))
        for url, body in zip(upstream.calls, upstream.bodies)
        if "/api/v1/recall" in url
    ]


# --- the pinned constants -------------------------------------------------


def test_serve_pins_the_dive_constants_in_source():
    text = SERVE.read_text(encoding="utf-8")
    assert serve.DIVE_MODEL == "glm-5.3"
    assert serve.DIVE_SEARCH_TYPE == "GRAPH_COMPLETION"
    assert serve.DIVE_SEARCH_TIMEOUT == 600
    # Pinned in source like every model pin — never via env.
    assert 'environ.get("DIVE_MODEL"' not in text
    assert 'environ.get("DIVE_SEARCH_TYPE"' not in text


# --- the server-side Evidence parser ---------------------------------------


def test_parse_evidence_sources_reads_locators_and_passages():
    sources = serve.parse_evidence_sources(RECALL_TEXT_A)
    assert sources == [
        {
            "reference": "chunk 101 of document tarhe-kolli (pages 740-745)",
            "passage": NOISY_PASSAGE,
        }
    ]


def test_parse_evidence_sources_reads_multiple_bullets_and_drops_malformed():
    text = (
        "پاسخ.\n\nEvidence:\n"
        f"- chunk 1 of document tarhe-kolli: \"{NOISY_PASSAGE}\"\n"
        f"- chunk 29 of document 70143-336 (pages 12-13): \"{OTHER_PASSAGE}\"\n"
        "- یک گلوله بدشکل بدون نقل\n"
    )
    sources = serve.parse_evidence_sources(text)
    assert sources == [
        {"reference": "chunk 1 of document tarhe-kolli", "passage": NOISY_PASSAGE},
        {
            "reference": "chunk 29 of document 70143-336 (pages 12-13)",
            "passage": OTHER_PASSAGE,
        },
    ]


def test_parse_evidence_sources_answers_no_evidence_with_an_empty_pool():
    assert serve.parse_evidence_sources("پاسخ بی استناد.") == []
    assert serve.parse_evidence_sources("") == []
    assert serve.parse_evidence_sources(None) == []


# --- the Planner -----------------------------------------------------------


def test_dive_planner_prompt_asks_for_farsi_subquestions_as_json():
    prompt = serve.build_dive_planner_prompt("اندیشه اسلامی در قرآن چه طرحی دارد؟")
    assert "اندیشه اسلامی در قرآن چه طرحی دارد؟" in prompt
    assert "Farsi" in prompt
    assert "six" in prompt
    assert "JSON" in prompt


def test_dive_subquestions_parser_keeps_strings_and_caps_at_six():
    subs = serve.dive_subquestions_from_reply(
        json.dumps([f"زیرپرسش {n}؟" for n in range(9)], ensure_ascii=False),
        "پرسش اصلی؟",
    )
    assert subs == [f"زیرپرسش {n}؟" for n in range(6)]


def test_dive_subquestions_parser_survives_a_fence_and_prose():
    fenced = '```json\n["زیرپرسش یک؟", "زیرپرسش دو؟"]\n```'
    assert serve.dive_subquestions_from_reply(fenced, "پرسش؟") == [
        "زیرپرسش یک؟",
        "زیرپرسش دو؟",
    ]
    # Non-string items drop; empty strings drop.
    assert serve.dive_subquestions_from_reply(
        '["زیرپرسش؟", 3, "", null]', "پرسش؟"
    ) == ["زیرپرسش؟"]


def test_dive_subquestions_parser_falls_back_to_the_raw_question():
    assert serve.dive_subquestions_from_reply("no json here", "پرسش اصلی؟") == [
        "پرسش اصلی؟"
    ]
    assert serve.dive_subquestions_from_reply("[]", "پرسش اصلی؟") == ["پرسش اصلی؟"]
    assert serve.dive_subquestions_from_reply(None, "پرسش اصلی؟") == ["پرسش اصلی؟"]


def test_plan_dive_subquestions_runs_glm_with_thinking_on():
    upstream = DiveUpstream(composer_replies=[subquestions_reply(["زیرپرسش؟"])])
    with_upstream(upstream)(lambda: serve.plan_dive_subquestions("پرسش؟"))
    payloads = composer_calls(upstream)
    assert len(payloads) == 1
    assert payloads[0]["model"] == "glm-5.3"
    assert payloads[0]["thinking"] == {"type": "enabled"}
    assert payloads[0]["max_tokens"] == serve.COMPOSER_MAX_TOKENS
    assert "پرسش؟" in payloads[0]["messages"][0]["content"]
    assert upstream.timeouts == [serve.COMPOSER_TIMEOUT]


def test_plan_dive_subquestions_falls_back_when_the_call_fails():
    upstream = DiveUpstream(composer_replies=[OSError("planner down")])
    subs = with_upstream(upstream)(lambda: serve.plan_dive_subquestions("پرسش؟"))
    assert subs == ["پرسش؟"]


# --- the searchers ---------------------------------------------------------


def test_dive_recall_pins_the_search_payload_on_the_second_service():
    upstream = DiveUpstream(recall_reply=lambda p: cognee_payload(RECALL_TEXT_A))
    with_upstream(upstream)(lambda: serve.dive_recall("زیرپرسش؟"))
    urls, payloads = zip(*recall_calls(upstream))
    assert urls[0].startswith(serve.NEXT_TIER_URL + "/api/v1/recall")
    assert payloads[0] == {
        "searchType": "GRAPH_COMPLETION",
        "query": "زیرپرسش؟",
        "datasets": ["tarhe-kolli", "70143-336"],
        "includeReferences": True,
    }
    assert upstream.timeouts == [serve.DIVE_SEARCH_TIMEOUT]


def test_dive_recall_returns_an_empty_pool_on_failure():
    upstream = DiveUpstream(recall_reply=lambda p: OSError("cognee down"))
    assert with_upstream(upstream)(lambda: serve.dive_recall("زیرپرسش؟")) == []
    upstream = DiveUpstream(recall_reply=lambda p: b"not json")
    assert with_upstream(upstream)(lambda: serve.dive_recall("زیرپرسش؟")) == []


def test_run_dive_searches_fans_out_in_parallel():
    # Three searchers must be inside the fake at the same time — a
    # sequential loop deadlocks the barrier until its timeout breaks it.
    barrier = threading.Barrier(3, timeout=10)
    seen = []
    lock = threading.Lock()

    def parallel_reply(payload):
        with lock:
            seen.append(payload["query"])
        barrier.wait()
        return cognee_payload(recall_text(f"نقلِ {payload['query']}"))

    upstream = DiveUpstream(recall_reply=parallel_reply)
    sources = with_upstream(upstream)(
        lambda: serve.run_dive_searches(["الف؟", "ب؟", "پ؟"])
    )
    assert sorted(seen) == ["الف؟", "ب؟", "پ؟"]
    assert len(sources) == 3


def test_run_dive_searches_merges_pools_and_dedupes_identical_passages():
    def reply(payload):
        if payload["query"] == "الف؟":
            return cognee_payload(
                recall_text(SENTENCE, "chunk 1 of document tarhe-kolli")
                + f"\n- chunk 29 of document tarhe-kolli: \"{OTHER_PASSAGE}\""
            )
        return cognee_payload(
            # The same passage as الف's first bullet — deduped away.
            recall_text(SENTENCE, "chunk 1 of document tarhe-kolli")
            + f"\n- chunk 40 of document tarhe-kolli (pages 7-9): \"{NOISY_PASSAGE}\""
        )

    upstream = DiveUpstream(recall_reply=reply)
    sources = with_upstream(upstream)(
        lambda: serve.run_dive_searches(["الف؟", "ب؟"])
    )
    assert sources == [
        {"reference": "chunk 1 of document tarhe-kolli", "passage": SENTENCE},
        {"reference": "chunk 29 of document tarhe-kolli", "passage": OTHER_PASSAGE},
        {
            "reference": "chunk 40 of document tarhe-kolli (pages 7-9)",
            "passage": NOISY_PASSAGE,
        },
    ]


# --- the Synthesizer --------------------------------------------------------


def test_dive_prompt_asks_for_the_headed_quoted_study():
    sources = [
        {"reference": "chunk 1 of document tarhe-kolli", "passage": SENTENCE},
        {"reference": "chunk 29 of document tarhe-kolli", "passage": OTHER_PASSAGE},
    ]
    prompt = serve.build_dive_prompt("پرسش؟", sources)
    assert "پرسش؟" in prompt
    assert SENTENCE in prompt
    assert "2,000" in prompt and "4,000" in prompt
    assert "five to eight" in prompt
    assert "copied VERBATIM" in prompt
    assert "parts" in prompt
    # The references list is the server's job — never the model's.
    assert "Do NOT write a references list" in prompt


def test_compose_dive_study_runs_glm_with_thinking_off_and_returns_guarded_blocks():
    sources = [
        {"reference": "chunk 1 of document tarhe-kolli", "passage": SENTENCE},
    ]
    blocks = [{"type": "heading", "text": "بخش"}, None] + dive_blocks(sources)[:2]
    upstream = DiveUpstream(
        composer_replies=[
            composer_reply(json.dumps({"blocks": blocks}, ensure_ascii=False))
        ]
    )
    kept, truncated = with_upstream(upstream)(
        lambda: serve.compose_dive_study("پرسش؟", sources)
    )
    payloads = composer_calls(upstream)
    assert payloads[0]["model"] == "glm-5.3"
    assert payloads[0]["thinking"] == {"type": "disabled"}
    assert payloads[0]["max_tokens"] == serve.COMPOSER_MAX_TOKENS
    assert truncated is False
    assert kept[0] == {"type": "heading", "text": "بخش"}


def truncated_dive_reply(sources):
    prefix = json.dumps({"blocks": dive_blocks(sources)[:2]}, ensure_ascii=False)
    content = prefix[:-2] + ", "
    return composer_reply(content, finish_reason="length")


def dive_continuation_reply(sources, finish_reason="stop"):
    content = json.dumps(
        {"blocks": dive_blocks(sources)[2:]}, ensure_ascii=False
    )
    return composer_reply(content, finish_reason=finish_reason)


def dive_continuation_cut_reply():
    # A doubly-cut continuation dies before even one complete block.
    return composer_reply('{"blocks": [ {"type"', finish_reason="length")


def test_compose_dive_study_continues_once_past_a_length_cut():
    sources = [
        {"reference": "chunk 1 of document tarhe-kolli", "passage": SENTENCE},
        {"reference": "chunk 29 of document tarhe-kolli", "passage": OTHER_PASSAGE},
    ]
    upstream = DiveUpstream(
        composer_replies=[
            truncated_dive_reply(sources),
            dive_continuation_reply(sources),
        ]
    )
    kept, truncated = with_upstream(upstream)(
        lambda: serve.compose_dive_study("پرسش؟", sources)
    )
    payloads = composer_calls(upstream)
    # The continuation is a second glm-5.3 call, still thinking disabled.
    assert payloads[1]["model"] == "glm-5.3"
    assert payloads[1]["thinking"] == {"type": "disabled"}
    assert payloads[1]["max_tokens"] == serve.COMPOSER_MAX_TOKENS
    assert "AFTER the last block" in payloads[1]["messages"][0]["content"]
    assert truncated is False
    # The merged study survived: both quoting paragraphs, both guarded.
    quotes = [
        part["quote"]
        for block in kept
        if block["type"] == "paragraph"
        for part in block["parts"]
        if "quote" in part
    ]
    assert quotes == [SENTENCE, OTHER_PASSAGE]


def test_compose_dive_study_lands_the_guarded_prefix_when_cut_twice():
    sources = [
        {"reference": "chunk 1 of document tarhe-kolli", "passage": SENTENCE},
        {"reference": "chunk 29 of document tarhe-kolli", "passage": OTHER_PASSAGE},
    ]
    upstream = DiveUpstream(
        composer_replies=[
            truncated_dive_reply(sources),
            dive_continuation_cut_reply(),
        ]
    )
    kept, truncated = with_upstream(upstream)(
        lambda: serve.compose_dive_study("پرسش؟", sources)
    )
    assert len(composer_calls(upstream)) == 2
    assert truncated is True
    quotes = [
        part["quote"]
        for block in kept
        if block["type"] == "paragraph"
        for part in block["parts"]
        if "quote" in part
    ]
    assert quotes == [SENTENCE]


# --- the guard and the references block -------------------------------------


def test_guard_blocks_passes_a_references_block_through_untouched():
    sources = [{"reference": "chunk 1 of document tarhe-kolli", "passage": SENTENCE}]
    heading = {"type": "heading", "text": "بخش"}
    paragraph = {
        "type": "paragraph",
        "parts": [{"text": "متن."}, {"quote": SENTENCE, "source": 0}],
    }
    refs = {"type": "references", "items": ["chunk 1 of document tarhe-kolli"]}
    guarded = serve.guard_blocks([heading, paragraph, refs], sources)
    assert guarded[-1] == refs
    # A malformed references block is not a block the sheet can render.
    for malformed in ({"type": "references"}, {"type": "references", "items": "not a list"}):
        assert serve.guard_blocks([heading, paragraph, malformed], sources) == (
            guarded[:-1]
        )


def test_with_dive_references_appends_the_actually_used_pool():
    sources = [
        {"reference": "chunk 1 of document tarhe-kolli", "passage": SENTENCE},
        {"reference": "chunk 29 of document tarhe-kolli", "passage": OTHER_PASSAGE},
    ]
    blocks = dive_blocks(sources)
    guarded = serve.guard_blocks(blocks, sources)
    assert "نقل‌وارگی که در هیچ قطعه‌ای نیست" not in json.dumps(
        guarded, ensure_ascii=False
    )
    dived = serve.with_dive_references(guarded, sources)
    assert dived[-1] == {
        "type": "references",
        "items": [
            "chunk 1 of document tarhe-kolli",
            "chunk 29 of document tarhe-kolli",
        ],
    }
    # A pool no quote survived appends nothing — the study has failed anyway.
    assert serve.with_dive_references([], sources) == []


# --- the whole dive ---------------------------------------------------------


def test_compose_deep_dive_runs_planner_searchers_synthesizer_in_order():
    sources = [
        {"reference": "chunk 1 of document tarhe-kolli", "passage": SENTENCE},
        {"reference": "chunk 29 of document tarhe-kolli", "passage": OTHER_PASSAGE},
    ]

    def reply(payload):
        if payload["query"] == "الف؟":
            return cognee_payload(
                recall_text(SENTENCE, "chunk 1 of document tarhe-kolli")
            )
        return cognee_payload(
            recall_text(OTHER_PASSAGE, "chunk 29 of document tarhe-kolli")
        )

    upstream = DiveUpstream(
        composer_replies=[
            subquestions_reply(["الف؟", "ب؟"]),
            composer_reply(
                json.dumps({"blocks": dive_blocks(sources)}, ensure_ascii=False)
            ),
        ],
        recall_reply=reply,
    )
    blocks, truncated = with_upstream(upstream)(
        lambda: serve.compose_deep_dive("پرسش اصلی؟")
    )
    # Call order: the planner first, then the searchers, then the writer.
    assert "chat/completions" in upstream.calls[0]
    assert "/api/v1/recall" in upstream.calls[1]
    assert "/api/v1/recall" in upstream.calls[2]
    assert "chat/completions" in upstream.calls[3]
    assert truncated is False
    quotes = [
        part["quote"]
        for block in blocks
        if block["type"] == "paragraph"
        for part in block["parts"]
        if "quote" in part
    ]
    # The verbatim guard dropped the study's paraphrase and wrong-index
    # sentence; every surviving body paragraph still quotes.
    assert quotes == [SENTENCE, OTHER_PASSAGE]
    # The closing references list is server-built from the real pool.
    assert blocks[-1] == {
        "type": "references",
        "items": [
            "chunk 1 of document tarhe-kolli",
            "chunk 29 of document tarhe-kolli",
        ],
    }


def test_compose_deep_dive_caps_the_planned_subquestions_at_six():
    upstream = DiveUpstream(
        composer_replies=[
            subquestions_reply([f"زیرپرسش {n}؟" for n in range(9)]),
            composer_reply(
                json.dumps(
                    {"blocks": dive_blocks([
                        {"reference": "chunk 1 of document tarhe-kolli",
                         "passage": SENTENCE}])
                    },
                    ensure_ascii=False,
                )
            ),
        ]
    )
    with_upstream(upstream)(lambda: serve.compose_deep_dive("پرسش اصلی؟"))
    recalls = recall_calls(upstream)
    assert len(recalls) == 6
    # The searchers run in parallel, so the call log may interleave;
    # the SET of queries is exactly the capped six.
    assert sorted(payload["query"] for _, payload in recalls) == sorted(
        f"زیرپرسش {n}؟" for n in range(6)
    )


def test_compose_deep_dive_falls_back_to_the_raw_question_when_planning_fails():
    upstream = DiveUpstream(
        composer_replies=[
            OSError("planner down"),
            composer_reply(
                json.dumps(
                    {"blocks": dive_blocks([
                        {"reference": "chunk 1 of document tarhe-kolli",
                         "passage": SENTENCE}])
                    },
                    ensure_ascii=False,
                )
            ),
        ]
    )
    with_upstream(upstream)(lambda: serve.compose_deep_dive("پرسش اصلی؟"))
    recalls = recall_calls(upstream)
    # Exactly one searcher, carrying the raw question.
    assert len(recalls) == 1
    assert recalls[0][1]["query"] == "پرسش اصلی؟"


def test_compose_deep_dive_dies_quietly_when_no_searcher_returns_evidence():
    upstream = DiveUpstream(
        composer_replies=[subquestions_reply(["الف؟"])],
        recall_reply=lambda payload: b"[]",
    )
    blocks, truncated = with_upstream(upstream)(
        lambda: serve.compose_deep_dive("پرسش اصلی؟")
    )
    # No Evidence pool means no study: the synthesizer never runs, and the
    # endpoint answers empty blocks the sheet reports as a failed dive.
    assert (blocks, truncated) == ([], False)
    assert len(composer_calls(upstream)) == 1
