"""The shared test harness for the Research Mode seams (ADR-0012's
router tickets): the scripted composer + second-tier Cognee upstream,
the synchronous turn worker over a registry turn, and the store-backed
session factory. Lives OUTSIDE the test modules — no test module is
another's library (tests/helpers.py's rule); this module is the fakes'
home for the research engine, kept separate from helpers.py's HTTP
gate helpers so each stays single-purpose.

Every scripted reply shape here mirrors the contract locks in
tests/test_research_mode.py — the well-fed recall, the classify
object, the guarded block JSON."""

import json
import os
import threading

from tests.conftest import REPO_ROOT

import sys  # noqa: E402

sys.path.insert(0, str(REPO_ROOT))

from ui import dive, research, research_store  # noqa: E402

PHONE = "09120000000"
SENTENCE = "سخن در این است؛"
OTHER_SENTENCE = "این جمله از قطعهٔ دیگری است."

FED_RECALL_TEXT = (
    "پاسخ.\n\nEvidence:\n"
    f"- chunk 1 of document tarhe-kolli (pages 10-12): \"{SENTENCE}\"\n"
    f"- chunk 29 of document tarhe-kolli: \"{OTHER_SENTENCE}\""
)


def cognee_payload(text):
    return json.dumps([{"text": text}]).encode("utf-8")


def composer_reply(content):
    return json.dumps({"choices": [{"message": {"content": content}}]}).encode(
        "utf-8"
    )


def classify_reply(intent="casual_question", **fields):
    payload = {
        "intent": intent,
        "rq_proposal": "",
        "reason": "",
        "concepts": [],
        "subquestions": [],
        "scope_in": [],
        "scope_out": [],
    }
    payload.update(fields)
    return composer_reply(json.dumps(payload, ensure_ascii=False))


def guarded_blocks(sources):
    """One quoting paragraph per source — the shape the verbatim guard
    keeps. Each paragraph's text differs, so each records as its own
    claim."""
    blocks = [{"type": "heading", "text": "بخش نخست"}]
    for index, source in enumerate(sources):
        blocks.append(
            {
                "type": "paragraph",
                "parts": [
                    {"text": f"ادعای {index + 1}: "},
                    {"quote": source["passage"], "source": index},
                ],
            }
        )
    return {"blocks": blocks}


class ResearchUpstream:
    """The scripted composer + second-tier Cognee, told apart by URL;
    an entry that is an Exception instance is raised instead of
    answered. Thread-safe — the gather's searchers call in parallel."""

    def __init__(self, composer_replies=(), recall_reply=None):
        self.lock = threading.Lock()
        self.calls = []
        self.bodies = []
        self.composer_replies = [
            reply.encode("utf-8") if isinstance(reply, str) else reply
            for reply in composer_replies
        ]
        self.recall_reply = recall_reply or (
            lambda payload: cognee_payload(FED_RECALL_TEXT)
        )

    def __call__(self, request, timeout=None):
        url = request.full_url
        body = request.data.decode("utf-8") if request.data else ""
        if "chat/completions" in url:
            with self.lock:
                self.calls.append(url)
                self.bodies.append(body)
                reply = self.composer_replies.pop(0)
        else:
            reply = self.recall_reply(json.loads(body))
            with self.lock:
                self.calls.append(url)
                self.bodies.append(body)
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


def composer_bodies(upstream):
    """The parsed composer request bodies, in call order."""
    return [
        json.loads(body)
        for url, body in zip(upstream.calls, upstream.bodies)
        if "chat/completions" in url
    ]


def recall_call_count(upstream):
    return len(upstream.calls) - len(composer_bodies(upstream))


def with_patched_upstream(upstream, tmp_path):
    """Patch the engine's and the kernel's urlopens plus the store's DB
    for one direct call; restore after."""

    def run(fn):
        research_store.RESEARCH_DB = tmp_path / "research.sqlite3"
        originals = [(module, module.urlopen) for module in (research, dive)]
        for module, _ in originals:
            module.urlopen = upstream
        os.environ["LLM_API_KEY"] = "test-key"
        try:
            return fn()
        finally:
            for module, original in originals:
                module.urlopen = original
            os.environ.pop("LLM_API_KEY", None)

    return run


def run_turn_sync(session, message, upstream, tmp_path):
    """One turn's worker, synchronously over a registry turn — the
    orchestration seam without the HTTP layer or the thread."""
    turn = research.ResearchTurn(session["phone"], session["id"], message)
    research.RESEARCH_REGISTRY[turn.id] = turn
    with_patched_upstream(upstream, tmp_path)(
        lambda: research.run_research_turn(turn, session)
    )
    return turn


def make_session(tmp_path, state=None):
    """One session in the test's own store DB — never the module's real
    default file."""
    research_store.RESEARCH_DB = tmp_path / "research.sqlite3"
    session, error = research.ensure_session(
        PHONE, None, "پیام آغازین", "پرسش پژوهش؟", []
    )
    assert session is not None, error
    if state:
        session["state"] = state
    return session


def conversational_writer_reply():
    return composer_reply(
        json.dumps(
            guarded_blocks([{"passage": SENTENCE}]), ensure_ascii=False
        )
    )


def parked_proposal_state():
    """A state with one research-question proposal waiting — the
    checkpoint the user must decide."""
    state = research.new_research_state("پرسش پژوهش؟")
    research._apply_classify_updates(
        state,
        {
            "intent": "research_exploration",
            "rq_proposal": "پرسش دقیق‌تر؟",
            "reason": "چون",
            "concepts": [],
            "subquestions": [],
            "scope_in": [],
            "scope_out": [],
        },
    )
    return state
