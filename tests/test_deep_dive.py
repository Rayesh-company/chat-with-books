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
import time

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

    def __init__(self, composer_replies=(), recall_reply=None, gate=None):
        self.lock = threading.Lock()
        self.calls = []
        self.bodies = []
        self.timeouts = []
        self.gate = gate
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
        if self.gate is not None:
            # The scripted call is in flight — logged, unanswered — until
            # the test releases the gate. A bounded wait keeps a bug from
            # hanging the suite; the finally blocks set the gate anyway.
            self.gate.wait(timeout=30)
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


def run_dive_job_sync(question, upstream):
    """Run the dive worker synchronously over a registry job — the
    orchestration seam without the HTTP layer or the thread."""
    job = serve.DiveJob(DIVE_PHONE, question)
    serve.DIVE_REGISTRY[job.id] = job
    with_upstream(upstream)(lambda: serve.run_dive_job(job))
    return job


# --- the pinned constants -------------------------------------------------


def test_serve_pins_the_dive_constants_in_source():
    text = SERVE.read_text(encoding="utf-8")
    assert serve.DIVE_MODEL == "glm-5.3"
    assert serve.DIVE_SEARCH_TYPE == "GRAPH_COMPLETION"
    assert serve.DIVE_SEARCH_TIMEOUT == 600
    # Pinned in source like every model pin — never via env.
    assert 'environ.get("DIVE_MODEL"' not in text
    assert 'environ.get("DIVE_SEARCH_TYPE"' not in text
    # The registry's capacity is a source pin too (issue #26).
    assert serve.DIVE_MAX_CONCURRENT == 3
    assert 'environ.get("DIVE_MAX_CONCURRENT"' not in text


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


def test_dive_planner_prompt_derives_the_count_from_the_pinned_cap(monkeypatch):
    # The prompt's count word is DIVE_MAX_SUB_QUESTIONS spelled out — the
    # wording and the parser's cap are one constant, so they cannot drift.
    monkeypatch.setattr(serve, "DIVE_MAX_SUB_QUESTIONS", 3)
    prompt = serve.build_dive_planner_prompt("پرسش؟")
    assert "three" in prompt
    assert "six" not in prompt


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


def test_guard_blocks_drops_an_empty_references_block():
    # A references block with no items renders as a bare «منابع» heading
    # with nothing under it — not renderable content, so it drops like
    # any other malformed block.
    sources = [{"reference": "chunk 1 of document tarhe-kolli", "passage": SENTENCE}]
    heading = {"type": "heading", "text": "بخش"}
    paragraph = {
        "type": "paragraph",
        "parts": [{"text": "متن."}, {"quote": SENTENCE, "source": 0}],
    }
    guarded = serve.guard_blocks([heading, paragraph], sources)
    assert serve.guard_blocks(
        [heading, paragraph, {"type": "references", "items": []}], sources
    ) == guarded


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


def test_with_dive_references_appends_nothing_when_nothing_was_quoted():
    # The docstring's contract: a study with nothing left to reference —
    # the guard kept only headings, every paragraph dropped — appends
    # nothing. An empty references block would render a bare «منابع».
    sources = [{"reference": "chunk 1 of document tarhe-kolli", "passage": SENTENCE}]
    headings_only = [{"type": "heading", "text": "بخش تنها"}]
    assert serve.with_dive_references(headings_only, sources) == headings_only


# --- the whole dive ---------------------------------------------------------


def test_deep_dive_job_runs_planner_searchers_synthesizer_in_order():
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
    job = run_dive_job_sync("پرسش اصلی؟", upstream)
    # Call order: the planner first, then the searchers, then the writer.
    assert "chat/completions" in upstream.calls[0]
    assert "/api/v1/recall" in upstream.calls[1]
    assert "/api/v1/recall" in upstream.calls[2]
    assert "chat/completions" in upstream.calls[3]
    blocks, truncated = job.result
    assert truncated is False
    assert job.state == "done"
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


def test_dive_job_caps_the_planned_subquestions_at_six():
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
    job = run_dive_job_sync("پرسش اصلی؟", upstream)
    recalls = recall_calls(upstream)
    assert job.state == "done"
    assert len(recalls) == 6
    # The searchers run in parallel, so the call log may interleave;
    # the SET of queries is exactly the capped six.
    assert sorted(payload["query"] for _, payload in recalls) == sorted(
        f"زیرپرسش {n}؟" for n in range(6)
    )


def test_dive_job_falls_back_to_the_raw_question_when_planning_fails():
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
    job = run_dive_job_sync("پرسش اصلی؟", upstream)
    recalls = recall_calls(upstream)
    assert job.state == "done"
    # Exactly one searcher, carrying the raw question.
    assert len(recalls) == 1
    assert recalls[0][1]["query"] == "پرسش اصلی؟"


def test_dive_job_fails_quietly_when_no_searcher_returns_evidence():
    upstream = DiveUpstream(
        composer_replies=[subquestions_reply(["الف؟"])],
        recall_reply=lambda payload: b"[]",
    )
    job = run_dive_job_sync("پرسش اصلی؟", upstream)
    # No Evidence pool means no study: the job lands `failed` with the
    # short Farsi detail — the sheet's «ناتمام ماند» message — and the
    # synthesizer never runs.
    assert job.state == "failed"
    assert job.error == serve.DIVE_NO_EVIDENCE_DETAIL
    assert serve.DIVE_EVENT_FAILED in job.events
    assert len(composer_calls(upstream)) == 1


def test_dive_job_marks_failed_when_a_worker_call_raises():
    # Any worker failure — exception, scripted or real — lands `failed`
    # with a Farsi event; the endpoint never 500s from the worker thread.
    upstream = DiveUpstream(composer_replies=[OSError("planner exploded")])
    # The planner's failure is normally absorbed into the raw-question
    # fallback; force the raise past it by breaking the registry itself.
    job = serve.DiveJob(DIVE_PHONE, "پرسش؟")
    serve.DIVE_REGISTRY[job.id] = job

    def explode(_question):
        raise RuntimeError("scripted worker failure")

    original = serve.plan_dive_subquestions
    serve.plan_dive_subquestions = explode
    try:
        with_upstream(upstream)(lambda: serve.run_dive_job(job))
    finally:
        serve.plan_dive_subquestions = original
    assert job.state == "failed"
    assert job.error == serve.DIVE_FAILED_DETAIL
    assert serve.DIVE_EVENT_FAILED in job.events
    assert job.done.is_set()


# --- the /deep-dive endpoint -------------------------------------------------
#
# A held request in the /quoted-answer shape: the gate is the phase-2
# shape exactly (a valid phone with at least one chat today), the body
# carries only the query, and the reply is the guarded study — the
# request blocks until the study is ready.

from tests.helpers import get, post, stop_gate, wait_job_done, with_gate  # noqa: E402

DIVE_PHONE = "09120000021"

ONE_SOURCE = [{"reference": "chunk 1 of document tarhe-kolli", "passage": SENTENCE}]


def dive_study_upstream(gate=None):
    """A full happy-path dive: planner, one searcher, synthesizer."""
    return DiveUpstream(
        composer_replies=[
            subquestions_reply(["زیرپرسش؟"]),
            composer_reply(
                json.dumps({"blocks": dive_blocks(ONE_SOURCE)}, ensure_ascii=False)
            ),
        ],
        recall_reply=lambda payload: cognee_payload(
            recall_text(SENTENCE, "chunk 1 of document tarhe-kolli")
        ),
        gate=gate,
    )


def test_deep_dive_endpoint_needs_the_chat_today_gate(tmp_path):
    # Phase 2's gate shape: no phone -> 400; a phone with no chat today
    # -> 429; the upstream never runs for a rejected dive.
    upstream = dive_study_upstream()
    base, server, original = with_gate(tmp_path, upstream)
    try:
        status_no_phone, _ = post(base, "/deep-dive", {"query": "پرسش؟"})
        status_no_chat, payload = post(
            base, "/deep-dive", {"query": "پرسش؟"}, phone=DIVE_PHONE
        )
    finally:
        stop_gate(server, original)
    assert status_no_phone == 400
    assert status_no_chat == 429
    assert "گفتگو" in payload["detail"]
    assert upstream.calls == []


def test_deep_dive_rejects_an_empty_query(tmp_path):
    upstream = dive_study_upstream()
    base, server, original = with_gate(tmp_path, upstream)
    try:
        serve.record_chat(DIVE_PHONE)
        status, _ = post(base, "/deep-dive", {"query": "   "}, phone=DIVE_PHONE)
    finally:
        stop_gate(server, original)
    assert status == 400
    assert upstream.calls == []


def test_deep_dive_answers_the_guarded_study_and_never_counts_a_chat(tmp_path):
    upstream = dive_study_upstream()
    base, server, original = with_gate(tmp_path, upstream)
    try:
        serve.record_chat(DIVE_PHONE)
        status, body = post(base, "/deep-dive", {"query": "پرسش؟"}, phone=DIVE_PHONE)
        assert status == 202
        wait_job_done(body["job_id"])
        poll_status, payload = get(
            base, f"/deep-dive/status?job={body['job_id']}", phone=DIVE_PHONE
        )
    finally:
        stop_gate(server, original)
    assert poll_status == 200
    assert payload["state"] == "done"
    assert payload["truncated"] is False
    # The study: heading, the guarded quoting paragraph with its page
    # labels, and the closing references block built from the real pool.
    assert payload["blocks"] == [
        {"type": "heading", "text": "بخش نخست"},
        {
            "type": "paragraph",
            "parts": [
                {"text": "می‌خوانیم که"},
                {
                    "quote": SENTENCE,
                    "source": 0,
                    "pages_label": "",
                    "first_page_label": "",
                    "book_label": "طرح کلی اندیشۀ اسلامی در قرآن",
                },
            ],
        },
        {"type": "references", "items": ["chunk 1 of document tarhe-kolli"]},
    ]
    # The dive belongs to a chat that already started: it neither counts
    # nor checks the five-per-day limit.
    assert serve.chats_today(DIVE_PHONE) == 1


def test_deep_dive_runs_even_at_the_daily_limit(tmp_path):
    upstream = dive_study_upstream()
    base, server, original = with_gate(tmp_path, upstream)
    try:
        for _ in range(serve.DAILY_CHAT_LIMIT):
            serve.record_chat(DIVE_PHONE)
        status, body = post(base, "/deep-dive", {"query": "پرسش؟"}, phone=DIVE_PHONE)
        assert status == 202
        job = wait_job_done(body["job_id"])
    finally:
        stop_gate(server, original)
    assert job.state == "done"


def test_deep_dive_job_fails_when_no_searcher_finds_evidence(tmp_path):
    upstream = DiveUpstream(
        composer_replies=[subquestions_reply(["زیرپرسش؟"])],
        recall_reply=lambda payload: b"[]",
    )
    base, server, original = with_gate(tmp_path, upstream)
    try:
        serve.record_chat(DIVE_PHONE)
        status, body = post(base, "/deep-dive", {"query": "پرسش؟"}, phone=DIVE_PHONE)
        assert status == 202
        wait_job_done(body["job_id"])
        poll_status, payload = get(
            base, f"/deep-dive/status?job={body['job_id']}", phone=DIVE_PHONE
        )
    finally:
        stop_gate(server, original)
    # The sheet reads `failed` as a dive that did not prepare — the old
    # empty-blocks reply, now a job state with its Farsi detail.
    assert poll_status == 200
    assert payload["state"] == "failed"
    assert payload["detail"] == serve.DIVE_NO_EVIDENCE_DETAIL


def test_deep_dive_status_gates_the_phone(tmp_path):
    gate = threading.Event()
    upstream = dive_study_upstream(gate=gate)
    base, server, original = with_gate(tmp_path, upstream)
    try:
        serve.record_chat(DIVE_PHONE)
        _, body = post(base, "/deep-dive", {"query": "پرسش؟"}, phone=DIVE_PHONE)
        job_id = body["job_id"]
        unknown_status, unknown = get(
            base, "/deep-dive/status?job=does-not-exist", phone=DIVE_PHONE
        )
        # A running dive belongs to its phone: another number — and a
        # request with no number at all — learns nothing, not even that
        # the job exists.
        other_status, other = get(
            base, f"/deep-dive/status?job={job_id}", phone="09120000099"
        )
        no_phone_status, no_phone = get(base, f"/deep-dive/status?job={job_id}")
        gate.set()
        wait_job_done(job_id)
    finally:
        gate.set()
        stop_gate(server, original)
    for status, payload in (
        (unknown_status, unknown),
        (other_status, other),
        (no_phone_status, no_phone),
    ):
        assert status == 404
        assert payload["detail"] == serve.DIVE_NOT_FOUND_DETAIL


def test_a_restart_empties_the_registry_so_old_job_ids_answer_404(tmp_path):
    upstream = dive_study_upstream()
    base, server, original = with_gate(tmp_path, upstream)
    try:
        serve.record_chat(DIVE_PHONE)
        _, body = post(base, "/deep-dive", {"query": "پرسش؟"}, phone=DIVE_PHONE)
        job_id = body["job_id"]
        wait_job_done(job_id)
        # A restart empties the in-process registry — the next server
        # process starts with the same dict empty. The old id's status
        # must report the failure (404), never hang.
        serve.DIVE_REGISTRY.clear()
        status, payload = get(
            base, f"/deep-dive/status?job={job_id}", phone=DIVE_PHONE
        )
    finally:
        stop_gate(server, original)
    assert status == 404
    assert payload["detail"] == serve.DIVE_NOT_FOUND_DETAIL


# --- the caps (issue #26) ------------------------------------------------------
#
# At most one dive per phone and three dives globally; excess starts get
# a friendly Farsi busy rejection. Nothing is ever queued — a rejected
# start never reaches the upstream.


def test_a_second_start_by_the_same_phone_is_rejected_farsi_busy(tmp_path):
    gate = threading.Event()
    upstream = dive_study_upstream(gate=gate)
    base, server, original = with_gate(tmp_path, upstream)
    try:
        serve.record_chat(DIVE_PHONE)
        _, body = post(base, "/deep-dive", {"query": "پرسش؟"}, phone=DIVE_PHONE)
        job_id = body["job_id"]
        calls_before = len(upstream.calls)
        busy_status, busy = post(
            base, "/deep-dive", {"query": "پرسش دیگر؟"}, phone=DIVE_PHONE
        )
        calls_after = len(upstream.calls)
        gate.set()
        wait_job_done(job_id)
    finally:
        gate.set()
        stop_gate(server, original)
    assert busy_status == 429
    assert busy["detail"] == serve.DIVE_BUSY_PHONE_DETAIL
    # No second job exists, and the rejected start never ran a planner —
    # nothing was queued.
    assert [
        job for job in serve.DIVE_REGISTRY.values() if job.phone == DIVE_PHONE
    ] == [serve.DIVE_REGISTRY[job_id]]
    assert calls_before == calls_after == 1


def test_a_fourth_concurrent_start_is_rejected_and_nothing_is_queued(tmp_path):
    gate = threading.Event()
    phones = [f"0912000003{n}" for n in range(4)]
    # One planner reply per allowed dive — each job pops its own while
    # the gate holds it; the synthesizer replies are queued only after
    # all three planners are parked, so pops stay deterministic.
    upstream = DiveUpstream(
        composer_replies=[subquestions_reply(["زیرپرسش؟"]) for _ in range(3)],
        gate=gate,
    )
    base, server, original = with_gate(tmp_path, upstream)
    try:
        for phone in phones[:3]:
            serve.record_chat(phone)
        serve.record_chat(phones[3])
        job_ids = []
        for phone in phones[:3]:
            status, body = post(
                base, "/deep-dive", {"query": "پرسش؟"}, phone=phone
            )
            assert status == 202
            job_ids.append(body["job_id"])
        assert len(serve.DIVE_REGISTRY) == 3
        calls_before = len(upstream.calls)
        fourth_status, fourth = post(
            base, "/deep-dive", {"query": "پرسش؟"}, phone=phones[3]
        )
        calls_after = len(upstream.calls)
        # Release the parked planners and let the three dives land.
        upstream.composer_replies.extend(
            composer_reply(
                json.dumps({"blocks": dive_blocks(ONE_SOURCE)}, ensure_ascii=False)
            )
            for _ in range(3)
        )
        gate.set()
        for job_id in job_ids:
            wait_job_done(job_id)
    finally:
        gate.set()
        stop_gate(server, original)
    assert fourth_status == 429
    assert fourth["detail"] == serve.DIVE_BUSY_GLOBAL_DETAIL
    # The busy fourth start never reached the upstream — it was
    # rejected, never queued.
    assert calls_before == calls_after == 3
    assert phones[3] not in [job.phone for job in serve.DIVE_REGISTRY.values()]


def test_serve_pins_the_deep_dive_endpoint():
    text = SERVE.read_text(encoding="utf-8")
    assert 'path == "/deep-dive"' in text
    # The gate is the quoted-answer shape, not the ask gate — a dive
    # never records a chat.
    assert text.index('path == "/deep-dive"') < text.index("def _deep_dive")


# --- the job registry (issue #26) ---------------------------------------------
#
# The held request becomes a job: the start answers 202 {"job_id": ...}
# immediately while the dive runs on its own thread, and the sheet polls
# /deep-dive/status for state, events, and — when done — the study.


def test_deep_dive_start_returns_a_job_identity_immediately(tmp_path):
    gate = threading.Event()
    upstream = dive_study_upstream(gate=gate)
    base, server, original = with_gate(tmp_path, upstream)
    try:
        serve.record_chat(DIVE_PHONE)
        status, body = post(base, "/deep-dive", {"query": "پرسش؟"}, phone=DIVE_PHONE)
        # The reply lands while the planner is still blocked inside the
        # scripted upstream — the start never waits on the study.
        assert status == 202
        job_id = body["job_id"]
        assert job_id in serve.DIVE_REGISTRY
        assert "chat/completions" in upstream.calls[0]
        # The status endpoint observes the first transition while the
        # planner is still inside the fake.
        poll_status, poll_body = get(
            base, f"/deep-dive/status?job={job_id}", phone=DIVE_PHONE
        )
        assert poll_status == 200
        assert poll_body["state"] == "planning"
        gate.set()
        job = wait_job_done(job_id)
        assert job.state == "done"
    finally:
        gate.set()
        stop_gate(server, original)


def test_deep_dive_status_reports_transitions_events_and_the_study(tmp_path):
    upstream = dive_study_upstream()
    base, server, original = with_gate(tmp_path, upstream)
    try:
        serve.record_chat(DIVE_PHONE)
        _, body = post(base, "/deep-dive", {"query": "پرسش؟"}, phone=DIVE_PHONE)
        job_id = body["job_id"]
        wait_job_done(job_id)
        status, payload = get(
            base, f"/deep-dive/status?job={job_id}", phone=DIVE_PHONE
        )
    finally:
        stop_gate(server, original)
    assert status == 200
    assert payload["state"] == "done"
    # The transitions land in order, each carrying its Farsi progress
    # event — the status surface's observable timeline.
    assert payload["events"] == [
        serve.DIVE_EVENT_PLANNING,
        serve.DIVE_EVENT_SEARCHING,
        serve.DIVE_EVENT_WRITING,
        serve.DIVE_EVENT_DONE,
    ]
    assert isinstance(payload["elapsed"], (int, float)) and payload["elapsed"] >= 0
    # The study rides the terminal status reply, phase-2 payload shape.
    assert payload["truncated"] is False
    assert payload["blocks"] == [
        {"type": "heading", "text": "بخش نخست"},
        {
            "type": "paragraph",
            "parts": [
                {"text": "می‌خوانیم که"},
                {
                    "quote": SENTENCE,
                    "source": 0,
                    "pages_label": "",
                    "first_page_label": "",
                    "book_label": "طرح کلی اندیشۀ اسلامی در قرآن",
                },
            ],
        },
        {"type": "references", "items": ["chunk 1 of document tarhe-kolli"]},
    ]
