"""The Quote-selection picker: EXACTLY ONE composer call over the
Evidence pool, the guarded verbatim selections with their Citation
labels — the sheet's first answer. The upstream seam is this module's
`urlopen` attribute (the tests patch ui.picker.urlopen); the call
itself is ui.composer's one call shape, run through that seam."""

from __future__ import annotations

from urllib.request import urlopen

try:
    from ui.composer import (
        _composer_content,
        _composer_reply,
        conversation_context,
    )
    from ui.guard import (
        _citation_labels,
        _count_word,
        _json_object,
        _numbered_passages,
        guard_sentences,
    )
except ImportError:  # the container runs serve.py as a script beside the modules
    from composer import _composer_content, _composer_reply, conversation_context
    from guard import (
        _citation_labels,
        _count_word,
        _json_object,
        _numbered_passages,
        guard_sentences,
    )

# Quote selection (ADR-0006, issue #28): the first answer rendered on
# the sheet is the Evidence pool's Quote selection — verbatim Book
# sentences, no AI prose — picked by EXACTLY ONE composer call. The
# model is the existing COMPOSER_MODEL pin (glm-5.3-flash, ADR-0002);
# thinking disabled for the phase-2 writer's measured reason:
# copy-matching, not reasoning. The pool itself is untouched — the same
# (reference, passage) pairs stay phase 2's exact input.
QUOTE_SELECTION_AIM = 10
QUOTE_SELECTION_CEILING = 12
QUOTE_SELECTION_FLOOR = 4


def build_picker_prompt(
    question: str, sources, conversation_tail: str = ""
) -> str:
    """The picker's brief: select, don't write.

    The aim wording is derived from QUOTE_SELECTION_AIM — the two cannot
    drift — and the reply shape is the selection list guard_sentences
    already consumes. The sitting's earlier turns ride as framing since
    ADR-0019 (the follow-up thread, same conversation_context section
    the writer uses): the first answer the user sees should know what
    the sitting already covered — a follow-up's selection that ignores
    the thread re-answers the last ask. Framing only: the selections
    themselves are still verbatim passages from below.
    """
    passages = _numbered_passages(sources)
    tail = conversation_context(conversation_tail)
    return (
        "You are selecting the Quote selection for a Farsi Q&A sheet "
        "over the Books.\n\n"
        f"Question: {question}\n\n"
        f"{tail}"
        "Passages (numbered, from the Books' retrieved Evidence; "
        "text-layer noise like \\b backspaces may appear between "
        "words):\n"
        f"{passages}\n\n"
        "Task: select the Farsi sentences that together best answer "
        f"the question — aim for {_count_word(QUOTE_SELECTION_AIM)}; "
        "fewer only when the passages hold fewer. Each selection is a "
        "complete Farsi sentence copied VERBATIM from exactly ONE "
        "passage, exactly as printed: some Books' text layers carry "
        "damage — lost or fused letters, reversed digits, stray Latin "
        "fragments — copy the damage as it stands and correct nothing "
        "(only the \\b backspaces are word spacing, not part of a "
        "word). Do not paraphrase, do not merge, do not shorten. Never "
        "invent a sentence.\n\n"
        "Reply with ONLY a JSON object, no prose, no code fence:\n"
        '{"selections": [{"text": "<verbatim sentence>", '
        '"source": <passage index>}]}'
    )


def parse_picker_reply(content):
    """Pull the selections list out of the picker's reply; [] when
    malformed.

    The parse conventions of parse_quoted_reply — code fence stripped,
    json.loads with one brace-scoped retry for prose-wrapped JSON, the
    shared _json_object — without the salvage: a picker reply is tiny,
    and a truncated one would not survive the floor anyway.
    """
    parsed = _json_object(content)
    selections = parsed.get("selections") if isinstance(parsed, dict) else None
    return selections if isinstance(selections, list) else []


def pick_quote_selection(
    question: str, sources, conversation_tail: str = ""
):
    """One picker call over the pool; the guarded selections, or [].

    Exactly ONE composer call (glm-5.3-flash, thinking disabled,
    COMPOSER_MAX_TOKENS, endpoint-default temperature). The sitting's
    earlier turns ride as framing (conversation_tail, ADR-0019) — same
    fail-soft semantics as the writer's tail: no sitting, an empty
    string. The reply runs
    the existing verbatim letter-stream guard — a paraphrase, or a
    verbatim sentence claiming the wrong index, drops; the survivors cap
    at QUOTE_SELECTION_CEILING, and fewer than QUOTE_SELECTION_FLOOR of
    them means the picker missed the floor: [] — the same empty shape a
    call failure returns, so the sheet's fallback is one uniform shape.
    Every kept sentence carries the labels of exactly the passage it
    claims, attached server-side like guard_blocks does.
    """
    try:
        reply = _composer_reply(
            build_picker_prompt(question, sources, conversation_tail),
            "disabled",
            urlopen_fn=urlopen,
        )
        content = _composer_content(reply)
    except (KeyError, ValueError, OSError):
        return []
    kept = guard_sentences(parse_picker_reply(content), sources)
    kept = kept[:QUOTE_SELECTION_CEILING]
    if len(kept) < QUOTE_SELECTION_FLOOR:
        return []
    return [
        {
            "text": item["text"],
            "reference": item["reference"],
            **_citation_labels(item["reference"], item["text"]),
        }
        for item in kept
    ]
