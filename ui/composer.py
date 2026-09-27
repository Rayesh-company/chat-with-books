"""The composer client: the one upstream call shape (model + thinking +
max_tokens + endpoint-default temperature) and the parse / salvage /
one-continuation repair shared by every composer conversation — the
Quoted answer's two-call write here; the dive's Planner and Synthesizer
and the Quote-selection picker run the same shape through their own
modules' urlopen seams. The upstream seam is this module's `urlopen`
attribute — the tests patch ui.composer.urlopen."""

from __future__ import annotations

import json
import os
import threading
from urllib.request import Request, urlopen

try:
    from ui.guard import _numbered_passages, guard_blocks, parse_quoted_reply
except ImportError:  # the container runs serve.py as a script beside the modules
    from guard import _numbered_passages, guard_blocks, parse_quoted_reply

# Quoted-answer composer: asks the chat model twice to produce the
# interleaved document (paragraphs of AI text with embedded verbatim Book
# quotes) that replaces the streamed answer on the sheet (ADR-0003).
# glm-5.3-flash here — the first-answer path and phase 2 (ADR-0002);
# glm-5.3 is the Deep dive's model (DIVE_MODEL in ui/dive.py — the
# dive's Planner and Synthesizer, issue #25), and the second service's own
# compose env keeps glm-5.3 for the COT probe on 8001. The pin changes
# only here, after the README's /chat/completions smoke rule — never
# via env.
COMPOSER_MODEL = "glm-5.3-flash"
COMPOSER_URL = (
    os.environ.get("LLM_ENDPOINT", "https://api.z.ai/api/coding/paas/v4").rstrip("/")
    + "/chat/completions"
)
# Two sequential calls since 2026-09-10 (PM planning call): a reasoning
# planner (~84s measured) then the non-reasoning writer (~17s), so phase
# 2 lands in ~100s expected. 240s per call stays as headroom for the
# coding endpoint's queue variance — worst case ~480s, covered by the
# sheet's pulsing status and pipeline timer.
COMPOSER_TIMEOUT = int(os.environ.get("COMPOSER_TIMEOUT", "240"))
# The endpoint caps a reply's length; left unpinned, an unknown default
# applies and a long document runs out of room mid-write (live run
# 2026-09-10: the Quoted answer ended unfinished at its final section).
# A capped reply returns finish_reason "length" cut mid-sentence, and
# reasoning tokens count inside the ceiling — measured 128 of 173
# completion tokens on a trivial reply, thinking disabled — so the
# reasoning planner carries the same ceiling. Pinned in source like the
# model, never via env.
COMPOSER_MAX_TOKENS = 16384
# The follow-up rewrite's own budget (ADR-0015): one fast call that
# must never hold an ask hostage — its own short timeout and a small
# ceiling, both pinned like the model.
REWRITE_TIMEOUT = 15
REWRITE_MAX_TOKENS = 512


def conversation_context(conversation_tail: str) -> str:
    """The sitting's earlier turns as framing (the follow-up thread,
    ADR-0015): empty when there is no tail; otherwise a section the
    writer reads but never quotes — every Book claim still grounds in
    the passages below."""
    if not conversation_tail:
        return ""
    return (
        "The sitting's earlier turns, oldest first (framing only — this "
        "reply answers the CURRENT question and continues the thread "
        "naturally; never quote from this section, ground every Book "
        f"claim in the passages below):\n{conversation_tail}\n\n"
    )


def framing_context(answer: str, plan: str = "") -> str:
    """The writer-side framing context: the plan when there is one (PM
    call, 2026-09-10), otherwise — planner failed — the draft answer's
    single-call framing: question + draft answer + passages.
    """
    if plan:
        return (
            "A planning pass over the same passages produced this plan — "
            "follow its structure and paragraph outline; its suggested "
            "sentences are pointers only, you still copy each quoted "
            f"sentence VERBATIM from the passages below:\n{plan}"
        )
    return (
        "A faster model's draft answer (context for framing and coverage "
        "only — its claims about the Book are unverified; ground every Book "
        f"claim in the passages below):\n{answer}"
    )


def build_quoted_prompt(
    question: str, answer: str, sources, plan: str = "", conversation_tail: str = ""
) -> str:
    """Build the writer prompt."""
    passages = _numbered_passages(sources)
    context = framing_context(answer, plan)
    tail = conversation_context(conversation_tail)
    return (
        "You are writing a Farsi Quoted answer for a Q&A sheet over one "
        "Book.\n\n"
        f"Question: {question}\n\n"
        f"{tail}"
        f"{context}\n\n"
        "Passages (numbered, from the Book's retrieved Evidence; text-layer "
        "noise like \\b backspaces may appear between words):\n"
        f"{passages}\n\n"
        "Task: write a document that answers the question in interleaved "
        "paragraphs.\n"
        "Every paragraph is one unit: your own Farsi text with quoted "
        "sentences embedded inside it — your text, then a quoted sentence, "
        "then more of your text, as the argument needs. Introduce the topic, "
        "connect the quotes, and summarize what they establish; never state "
        "a Book claim the passages do not support. A paragraph may quote "
        "from more than one passage. Each quoted sentence is a complete "
        "Farsi sentence copied VERBATIM from exactly ONE passage (ignore "
        "the \\b noise; write proper Farsi). Do not paraphrase, do not "
        "merge, do not shorten. Never write a paragraph without at least "
        "one quoted sentence, and never a paragraph of bare quotes without "
        "your connective text.\n"
        "Aim for at least eight quote paragraphs across the document when "
        "the passages support them; never invent or paraphrase a quote to "
        "reach the count.\n"
        "You may write short section headings.\n\n"
        "Reply with ONLY a JSON object, no prose, no code fence:\n"
        '{"blocks": [{"type": "heading", "text": "..."}, '
        '{"type": "paragraph", "parts": [{"text": "..."}, '
        '{"quote": "<verbatim sentence>", "source": <passage index>}, '
        '{"text": "..."}]}]}'
    )


def build_planner_prompt(question: str, sources) -> str:
    """The reasoning pass's brief: plan the document's structure and the
    cross-passage weaving — not write it (PM call, 2026-09-10). Kept
    short the same night after live phase 2 measured ~295s: reasoning
    time scales with what the planner reads and writes, so it plans
    from the question and passages alone — the draft answer is held
    back (the writer still gets it when the planner fails) — and the
    plan itself is capped.
    """
    passages = _numbered_passages(sources)
    return (
        "You are planning a Farsi Quoted answer for a Q&A sheet over one "
        "Book.\n\n"
        f"Question: {question}\n\n"
        "Passages (numbered, from the Book's retrieved Evidence; text-layer "
        "noise like \\b backspaces may appear between words):\n"
        f"{passages}\n\n"
        "Task: outline the document in a plain-text plan — not the "
        "document itself, not JSON:\n"
        "- the section headings, in order;\n"
        "- for each paragraph: the point it makes and which passages to "
        "weave into it, noting where one paragraph should weave "
        "sentences from more than one passage;\n"
        "- at least eight quote paragraphs when the passages support "
        "them; never plan a quote the passages do not contain.\n"
        "Keep the plan under 150 words. Reply with ONLY the plan as "
        "plain text."
    )


def build_continuation_prompt(
    question: str, answer: str, sources, plan: str, blocks: list
) -> str:
    """The resume brief after a length-cut write: the same question,
    framing, and passages as the writer prompt, plus the blocks that
    completed before the cut; the writer writes only what follows them.
    """
    passages = _numbered_passages(sources)
    written = json.dumps({"blocks": blocks}, ensure_ascii=False)
    return (
        "You are continuing a Farsi Quoted answer for a Q&A sheet over one "
        "Book.\n\n"
        f"Question: {question}\n\n"
        f"{framing_context(answer, plan)}\n\n"
        "Passages (numbered, from the Book's retrieved Evidence; text-layer "
        "noise like \\b backspaces may appear between words):\n"
        f"{passages}\n\n"
        "A previous write of this document was cut by a reply length "
        "limit. The blocks that completed before the cut (JSON):\n"
        f"{written}\n\n"
        "Task: continue that SAME document. Write ONLY the blocks that "
        "come AFTER the last block above — the remaining headings and "
        "interleaved paragraphs, in the same JSON object shape. Every "
        "paragraph follows the same rule as before: your own Farsi text "
        "with quoted sentences embedded inside it, each quoted sentence a "
        "complete Farsi sentence copied VERBATIM from exactly ONE passage "
        "(ignore the \\b noise; write proper Farsi). Do not paraphrase, do "
        "not merge. Never repeat a block that is already written; never "
        "invent or paraphrase a quote. If nothing is missing, reply with "
        'an empty list: {"blocks": []}.\n\n'
        "Reply with ONLY a JSON object, no prose, no code fence:\n"
        '{"blocks": [{"type": "heading", "text": "..."}, '
        '{"type": "paragraph", "parts": [{"text": "..."}, '
        '{"quote": "<verbatim sentence>", "source": <passage index>}, '
        '{"text": "..."}]}]}'
    )


def _thinking_fields(thinking_type: str) -> dict:
    """The payload fields toggling reasoning for one composer call.

    The `thinking` toggle is the Z.AI coding endpoint's native shape and
    the recorded payload everywhere. AvalAI (the chat endpoint since
    2026-09-13) 400s on `{"type": "disabled"}` — recorded live — and
    its omitted-field default is reasoning ON, which is fatal for the
    writers: recorded 2026-09-13, a dive writer call burned the whole
    16384-token ceiling on 43k chars of invisible reasoning and
    returned EMPTY content (finish "length"). `reasoning_effort`
    "none"/"minimal" are ignored there; "low" is honored — reasoning
    shrinks and the document gets written. So on non-Z.AI endpoints a
    disabled writer sends `reasoning_effort: "low"`; the reasoning
    passes keep the `thinking` field, which AvalAI accepts for
    "enabled".
    """
    if thinking_type == "enabled" or "api.z.ai" in COMPOSER_URL:
        return {"thinking": {"type": thinking_type}}
    return {"reasoning_effort": "low"}


# The usage tap (T22, GitLab #24): the ask-path handlers park a listener
# on their OWN thread; every _composer_reply fires it with the raw reply
# so the ledger records metered-or-estimated exactly once per upstream
# call. Thread-local on purpose: ThreadingHTTPServer serves requests
# concurrently, and a research turn worker's thread must never inherit
# another request's listener.
_METER = threading.local()


def set_meter(listener) -> None:
    """Park this thread's usage listener (None clears it — the handler's
    finally always clears, so one request can never bill another)."""
    _METER.listener = listener


def _composer_reply(
    message: str,
    thinking_type: str,
    model: str = COMPOSER_MODEL,
    urlopen_fn=None,
    timeout: int = COMPOSER_TIMEOUT,
    max_tokens: int = COMPOSER_MAX_TOKENS,
):
    """One POST to the composer endpoint; raises on any failure.

    thinking_type "enabled" for reasoning passes — the phase-2 planner
    and the dive's Planner (reasoning structures the decomposition and
    the cross-passage weaving, PM call, 2026-09-10) — and "disabled" for
    every writer, so copied sentences survive the verbatim guard. All
    calls run at the endpoint's default temperature (the writer's
    "temperature": 0 pin was dropped the same call: final synthesizing,
    not extraction). The Quoted answer runs glm-5.3-flash; the dive's
    two calls run glm-5.3-flash too (both tiers moved to flash
    2026-09-13, operator call — deep-dive latency was the pain).
    Interleaving AI text with verbatim quotes is light writing plus
    copy-matching, not reasoning; glm-5.3-flash's default thinking adds
    ~70s for identical output (measured 2026-09-10: 84.2s -> 16.5s on
    the writing task). Every call pins COMPOSER_MAX_TOKENS — the
    unpinned endpoint default left a long document out of room
    mid-write (2026-09-10).

    urlopen_fn rides for the dive and picker modules, whose scripted-
    upstream tests patch ONE urlopen per module: they run this same
    call shape through their own seam instead of this module's.
    """
    thinking = _thinking_fields(thinking_type)
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": message}],
    }
    payload.update(thinking)
    request = Request(
        COMPOSER_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.environ['LLM_API_KEY']}",
        },
        method="POST",
    )
    with (urlopen_fn if urlopen_fn is not None else urlopen)(
        request, timeout=timeout
    ) as response:
        reply = json.load(response)
    # The tap fires after the with-block: the socket is closed, the reply
    # is complete, and a listener crash can never take the answer down —
    # metering watches the work, it never gates it.
    listener = getattr(_METER, "listener", None)
    if listener is not None:
        try:
            listener(message, reply)
        except Exception:
            pass
    return reply


def _composer_content(reply):
    """Walk a composer reply down to its message content, raising on any
    malformed reply — the walk every composer caller guards with
    (KeyError, ValueError, OSError). A successful call guarantees
    reply["choices"][0] exists, so callers reading finish_reason use
    reply["choices"][0].get(...) safely."""
    return reply["choices"][0]["message"]["content"]


def plan_quoted_document(question: str, sources) -> str:
    """Reason out the document plan; "" on any planner failure.

    The plan is loose plain text — it is never machine-guarded, only
    fed to the writer as context. "" means the writer falls back to
    the no-plan prompt, so a planner failure never empties the sheet
    (AC-4, issue #23).
    """
    try:
        content = _composer_content(
            _composer_reply(build_planner_prompt(question, sources), "enabled")
        )
    except (KeyError, ValueError, OSError):
        return ""
    # A thinking reply lands its final text in message.content (the
    # reasoning itself rides in a separate field); anything empty or
    # non-string counts as planner failure, not a plan.
    return content.strip() if isinstance(content, str) else ""


def continue_quoted_document(question, answer, sources, plan, blocks):
    """One continuation call past a length-cut document; (blocks, truncated).

    The writer reads the blocks that survived the cut and writes only
    what follows them, same shape and rules. A continuation that is cut
    again is salvaged the same way and truncated comes back True so the
    sheet can say the document ended at the ceiling. On any failure the
    prefix alone rides on — still cut, never empty.
    """
    try:
        reply = _composer_reply(
            build_continuation_prompt(question, answer, sources, plan, blocks),
            "disabled",
        )
        content = _composer_content(reply)
    except (KeyError, ValueError, OSError):
        return blocks, True
    extra = parse_quoted_reply(content)
    return blocks + extra, reply["choices"][0].get("finish_reason") == "length"


def compose_quoted_answer(
    question: str, answer: str, sources, conversation_tail: str = ""
):
    """Write the Quoted answer blocks; ([], False) on writer failure or
    when the document misses the swap threshold (AC-4).

    Two sequential glm-5.3-flash calls since 2026-09-10 (PM call): a
    reasoning planner first, then the non-reasoning writer. The planner
    is best-effort — on any planner failure the writer runs without a
    plan (the single-call shape), so the sheet is never left empty.
    Each call gets its own COMPOSER_TIMEOUT. The sitting's earlier
    turns ride the writer only (conversation_tail, ADR-0015) — the
    planner plans from the question and passages alone.

    Returns (blocks, truncated). A reply stopped by the output ceiling
    (finish_reason "length") dies mid-JSON — the live 2026-09-10 run
    ended unfinished at its final section — so the complete block prefix
    is salvaged and ONE continuation call writes the rest; truncated is
    True only when even the continuation came back cut.

    One writer repair since 2026-09-15 (the intermittent «پاسخ استنادی
    آماده نشد», live smoke): a timeout, an empty reasoning reply, or a
    paraphrasing writer the guard strips below the swap threshold used
    to land [] with no recourse. The guarded result now gets ONE full
    writer retry — same plan, no planner re-run — and the better
    attempt rides; both attempts failing keeps the honest fallback.
    """
    plan = plan_quoted_document(question, sources)
    blocks, truncated = _write_quoted_once(
        question, answer, sources, plan, conversation_tail
    )
    if blocks:
        return blocks, truncated
    blocks, truncated = _write_quoted_once(
        question, answer, sources, plan, conversation_tail
    )
    return blocks, truncated


def _write_quoted_once(
    question: str, answer: str, sources, plan: str, conversation_tail: str = ""
):
    """One writer attempt: call, parse, ONE length-cut continuation,
    guard. ([], False) on call failure or a guard that keeps nothing —
    the retry's unit."""
    try:
        reply = _composer_reply(
            build_quoted_prompt(
                question, answer, sources, plan, conversation_tail
            ),
            "disabled",
        )
        content = _composer_content(reply)
    except (KeyError, ValueError, OSError):
        return [], False
    blocks = parse_quoted_reply(content)
    if reply["choices"][0].get("finish_reason") != "length":
        return guard_blocks(blocks, sources), False
    blocks, truncated = continue_quoted_document(
        question, answer, sources, plan, blocks
    )
    return guard_blocks(blocks, sources), truncated


def rewrite_followup_query(query: str, history: str) -> str:
    """A short follow-up made self-contained for the searcher (the
    follow-up thread, ADR-0015): ONE fast glm-5.3-flash call over the
    sitting's recent turns. The retrieval's quality is only as good as
    the query it sees — «بیشتر توضیح بده» alone retrieves noise, the
    rewritten form retrieves the discussed subject. Raises on any
    failure; the caller (serve.py's _contextual_query) falls back to
    the raw question — the rewrite is a better retrieval hint, never a
    gate."""
    prompt = (
        "You are making a follow-up question self-contained for a Book "
        "search engine.\n\n"
        "Earlier turns of the conversation, oldest first:\n"
        f"{history}\n\n"
        f"The user's new message: {query}\n\n"
        "Task: rewrite the new message as ONE standalone Farsi search "
        "query that carries the context it needs from the earlier turns "
        "— name the subject it refers to, keep it a question or a short "
        "keyword phrase, add nothing the turns do not support. Reply "
        "with ONLY the rewritten query: no quotes, no explanation, no "
        "extra words."
    )
    reply = _composer_reply(
        prompt,
        "disabled",
        timeout=REWRITE_TIMEOUT,
        max_tokens=REWRITE_MAX_TOKENS,
    )
    content = _composer_content(reply).strip().strip("\"«»").strip()
    return content or query
